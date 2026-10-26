#!/usr/bin/env python3
"""N 字形态：因子分层 + 时间切分验证。

在 nshape_backtest.py 的候选集上，为每条信号打特征标签，再按因子分层统计
「2%/3% 止盈」的胜率与期望值，最后按 2024 / 2025 / 2026 切分做稳定性检验
（避免只在一段行情里成立）。
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nshape_backtest import (load_all, find_setups, summarize, fmt,
                             limit_pct, BASE)
from nshape_bt import sim2

TP, SL, HOLD = 0.03, 0.05, 5
ENTRY = 'open'   # 次日开盘买：实测优于当日收盘买


def features(st, t, d, t0, ctx):
    c, o, a, h, l = st.c, st.o, st.a, st.h, st.l
    f = {}
    # 启动强度
    f['t0_gain'] = st.pct(t0)
    lp = limit_pct(st.code, st.name)
    f['t0_is_limit'] = 1 if f['t0_gain'] >= lp else 0
    # 是否首板（前 20 日内无涨停）
    prior = 0
    for k in range(max(0, t0 - 20), t0):
        if st.pct(k) >= lp - 0.003:
            prior += 1
    f['t0_prior_limit'] = prior
    f['t0_first_board'] = 1 if prior == 0 else 0
    # 位置
    f['pos250'] = (c[t0] / st.hi250[t0]) if st.hi250[t0] else 1.0
    f['t0_new_high'] = 1 if c[t0] >= st.hi250[t0] * 0.999 else 0
    f['above_ma60'] = 1 if (st.ma60[t0] and c[t0] > st.ma60[t0]) else 0
    f['above_ma250'] = 1 if (st.ma250[t0] and c[t0] > st.ma250[t0]) else 0
    # 启动前 20 日涨幅（是否已经拉过一段）
    f['pre20_gain'] = (c[t0] / c[t0 - 20] - 1) if t0 >= 20 else 0
    # 整理期特征
    vol_sum = sum(a[t0 + 1:t + 1])
    f['shrink'] = vol_sum / (a[t0] * max(1, d))          # 整理日均量 / 启动日量
    f['retreat'] = c[t0] / c[t] - 1                       # 相对启动日收盘回撤（负=还在涨）
    f['retreat_from_high'] = max(h[t0 + 1:t + 1] or [h[t0]]) / c[t] - 1
    f['ma5_dev'] = (c[t] / st.ma5[t] - 1) if st.ma5[t] else 0
    f['ma20_dev'] = (c[t] / st.ma20[t] - 1) if st.ma20[t] else 0
    f['body'] = c[t] / o[t] - 1 if o[t] else 0
    f['upper_shadow'] = (h[t] - max(c[t], o[t])) / max(c[t], o[t]) if c[t] else 0
    f['lower_shadow'] = (min(c[t], o[t]) - l[t]) / max(c[t], o[t]) if c[t] else 0
    f['day_amt'] = a[t]
    f['turn'] = st.v[t] * c[t] / st.mcap if st.mcap else 0
    f['mcap_yi'] = (st.mcap / 1e8) if st.mcap else 0
    # 市场环境
    f['mkt_limit_cnt'] = ctx['limit_cnt'].get(st.d[t], 0)
    f['idx5'] = ctx['idx5'].get(st.d[t], 0)
    f['idx20'] = ctx['idx20'].get(st.d[t], 0)
    # 日历
    import datetime
    yy, mm, dd = st.d[t].split('-')
    f['weekday'] = datetime.date(int(yy), int(mm), int(dd)).weekday()
    f['year'] = yy
    # 未来 5 日是否突破启动日高点（形态完成率，用于诊断，不参与选股）
    fut = max(h[t + 1:t + 6]) if t + 5 < len(h) else 0
    f['broke_t0_high'] = 1 if fut >= h[t0] else 0
    f['fwd5_max'] = (fut / c[t] - 1) if fut else 0
    return f


def bucket(f, key):
    v = f.get(key)
    if v is None:
        return None
    if key in ('t0_gain',):
        return '涨停' if f['t0_is_limit'] else '大阳(9.7%以下)'
    if key == 't0_prior_limit':
        return {0: '首板', 1: '二板', 2: '三板'}.get(v, '4板以上')
    if key == 'pos250':
        return '≤80%(低位)' if v <= 0.8 else ('80-95%' if v <= 0.95 else ('95-100%' if v <= 1.0 else '创新高'))
    if key == 'pre20_gain':
        return '≤0%' if v <= 0 else ('0-10%' if v <= 0.1 else ('10-25%' if v <= 0.25 else '>25%'))
    if key == 'shrink':
        return '<0.4' if v < 0.4 else ('0.4-0.6' if v < 0.6 else ('0.6-0.8' if v < 0.8 else '0.8-1.0'))
    if key == 'retreat':
        return '<0(高于启动日)' if v < 0 else ('0-3%' if v <= 0.03 else ('3-6%' if v <= 0.06 else '>6%'))
    if key == 'ma5_dev':
        return '<0(破MA5)' if v < 0 else ('0-2%' if v <= 0.02 else ('2-5%' if v <= 0.05 else '>5%'))
    if key == 'ma20_dev':
        return '<0' if v < 0 else ('0-5%' if v <= 0.05 else ('5-15%' if v <= 0.15 else '>15%'))
    if key == 'mcap_yi':
        return '<50亿' if v < 50 else ('50-100亿' if v < 100 else ('100-300亿' if v < 300 else '>300亿'))
    if key == 'mkt_limit_cnt':
        return '≤30家(冰点)' if v <= 30 else ('31-60家' if v <= 60 else ('61-100家' if v <= 100 else '>100家(亢奋)'))
    if key == 'idx5':
        return '<-2%' if v < -0.02 else ('-2~0%' if v < 0 else ('0-2%' if v <= 0.02 else '>2%'))
    if key == 'idx20':
        return '<-3%' if v < -0.03 else ('-3~0%' if v < 0 else ('0-3%' if v <= 0.03 else '>3%'))
    if key == 'weekday':
        return ['周一', '周二', '周三', '周四', '周五'][v] if v < 5 else '周末'
    if key == 'day_amt':
        y = v / 1e8
        return '<2亿' if y < 2 else ('2-5亿' if y < 5 else ('5-15亿' if y < 15 else '>15亿'))
    if key == 'body':
        return '<-2%(阴)' if v < -0.02 else ('-2~0%(小阴)' if v < 0 else ('0-2%(小阳)' if v <= 0.02 else '>2%(中阳)'))
    if key == 'upper_shadow':
        return '<1%' if v < 0.01 else ('1-3%' if v < 0.03 else ('3-6%' if v < 0.06 else '>6%'))
    if key == 'lower_shadow':
        return '<1%' if v < 0.01 else ('1-3%' if v < 0.03 else '>3%')
    if key == 'above_ma60':
        return 'MA60上' if v else 'MA60下'
    if key == 'above_ma250':
        return '年线上' if v else '年线下'
    if key == 't0_first_board':
        return '首板' if v else '非首板'
    return str(v)


KEYS = ['t0_is_limit', 't0_prior_limit', 'pos250', 'pre20_gain', 'shrink', 'retreat',
        'ma5_dev', 'ma20_dev', 'mcap_yi', 'mkt_limit_cnt', 'idx5', 'idx20',
        'weekday', 'day_amt', 'body', 'upper_shadow', 'lower_shadow',
        'above_ma60', 'above_ma250']

ORDER = {
    'pos250': ['≤80%(低位)', '80-95%', '95-100%', '创新高'],
    'pre20_gain': ['≤0%', '0-10%', '10-25%', '>25%'],
    'shrink': ['<0.4', '0.4-0.6', '0.6-0.8', '0.8-1.0'],
    'retreat': ['<0(高于启动日)', '0-3%', '3-6%', '>6%'],
    'ma5_dev': ['<0(破MA5)', '0-2%', '2-5%', '>5%'],
    'mcap_yi': ['<50亿', '50-100亿', '100-300亿', '>300亿'],
    'mkt_limit_cnt': ['≤30家(冰点)', '31-60家', '61-100家', '>100家(亢奋)'],
    'idx5': ['<-2%', '-2~0%', '0-2%', '>2%'],
    'idx20': ['<-3%', '-3~0%', '0-3%', '>3%'],
    'day_amt': ['<2亿', '2-5亿', '5-15亿', '>15亿'],
    'body': ['<-2%(阴)', '-2~0%(小阴)', '0-2%(小阳)', '>2%(中阳)'],
}


def main():
    stocks = load_all()
    print('[load] %d 只' % len(stocks), flush=True)

    # 每日全市场涨停家数（市场情绪）
    limit_cnt = defaultdict(int)
    for st in stocks:
        lp = limit_pct(st.code, st.name)
        for i in range(len(st.c)):
            if st.pct(i) >= lp - 0.003:
                limit_cnt[st.d[i]] += 1

    # 指数动量（上证 000001 用东财指数 secid=1.000001，此处从缓存读不到，改用本地已有 bench）
    idx5, idx20 = {}, {}
    try:
        import subprocess
        raw = subprocess.run(['curl', '-s', '--max-time', '15',
                              'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001'
                              '&fields1=f1&fields2=f51,f52,f53,f54,f55,f56,f57,f58&klt=101&fqt=1'
                              '&beg=20230101&end=20261101'],
                             capture_output=True).stdout.decode()
        j = json.loads(raw)['data']['klines']
        days = [x.split(',')[0] for x in j]
        cls = [float(x.split(',')[2]) for x in j]
        for i in range(len(days)):
            if i >= 5:
                idx5[days[i]] = cls[i] / cls[i - 5] - 1
            if i >= 20:
                idx20[days[i]] = cls[i] / cls[i - 20] - 1
        print('[idx] 上证 %d 根' % len(days))
    except Exception as e:
        print('[idx] 指数抓取失败:', e)

    ctx = {'limit_cnt': limit_cnt, 'idx5': idx5, 'idx20': idx20}

    cands = []
    for idx, st in enumerate(stocks):
        for (t, d, t0) in find_setups(st):
            cands.append((idx, t, d, t0))
    print('[scan] 候选 %d' % len(cands), flush=True)

    rows = []      # 每条信号：特征 + 收益
    for i, t, d, t0 in cands:
        st = stocks[i]
        r = sim2(st, t, t0, d, TP, SL, HOLD, ENTRY)
        if not r:
            continue
        f = features(st, t, d, t0, ctx)
        f['_ret'] = r['ret']
        f['_exit'] = r['exit']
        f['_d'] = d
        f['_code'] = st.code
        f['_date'] = st.d[t]
        rows.append(f)
    print('[sim] 有效信号 %d' % len(rows), flush=True)

    base = summarize([{'_ret': r['_ret'], 'exit': r['_exit'], 'ret': r['_ret'], 'days': 0}
                      for r in rows])
    print('\n[基准] tp2%% sl2%% hold5: %s' % fmt(base))

    out = {'base': base, 'by_d': {}, 'factors': {}}
    for d in range(1, 6):
        sub = [r for r in rows if r['_d'] == d]
        s = summarize([{'_ret': x['_ret'], 'exit': x['_exit'], 'ret': x['_ret'], 'days': 0} for x in sub])
        out['by_d'][d] = s
        print('  d=%d %s' % (d, fmt(s)))

    for key in KEYS:
        grp = defaultdict(list)
        for r in rows:
            b = bucket(r, key)
            if b is None:
                continue
            grp[b].append(r)
        res = {}
        print('\n--- 因子 %s ---' % key)
        order = ORDER.get(key) or sorted(grp.keys())
        for b in order:
            sub = grp.get(b) or []
            if len(sub) < 100:
                continue
            s = summarize([{'ret': x['_ret'], 'exit': x['_exit'], 'days': 0} for x in sub])
            s['broke'] = sum(x['broke_t0_high'] for x in sub) / len(sub)
            s['fwd5'] = sum(x['fwd5_max'] for x in sub) / len(sub)
            res[b] = s
            print('  %-14s %s | 后续破T0高点%.0f%% 5日最大涨幅%.1f%%' %
                  (b, fmt(s), s['broke'] * 100, s['fwd5'] * 100))
        out['factors'][key] = res

    # 时间切分
    print('\n--- 按年 ---')
    out['by_year'] = {}
    for y in ('2024', '2025', '2026'):
        sub = [r for r in rows if r['year'] == y]
        s = summarize([{'ret': x['_ret'], 'exit': x['_exit'], 'days': 0} for x in sub])
        out['by_year'][y] = s
        print('  %s %s' % (y, fmt(s)))

    json.dump(out, open(os.path.join(BASE, 'nshape_factors.json'), 'w'),
              ensure_ascii=False, indent=1)
    # 明细落盘供后续组合筛选
    slim = [{k: v for k, v in r.items()} for r in rows]
    json.dump(slim, open(os.path.join(BASE, 'nshape_rows.json'), 'w'), ensure_ascii=False)


if __name__ == '__main__':
    main()

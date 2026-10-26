#!/usr/bin/env python3
"""N 字形态：可落地策略的最终验证（规则组合 + 结构止损 + 日历组合年化）。

规则来源：nshape_rules.py 的双段验证结果（2024-2025 训练 / 2026 样本外）。
最终口径：
  候选：find_setups（T0 涨停放量 + 1~4 日缩量不破位 + 信号日收阳/小实体）
  过滤：市值 ≥ 100 亿 & 启动前 20 日涨幅 ≤ 10% & 信号日上影 < 3%
  入场：次日开盘（实测优于当日收盘：避开尾盘追高）
  止盈：2% / 3%（触价即走）
  止损：结构止损（跌破 T0 阳线开盘价）或固定 5%
"""
import json
import os
import sys
import statistics
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nshape_backtest import load_all, find_setups, COST, BASE


def sim3(st, t, t0, tp, mode='struct', hold=10):
    n = len(st.c)
    if t + 1 >= n:
        return None
    buy = st.o[t + 1]
    if buy <= 0:
        return None
    if st.h[t + 1] == st.l[t + 1] and st.pct(t + 1) > 0.09:
        return None                     # 一字涨停买不进
    start = t + 1
    stop = st.o[t0] if mode == 'struct' else buy * 0.95
    tp_px = buy * (1 + tp)
    for j in range(start, min(start + hold, n)):
        o, h, l = st.o[j], st.h[j], st.l[j]
        hit_tp, hit_sl = h >= tp_px, l <= stop
        if hit_tp and hit_sl:
            return {'ret': min(stop / buy - 1, o / buy - 1) - COST, 'exit': 'sl'}
        if hit_tp:
            return {'ret': max(tp, o / buy - 1) - COST, 'exit': 'tp'}
        if hit_sl:
            return {'ret': min(stop / buy - 1, o / buy - 1) - COST, 'exit': 'sl'}
    j = min(start + hold, n) - 1
    return {'ret': st.c[j] / buy - 1 - COST, 'exit': 'time'}


def filt(st, t, t0):
    if not st.mcap or st.mcap < 1e10:
        return False
    if t0 >= 20 and st.c[t0] / st.c[t0 - 20] - 1 > 0.10:
        return False
    up = (st.h[t] - max(st.o[t], st.c[t])) / max(1e-9, st.c[t])
    if up >= 0.03:
        return False
    return True


def stat(rows):
    if not rows:
        return None
    rets = [r['ret'] for r in rows]
    g = sum(1 for x in rets if x > 0)
    gains = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    return {'n': len(rows), 'win': g / len(rows), 'avg': sum(rets) / len(rets),
            'med': statistics.median(rets),
            'avgw': sum(gains) / len(gains) if gains else 0,
            'avgl': sum(losses) / len(losses) if losses else 0,
            'pf': (sum(gains) / abs(sum(losses))) if losses else 0}


def show(tag, rows, dates):
    s = stat(rows)
    if not s:
        print('  %-30s 无样本' % tag)
        return None
    # 日历组合年化：每日用 1/5 资金买入当天全部信号并持有 5 日
    byday = defaultdict(list)
    for r in rows:
        byday[r['date']].append(r['ret'])
    nav = 1.0
    for d in sorted(byday):
        nav *= (1 + 0.2 * statistics.mean(byday[d]))
    years = (len(dates) / 244.0) if dates else 1
    ann = nav ** (1 / years) - 1 if years > 0 else 0
    print('  %-30s n=%-5d 胜率%5.1f%% 期望%+.3f%% 均盈%+.2f%% 均亏%+.2f%% 盈亏比%.2f'
          ' | 日净值%.2f 年化%+.1f%%' %
          (tag, s['n'], s['win'] * 100, s['avg'] * 100, s['avgw'] * 100,
           s['avgl'] * 100, s['pf'], nav, ann * 100))
    s.update({'nav': nav, 'ann': ann})
    return s


def main():
    stocks = load_all()
    print('[load] %d 只' % len(stocks), flush=True)
    cand = []
    for idx, st in enumerate(stocks):
        for (t, d, t0) in find_setups(st):
            cand.append((idx, t, d, t0))
    print('[scan] 候选 %d' % len(cand), flush=True)
    dates = sorted({stocks[i].d[t] for i, t, d, t0 in cand})
    out = {}

    print('\n===== 全样本（不加规则）=====')
    for tp in (0.02, 0.03):
        for mode in ('struct', 'fixed5'):
            rows = []
            for i, t, d, t0 in cand:
                r = sim3(stocks[i], t, t0, tp, mode)
                if r:
                    r['date'] = stocks[i].d[t]
                    r['year'] = r['date'][:4]
                    rows.append(r)
            out['all_tp%d_%s' % (round(tp * 100), mode)] = show(
                'tp%d%% / %s' % (round(tp * 100), mode), rows, dates)

    print('\n===== 加规则过滤（市值≥100亿 + 前20日≤10% + 上影<3%）=====')
    picked = []
    for i, t, d, t0 in cand:
        st = stocks[i]
        if not filt(st, t, t0):
            continue
        for tp in (0.02, 0.025, 0.03):
            for mode in ('struct', 'fixed5'):
                r = sim3(st, t, t0, tp, mode)
                if r:
                    r['date'] = st.d[t]
                    r['year'] = r['date'][:4]
                    r['tp'] = tp
                    r['mode'] = mode
                    picked.append(r)
    print('  过滤后信号 %d 个（原 %d，保留 %.0f%%）' %
          (len({(r['date']) for r in picked if r['tp'] == 0.02}), len(cand),
           100.0 * len([1 for i, t, d, t0 in cand if filt(stocks[i], t, t0)]) / max(1, len(cand))))

    for tp in (0.02, 0.025, 0.03):
        for mode in ('struct', 'fixed5'):
            rows = [r for r in picked if r['tp'] == tp and r['mode'] == mode]
            key = 'f_tp%d_%s' % (round(tp * 100), mode)
            out[key] = show('tp%d%% / %s' % (round(tp * 100), mode), rows, dates)

    # 分年（挑最优组合 tp2 + struct）
    print('\n===== 分年稳定性（tp2% + 结构止损）=====')
    rows = [r for r in picked if r['tp'] == 0.02 and r['mode'] == 'struct']
    for y in ('2024', '2025', '2026'):
        sub = [r for r in rows if r['year'] == y]
        s = show('  %s' % y, sub, [d for d in dates if d.startswith(y)])
        out['y_%s' % y] = s

    # 分月（看月度胜率波动）
    print('\n===== 分月（tp2% + 结构止损）=====')
    bym = defaultdict(list)
    for r in rows:
        bym[r['date'][:7]].append(r)
    ms = []
    for m in sorted(bym):
        s = stat(bym[m])
        ms.append({'m': m, 'n': s['n'], 'win': s['win'], 'avg': s['avg']})
    neg = [x for x in ms if x['avg'] < 0]
    print('  共 %d 个月，其中期望为负的月份 %d 个（%.0f%%）；最好 %s %+.2f%%，最差 %s %+.2f%%' % (
        len(ms), len(neg), 100.0 * len(neg) / max(1, len(ms)),
        max(ms, key=lambda x: x['avg'])['m'], max(x['avg'] for x in ms) * 100,
        min(ms, key=lambda x: x['avg'])['m'], min(x['avg'] for x in ms) * 100))
    out['months'] = ms

    json.dump(out, open(os.path.join(BASE, 'nshape_final.json'), 'w'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()

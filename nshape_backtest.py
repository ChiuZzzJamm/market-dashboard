#!/usr/bin/env python3
"""N 字形态（涨停/大阳 → 缩量整理 → 再上攻）识别 + 买点/止盈回测。

口径（把用户截图翻译成可计算规则，全部只用 t 日及之前数据，无未来函数）：

  锚点 T0：涨停或大阳（主板 涨幅 >= 9.7%；双创 >= 19.5%；ST 名称含 ST 用 >= 4.8%）
           且成交额 >= 前 5 日均额 × 1.5（放量启动）
  整理期 T0+1 .. t：① 收盘价始终 >= T0 阳线实体中位 (open+close)/2
                    ② 最低价始终不破 T0 开盘价
                    ③ 每日成交额 <= T0 成交额 × 1.0（缩量）
                    ④ 收盘价不破 MA5 超过 3%（容错）
  信号日 t = T0 + d（d 即「T0 后第几天买」，N 字跨度 = d+1 根 K 线）
            当天需收阳或小实体（|涨跌| <= 3%），且收盘未涨停（否则买不进）

  买点：t 日收盘价（主口径）/ t+1 日开盘价（对照）
  卖点：t+1 起 hold 日内
        - 最高价触及 buy×(1+tp) → 止盈成交（若开盘已越线，按开盘价）
        - 最低价触及 buy×(1-sl) → 止损成交（同日双触发按保守=先止损）
        - 都没触发 → 第 hold 日收盘平仓
  成本：双边 0.16%
"""
import json
import os
import glob
import statistics
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(BASE, '.kline_hist')
COST = 0.0016


def is_st(name):
    n = (name or '').upper()
    return ('ST' in n) or n.startswith('*')


def limit_pct(code, name):
    if is_st(name):
        return 0.048
    if code[0] == '3' or code.startswith('688') or code.startswith('689'):
        return 0.195
    return 0.097


def sma(arr, n):
    out = [None] * len(arr)
    s = 0.0
    for i, v in enumerate(arr):
        s += v
        if i >= n:
            s -= arr[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


class Stock(object):
    __slots__ = ('code', 'name', 'mcap', 'd', 'o', 'c', 'h', 'l', 'v', 'a',
                 'ma5', 'ma10', 'ma20', 'ma30', 'ma60', 'ma250', 'hi250', 'prev_c')

    def __init__(self, code, name, mcap, rows):
        self.code, self.name, self.mcap = code, name, mcap
        self.d = [r[0] for r in rows]
        self.o = [r[1] for r in rows]
        self.c = [r[2] for r in rows]
        self.h = [r[3] for r in rows]
        self.l = [r[4] for r in rows]
        self.v = [r[5] for r in rows]
        # 成交额统一口径：volume(股) × close —— 东财/腾讯两源 amount 一真一估，
        # 只有「比值」进因子，统一重算才能跨源可比。
        self.a = [r[5] * r[2] for r in rows]
        self.ma5 = sma(self.c, 5)
        self.ma10 = sma(self.c, 10)
        self.ma20 = sma(self.c, 20)
        self.ma30 = sma(self.c, 30)
        self.ma60 = sma(self.c, 60)
        self.ma250 = sma(self.c, 250)
        self.prev_c = [self.c[0]] + self.c[:-1]
        hi = [None] * len(self.c)
        run = 0.0
        for i, x in enumerate(self.h):
            run = max(run, x)
            if i >= 250:
                run = max(self.h[i - 249:i + 1])
            hi[i] = run
        self.hi250 = hi

    def pct(self, i):
        p = self.prev_c[i]
        return (self.c[i] / p - 1.0) if p else 0.0

    def gap_ok(self, i):
        """停牌保护：与上一根间隔 <= 10 自然日。"""
        if i == 0:
            return False
        y0, m0, d0 = self.d[i - 1].split('-')
        import datetime
        t0 = datetime.date(int(y0), int(m0), int(d0))
        y1, m1, d1 = self.d[i].split('-')
        t1 = datetime.date(int(y1), int(m1), int(d1))
        return (t1 - t0).days <= 10


def load_all(limit=None):
    files = sorted(glob.glob(os.path.join(HIST, '*.json')))
    if limit:
        files = files[:limit]
    out = []
    for f in files:
        try:
            j = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        rows = j.get('k') or []
        if len(rows) < 300:
            continue
        out.append(Stock(j['code'], j.get('name') or '', j.get('mcap'), rows))
    return out


def find_setups(st, dmax=5):
    """产出 (i_t, d, t0) 候选：t 为买入日下标，d = t - t0。"""
    res = []
    n = len(st.c)
    lp = limit_pct(st.code, st.name)
    for t0 in range(260, n - 6):
        if not st.gap_ok(t0):
            continue
        pc = st.pct(t0)
        if pc < lp:
            continue
        a0 = st.a[t0]
        base = st.a[t0 - 5:t0]
        if not base or a0 <= 0:
            continue
        if a0 < statistics.mean(base) * 1.5:
            continue
        mid = (st.o[t0] + st.c[t0]) / 2.0
        for d in range(1, dmax + 1):
            t = t0 + d
            if t >= n - 1:
                break
            ok = True
            for k in range(t0 + 1, t + 1):
                if st.c[k] < mid or st.l[k] < st.o[t0]:
                    ok = False
                    break
                if st.a[k] > a0:            # 整理期不能放量超过启动日
                    ok = False
                    break
                if st.ma5[k] and st.c[k] < st.ma5[k] * 0.97:
                    ok = False
                    break
            if not ok:
                break                          # 破位即整段失效，不再往更晚的 d 找
            # 信号日条件
            if not st.gap_ok(t):
                continue
            if st.pct(t) >= lp - 0.003:        # 收盘涨停买不进
                continue
            body = abs(st.c[t] / st.o[t] - 1) if st.o[t] else 0
            if st.c[t] < st.o[t] and body > 0.045:
                continue                       # 大阴线不算整理
            res.append((t, d, t0))
    return res


def simulate(st, t, d, t0, tp, sl, hold, entry='close'):
    n = len(st.c)
    if entry == 'close':
        buy = st.c[t]
        start = t + 1
    else:
        if t + 1 >= n:
            return None
        buy = st.o[t + 1]
        start = t + 1
    if buy <= 0:
        return None
    # 一字涨停开盘无法买入（对照口径）
    if start == t + 1 and entry == 'open' and st.o[t + 1] >= st.prev_c[t + 1] * (1 + 0.095):
        if st.h[t + 1] == st.l[t + 1]:
            return None
    hi_p, lo_p = buy * (1 + tp), buy * (1 - sl)
    for j in range(start, min(start + hold, n)):
        o, h, l = st.o[j], st.h[j], st.l[j]
        hit_tp = h >= hi_p
        hit_sl = l <= lo_p
        if hit_tp and hit_sl:
            # 保守：同日双触发判止损
            ret = min(-sl, (o / buy - 1) if o <= lo_p else -sl)
            return {'ret': ret - COST, 'days': j - t, 'exit': 'sl'}
        if hit_tp:
            ret = max(tp, o / buy - 1) if o > hi_p else tp
            return {'ret': ret - COST, 'days': j - t, 'exit': 'tp'}
        if hit_sl:
            ret = min(-sl, o / buy - 1)
            return {'ret': ret - COST, 'days': j - t, 'exit': 'sl'}
    j = min(start + hold, n) - 1
    return {'ret': st.c[j] / buy - 1 - COST, 'days': j - t, 'exit': 'time'}


def summarize(rows, label=''):
    if not rows:
        return None
    rets = [r['ret'] for r in rows]
    tp = sum(1 for r in rows if r['exit'] == 'tp')
    sl = sum(1 for r in rows if r['exit'] == 'sl')
    tm = sum(1 for r in rows if r['exit'] == 'time')
    win = sum(1 for x in rets if x > 0)
    gains = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    pf = (sum(gains) / abs(sum(losses))) if losses and sum(losses) != 0 else 0
    return {'label': label, 'n': len(rows), 'win': win / len(rows),
            'tp': tp / len(rows), 'sl': sl / len(rows), 'time': tm / len(rows),
            'avg': sum(rets) / len(rets),
            'med': statistics.median(rets),
            'avg_win': (sum(gains) / len(gains)) if gains else 0,
            'avg_loss': (sum(losses) / len(losses)) if losses else 0,
            'pf': pf, 'days': sum(r['days'] for r in rows) / len(rows)}


def fmt(s):
    if not s:
        return '（无样本）'
    return ('n=%-6d 胜率%5.1f%% 止盈%5.1f%% 止损%5.1f%% 到期%5.1f%% '
            '均值%+.2f%% 中位%+.2f%% 盈亏比%.2f 均持%.1f日' %
            (s['n'], s['win'] * 100, s['tp'] * 100, s['sl'] * 100, s['time'] * 100,
             s['avg'] * 100, s['med'] * 100, s['pf'], s['days']))


def main():
    print('[load] 读取日K…', flush=True)
    stocks = load_all()
    print('[load] %d 只可用标的' % len(stocks), flush=True)
    dates = sorted({d for s in stocks for d in s.d})
    print('[load] 区间 %s ~ %s，共 %d 个交易日' % (dates[0], dates[-1], len(dates)), flush=True)

    # 1) 建全部候选（一次扫描，多组参数复用）
    print('[scan] 扫描 N 字候选…', flush=True)
    cand = []          # (stock_idx, t, d, t0)
    for idx, st in enumerate(stocks):
        for (t, d, t0) in find_setups(st):
            cand.append((idx, t, d, t0))
        if idx % 500 == 0:
            print('   %d/%d 累计候选 %d' % (idx, len(stocks), len(cand)), flush=True)
    print('[scan] 候选 %d' % len(cand), flush=True)

    # 统计按跨度分布
    by_d = defaultdict(int)
    for _, _, d, _ in cand:
        by_d[d] += 1
    for d in sorted(by_d):
        print('  跨度 %d 天(T0+%d)：%d 个' % (d + 1, d, by_d[d]), flush=True)

    json.dump([{'c': stocks[i].code, 't': stocks[i].d[t], 'd': d} for i, t, d, _ in cand],
              open(os.path.join(BASE, 'nshape_signals.json'), 'w'), ensure_ascii=False)

    out = {}
    # 2) 主扫描：tp × sl × d
    for tp in (0.02, 0.03):
        for sl in (0.015, 0.02, 0.03, 0.05):
            for hold in (3, 5):
                key = 'tp%d_sl%d_h%d' % (round(tp * 100), round(sl * 100), hold)
                rows_by_d = defaultdict(list)
                for i, t, d, t0 in cand:
                    r = simulate(stocks[i], t, d, t0, tp, sl, hold)
                    if r:
                        rows_by_d[d].append(r)
                        rows_by_d['all'].append(r)
                out[key] = {str(d): summarize(rows_by_d[d]) for d in rows_by_d}
                print('\n== %s ==' % key)
                for d in sorted([x for x in rows_by_d if isinstance(x, int)]):
                    print('  d=%d %s' % (d, fmt(summarize(rows_by_d[d]))), flush=True)
                print('  ALL %s' % fmt(summarize(rows_by_d['all'])), flush=True)
    json.dump(out, open(os.path.join(BASE, 'nshape_bt.json'), 'w'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()

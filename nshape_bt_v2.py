#!/usr/bin/env python3
"""N 字形态：买点 × 止盈 × 止损 二维矩阵 + 隔夜跳空诊断（v2）。

与 v1 的差别：
  1. 增加入场方式：当日收盘买 / 次日开盘买（实盘两种下单习惯）
  2. 增加「次日开盘涨跌」分布——回答「为什么 70% 胜率还是亏」
  3. 增加结构止损（跌破 T0 阳线开盘价），替代拍脑袋的固定百分比
  4. 输出可直接落地的参数表 + 按年稳定性
"""
import json
import os
import sys
import statistics
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nshape_backtest import load_all, find_setups, summarize, COST, BASE

HOLD_DEF = 5


def sim2(st, t, t0, d, tp, sl, hold, entry='close', struct_sl=False):
    n = len(st.c)
    if entry == 'close':
        buy = st.c[t]
        start = t + 1
    else:
        if t + 1 >= n:
            return None
        buy = st.o[t + 1]
        start = t + 1
        # 次日开盘一字涨停 → 买不进
        if st.h[t + 1] == st.l[t + 1] and st.pct(t + 1) > 0.09:
            return None
    if buy <= 0:
        return None
    stop_px = st.o[t0] * 1.0 if struct_sl else buy * (1 - sl)
    tp_px = buy * (1 + tp) if tp else None
    for j in range(start, min(start + hold, n)):
        o, h, l = st.o[j], st.h[j], st.l[j]
        hit_tp = tp_px is not None and h >= tp_px
        hit_sl = l <= stop_px
        if hit_tp and hit_sl:
            return {'ret': min(stop_px / buy - 1, o / buy - 1) - COST, 'exit': 'sl', 'days': j - t}
        if hit_tp:
            return {'ret': max(tp, o / buy - 1) - COST, 'exit': 'tp', 'days': j - t}
        if hit_sl:
            return {'ret': min(stop_px / buy - 1, o / buy - 1) - COST, 'exit': 'sl', 'days': j - t}
    j = min(start + hold, n) - 1
    return {'ret': st.c[j] / buy - 1 - COST, 'exit': 'time', 'days': j - t}


def detail(rows):
    s = summarize(rows)
    g = [r['ret'] for r in rows if r['ret'] > 0]
    l = [r['ret'] for r in rows if r['ret'] <= 0]
    s['avg_win'] = sum(g) / len(g) if g else 0
    s['avg_loss'] = sum(l) / len(l) if l else 0
    s['worst'] = min(r['ret'] for r in rows)
    s['p5'] = sorted(r['ret'] for r in rows)[int(len(rows) * 0.05)]
    return s


def line(s):
    if not s:
        return '无样本'
    return ('n=%-5d 胜率%5.1f%% 期望%+.3f%% 均盈%+.2f%% 均亏%+.2f%% '
            '盈亏比%.2f 最差%+.1f%% P5%+.1f%%' %
            (s['n'], s['win'] * 100, s['avg'] * 100, s['avg_win'] * 100,
             s['avg_loss'] * 100, s['pf'], s['worst'] * 100, s['p5'] * 100))


def main():
    stocks = load_all()
    print('[load] %d 只' % len(stocks), flush=True)
    cand = []
    for idx, st in enumerate(stocks):
        for (t, d, t0) in find_setups(st):
            cand.append((idx, t, d, t0))
    print('[scan] 候选 %d' % len(cand), flush=True)
    out = {}

    # ---------- 1. 隔夜跳空诊断 ----------
    print('\n===== 隔夜跳空诊断（买入日收盘 → 次日开盘）=====')
    gaps = []
    for i, t, d, t0 in cand:
        st = stocks[i]
        if t + 1 >= len(st.c):
            continue
        gaps.append(st.o[t + 1] / st.c[t] - 1)
    gaps.sort()
    q = lambda p: gaps[int(len(gaps) * p)]
    print('  n=%d 均值%+.2f%% 中位%+.2f%% P10%+.2f%% P25%+.2f%% P75%+.2f%% P90%+.2f%%'
          % (len(gaps), statistics.mean(gaps) * 100, q(0.5) * 100, q(0.1) * 100,
             q(0.25) * 100, q(0.75) * 100, q(0.9) * 100))
    print('  低开>2%%的比例 %.1f%%；低开>4%%的比例 %.1f%%；高开>2%%的比例 %.1f%%' % (
        sum(1 for g in gaps if g < -0.02) / len(gaps) * 100,
        sum(1 for g in gaps if g < -0.04) / len(gaps) * 100,
        sum(1 for g in gaps if g > 0.02) / len(gaps) * 100))
    out['gap'] = {'mean': statistics.mean(gaps), 'p10': q(0.1), 'p50': q(0.5), 'p90': q(0.9)}

    # ---------- 2. 主矩阵：入场 × 止盈 × 止损 ----------
    print('\n===== 主矩阵（hold=5，买点=各跨度汇总）=====')
    out['matrix'] = {}
    for entry in ('close', 'open'):
        for tp in (0.02, 0.025, 0.03):
            for sl in (0.03, 0.05, 0.08):
                rows = []
                for i, t, d, t0 in cand:
                    r = sim2(stocks[i], t, t0, d, tp, sl, HOLD_DEF, entry)
                    if r:
                        r['d'] = d
                        rows.append(r)
                key = '%s_tp%d_sl%d' % (entry, round(tp * 100), round(sl * 100))
                s = detail(rows)
                s['by_d'] = {d: detail([r for r in rows if r['d'] == d]) for d in range(1, 6)}
                out['matrix'][key] = s
                print('  %-22s %s' % (key, line(s)))

    # ---------- 3. 结构止损 ----------
    print('\n===== 结构止损（跌破 T0 阳线开盘价即走）=====')
    for entry in ('close', 'open'):
        for tp in (0.02, 0.03):
            rows = []
            for i, t, d, t0 in cand:
                r = sim2(stocks[i], t, t0, d, tp, 0.99, HOLD_DEF, entry, struct_sl=True)
                if r:
                    r['d'] = d
                    rows.append(r)
            s = detail(rows)
            out['struct_%s_tp%d' % (entry, round(tp * 100))] = s
            print('  %-22s %s' % ('%s_tp%d_struct' % (entry, round(tp * 100)), line(s)))

    # ---------- 4. 按跨度 × 入场（tp2 / sl5）----------
    print('\n===== 跨度 × 入场（tp=2%%, sl=5%%, hold=5）=====')
    for entry in ('close', 'open'):
        for d in range(1, 6):
            rows = []
            for i, t, dd, t0 in cand:
                if dd != d:
                    continue
                r = sim2(stocks[i], t, t0, d, 0.02, 0.05, HOLD_DEF, entry)
                if r:
                    rows.append(r)
            s = detail(rows)
            out['span_%s_d%d' % (entry, d)] = s
            print('  跨度%d天 %-6s %s' % (d + 1, entry, line(s)))

    # ---------- 5. 年度稳定性 ----------
    print('\n===== 年度稳定性（tp2/sl5/h5，收盘买）=====')
    byy = defaultdict(list)
    for i, t, d, t0 in cand:
        r = sim2(stocks[i], t, t0, d, 0.02, 0.05, HOLD_DEF, 'close')
        if r:
            byy[stocks[i].d[t][:4]].append(r)
    out['by_year'] = {}
    for y in sorted(byy):
        out['by_year'][y] = detail(byy[y])
        print('  %s %s' % (y, line(out['by_year'][y])))

    json.dump(out, open(os.path.join(BASE, 'nshape_bt_v2.json'), 'w'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()

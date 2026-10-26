#!/usr/bin/env python3
"""补三组交叉表：① 平台突破的可买性 ② 过滤后按跨度 ③ 市场状态开关。"""
import json
import os
import sys
import statistics
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nshape_backtest import load_all, find_setups, limit_pct, BASE
from nshape_final import sim3, filt, stat
import breakout_study as bs

stocks = load_all()
print('[load] %d' % len(stocks), flush=True)
out = {}

# ---------- ① 平台突破日的涨停占比（可买性）----------
lim, tot = 0, 0
for si, st in enumerate(stocks):
    n = len(st.c)
    t = bs.W + 60
    lp = limit_pct(st.code, st.name)
    while t < n - 1:
        pf = bs.platform(st, t)
        if not pf:
            t += 1
            continue
        hi, lo = pf
        for k in range(t, min(t + 40, n)):
            if st.c[k] > hi * 1.005:
                m20 = statistics.mean(st.a[k - 20:k])
                if st.a[k] >= m20 * 1.8 and st.pct(k) >= 0.05:
                    tot += 1
                    if st.pct(k) >= lp - 0.003:
                        lim += 1
                break
            if st.c[k] < lo * 0.97:
                break
        t += 1
print('\n① 平台突破 %d 次，其中收盘涨停（买不进）%d 次（%.0f%%）' %
      (tot, lim, 100.0 * lim / max(1, tot)))
out['plat_brk'] = {'n': tot, 'limit': lim}

# ---------- ② 过滤后 跨度 × 止盈 ----------
cand = []
for idx, st in enumerate(stocks):
    for (t, d, t0) in find_setups(st):
        if filt(st, t, t0):
            cand.append((idx, t, d, t0))
print('② 过滤后候选 %d' % len(cand))

print('\n   跨度   tp2%%胜率  期望    | tp3%%胜率  期望    | 样本')
for d in range(1, 6):
    line = []
    for tp in (0.02, 0.03):
        rows = []
        for i, t, dd, t0 in cand:
            if dd != d:
                continue
            r = sim3(stocks[i], t, t0, tp, 'struct')
            if r:
                rows.append(r)
        s = stat(rows)
        line.append((s['win'], s['avg'], s['n']) if s else (0, 0, 0))
    print('   跨度%d天 %6.1f%% %+7.3f%%  | %6.1f%% %+7.3f%%  | %d' %
          (d + 1, line[0][0] * 100, line[0][1] * 100,
           line[1][0] * 100, line[1][1] * 100, line[0][2]))
    out['span_%d' % (d + 1)] = {'tp2': line[0], 'tp3': line[1]}

# ---------- ③ 市场状态开关 ----------
limit_cnt = defaultdict(int)
for st in stocks:
    lp = limit_pct(st.code, st.name)
    for i in range(len(st.c)):
        if st.pct(i) >= lp - 0.003:
            limit_cnt[st.d[i]] += 1

print('\n③ 市场状态（当日全市场涨停家数）下的 tp2%%/结构止损 表现：')
for lo, hi, name in ((0, 40, '≤40家（冰点）'), (40, 70, '41-70家'),
                     (70, 100, '71-100家'), (100, 9999, '>100家（活跃）')):
    rows = []
    for i, t, d, t0 in cand:
        c = limit_cnt.get(stocks[i].d[t], 0)
        if not (lo < c <= hi):
            continue
        r = sim3(stocks[i], t, t0, 0.02, 'struct')
        if r:
            rows.append(r)
    s = stat(rows)
    if s:
        print('   %-14s n=%-4d 胜率%5.1f%% 期望%+.3f%% 盈亏比%.2f' %
              (name, s['n'], s['win'] * 100, s['avg'] * 100, s['pf']))
        out['mkt_%d_%d' % (lo, hi)] = s

json.dump(out, open(os.path.join(BASE, 'nshape_extra.json'), 'w'), ensure_ascii=False, indent=1)

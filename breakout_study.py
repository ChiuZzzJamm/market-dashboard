#!/usr/bin/env python3
"""横盘平台突破：提前介入的量化验证 v2（修正 v1 的幸存者偏差）。

v1 的错误：只在「10 日内确实突破了」的平台里取左侧买点，等于用未来信息筛选样本，
得到 93.7% 的假胜率。v2 改为「每个贴沿状态日都买」，不论后续是否突破，
再单独做事前特征对比（突破组 vs 未突破组），找出真正可区分二者的变量。

口径：
  平台（在 t 日盘后判定，只用 t-W..t-1）
    ① W=60 根振幅 (maxHigh-minLow)/minLow <= 30%
    ② 这 W 根收盘都在 [minLow*1.015, maxHigh*0.985]
    ③ 均线粘合 max(MA5,10,20,30)/min - 1 <= 6%
    ④ 近 10 日均额 / 平台前 50 日均额 <= 0.75
  贴沿状态日 t：当日收盘距平台上沿 <= 3%，且未突破
  突破 b：收盘 > 上沿*1.005 且 成交额 >= 20 日均额*1.8 且 当日涨幅 >= 5%
"""
import json
import os
import sys
import statistics
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nshape_backtest import load_all, summarize, fmt, COST, BASE

AMP, MA_TIGHT, SHRINK, NEAR, W = 0.30, 0.06, 0.75, 0.03, 60


def platform(st, t):
    """t 日盘后，用 t-W..t-1 判定平台。返回 (hi, lo, 平台天数近似 W)。"""
    if t < W + 60:
        return None
    hs = st.h[t - W:t]
    ls = st.l[t - W:t]
    hi, lo = max(hs), min(ls)
    if lo <= 0 or (hi - lo) / lo > AMP:
        return None
    for k in range(t - W, t):
        if st.c[k] > hi * 0.985 or st.c[k] < lo * 1.015:
            return None
    mas = [st.ma5[t - 1], st.ma10[t - 1], st.ma20[t - 1], st.ma30[t - 1]]
    if min(mas) <= 0 or max(mas) / min(mas) - 1 > MA_TIGHT:
        return None
    a10 = st.a[t - 10:t]
    a50 = st.a[t - 60:t - 10]
    if not a50 or statistics.mean(a50) <= 0:
        return None
    if statistics.mean(a10) / statistics.mean(a50) > SHRINK:
        return None
    return hi, lo


def sim(st, t, tp, sl, hold):
    n = len(st.c)
    if t >= n - 1:
        return None
    buy = st.c[t]
    start = t + 1
    if buy <= 0:
        return None
    hi_p, lo_p = buy * (1 + tp), buy * (1 - sl)
    for j in range(start, min(start + hold, n)):
        o, h, l = st.o[j], st.h[j], st.l[j]
        if h >= hi_p and l <= lo_p:
            return {'ret': -sl - COST, 'exit': 'sl', 'days': j - t}
        if h >= hi_p:
            return {'ret': max(tp, o / buy - 1) - COST, 'exit': 'tp', 'days': j - t}
        if l <= lo_p:
            return {'ret': min(-sl, o / buy - 1) - COST, 'exit': 'sl', 'days': j - t}
    j = min(start + hold, n) - 1
    return {'ret': st.c[j] / buy - 1 - COST, 'exit': 'time', 'days': j - t,
            'maxup': max(st.h[start:j + 1]) / buy - 1 if j >= start else 0}


def main():
    stocks = load_all()
    print('[load] %d 只' % len(stocks), flush=True)

    states = []     # 每个贴沿状态日
    brks = []       # 每个突破日
    platforms = 0
    for si, st in enumerate(stocks):
        n = len(st.c)
        t = W + 60
        while t < n - 1:
            pf = platform(st, t)
            if not pf:
                t += 1
                continue
            platforms += 1
            hi, lo = pf
            # 找这个平台的结局：突破 / 破位 / 拖到窗口末
            end = None
            for k in range(t, min(t + 40, n)):
                if st.c[k] > hi * 1.005:
                    m20 = statistics.mean(st.a[k - 20:k]) if k >= 20 else st.a[k]
                    if st.a[k] >= m20 * 1.8 and st.pct(k) >= 0.05:
                        end = ('brk', k)
                    else:
                        end = ('weak', k)
                    break
                if st.c[k] < lo * 0.97:
                    end = ('fail', k)
                    break
            # 贴沿状态日：突破之前的每一根「距上沿 <=3% 且未突破」的 K
            if end and end[0] in ('brk', 'weak'):
                k0 = end[1]
                for k in range(t, k0):
                    if hi * (1 - NEAR) <= st.c[k] <= hi * 1.005:
                        states.append((si, k, hi, lo, end[0], k0 - k))
                if end[0] == 'brk':
                    brks.append((si, k0, hi, lo))
                    t = k0 + 1
                else:
                    t = k0 + 1
            elif end and end[0] == 'fail':
                for k in range(t, end[1]):
                    if hi * (1 - NEAR) <= st.c[k] <= hi * 1.005:
                        states.append((si, k, hi, lo, 'fail', end[1] - k))
                t = end[1] + 1
            else:
                # 40 日内没走出结局（继续横盘/阴跌但没破位）：这段贴沿日同样是
                # 实盘会遇到的情况，v1 把它漏掉会造成胜率虚高，必须计入。
                kk = min(t + 40, n)
                for k in range(t, kk):
                    if hi * (1 - NEAR) <= st.c[k] <= hi * 1.005:
                        states.append((si, k, hi, lo, 'none', kk - k))
                t = kk + 1
        if si % 500 == 0:
            print('  %d/%d 平台%d 状态%d 突破%d' % (si, len(stocks), platforms, len(states), len(brks)), flush=True)

    print('\n[样本] 平台 %d 个，贴沿状态日 %d 个，合格突破 %d 次' % (platforms, len(states), len(brks)))

    # ---- 提前介入（无偏）：每个贴沿日收盘买入 ----
    print('\n===== 提前介入：每个贴沿日都买（无未来信息）=====')
    out = {}
    for tp in (0.02, 0.03, None):
        rows = []
        for si, k, hi, lo, res, lead in states:
            r = sim(stocks[si], k, tp or 0.999, 0.05, 10)
            if r:
                r['res'] = res
                r['lead'] = lead
                rows.append(r)
        s = summarize([{'ret': r['ret'], 'exit': r['exit'], 'days': r['days']} for r in rows])
        out['left_tp%s' % (tp or 'none')] = s
        tag = ('%d%%止盈' % round(tp * 100)) if tp else '不设止盈持10日'
        print('  %-14s %s' % (tag, fmt(s)))

    # ---- 突破日买 ----
    print('\n===== 突破日收盘买 =====')
    for tp in (None, 0.02, 0.03):
        rows = []
        for si, k, hi, lo in brks:
            r = sim(stocks[si], k, tp or 0.999, 0.05, 10)
            if r:
                rows.append(r)
        s = summarize([{'ret': r['ret'], 'exit': r['exit'], 'days': r['days']} for r in rows])
        out['brk_tp%s' % (tp or 'none')] = s
        tag = ('%d%%止盈' % round(tp * 100)) if tp else '不设止盈持10日'
        print('  %-14s %s' % (tag, fmt(s)))

    # ---- 事前特征：突破 vs 未突破 ----
    print('\n===== 贴沿日的事前特征（突破组 vs 未突破组）=====')
    feats = ['dist', 'shrink10', 'matight', 'amp', 'ma60slope', 'pre20',
             'volratio', 'upper', 'lower', 'body', 'close_pos']
    acc = defaultdict(lambda: {'brk': [], 'fail': []})
    for si, k, hi, lo, res, lead in states:
        st = stocks[si]
        f = {}
        f['dist'] = hi / st.c[k] - 1
        f['shrink10'] = (statistics.mean(st.a[k - 9:k + 1]) /
                         max(1e-9, statistics.mean(st.a[k - 59:k - 9])))
        mas = [st.ma5[k], st.ma10[k], st.ma20[k], st.ma30[k]]
        f['matight'] = max(mas) / min(mas) - 1 if min(mas) > 0 else 0
        f['amp'] = (hi - lo) / lo if lo > 0 else 0
        f['ma60slope'] = (st.ma60[k] / st.ma60[k - 10] - 1) if st.ma60[k] and st.ma60[k - 10] else 0
        f['pre20'] = st.c[k] / st.c[k - 20] - 1 if k >= 20 else 0
        f['volratio'] = st.a[k] / max(1e-9, statistics.mean(st.a[k - 5:k]))
        f['upper'] = (st.h[k] - max(st.o[k], st.c[k])) / max(1e-9, st.c[k])
        f['lower'] = (min(st.o[k], st.c[k]) - st.l[k]) / max(1e-9, st.c[k])
        f['body'] = st.c[k] / st.o[k] - 1 if st.o[k] else 0
        f['close_pos'] = (st.c[k] - lo) / (hi - lo) if hi > lo else 1
        grp = 'brk' if res == 'brk' else 'fail'
        for key in feats:
            acc[key][grp].append(f[key])

    print('  %-12s %10s %10s %9s' % ('特征', '突破组均值', '未突破组', '差异'))
    for key in feats:
        a = acc[key]['brk']
        b = acc[key]['fail']
        if not a or not b:
            continue
        ma, mb = statistics.mean(a), statistics.mean(b)
        print('  %-12s %10.4f %10.4f %9.4f' % (key, ma, mb, ma - mb))

    # ---- 用「突破前最后 3 日」vs「提前 5-10 日」看时间结构 ----
    print('\n===== 提前天数 vs 胜率（2%止盈）=====')
    for lo_d, hi_d in ((0, 1), (1, 2), (2, 3), (3, 5), (5, 11)):
        rows = []
        for si, k, hi_, lo_, res, lead in states:
            if not (lo_d <= lead < hi_d):
                continue
            r = sim(stocks[si], k, 0.02, 0.05, 10)
            if r:
                rows.append(r)
        s = summarize([{'ret': r['ret'], 'exit': r['exit'], 'days': r['days']} for r in rows]) if rows else None
        out['lead_%d_%d' % (lo_d, hi_d)] = s
        print('  距突破 %d~%d 日  %s' % (lo_d, hi_d - 1, fmt(s) if s else '无样本'))

    json.dump(out, open(os.path.join(BASE, 'breakout_result.json'), 'w'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""断板反包止损口径对照（R100z232 第二步）。

`duanban_study.py` 量出第一件大事：现状口径胜率 65.3%、**均收益 +0.010%** ——
恰好卡在保本线上。原因不是选股，是**赔率结构**：盈利单平均 +2.85%（3% 止盈），
亏损单平均 −5.34%（结构止损 = T 日最低价，中位宽 6~8%）。65% 胜率 × 2.85 与
35% 亏损 × 5.34 刚好相抵。

所以「提升准确度」的第二步不是再加选股条件（那只会砍样本），而是问：
**止损能不能放得更近，让一次亏损不再需要 2 次盈利来补？**

三种更近的锚：
    wash_low  T+1 断板日 .. 信号日 的最低价（洗盘段最低，形态破了就是破了）
    sig_low   信号日当天最低价（最近、最容易被噪音打掉）
    atr_k     信号日收盘 − k×ATR14（波动率自适应）
对照基准：zt_low（T 日最低价，生产现用）

用法：python3 duanban_tune.py
"""
import statistics
import sys

import duanban_study as DS
import nshape_backtest as NB

COST = DS.COST


def sim(smp, stop_mode, tp, hold):
    o, c, h, l = smp['arr']
    b = smp['b']
    n = len(c)
    buy = smp['buy']
    if stop_mode == 'zt_low':
        stop = smp['lowT']
    elif stop_mode == 'wash_low':
        stop = smp['wash_low']
    elif stop_mode == 'sig_low':
        stop = smp['sig_low']
    elif stop_mode.startswith('atr'):
        k = float(stop_mode[3:]) / 10.0
        stop = smp['sig_close'] - k * smp['atr']
    elif stop_mode.startswith('fix'):
        stop = buy * (1 - float(stop_mode[3:]) / 100.0)
    else:
        raise ValueError(stop_mode)
    # 止损必须严格低于买入价，且不浅于 1.5%（否则等于没设，被日内噪音秒杀）
    if stop > buy * 0.985:
        stop = buy * 0.985
    if stop < buy * 0.85:
        stop = buy * 0.85
    for j in range(b, min(b + hold, n)):
        if o[j] <= stop:
            return (o[j] / buy - 1) * 100 - COST * 100, 'stop_gap', j - b + 1
        if l[j] <= stop:
            return (stop / buy - 1) * 100 - COST * 100, 'stop', j - b + 1
        if o[j] >= buy * (1 + tp):
            return (o[j] / buy - 1) * 100 - COST * 100, 'tp_gap', j - b + 1
        if h[j] >= buy * (1 + tp):
            return tp * 100 - COST * 100, 'tp', j - b + 1
    j = min(b + hold, n) - 1
    return (c[j] / buy - 1) * 100 - COST * 100, 'time', j - b + 1


def stat(rows):
    n = len(rows)
    if n < 30:
        return None
    rets = [r[0] for r in rows]
    gains = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    pf = (sum(gains) / -sum(losses)) if losses and sum(losses) < 0 else 9.99
    return dict(n=n, win=sum(1 for x in rets if x > 0) / n * 100,
                avg=sum(rets) / n, med=statistics.median(rets), pf=pf,
                ag=sum(gains) / len(gains) if gains else 0,
                al=sum(losses) / len(losses) if losses else 0,
                stop_rate=sum(1 for r in rows if r[1].startswith('stop')) / n * 100)


def main():
    stocks = NB.load_all()
    smps = DS.collect(stocks)
    print('[scan] %d 个信号' % len(smps), flush=True)

    # ---- A. 止损口径 × 止盈 矩阵（全样本）
    print()
    print('=== A. 止损口径对照（全样本 %d 笔） ===' % len(smps))
    print('%-10s %5s %-4s %6s %7s %8s %8s %6s %7s'
          % ('止损锚', '止盈', '持有', '笔数', '胜率', '均收益', '中位', '盈亏比', '止损率'))
    best = []
    for sm in ('zt_low', 'wash_low', 'sig_low', 'atr15', 'atr20', 'fix3', 'fix4', 'fix5'):
        for tp, hold in ((0.03, 5), (0.03, 10), (0.05, 10)):
            rows = [sim(s, sm, tp, hold) for s in smps]
            st = stat(rows)
            if not st:
                continue
            print('%-10s %5.0f%% %-4d %6d %6.1f%% %+8.3f%% %+8.3f%% %6.2f %6.1f%%'
                  % (sm, tp * 100, hold, st['n'], st['win'], st['avg'], st['med'],
                     st['pf'], st['stop_rate']))
            best.append((st['avg'], sm, tp, hold, st))

    print()
    print('=== B. 叠加选股过滤（止损 wash_low / 止盈 3% / 持 10 日） ===')

    def filt(r, ma5_on=True, mc=0, pre=1e9, up=1e9, drop1=None, pos=0):
        f = r['feat']
        if ma5_on and f['dist_ma5'] < 0:
            return False
        if f['mcap'] < mc:
            return False
        if f['pre20'] > pre:
            return False
        if f['upper_s'] >= up:
            return False
        if drop1 is not None and f['pct1'] < drop1:
            return False
        if f['pos250'] < pos:
            return False
        return True

    print('%-42s %6s %7s %8s %6s' % ('过滤条件', '笔数', '胜率', '均收益', '盈亏比'))
    combos = [
        ('现状（无额外过滤）', dict(ma5_on=False)),
        ('站上MA5（距MA5≥0）', dict()),
        ('+市值≥100亿', dict(mc=100)),
        ('+市值≥100 + 前20≤10', dict(mc=100, pre=10)),
        ('+市值≥100 + 前20≤10 + 上影<3', dict(mc=100, pre=10, up=3)),
        ('+市值≥100 + 前20≤10 + 上影<3 + 断板日>-5%', dict(mc=100, pre=10, up=3, drop1=-5.0)),
        ('+市值≥50 + 前20≤10 + 上影<3', dict(mc=50, pre=10, up=3)),
        ('+市值≥100 + 上影<3', dict(mc=100, up=3)),
    ]
    for nm, kw in combos:
        sub = [s for s in smps if filt(s, **kw)]
        rows = [sim(s, 'wash_low', 0.03, 10) for s in sub]
        st = stat(rows)
        if not st:
            print('%-42s %6d  样本不足' % (nm, len(sub)))
            continue
        print('%-42s %6d %6.1f%% %+8.3f%% %6.2f' % (nm, st['n'], st['win'], st['avg'], st['pf']))

    # ---- C. 最优组合的止损口径再扫一遍
    print()
    print('=== C. 「站上MA5 + 市值≥100 + 前20≤10 + 上影<3」下，止损锚再选一次 ===')
    sub = [s for s in smps if filt(s, mc=100, pre=10, up=3)]
    print('%-10s %5s %5s %6s %7s %8s %6s' % ('止损锚', '止盈', '持有', '笔数', '胜率', '均收益', '盈亏比'))
    for sm in ('zt_low', 'wash_low', 'sig_low', 'atr15', 'atr20', 'fix3', 'fix4', 'fix5'):
        for tp in (0.03, 0.05):
            rows = [sim(s, sm, tp, 10) for s in sub]
            st = stat(rows)
            if st:
                print('%-10s %5.0f%% %-5d %6d %6.1f%% %+8.3f%% %6.2f'
                      % (sm, tp * 100, 10, st['n'], st['win'], st['avg'], st['pf']))

    # ---- D. 训练/样本外：最优 vs 现状
    print()
    print('=== D. 训练 / 样本外（最优组合 vs 现状） ===')
    split = '2026-01-01'
    print('%-30s %-7s %6s %7s %8s %6s' % ('口径', '区间', '笔数', '胜率', '均收益', '盈亏比'))
    for nm, kw, sm, tp in (
            ('现状(z t_low/TP3/H5)', dict(ma5_on=False), 'zt_low', 0.03),
            ('优化(wash_low/TP3/H10+过滤)', dict(mc=100, pre=10, up=3), 'wash_low', 0.03),
            ('优化(wash_low/TP5/H10+过滤)', dict(mc=100, pre=10, up=3), 'wash_low', 0.05)):
        for seg, cond in (('训练', lambda s: s['sig_day'] < split),
                          ('样本外', lambda s: s['sig_day'] >= split)):
            sub2 = [s for s in smps if filt(s, **kw) and cond(s)]
            hold = 5 if 'H5' in nm else 10
            rows = [sim(s, sm, tp, hold) for s in sub2]
            st = stat(rows)
            if st:
                print('%-30s %-7s %6d %6.1f%% %+8.3f%% %6.2f'
                      % (nm, seg, st['n'], st['win'], st['avg'], st['pf']))
    return 0


if __name__ == '__main__':
    sys.exit(main())

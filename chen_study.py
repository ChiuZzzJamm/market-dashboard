#!/usr/bin/env python3
"""陈学长口径全市场回测（R100z232，2026-10-26）。

## 与 duanban_study 的关系

两套口径是**并列**的（用户 2026-10-25 明确要求「建一个陈学长技术指标，而不是更新断板反包」），
所以这里不合并、不互相替换，只把 `chen_pool.py` 的冻结参数逐条量一遍。

## 冻结参数（chen_pool.py 顶部常量，2026-10-25 定）

    S1 股价 ≥ 4 元
    S2 T 日涨停（主板 9.8%）
    S4 T+1 断板放量  V(T+1)/V(T) ≥ 2.0（**无上限**）且 T+1 未涨停
    S5 T+2 缩量      V(T+2) < V(T+1)
    S7 不破 T 日低点  收盘 ≥ low(T)×0.98
    S8 站稳 MA5      从 T+2 起第一根 收盘 ≥ MA5×0.97 = 确认日
    窗口            T+2 .. T+4（MAX_WASH_DAYS=4）
    买点            确认日**次日开盘**
    止盈            +3.5%
    止损            洗盘段最低价（T+1..确认日 的最低价），最少 2% 距离
    持有上限        2 个交易日

在 `duanban` 口径里，同一个形态的差异只有三处：放量上限（3.5 vs 无上限）、
止损锚（T 日最低价 vs 洗盘段最低价）、止盈持有（3%/5 日 vs 3.5%/2 日）。
这三处恰恰是「胜率差」的全部来源——所以必须分开量，不能合并。

用法：python3 chen_study.py
"""
import statistics
import sys

import nshape_backtest as NB

COST = 0.0016
PRICE_MIN = 4.0
VOL_UP_MIN = 2.0
MAX_WASH_DAYS = 4
LOW_TOL = 0.98
MA5_TOL = 0.97
TARGET_PCT = 3.5
HOLD_DAYS = 2


def _ma5(c, j):
    if j < 4:
        return 0.0
    return sum(c[j - 4:j + 1]) / 5.0


def collect(stocks):
    """陈学长口径样本：每只票每个涨停取**第一个确认日**。"""
    out = []
    for st in stocks:
        if not st.code.startswith(('60', '00')):
            continue
        n = len(st.c)
        if n < 40:
            continue
        iT = 5
        while iT < n - 2:
            if st.pct(iT) < 0.098 or not st.gap_ok(iT):
                iT += 1
                continue
            if st.c[iT] < PRICE_MIN:
                iT += 1
                continue
            vT = st.v[iT]
            if vT <= 0:
                iT += 1
                continue
            i1 = iT + 1
            if i1 >= n:
                break
            r1 = st.v[i1] / vT
            if st.pct(i1) >= 0.098 or r1 < VOL_UP_MIN:
                iT += 1
                continue
            lowT = st.l[iT]
            if lowT <= 0 or min(st.c[i1:n]) < lowT * LOW_TOL:
                iT += 1
                continue
            i2 = iT + 2
            if i2 >= n - 1 or not (0 < st.v[i2] < st.v[i1]):
                iT += 1
                continue
            # 确认日 = 从 T+2 起第一根站上 MA5 的交易日，且必须在 T+4 窗口内
            conf = None
            for j in range(i2, min(iT + MAX_WASH_DAYS + 1, n)):
                m = _ma5(st.c, j)
                if m > 0 and st.c[j] >= m * MA5_TOL:
                    conf = j
                    break
            if conf is None or conf + 1 >= n:
                iT += 1
                continue
            b = conf + 1
            buy = st.o[b]
            if buy <= 0 or buy < PRICE_MIN:
                iT += 1
                continue
            if st.o[b] >= st.c[conf] * 1.095:          # 一字板买不进
                iT = conf + 1
                continue
            wash_low = min(st.l[i1:conf + 1])
            if buy <= wash_low:                         # 开盘已在止损位下，不下单
                iT = conf + 1
                continue
            lbc = 0
            k = iT
            while k >= 1 and st.pct(k) >= 0.098:
                lbc += 1
                k -= 1
            v5 = [st.v[i] for i in range(max(0, iT - 5), iT)]
            avg5 = (sum(v5) / len(v5)) if v5 else 0.0
            out.append(dict(
                code=st.code, name=st.name, sig_day=st.d[conf], buy_day=st.d[b],
                buy=buy, wash_low=wash_low, lowT=lowT, zt_day=st.d[iT],
                arr=(st.o, st.c, st.h, st.l), b=b,
                feat=dict(
                    mcap=(st.mcap or 0) / 1e8,
                    pre20=(st.c[iT - 1] / st.c[iT - 21] - 1) * 100 if iT >= 21 else 0.0,
                    r1=r1,
                    shr_t=(vT / avg5) if avg5 else 0.0,
                    drop1=st.pct(i1) * 100,
                    upper_c=(st.h[conf] - max(st.o[conf], st.c[conf])) / st.c[conf] * 100,
                    dist_ma5=(st.c[conf] / _ma5(st.c, conf) - 1) * 100,
                    pos250=(st.c[conf] / (st.hi250[conf] or st.h[conf])) * 100,
                    amp=(st.h[iT] - st.l[iT]) / st.c[iT] * 100,
                    lbc=lbc,
                    lag=conf - i1,
                    stop_pct=(wash_low / buy - 1) * 100,
                )))
            iT = conf + 1
    return out


def sim(smp, tp=TARGET_PCT / 100.0, hold=HOLD_DAYS, stop_mode='wash_low'):
    o, c, h, l = smp['arr']
    b = smp['b']
    n = len(c)
    buy = smp['buy']
    stop = smp['wash_low'] if stop_mode == 'wash_low' else smp['lowT']
    if (buy - stop) / buy < 0.02:                  # 最小 2% 距离（chen_pool 同口径）
        stop = buy * 0.98
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
                al=sum(losses) / len(losses) if losses else 0)


HEAD = ('%-30s %6s %7s %8s %8s %6s %7s %7s'
        % ('分层', '笔数', '胜率', '均收益', '中位', '盈亏比', '均盈', '均亏'))


def line(s):
    if not s:
        return '  样本不足'
    return ('%-30s %6d %6.1f%% %+8.3f%% %+8.3f%% %6.2f %+6.2f%% %+6.2f%%'
            % (s['label'], s['n'], s['win'], s['avg'], s['med'], s['pf'], s['ag'], s['al']))


def main():
    smps = collect(NB.load_all())
    print('[scan] 陈学长口径确认信号 %d 个' % len(smps), flush=True)
    rows = []
    for s in smps:
        ret, ex, dys = sim(s)
        rows.append(dict(s, ret=ret, exit=ex, days=dys))

    def show(nm, sub):
        s = stat([(r['ret'], r['exit'], r['days']) for r in sub])
        if s:
            s['label'] = nm
            print(line(s))

    print()
    print('=== 基线（冻结口径：TP3.5%% / 持 2 日 / 洗盘段最低价止损） ===')
    print(HEAD)
    show('全样本', rows)
    print('   止损宽度中位 %.2f%%' % statistics.median([r['feat']['stop_pct'] for r in rows]))
    cnt = {}
    for r in rows:
        cnt[r['exit']] = cnt.get(r['exit'], 0) + 1
    print('   出场：' + '、'.join('%s %d(%.0f%%)' % (k, v, v / len(rows) * 100)
                                for k, v in sorted(cnt.items(), key=lambda x: -x[1])))

    split = '2026-01-01'
    print()
    print('=== 时间分段 ===')
    print(HEAD)
    show('训练 2024-01~2025-12', [r for r in rows if r['sig_day'] < split])
    show('样本外 2026-01~', [r for r in rows if r['sig_day'] >= split])

    print()
    print('=== 与断板口径的差异点逐项验证 ===')
    print(HEAD)
    # ① 放量上限：陈学长无上限
    show('放量 2.0~3.5 倍（断板上限内）', [r for r in rows if r['feat']['r1'] <= 3.5])
    show('放量 >3.5 倍（陈学长独有）', [r for r in rows if r['feat']['r1'] > 3.5])
    show('放量 >5 倍', [r for r in rows if r['feat']['r1'] > 5])
    # ② 止损锚
    rr = []
    for r in smps:
        ret, ex, dys = sim(r, stop_mode='zt_low')
        rr.append(dict(r, ret=ret, exit=ex, days=dys))
    show('止损换成 T 日最低价', rr)
    # ③ 止盈持有
    for tp, hold, lb in ((0.035, 2, 'TP3.5% / 持 2 日（现状）'),
                         (0.03, 2, 'TP3% / 持 2 日'),
                         (0.035, 5, 'TP3.5% / 持 5 日'),
                         (0.05, 5, 'TP5% / 持 5 日'),
                         (0.05, 10, 'TP5% / 持 10 日')):
        rr = []
        for r in smps:
            ret, ex, dys = sim(r, tp=tp, hold=hold)
            rr.append(dict(r, ret=ret, exit=ex, days=dys))
        show(lb, rr)

    print()
    print('=== 单因子分层 ===')
    print(HEAD)
    groups = [
        ('市值(亿)', 'mcap', [(0, 50, '<50'), (50, 100, '50~100'), (100, 300, '100~300'),
                              (300, 1e9, '>300')]),
        ('启动前20日%', 'pre20', [(-1e9, -10, '<-10'), (-10, 0, '-10~0'), (0, 10, '0~10'),
                                  (10, 100, '>10')]),
        ('放量倍数', 'r1', [(2, 3, '2~3'), (3, 5, '3~5'), (5, 8, '5~8'), (8, 99, '>8')]),
        ('T日量/前5均量', 'shr_t', [(0, 0.6, '<0.6'), (0.6, 1.0, '0.6~1.0'),
                                    (1.0, 1.5, '1.0~1.5'), (1.5, 9, '>1.5')]),
        ('断板日跌幅%', 'drop1', [(-11, -5, '<-5'), (-5, -3, '-5~-3'), (-3, 0, '-3~0'),
                                  (0, 10, '>0')]),
        ('确认日上影%', 'upper_c', [(0, 1, '<1'), (1, 3, '1~3'), (3, 100, '>3')]),
        ('距MA5%', 'dist_ma5', [(0, 2, '0~2'), (2, 5, '2~5'), (5, 100, '>5')]),
        ('250日位置%', 'pos250', [(0, 60, '<60'), (60, 80, '60~80'), (80, 95, '80~95'),
                                  (95, 101, '95~100')]),
        ('连板数', 'lbc', [(1, 2, '首板'), (2, 3, '2板'), (3, 99, '3板+')]),
        ('止损宽度%', 'stop_pct', [(-30, -10, '<-10'), (-10, -6, '-10~-6'),
                                   (-6, -3, '-6~-3'), (-3, 0, '-3~0')]),
        ('确认延迟(日)', 'lag', [(1, 2, 'T+2 当天'), (2, 3, '隔 2 日'), (3, 4, '隔 3 日')]),
    ]
    for nm, key, cuts in groups:
        print('-- %s' % nm)
        for lo, hi, lb in cuts:
            sub = [r for r in rows if lo <= r['feat'][key] < hi]
            s = stat([(r['ret'], r['exit'], r['days']) for r in sub])
            if s:
                s['label'] = '%s %s' % (nm, lb)
                if s['n'] >= 30:
                    print(line(s))

    print()
    print('=== 组合（训练挑 → 样本外验） ===')
    print(HEAD)

    def F(r, mc=0, pre=1e9, up=1e9, drop1=None):
        f = r['feat']
        return (f['mcap'] >= mc and f['pre20'] <= pre and f['upper_c'] < up
                and (drop1 is None or f['drop1'] > drop1))

    for nm, kw in (('基线', {}),
                   ('+市值≥100亿', dict(mc=100)),
                   ('+前20≤10%', dict(pre=10)),
                   ('+确认日上影<3%', dict(up=3)),
                   ('市值≥100+前20≤10', dict(mc=100, pre=10)),
                   ('市值≥100+前20≤10+上影<3', dict(mc=100, pre=10, up=3)),
                   ('市值≥100+前20≤10+上影<3+断板>-5', dict(mc=100, pre=10, up=3, drop1=-5.0))):
        sub = [r for r in rows if F(r, **kw)]
        s = stat([(r['ret'], r['exit'], r['days']) for r in sub])
        if not s:
            print('%-30s %6d  样本不足' % (nm, len(sub)))
            continue
        tr = stat([(r['ret'], r['exit'], r['days']) for r in sub if r['sig_day'] < split])
        te = stat([(r['ret'], r['exit'], r['days']) for r in sub if r['sig_day'] >= split])
        s['label'] = '%s [训%.0f%%/%d 外%.0f%%/%d]' % (
            nm, tr['win'] if tr else 0, tr['n'] if tr else 0,
            te['win'] if te else 0, te['n'] if te else 0)
        print(line(s))
    return 0


if __name__ == '__main__':
    sys.exit(main())

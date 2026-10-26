#!/usr/bin/env python3
"""断板反包口径全市场回测（R100z232，2026-10-26）。

## 为什么要有这个脚本

`check_duanban.py` 的判定条件（T 日涨停 → T+1 放量断板 1.1~3.5 倍 → T+2 起缩量
→ 不破 T 日低点 → 站稳 MA5）是 2026 年一路上叠出来的，每一道都来自「这次亏了，
加一条」，**从来没有人量过每一条到底值多少**。用户 2026-10-26 要求「深度优化
断板反包提升准确度」——优化必须先是减法：先量清楚哪道门在提供信息、哪道门只是
在减少样本。

## 口径（与 check_duanban.check_form 逐条对齐，不是另写一套）

    T 日：涨幅 >= 9.7%（主板，只取 60/00；生产也只要主板）
    T+1：量比 V(T+1)/V(T) ∈ [1.1, 3.5] 且当日未涨停（断板）
    T+2：V(T+2) < V(T+1)
    T+3..t：V(j) < V(T+1)
    全程 收盘 >= T 日最低价 × 0.98
    信号日 t：收盘 >= MA5(t) × 0.97
    t ∈ [T+2, T+5]

**一只票一次涨停只取一个样本**：取 t 最小的那个信号日（= 首次进池那天）。
生产里同一只票会在池里连续挂 4 天，若按「状态日」统计，等于一只票投 4 票，
胜率被样本量虚增；按首次进池算，才是「用户第一次看到它」的那个时点。

入场：信号日**次日开盘**（收盘才出信号，不能按当天收盘价成交）
止损：T 日最低价（生产 `build_stock_pool.zt_low` 的锚，固定不漂移）
止盈：盘中最高触及 buy×(1+tp)；跳空越线按开盘价
同日双触发按**先止损**（保守）
持有上限 hold 个交易日，未触发按收盘平仓
成本：双边 0.16%（与 backtest_indicators 同）

用法：
    python3 duanban_study.py              # 全表
    python3 duanban_study.py --quick      # 只打基线与主分层
"""
import argparse
import statistics
import sys

import nshape_backtest as NB

COST = 0.0016
HOLD = 5
TP = 0.03

# 被排除的候选计数（不是静默丢弃：这两类各自有含义，要打进报告）
SKIP = {}


def _ma5(st, s):
    try:
        return st.ma5[s]
    except IndexError:
        return None


def collect(stocks, only_main=True):
    """扫全部股票，按 check_form 口径产出样本。

    返回 [{code,name,mcap,sig_i,buy_i,buy,stop,lowT,zt_i,d,feat…}]
    """
    out = []
    for st in stocks:
        if only_main and not st.code.startswith(('60', '00')):
            continue
        n = len(st.c)
        if n < 60:
            continue
        iT = 5
        while iT < n - 2:
            # ---- T 日涨停
            if st.pct(iT) < 0.097 or not st.gap_ok(iT):
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
            # ---- T+1 断板放量
            if not (1.1 <= r1 <= 3.5) or st.pct(i1) >= 0.097:
                iT += 1
                continue
            lowT = st.l[iT]
            if lowT <= 0:
                iT += 1
                continue
            v1 = st.v[i1]
            sig = None
            for d in (2, 3, 4, 5):
                s = iT + d
                if s >= n - 1:                 # 需要 s+1 存在才能次日开盘买
                    break
                # 缩量：T+2 必查；T+3..s 逐日
                ok = True
                for j in range(iT + 2, s + 1):
                    if not (0 < st.v[j] < v1):
                        ok = False
                        break
                if not ok:
                    break                       # 缩量断了，后面也不会回头
                # 不破 T 日低点（截至 s）
                if min(st.c[iT + 1:s + 1]) < lowT * 0.98:
                    break
                m5 = _ma5(st, s)
                if m5 and st.c[s] >= m5 * 0.97:
                    sig = s
                    break
            if sig is None:
                iT += 1
                continue

            s = sig
            b = s + 1
            buy = st.o[b]
            if buy <= 0:
                iT += 1
                continue
            # 一字板买不进（次日开盘即涨停）
            if st.o[b] >= st.c[s] * 1.095:
                SKIP['limit_open'] = SKIP.get('limit_open', 0) + 1
                iT = s + 1
                continue
            # 开盘已在 T 日最低价之下 = 形态已破，按纪律不买（不是"不统计"，是"不下单"）
            if buy <= lowT:
                SKIP['below_stop'] = SKIP.get('below_stop', 0) + 1
                iT = s + 1
                continue

            # ---- 因子（全部只用 s 及之前数据）
            pre20 = (st.c[iT - 1] / st.c[iT - 21] - 1) if iT >= 21 else 0.0
            vma5T = sum(st.v[iT - 5:iT]) / 5.0 if iT >= 5 else vT
            vrT = (vT / vma5T) if vma5T else 0.0
            lbc = 0
            k = iT
            while k >= 1 and st.pct(k) >= 0.097:
                lbc += 1
                k -= 1
            ampT = (st.h[iT] - st.l[iT]) / st.c[iT] if st.c[iT] else 0.0
            upper_s = (st.h[s] - max(st.o[s], st.c[s])) / st.c[s] if st.c[s] else 0.0
            m5s = _ma5(st, s) or st.c[s]
            hi250 = st.hi250[s] or st.h[s]
            # 止损候选（供 duanban_tune 做口径对照；这里只是把事实记下来，不改判定）
            wash_low = min(st.l[iT + 1:s + 1])
            trs = []
            for j in range(max(1, s - 14), s + 1):
                trs.append(max(st.h[j] - st.l[j], abs(st.h[j] - st.c[j - 1]),
                               abs(st.l[j] - st.c[j - 1])))
            atr = (sum(trs) / len(trs)) if trs else 0.0
            feat = dict(
                mcap=(st.mcap or 0) / 1e8,
                pre20=pre20 * 100,
                vrT=vrT,
                r1=r1,
                pct1=st.pct(i1) * 100,
                shrink=(st.v[s] / v1) if v1 else 1.0,
                pct_s=st.pct(s) * 100,
                upper_s=upper_s * 100,
                dist_ma5=(st.c[s] / m5s - 1) * 100,
                pos250=(st.c[s] / hi250) * 100 if hi250 else 0.0,
                ampT=ampT * 100,
                lbc=lbc,
                d=d,
                hold_pct=(buy / st.c[s] - 1) * 100,
            )
            out.append(dict(code=st.code, name=st.name, sig_day=st.d[s], buy_day=st.d[b],
                            buy=buy, stop=lowT, lowT=lowT, zt_day=st.d[iT],
                            stop_pct=(lowT / buy - 1) * 100, feat=feat,
                            wash_low=wash_low, sig_low=st.l[s], atr=atr,
                            sig_close=st.c[s], arr=(st.o, st.c, st.h, st.l), b=b))
            iT = s + 1
    return out


def sim(smp, tp=TP, hold=HOLD, use_struct_sl=True, sl_pct=-5.0):
    """单笔模拟。返回 (ret%, exit, days)。

    ★跳空止损必须按**开盘价**成交，不能按止损价：信号日次日若直接低开穿过止损位
    （2024-10-09 那批行情里常见），按 stop 价成交等于假装「在更低的价位上有对手盘接」，
    会系统性低估亏损、把胜率抬高几个点。同一条纪律在止盈侧已经处理过（tp_gap）。
    """
    o, c, h, l = smp['arr']
    b = smp['b']
    n = len(c)
    buy = smp['buy']
    stop = smp['stop'] if use_struct_sl else buy * (1 + sl_pct / 100.0)
    if stop >= buy:
        stop = buy * 0.95
    for j in range(b, min(b + hold, n)):
        if o[j] <= stop:                       # 开盘即破位 → 按开盘价走
            return (o[j] / buy - 1) * 100 - COST * 100, 'stop_gap', j - b + 1
        if l[j] <= stop:                       # 盘中破位（同日双触发按先止损，保守）
            return (stop / buy - 1) * 100 - COST * 100, 'stop', j - b + 1
        if o[j] >= buy * (1 + tp):
            return (o[j] / buy - 1) * 100 - COST * 100, 'tp_gap', j - b + 1
        if h[j] >= buy * (1 + tp):
            return tp * 100 - COST * 100, 'tp', j - b + 1
    j = min(b + hold, n) - 1
    return (c[j] / buy - 1) * 100 - COST * 100, 'time', j - b + 1


def summarize(rows, label=''):
    n = len(rows)
    if not n:
        return None
    rets = [r['ret'] for r in rows]
    win = sum(1 for x in rets if x > 0)
    gains = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    avg = sum(rets) / n
    pf = (sum(gains) / -sum(losses)) if losses and sum(losses) < 0 else float('inf')
    return dict(label=label, n=n, win=win / n * 100, avg=avg,
                med=statistics.median(rets),
                pf=pf if pf != float('inf') else 9.99,
                ag=sum(gains) / len(gains) if gains else 0,
                al=sum(losses) / len(losses) if losses else 0,
                days=sum(r['days'] for r in rows) / n)


def line(s):
    return ('%-30s %6d %6.1f%% %+7.3f%% %+7.3f%% %5.2f %+6.2f%% %+6.2f%%'
            % (s['label'], s['n'], s['win'], s['avg'], s['med'], s['pf'], s['ag'], s['al']))


HEAD = ('%-30s %6s %7s %8s %8s %5s %7s %7s'
        % ('分层', '笔数', '胜率', '均收益', '中位', '盈亏比', '均盈', '均亏'))


def bucket(rows, key, cuts, fmt='%.1f', name=''):
    """按 feat[key] 阈值切桶，逐桶统计。cuts = [(lo, hi, 标签), …]"""
    out = []
    for lo, hi, lb in cuts:
        sub = [r for r in rows if lo <= r['feat'][key] < hi]
        s = summarize(sub, '%s %s' % (name or key, lb))
        if s and s['n'] >= 30:
            out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    a = ap.parse_args()

    stocks = NB.load_all()
    print('[load] %d 只（主板口径筛前）' % len(stocks), flush=True)

    # ---- 信号只扫一遍（3908 只 × 920 根，重复扫是纯浪费）
    smps = collect(stocks)
    _CACHE.extend(smps)
    print('[scan] 断板反包合格信号 %d 个（每涨停只取首次进池日）' % len(smps), flush=True)
    if SKIP:
        print('[scan] 形态成立但**不可下单**而剔除：' + '、'.join(
            '%s %d' % ('次日一字涨停买不进' if k == 'limit_open' else '次日开盘已在T日低点之下',
                       v) for k, v in sorted(SKIP.items())))

    # ---- 分段：训练 2024-01~2025-12 / 样本外 2026-01~
    raw = []
    split = '2026-01-01'
    for tp, hold, tag in ((0.03, 5, 'TP3%/H5'), (0.02, 5, 'TP2%/H5'), (0.03, 10, 'TP3%/H10')):
        rows = []
        for smp in smps:
            ret, ex, dys = sim(smp, tp=tp, hold=hold)
            rows.append(dict(smp, ret=ret, exit=ex, days=dys))
        raw.append((tag, rows))
        print(line(summarize(rows, tag)), flush=True)

    tag, rows = raw[0]        # 主口径 TP3%/H5 做后续分层
    print()
    print('=== 基线（%s）：%d 笔，%d 只标的，%d 个信号日 ==='
          % (tag, len(rows), len(set(r['code'] for r in rows)),
             len(set(r['sig_day'] for r in rows))))
    base = summarize(rows, '全样本')
    print(HEAD)
    print(line(base))
    print('   止损宽度中位 %.2f%%（结构止损=T 日最低价），盈亏比 %.2f'
          % (statistics.median([r['stop_pct'] for r in rows]), base['pf']))

    # ---- 出场方式分布
    cnt = {}
    for r in rows:
        cnt[r['exit']] = cnt.get(r['exit'], 0) + 1
    print('   出场：' + '、'.join('%s %d(%.1f%%)' % (k, v, v / len(rows) * 100)
                                for k, v in sorted(cnt.items(), key=lambda x: -x[1])))

    # ---- 训练/样本外
    tr = [r for r in rows if r['sig_day'] < split]
    te = [r for r in rows if r['sig_day'] >= split]
    print()
    print('=== 时间分段 ===')
    print(HEAD)
    print(line(summarize(tr, '训练 2024-01~2025-12')))
    print(line(summarize(te, '样本外 2026-01~')))

    print()
    print('=== 单因子分层（主口径 %s） ===' % tag)
    print(HEAD)
    groups = [
        ('市值(亿)', 'mcap', [(0, 30, '<30'), (30, 100, '30~100'), (100, 300, '100~300'),
                              (300, 1e9, '>300')], ''),
        ('启动前20日涨幅%', 'pre20', [(-1e9, -10, '<-10'), (-10, 0, '-10~0'),
                                     (0, 10, '0~10'), (10, 100, '>10')], ''),
        ('T日量比', 'vrT', [(0, 1.5, '<1.5'), (1.5, 2.5, '1.5~2.5'), (2.5, 4, '2.5~4'),
                            (4, 1e9, '>4')], ''),
        ('断板日量比r1', 'r1', [(1.1, 1.5, '1.1~1.5'), (1.5, 2.2, '1.5~2.2'),
                                (2.2, 3.5, '2.2~3.5')], ''),
        ('断板日跌幅%', 'pct1', [(-11, -5, '<-5'), (-5, -2, '-5~-2'), (-2, 0, '-2~0'),
                                 (0, 10, '>0')], ''),
        ('信号日缩量比', 'shrink', [(0, 0.4, '<0.4'), (0.4, 0.6, '0.4~0.6'),
                                    (0.6, 0.8, '0.6~0.8'), (0.8, 1.0, '0.8~1.0')], ''),
        ('信号日涨幅%', 'pct_s', [(-11, -2, '<-2'), (-2, 1, '-2~1'), (1, 4, '1~4'),
                                  (4, 10, '>4')], ''),
        ('信号日上影%', 'upper_s', [(0, 1, '<1'), (1, 3, '1~3'), (3, 6, '3~6'),
                                    (6, 100, '>6')], ''),
        ('距MA5%', 'dist_ma5', [(-4, 0, '<0'), (0, 2, '0~2'), (2, 5, '2~5'), (5, 100, '>5')], ''),
        ('250日位置%', 'pos250', [(0, 60, '<60'), (60, 80, '60~80'), (80, 95, '80~95'),
                                  (95, 101, '95~100')], ''),
        ('连板数', 'lbc', [(1, 2, '首板'), (2, 3, '2连板'), (3, 100, '3连板+')], ''),
        ('距T日天数', 'd', [(2, 3, 'T+2'), (3, 4, 'T+3'), (4, 5, 'T+4'), (5, 6, 'T+5')], ''),
        ('次日跳空%', 'hold_pct', [(-10, -1, '<-1'), (-1, 1, '-1~1'), (1, 4, '1~4'),
                                   (4, 10, '>4')], ''),
    ]
    for nm, key, cuts, _ in groups:
        bs = bucket(rows, key, cuts, name=nm)
        if not bs:
            continue
        print('-- %s' % nm)
        for b in bs:
            print(line(b))

    if a.quick:
        return 0

    # ---- 组合：用训练段挑条件，样本外验证
    print()
    print('=== 组合过滤（训练段挑阈值 → 样本外验证，避免拿未来数据凑好看） ===')
    print(HEAD)

    def cond(r, mc=0, pre=1e9, up=1e9, dmin=0, dmax=99, lbc=0, pos=0):
        f = r['feat']
        return (f['mcap'] >= mc and f['pre20'] <= pre and f['upper_s'] < up
                and dmin <= f['d'] <= dmax and f['lbc'] >= lbc and f['pos250'] >= pos)

    combos = [
        ('基线（现状口径）', dict()),
        ('+市值>=50亿', dict(mc=50)),
        ('+市值>=100亿', dict(mc=100)),
        ('+启动前20日<=10%', dict(pre=10)),
        ('+信号日上影<3%', dict(up=3)),
        ('+T+2或T+3进场', dict(dmin=2, dmax=3)),
        ('+首板（非连板）', dict(lbc=1, pos=0)),
        ('市值>=100 + 前20<=10', dict(mc=100, pre=10)),
        ('市值>=100 + 上影<3', dict(mc=100, up=3)),
        ('市值>=100 + 前20<=10 + 上影<3', dict(mc=100, pre=10, up=3)),
        ('市值>=50 + 前20<=10 + 上影<3 + T+2/3', dict(mc=50, pre=10, up=3, dmin=2, dmax=3)),
        ('市值>=100+前20<=10+上影<3+T+2/3', dict(mc=100, pre=10, up=3, dmin=2, dmax=3)),
        ('市值>=100+前20<=10+上影<3+250位>=60', dict(mc=100, pre=10, up=3, pos=60)),
    ]
    for nm, kw in combos:
        sub = [r for r in rows if cond(r, **kw)]
        subtr = [r for r in sub if r['sig_day'] < split]
        subte = [r for r in sub if r['sig_day'] >= split]
        s = summarize(sub, nm)
        if not s:
            print('%-30s %6d  样本不足' % (nm, len(sub)))
            continue
        st_ = summarize(subtr, '') or dict(win=0, avg=0, n=0)
        se_ = summarize(subte, '') or dict(win=0, avg=0, n=0)
        s['label'] = '%s  [训%.0f%%/%d 外%.0f%%/%d]' % (
            nm, st_['win'], st_['n'], se_['win'], se_['n'])
        print(line(s))

    # ---- 止盈档位对照（同一批信号，不同卖出纪律）
    print()
    print('=== 止盈/持有对照（同一批样本） ===')
    print('%-24s %6s %7s %8s %6s %6s' % ('卖出纪律', '笔数', '胜率', '均收益', '盈亏比', '均持有'))
    for tp, hold, lb in ((0.02, 3, 'TP2% / 持3日'), (0.02, 5, 'TP2% / 持5日'),
                         (0.03, 3, 'TP3% / 持3日'), (0.03, 5, 'TP3% / 持5日'),
                         (0.03, 10, 'TP3% / 持10日'), (0.05, 10, 'TP5% / 持10日'),
                         (0.08, 10, 'TP8% / 持10日')):
        rs = []
        for smp in _CACHE:
            ret, ex, dys = sim(smp, tp=tp, hold=hold)
            rs.append(dict(smp, ret=ret, exit=ex, days=dys))
        s = summarize(rs, lb)
        if s:
            print('%-24s %6d %6.1f%% %+8.3f%% %6.2f %6.1f'
                  % (lb, s['n'], s['win'], s['avg'], s['pf'], s['days']))
    return 0


_CACHE = []


if __name__ == '__main__':
    sys.exit(main())

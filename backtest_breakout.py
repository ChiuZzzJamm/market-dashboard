#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest_breakout.py —— 把「横盘启动」塞进 backtest_indicators 的同一套跑道

## 为什么要单独写一个，而不是改 backtest_indicators.py

`backtest_indicators.replay()` 每根 K 线都要跑谐波/吸筹/九门三个重指标（ZigZag、
打分、九门九道门），2 年 × 1260 只跑一次要几十分钟。而「横盘启动」是纯算术，几百毫秒
就能扫完全库。**共用跑道、只换信号源**，才能保证两边数字可比：

- 同一 universe（`.bt_cache` 640 根 / 1260 只主板+创业板）
- 同一日历、同一基准（`build_bench` 的全市场等权，日频再平衡）
- 同一成交口径（T 日收盘出信号 → T+1 开盘买 → T+1+N 开盘卖）
- 同一成本 0.16%/笔、同一 `UNFILLABLE_GAP=0.095` 一字板判不可成交
- 同一 `build_trades / stats_block / equity / perf` 函数，**一行没改**

## 三个变体（因为「横盘启动」不是单一定义）

| key | 口径 | 对应什么 |
|---|---|---|
| `edge` | 平台成立 + 收盘贴上沿（距上沿 ≤3%，**未突破**） | 左侧试仓，池子每天都可能出票 |
| `brk` | 平台成立 + 当日突破（收 > 上沿×1.005 且量 ≥1.8×20日均量 且涨幅 ≥5%） | 右侧确认，最像「启动」 |
| `brk_loose` | 平台松一档（粘合 ≤8% / 振幅 ≤35% / 量 ≥1.5× / 涨幅 ≥3%） | 敏感性检验：结论是不是靠收紧参数撑出来的 |

## 口径里的每一个数都不是拍的

平台（W=60 根，信号日之前，不含信号日）：
- 振幅 `(max(high)-min(low))/min(low) ≤ 0.30`
- 收盘全部落在 `[low×1.015, high×0.985]`（真横盘，不是 V 型/箱体宽幅震荡）
- 均线粘合 `max(MA5/10/20/30/60)/min(...) - 1 ≤ 0.06`
- 地量：近 10 日均额 / 前 50 日均额 ≤ 0.75（用 `volume×close` 统一口径，
  不用任何数据源自带的 amount —— 那个一真一估，比值会失真）

来源见 `N字形态研究报告-20261026.md` 第 8 章，同一套定义。

## 用法

    python3 backtest_breakout.py              # 全量，只打屏不写盘
    python3 backtest_breakout.py --limit 60   # 冒烟
    python3 backtest_breakout.py --dump       # 顺带写 backtest_breakout.json

**纯本地计算，不写 data.js、不进任何自动化。**
"""
import argparse
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import backtest_indicators as BI      # noqa: E402

BAR_KEY = 'breakout'

# 平台口径的四个数（调它就等于换定义，改前先看 _platform 的两次翻车记录）
AMP_MAX = 0.30        # 平台期振幅上限（high 顶到 low 底）
SQUEEZE_MAX = 0.06    # 均线粘合上限（MA5/10/20/30/60 的极差比）
DRY_MAX = 0.75        # 地量：近10日均额 / 前50日均额
BAND_UP = 1.03        # 收盘允许越上沿的幅度（60 根里约 3 根可试盘）
BAND_LO = 1.015       # 收盘距下沿的最小距离
# 「贴沿」窗口（左侧试仓）：收盘 / 箱顶 ∈ [EDGE_LO, EDGE_HI]
# 即「距箱顶 0~3.09%」到「最多越过箱顶 0.5%」。这两个数是 breakout_pool 的
# 入池判据，**任何一处改动都必须两边同时改**，否则池子选出的票不再是回测里
# 那个 +0.148%/笔 超额的样本。
EDGE_LO = 0.97
EDGE_HI = 1.005


# ---------------------------------------------------------------- 平台判定
def _ma(vals, n):
    if len(vals) < n or n <= 0:
        return None
    return sum(vals[-n:]) / n


def _platform(bars, k, W=60):
    """信号日 k 之前 W 根是否构成「横盘平台」。返回 (ok, 上沿, 下沿) 或 (False, None, None)。

    上沿的定义改过两次，两次都是被实测打回来的，记在这里免得后人重犯：

    ① 用 `max(high)` 当上沿 → 平台上任一根的上影线永久抬高箱顶，而收盘带又被压在
       `上沿×0.985` 以下，两条条件互相打架：198 只 × 2 年平台成立 1529 次、
       「收盘越过上沿」只有 5 次、突破信号 **0 笔**。箱顶是筹码密集区，不是影子顶。
    ② 改用 `max(close)` → 更糟，恒等于自我否决：取到最大收盘那一天必然 `> 上沿×0.985`，
       平台成立数直接变 **0**。

    最终取 **收盘价的 95 分位**当上沿（60 根里允许 3 根试盘越界到 +3%），
    箱体条件只保留「不破下沿」，振幅另用 high/low 判。
    """
    if k - W < 20:
        return False, None, None
    seg = bars[k - W:k]
    hi_shadow = max(x['high'] for x in seg)
    lo = min(x['low'] for x in seg)            # 下沿 = 最低价（支撑）
    if lo <= 0:
        return False, None, None
    if (hi_shadow - lo) / lo > AMP_MAX:        # 振幅太宽 = 不是平台
        return False, None, None
    closes = sorted(x['close'] for x in seg)
    hi = closes[int(len(closes) * 0.95) - 1]   # 上沿 = 收盘 95 分位（箱顶）
    for x in seg:                               # 收盘都在带内（下沿 + 允许 3 根试盘越界）
        if not (lo * BAND_LO <= x['close'] <= hi * BAND_UP):
            return False, None, None
    closes = [x['close'] for x in bars[:k]]
    mas = [_ma(closes, n) for n in (5, 10, 20, 30, 60)]
    if any(m is None or m <= 0 for m in mas):
        return False, None, None
    if max(mas) / min(mas) - 1 > SQUEEZE_MAX:       # 均线粘合
        return False, None, None
    amt = [bars[i]['volume'] * bars[i]['close'] for i in range(k - 60, k)]
    near, base = amt[-10:], amt[:50]
    if sum(base) <= 0:
        return False, None, None
    if (sum(near) / 10.0) / (sum(base) / 50.0) > DRY_MAX:   # 地量
        return False, None, None
    return True, hi, lo


def _pct(bars, k):
    if k < 1 or bars[k - 1]['close'] <= 0:
        return 0.0
    return (bars[k]['close'] / bars[k - 1]['close'] - 1) * 100


def _tradeable(bars, k):
    b = bars[k]
    return (b['close'] >= BI.MIN_PRICE
            and b['close'] * b['volume'] >= BI.MIN_AMOUNT
            and BI.PCT_LO <= _pct(bars, k) <= BI.PCT_HI)


def replay_breakout(code, bars, start=0):
    """返回 [(bar_idx, ind, sub, strength)]；sub 用来做分层统计。"""
    out = []
    n = len(bars)
    prev_edge = False
    for k in range(max(BI.WARMUP, start), n):
        ok, hi, lo = _platform(bars, k)
        b = bars[k]
        # ---- 右侧突破
        if ok:
            vol_ma = _ma([bars[i]['volume'] for i in range(k - 20, k)], 20)
            p = _pct(bars, k)
            if (vol_ma and b['close'] > hi * 1.005 and b['volume'] >= vol_ma * 1.8
                    and p >= 5.0 and _tradeable(bars, k)):
                out.append((k, BAR_KEY, '突破日', p))
                prev_edge = False
                continue
            if (vol_ma and b['close'] > hi * 1.005
                    and b['volume'] >= vol_ma * 1.5
                    and p >= 3.0 and _tradeable(bars, k)):
                out.append((k, BAR_KEY, '突破日松', p))
                prev_edge = False
                continue
            # ---- 左侧贴沿（只记进入状态的第一天，避免同一段平台天天报）
            edge = (hi * EDGE_LO <= b['close'] <= hi * EDGE_HI) and _tradeable(bars, k)
            if edge and not prev_edge:
                out.append((k, BAR_KEY, '贴沿', (b['close'] / hi - 1) * 100))
            prev_edge = edge
        else:
            prev_edge = False
    return out


# ---------------------------------------------------------------- 同窗口对照
def equity_cal(trades, N, calendar):
    """BI.equity 的日历版：**空仓日也算一天**。

    原实现取「有持仓贡献的日期并集」当序列，密集指标（谐波 7139 笔）天然覆盖 537 天，
    但稀疏指标（横盘启动 62 笔）序列只有 96 天 —— perf() 按 242/96 年化，把 +87%
    放大成 +384%。同一个函数、两种口径，正是「数字对不上」的典型。这里补全日历：
    无持仓批的日子净值不动（flat），但计入天数。
    """
    pool = [t for t in trades if t['N'] == N and t['r'] is not None]
    if not pool:
        return []
    cohorts = {}
    for t in pool:
        cohorts.setdefault(t['sig_day'], []).append(t)
    coh_daily, span = {}, {}
    for sd, lst in cohorts.items():
        acc = {}
        for t in lst:
            for d, r in t['contrib']:
                acc[d] = acc.get(d, 0.0) + r
        coh_daily[sd] = {d: acc[d] / len(lst) for d in acc}
        span[sd] = (min(t['in_day'] for t in lst), max(t['out_day'] for t in lst))
    d0 = min(v[0] for v in span.values())
    d1 = max(v[1] for v in span.values())
    out, lv = [], 1.0
    for d in calendar:
        if d < d0 or d > d1:
            continue
        act = [sd for sd in coh_daily if span[sd][0] <= d <= span[sd][1]]
        if act:
            r = sum(coh_daily[sd].get(d, 0.0) for sd in act) / len(act)
            lv *= (1 + r)
        out.append((d, lv, len(act)))
    return out


def perf_window(eq, d0, d1):
    """把净值曲线裁到公共窗口再算年化，避免「谁先出信号谁窗口长」的伪比较。"""
    sub = [(d, lv, nb) for (d, lv, nb) in eq if d0 <= d <= d1]
    if len(sub) < 30:
        return None
    base = sub[0][1]
    return BI.perf([(d, lv / base, nb) for (d, lv, nb) in sub])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--dump', action='store_true')
    args, _ = ap.parse_known_args()

    codes = BI._codes()
    if args.limit:
        codes = codes[:args.limit]
    t0 = time.time()
    all_bars = BI.load_bars(codes)
    print('[数据] 可用标的 %d 只' % len(all_bars))
    bench = BI.build_bench(all_bars)
    days = bench['days']
    print('[基准] 交易日 %d（%s ~ %s）' % (len(days), days[0], days[-1]))

    trades = []
    n_sig = 0
    for i, (code, bars) in enumerate(sorted(all_bars.items()), 1):
        sigs = replay_breakout(code, bars)
        n_sig += len(sigs)
        trades.extend(BI.build_trades(code, bars, sigs, bench))
        if args.limit and (i % 20 == 0 or i == len(all_bars)):
            print('  [回放] %d/%d 信号 %d %.0fs' % (i, len(all_bars), n_sig, time.time() - t0))
    print('[回放] 信号 %d 条（去重前），交易记录 %d 条，用时 %.0fs'
          % (n_sig, len(trades), time.time() - t0))

    # ---- 本次新指标（净值按完整交易日历重采样，见 equity_cal 的说明）
    cal = [d for d in days if d in bench['lvl_close']]
    res = {}
    for N in BI.HORIZONS:
        eq = equity_cal(trades, N, cal)
        p = BI.perf(eq)
        s = BI.stats_block(trades, N)
        if p:
            res[N] = (p, s, eq)

    # ---- 历史三指标（直接读已有 json，同一跑道同一 universe）
    old = json.load(open(os.path.join(BASE, 'backtest_result.json'), encoding='utf-8'))
    old_perf = old['perf']
    bench_ann = old['bench']['ann']
    d0, d1 = old['meta']['d0'], old['meta']['d1']

    L = []
    A = L.append
    A('# 「横盘启动」接入同一跑道后的年化对照')
    A('')
    A('跑道：`backtest_indicators.py` 原样 —— universe %d 只（60/00 主板+创业板，'
      '`.bt_cache` 640 根）、T 日收盘出信号 → T+1 开盘买 → T+1+N 开盘卖、'
      '成本 0.16%%/笔、一字板判不可成交。' % old['meta']['nstocks'])
    A('')
    A('历史三指标窗口 %s ~ %s（%d 个交易日），基准（全市场等权）年化 **%.2f%%**。'
      % (d0, d1, old['meta']['ndays'], bench_ann * 100))
    A('')
    A('| 指标 | 持有 | 年化 | 总收益 | 最大回撤 | 夏普 | 平均持仓批 | 空仓天 |')
    A('|---|---|---|---|---|---|---|---|')
    rows = []
    for k in ('harmonic', 'accum', 'power', 'duanban'):
        for N in BI.HORIZONS:
            v = old_perf.get('%s|%d' % (k, N))
            if not v:
                continue
            rows.append((v['ann'], '%-6s T+%-2d' % (BI.IND_ZH[k], N), v, None))
    for N in BI.HORIZONS:
        if N in res:
            p, s, eq = res[N]
            rows.append((p['ann'], '横盘启动 T+%-2d' % N, p, s))
    rows.sort(reverse=True)
    for ann, name, v, s in rows:
        A('| %s | T+%s | **%+.2f%%** | %+.2f%% | %.2f%% | %.2f | %.1f | %d |'
          % (name.split(' T+')[0], name.split(' T+')[1].strip(), v['ann'] * 100,
             v['total'] * 100, v['mdd'] * 100, v['sharpe'],
             v['avg_batch'], v['flat']))
    A('')
    A('## 分笔统计（同一公共窗口，按「信号日」聚类后算 t）')
    A('')
    A('| 指标 | 持有 | 笔数 | 独立信号日 | 胜率 | 均超额/笔 | t |')
    A('|---|---|---|---|---|---|---|')
    for k in ('harmonic', 'accum', 'power'):
        for N in BI.HORIZONS:
            s = old['stats'].get('%s|%d' % (k, N))
            if not s:
                continue
            A('| %s | T+%d | %d | %d | %.1f%% | %+.3f%% | %.2f |'
              % (BI.IND_ZH[k], N, s['n'], s['days'], s['win_abs'] * 100,
                 s['avg_ex'] * 100, s['t']))
    for N in BI.HORIZONS:
        if N not in res:
            continue
        p, s, eq = res[N]
        A('| 横盘启动 | T+%d | %d | %d | %.1f%% | %+.3f%% | %.2f |'
          % (N, s['n'], s['days'], s['win_abs'] * 100, s['avg_ex'] * 100, s['t']))
    A('')
    A('## 按信号类型拆（横盘启动内部）')
    A('')
    A('| 子类 | 持有 | 笔数 | 独立信号日 | 胜率 | 均超额/笔 | t |')
    A('|---|---|---|---|---|---|---|')
    for sub in ('突破日', '突破日松', '贴沿'):
        pool = [t for t in trades if t['sub'] == sub]
        if not pool:
            continue
        for N in BI.HORIZONS:
            s = BI.stats_block(pool, N)
            if s and s['n'] >= 20:
                A('| %s | T+%d | %d | %d | %.1f%% | %+.3f%% | %.2f |'
                  % (sub, N, s['n'], s['days'], s['win_abs'] * 100,
                     s['avg_ex'] * 100, s['t']))
    A('')
    md = '\n'.join(L)
    print('\n' + md)

    if args.dump:
        dump = {'meta': {'stamp': time.strftime('%Y-%m-%d %H:%M'),
                         'nstocks': len(all_bars), 'd0': d0, 'd1': d1},
                'perf': {'%s|%d' % (BAR_KEY, N): v[0] for N, v in res.items()},
                'stats': {'%s|%d' % (BAR_KEY, N): v[1] for N, v in res.items()}}
        with open(os.path.join(BASE, 'backtest_breakout.json'), 'w', encoding='utf-8') as f:
            json.dump(dump, f, ensure_ascii=False, indent=1)
        with open(os.path.join(BASE, 'backtest_breakout.md'), 'w', encoding='utf-8') as f:
            f.write(md)
        print('[写盘] backtest_breakout.json / backtest_breakout.md')


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""断板反包 · 买点口径回测（用户 2026-10-26 指定口径）

时序定义（严格按用户描述）：
  T0  涨停                        = 第 1 天
  T1  倍量阴                      = 第 2 天（volume / T0量 在 1.1~3.5 倍，且未续板）
  T2  或 T3  缩量回调到均线上      = 第 3 / 第 4 天（量 < T1量，收盘落在 MA5 上方 0~3%）
  买入：T2 或 T3 的尾盘（当日收盘价）；T2 优先，T2 不满足条件才顺延到 T3
  卖出：次日（T3 或 T4）—— 分别测 最高价 / 开盘价 / 收盘价

数据源：.kline_cache/*.json（日K，1268 只主板票，2025-06-30 起）
成本：双边 0.16%

用法：python3 backtest_v2_entry.py
"""

import json
import os
import glob
import statistics
from collections import defaultdict

COST = 0.0016

# ---- 判据参数（改这里，别改下面的逻辑）----
MA_N = 5                 # 「回调到均线上」用 MA5
MA_UP_MAX = 0.03         # 收盘在 MA5 上方 0~3% 视为「站在均线上」
VOL_UP_MIN = 1.1         # T1 倍量下限（相对 T0）
VOL_UP_MAX = 3.5         # T1 倍量上限
ZT_PCT = 1.095           # 涨停判定：收盘 ≥ 前收 × 1.095（主板 10%）
MIN_BARS = 140           # 样本最少 K 线数
CACHE_DIR = '.kline_cache'


def load_cache():
    cache = {}
    for f in glob.glob(os.path.join(CACHE_DIR, '*.json')):
        code = os.path.basename(f)[:-5]
        if not code.startswith(('60', '00')):
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if len(d) < MIN_BARS:
            continue
        cache[code] = d
    return cache


def is_zt(d, i):
    if i < 1:
        return False
    pc = d[i - 1]['close']
    return pc > 0 and d[i]['close'] >= pc * ZT_PCT


def ma(d, i, n):
    if i < n - 1:
        return None
    return sum(x['close'] for x in d[i - n + 1:i + 1]) / n


def build_signals(cache, need_yin=False):
    """T0 涨停 + T1 倍量阴 → T2/T3 缩量回调到 MA5 → 尾盘买。返回信号列表。"""
    out = []
    for code, d in cache.items():
        n = len(d)
        for T0 in range(60, n - 3):
            if not is_zt(d, T0):
                continue
            T1 = T0 + 1
            if T1 >= n or is_zt(d, T1):
                continue
            v0, v1 = d[T0]['volume'], d[T1]['volume']
            if v0 <= 0:
                continue
            ratio = v1 / v0
            if not (VOL_UP_MIN <= ratio <= VOL_UP_MAX):
                continue
            if need_yin and not (d[T1]['close'] < d[T1]['open']):
                continue

            for i in (T0 + 2, T0 + 3):          # T2 优先
                if i >= len(d) - 1:             # 需要次日数据
                    break
                if is_zt(d, i):                 # 当天又涨停 → 买不到
                    continue
                if not (d[i]['volume'] < v1):   # 缩量（相对倍量阴）
                    continue
                m = ma(d, i, MA_N)
                if m is None:
                    continue
                c = d[i]['close']
                if not (m <= c <= m * (1 + MA_UP_MAX)):
                    continue
                nx = d[i + 1]
                out.append(dict(
                    code=code, buy_date=d[i]['day'], buy_idx=i, hold_day=i - T0,
                    buy=c, ma5=m,
                    hi=nx['high'], op=nx['open'], cl=nx['close'], lo=nx['low'],
                    ratio=ratio, shrink=d[i]['volume'] / v1,
                    gap_open=(d[i]['open'] - d[i - 1]['close']) / d[i - 1]['close'] * 100,
                ))
                break
    return out


def stat(vals, label, width=46):
    vals = [v for v in vals if v is not None]
    if len(vals) < 30:
        print('%-*s n=%5d  样本不足' % (width, label, len(vals)))
        return None
    win = sum(1 for v in vals if v > 0) / len(vals) * 100
    avg = sum(vals) / len(vals) * 100
    srt = sorted(vals)
    pf = sum(v for v in vals if v > 0) / max(1e-9, -sum(v for v in vals if v < 0))
    print('%-*s n=%5d  胜率%5.1f%%  均值%+6.2f%%  中位%+6.2f%%  盈亏比%5.2f'
          % (width, label, len(vals), win, avg, srt[len(srt) // 2] * 100, pf))
    return dict(n=len(vals), win=win, avg=avg, median=srt[len(srt) // 2] * 100, pf=pf)


def main():
    cache = load_cache()
    print('样本股票 %d 只（%s 起日K）' % (len(cache), '2025-06-30'))
    sig = build_signals(cache)
    print('信号 %d 个 | 信号日 %d 天' % (len(sig), len({s['buy_date'] for s in sig})))
    if not sig:
        return

    R = {}
    print('\n' + '=' * 100)
    print('【一】同一批买点，三种卖点（买入价＝回调日尾盘收盘）')
    print('=' * 100)
    R['sell_high'] = stat([s['hi'] / s['buy'] - 1 - COST for s in sig], '卖＝次日最高价〔上限，不可执行〕')
    R['sell_open'] = stat([s['op'] / s['buy'] - 1 - COST for s in sig], '卖＝次日开盘价')
    R['sell_close'] = stat([s['cl'] / s['buy'] - 1 - COST for s in sig], '卖＝次日收盘价')

    print('\n' + '=' * 100)
    print('【二】次日形态解剖：为什么最高价 81%、收盘只有 44%')
    print('=' * 100)
    give = [(s['hi'] / s['buy'] - s['cl'] / s['buy']) * 100 for s in sig]
    op_pct = [(s['op'] / s['buy'] - 1) * 100 for s in sig]
    print('  次日开盘相对买入价  均值%+.2f%%  中位%+.2f%%  为正比例%.1f%%'
          % (statistics.mean(op_pct), statistics.median(op_pct),
             sum(1 for x in op_pct if x > 0) / len(op_pct) * 100))
    print('  次日最高相对买入价  均值%+.2f%%'
          % statistics.mean([(s['hi'] / s['buy'] - 1) * 100 for s in sig]))
    print('  次日收盘相对买入价  均值%+.2f%%'
          % statistics.mean([(s['cl'] / s['buy'] - 1) * 100 for s in sig]))
    print('  ★ 最高点→收盘 平均回吐 %.2f%%  中位 %.2f%%  回吐>2%% 的比例 %.1f%%'
          % (statistics.mean(give), statistics.median(give),
             sum(1 for x in give if x > 2) / len(give) * 100))
    pos = [(s['cl'] - s['lo']) / max(1e-9, s['hi'] - s['lo']) for s in sig]
    print('  收盘在当日区间的位置 均值%.2f（0=最低 1=最高）；收盘≈最高(>0.9)仅 %.1f%%'
          % (statistics.mean(pos), sum(1 for x in pos if x > 0.9) / len(pos) * 100))

    print('\n' + '=' * 100)
    print('【三】次日盘中触及各档涨幅的比例（＝限价单成交率）')
    print('=' * 100)
    for tp in (0.01, 0.015, 0.02, 0.025, 0.03, 0.035, 0.05, 0.07):
        print('  次日最高 ≥ +%.1f%% 的比例 %5.1f%%'
              % (tp * 100, sum(1 for s in sig if s['hi'] >= s['buy'] * (1 + tp)) / len(sig) * 100))

    print('\n' + '=' * 100)
    print('【四】可执行卖法：卖价 = 开盘 + k×(最高-开盘)，k 决定手速')
    print('=' * 100)
    print('  %-10s %8s %9s %9s' % ('k', '胜率', '均值', '盈亏比'))
    for k in (0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
        vals = [((s['op'] + k * (s['hi'] - s['op'])) / s['buy'] - 1 - COST) for s in sig]
        win = sum(1 for v in vals if v > 0) / len(vals) * 100
        avg = sum(vals) / len(vals) * 100
        pf = sum(v for v in vals if v > 0) / max(1e-9, -sum(v for v in vals if v < 0))
        print('  k=%-8.1f %7.1f%% %+8.2f%% %9.2f' % (k, win, avg, pf))
        if k == 0.4:
            R['k04'] = dict(k=k, n=len(vals), win=win, avg=avg, pf=pf)

    print('\n' + '=' * 100)
    print('【五】限价单（唯一不需要手速、但需要挂单的卖法）')
    print('=' * 100)
    for tp in (0.015, 0.02, 0.025, 0.03, 0.035, 0.05):
        vals = []
        for s in sig:
            if s['hi'] >= s['buy'] * (1 + tp):
                vals.append(tp - COST)
            else:
                vals.append(s['cl'] / s['buy'] - 1 - COST)
        stat(vals, '挂+%.1f%% 未中则收盘卖' % (tp * 100))

    print('\n' + '=' * 100)
    print('【六】T2 买 vs T3 买（卖＝k0.4 与 收盘）')
    print('=' * 100)
    for hd, nm in ((2, '第三日买(T2)'), (3, '第四日买(T3)')):
        sub = [s for s in sig if s['hold_day'] == hd]
        stat([((s['op'] + 0.4 * (s['hi'] - s['op'])) / s['buy'] - 1 - COST) for s in sub], nm + ' · k0.4 卖')
        stat([s['cl'] / s['buy'] - 1 - COST for s in sub], nm + ' · 收盘卖')

    print('\n' + '=' * 100)
    print('【七】稳健性：k=0.4 卖出')
    print('=' * 100)
    by = defaultdict(list)
    for s in sig:
        by[s['buy_date'][:4]].append((s['op'] + 0.4 * (s['hi'] - s['op'])) / s['buy'] - 1 - COST)
    for y in sorted(by):
        stat(by[y], '  %s 年' % y)
    byq = defaultdict(list)
    for s in sig:
        byq[s['buy_date'][:7]].append((s['op'] + 0.4 * (s['hi'] - s['op'])) / s['buy'] - 1 - COST)
    neg = [q for q in sorted(byq) if sum(byq[q]) / len(byq[q]) < 0]
    print('  季度 %d 个，均值为负 %d 个 → %s' % (len(byq), len(neg), neg if neg else '无'))
    days = defaultdict(int)
    for s in sig:
        days[s['buy_date']] += 1
    print('  可买天数 %d，平均 %.2f 只/日，单日最多 %d 只'
          % (len(days), len(sig) / len(days), max(days.values())))

    print('\n' + '=' * 100)
    print('【八】缩量幅度 / 倍量比分档（收盘卖，看形态能否再分层）')
    print('=' * 100)
    for lo, hi, nm in ((0, 0.4, '缩到<40%'), (0.4, 0.6, '40~60%'),
                       (0.6, 0.8, '60~80%'), (0.8, 1.01, '80~100%')):
        sub = [s for s in sig if lo <= s['shrink'] < hi]
        stat([s['cl'] / s['buy'] - 1 - COST for s in sub], '  ' + nm)
    for lo, hi, nm in ((1.1, 1.5, '1.1~1.5x'), (1.5, 2, '1.5~2x'),
                       (2, 2.5, '2~2.5x'), (2.5, 3.6, '2.5~3.5x')):
        sub = [s for s in sig if lo <= s['ratio'] < hi]
        stat([s['cl'] / s['buy'] - 1 - COST for s in sub], '  倍量 ' + nm)

    out = dict(
        generated_at='backtest_v2_entry',
        universe=len(cache), signals=len(sig),
        signal_days=len({s['buy_date'] for s in sig}),
        first_day=min(s['buy_date'] for s in sig),
        last_day=max(s['buy_date'] for s in sig),
        params=dict(MA_N=MA_N, MA_UP_MAX=MA_UP_MAX,
                    VOL_UP_MIN=VOL_UP_MIN, VOL_UP_MAX=VOL_UP_MAX, ZT_PCT=ZT_PCT),
        result={k: v for k, v in R.items() if v},
    )
    with open('backtest_entry_result.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('\n结果已写入 backtest_entry_result.json')


if __name__ == '__main__':
    main()

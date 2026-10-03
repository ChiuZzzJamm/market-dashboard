#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""signal_stats.py —— 技术信号「历史有效性」回测（R100z23）

为什么需要它：全站四大技术模块（谐波 / 吸筹 / 九门 / 断板）都只回答「今天
哪只票符合形态」，**没有任何模块回答过「这套形态过去出信号时，之后到底涨
不涨」**。一个没被统计过的信号，本质上是自说自话——专业投资人的第一问就是
「你这个信号的历史胜率多少」。本脚本补的就是这一问。

做法（逐日重放，不做未来函数）：
  1. 扫描域 = data.js 里 harmonic.confirm/watch + accumulation.scored +
     powerScreen.passed + duanban 确认池 的并集（够窄，保证样本相关；也够
     广，保证能攒出样本）。
  2. 对每只票取其最近 ~250 根日 K，对过去 LOOKBACK 个交易日逐日重跑一次
     检测器（谐波走 harmonic_detect.scan_bars、吸筹走 accumulation_score.
     score_one），**只看信号是否恰好落在当天**（di == t / sigIdx == t）。
     信号必须"当天才成立"，避免把陈旧形态算成新信号。
  3. 记下信号日的收盘价，统计 T+1 / T+3 / T+5 的收益均值与胜率。
  4. 样本数 < MIN_SAMPLE 时不给结论（写 '样本积累中'），**绝不拿三五条
     样本算胜率糊弄**——与 track_calibration.py 的最小样本门槛同一纪律。

写回 data.js 顶层 D.signalStats（前端四个技术页角落展示）：
  {harmonic:{n,avgPct1,winRate1,...}, accumulation:{...}, lookback, updatedAt}

用法：
  python3 signal_stats.py              # 回测并写回
  python3 signal_stats.py --dry        # 只打印不写回
  python3 signal_stats.py --days 90    # 换回看长度
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
import kline_cache as K
import harmonic_detect as H
import accumulation_score as A

BASE = os.path.dirname(os.path.abspath(__file__))
LOOKBACK = 120          # 回看交易日数（信号必须落在这段窗口里）
MIN_SAMPLE = 12         # 单模块最小样本数，低于此值不给结论
HORIZONS = (1, 3, 5)    # 统计 T+1 / T+3 / T+5


def pool_codes(D):
    """回测扫描域：四个技术池 + 断板确认池的并集。"""
    codes = []
    hz = D.get('harmonic') or {}
    for k in ('confirm', 'watch'):
        for it in (hz.get(k) or []):
            c = str(it.get('code') or '')[:6]
            if c.isdigit() and c not in codes:
                codes.append(c)
    for it in ((D.get('accumulation') or {}).get('scored') or []):
        c = str(it.get('code') or '')[:6]
        if c.isdigit() and c not in codes:
            codes.append(c)
    for it in ((D.get('powerScreen') or {}).get('passed') or []):
        c = str(it.get('code') or '')[:6]
        if c.isdigit() and c not in codes:
            codes.append(c)
    db = D.get('duanban') or {}
    for k in ('confirmed', 'watching'):
        for it in (db.get(k) or []):
            if isinstance(it, dict):
                c = str(it.get('code') or '')[:6]
            else:
                c = str(it)[:6]
            if c.isdigit() and c not in codes:
                codes.append(c)
    return codes


def fwd_returns(closes, sig_i, horizons=HORIZONS):
    """信号日 sig_i 之后的各 horizon 收益（相对信号日收盘，百分比）。"""
    out = {}
    base = closes[sig_i]
    if not base:
        return out
    for h in horizons:
        j = sig_i + h
        if j < len(closes) and closes[j]:
            out[h] = (closes[j] / base - 1) * 100
    return out


def find_break(bars, t, max_ahead=5):
    """信号日 t 之后 max_ahead 个交易日内首次出现「放量突破」的日子。

    入场规则：当日涨幅 ≥3% 且 成交量 ≥1.5×前 5 日均量 —— 即「不接飞刀，等
    资金真的愿意用真金白银往上打再跟」。返回该日 bar 下标，找不到返回 None。
    """
    for j in range(t + 1, min(t + 1 + max_ahead, len(bars))):
        prev5 = [bars[k]['volume'] for k in range(max(0, j - 5), j)]
        vma = (sum(prev5) / len(prev5)) if prev5 else 0
        chg = bars[j]['close'] / bars[j - 1]['close'] - 1
        if chg >= 0.03 and vma > 0 and bars[j]['volume'] >= 1.5 * vma:
            return j
    return None


def stat_of(samples):
    """samples: {horizon: [pct, ...]} → 均值/胜率/样本数。"""
    res = {'n': 0, 'avgPct': None, 'winRate': None, 'concl': '样本积累中'}
    rows = samples.get(1) or []
    if not rows:
        return res
    n = len(rows)
    avg = sum(rows) / n
    win = sum(1 for x in rows if x > 0) / n
    res = {'n': n, 'avgPct': round(avg, 2), 'winRate': round(win, 3),
           'concl': '有效' if (n >= MIN_SAMPLE and avg > 0) else
                    ('无效（历史负收益）' if n >= MIN_SAMPLE else '样本积累中')}
    for h in HORIZONS:
        rr = samples.get(h) or []
        if rr:
            res['avgPct%d' % h] = round(sum(rr) / len(rr), 2)
            res['winRate%d' % h] = round(sum(1 for x in rr if x > 0) / len(rr), 3)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true', help='只打印不写回')
    ap.add_argument('--days', type=int, default=LOOKBACK, help='回看交易日数')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    days = args.days

    D = common.load_dashboard_data(BASE)
    codes = pool_codes(D)
    print('[SIG] 回测扫描域 %d 只，回看 %d 个交易日' % (len(codes), days))

    hz = {1: [], 3: [], 5: []}
    ac = {1: [], 3: [], 5: []}
    # 基准：扫描域内**所有交易日**（不设信号条件）的前瞻收益。没有它，
    # -1.5% 的绝对数字毫无意义——同期大盘若跌 3%，-1.5% 反而是跑赢。
    # 与 track_calibration.py 的超额口径同一原则（R100z15）。
    base = {1: [], 3: [], 5: []}
    # 「确认后再买」样本：信号出完之后，等出现一次放量突破（当日涨幅 ≥3% 且
    # 量 ≥1.5×前 5 日均量）才作为入场，再统计入场后收益——验证「不接飞刀、
    # 等结构确认」能不能把上面的负超额救回来。
    conf = {1: [], 3: [], 5: []}

    for code in codes:
        bars = K.get_bars(code, D)
        if not bars or len(bars) < 70:
            continue
        closes = [b['close'] for b in bars]
        n = len(bars)
        # 只回测有足够未来数据的信号日
        for t in range(max(60, n - days), n - max(HORIZONS)):
            # 基准样本：每个交易日都记一次（不分是否出信号）
            rb = fwd_returns(closes, t)
            for h in HORIZONS:
                if h in rb:
                    base[h].append(rb[h])
            seg = bars[:t + 1]
            # scan_bars 只回吐「当下最优」一个候选，D 点落日用 pointDays.D 判定
            # 是否就是当天——只有信号恰好当天成立才统计，避免把陈旧图形算成新信号。
            try:
                cand = H.scan_bars(seg)
            except Exception:
                cand = None
            hit_hz = bool(isinstance(cand, dict) and
                          (cand.get('pointDays') or {}).get('D') == bars[t].get('day'))
            if hit_hz:
                r = fwd_returns(closes, t)
                for h in HORIZONS:
                    if h in r:
                        hz[h].append(r[h])
                e = find_break(bars, t)
                if e is not None:
                    r2 = fwd_returns(closes, e)
                    for h in HORIZONS:
                        if h in r2:
                            conf[h].append(r2[h])
            try:
                r1 = A.score_one(seg, '')
            except Exception:
                r1 = None
            # 吸筹是「结构成型于最近几日」的区间模型，不是单点信号：F3 横盘会把
            # sigIdx 钉在 n-2，所以给 2 个交易日容差（t-2 ~ t），基准仍取 t 日收盘。
            sig = r1.get('sigIdx') if r1 else -1
            if r1 and r1.get('score', 0) >= A.MIN_SCORE and sig >= 0 and t - 2 <= sig <= t:
                r = fwd_returns(closes, t)
                for h in HORIZONS:
                    if h in r:
                        ac[h].append(r[h])
                e = find_break(bars, t)
                if e is not None:
                    r2 = fwd_returns(closes, e)
                    for h in HORIZONS:
                        if h in r2:
                            conf[h].append(r2[h])

    hz_res, ac_res, cf_res = stat_of(hz), stat_of(ac), stat_of(conf)
    bench = stat_of(base)
    stats = {
        'harmonic': hz_res,
        'accumulation': ac_res,
        'benchmark': bench,
        'confirmed': cf_res,
        'lookback': days,
        'horizons': list(HORIZONS),
        'updatedAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'note': ('逐日重放检测器、只统计「信号恰好落在当天」的案例，'
                 '相对信号日收盘价的 T+1/T+3/T+5 收益均值与胜率；'
                 '样本 < %d 条不给结论。' % MIN_SAMPLE),
    }
    if args.json:
        print(json.dumps(stats, ensure_ascii=False))
    else:
        print('[SIG] 谐波   T+1 %s%% 胜率%s｜T+5 %s%% 胜率%s（%d 样本）→ %s'
              % (hz_res.get('avgPct1'), hz_res.get('winRate1'),
                 hz_res.get('avgPct5'), hz_res.get('winRate5'),
                 hz_res['n'], hz_res['concl']))
        print('[SIG] 吸筹   T+1 %s%% 胜率%s｜T+5 %s%% 胜率%s（%d 样本）→ %s'
              % (ac_res.get('avgPct1'), ac_res.get('winRate1'),
                 ac_res.get('avgPct5'), ac_res.get('winRate5'),
                 ac_res['n'], ac_res['concl']))
        print('[SIG] 基准(随机日) T+1 %s%% 胜率%s｜T+5 %s%% 胜率%s（%d 样本）'
              % (bench.get('avgPct1'), bench.get('winRate1'),
                 bench.get('avgPct5'), bench.get('winRate5'), bench['n']))
        for k, nm in (('harmonic', '谐波'), ('accumulation', '吸筹')):
            r = stats[k]
            if r.get('avgPct5') is None:
                continue
            print('[SIG] %s 超额(T+5) = %+.2f 个百分点（信号 %+.2f%% vs 基准 %+.2f%%）'
                  % (nm, r['avgPct5'] - (bench.get('avgPct5') or 0),
                     r['avgPct5'], bench.get('avgPct5') or 0))
        cf = stats['confirmed']
        print('[SIG] 等放量突破再买（两类信号合计 %d 样本）T+5 %s%% 胜率%s'
              % (cf['n'], cf.get('avgPct5'), cf.get('winRate5')))
    if args.dry:
        return stats
    # 写回 data.js 顶层 signalStats（只加这一个键，不动其它字段）
    s = open(os.path.join(BASE, 'data.js'), encoding='utf-8').read()
    i = s.find('window.DASHBOARD_DATA = ')
    if i < 0:
        print('[SIG] data.js 未找到 DASHBOARD_DATA，跳过写回', file=sys.stderr)
        return stats
    j = s.rfind(';', i)
    D = json.loads(s[i + len('window.DASHBOARD_DATA = '):j])
    D['signalStats'] = stats
    out = 'window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False,
                                                  separators=(',', ':')) + ';\n'
    open(os.path.join(BASE, 'data.js'), 'w', encoding='utf-8').write(out)
    print('[SIG] 写回 data.js.signalStats：谐波 %d 样本 / 吸筹 %d 样本'
          % (hz_res['n'], ac_res['n']))
    return stats


if __name__ == '__main__':
    main()

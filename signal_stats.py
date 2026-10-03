#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""signal_stats.py —— 技术信号「历史有效性」回测（R100z23 → R100z27）

为什么需要它：全站四大技术模块（谐波 / 吸筹 / 九门 / 断板）都只回答「今天
哪只票符合形态」，**没有任何模块回答过「这套形态过去出信号时，之后到底涨
不涨」**。一个没被统计过的信号，本质上是自说自话——专业投资人的第一问就是
「你这个信号的历史胜率多少」。本脚本补的就是这一问。

R100z27 加了四件事（上一版只有谐波+吸筹）：
  1. 断板反包确认池回测：判定四步严格照抄 check_duanban.check_form，
     不用历史涨停池（沙箱封东财、存不下历史），直接拿日 K 反算涨停日。
  2. 九门改「单门统计」：9 道门各自拆 pass / fail 两组各报 T+5 均值与胜率，
     「九门全过」是极稀疏事件、日均不足 12 样本，单门才能给出可用数字。
  3. −3% 止损路径：此前口径是「买了不动拿到 T+5」，等于默认扛跌。现在持有
     期内最低价触及 −3% 即按止损价离场结算——这一格补上，结论才接近实盘。
  4. confirmed 改名 breakout：它其实是「等放量突破再买」的样本，原名会让人
     误以为是断板确认池的回测（断板池自己的回测见第 1 点）。

做法（逐日重放，不做未来函数）：
  1. 扫描域 = data.js 里 harmonic.confirm/watch + accumulation.scored +
     powerScreen.passed + duanban 确认/观察池 的并集。
  2. 对每只票取最近 ~250 根日 K，对过去 LOOKBACK 个交易日逐日重跑一次检测器
     （谐波走 harmonic_detect.scan_bars、吸筹走 accumulation_score.score_one、
     九门走 power_screener.screen_one(idx=t)、断板走本地 duanban_sig），
     **只看信号是否恰好落在当天**。
  3. 记下信号日收盘价，统计 T+1 / T+3 / T+5 的收益均值、胜率与止损率。
  4. 样本数 < MIN_SAMPLE 时不给结论（写 '样本积累中'），**绝不拿三五条样本
     算胜率糊弄**——与 track_calibration.py 的最小样本门槛同一纪律。

写回 data.js 顶层 D.signalStats（前端四个技术页展示）。
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
import power_screener as P

BASE = os.path.dirname(os.path.abspath(__file__))
LOOKBACK = 120          # 回看交易日数（信号必须落在这段窗口里）
MIN_SAMPLE = 12         # 单模块最小样本数，低于此值不给结论
HORIZONS = (1, 3, 5)    # 统计 T+1 / T+3 / T+5
STOP_PCT = 0.97         # −3% 止损：持有期内最低价触及即按止损价离场
GATE_KEYS = ('g1', 'g2', 'g3', 'g4', 'g5', 'g6', 'g7', 'g8', 'g9')
GATE_LABEL = {
    'g1': 'G1 涨幅≥3%', 'g2': 'G2 放量≥1.5×5日均量', 'g3': 'G3 价处20日区间上1/3',
    'g4': 'G4 站上MA20', 'g5': 'G5 非ST主板', 'g6': 'G6 近5日无长上影',
    'g7': 'G7 当日振幅≤15%', 'g8': 'G8 ATR14/收盘 2~8%', 'g9': 'G9 RSI14 40~80',
}


def pool_codes(D):
    """回测扫描域：四个技术池 + 断板确认/观察池的并集。"""
    codes = []
    for it in (((D.get('harmonic') or {}).get('confirm') or [])
               + ((D.get('harmonic') or {}).get('watch') or [])):
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


def fwd_returns(closes, lows, sig_i, horizons=HORIZONS, stop=STOP_PCT):
    """信号日 sig_i 之后的各 horizon 收益（相对信号日收盘，百分比）。

    含 −3% 止损路径：持有期内某日最低价 ≤ 止损线，则按止损价离场结算
    （不再往后算），并置 'stop<h>' 标记——否则「买了不动」等于默认扛跌，
    与实盘纪律不符，结论会系统性偏乐观。
    """
    out = {}
    base = closes[sig_i]
    if not base:
        return out
    lim = base * stop
    for h in horizons:
        j = sig_i + h
        if j >= len(closes):
            continue
        stopt = False
        for k in range(sig_i + 1, j + 1):
            if lows[k] and lows[k] <= lim:
                stopt = True
                break
        out[h] = (stop - 1) * 100 if stopt else (closes[j] / base - 1) * 100
        if stopt:
            out['stop%d' % h] = True
    return out


def duanban_sig(bars, t):
    """断板反包「确认池」判定是否恰好在 t 日成立（t 作为最新一根）。

    阈值与 check_duanban.check_form 完全一致——那边改口径这里必须同步改：
      涨停日 iT（涨幅≥ limit_ratio）→ 次日放量断板（量比 1.1~3.5 且未再涨停）
      → T+2 起缩量（<断板日量）→ 之后不破 T 日最低点（容差 2%）→ 收盘站稳 MA5（×0.97）
    信号日限定 days_ago∈[2,5]，与实盘「确认池才动手」对齐。"""
    if t < 8:
        return False

    def C(i):
        return float(bars[i].get('close') or 0)

    def L(i):
        return float(bars[i].get('low') or 0)

    def V(i):
        return float(bars[i].get('volume') or 0)

    code = str(bars[t].get('code') or '')
    th = 19.8 if code.startswith(('30', '68')) else 9.8
    for back in range(2, 6):          # days_ago ∈ [2,5]
        iT = t - back
        if iT < 0:
            continue
        if C(iT - 1) <= 0 or (C(iT) / C(iT - 1) - 1) * 100 < th:
            continue
        vT = V(iT)
        if vT <= 0:
            continue
        i1 = iT + 1
        if i1 > t:
            continue
        r1 = V(i1) / vT
        if not (1.1 <= r1 <= 3.5):
            continue
        if (C(i1) / C(i1 - 1) - 1) * 100 >= th:
            continue                   # 次日在涨停＝没断板
        i2 = iT + 2
        if i2 > t or not (0 < V(i2) < V(i1)):
            continue
        i3 = iT + 3
        if i3 <= t and not (0 < V(i3) < V(i1)):
            continue
        lowT = L(iT)
        if lowT > 0 and min(C(i) for i in range(i1, t + 1)) < lowT * 0.98:
            continue                   # 已破 T 日低点，形态失效
        ma5 = sum(C(i) for i in range(t - 4, t + 1)) / 5.0
        if C(t) < ma5 * 0.97:
            continue
        return True
    return False


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


def stat_of(samples, stop_flags=None):
    """samples: {horizon: [pct, ...]} → 均值/胜率/样本数/止损率。"""
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
    if stop_flags:
        res['stopRate'] = round(sum(stop_flags) / max(1, len(stop_flags)), 3)
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
    print('[SIG] 回测扫描域 %d 只，回看 %d 个交易日，止损线 %.0f%%'
          % (len(codes), days, (1 - STOP_PCT) * 100))

    hz = {h: [] for h in HORIZONS}
    hz_stop = []
    ac = {h: [] for h in HORIZONS}
    ac_stop = []
    db = {h: [] for h in HORIZONS}
    db_stop = []
    brk = {h: [] for h in HORIZONS}      # 等放量突破再买（原 confirmed → 改名；入场日已变，不另记止损率）
    # 九门单门：每道门拆 pass / fail 两组
    gpass = {g: {h: [] for h in HORIZONS} for g in GATE_KEYS}
    gfail = {g: {h: [] for h in HORIZONS} for g in GATE_KEYS}
    # 基准：扫描域内所有交易日（不设信号条件）
    base = {h: [] for h in HORIZONS}
    base_stop = []

    for code in codes:
        bars = K.get_bars(code, D)
        if not bars or len(bars) < 70:
            continue
        closes = [b['close'] for b in bars]
        lows = [b.get('low') or 0 for b in bars]
        n = len(bars)
        for t in range(max(60, n - days), n - max(HORIZONS)):
            rb = fwd_returns(closes, lows, t)
            for h in HORIZONS:
                if h in rb:
                    base[h].append(rb[h])
                    if rb.get('stop%d' % h):
                        base_stop.append(1)
                    else:
                        base_stop.append(0)
            seg = bars[:t + 1]
            # ---- 谐波 ----
            try:
                cand = H.scan_bars(seg)
            except Exception:
                cand = None
            if isinstance(cand, dict) and (cand.get('pointDays') or {}).get('D') == bars[t].get('day'):
                r = fwd_returns(closes, lows, t)
                for h in HORIZONS:
                    if h in r:
                        hz[h].append(r[h])
                if r.get('stop1'):
                    hz_stop.append(1)
                else:
                    hz_stop.append(0)
                e = find_break(bars, t)
                if e is not None:
                    r2 = fwd_returns(closes, lows, e)
                    for h in HORIZONS:
                        if h in r2:
                            brk[h].append(r2[h])
            # ---- 吸筹（F3 会把 sigIdx 钉在 n-2，给 2 日容差）----
            try:
                r1 = A.score_one(seg, '')
            except Exception:
                r1 = None
            if r1 and r1.get('score', 0) >= A.MIN_SCORE and \
               r1.get('sigIdx', -1) >= 0 and t - 2 <= r1['sigIdx'] <= t:
                r = fwd_returns(closes, lows, t)
                for h in HORIZONS:
                    if h in r:
                        ac[h].append(r[h])
                if r.get('stop1'):
                    ac_stop.append(1)
                else:
                    ac_stop.append(0)
                e = find_break(bars, t)
                if e is not None:
                    r2 = fwd_returns(closes, lows, e)
                    for h in HORIZONS:
                        if h in r2:
                            brk[h].append(r2[h])
            # ---- 断板反包确认池 ----
            if duanban_sig(bars, t):
                r = fwd_returns(closes, lows, t)
                for h in HORIZONS:
                    if h in r:
                        db[h].append(r[h])
                if r.get('stop1'):
                    db_stop.append(1)
                else:
                    db_stop.append(0)
            # ---- 九门单门（每道门拆 pass/fail）----
            try:
                gs = P.screen_one(bars, '', idx=t).get('gates') or {}
            except Exception:
                gs = {}
            if gs:
                for g in GATE_KEYS:
                    target = gpass[g] if gs.get(g) else gfail[g]
                    r = fwd_returns(closes, lows, t)
                    for h in HORIZONS:
                        if h in r:
                            target[h].append(r[h])

    hz_res = stat_of(hz, hz_stop)
    ac_res = stat_of(ac, ac_stop)
    db_res = stat_of(db, db_stop)
    brk_res = stat_of(brk)
    bench = stat_of(base, base_stop)

    gates = {}
    for g in GATE_KEYS:
        ps, fs = stat_of(gpass[g]), stat_of(gfail[g])
        p5, f5 = ps.get('avgPct5'), fs.get('avgPct5')
        gates[g] = {'label': GATE_LABEL[g], 'pass': ps, 'fail': fs,
                    'diff5': (round(p5 - f5, 2) if (p5 is not None and f5 is not None) else None),
                    'diff5concl': ('通过组更高' if (p5 is not None and f5 is not None and p5 > f5)
                                   else ('未通过组更高' if (p5 is not None and f5 is not None) else '样本不足'))}

    stats = {
        'harmonic': hz_res,
        'accumulation': ac_res,
        'duanban': db_res,
        'power': {'n': bench.get('n'), 'gates': gates},
        'benchmark': bench,
        'breakout': brk_res,
        'stopPct': round((1 - STOP_PCT) * 100, 1),
        'lookback': days,
        'horizons': list(HORIZONS),
        'updatedAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'note': ('逐日重放检测器、只统计「信号恰好落在当天」的案例；含 %.0f%% 止损路径'
                 '（持有期内触及即按止损价离场结算，该笔直接记为亏损）；样本 < %d 条不给结论。'
                 '「胜率」＝到 T+5 仍为正的比例（止损离场笔计入亏损），'
                 '**不是**「买了不动扛到 T+5」的无止损口径——那是本脚本改口径前的旧算法。'
                 % ((1 - STOP_PCT) * 100, MIN_SAMPLE)),
        'noteGates': ('九门为「单门统计」：每道门拆通过/未通过两组，各报 T+5 均值与胜率；'
                      '「九门全过」是极稀疏事件、日均不足 %d 样本，单门才有可用数字。' % MIN_SAMPLE),
    }
    if args.json:
        print(json.dumps(stats, ensure_ascii=False))
    else:
        for key, nm in (('harmonic', '谐波'), ('accumulation', '吸筹'), ('duanban', '断板反包')):
            r = stats[key]
            print('[SIG] %-5s T+1 %s%% 胜率%s｜T+5 %s%% 胜率%s（%d 样本，止损率 %s）→ %s'
                  % (nm, r.get('avgPct1'), r.get('winRate1'),
                     r.get('avgPct5'), r.get('winRate5'), r['n'],
                     r.get('stopRate'), r['concl']))
        print('[SIG] 基准(随机日) T+5 %s%% 胜率%s（%d 样本，止损率 %s）'
              % (bench.get('avgPct5'), bench.get('winRate5'), bench['n'], bench.get('stopRate')))
        for key, nm in (('harmonic', '谐波'), ('accumulation', '吸筹'), ('duanban', '断板反包')):
            r, b = stats[key], bench
            if r.get('avgPct5') is None or b.get('avgPct5') is None:
                continue
            print('[SIG] %s 超额(T+5) = %+.2f 个百分点（信号 %+.2f%% vs 基准 %+.2f%%）'
                  % (nm, r['avgPct5'] - b['avgPct5'], r['avgPct5'], b['avgPct5']))
        cf = stats['breakout']
        print('[SIG] 等放量突破再买（两类信号合计 %d 样本）T+5 %s%% 胜率%s'
              % (cf['n'], cf.get('avgPct5'), cf.get('winRate5')))
        print('[SIG] 九门单门（通过组 T+5 相对未通过组的增量 pp）：')
        for g in GATE_KEYS:
            it = gates[g]
            print('     %-24s n=%-5d 过 %+.2f%%/%.1f%% → 不过 %+.2f%%/%.1f%%  差 %+s'
                  % (it['label'], it['pass']['n'], (it['pass'].get('avgPct5') or 0),
                     (it['pass'].get('winRate5') or 0) * 100,
                     (it['fail'].get('avgPct5') or 0),
                     (it['fail'].get('winRate5') or 0) * 100, it['diff5']))
    if args.dry:
        return stats
    # 写回 data.js 顶层 signalStats（只改这一个键，不动其它字段）
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
    print('[SIG] 写回 data.js.signalStats：谐波 %d / 吸筹 %d / 断板 %d / 九门单门 %d 门'
          % (hz_res['n'], ac_res['n'], db_res['n'], len(gates)))
    return stats


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""signal_attribution.py —— 指标归因台账与建议权重（R100z184，2026-10-24）

## 要解决什么

这套系统有四个量化选股模块（谐波形态 / 吸筹打分 / 九门强势 / 断板反包）加一个
AI 预测。它们**各自独立推荐**，但从来没有人回答一个最基本的问题：

    **这四个指标，哪一个现在是准的？**

没有这个答案，四个指标就只能等权看待——等于把「已经证明有效的信号」和
「一直亏钱的信号」按同样可信度摆在一起。这是「四指标选股」只能拿 4 分的根本原因：
**有指标，没归因**。

## 怎么做

**台账**（`signal_ledger.json`）：每天把每个指标选出的标的登记一次，记录登记日
与登记日收盘。之后回扫 K 线，算每个标的 **T+1 / T+3 / T+5 / T+10** 的收益，
以及与**全市场等权基准**的差额（超额）。

用全市场等权而不是上证指数作基准，理由：
- 上证是市值加权，被银行石油绑架，中小盘涨跌它看不出来；
- 这套系统的标的绝大多数是中小盘，拿上证比会系统性高估或低估；
- 全市场等权（1,300+ 只当日涨幅中位数累乘）就地可算、零外部依赖、可复现。

**建议权重**：只有样本够（≥ MIN_N 条 且 ≥ MIN_DAYS 个交易日）的指标才参与定权，
权重 ∝ max(0, 超额 + 1)，超额为负的指标权重归零。样本不足时**不给结论**，
显示「积累中」——宁可空着，也不拿 3 个样本编一个排名。

**冷启动**：台账第一天是空的，但 `stop_ledger.json` 已有 12 个交易日、1,900 条
真实回算样本，其 `stopSrc` 能区分「谐波形态位 / AI 自选 / 断板 T 日低点 / 机械池
兜底」四类。这一份先当「止损口径归因」贴出来，让页面第一天就有真数据可看，
同时与新建的指标台账分开标注，不混成一个数。

## 输出

写 data.js 顶层 `signalPerf`。前端「指标表现」卡渲染：每个指标一行（样本数、
5 日超额、胜率、判定），底部给建议权重与数据来源说明。

用法：
    python3 signal_attribution.py            # 登记今日 + 回算 + 写 data.js
    python3 signal_attribution.py --view     # 只打印统计，不登记
    python3 signal_attribution.py --dry      # 不写盘
    python3 signal_attribution.py --self-test
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common          # noqa: E402
import atr_stop as A   # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(ROOT, 'signal_ledger.json')
STOP_LEDGER = os.path.join(ROOT, 'stop_ledger.json')

HORIZONS = (1, 3, 5, 10)
MIN_N = 30          # 出结论最少样本数
MIN_DAYS = 5        # 出结论最少覆盖交易日
REVERSE_T = -2.0    # 1 日期望的 t 值低于此 → 判「负期望」，不是「运气差」

SRC_LABEL = {
    'harmonic': '谐波形态',
    'accumulation': '吸筹打分',
    'powerScreen': '九门强势',
    'duanban': '断板反包',
    'ai': 'AI 预测',
    'mech': '机械池（吸筹+九门）',
    'leadLag': '强板块落后股',
}

# stop_ledger 的 stopSrc → 主台账键名。stop_ledger 无法区分吸筹与九门
# （两者都落进 default-3%），所以合成一个 'mech'，不硬拆成两个假精度。
SRC_LEGACY_MAP = {'harmonic': 'harmonic', 'ai-exit': 'ai',
                  'T日低点': 'duanban', 'default-3.0%': 'mech', 'default-3%': 'mech'}


def _f(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def load_ledger():
    if os.path.exists(LEDGER):
        try:
            return json.load(open(LEDGER, encoding='utf-8'))
        except Exception:
            pass
    return {'entries': {}}


def save_ledger(L):
    common.write_json_atomic(LEDGER, L, indent=1)


def market_bench(K):
    """全市场等权日收益（当日中位数）与累乘基准。

    返回 {'daily': {day: pct}, 'cum': {day: 累计涨幅%}}。
    只在当天有 ≥50 只有效样本时才算（样本太少的日期不产出基准，宁可缺）。
    """
    by_day = {}
    for code, bars in K.items():
        b = A.norm_bars(bars)
        for i in range(1, len(b)):
            p0, p1 = b[i - 1][2], b[i][2]
            if p0 and p1 and p0 > 0:
                by_day.setdefault(b[i][0], []).append((p1 - p0) / p0 * 100)
    daily = {}
    for d, v in by_day.items():
        if len(v) >= 50:
            v.sort()
            daily[d] = v[len(v) // 2]
    cum, acc = {}, 1.0
    for d in sorted(daily):
        acc *= (1 + daily[d] / 100)
        cum[d] = (acc - 1) * 100
    return {'daily': daily, 'cum': cum}


def snapshot(D):
    """今日各指标选出的标的（code/name/sector/close）。按指标分组，同指标内去重。"""
    K = D.get('stkKlines') or {}
    out = {}
    for code, meta in A.collect(D):
        bars = A.norm_bars(K.get(code))
        close = bars[-1][2] if bars else None
        for s in meta['_srcs']:
            grp = out.setdefault(s, {})
            if code not in grp:
                grp[code] = {'code': code, 'name': meta['name'],
                             'sector': meta['sector'], 'close': close}
    return {s: list(v.values()) for s, v in out.items()}


def fwd_ret(bars, entry_day, n):
    """登记日之后第 n 个交易日的收益（%）。找不到登记日就返回 None，绝不补 0。"""
    idx = None
    for i, b in enumerate(bars):
        if b[0] == entry_day:
            idx = i
            break
    if idx is None or idx + n >= len(bars):
        return None
    c0, c1 = bars[idx][2], bars[idx + n][2]
    if not c0 or c0 <= 0 or c1 is None:
        return None
    return round((c1 - c0) / c0 * 100, 3)


def bench_ret(bench, entry_day, n):
    """基准同期收益：从登记日次日开始累乘 n 个交易日。"""
    days = sorted(bench['daily'])
    if entry_day not in days:
        return None
    i = days.index(entry_day)
    if i + n >= len(days):
        return None
    acc = 1.0
    for d in days[i + 1:i + 1 + n]:
        acc *= (1 + bench['daily'][d] / 100)
    return round((acc - 1) * 100, 3)


def exec_ret(bars, entry_day, h):
    """**可执行口径**：T+1 开盘买入 → T+h 收盘卖出（%）。h=1 即当日收盘卖。

    ## 为什么必须另算一套

    原来的 `fwd_ret` 是「登记日收盘买入」——那是 16:00 复盘写完的时刻，
    **用户根本买不到**（收盘了）。真实能下单的最早价格是次日开盘。

    这不是学术洁癖：实测（1,303 条真实选股 × T→T+1）隔夜跳空均值 −0.465%、
    胜率 31%，而全市场是 −0.181%、34%——**系统选出来的票比市场更容易跳空低开**。
    用「收盘买」记账等于把这 0.28pp 的额外损失算进成绩，页面显示的胜率被系统性压低；
    反过来，真实买入价平均比收盘价低，所以可执行口径的胜率反而更高。

    两套口径并列显示，谁都不许单独充当结论。
    """
    idx = None
    for i, b in enumerate(bars):
        if b[0] == entry_day:
            idx = i
            break
    if idx is None:
        return None
    i0, i1 = idx + 1, idx + h        # 买入 = 次日开盘；卖出 = 第 h 日收盘
    if i1 >= len(bars) or i0 >= len(bars):
        return None
    op = bars[i0][1]
    c1 = bars[i1][2]
    if not op or op <= 0 or c1 is None:
        return None
    return round((c1 - op) / op * 100, 3)


def tstat(xs):
    """均值的 t 值。样本 <5 或方差为 0 → None（不假装有显著性）。"""
    n = len(xs)
    if n < 5:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    if var <= 0:
        return None
    return round(m / (var / n) ** 0.5, 2)


def win_rate(xs):
    return round(len([x for x in xs if x > 0]) / len(xs), 3) if xs else None


def _xstat(rets, by_day):
    """可执行口径的统计块。

    ## t 值为什么要按日聚类

    同一天选出的 40 只票不是 40 个独立观测——它们共享同一个大盘、同一批板块。
    直接对 300 个「股票×日」求 t 值会把有效样本量高估十几倍，得出「t=−2.4、
    显著负期望、应停用」这种**过度自信**的结论（2026-10-24 实测就是差点这么写）。

    正确做法：先把当日所有票压成 1 个日收益，再对「日」求 t。这样
    t 的自由度 = 交易日数 − 1，样本少就自动不显著——这正是我们要的。
    `t`（逐票）保留作参考，判定只用 `tDaily`。
    """
    rets = rets or []
    daily = [sum(v) / len(v) for v in (by_day or {}).values() if v]
    return {
        'n': len(rets),
        'days': len(daily),
        'ret': round(sum(rets) / len(rets), 2) if rets else None,
        'win': win_rate(rets),
        't': tstat(rets),               # 逐票，仅参考
        'tDaily': tstat(daily),         # 按日聚类，判定用这个
        'dayWin': win_rate(daily),      # 按日口径的胜率（一天整体赚没赚）
    }


def compute(L, D):
    """回算全台账，按指标 × 持有期 × 两套入场口径出统计。

    两套口径并列（R100z186）：
      h   台账口径：登记日收盘买入 → T+h 收盘（旧口径，用户买不到，但可算超额）
      x   可执行口径：T+1 开盘买入 → T+h 收盘（真实能下单，只报绝对收益与胜率）
    """
    K = D.get('stkKlines') or {}
    bench = market_bench(K)
    stats = {}
    for day, groups in sorted((L.get('entries') or {}).items()):
        for src, items in groups.items():
            st = stats.setdefault(src, {
                'n': 0, 'days': set(),
                'h': {str(h): {'rets': [], 'excs': []} for h in HORIZONS},
                'x': {str(h): {'rets': [], 'byday': {}} for h in HORIZONS},
            })
            st['days'].add(day)
            for it in items:
                bars = A.norm_bars(K.get(it.get('code')))
                if len(bars) < 2:
                    continue
                got = False
                for h in HORIZONS:
                    r = fwd_ret(bars, day, h)
                    if r is None:
                        continue
                    got = True
                    st['h'][str(h)]['rets'].append(r)
                    b = bench_ret(bench, day, h)
                    if b is not None:
                        st['h'][str(h)]['excs'].append(r - b)
                    xr = exec_ret(bars, day, h)
                    if xr is not None:
                        st['x'][str(h)]['rets'].append(xr)
                        st['x'][str(h)]['byday'].setdefault(day, []).append(xr)
                if got:
                    st['n'] += 1
    return stats, bench


def summarize(stats):
    """把原始列表压成页面要用的统计量。"""
    out = {}
    for src, st in stats.items():
        rec = {'label': SRC_LABEL.get(src, src), 'n': st['n'], 'days': len(st['days']),
               'h': {}, 'x': {}}
        for h in HORIZONS:
            d = st['h'][str(h)]
            rets, excs = d['rets'], d['excs']
            rec['h'][str(h)] = {
                'n': len(rets),
                'ret': round(sum(rets) / len(rets), 2) if rets else None,
                'exc': round(sum(excs) / len(excs), 2) if excs else None,
                'win': win_rate(rets),
                'winExc': win_rate(excs),
                't': tstat(rets),
            }
            xd = (st.get('x') or {}).get(str(h), {})
            rec['x'][str(h)] = _xstat(xd.get('rets') or [], xd.get('byday') or {})
        rec['sampleEnough'] = (rec['n'] >= MIN_N and rec['days'] >= MIN_DAYS)
        out[src] = rec
    return out


def suggest_weights(summary):
    """建议权重。只有样本够的指标参与，超额为负的归零。"""
    usable = {s: r for s, r in summary.items() if r['sampleEnough']}
    if not usable:
        return {}, '样本不足（需 ≥%d 条且 ≥%d 个交易日），暂不给建议权重' % (MIN_N, MIN_DAYS)
    raw = {}
    for s, r in usable.items():
        e = r['h']['5'].get('exc')
        raw[s] = max(0.0, (e if e is not None else 0.0) + 1.0)
    tot = sum(raw.values())
    if tot <= 0:
        return {}, '所有指标的 5 日超额都为负或不可用，不给权重（等权也未必该买）'
    w = {s: round(v / tot, 3) for s, v in raw.items() if v > 0}
    zero = [SRC_LABEL.get(s, s) for s, v in raw.items() if v <= 0]
    msg = '按 5 日超额定权（超额+1 归一化）'
    if zero:
        msg += '；%s 超额为负，权重归零' % '、'.join(zero)
    return w, msg


def stop_evidence():
    """从 stop_ledger 取「止损口径」的现成回算结论，作冷启动证据。

    ⚠️ 这与指标归因**不是同一个口径**：它衡量的是「该止损位会不会被触及」，
    不是「该指标选的票涨不涨」。所以单独放在 `stopEvidence` 里、页面分开标注，
    绝不和 bySrc 混在一起算——混了就成了用一个口径的结论冒充另一个口径。
    """
    if not os.path.exists(STOP_LEDGER):
        return None
    try:
        L = json.load(open(STOP_LEDGER, encoding='utf-8'))
    except Exception:
        return None
    st = L.get('stats') or {}
    src_label = {'harmonic': '谐波形态位', 'ai-exit': 'AI 自选 −3%',
                 'T日低点': '断板 T 日低点', 'default-3.0%': '机械池 −3%',
                 'default-3%': '机械池 −3%'}
    rows = []
    by_touch = st.get('bySrcTouch') or {}
    by_p5 = st.get('bySrc') or {}
    for k, v in by_touch.items():
        p5 = (by_p5.get(k) or {}).get('p5')
        rows.append({'src': k, 'label': src_label.get(k, k), 'n': v.get('n'),
                     'touchRate': v.get('rate'), 'p5': p5})
    rows.sort(key=lambda x: -(x['p5'] if x['p5'] is not None else -99))
    return {'updatedAt': L.get('updatedAt'), 'total': st.get('total'),
            'touched': st.get('touched'), 'rows': rows}


def verdict_of(r):
    """一个指标的判定。**先看可执行口径，再看超额**。

    排序理由：用户关心的是「照这个指标买，第二天赚不赚钱」，不是「跑没跑赢基准」。

    分级刻意保守（R100z186）：
      · t 值一律用**按日聚类**的那一个。同一天 40 只票不是 40 个独立样本，
        用逐票 t 值会得出「t=−2.4、应停用」这种过度自信的结论。
      · 日数不够时只说「疑似」，不说「应停用」——只有 6-7 个交易日就宣判一个
        指标死刑，是拿噪声当证据；这也正是用户质问的「复盘到底有没有用」的同一类错误。

    文字刻意短（R100z191）：判定列只有约 270px，把收益/胜率/日数全塞进去会折成两行、
    右对齐读起来散架。收益、胜率、t、样本都各有专列，判定**只负责给结论**；
    但「应停用 / 可交易」这种重话必须自带证据（t 与日数），不让读者去别处核。
    """
    e5 = (r.get('h') or {}).get('5', {}).get('exc')
    x1 = (r.get('x') or {}).get('1') or {}
    t1, w1, nd = x1.get('tDaily'), x1.get('win'), x1.get('days') or 0
    n1 = x1.get('n') or 0
    if not r['sampleEnough']:
        return '积累中（%d 条/%d 日）' % (r['n'], r['days'])
    if t1 is not None and t1 <= REVERSE_T:
        if nd >= 10:
            return '负期望·应停用（t=%.1f、%d 日）' % (t1, nd)
        return '疑似负期望·观察中（t=%.1f，仅 %d 日）' % (t1, nd)
    if t1 is not None and t1 >= 2.0 and (w1 or 0) >= 0.60 and nd >= 10:
        return '可交易（t=%.1f、胜率 %.0f%%、%d 日）' % (t1, w1 * 100, nd)
    # 可执行口径有足够样本、但结论落在「既不显著正、也不显著负」的中间地带。
    #
    # 这一档**必须**留在这里、且不许掉回去用 5 日超额定生死。原因：5 日超额是
    # 「登记日收盘价买入」算出来的，而那个价格在复盘写完时已经不存在了。
    # 实测踩过：断板反包 1 日 +0.34%（买得到的价格是赚的），5 日超额 −0.78%，
    # 旧逻辑判「无效（跑输基准）」——页面上同一行里 +0.34% 与「无效」并排，
    # 读者只能得出「这张表是乱的」。买不到的价格不能用来宣判买得到的策略。
    if t1 is not None and n1 >= MIN_N:
        return '未定（%d 日%s）' % (nd, '，样本还少' if nd < 10 else '')
    # 兜底：连可执行口径都算不出来（K 线不够）时，才退回 5 日超额。
    # 这一档天然偏弱，且日数越少越不可信——把「几日的样本」直接挂在判定后面，
    # 不许只给结论不给样本量。
    tag = '' if (r.get('days') or 0) >= 10 else '（仅 %d 日·不可据此停用）' % (r.get('days') or 0)
    if e5 is None:
        return '不可判（5 日超额缺失）'
    if e5 > 0.5 and (r['h']['5'].get('winExc') or 0.5) > 0.5:
        return '弱有效（跑赢基准但绝对收益不一定为正）' + tag
    if e5 > 0:
        return '弱有效' + tag
    return '无效（跑输基准）' + tag


def build(D, do_snapshot=True):
    L = load_ledger()
    ent = L.setdefault('entries', {})
    trade_date = str((D.get('ashare') or {}).get('tradeDate')
                     or (D.get('aiPrediction') or {}).get('date') or '')[:10]

    if do_snapshot and trade_date:
        snap = snapshot(D)
        cur = ent.get(trade_date) or {}
        added = 0
        for src, items in snap.items():
            known = {x['code'] for x in cur.get(src, [])}
            new = [x for x in items if x['code'] not in known]
            if new:
                cur[src] = (cur.get(src) or []) + new
                added += len(new)
        if cur:
            ent[trade_date] = cur
        if added:
            print('[OK] 登记 %s：新增 %d 条指标选股记录' % (trade_date, added))

    stats, bench = compute(L, D)
    summ = summarize(stats)

    # 冷启动：新台账第一天必然回算不出样本（今天的票还没有明天）。此时代用
    # stop_ledger 的历史样本顶上，并把 dataSource 标成 stop_ledger，
    # 前端据此显示「数据来源」，不让读者误以为这是新台账算出来的。
    legacy, legacy_at, legacy_days = stats_from_stops(D, bench)
    data_source = 'signal_ledger'
    if legacy and not any(r['sampleEnough'] for r in summ.values()):
        data_source = 'stop_ledger'
        for lsrc, r in legacy.items():
            key = SRC_LEGACY_MAP.get(lsrc, lsrc)
            if (summ.get(key) or {}).get('sampleEnough'):
                continue          # 新台账已经够了，不用旧的覆盖
            summ[key] = {
                'label': SRC_LABEL.get(key, key),
                'n': r['n'], 'days': r['days'],
                'h': {str(h): {'n': r['n'], 'ret': None, 'exc': None,
                               'win': None, 'winExc': None, 't': None} for h in HORIZONS},
                'x': r.get('x') or {},
                'sampleEnough': r['n'] >= MIN_N and r['days'] >= MIN_DAYS,
                'legacy': True,
                'touchRate': r['touchRate'],
            }
            summ[key]['h']['5'] = {'n': r['n'], 'ret': r['ret5'], 'exc': r['exc5'],
                                   'win': r['win5'], 'winExc': None,
                                   't': r['x'].get('2', {}).get('t') if r.get('x') else None}

    weights, wmsg = suggest_weights(summ)

    ranking = []
    for s, r in summ.items():
        e5 = r['h']['5'].get('exc')
        x1 = (r.get('x') or {}).get('1') or {}
        x2 = (r.get('x') or {}).get('2') or {}
        ranking.append({'src': s, 'label': r['label'], 'n': r['n'], 'days': r['days'],
                        'ret5': r['h']['5'].get('ret'), 'exc5': e5,
                        'win5': r['h']['5'].get('winExc'),
                        # 可执行口径：1 日（当日收盘卖）/ 2 日（次日收盘卖）
                        'ret1': x1.get('ret'), 'win1': x1.get('win'),
                        't1': x1.get('tDaily'), 't1raw': x1.get('t'),
                        'days1': x1.get('days'),
                        'ret2': x2.get('ret'), 'win2': x2.get('win'),
                        't2': x2.get('tDaily'),
                        'touchRate': r.get('touchRate'),
                        'verdict': verdict_of(r), 'sampleEnough': r['sampleEnough'],
                        'legacy': bool(r.get('legacy')),
                        'weight': weights.get(s)})
    # 排序：能交易的排前面（t1 降序），没 t1 的按 5 日超额，最后按样本
    ranking.sort(key=lambda x: (-(x['t1'] if x['t1'] is not None else -99),
                                -(x['exc5'] if x['exc5'] is not None else -99),
                                -x['n']))

    if do_snapshot and ent:
        L['updatedAt'] = _now()
        save_ledger(L)

    return {
        'updatedAt': _now(),
        'asOf': trade_date,
        'horizons': list(HORIZONS),
        'criteria': {'minN': MIN_N, 'minDays': MIN_DAYS, 'reverseT': REVERSE_T,
                     'bench': '全市场等权（1300+ 只当日涨幅中位数累乘）',
                     'excess': '标的收益 − 同期基准收益（仅「收盘买入」口径有）',
                     'hBasis': '收盘买入：登记日收盘 → T+h 收盘（旧口径，复盘写完时已收盘，买不到）',
                     'xBasis': '可执行：T+1 开盘 → T+h 收盘（1 日 = 当日收盘卖，2 日 = 次日收盘卖）'},
        'bySrc': summ,
        'ranking': ranking,
        'weights': weights,
        'weightNote': wmsg,
        'dataSource': data_source,
        'dataSourceNote': (
            '新台账（signal_ledger）刚开始登记，样本不足以回算；当前使用 stop_ledger 的 '
            '%d 个交易日历史样本（截至 %s）。新台账样本达标后自动切换。'
            % (legacy_days, legacy_at or '—') if data_source == 'stop_ledger' else
            '来自 signal_ledger（本指标台账，逐日登记、逐日回算）'),
        'stopEvidence': stop_evidence(),
        'ledger': {'dates': len(ent), 'entries': sum(len(v) for g in ent.values()
                                                     for v in g.values())},
    }


def stats_from_stops(D, bench):
    """用 stop_ledger 的历史条目算「按指标来源的选股收益 + 超额」。

    ## 为什么可以直接用

    之前我把它当成「止损口径、不能当选股用」而排除掉，这是错的：
    stop_ledger 的每条记录都是**某个指标在某个交易日选出的票**，其 `p5Pct`
    是「入场后第 5 个交易日相对入场价的涨跌」——**与止损设在哪无关**，
    就是选股收益本身。只有 `touchRate` 才是止损口径。

    所以这张表同时给出两列，页面分开标注：
        ret5  选股能力（这个指标选的票 5 日赚不赚）
        touch 止损口径（这个指标的止损位合不合适）

    局限（必须显示出来，不能假装没有）：
      · `stopSrc` 无法区分「吸筹」与「九门」（两者都落进 default-3%）；
      · 同一只票被多指标选中时，stop_ledger 只记首个来源（先到先得），
        会低估共振票。这两点由新的 signal_ledger 逐步接管。

    返回 (out, updatedAt, 覆盖交易日数)，out 形如 {src: {...}}，
    src 用与主台账一致的键名，便于前端统一渲染。
    """
    if not os.path.exists(STOP_LEDGER):
        return {}, None, 0
    try:
        L = json.load(open(STOP_LEDGER, encoding='utf-8'))
    except Exception:
        return {}, None, 0
    ent = L.get('entries') or {}
    K = D.get('stkKlines') or {}
    acc = {}
    for day, items in sorted(ent.items()):
        for it in items:
            src = it.get('stopSrc') or it.get('from') or 'unknown'
            bars = A.norm_bars(K.get(str(it.get('code'))))
            if len(bars) < 2:
                continue
            r5 = fwd_ret(bars, day, 5)
            if r5 is None:
                continue
            b5 = bench_ret(bench, day, 5)
            a = acc.setdefault(src, {'rets': [], 'excs': [], 'touch': 0, 'n': 0, 'days': set(),
                                     'x1': [], 'x2': [], 'x1d': {}, 'x2d': {}})
            a['rets'].append(r5)
            if b5 is not None:
                a['excs'].append(r5 - b5)
            # 冷启动路径也必须给可执行口径——否则页面上最重要的两列全是「—」，
            # 等于把这个模块的核心问题（「照它买能不能赚」）又藏起来了。
            for _h, _k, _dk in ((1, 'x1', 'x1d'), (2, 'x2', 'x2d')):
                xr = exec_ret(bars, day, _h)
                if xr is not None:
                    a[_k].append(xr)
                    a[_dk].setdefault(day, []).append(xr)
            a['n'] += 1
            a['days'].add(day)
            if (it.get('res') or {}).get('touched'):
                a['touch'] += 1
    out = {}
    all_days = set()
    for src, a in acc.items():
        all_days |= a['days']
        out[src] = {
            'n': a['n'], 'days': len(a['days']),
            'ret5': round(sum(a['rets']) / len(a['rets']), 2) if a['rets'] else None,
            'exc5': round(sum(a['excs']) / len(a['excs']), 2) if a['excs'] else None,
            'win5': round(len([x for x in a['rets'] if x > 0]) / len(a['rets']), 3)
            if a['rets'] else None,
            'touchRate': round(a['touch'] / a['n'], 3) if a['n'] else None,
            'x': {'1': _xstat(a['x1'], a['x1d']), '2': _xstat(a['x2'], a['x2d'])},
        }
    return out, L.get('updatedAt'), len(all_days)


def _now():
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def self_test():
    """自检：收益计算、基准累乘、样本门槛、权重归零四件事。"""
    ok = True

    def chk(name, got):
        nonlocal ok
        print(('  OK   ' if got else '  FAIL ') + name)
        ok = ok and bool(got)

    # ① 前向收益：登记日收盘 100，之后 1/3/5/10 日分别 101/103/105/110
    bars = [('2026-10-01', 100, 100, 101, 99, 1e6),
            ('2026-10-02', 100, 101, 102, 100, 1e6),
            ('2026-10-03', 101, 101, 102, 100, 1e6),
            ('2026-10-04', 101, 103, 104, 101, 1e6),
            ('2026-10-05', 103, 104, 105, 103, 1e6),
            ('2026-10-06', 104, 105, 106, 104, 1e6),
            ('2026-10-07', 105, 106, 107, 105, 1e6),
            ('2026-10-08', 106, 107, 108, 106, 1e6),
            ('2026-10-09', 107, 108, 109, 107, 1e6),
            ('2026-10-10', 108, 109, 110, 108, 1e6),
            ('2026-10-11', 109, 110, 111, 109, 1e6)]
    b = A.norm_bars(bars)
    chk('T+1 收益 = +1.0（实测 %s）' % fwd_ret(b, '2026-10-01', 1), fwd_ret(b, '2026-10-01', 1) == 1.0)
    chk('T+3 收益 = +3.0（实测 %s）' % fwd_ret(b, '2026-10-01', 3), fwd_ret(b, '2026-10-01', 3) == 3.0)
    chk('T+10 收益 = +10.0（实测 %s）' % fwd_ret(b, '2026-10-01', 10),
        fwd_ret(b, '2026-10-01', 10) == 10.0)
    chk('未来不够 → None（不补 0）', fwd_ret(b, '2026-10-05', 10) is None)
    chk('登记日不存在 → None', fwd_ret(b, '2026-09-01', 1) is None)

    # ② 基准累乘：连续 +10% 两日 → +21%
    bench = {'daily': {'2026-10-01': 0.0, '2026-10-02': 10.0, '2026-10-03': 10.0},
             'cum': {}}
    chk('基准 2 日累乘 = +21.0（实测 %s）' % bench_ret(bench, '2026-10-01', 2),
        abs((bench_ret(bench, '2026-10-01', 2) or 0) - 21.0) < 0.01)

    # ③ 样本门槛：不足不给权重
    thin = {'harmonic': {'label': '谐波形态', 'n': 2, 'days': 1, 'h': {
        str(h): {'n': 2, 'ret': 5.0, 'exc': 5.0, 'win': 1.0, 'winExc': 1.0} for h in HORIZONS},
        'sampleEnough': False}}
    w, msg = suggest_weights(thin)
    chk('样本不足 → 权重为空', w == {})
    chk('样本不足 → 说明写明需多少', '需' in msg)

    # ④ 权重：超额为正的才有权重，负的归零；合计为 1
    good = {}
    for s, e in (('harmonic', 2.0), ('accumulation', -1.5), ('powerScreen', 1.0)):
        good[s] = {'label': s, 'n': 100, 'days': 10, 'sampleEnough': True,
                   'h': {str(h): {'n': 100, 'ret': e, 'exc': e, 'win': 0.6, 'winExc': 0.6}
                         for h in HORIZONS}}
    w2, msg2 = suggest_weights(good)
    chk('超额为正的指标有权重', 'harmonic' in w2 and 'powerScreen' in w2)
    chk('超额为负的指标权重归零（不在表里）', 'accumulation' not in w2)
    chk('权重合计 = 1.0（实测 %s）' % round(sum(w2.values()), 3),
        abs(sum(w2.values()) - 1.0) < 0.01)
    chk('零权说明里点名了该指标', '吸筹打分' in msg2)

    # ⑤ 可执行口径：T+1 开盘买入
    b2 = [('2026-10-01', 100, 100, 101, 99, 1e6),      # 登记日：收 100
          ('2026-10-02', 90, 99, 100, 89, 1e6),        # 次日：开 90（低开 10%）、收 99
          ('2026-10-03', 99, 108, 109, 98, 1e6)]       # 再次日：收 108
    b2 = A.norm_bars(b2)
    chk('可执行 1 日 = 90→99 = +10.0（实测 %s）' % exec_ret(b2, '2026-10-01', 1),
        exec_ret(b2, '2026-10-01', 1) == 10.0)
    chk('可执行 2 日 = 90→108 = +20.0（实测 %s）' % exec_ret(b2, '2026-10-01', 2),
        exec_ret(b2, '2026-10-01', 2) == 20.0)
    # 关键区分：同一段行情，收盘买入是 −1%（100→99），开盘买入是 +10%（90→99）。
    # 隔夜低开 10% 被「收盘买」记成亏损、被「开盘买」记成利润——这正是两套口径必须并列的原因。
    chk('同段行情收盘口径 = −1.0（实测 %s）' % fwd_ret(b2, '2026-10-01', 1),
        fwd_ret(b2, '2026-10-01', 1) == -1.0)
    chk('未来不足 → 可执行口径也返回 None', exec_ret(b2, '2026-10-03', 1) is None)

    # ⑥ 显著性
    chk('t 值：恒 +1% 序列方差为 0 → None（不假装显著）', tstat([1.0] * 10) is None)
    chk('t 值：样本 <5 → None', tstat([1.0, 2.0, 3.0]) is None)
    chk('t 值：+1/−1 交替 → 均值 0、t≈0（实测 %s）' % tstat([1.0, -1.0] * 20),
        abs(tstat([1.0, -1.0] * 20)) < 0.001)
    chk('胜率：3/4 = 0.75（实测 %s）' % win_rate([1, -1, 2, 3]), win_rate([1, -1, 2, 3]) == 0.75)

    # ⑦ 判定优先级：负期望必须压过「5 日超额为正」
    neg = {'n': 300, 'days': 10, 'sampleEnough': True,
           'h': {str(h): {'n': 300, 'ret': -1.0, 'exc': 2.0, 'win': 0.3, 'winExc': 0.55}
                 for h in HORIZONS},
           'x': {'1': {'n': 300, 'ret': -0.5, 'win': 0.42, 't': -3.1, 'tDaily': -3.1, 'days': 12},
                 '2': {'n': 300, 'ret': -1.2, 'win': 0.38, 't': -4.0, 'tDaily': -4.0, 'days': 12}}}
    chk('1 日 t=−3.1 → 判「负期望·应停用」（实测 %s）' % verdict_of(neg),
        '负期望' in verdict_of(neg))
    good1 = {'n': 300, 'days': 10, 'sampleEnough': True,
             'h': {str(h): {'n': 300, 'ret': 1.0, 'exc': 0.2, 'win': 0.6, 'winExc': 0.55}
                   for h in HORIZONS},
             'x': {'1': {'n': 200, 'ret': 1.29, 'win': 0.68, 't': 3.4, 'tDaily': 3.4, 'days': 12},
                   '2': {'n': 150, 'ret': 1.5, 'win': 0.66, 't': 2.9, 'tDaily': 2.9, 'days': 12}}}
    chk('1 日 t=+3.4 且胜率 68%% → 判「可交易」（实测 %s）' % verdict_of(good1),
        '可交易' in verdict_of(good1))
    thin2 = {'n': 5, 'days': 1, 'sampleEnough': False,
             'h': {str(h): {'n': 5, 'ret': -5.0, 'exc': -5.0, 'win': 0.0, 'winExc': 0.0}
                   for h in HORIZONS},
             'x': {'1': {'n': 5, 'ret': -5.0, 'win': 0.0, 't': -9.0, 'tDaily': -9.0, 'days': 1}}}
    chk('样本不足时即便 t 很负也不判负期望（实测 %s）' % verdict_of(thin2),
        '积累中' in verdict_of(thin2))

    # ⑦b 日数不够时不许说「应停用」——这是本项目最贵的教训：
    # 只有 7 个交易日、每天几十只票高度相关，算出 t=−2.4 也不能宣判死刑。
    thin3 = {'n': 248, 'days': 7, 'sampleEnough': True,
             'h': {str(h): {'n': 248, 'ret': -3.88, 'exc': 0.43, 'win': 0.35, 'winExc': 0.52}
                   for h in HORIZONS},
             'x': {'1': {'n': 248, 'ret': -0.44, 'win': 0.41, 't': -2.4, 'tDaily': -2.4, 'days': 7}}}
    v3 = verdict_of(thin3)
    chk('248 条但只有 7 日 → 说「疑似」不说「应停用」（实测 %s）' % v3,
        ('疑似' in v3) and ('应停用' not in v3))
    # 兜底档（按 5 日超额判）也必须报出样本日数，否则读者不知道这个结论有多虚。
    weak4 = {'n': 300, 'days': 7, 'sampleEnough': True,
             'h': {str(h): {'n': 300, 'ret': 0.5, 'exc': 0.6, 'win': 0.5, 'winExc': 0.55}
                   for h in HORIZONS},
             'x': {'1': {'n': 300, 'ret': None, 'win': None, 't': None, 'tDaily': None, 'days': 0}}}
    chk('兜底档判定标注了「仅 N 日」（实测 %s）' % verdict_of(weak4), '仅 7 日' in verdict_of(weak4))

    # ⑦c 中间地带：1 日买得到的价格是赚的、5 日超额是负的——不许判「无效」。
    # 实测就是断板反包那一行（1 日 +0.34% / 5 日超额 −0.78%），旧逻辑判「无效（跑输基准）」，
    # 页面上同一行 +0.34% 与「无效」并排，读者只会认为这张表坏了。
    mid = {'n': 334, 'days': 6, 'sampleEnough': True,
           'h': {str(h): {'n': 334, 'ret': -5.17, 'exc': -0.78, 'win': 0.35, 'winExc': 0.45}
                 for h in HORIZONS},
           'x': {'1': {'n': 334, 'ret': 0.34, 'win': 0.473, 't': 1.28, 'tDaily': 0.43, 'days': 6}}}
    vm = verdict_of(mid)
    chk('1 日 +0.34%% 但 5 日超额 −0.78%% → 判「未定」不判「无效」（实测 %s）' % vm,
        ('未定' in vm) and ('无效' not in vm))
    # 判定列只有 ~270px：结论要短，样本少要说出来，但收益/胜率/t 不许再重复进这一格
    # （旁边就有专列）。写长了会折成两行、右对齐散架。
    chk('「未定」带上样本日数、且不再重复收益数字（实测 %s）' % vm,
        ('6 日' in vm) and ('%' not in vm))

    print('[SELF-TEST] ' + ('OK' if ok else 'FAIL'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--view', action='store_true', help='只打印统计，不登记今日')
    ap.add_argument('--dry', action='store_true', help='不写 data.js')
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    D = common.load_dashboard_data(ROOT)
    if D is None:
        print('[ERR] data.js 解析失败', file=sys.stderr)
        return 1
    out = build(D, do_snapshot=not a.view)
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
        return 0

    print('\n=== 指标归因（谁准，谁不准）===')
    if out['dataSource'] == 'stop_ledger':
        print('[来源] ' + out['dataSourceNote'])
    print('本台账 %d 个交易日 / %d 条记录｜判定门槛 ≥%d 条且 ≥%d 日'
          % (out['ledger']['dates'], out['ledger']['entries'], MIN_N, MIN_DAYS))
    print('\n  【可执行口径】T+1 开盘买入（这是唯一买得到的价格）')
    print('  指标             样本  覆盖    1日(当日收盘卖)      2日(次日收盘卖)     判定')
    for r in out['ranking']:
        print('  %-14s %4d条/%2d日  %s 胜%3s t=%5s  %s 胜%3s  %s'
              % (r['label'], r['n'], r['days'],
                 '  —   ' if r['ret1'] is None else '%+6.2f%%' % r['ret1'],
                 '—' if r['win1'] is None else '%.0f%%' % (r['win1'] * 100),
                 '—' if r['t1'] is None else '%+.1f' % r['t1'],
                 # t 值按日聚类，自由度 = 交易日数−1；逐票 t 会严重高估显著性
                 '  —   ' if r['ret2'] is None else '%+6.2f%%' % r['ret2'],
                 '—' if r['win2'] is None else '%.0f%%' % (r['win2'] * 100),
                 r['verdict']))
    print('\n  【台账原口径】登记日收盘买入（复盘写完时已收盘，买不到；仅用于和历史对比）')
    print('  指标             样本  5日收益  5日超额  超额胜率  止损触及')
    for r in out['ranking']:
        h5 = (out['bySrc'].get(r['src']) or {}).get('h', {}).get('5', {})
        print('  %-14s %4d  %s  %s  %s  %s'
              % (r['label'], r['n'],
                 '  —    ' if h5.get('ret') is None else '%+6.2f%%' % h5['ret'],
                 '—' if r['exc5'] is None else '%+6.2f%%' % r['exc5'],
                 '—' if r['win5'] is None else '%.0f%%' % (r['win5'] * 100),
                 '—' if r.get('touchRate') is None else '%.1f%%' % (r['touchRate'] * 100)))
    print('\n  建议权重：' + (', '.join('%s %.2f' % (SRC_LABEL.get(k, k), v)
                                     for k, v in out['weights'].items()) or '— 未定'))
    print('  依据：' + out['weightNote'])

    se = out.get('stopEvidence')
    if se:
        print('\n  附：止损口径实测（来自 stop_ledger，%d 条已完成跟踪，触及 %d 条）'
              % (se['total'] or 0, se['touched'] or 0))
        for r in se['rows']:
            print('    %-14s 样本 %4s  触及率 %5s  入场后5日 %6s'
                  % (r['label'], r['n'],
                     '—' if r['touchRate'] is None else '%.1f%%' % (r['touchRate'] * 100),
                     '—' if r['p5'] is None else '%+.2f%%' % r['p5']))
        print('    注：此表衡量「该止损位会不会被触及」，与上面的「指标选股赚不赚」'
              '是两个口径，不可相加或互相替代。')

    if a.dry:
        print('\n（--dry，未写 data.js）')
        return 0
    D['signalPerf'] = out
    common.write_data_js(ROOT, D, '[signal_attribution]')
    print('\n[OK] 已写回 data.js 顶层 signalPerf')
    return 0


if __name__ == '__main__':
    sys.exit(main())

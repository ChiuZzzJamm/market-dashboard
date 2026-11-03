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
    python3 signal_attribution.py --backfill 80   # 从 git 历史补「今日可下单」清单（幂等）
    python3 signal_attribution.py --self-test
"""
import argparse
import json
import os
import re          # R100z255：--backfill 要解析 git 历史里的 data.js 文本
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
    'ai': 'AI 预测',
    'mech': '机械池（吸筹+横盘启动）',
    'duanban': '断板反包',
    # R100z297：断板反包按池拆开（历史合并口径仍用上面那行，见 ROW_ORDER 的说明）
    'duanban_confirmed': '断板反包·确认池',
    'duanban_watching': '断板反包·观察池',
    'chen': '陈学长',
    'chen_strict': '陈学长·严格池',
    'chen_loose': '陈学长·放宽池',
    'breakoutPool': '横盘启动',
    'harmonic': '谐波形态',
    'accumulation': '主力吸筹',
    # R100z255：全站唯一「明确告诉你现在能买」的清单，此前是唯一没有台账的源。
    'buy': '今日可下单',
    # R100z297：按来源池拆开
    'buy_duanban': '今日可下单·断板确认',
    'buy_chen': '今日可下单·陈学长放宽',
    'buy_tech': '今日可下单·技术池',
    'buy_other': '今日可下单·其他来源',
}

# 页面展示顺序（用户 2026-10-26 指定）：七个指标逐行分开，不再合并成「机械池」一行。
# 不在这个元组里的 src 一律不展示——powerScreen（九门）已废弃，台账里还留着 30 条历史记录，
# 不过滤就会在页面上挂一行永久「累积中」的死指标。
# R100z255：'buy'（今日可下单）排在最后，**不动用户点名的那七个的顺序**——
# 它是一个「组合清单」而非第八个指标，放在末尾既不打乱原有对照，又能一眼看到
# 「这份清单按自己的口径算下来是什么水平」。
# R100z297（用户 2026-11-03「是汇总的话拆开」）：duanban / chen / buy 三个汇总行下面
# **紧跟各自的子池行**（缩进由前端按 `sub_of` 处理）。汇总行保留历史（改版前的条目
# 没有池归属，拆不开），子行从改版当天起重新积累——两边不重复计数。
# R100z298（用户 2026-11-03 截图圈出：「今日可下单·断板确认 / ·陈学长放宽 不是重复的吗」）：
# buy 的四个子行**不再单独占行**。原因不是数据错，是它们与上面「断板反包·确认池」
# 「陈学长·放宽池」是同一批票的子集——并排摆着会让人以为是两组独立成绩。台账里仍按
# 来源分开登记（note 里报构成用），只是页面压成一行：`buy` 回答的是「照清单买赚不赚」，
# 上面几行回答的是「池选出来的票赚不赚」，两个问题，一行足够。
ROW_ORDER = ('ai', 'mech',
             'duanban', 'duanban_confirmed', 'duanban_watching',
             'chen', 'chen_strict', 'chen_loose',
             'breakoutPool', 'harmonic', 'accumulation',
             'buy')

# 子池行 → 它的父汇总行。前端据此把子行缩进、去掉重复的「断板反包」前缀感。
SUB_OF = {
    'duanban_confirmed': 'duanban', 'duanban_watching': 'duanban',
    'chen_strict': 'chen', 'chen_loose': 'chen',
    'buy_duanban': 'buy', 'buy_chen': 'buy', 'buy_tech': 'buy', 'buy_other': 'buy',
}

# stop_ledger 的 stopSrc → 主台账键名。stop_ledger 无法区分吸筹与横盘启动
# （两者都落进 default-3%），所以合成一个 'mech'，不硬拆成两个假精度。
SRC_LEGACY_MAP = {'harmonic': 'harmonic', 'ai-exit': 'ai',
                  'T日低点': 'duanban', 'default-3.0%': 'mech', 'default-3%': 'mech'}


def _f(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def load_ledger():
    # R100z273：读坏不许静默变空账本——save_ledger 整份覆盖写，读成空就把历史冲掉。
    l = common.load_json_quarantine(LEDGER, None, tag='[signal_attribution] 归因台账')
    return l if isinstance(l, dict) else {'entries': {}}


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

    def _put(s, code, name, sector):
        c = str(code or '').strip()
        if not c:
            return
        bars = A.norm_bars(K.get(c))
        grp = out.setdefault(s, {})
        if c not in grp:
            grp[c] = {'code': c, 'name': name or '',
                      'sector': (sector or '').strip(),
                      'close': bars[-1][2] if bars else None}

    # R100z297（用户 2026-11-03）：`duanban` / `chen` / `buy` 这三行原来是**汇总口径**，
    # 页面上只给一个总胜率，读者没法知道是哪个池拉高/拉低的：
    #   duanban = confirmed + watching（观察池还没确认就记账，等于抢跑）
    #   chen    = strict + loose（观察池已排除，但严格/放宽两档被合成一行）
    #   buy     = buyList 的 duanban(2) + chen_loose(5)（剔了 bp）
    # 现在按子池分开登记，各算各的胜率。`_SUB` 里的源不再从 A.collect 的共振结果里取，
    # 改由下面按池逐个登记——**注意**不能两边都加，同一只票会被记两次。
    _SUB = ('duanban', 'chen')
    for code, meta in A.collect(D):
        for s in meta['_srcs']:
            if s in _SUB:
                continue
            _put(s, code, meta['name'], meta['sector'])

    # 断板反包：确认池 / 观察池分开登记。观察池是「还差确认」的票，混进确认池会把
    # 「没确认就买」的抢跑样本算成命中——与陈学长那边排除 watching 的理由完全一样。
    db = D.get('duanban') or {}
    for pool, key in (('confirmed', 'duanban_confirmed'), ('watching', 'duanban_watching')):
        for x in db.get(pool) or []:
            _put(key, x.get('code'), x.get('name'), x.get('sector'))

    # 陈学长：拆成严格池 / 放宽池。仍只登记「已确认」的两档，watching 是观察池、还没确认，
    # 记进来会把「没确认的票」算成指标命中，凭空抬高它的胜率。
    # 注：不往 atr_stop.collect 里加——那会把这批票塞进全站止损清单，改变 atrStops 的覆盖口径。
    cp = D.get('chenPool') or {}
    for pool, key in (('strict', 'chen_strict'), ('loose', 'chen_loose')):
        for x in cp.get(pool) or []:
            _put(key, x.get('code'), x.get('name'), x.get('sector'))

    # ---- R100z255：今日可下单（duanban.star.buyList） ----
    # 为什么单独登记这一源：它是全站唯一「明确告诉你现在能买、买在哪个价带、破哪个价撤」的清单，
    # 却也是唯一没有任何后续台账的出口——24.109 用**回测**证明过各源 edge 大小，
    # 但「按这套清单真买会怎样」从来没被自己的实盘快照验证过。剔掉横盘启动源
    # （24.110）这个决定是否正确，只能靠这张表回答。
    # 口径必须与生成端一致：剔除 src='bp'（T+5 超额 t=0.41 不显著），否则台账里会多出
    # 21 条「本来就不让人买」的记录，把这份清单的胜率拉向一个没人执行过的结果。
    # R100z297：再按来源池拆开（断板反包·确认池 / 陈学长·放宽池），否则读者看到一个
    # 60.9% 却不知道是哪个池贡献的。srcZh 由 update_star.py 写好，按它分流。
    bl = ((D.get('duanban') or {}).get('star') or {}).get('buyList') or []
    for x in bl:
        src = str(x.get('src') or '')
        if src == 'bp':
            continue
        _zh = str(x.get('srcZh') or '')
        if '陈学长' in _zh:
            key = 'buy_chen'
        elif '断板' in _zh:
            key = 'buy_duanban'
        elif '技术池' in _zh or '板块' in _zh:
            key = 'buy_tech'
        else:
            key = 'buy_other'
        _put(key, x.get('code'), x.get('name'), x.get('sector'))

    return {s: list(v.values()) for s, v in out.items()}


def buy_entry_date(D):
    """清单的登记日 = **清单生成日**（star.date），不是 ashare.tradeDate。

    ## 为什么必须分开算（2026-10-28 实测）

    `ashare.tradeDate` 是「数据步最近一次跑出来的交易日」，在 16:00 数据步之前它仍是 **T−1**
    （实测 10-28 10:49：star.date=2026-10-28，ashare.tradeDate=2026-10-27）。
    若拿它当登记日，今天 9:45 生成的清单会被记到**昨天**名下，次日回算就变成
    「昨天买的、今天已经涨了」——凭空生出一笔从不存在的收益，而且**没有任何闸门会拦**
    （条目结构完全合法，只是日期错了）。

    这与 24.101「加护栏必须验证拦住之后落到哪」是同一类错：错在标杆选错了对象，
    而不是算错了数。清单的可下单日，只由清单自己的生成日决定。
    """
    d = str((((D.get('duanban') or {}).get('star') or {}).get('date')) or '')[:10]
    return d or None


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
    """**可执行口径**：T+1 开盘买入 → 买入日之后第 h 个交易日收盘卖出（%）。

    h=1 ＝ 次日收盘卖（买入日 b 的下一交易日）—— 这是 A 股 T+1 交割下最早的合法卖点。
    （R100z298 前写的是 `T+h 收盘卖`，h=1 会退化成「当天开盘买、当天收盘卖」，卖不掉。）

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
    # R100z298（用户 2026-11-03「交易都是 T+1 的，你次日开盘买、次日收盘同时卖怎么卖得出去」）：
    # 原写 `i1 = idx + h`，h=1 时 i0 == i1 == idx+1 —— **当天开盘买、当天收盘卖**，
    # A 股 T+1 交割下根本卖不掉，等于给 1 日列编了一个不存在的收益。
    # 改成 `i1 = i0 + h`：买入日 b = idx+1，卖出 = b 之后第 h 个交易日收盘。
    # h=1 → 次日（b+1）收盘，这才是「持 1 日」在 A 股的真实最早卖点。
    i0, i1 = idx + 1, idx + 1 + h
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

    # R100z297（用户 2026-11-03「拆开」）：登记口径改成子池后，三个汇总行不再有自己的
    # entries，合计要由子源**现算**——否则页面只剩「确认池 70 条 / 观察池 126 条」，
    # 读者看不到 196 条的总账。合计与子行不重复计数：duanban 的两个池互斥、
    # chen 的严格/放宽互斥、buyList 每只票只带一个 src。
    for parent, kids in (('duanban', ('duanban_confirmed', 'duanban_watching')),
                         ('chen', ('chen_strict', 'chen_loose')),
                         ('buy', ('buy_duanban', 'buy_chen', 'buy_tech', 'buy_other'))):
        subs = [stats[k] for k in kids if k in stats]
        if not subs:
            continue
        merged = _merge_stats(subs)
        if parent in stats:          # 改版前的历史条目还在父键上 → 一起并进来
            merged = _merge_stats([stats[parent], merged])
        stats[parent] = merged
    return stats, bench


def _merge_stats(subs):
    """把多个 src 的原始统计块合并成一个（用于「子池 → 合计」）。

    注意只能合**原始列表**，不能合已经算好的统计量：胜率不能简单平均
    （70 条 44% + 126 条 47% ≠ 两条平均 45.5%，要按样本数加权），t 值更不能加。
    所以这一步必须在 summarize() 之前做。
    """
    out = {'n': 0, 'days': set(),
           'h': {str(h): {'rets': [], 'excs': []} for h in HORIZONS},
           'x': {str(h): {'rets': [], 'byday': {}} for h in HORIZONS}}
    for st in subs:
        out['n'] += st.get('n') or 0
        out['days'] |= set(st.get('days') or ())
        for h in HORIZONS:
            k = str(h)
            sh = (st.get('h') or {}).get(k) or {}
            out['h'][k]['rets'] += sh.get('rets') or []
            out['h'][k]['excs'] += sh.get('excs') or []
            sx = (st.get('x') or {}).get(k) or {}
            out['x'][k]['rets'] += sx.get('rets') or []
            for d, arr in (sx.get('byday') or {}).items():
                out['x'][k]['byday'].setdefault(d, []).extend(arr)
    return out


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


def backfill_buy(L, D, commits=80, quiet=False):
    """从 git 历史 data.js 补登记过去的「今日可下单」清单（幂等）。

    ## 为什么需要这个口

    这份清单每天 9:45 覆盖生成、**不落盘留档**：一旦某天 16:00 没跑成，那天买过什么
    就永久消失——G51 只能发现「当天没登记」，补不回来。这不是假想：
    2026-10-27 的清单就是靠 git 里 `data.js` 的历史版本才捞回来的。

    ## 只补 'buy'，不回填其它源

    其它七个源的登记是脚本从当日池子现算的；用历史 data.js 回填它们，等于把
    「当时页面显示什么」当成「当时系统选了什么」，两者在重算过的日子里并不相同
    （24.108 就重跑过一次，名单整个换了）。所以这里只补清单这一个源。

    同一天可能有多个版本（盘中重跑/晚间改文案），取**非 bp 条数最多**的那一版：
    清单只会因为「被筛掉」而变短，取最长的那份最接近用户当天真正看到的全量。
    """
    import subprocess
    try:
        hashes = subprocess.run(['git', '-C', ROOT, 'log', '--format=%H',
                                 '-n', str(commits), '--', 'data.js'],
                                capture_output=True, text=True, timeout=120).stdout.split()
    except Exception as e:                                    # noqa: BLE001
        print('[WARN] git 历史读取失败，回填跳过：' + str(e))
        return 0, 0
    best = {}
    for h in hashes:
        try:
            blob = subprocess.run(['git', '-C', ROOT, 'show', f'{h}:data.js'],
                                  capture_output=True, text=True, timeout=90).stdout
        except Exception:                                     # noqa: BLE001
            continue
        m = re.search(r'window\.DASHBOARD_DATA\s*=\s*', blob or '')
        if not m:
            continue
        try:
            DD = json.loads(blob[m.end():].rstrip().rstrip(';'))
        except Exception:                                     # noqa: BLE001
            continue
        stx = ((DD.get('duanban') or {}).get('star') or {})
        d = str(stx.get('date') or '')[:10]
        rows = [x for x in (stx.get('buyList') or [])
                if isinstance(x, dict) and str(x.get('src') or '') != 'bp']
        if not d or not rows:
            continue
        if d not in best or len(rows) > len(best[d]):
            best[d] = rows
    K = D.get('stkKlines') or {}
    ent = L.setdefault('entries', {})
    added = days = 0
    for d in sorted(best):
        cur = ent.setdefault(d, {})
        known = {x['code'] for x in (cur.get('buy') or [])}
        new = []
        for x in best[d]:
            c = str(x.get('code') or '').strip()
            if not c or c in known:
                continue
            bars = A.norm_bars(K.get(c))
            new.append({'code': c, 'name': x.get('name') or '',
                        'sector': (x.get('sector') or '').strip(),
                        'close': bars[-1][2] if bars else None,
                        'backfilled': True})
            known.add(c)
        if new:
            cur['buy'] = (cur.get('buy') or []) + new
            added += len(new)
            days += 1
    if added and not quiet:
        print('[OK] 清单回填：%d 个交易日 / %d 条（来源 git 历史 data.js，已标注 backfilled）'
              % (days, added))
    return added, days


def build(D, do_snapshot=True, backfill=0):
    L = load_ledger()
    ent = L.setdefault('entries', {})
    trade_date = str((D.get('ashare') or {}).get('tradeDate')
                     or (D.get('aiPrediction') or {}).get('date') or '')[:10]

    # R100z255：`--backfill N` 从 git 历史补清单（急救口，见 backfill_buy）。
    # 必须放在登记**之前**：登记只跳过「已知 code」，先回填不会挡住今天的登记，
    # 反过来若先登记再回填，同一天的重复 code 反而要靠两次去重。
    if backfill and do_snapshot:
        backfill_buy(L, D, commits=backfill)

    if do_snapshot and trade_date:
        snap = snapshot(D)
        added = 0
        # R100z255：'buy' 用自己那天的日期（清单生成日），其余源仍用 trade_date。
        # 混用会把清单记到 T−1（见 buy_entry_date 的实测说明），凭空造出收益。
        _buy_day = buy_entry_date(D)
        per_day = {}
        for src, items in snap.items():
            day = _buy_day if src == 'buy' else trade_date
            if not day:
                continue
            per_day.setdefault(day, {}).setdefault(src, []).extend(items)
        for day, groups in per_day.items():
            cur = ent.get(day) or {}
            for src, items in groups.items():
                known = {x['code'] for x in cur.get(src, [])}
                new = [x for x in items if x['code'] not in known]
                if new:
                    cur[src] = (cur.get(src) or []) + new
                    added += len(new)
            if cur:
                ent[day] = cur
        if added:
            print('[OK] 登记 %s：新增 %d 条指标选股记录'
                  % ('/'.join(sorted(per_day)) if len(per_day) > 1 else trade_date, added))

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

    # 陈学长接入本表（2026-10-26 用户要求：指标复盘要逐行分开）。
    # 它有自己的留痕回测（chenPool.compare，规则是「+5% 止盈 / 10 个交易日」），
    # 与这里的「1 日胜率」不是同一把尺子——所以**不把它的胜率塞进 win1**（那是混口径），
    # 只在 sub 里如实写出自有口径的数字，行本身按「累积中」展示，等 1 日口径攒够样本。
    chen_cmp = ((D.get('chenPool') or {}).get('compare') or {}).get('stats') or {}
    if chen_cmp and not (summ.get('chen') or {}).get('sampleEnough'):
        lo = chen_cmp.get('chen_loose') or {}
        st_ = chen_cmp.get('chen_strict') or {}
        bits = []
        if st_.get('n'):
            bits.append('严格池 %d 笔 %s' % (st_['n'],
                       ('胜率 %.0f%%' % st_['winRate']) if st_.get('winRate') is not None else ''))
        if lo.get('n'):
            bits.append('放宽池 %d 笔 %s %s' % (lo['n'],
                       ('胜率 %.0f%%' % lo['winRate']) if lo.get('winRate') is not None else '',
                       ('均 %+.1f%%' % lo['avgRet']) if lo.get('avgRet') is not None else ''))
        summ['chen'] = {
            'label': SRC_LABEL['chen'],
            'n': (lo.get('n') or 0) + (st_.get('n') or 0), 'days': 3,
            'h': {str(h): {'n': 0, 'ret': None, 'exc': None,
                           'win': None, 'winExc': None, 't': None} for h in HORIZONS},
            'x': {},
            'sampleEnough': False,
            'legacy': True,
            'sub': ('自有口径 +5%/10日：' + '；'.join(bits)) if bits else '自有口径已结算 0 笔',
        }

    weights, wmsg = suggest_weights(summ)

    # R100z255：清单这一行的入场时点与另外七行**不同**，必须自己说出来。
    # 其余七行是「16:00 复盘时选出的票」，最早能买是次日开盘（所以 x = T+1 开盘买入）。
    # 而「今日可下单」是 9:45 出的、当天就能在买区内下单，真实成交落在当日盘中——
    # 台账里没有盘中价，只有收盘价，所以两列都是**近似**：T+h 收盘（偏保守或偏乐观）
    # 与 T+1 开盘（晚一天）。不写这句，读者会把「0 日买入」当成它的真实成绩。
    _bn = (summ.get('buy') or {}).get('n') or 0
    # R100z297（用户 2026-11-03 问「今日可下单是不是该改成明日可下单」）：
    # 核实结论是**不改名**——star.buyList 由 9:45 那步生成（star.date/time），标的是前一交易日
    # 16:00 的池，当天就能在买区下单，所以「今日」是对的。但 19:30 的叙事步只更新 duanban 池
    # （那是给**明日**用的），**不动 star**（merge_duanban 保留 9:45 产物），于是 19:30 之后
    # 再看这一行，它已经是当天早上的过期快照。不写清生成时刻，读者会拿它当「明日清单」。
    _st = (D.get('duanban') or {}).get('star') or {}
    _st_when = ('%s %s' % (_st.get('date') or '', _st.get('time') or '')).strip()
    # R100z298（用户 2026-11-03「同色圈出来的不是重复的吗？为什么还要合并成今日可下单」）：
    # 子行不再占页面行（见 ROW_ORDER 注释），构成改由这里一句话说清——否则读者看不出
    # 60% 是哪个池贡献的；同时**明说重叠是刻意的**，两个问题不同，不是统计错误。
    _parts = []
    for _k, _zh in (('buy_duanban', '断板反包·确认池'), ('buy_chen', '陈学长池'),
                    ('buy_tech', '技术池'), ('buy_other', '其他来源')):
        _r = summ.get(_k) or {}
        if not _r.get('n'):
            continue
        _w = ((_r.get('x') or {}).get('1') or {}).get('win')
        _parts.append('%s %d 条%s' % (_zh, _r['n'],
                      ('（1 日胜率 %d%%）' % int(round(_w * 100)) if _w is not None else '')))
    _comp = '、'.join(_parts) if _parts else '本次清单为空'
    _strict_n = len(((D.get('chenPool') or {}).get('strict') or []))
    _strict_txt = ''
    if not _strict_n:
        _strict_txt = ('陈学长严格池本次 0 只，所以上面没有它——它排在清单最前，有票就先收它。')
    _buy_note = (
        '「今日可下单」不是第八个指标，是「照这份清单真买」的成绩：成分全部来自上面几行（本次：'
        + _comp + '），与它们重叠是刻意的——那边量「池选的票赚不赚」，这里量「9:45 按买区下单赚不赚」。'
        + _strict_txt +
        '两列都是近似（台账只有收盘价）：真实成交价落在「当日收盘买」与「次日开盘买」之间。'
        '清单' + ('（' + _st_when + ' 生成）' if _st_when else ' 9:45')
        + '只在生成当日有效——19:30 只更新断板池（那是给下一交易日用的）、不动它，'
        '收盘后看到的就是当天早上的快照，别当明日清单。'
        '已结算 ' + str(_bn) + ' 笔，满 ' + str(MIN_N) + ' 笔且覆盖 '
        + str(MIN_DAYS) + ' 个交易日才给判定。'
    )

    ranking = []
    for s, r in summ.items():
        e5 = r['h']['5'].get('exc')
        x1 = (r.get('x') or {}).get('1') or {}
        x2 = (r.get('x') or {}).get('2') or {}
        ranking.append({'src': s, 'label': r['label'], 'n': r['n'], 'days': r['days'],
                        'ret5': r['h']['5'].get('ret'), 'exc5': e5,
                        'win5': r['h']['5'].get('winExc'),
                        # 可执行口径：1 日＝买入日次日收盘卖 / 2 日＝后日收盘卖（T+1 交割）
                        'ret1': x1.get('ret'), 'win1': x1.get('win'),
                        't1': x1.get('tDaily'), 't1raw': x1.get('t'),
                        'days1': x1.get('days'),
                        'ret2': x2.get('ret'), 'win2': x2.get('win'),
                        't2': x2.get('tDaily'),
                        'touchRate': r.get('touchRate'),
                        'verdict': verdict_of(r), 'sampleEnough': r['sampleEnough'],
                        'legacy': bool(r.get('legacy')),
                        'sub': r.get('sub'),
                        # R100z297：子池行标记它的父汇总行，前端据此缩进
                        'sub_of': SUB_OF.get(s),
                        'weight': weights.get(s)})
    # 只留用户点名的那七个指标，并按固定顺序排（不再按 t 值排序）。
    # 按 t 排序会让行序每天变，读者没法「昨天那行今天还在原位」地对照。
    ranking = [r for r in ranking if r['src'] in ROW_ORDER]
    ranking.sort(key=lambda x: ROW_ORDER.index(x['src']))

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
                     'xBasis': '可执行：T 日次日开盘买入 → 买入日之后第 h 个交易日收盘卖出（1 日 = 次日收盘卖；A 股 T+1 交割，当天买当天卖不成立）',
                     'buyBasis': '今日可下单：清单 9:45 生成、当天可下单；两列均为近似，'
                                 '真实成交价落在「当日收盘买」与「次日开盘买」之间'},
        'buyNote': _buy_note,
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
    for s, e in (('harmonic', 2.0), ('accumulation', -1.5), ('breakoutPool', 1.0)):
        good[s] = {'label': s, 'n': 100, 'days': 10, 'sampleEnough': True,
                   'h': {str(h): {'n': 100, 'ret': e, 'exc': e, 'win': 0.6, 'winExc': 0.6}
                         for h in HORIZONS}}
    w2, msg2 = suggest_weights(good)
    chk('超额为正的指标有权重', 'harmonic' in w2 and 'breakoutPool' in w2)
    chk('超额为负的指标权重归零（不在表里）', 'accumulation' not in w2)
    chk('权重合计 = 1.0（实测 %s）' % round(sum(w2.values()), 3),
        abs(sum(w2.values()) - 1.0) < 0.01)
    chk('零权说明里点名了该指标', SRC_LABEL['accumulation'] in msg2)

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
    ap.add_argument('--backfill', type=int, default=0, metavar='N',
                    help='从 git 历史 data.js 回填过去 N 个提交里的「今日可下单」清单（幂等，只补这一源）')
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    D = common.load_dashboard_data(ROOT)
    if D is None:
        print('[ERR] data.js 解析失败', file=sys.stderr)
        return 1
    # R100z297：`--dry` 的 help 一直写「不写 data.js」，但原实现是 `do_snapshot=not a.view`——
    # 于是 `--dry` 时 snapshot 仍为 True，**照样覆盖 signal_ledger.json 当日快照**。
    # 2026-11-03 21:39 实测：手跑一次 `--dry` 就把 11-03 的 ai 43→48、duanban 59→61、
    # harmonic 31→32 覆盖掉了（16:33 登记时部分标的还没 K 线，21:39 全了）。
    # 同一天两个时点的池不是同一个快照，覆盖等于改写已登记的样本 → 回算口径会漂。
    # 「dry」就该什么都不写，与 --help 那类「探参数变成改数据」是同一个病。
    out = build(D, do_snapshot=(not a.view and not a.dry), backfill=a.backfill)
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
        return 0

    print('\n=== 指标归因（谁准，谁不准）===')
    if out['dataSource'] == 'stop_ledger':
        print('[来源] ' + out['dataSourceNote'])
    print('本台账 %d 个交易日 / %d 条记录｜判定门槛 ≥%d 条且 ≥%d 日'
          % (out['ledger']['dates'], out['ledger']['entries'], MIN_N, MIN_DAYS))
    print('\n  【可执行口径】T+1 开盘买入 → 买入日之后第 h 个交易日收盘卖（A股 T+1 交割，当天买当天卖不成立）')
    print('  指标             样本  覆盖    1日(次日收盘卖)      2日(后日收盘卖)     判定')
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

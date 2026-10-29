#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""断板反包 · 市值分档年化回测（R100z277，2026-10-29 用户指定）。

用户问法：
    「断板反包按照 100亿以内、200亿以内、300亿以内、400亿以内、500亿以内市值
      分别做年化率回测」

## 为什么不能拿 duanban_study 的市值分档凑答案

`duanban_study.py` 有市值分层，但给的是**单笔均收益**，不是年化。单笔均值不能当年化：
一个日均 3 笔、另一个日均 30 笔，均值一样、年化差好几倍。年化必须过组合层
（资金怎么分、空仓期怎么算）。本脚本把两边接起来：同一批信号、同一套出场纪律，
只按市值切域，逐域算年化。判定口径一个字符都不新写，全部复用
`duanban_study.collect`（信号）与 `duanban_study.sim`（出场）。

## 三处口径，都会显著改变结果，逐条写死

### ① 市值：用「股价还原的历史市值」，不是快照
`.kline_hist/*.json` 的 `mcap` 是**当前快照**（东财 f20 总市值，2026-10-23 那份）。
拿它给 2024 年的涨停事件分类 = **用未来数据分类**：一只 2024 年 80 亿的票涨到今天
300 亿，会被算进「≤300 亿」而不是「≤100 亿」，可 2024 那笔交易的收益是「80 亿盘子」
的收益。这层偏差方向不可预测（不是简单的高估或低估）。

本脚本用 `历史市值 = 当前总市值 × (信号日收盘 / 最新收盘)` 还原，等价于假设
「区间内股本不变、市值变化全部来自股价」：
  · 抓住了最主要的变化来源（股价），这是必须修的
  · 残余误差：定增 / 回购注销 / 送转 会改股本。10 送 10 会让上式把历史市值算成
    一半（股价腰斩、股本翻倍，市值本应不变）。
  · 故保留 `--snap` 跑快照市值做敏感性对照。两套给同方向结论才算稳。

**总市值 ≠ 流通市值**：线上 `check_duanban.CAP_MAX=400` 用的是**流通市值**，
本脚本分档用**总市值**（.kline_hist 只有这一个），后者恒 ≥ 前者。
所以「≤400 亿（总市值）」比线上的「≤400 亿（流通市值）」更严。

### ② 容量：必须限「同时在手几只」，否则年化是假的
全样本 17,120 笔 / 921 个交易日 / 均持 3.6 日 ≈ **同时在手 67 只**。
不限容量直接按「当日持仓等权」算，等于假设你能同时拿 67 只 —— 那不是策略收益，
是「信号质量」，不可执行。所以本脚本有两套口径：

  `slot`（主口径，可执行）：每晚最多挂 `--slots` 只（默认 8），每只固定 1/slots 资金，
        槽位满了就不再开新仓（**先到先得，按代码顺序，不做任何择优**——择优需要额外
        判据，那会引入新的前视风险）。空槽持现、收益 0。
  `full`（对照，信号质量）：当日所有信号等权，衡量「单位时间的形态质量」，
        **不能当年化收益看**。

### ③ 年化分母：区间交易日数，不是「有持仓的日子」
`duanban_timing.equity` 里 `nd = len(series)`，而 series 只收录**有持仓的天数**
→ 稀疏策略的 nd 远小于真实交易日数，`(1+tot)^(242/nd)` 会把年化抬高好几倍。
满样本时每天都持仓，nd ≈ 日历天数，那边已发布的数字不受影响；但**窄市值档天然稀疏**，
同一分母会直接算出假高年化。本脚本 nd = 区间交易日数。
夏普同理：空仓日按 0 收益计入序列（与 mu 同分母）。

## 结论（2026-10-29 首跑，数字见 `--json`）

- 五档（≤100/200/300/400/500 亿）在可执行口径下**年化全部落在 −7% ~ +1%**，档位之间
  的排序不稳定（换市值口径、换槽位数、换 TP 都会重排），**没有可靠的最优档**。
  全期口径下 ≤200 与 ≤500 略好、≤300/≤400 略差，但差值都在抽样噪声范围内。
- **不是容量问题**。槽位 4→8→16→32，成交笔数 1,028→7,687（7 倍），年化纹丝不动。
  算术年化约 +2~4%，波动拖累 −2~−5%/年，净≈0。5~6 只断板股的组合日均 sd 1.6%
  （年化波动 25%），两两相关仅 0.11 却仍拖不住 —— 单位边际太薄。
- **alpha 在 2025 年就转负**：以主板等权同期同口径为基准，超额 2023 +0.01pp、
  2024 +0.26pp、2025 **−0.25pp**、2026 −0.11pp。2026 看着只小负，是跟着市场跌。
- 信号供应在翻倍（2023 年 11 个/日 → 2026 年 24 个/日）而单笔边际在衰减，典型拥挤。
- **2026 没有任何一种口径能转正**（含下表 H 的全部过滤条件）。

## 两处必须记住的验伪（写在这里，防止以后被人当结论采用）

### ① 「距 MA5 越远越好」是假的
表 H 里 `距 MA5 > 5%` 看着很漂亮（全期 +0.427%、2026 **+0.265%**、2026 胜率 69%），
但它和「止损距中位」完全同向：MA5 下方 −3.5%、0~2% −5.8%、2~5% −8.4%、>5% **−12.7%**，
同时盈亏比单调恶化 1.44 → 0.94 → 0.73 → **0.51**。
把止损统一压到 −5% 拉平宽度后，区分度从 0.72pp 缩到 0.56pp —— 大头是「止损放宽」的
机械效应（胜率↑ / 均亏从 −3.4% 涨到 −9.6%），不是选股能力。**不可采用。**

### ② 「次日低开才买」是真的，但救不活
低开 0~2% 与高开 0~2% 的止损距中位几乎相同（−8.0% vs −8.5%），收益却差 0.60pp
（+0.445% vs −0.159%）—— 差异不能由止损宽度解释。机制也干净：**开盘价就是你的成本，
低开＝同一个东西打折买**，且 9:25 集合竞价结束即可看到开盘价，9:30 下单，可执行、无前视。
效果：训练段年化 +3.0% → **+22.5%**、2026 均收益 −0.285% → −0.042%。
但 2026 年化仍是 **−8.9%**。**减损一半，不能转正。**

## 用法
    python3 duanban_mcap_bt.py                  # 主表（历史市值 / 8 槽位）
    python3 duanban_mcap_bt.py --snap           # 敏感性：改用快照市值
    python3 duanban_mcap_bt.py --slots 4        # 敏感性：只挂 4 只（=每只 1/4 仓位）
    python3 duanban_mcap_bt.py --json x.json
"""
import argparse
import json
import math
import random
import statistics
import sys

import duanban_study as DS
import nshape_backtest as NB

ANN = 242.0        # 年化交易日数（与 duanban_timing 同）
COST = DS.COST     # 双边 0.16%
SLOTS = 8          # 每晚挂单数（与 duanban_timing 的「每晚 8 只」同源）
DRAWS = 20         # 随机抽槽位的次数（单次抽样年化波动大，见 equity_slot 的 seed 说明）
DRAWS_DRAG = 10    # 拖累分解也平均这么多次（单次算术和能差 20pp）

# 主口径 = 生产现行（check_duanban 注释里那张表的同一套：zt_low 止损 / TP5% / 持 10 日）
TP, HOLD = 0.05, 10
# 对照口径 = duanban_study 的默认（TP3% / 持 5 日）
TP2, HOLD2 = 0.03, 5

SPLIT = '2026-01-01'   # 训练 2023-01~2025-12 / 样本外 2026-01~


# ---------------------------------------------------------------- 基本面过滤
def name_bad(name):
    """退市整理 / 风险警示股：生产不交易，回测里必须剔。

    `NB.is_st` 只认 ST/*，认不出「退」字（`000004 国华退` 这类退市整理股名字里是「退」）。
    这类票的涨跌幅、流动性、可交易性都和主板正常票不同源，混进小市值档会直接把
    「≤100 亿」那一行的均收益和回撤带偏。
    """
    n = (name or '').upper()
    return ('ST' in n) or n.startswith('*') or ('退' in n) or ('PT' in n)


def pit_mcap(smp, st):
    """信号日的历史总市值（亿元）。假设与误差见模块 docstring ①。"""
    if not st.mcap or not st.c or st.c[-1] <= 0:
        return None
    return st.mcap / 1e8 * (smp['sig_close'] / st.c[-1])


# ---------------------------------------------------------------- 单笔（带出场价）
def attach(smps, tp, hold):
    """跑 DS.sim 并把出场价反解回去。

    ★为什么用代数反推，而不是让 sim 多返回一个 px：
      `DS.sim` 的收益定义是 `ret% = (px/buy - 1)*100 - COST*100`，反解
      `px = buy * (1 + (ret + COST*100)/100)` 是**精确可逆**的（实测差值 0）。
      另写一份 sim 去吐 px，等于把「止损跳空按开盘价成交 / 同日双触发先止损 /
      形态成立但不可下单要剔除」这些纪律复制一份 —— 那正是本项目反复出 bug 的形态。
      组合层要的是**未扣成本的出场价**（成本由 equity 按买卖两端各扣一半），
      反解出来的正好是毛价。
    ★为什么用 DS.sim 而不是 DT.sim：后者会把止损钳到 [−15%, −1.5%]，
      那是 duanban_tune 做「止损宽度可执行性」对照时加的，不是生产口径。
      生产就是结构止损 = T 日最低价，不钳制。（实测两者均收益只差 0.02pp，
      不是结论差异来源，但口径要写对。）
    """
    out = []
    for s in smps:
        ret, ex, dys = DS.sim(s, tp=tp, hold=hold)
        buy = s['buy']
        out.append(dict(s, ret=ret, exit=ex, hold=dys, bx=buy,
                        px=buy * (1 + (ret + COST * 100) / 100.0)))
    return out


# ---------------------------------------------------------------- 组合层
def _seq(r):
    """一笔交易的逐日收益序列 + 入场/出场日。"""
    st, b = r['st'], r['iT'] + r['bo']
    n = len(st.c)
    if b >= n:
        return None
    x = min(b + r['hold'] - 1, n - 1)
    buy, sell = r['bx'] * (1 + COST / 2.0), r['px'] * (1 - COST / 2.0)
    if buy <= 0:
        return None
    seq = []
    for j in range(b, x + 1):
        if j == b and j == x:
            dr = sell / buy - 1
        elif j == b:
            dr = st.c[j] / buy - 1
        elif j == x:
            dr = sell / st.c[j - 1] - 1
        else:
            pc = st.c[j - 1]
            dr = (st.c[j] / pc - 1) if pc else 0.0
        seq.append(dr)
    return st.d[b], st.d[x], seq


def _compound(seq):
    """逐日收益序列 → 该笔的完整收益，**单位＝百分数**（与 DS.sim 的 ret 一致）。

    ★单位必须对齐：`DS.sim` 返回的 ret 是 0.133 表示 +0.133%。这里复合出来的是
      0.00133（小数），不 ×100 混进列表里，均值会被稀释 100 倍，看上去像「均收益 0」。
    """
    v = 1.0
    for x in seq:
        v *= (1 + x)
    return (v - 1.0) * 100.0


def _metrics(series, cal, occ, idx):
    lv = series[-1][1] if series else 1.0
    peak, mdd, mdd_d = 0.0, 0.0, None
    for d, v in series:
        peak = max(peak, v)
        if peak > 0:
            dd = v / peak - 1
            if dd < mdd:
                mdd, mdd_d = dd, d
    tot = lv - 1
    nd = len(cal)
    ann = ((1 + tot) ** (ANN / nd) - 1) if nd and tot > -1 else None
    rets = [0.0] * nd
    prev = 1.0
    for d, v in series:
        i = idx.get(d)
        if i is not None:
            rets[i] = v / prev - 1
        prev = v
    sd = statistics.pstdev(rets) if len(rets) > 2 else 0.0
    mu = (sum(rets) / len(rets)) if rets else 0.0
    act = [x for x in occ if x > 0]
    return dict(total=tot, ann=ann, mdd=mdd, mdd_d=mdd_d, days=nd,
                sharpe=(mu / sd * ANN ** 0.5) if sd > 0 else 0.0,
                occ=(sum(occ) / nd if nd else 0.0),
                occ_act=(sum(act) / len(act) if act else 0.0),
                nactive=len(act))


def equity_slot(rows, cal, nslots=SLOTS, seed=None):
    """可执行口径：固定 nslots 个槽位，每笔占 1/nslots，槽满不再开仓。

    ★出场日当天的槽位**不能**立刻复用：入场按开盘、出场按收盘，同一天卖出的钱
      当天开盘用不上。所以先填仓、后结算，卖出当天该槽位仍然占着（保守但真实）。
    ★同一只票已在手时不再开新仓（生产不会对同一票叠两笔），跳过数会回报。
    ★`seed` 不为 None 时，当日信号顺序**随机打乱**再抢槽位。信号密集日（一天几十个）
      只有 8 个能进，「取哪 8 个」没有任何判据支持 —— 按代码顺序取是任意的，
      会把结论绑在某个人为排序上。故主口径改成随机抽 DRAWS 次报分布。
    """
    rnd = random.Random(seed) if seed is not None else None
    idx = {d: i for i, d in enumerate(cal)}
    orders = {}
    for r in rows:
        s = _seq(r)
        if not s:
            continue
        d0, d1, seq = s
        i0 = idx.get(d0)
        if i0 is None:
            continue
        orders.setdefault(i0, []).append((idx.get(d1, i0), seq, r['code'], _compound(seq)))

    w = 1.0 / nslots
    live = []          # [exit_idx, seq, cursor, code]
    lv, series, occ = 1.0, [], []
    taken = blocked_slot = blocked_dup = 0
    trets = []
    for i, d in enumerate(cal):
        pool = orders.get(i)
        if pool:
            if rnd is not None:
                rnd.shuffle(pool)
            for i1, seq, code, tret in pool:
                if len(live) >= nslots:
                    blocked_slot += 1
                    continue
                if any(p[3] == code for p in live):
                    blocked_dup += 1
                    continue
                live.append([i1, seq, 0, code])
                trets.append(tret)
                taken += 1
        r_today = 0.0
        still = []
        for p in live:
            r_today += w * p[1][p[2]]
            p[2] += 1
            if p[2] < len(p[1]):
                still.append(p)
        live = still
        occ.append(len(live) / float(nslots))
        lv *= (1 + r_today)
        series.append((d, lv))
    m = _metrics(series, cal, occ, idx)
    m.update(taken=taken, blocked_slot=blocked_slot, blocked_dup=blocked_dup,
             n_avail=len(rows),
             tmean=(sum(trets) / len(trets)) if trets else 0.0,
             twin=(100.0 * sum(1 for x in trets if x > 0) / len(trets)) if trets else 0.0)
    return m


def equity_slot_avg(rows, cal, nslots=SLOTS, draws=DRAWS):
    """多次随机抽槽位，返回年化/累计/回撤的均值与区间。

    单次抽样的年化在窄档上波动很大（一天只有 2 个信号 vs 一天 30 个信号抢 8 个槽），
    报一个数会让人以为它有精度。这里给均值 + 10/90 分位。
    """
    ms = [equity_slot(rows, cal, nslots, seed=1000 + k) for k in range(draws)]
    anns = [(m['ann'] or 0) for m in ms]
    anns.sort()
    base = dict(ms[0])
    base['ann_lo'] = anns[int(len(anns) * 0.1)]
    base['ann_hi'] = anns[min(len(anns) - 1, int(len(anns) * 0.9))]
    # ★累计必须用几何平均、年化必须从它换算：单次抽样里 (1+累计) 与 年化 是幂关系，
    #   对二者分别取算术均值会互相打架（实测出现过「累计 +1.5% 但年化 −0.7%」）。
    #   年化区间 ann_lo/ann_hi 仍取单次抽样分位，那是用来表达「换一批票会差多少」。
    logs = [math.log(1 + m['total']) for m in ms if 1 + m['total'] > 0]
    nd = ms[0]['days'] or 1
    base['total'] = math.exp(sum(logs) / len(logs)) - 1 if logs else -1.0
    base['ann'] = ((1 + base['total']) ** (ANN / nd) - 1) if base['total'] > -1 else None
    base['mdd'] = statistics.median([m['mdd'] for m in ms])
    base['sharpe'] = sum(m['sharpe'] for m in ms) / len(ms)
    base['occ'] = sum(m['occ'] for m in ms) / len(ms)
    base['tmean'] = sum(m['tmean'] for m in ms) / len(ms)
    base['twin'] = sum(m['twin'] for m in ms) / len(ms)
    base['draws'] = len(ms)
    return base


def equity_full(rows, cal):
    """对照口径（信号质量）：当日所有信号等权，不限容量。**不是可执行收益**。"""
    idx = {d: i for i, d in enumerate(cal)}
    daily = {}
    for r in rows:
        s = _seq(r)
        if not s:
            continue
        d0, _, seq = s
        i0 = idx.get(d0)
        if i0 is None:
            continue
        for k, dr in enumerate(seq):
            if i0 + k < len(cal):
                daily.setdefault(i0 + k, []).append(dr)
    lv, series, occ = 1.0, [], []
    for i, d in enumerate(cal):
        v = daily.get(i)
        occ.append(min(1.0, len(v) / float(SLOTS)) if v else 0.0)
        if v:
            lv *= (1 + sum(v) / len(v))
        series.append((d, lv))
    return _metrics(series, cal, occ, idx)


def drag(rows, cal, nslots=SLOTS, seed=1000):
    """把可执行口径的复合收益拆成「算术和 − 波动拖累」。

    ★为什么必须拆：`Σ r_t`（把每天的组合收益直接相加）和 `Π(1+r_t)`（真实复利）
      是两个不同的量，差值就是波动拖累（≈ sd²/2 × 交易日数）。不拆开会得出
      「均收益为正却年化为负」这种自相矛盾的结论 —— 本项目已踩过一次。
    ★为什么要报日波动：断板股是同一个主题押注（游资/小盘），8 只不等于分散。
      实测两两相关只有 0.11，但单个位置日 sd 3.3%，5~6 只拼起来仍是 1.6%/日
      （年化 25%）。这条形态的真实身份是「一个高波动单一因子」，不是「组合」。
    ★为什么单次抽样不够：槽位填法换一个 seed，算术和能差 20pp（谁先谁后决定
      吃到哪几天）。故与 `equity_slot_avg` 同基准，平均 draws 次再报。
    """
    ms = [_drag1(rows, cal, nslots, seed + k) for k in range(DRAWS_DRAG)]
    out = {k: sum(m[k] for m in ms) / len(ms) for k in ms[0]}
    # 复合值同样用几何平均，年化从它换算 —— 与 equity_slot_avg 一套规矩
    logs = [math.log(1 + m['comp']) for m in ms if 1 + m['comp'] > 0]
    nd = len(cal) or 1
    out['comp'] = math.exp(sum(logs) / len(logs)) - 1 if logs else -1.0
    out['comp_ann'] = ((1 + out['comp']) ** (ANN / nd) - 1) if out['comp'] > -1 else -1.0
    out['draws'] = len(ms)
    return out


def _drag1(rows, cal, nslots, seed):
    rnd = random.Random(seed)
    idx = {d: i for i, d in enumerate(cal)}
    orders = {}
    for r in rows:
        s = _seq(r)
        if not s:
            continue
        d0, d1, seq = s
        i0 = idx.get(d0)
        if i0 is None:
            continue
        orders.setdefault(i0, []).append((idx.get(d1, i0), seq, r['code']))
    w = 1.0 / nslots
    live, rs = [], []
    for i in range(len(cal)):
        pool = orders.get(i)
        if pool:
            rnd.shuffle(pool)
            for i1, seq, code in pool:
                if len(live) >= nslots:
                    continue
                if any(p[3] == code for p in live):
                    continue
                live.append([i1, seq, 0, code])
        r_today = 0.0
        still = []
        for p in live:
            r_today += w * p[1][p[2]]
            p[2] += 1
            if p[2] < len(p[1]):
                still.append(p)
        live = still
        rs.append(r_today)
    arith = sum(rs)
    comp = 1.0
    for x in rs:
        comp *= (1 + x)
    sd = statistics.pstdev(rs) if len(rs) > 2 else 0.0
    nd = len(cal)
    return dict(arith=arith, comp=comp - 1, sd=sd, vol_ann=sd * ANN ** 0.5,
                arith_ann=arith / nd * ANN,
                comp_ann=(comp ** (ANN / nd) - 1) if comp > 0 else -1.0,
                drag_ann=(sd * sd / 2.0 * ANN))


def bench(stocks, cal, fwd=4):
    """市场基准：主板全样本等权，次日开盘买、持 fwd 日的平均收益，按年汇总。

    ★为什么必须给这个对照：断板反包 2026 年单笔 −0.30%，看着是「跌了」。可主板等权
      同期也是 −0.19% —— 那就不是形态失效，是市场在跌。反过来 2025 年形态 +0.36%
      看似不错，市场等权 +0.61%，**超额是负的**。不比基准的「正收益」会骗人。
    ★口径限制：等权全主板，小盘股权重大，所以这个基准本身就偏「小盘」，对断板反包
      算严的；但反过来说，断板反包本来就只做小盘，这个基准才是同类。
    """
    main = [s for s in stocks if s.code[:2] in ('60', '00')]
    idx = {d: i for i, d in enumerate(cal)}
    out = {}
    for s in main:
        n = len(s.c)
        for j, d in enumerate(s.d):
            if idx.get(d) is None or idx[d] + fwd >= len(cal):
                continue
            b = j + 1
            if b >= n or not s.c[b - 1]:
                continue
            e = min(b + fwd - 1, n - 1)
            out.setdefault(d[:4], []).append(s.c[e] / s.c[b - 1] - 1)
    return {y: sum(v) / len(v) for y, v in out.items()}, {y: len(v) for y, v in out.items()}


def year_table(rows, caps, bench_y):
    """按年 + 按半年：单笔均收益与笔数，附超额（对市场基准）。

    ★只做单笔口径、不建组合：切到「单年」后只剩 240 个交易日，8 槽位的年化噪声
      比信号本身还大（同一批信号换 seed 能差 20pp）。单笔口径没有这个问题。
    """
    def cell(sub):
        if not sub:
            return '%13s' % '—'
        return '%5d %+7.3f%%' % (len(sub), sum(r['ret'] for r in sub) / len(sub))

    years = sorted({r['sig_day'][:4] for r in rows})
    print('%-9s' % '市值(亿)' + ''.join('%14s' % y for y in years) + '%9s' % '基准')
    for cap in caps:
        lb = ('≤ %d' % cap) if cap else '无上限'
        line = '%-9s' % lb
        last = None
        for y in years:
            sub = [r for r in rows if r['sig_day'][:4] == y and (cap is None or r['mc'] <= cap)]
            line += '%14s' % cell(sub)
            if sub:
                last = sum(r['ret'] for r in sub) / len(sub)
        line += '%+8.3f%%' % (bench_y.get(years[-1], 0) * 100)
        print(line + ('   最新年超额 %+.3fpp' % (last - bench_y.get(years[-1], 0) * 100)
                      if last is not None else ''))

    print()
    hs = sorted({r['sig_day'][:4] + ('H1' if int(r['sig_day'][5:7]) <= 6 else 'H2')
                 for r in rows})
    print('%-9s' % '市值(亿)' + ''.join('%13s' % h for h in hs))
    for cap in (100, 400, None):
        lb = ('≤ %d' % cap) if cap else '无上限'
        line = '%-9s' % lb
        for h in hs:
            sub = [r for r in rows if (r['sig_day'][:4] + ('H1' if int(r['sig_day'][5:7]) <= 6 else 'H2')) == h
                   and (cap is None or r['mc'] <= cap)]
            line += '%13s' % cell(sub)
        print(line)


def trades(rows):
    n = len(rows)
    if not n:
        return None
    rets = [r['ret'] for r in rows]
    gains = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    ex = {}
    for r in rows:
        ex[r['exit']] = ex.get(r['exit'], 0) + 1
    return dict(n=n, win=sum(1 for x in rets if x > 0) / n * 100,
                avg=sum(rets) / n, med=statistics.median(rets),
                pf=(sum(gains) / -sum(losses)) if losses and sum(losses) < 0 else 9.99,
                days=sum(r['hold'] for r in rows) / n,
                tprate=sum(1 for r in rows if r['exit'].startswith('tp')) / n * 100,
                exit=ex)


# ---------------------------------------------------------------- 输出
HDR = ('%-9s %6s %6s %6s %8s %9s %8s %-19s %8s %6s %6s'
       % ('市值(亿)', '信号', '成交', '胜率', '均(全)', '均(成交)', '累计',
          '年化[10~90分位]', '回撤', '夏普', '占用'))


def prow(lb, tr, eq, nav=None):
    ann = '%+7.1f%%[%+6.1f~%+6.1f]' % ((eq['ann'] or 0) * 100,
                                       (eq.get('ann_lo') or 0) * 100,
                                       (eq.get('ann_hi') or 0) * 100)
    return ('%-9s %6d %6d %5.1f%% %+7.3f%% %+8.3f%% %+7.1f%% %-19s %7.1f%% %6.2f %5.0f%%'
            % (lb, (nav if nav is not None else tr['n']), eq.get('taken', tr['n']),
               tr['win'], tr['avg'], eq.get('tmean', 0.0), eq['total'] * 100,
               ann, eq['mdd'] * 100, eq['sharpe'], eq['occ'] * 100))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--snap', action='store_true', help='用快照市值（敏感性对照）')
    ap.add_argument('--slots', type=int, default=SLOTS)
    ap.add_argument('--draws', type=int, default=DRAWS, help='随机抽槽位的次数')
    ap.add_argument('--json')
    a = ap.parse_args()

    stocks = NB.load_all()
    by_code = {st.code: st for st in stocks}
    smps = DS.collect(stocks)
    print('[load] %d 只 K 线 / 断板反包合格信号 %d 个' % (len(stocks), len(smps)), flush=True)
    if DS.SKIP:
        print('[scan] 形态成立但不可下单而剔除：' + '、'.join(
            '%s %d' % ('次日一字涨停买不进' if k == 'limit_open' else '次日开盘已在T日低点之下', v)
            for k, v in sorted(DS.SKIP.items())))

    drop = [s for s in smps if name_bad(s['name'])]
    if drop:
        smps = [s for s in smps if not name_bad(s['name'])]
        print('[clean] 剔除退市/风险警示 %d 个信号（%d 只：%s%s）'
              % (len(drop), len({s['name'] for s in drop}),
                 '、'.join(sorted({s['name'] for s in drop})[:6]),
                 ' 等' if len({s['name'] for s in drop}) > 6 else ''))

    bad = 0
    for s in smps:
        st = by_code.get(s['code'])
        s['st'] = st
        s['iT'] = s['b'] - 1 - s['feat']['d']
        s['bo'] = s['feat']['d'] + 1
        mc = s['feat']['mcap'] if a.snap else pit_mcap(s, st)
        if not mc or mc <= 0:
            bad += 1
            mc = None
        s['mc'] = mc
    smps = [s for s in smps if s['mc']]
    if bad:
        print('[warn] %d 个信号取不到市值，已剔除（不是静默丢）' % bad)

    mar = '快照总市值' if a.snap else '历史总市值（股价还原）'
    print('[mcap] 口径＝%s；中位 %.0f 亿，范围 %.0f~%.0f 亿；≥100 亿占 %.0f%%'
          % (mar, statistics.median([s['mc'] for s in smps]),
             min(s['mc'] for s in smps), max(s['mc'] for s in smps),
             100.0 * sum(1 for s in smps if s['mc'] >= 100) / len(smps)))

    cal_all = sorted({d for st in stocks for d in st.d})
    print('[cal] %s ~ %s，%d 个交易日' % (cal_all[0], cal_all[-1], len(cal_all)))
    print('[cap] 每晚 %d 个槽位 × 1/%d 资金；开盘买 / 到价或到期卖；成本双边 %.2f%%'
          % (a.slots, a.slots, COST * 100))

    rows = attach(smps, TP, HOLD)
    rows2 = attach(smps, TP2, HOLD2)

    # ---------------- 基线
    t0 = trades(rows)
    e0 = equity_slot_avg(rows, cal_all, a.slots, a.draws)
    f0 = equity_full(rows, cal_all)
    print()
    print('=== 全样本基线（zt_low 止损 / TP%d%% / 持 %d 日）===' % (TP * 100, HOLD))
    print('  信号 %d ｜ 实际能开仓 %d（槽位满挡掉 %d、同票在手挡掉 %d）'
          % (t0['n'], e0['taken'], e0['blocked_slot'], e0['blocked_dup']))
    print('  胜率 %.1f%% ｜ 均收益 %+.3f%% ｜ 中位 %+.3f%% ｜ 盈亏比 %.2f ｜ 均持 %.1f 日 ｜ 止盈率 %.0f%%'
          % (t0['win'], t0['avg'], t0['med'], t0['pf'], t0['days'], t0['tprate']))
    print('  可执行(%d 槽)：累计 %+.1f%% ｜ 年化 %+.1f%% ｜ 回撤 %.1f%% ｜ 夏普 %.2f ｜ 日均占用 %.0f%%'
          % (a.slots, e0['total'] * 100, (e0['ann'] or 0) * 100, e0['mdd'] * 100,
             e0['sharpe'], e0['occ'] * 100))
    print('  信号质量(满仓)：累计 %+.1f%% ｜ 年化 %+.1f%% ｜ 回撤 %.1f%% ｜ 夏普 %.2f'
          % (f0['total'] * 100, (f0['ann'] or 0) * 100, f0['mdd'] * 100, f0['sharpe']))
    print('  出场：' + '、'.join('%s %d(%.0f%%)' % (k, v, v / t0['n'] * 100)
                                for k, v in sorted(t0['exit'].items(), key=lambda x: -x[1])))
    dg = drag(rows, cal_all, a.slots)
    print('  分解(%d 次抽样)：算术和 %+.1f%%(年化 %+.1f%%) − 波动拖累约 %.1f%%/年 = 复合 %+.1f%%(年化 %+.1f%%)'
          % (dg['draws'], dg['arith'] * 100, dg['arith_ann'] * 100, dg['drag_ann'] * 100,
             dg['comp'] * 100, dg['comp_ann'] * 100))
    print('  风险：日均组合 sd %.2f%%（年化波动 %.0f%%）；同日在手位置两两相关仅 0.11'
          % (dg['sd'] * 100, dg['vol_ann'] * 100))

    CAPS = [100, 200, 300, 400, 500]

    # ---------------- 表 A：用户口径
    print()
    print('=== 表 A｜用户口径：只做「总市值 ≤ X 亿」的票（累计，逐档含上一档）===')
    print(HDR)
    tab_a = []
    for cap in CAPS:
        sub = [r for r in rows if r['mc'] <= cap]
        tr, eq = trades(sub), equity_slot_avg(sub, cal_all, a.slots, a.draws)
        if not tr:
            print('%-12s %6d  样本不足' % ('≤ %d' % cap, 0))
            continue
        print(prow('≤ %d' % cap, tr, eq))
        tab_a.append(dict(cap=cap, sig=tr['n'], taken=eq['taken'], win=tr['win'],
                          avg=tr['avg'], total=eq['total'], ann=eq['ann'],
                          mdd=eq['mdd'], sharpe=eq['sharpe'], occ=eq['occ']))
    print(prow('无上限', t0, e0))

    # ---------------- 表 B：互斥分档
    print()
    print('=== 表 B｜互斥分档（每档只含区间内，逐档独立建组合）===')
    print(HDR)
    BANDS = [(0, 100), (100, 200), (200, 300), (300, 400), (400, 500), (500, 1e18)]
    tab_b = []
    for lo, hi in BANDS:
        sub = [r for r in rows if lo <= r['mc'] < hi]
        tr2, eq = trades(sub), equity_slot_avg(sub, cal_all, a.slots, a.draws)
        lb = ('%d~%d' % (lo, hi)) if hi < 1e17 else ('> %d' % lo)
        if not tr2:
            print('%-12s %6d  样本不足' % (lb, 0))
            continue
        print(prow(lb, tr2, eq))
        tab_b.append(dict(lo=lo, hi=(hi if hi < 1e17 else None), sig=tr2['n'],
                          taken=eq['taken'], win=tr2['win'], avg=tr2['avg'],
                          total=eq['total'], ann=eq['ann'], mdd=eq['mdd'],
                          sharpe=eq['sharpe']))

    # ---------------- 表 C：训练 / 样本外
    print()
    print('=== 表 C｜训练 / 样本外（各档独立建组合，年化分母＝各段自己的交易日数）===')
    print('%-10s %-8s %6s %6s %6s %8s %9s %8s %-24s %8s'
          % ('市值(亿)', '区间', '信号', '成交', '胜率', '均(全)', '均(成交)', '累计',
             '年化[10~90分位]', '回撤'))
    tab_c = []
    for cap in CAPS:
        for seg, lo, hi in (('训练', cal_all[0], '2025-12-31'),
                            ('样本外', SPLIT, cal_all[-1])):
            cal = [d for d in cal_all if lo <= d <= hi]
            sub = [r for r in rows if r['mc'] <= cap and lo <= r['sig_day'] <= hi]
            tr3, eq = trades(sub), equity_slot_avg(sub, cal, a.slots, a.draws)
            if not tr3:
                print('%-10s %-8s %6d  样本不足' % ('≤%d' % cap, seg, 0))
                continue
            print('%-10s %-8s %6d %6d %5.1f%% %+7.3f%% %+8.3f%% %+7.1f%% %+7.1f%%[%+6.1f~%+6.1f] %7.1f%%'
                  % ('≤%d' % cap, seg, tr3['n'], eq['taken'], tr3['win'], tr3['avg'],
                     eq.get('tmean', 0.0), eq['total'] * 100, (eq['ann'] or 0) * 100,
                     (eq.get('ann_lo') or 0) * 100, (eq.get('ann_hi') or 0) * 100,
                     eq['mdd'] * 100))
            tab_c.append(dict(cap=cap, seg=seg, sig=tr3['n'], taken=eq['taken'],
                              win=tr3['win'], avg=tr3['avg'], total=eq['total'],
                              ann=eq['ann'], mdd=eq['mdd']))
        print()

    # ---------------- 表 D：信号质量
    print('=== 表 D｜信号质量：满仓等权年化（**不是可执行收益**，仅剥离容量影响）===')
    print('%-12s %6s %9s %9s %7s' % ('市值(亿)', '信号', '年化(槽位)', '年化(满仓)', '倍数'))
    for cap in CAPS:
        sub = [r for r in rows if r['mc'] <= cap]
        if not sub:
            continue
        e1 = equity_slot_avg(sub, cal_all, a.slots, a.draws)
        e2 = equity_full(sub, cal_all)
        mult = ('%5.1fx' % ((e2['ann'] or 0) / e1['ann'])) if (e1['ann'] or 0) > 0 else '    —'
        print('%-12s %6d %+8.1f%% %+8.1f%% %s'
              % ('≤ %d' % cap, len(sub), (e1['ann'] or 0) * 100,
                 (e2['ann'] or 0) * 100, mult))

    # ---------------- 表 E：口径敏感性
    print()
    print('=== 表 E｜口径敏感性：TP%d%% / 持 %d 日（分档排序是否一致）==='
          % (TP2 * 100, HOLD2))
    print('%-12s %6s %8s %9s' % ('市值(亿)', '信号', '均收益', '年化'))
    tab_e = []
    for cap in CAPS:
        sub = [r for r in rows2 if r['mc'] <= cap]
        tr4, eq = trades(sub), equity_slot_avg(sub, cal_all, a.slots, a.draws)
        if not tr4:
            continue
        print('%-12s %6d %+8.3f%% %+8.1f%%'
              % ('≤ %d' % cap, tr4['n'], tr4['avg'], (eq['ann'] or 0) * 100))
        tab_e.append(dict(cap=cap, sig=tr4['n'], avg=tr4['avg'], ann=eq['ann']))

    # ---------------- 表 F：按年 / 按半年 + 市场基准
    by, bn = bench(stocks, cal_all)
    print()
    print('=== 表 F｜按年单笔均收益（不建组合：切年后 240 天，槽位年化噪声比信号还大）===')
    print('基准＝主板等权同口径（次日开盘买、持 4 日）')
    year_table(rows, CAPS + [None], by)
    print()
    print('市场基准（主板等权）：' + ' ｜ '.join(
        '%s %+.3f%%(%d)' % (y, by[y] * 100, bn[y]) for y in sorted(by)))

    # ---------------- 表 G：槽位数不敏感 → 说明瓶颈不是容量
    print()
    print('=== 表 G｜槽位数扫描：成交放大 12 倍，年化纹丝不动 → 瓶颈不是容量 ===')
    print('%-6s %8s %10s %12s %10s %10s' % ('槽位', '成交', '均(成交)', '算术和', '拖累/年', '复合年化'))
    tab_g = []
    for ns in (4, 8, 16, 32):
        e = equity_slot_avg(rows, cal_all, ns, min(a.draws, 10))
        d2 = drag(rows, cal_all, ns)
        print('%-6d %8d %+9.3f%% %+11.1f%% %+9.1f%% %+9.1f%%'
              % (ns, e['taken'], e['tmean'], d2['arith'] * 100,
                 -d2['drag_ann'] * 100, (e['ann'] or 0) * 100))
        tab_g.append(dict(slots=ns, taken=e['taken'], tmean=e['tmean'],
                          arith=d2['arith'], drag_ann=d2['drag_ann'],
                          ann=e['ann']))

    # ---------------- 表 H：候选过滤条件（回答「那还能不能救」）
    CAP_H = 400
    base_h = [r for r in rows if r['mc'] <= CAP_H]
    FILTERS = [
        ('不过滤（对照）', lambda r: True),
        ('距 MA5 < 2%（现行「贴线」）', lambda r: r['feat']['dist_ma5'] < 2),
        ('距 MA5 > 5%', lambda r: r['feat']['dist_ma5'] > 5),
        ('接近 250 日新高(>85%)', lambda r: r['feat']['pos250'] > 85),
        ('首板（lbc=1）', lambda r: r['feat']['lbc'] == 1),
        ('次日低开才买', lambda r: r['feat']['hold_pct'] < 0),
        ('次日低开 0~2%', lambda r: -2 <= r['feat']['hold_pct'] < 0),
        ('次日低开 > 2%', lambda r: r['feat']['hold_pct'] < -2),
    ]
    print()
    print('=== 表 H｜候选过滤条件（≤%d 亿域，%d 槽）：能不能把负边际掰回来 ===' % (CAP_H, a.slots))
    print('★「止损距中位」这一列是验伪用的：某条件收益高、但止损距也跟着变大，说明赚的是')
    print('  「止损放宽」的机械效应（胜率↑、盈亏比↓），不是选股 alpha。')
    print('%-24s %6s %9s %9s %9s %7s %-17s %9s' %
          ('条件', '信号', '止损距中位', '全期均', '2026均', '26胜率',
           '全期年化[10~90]', '2026年化'))
    tab_h = []
    cal26 = [d for d in cal_all if d >= SPLIT]
    for lb, fn in FILTERS:
        sub = [r for r in base_h if fn(r)]
        oos = [r for r in sub if r['sig_day'] >= SPLIT]
        if len(sub) < 200 or not oos:
            print('%-24s %6d  样本不足' % (lb, len(sub)))
            continue
        e = equity_slot_avg(sub, cal_all, a.slots, min(a.draws, 10))
        e26 = equity_slot_avg(oos, cal26, a.slots, min(a.draws, 10))
        m_all = sum(r['ret'] for r in sub) / len(sub)
        m_oos = sum(r['ret'] for r in oos) / len(oos)
        w_oos = 100.0 * sum(1 for r in oos if r['ret'] > 0) / len(oos)
        sp = statistics.median([r['stop_pct'] for r in sub])
        print('%-24s %6d %+8.1f%% %+8.3f%% %+8.3f%% %6.1f%% %+7.1f%%[%+6.1f~%+6.1f] %+8.1f%%'
              % (lb, len(sub), sp, m_all, m_oos, w_oos,
                 (e['ann'] or 0) * 100, (e.get('ann_lo') or 0) * 100,
                 (e.get('ann_hi') or 0) * 100, (e26['ann'] or 0) * 100))
        tab_h.append(dict(label=lb, sig=len(sub), stop_med=sp, avg=m_all, avg26=m_oos,
                          win26=w_oos, n26=len(oos), ann=e['ann'],
                          ann_lo=e.get('ann_lo'), ann_hi=e.get('ann_hi'), ann26=e26['ann']))

    if a.json:
        json.dump(dict(mcap_mode=('snap' if a.snap else 'pit'), slots=a.slots,
                       tp=TP, hold=HOLD, cost=COST,
                       cal=[cal_all[0], cal_all[-1], len(cal_all)],
                       base=dict(trades=t0,
                                 slot={k: e0[k] for k in ('total', 'ann', 'mdd', 'sharpe',
                                                          'occ', 'taken', 'blocked_slot',
                                                          'blocked_dup', 'tmean')},
                                 full={k: f0[k] for k in ('total', 'ann', 'mdd', 'sharpe')},
                                 drag=dg),
                       bench=by,
                       cum=tab_a, bands=tab_b, segments=tab_c, tp35=tab_e,
                       slotscan=tab_g, filters=tab_h),
                  open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('\n[json] 已写 %s' % a.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())

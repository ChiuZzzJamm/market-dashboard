#!/usr/bin/env python3
"""断板反包「时间口」对照回测（2026-10-27，用户第 2 问）。

## 为什么要单独跑这一份

2026-10-27 用户提出一个具体质疑：

> 「断板反包的年化和收益低，是因为这个技术指标和形态的噪音很多，往往出现一个涨停
>   后面几天就是砸盘或持续走高，但我觉得你的回测买卖点是有问题的，如果买点设置成
>   涨停后 2-3 个交易日，卖点设置成涨停后 4-5 个交易日，又会如何？」

这句话里有两个互相独立的指控，必须分开量：

  ① **买卖点口径**：现行是「信号日次日开盘买、持有 5 日、3% 止盈」，而信号日本身是
     T+2 ~ T+5 里第一个满足四道门的日子——也就是说现行口径的买入时点漂移在 T+3 ~ T+6
     之间，且必须等「缩量 + 站稳 MA5」才承认形态成立。用户要的是**固定时间口**：
     不问形态成不成立，T+2 / T+3 开盘就买，T+4 / T+5 收盘就卖。
  ② **形态过滤器是不是噪音**：现行四道门（T+1 放量 1.1~3.5 / T+2 起缩量 / 不破 T 日低点 /
     站稳 MA5）可能只是在减少样本，不提供信息。用户说「噪音很多」——如果过滤器真有信息，
     同一时间口下「过门」组应当显著好于「全涨停」组；如果没有，就该拆门。

所以这份脚本做两件事：把 **买点 ∈ {T+1, T+2, T+3 开盘} × 卖点 ∈ {T+4, T+5, T+6 收盘}**
九宫格全跑一遍，每个格子再分「全涨停 / 断板成立 / 过现行四道门」三个口径。

## 口径（全部只用 T 日及之前 + 已发生的 T+1..T+k 数据，无未来函数）

    T 日：主板（60/00，非 ST）涨幅 >= 9.7%，且与前一根间隔 <= 10 自然日（停牌保护）
    买点：T+bo 日**开盘价**（bo ∈ 1,2,3）
    卖点：T+so 日**收盘价**（so ∈ 4,5,6，且 so > bo）
    止损：T 日最低价（生产 build_stock_pool.zt_low 的锚），等收盘确认前先按盘中触发算，
          跳空穿线按开盘价成交（与 duanban_study.sim 同一纪律，避免假装在更低价有对手盘）
    成本：双边 0.16%（与 backtest_indicators / duanban_study 同）

## 组合层：资金槽位法（2026-10-27 第五次修正，最重要的一次）

单笔均值会骗人：一个日均 3 笔、另一个日均 30 笔，均值一样但年化天差地别。

★ 早期实现是「每天对当时持有的全部仓位等权、按日再平衡」，
  即 `日收益 = Σ(各笔当日收益) / 笔数` —— 这等价于**永远满仓**，隐含
  「今天有几笔，就把 100% 资金分给这几笔」。而真实流程是每晚挂 8 只单、资金提前分配：
  · 某天只有 2 只跌到限价成交（另外 6 只没买进）→ 实际只用了 2/8 资金，
    旧写法把这 2 只当满仓结算，收益被放大 4 倍；
  · 重叠日（前 3 天买的 24 只还在手上）旧写法恰好正确。

  于是**稀疏日被系统性高估**，而稀疏日恰恰是限价单「跌得深才成交」的那批高收益样本：
  同一套逻辑，「满仓归一化」给 +99% 年化/夏普 3.08，「资金槽位法」只有 +33.6%/3.11。
  年化差 3 倍，夏普几乎不变 —— 这正是「分母写错」的特征，不是策略差异。

现在的口径（资金槽位法）：
    每只票固定占 1/SLOTS 的资金，SLOTS = 每晚挂单数 × 平均持有天数 = 8 × 4 = 32
    某日组合收益 = Σ(该笔当日收益 / SLOTS)     ← 没成交的槽位天然空着（贡献 0）
    净值 = Π(1 + 该日组合收益)
  这样「只成交 2 只」的日子只贡献 2/32 仓位，不会凭空放大。

基准 = 同期全市场等权（同一天所有主板个股收盘/收盘均值累乘），
同一区间、同一成本口径（基准无成本），与 backtest_result.json 的「全市场等权」同源。

用法：
    python3 duanban_timing.py            # 九宫格 + 三口径 + 组合层
    python3 duanban_timing.py --quick    # 只打主表
    python3 duanban_timing.py --json timing_bt.json   # 落盘（供闸门/前端读）
"""
import argparse
import json
import os
import statistics
import sys

import nshape_backtest as NB

COST = 0.0016
ANN = 242.0
SLOTS = 32          # 每晚挂 8 只单 × 平均持有 4 个交易日 = 32 个资金槽位
FLOOR_K = 1.002     # 限价下限 = T 日最低价 × 1.002（再低＝形态已破，不挂单）
LIMIT_K = -0.5      # 定稿限价位置：T+1 收盘 × 0.995（见 main 的限价网格）


# ---------------------------------------------------------------- 事件采集
def collect(stocks):
    """每只票每个涨停日一条事件；后续买卖点全部靠下标偏移，不做形态判定。"""
    ev = []
    for st in stocks:
        if not st.code.startswith(('60', '00')):
            continue
        nm = (st.name or '').upper()
        if 'ST' in nm or nm.startswith('*'):
            continue                    # 涨跌幅 5%，与 9.7% 口径不同源
        n = len(st.c)
        if n < 80:
            continue
        for iT in range(5, n - 7):
            if st.pct(iT) < 0.097 or not st.gap_ok(iT):
                continue
            vT = st.v[iT]
            if vT <= 0 or st.l[iT] <= 0:
                continue
            lowT = st.l[iT]

            # ---- 形态标签（只用 T 日及之后已发生的数据，不参与时间口选股）
            p1 = st.pct(iT + 1)
            r1 = st.v[iT + 1] / vT
            t1_limit = p1 >= 0.097                      # T+1 继续涨停 = 没断板
            brk = (not t1_limit)                        # 断板成立（最宽的定义）
            v1 = st.v[iT + 1]
            shrink2 = (0 < st.v[iT + 2] < v1) if iT + 2 < n else False
            hold_low = min(st.c[iT + 1:iT + 3]) >= lowT * 0.98 if iT + 2 < n else False
            m5 = st.ma5[iT + 2] if iT + 2 < n else None
            ma5_ok = bool(m5 and st.c[iT + 2] >= m5 * 0.97)
            # 现行四道门的最宽松版（T+1 放量区间 + 缩量 + 不破低 + 站稳 MA5）
            gate = (1.1 <= r1 <= 3.5) and brk and shrink2 and hold_low and ma5_ok

            lbc = 0
            k = iT
            while k >= 1 and st.pct(k) >= 0.097:
                lbc += 1
                k -= 1

            ev.append(dict(st=st, iT=iT, lowT=lowT, r1=r1, brk=brk, gate=gate,
                           t1_limit=t1_limit, lbc=lbc, day=st.d[iT]))
    return ev


# ---------------------------------------------------------------- 单笔
def one(st, iT, bo, so, lowT, use_stop=True, tp=None, slip=0.0):
    """T+bo 开盘买、T+so 收盘卖（或中途止盈/止损）。

    返回 (ret%, exit, hold_days, sell_px, buy_px) —— **两个成交价都必须带出来**：
    组合层要用真实出场价结算，只用收盘价序列会把「止损/止盈提前离场」抹掉，
    算出来的年化与单笔统计脱节（2026-10-27 实测：3% 止盈胜率从 39.8% 跳到 67.0%，
    但年化一动不动 —— 那就是组合层没读出场价）。

    `slip` 是**单边**额外滑点（%），涨停股次日的冲击成本。买价上浮、卖价下压，
    同时体现在 ret 与返回的 buy_px/sell_px 上——首版只在 ret 里扣、组合层不认，
    导致「滑点从 0 加到 1% 年化一个点没动」的假象（2026-10-27 自测抓到）。"""
    b, s = iT + bo, iT + so
    n = len(st.c)
    if b >= n or s >= n:
        return None
    buy = st.o[b]
    if buy <= 0:
        return None
    # 一字板买不进：开盘即涨停（相对前收）
    if st.c[b - 1] > 0 and buy >= st.c[b - 1] * 1.095:
        return None
    # 生产纪律（build_stock_pool / duanban_study 同一条）：开盘已在 T 日最低价之下
    # = 形态当天已破，不下单。不算进样本（否则等于假装在更低的位置有人接）。
    if lowT and buy <= lowT:
        return None
    slip_k = slip / 100.0
    buy_px = buy * (1 + slip_k)                 # 实际成交的买价（含滑点）
    stop = lowT if use_stop else None
    if stop is not None and stop >= buy_px:
        stop = buy_px * 0.95                    # T 日最低高于买价（跳空高开后回踩）：退化成 -5%

    def _out(px, exit_tag, hold):
        sell_px = px * (1 - slip_k)
        return ((sell_px / buy_px - 1) * 100 - COST * 100, exit_tag, hold,
                sell_px, buy_px)

    for j in range(b, s + 1):
        if stop is not None:
            if st.o[j] <= stop:
                return _out(st.o[j], 'stop_gap', j - b + 1)
            if st.l[j] <= stop:
                return _out(stop, 'stop', j - b + 1)
        if tp:
            if st.o[j] >= buy * (1 + tp):
                return _out(st.o[j], 'tp_gap', j - b + 1)
            if st.h[j] >= buy * (1 + tp):
                return _out(buy * (1 + tp), 'tp', j - b + 1)
    return _out(st.c[s], 'time', s - b + 1)


def one_limit(st, iT, bo, so, lowT, tp=0.05, k=LIMIT_K):
    """★可下单形态：限价挂单，而不是「9:45 看盘再决定」。

    为什么必须挂限价（2026-10-27 实测，见 main 的限价网格）：
      本策略单笔毛利只有 +0.43%，买入价每贵 0.5% 年化就从 +58.6% 掉到 −6.8%，
      贵 1% 就是 −44%。而 9:45 相对 9:25 开盘价通常已经偏了 0.3~1%……
      也就是说「9:45 才决定买什么」这个动作本身就把 alpha 吃光了。
      真正可执行的形态是：昨晚 16:00 就定好清单，开盘前把限价单挂进去。

    限价规则（三处前视/实现 bug 都在这里踩过，逐条写死）：
      cap   = T+1 收盘 × (1+k)         愿意追高的上限
      floor = T 日最低价 × 1.002        形态有效区下沿，再低就不该持有
      · cap < floor           → 不挂单（愿意出的价买不到有效形态）
      · T+2 开盘 <= floor     → 不买（开盘直接破位，形态已废）
      · T+2 开盘 <= cap       → 按开盘价成交
      · T+2 最低 <= cap       → 按 cap 成交
      · 否则                  → 不成交（放弃）
    ★禁止把「开盘价低于 floor」抬成 floor 成交 —— 那个价现实中拿不到。
    ★禁止因「成交价 <= lowT」整笔剔除 —— 挂单价成交那一刻你不可能知道当天最低，
      事后剔除 = 前视偏差（初版因此在 −3% 档跑出 +315% 年化）。
    """
    b, s = iT + bo, iT + so
    n = len(st.c)
    if b >= n or s >= n:
        return None
    op = st.o[b]
    if op <= 0:
        return None
    if st.c[b - 1] > 0 and op >= st.c[b - 1] * 1.095:
        return None                       # 一字涨停买不进
    floor = lowT * FLOOR_K if lowT else 0.0
    cap = st.c[b - 1] * (1 + k / 100.0)
    if floor and cap < floor:
        return None
    if floor and op <= floor:
        return None
    if op <= cap:
        buy = op
    elif st.l[b] <= cap:
        buy = cap
    else:
        return None
    if buy <= 0:
        return None
    stop = lowT if (lowT and lowT < buy) else buy * 0.95
    tp_px = buy * (1 + tp) if tp else None

    def _out(px, tag, hold):
        return ((px / buy - 1) * 100 - COST * 100, tag, hold, px, buy)

    for j in range(b, s + 1):
        pc = st.c[j - 1] if j else 0
        ld = pc > 0 and st.c[j] <= pc * 0.905 and abs(st.h[j] - st.l[j]) < 1e-9
        if not ld:                        # 一字跌停日卖不出去，顺延
            if st.o[j] <= stop:
                return _out(st.o[j], 'stop_gap', j - b + 1)
            if st.l[j] <= stop:
                return _out(stop, 'stop', j - b + 1)
        if tp_px:
            if st.o[j] >= tp_px:
                return _out(st.o[j], 'tp_gap', j - b + 1)
            if st.h[j] >= tp_px:
                return _out(tp_px, 'tp', j - b + 1)
    return _out(st.c[s], 'time', s - b + 1)


def summarize(rows, label=''):
    n = len(rows)
    if not n:
        return None
    rets = [r['ret'] for r in rows]
    win = sum(1 for x in rets if x > 0)
    gains = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    avg = sum(rets) / n
    pf = (sum(gains) / -sum(losses)) if losses and sum(losses) < 0 else 9.99
    return dict(label=label, n=n, win=win / n * 100, avg=avg,
                med=statistics.median(rets), pf=pf,
                ag=sum(gains) / len(gains) if gains else 0,
                al=sum(losses) / len(losses) if losses else 0,
                days=sum(r['hold'] for r in rows) / n)


HEAD = ('%-30s %6s %7s %8s %8s %5s %7s %7s'
        % ('口径', '笔数', '胜率', '均收益', '中位', '盈亏比', '均盈', '均亏'))


def line(s):
    if not s:
        return '%-30s %6d  —' % ('(空)', 0)
    return ('%-30s %6d %6.1f%% %+8.3f%% %+8.3f%% %5.2f %+7.2f%% %+7.2f%%'
            % (s['label'], s['n'], s['win'], s['avg'], s['med'], s['pf'], s['ag'], s['al']))


# ---------------------------------------------------------------- 组合层
def equity(rows, stocks, slots=SLOTS, all_days=None):
    """资金槽位法组合净值：每笔固定占 1/slots 资金，未成交槽位空仓。

    ★必须按**真实出场价**结算（2026-10-27 修）：早先版本只累乘收盘价，
      止损/止盈提前离场被抹平 → 换成 3% 止盈后单笔胜率 39.8%→67.0%，
      年化却一个点没动。那不是策略无效，是组合层读错了出场。
    ★必须除以**固定槽位**而非当日笔数（2026-10-27 再修，见模块 docstring）：
      除当日笔数 = 永远满仓 = 稀疏日高估 3 倍年化。
    ★权重 = 1 / max(slots, 当日持仓数)（2026-10-27 第六修）：
      只写 1/slots 时，同一天持仓超过 slots 只就等于加杠杆
      （无过滤口径日均 43 只持仓 × 1/32 = 134% 仓位 → 净值能穿负、回撤算出 −136%，
      这种数字不可能出现在真实账户里）。取 max 后：持仓不足 → 空槽位持现；
      持仓超额 → 按实际只数等权、总仓位恰好 100%，永不超杠杆。
      定稿口径（每晚 8 只 × 平均持 2.1 日 ≈ 17 只并发 < 32）不受影响。
    成本按双边 0.16% 摊到买卖两端（买价 +0.08%、卖价 −0.08%），与单笔口径同源。
    """
    days = all_days or sorted({d for st in stocks for d in st.d})
    daily = {}
    for r in rows:
        st = r['st']
        b = r['iT'] + r['bo']
        x = b + r['hold'] - 1                     # 实际出场日（可能早于 T+so）
        n = len(st.c)
        if b >= n:
            continue
        x = min(x, n - 1)
        buy = (r.get('bx') if r.get('bx') else st.o[b]) * (1 + COST / 2.0)
        sell = (r.get('px') if r.get('px') else st.c[x]) * (1 - COST / 2.0)
        if buy <= 0:
            continue
        for j in range(b, x + 1):
            if j == b and j == x:
                # ★当天进当天出（持有 1 日，含盘中止盈/止损立即成交）。
                #   原实现走 `j == b` 分支用收盘价，把这个最常见的短持有情形算成了
                #   「开盘买到收盘」，出场价被完全忽略（2026-10-27 自测抓到：
                #   5% 止盈的胜率 54.9% 却和「不止盈」年化几乎一样）。
                dr = sell / buy - 1
            elif j == b:
                dr = st.c[j] / buy - 1
            elif j == x:
                dr = sell / st.c[j - 1] - 1
            else:
                pc = st.c[j - 1]
                dr = (st.c[j] / pc - 1) if pc else 0.0
            daily.setdefault(st.d[j], []).append(dr)      # 先存原始笔收益
    lv, series = 1.0, []
    peak, mdd, mdd_d = 0.0, 0.0, None
    for d in days:
        v = daily.get(d)
        if not v:
            continue
        w = 1.0 / max(slots, len(v))              # ★不足 slot 空仓、超额等权不超杠杆
        lv *= (1 + sum(v) * w)
        series.append((d, lv))
        peak = max(peak, lv)
        if peak > 0:
            dd = lv / peak - 1
            if dd < mdd:
                mdd, mdd_d = dd, d
    tot = lv - 1
    nd = len(series)
    ann = ((1 + tot) ** (ANN / nd) - 1) if nd and tot > -1 else None
    # 日收益序列 → 夏普
    rets = []
    prev = 1.0
    for _, x in series:
        rets.append(x / prev - 1)
        prev = x
    sd = statistics.pstdev(rets) if len(rets) > 2 else 0.0
    mu = (sum(rets) / len(rets)) if rets else 0.0
    sharpe = (mu / sd * ANN ** 0.5) if sd > 0 else 0.0
    return dict(total=tot, ann=ann, mdd=mdd, mdd_d=mdd_d, days=nd, sharpe=sharpe,
                series=series)


def bench(stocks, days_lo, days_hi):
    """同期全市场等权（收盘/收盘均值累乘）。"""
    days = sorted({d for st in stocks for d in st.d
                   if days_lo <= d <= days_hi})
    per = {}
    for st in stocks:
        for j in range(1, len(st.c)):
            pc = st.c[j - 1]
            if pc <= 0:
                continue
            d = st.d[j]
            if d < days_lo or d > days_hi:
                continue
            per.setdefault(d, []).append(st.c[j] / pc - 1)
    lv, series = 1.0, []
    peak, mdd = 0.0, 0.0
    for d in days:
        v = per.get(d)
        if not v or len(v) < 100:
            continue
        lv *= (1 + sum(v) / len(v))
        series.append((d, lv))
        peak = max(peak, lv)
        mdd = min(mdd, lv / peak - 1 if peak else 0)
    tot = lv - 1
    nd = len(series)
    ann = ((1 + tot) ** (ANN / nd) - 1) if nd and tot > -1 else None
    rets, prev = [], 1.0
    for _, x in series:
        rets.append(x / prev - 1)
        prev = x
    sd = statistics.pstdev(rets) if len(rets) > 2 else 0.0
    mu = (sum(rets) / len(rets)) if rets else 0.0
    return dict(total=tot, ann=ann, mdd=mdd, days=nd,
                sharpe=(mu / sd * ANN ** 0.5) if sd > 0 else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--json')
    a = ap.parse_args()

    stocks = NB.load_all()
    print('[load] %d 只（含非主板，采集时再筛 60/00 非 ST）' % len(stocks), flush=True)
    ev = collect(stocks)
    print('[scan] 涨停事件 %d 个（%d 只标的，%s ~ %s）'
          % (len(ev), len({e['st'].code for e in ev}),
             min(e['day'] for e in ev), max(e['day'] for e in ev)) + '', flush=True)
    print('[scan] 其中断板成立 %d、过现行四道门 %d'
          % (sum(1 for e in ev if e['brk']), sum(1 for e in ev if e['gate'])), flush=True)

    # ---------------- 九宫格：买点 × 卖点
    print()
    print('=== 九宫格（全涨停口径，止损=T日最低价，成本 0.16%）===')
    print(HEAD)
    grid = {}
    for bo in (1, 2, 3):
        for so in (4, 5, 6):
            if so <= bo:
                continue
            rows = []
            for e in ev:
                r = one(e['st'], e['iT'], bo, so, e['lowT'])
                if r:
                    rows.append(dict(e, ret=r[0], exit=r[1], hold=r[2], px=r[3], bx=r[4],
                                     bo=bo, so=so))
            s = summarize(rows, '买 T+%d 开 · 卖 T+%d 收' % (bo, so))
            print(line(s))
            grid[(bo, so)] = (rows, s)

    # ---------------- 形态口径对照（同一时间口）
    print()
    print('=== 形态过滤器值不值：同一时间口下三个口径 ===')
    for bo, so in ((2, 4), (2, 5), (3, 5)):
        print('-- 买 T+%d 开盘 · 卖 T+%d 收盘' % (bo, so))
        print(HEAD)
        base = []
        for e in ev:
            r = one(e['st'], e['iT'], bo, so, e['lowT'])
            if r:
                base.append(dict(e, ret=r[0], exit=r[1], hold=r[2], px=r[3], bx=r[4],
                                 bo=bo, so=so))
        for lb, key in (('全涨停（不过滤）', None), ('仅断板（T+1未涨停）', 'brk'),
                        ('过现行四道门', 'gate')):
            sub = base if key is None else [x for x in base if x[key]]
            print(line(summarize(sub, lb)))
        # 首板 / 连板
        for lb, lo, hi in (('首板', 1, 2), ('2连板+', 2, 99)):
            sub = [x for x in base if lo <= x['lbc'] < hi]
            print(line(summarize(sub, lb)))

    if a.quick:
        return 0

    # ---------------- 止盈 / 不止损 / 单纯时间口
    print()
    print('=== 卖出纪律微调（买 T+2 开）===')
    print(HEAD)
    for lb, so, kw in (('卖 T+4 收 · 有止损', 4, dict(use_stop=True)),
                       ('卖 T+4 收 · 无止损', 4, dict(use_stop=False)),
                       ('卖 T+5 收 · 有止损', 5, dict(use_stop=True)),
                       ('卖 T+5 收 · 无止损', 5, dict(use_stop=False)),
                       ('卖 T+5 收 · 3%止盈', 5, dict(use_stop=True, tp=0.03)),
                       ('卖 T+5 收 · 5%止盈', 5, dict(use_stop=True, tp=0.05)),
                       ('卖 T+6 收 · 8%止盈', 6, dict(use_stop=True, tp=0.08))):
        rows = []
        for e in ev:
            r = one(e['st'], e['iT'], 2, so, e['lowT'], **kw)
            if r:
                rows.append(dict(e, ret=r[0], exit=r[1], hold=r[2], px=r[3], bx=r[4],
                                 bo=2, so=so))
        print(line(summarize(rows, lb)))

    # ---------------- 训练 / 样本外
    print()
    print('=== 时间分段（买 T+2 开 · 卖 T+4 收）===')
    print(HEAD)
    rows = grid[(2, 4)][0]
    split = '2026-01-01'
    print(line(summarize([r for r in rows if r['day'] < split], '训练 2024~2025')))
    print(line(summarize([r for r in rows if r['day'] >= split], '样本外 2026')))

    # ---------------- 组合层：年化、回撤、对基准
    print()
    print('=== 组合层（等权满仓、日再平衡）===')
    print('%-26s %8s %8s %8s %7s %6s' % ('口径', '累计', '年化', '最大回撤', '夏普', '天数'))
    picks = [(2, 4), (2, 5), (3, 5), (1, 4)]
    lo = min(e['day'] for e in ev)
    hi = max(e['day'] for e in ev)
    bm = bench(stocks, lo, hi)
    print('%-26s %+7.1f%% %+7.1f%% %7.1f%% %7.2f %6d'
          % ('全市场等权（基准）', bm['total'] * 100, (bm['ann'] or 0) * 100,
             bm['mdd'] * 100, bm['sharpe'], bm['days']))
    detail = {}
    for bo, so in picks:
        rows2 = grid[(bo, so)][0]
        eq = equity(rows2, stocks)
        print('%-26s %+7.1f%% %+7.1f%% %7.1f%% %7.2f %6d'
              % ('买T+%d开 卖T+%d收' % (bo, so), eq['total'] * 100,
                 (eq['ann'] or 0) * 100, eq['mdd'] * 100, eq['sharpe'], eq['days']))
        detail['%d-%d' % (bo, so)] = dict(
            n=len(rows2), win=grid[(bo, so)][1]['win'],
            avg=grid[(bo, so)][1]['avg'], tot=eq['total'], ann=eq['ann'],
            mdd=eq['mdd'], sharpe=eq['sharpe'])

    # ---------------- 只做首板 + 时间口（用户说的「噪音」最可能的方向）
    print()
    print('=== 叠加过滤后的时间口（买 T+2 开 · 卖 T+4 收）===')
    print('%-26s %6s %7s %8s %8s %7s' % ('口径', '笔数', '胜率', '均收益', '年化', '回撤'))
    for lb, f in (('全涨停', lambda x: True),
                  ('仅首板', lambda x: x['lbc'] == 1),
                  ('仅断板', lambda x: x['brk']),
                  ('仅放量1.1~3.5', lambda x: 1.1 <= x['r1'] <= 3.5),
                  ('仅 T+1 收阴', lambda x: x['st'].pct(x['iT'] + 1) < 0),
                  ('仅过四道门', lambda x: x['gate']),
                  ('首板+断板+放量', lambda x: x['lbc'] == 1 and x['brk'] and 1.1 <= x['r1'] <= 3.5)):
        sub = [x for x in grid[(2, 4)][0] if f(x)]
        s = summarize(sub, lb)
        if not s or s['n'] < 30:
            print('%-26s %6d  样本不足' % (lb, len(sub)))
            continue
        eq = equity(sub, stocks)
        detail['f|' + lb] = dict(n=s['n'], win=s['win'], avg=s['avg'],
                                 tot=eq['total'], ann=eq['ann'], mdd=eq['mdd'])
        print('%-26s %6d %6.1f%% %+8.3f%% %+6.1f%% %6.1f%%'
              % (lb, s['n'], s['win'], s['avg'], (eq['ann'] or 0) * 100, eq['mdd'] * 100))

    # ---------------- 生产口径：时间口 + 出清条件（用户方案的可执行版本）
    # 用户 2026-10-27 的原话是「买点涨停后 2-3 日、卖点涨停后 4-5 日」。九宫格已经证明
    # 方向对（T+1 买是灾难、越晚买越好、越早卖越好），但**纯时间口仍然亏钱**：
    # 全涨停口径买 T+2 卖 T+4 只有 −0.379%/笔、年化 −26.7%。真正让它能上桌的是
    # 「首板 + 断板 + 放量」三道**无前视**过滤（只用 T 日与 T+1 收盘就能算出来）。
    print()
    print('=== 生产口径：买 T+2 开 · 卖 T+4 收 · 首板+断板+放量 · 每日限 8 只 · 5% 止盈 ===')
    print('%-26s %6s %7s %8s %8s %7s %6s'
          % ('口径', '笔数', '胜率', '均收益', '年化', '回撤', '夏普'))

    def _filt(x, r1lo=1.1, r1hi=3.5):
        return (x['lbc'] == 1 and x['brk'] and r1lo <= x['r1'] <= r1hi)

    def _cap(rows, cap):
        by = {}
        for r in rows:
            by.setdefault(r['day'], []).append(r)
        out = []
        for _, v in by.items():
            v.sort(key=lambda r: r['r1'], reverse=True)
            out.extend(v[:cap])
        return out

    prod = {}
    for lb, cap, tp, slip in (('无过滤 · 不限笔数', None, None, 0.0),
                              ('无过滤 · 限8只', 8, None, 0.0),
                              ('首板+断板+放量 · 限8只', 8, None, 0.0),
                              ('再加 5% 止盈', 8, 0.05, 0.0),
                              ('+ 单边滑点 0.3%', 8, 0.05, 0.3),
                              ('+ 单边滑点 0.5%', 8, 0.05, 0.5),
                              ('+ 单边滑点 1.0%', 8, 0.05, 1.0)):
        rows = []
        for e in ev:
            if '首板' in lb and not _filt(e):
                continue
            r = one(e['st'], e['iT'], 2, 4, e['lowT'], tp=tp, slip=slip)
            if r:
                rows.append(dict(e, ret=r[0], exit=r[1], hold=r[2], px=r[3], bx=r[4],
                                 bo=2, so=4))
        if cap:
            rows = _cap(rows, cap)
        s = summarize(rows, lb)
        if not s:
            continue
        eq = equity(rows, stocks)
        prod[lb] = dict(n=s['n'], win=s['win'], avg=s['avg'], ann=eq['ann'],
                        mdd=eq['mdd'], sharpe=eq['sharpe'])
        print('%-26s %6d %6.1f%% %+8.3f%% %+8.1f%% %6.1f%% %6.2f'
              % (lb, s['n'], s['win'], s['avg'], (eq['ann'] or 0) * 100,
                 eq['mdd'] * 100, eq['sharpe']))

    # ---------------- ★限价挂单（定稿口径：这是唯一能真下单的形态）
    # 上面所有表都是「T+2 开盘价成交」，但真人做不到：9:45 看盘再买，价格已经偏了。
    # 2026-10-27 实测：单笔毛利 +0.43%，买贵 0.5% → 年化 +58.6% 掉到 −6.8%，
    # 买贵 1% → −44%。所以本策略的可执行形态只有一种：**昨晚定清单、开盘前挂限价**。
    print()
    print('=== 限价挂单（昨晚定清单、开盘前挂单；限价 = T+1 收盘×(1+k)）===')
    print('%-16s %8s %8s %8s %8s %6s %7s'
          % ('挂单价', '成交率', '笔数', '年化', '回撤', '夏普', '均收益'))

    def _plan(cap=8):
        """★cap 必须在挂单之前（第三处前视 bug）：
        先算成交再取前 N 只 = 「先知道谁会成交，再决定买几只」，
        而成交集合天然是「跌得多的」，越低的限价越能事后凑出高收益。
        真实流程：昨晚按已知的 r1 排序挑前 8 只挂单，成交几只就是几只。"""
        by = {}
        for e in ev:
            if not _filt(e):
                continue
            by.setdefault(e['day'], []).append(e)
        for d in by:
            by[d].sort(key=lambda e: e['r1'], reverse=True)
            by[d] = by[d][:cap]
        return by

    plan = _plan(8)
    n_plan = sum(len(v) for v in plan.values())
    all_days = sorted({d for st in stocks for d in st.d})
    limit_tbl = {}
    for k, zh in ((None, '市价开盘（基准）'), (0.0, '昨收价'), (-0.3, '昨收−0.3%'),
                  (-0.5, '昨收−0.5%'), (-1.0, '昨收−1%'), (-2.0, '昨收−2%')):
        rows = []
        for d, lst in plan.items():
            for e in lst:
                r = (one(e['st'], e['iT'], 2, 4, e['lowT'], tp=0.05, slip=0.0)
                     if k is None else
                     one_limit(e['st'], e['iT'], 2, 4, e['lowT'], tp=0.05, k=k))
                if r:
                    rows.append(dict(e, ret=r[0], exit=r[1], hold=r[2], px=r[3], bx=r[4],
                                     bo=2, so=4))
        if len(rows) < 100:
            continue
        s = summarize(rows, zh)
        eq = equity(rows, stocks, SLOTS, all_days)
        limit_tbl[zh] = dict(n=s['n'], win=s['win'], avg=s['avg'], ann=eq['ann'],
                             mdd=eq['mdd'], sharpe=eq['sharpe'], tot=eq['total'])
        print('%-16s %7.1f%% %8d %+7.1f%% %7.1f%% %6.2f %+7.3f%%'
              % (zh, len(rows) / max(1, n_plan) * 100, s['n'], (eq['ann'] or 0) * 100,
                 eq['mdd'] * 100, eq['sharpe'], s['avg']))

    # ---------------- 定稿口径的完整交代（分年 + 排序无关性 + 容量）
    print()
    print('=== 定稿口径明细（限价 −0.5% · 止盈 5% · 卖 T+4 收 · 每晚 8 只 · 每笔 1/32 资金）===')
    final_rows = []
    for d, lst in plan.items():
        for e in lst:
            r = one_limit(e['st'], e['iT'], 2, 4, e['lowT'], tp=0.05, k=LIMIT_K)
            if r:
                final_rows.append(dict(e, ret=r[0], exit=r[1], hold=r[2], px=r[3],
                                       bx=r[4], bo=2, so=4))
    feq = equity(final_rows, stocks, SLOTS, all_days)
    fs = summarize(final_rows, '定稿')
    print('  累计 %+.1f%%  年化 %+.1f%%  回撤 %.1f%%  夏普 %.2f  笔数 %d  胜率 %.1f%%  均 %+.3f%%'
          % (feq['total'] * 100, (feq['ann'] or 0) * 100, feq['mdd'] * 100,
             feq['sharpe'], fs['n'], fs['win'], fs['avg']))
    by_year = {}
    for y in ('2023', '2024', '2025', '2026'):
        sub = [r for r in final_rows if r['day'][:4] == y]
        if len(sub) < 50:
            continue
        ss = summarize(sub, y)
        yd = [d for d in all_days if d[:4] == y]
        eqy = equity(sub, stocks, SLOTS, yd)
        bmy = bench(stocks, y + '-01-01', y + '-12-31')
        exc = (eqy['ann'] or 0) * 100 - (bmy['ann'] or 0) * 100
        by_year[y] = dict(n=ss['n'], avg=ss['avg'], ann=eqy['ann'], mdd=eqy['mdd'],
                          bench=bmy['ann'], exc=exc / 100.0)
        print('    %s  n=%4d 均%+6.3f%% 年化%+7.1f%% 回撤%6.1f%% ｜ 基准%+6.1f%% 超额%+7.1f%%'
              % (y, ss['n'], ss['avg'], (eqy['ann'] or 0) * 100, eqy['mdd'] * 100,
                 (bmy['ann'] or 0) * 100, exc))

    # 排序无关性：如果只有「按 r1 挑」有效，那策略就是「挑对了那 8 只」而不是「形态有效」
    print()
    print('=== 排序无关性（同一批候选、换 8 只的挑法）===')
    sort_tbl = {}
    for zh2, key in (('放量比降序', lambda e: -e['r1']),
                     ('放量比升序', lambda e: e['r1']),
                     ('代码排序', lambda e: e['st'].code)):
        by2 = {}
        for e in ev:
            if _filt(e):
                by2.setdefault(e['day'], []).append(e)
        for d in by2:
            by2[d] = sorted(by2[d], key=key)[:8]
        rr = []
        for d, lst in by2.items():
            for e in lst:
                r = one_limit(e['st'], e['iT'], 2, 4, e['lowT'], tp=0.05, k=LIMIT_K)
                if r:
                    rr.append(dict(e, ret=r[0], exit=r[1], hold=r[2], px=r[3], bx=r[4],
                                   bo=2, so=4))
        ee = equity(rr, stocks, SLOTS, all_days)
        sort_tbl[zh2] = dict(n=len(rr), ann=ee['ann'], sharpe=ee['sharpe'])
        print('  %-12s n=%4d 年化 %+7.1f%% 夏普 %.2f'
              % (zh2, len(rr), (ee['ann'] or 0) * 100, ee['sharpe']))

    # ---------------- 稳健性：分年 + 出场分布 + 信号频率
    rows = final_rows
    print()
    print('=== 稳健性：分年（纯时间口口径，与上面定稿对照）===')
    for y in ('2023', '2024', '2025', '2026'):
        sub = [r for r in rows if r['day'][:4] == y]
        if len(sub) < 50:
            continue
        ss = summarize(sub, y)
        yd = [d for d in all_days if d[:4] == y]
        eqy = equity(sub, stocks, SLOTS, yd)
        bmy = bench(stocks, y + '-01-01', y + '-12-31')
        print('  %s  n=%5d 胜率%5.1f%% 均%+6.3f%% 年化%+7.1f%% 回撤%6.1f%% ｜ 基准年化%+6.1f%%'
              % (y, ss['n'], ss['win'], ss['avg'], (eqy['ann'] or 0) * 100,
                 eqy['mdd'] * 100, (bmy['ann'] or 0) * 100))

    cnt = {}
    for e in ev:
        if _filt(e):
            cnt[e['day']] = cnt.get(e['day'], 0) + 1
    vals = sorted(cnt.values())
    print()
    print('=== 可执行性 ===')
    print('  有信号交易日 %d 天 / 全区间 %d 天；日均候选 %.1f 只，中位 %d 只'
          % (len(cnt), len({d for st in stocks for d in st.d}),
             sum(vals) / len(vals), vals[len(vals) // 2]))
    print('  候选 ≥8 只的交易日 %.1f%%（限 8 只才真正起作用）'
          % (sum(1 for v in vals if v >= 8) / len(vals) * 100))
    dist = {}
    for r in rows:
        dist[r['exit']] = dist.get(r['exit'], 0) + 1
    zh = {'tp': '盘中止盈', 'tp_gap': '跳空越过止盈', 'stop': '盘中止损',
          'stop_gap': '跳空穿止损', 'time': '到期收盘平'}
    print('  出场：' + '、'.join('%s %.1f%%' % (zh.get(k, k), v / len(rows) * 100)
                              for k, v in sorted(dist.items(), key=lambda x: -x[1]))
          + '；平均持有 %.2f 个交易日' % (sum(r['hold'] for r in rows) / len(rows)))

    if a.json:
        out = dict(detail=detail, prod=prod, bench=dict(
            total=bm['total'], ann=bm['ann'], mdd=bm['mdd'], sharpe=bm['sharpe']),
            span=[lo, hi], nEvents=len(ev),
            # ★定稿口径（前端「开盘精选」与回测台账读这一段，别自己算）
            final=dict(
                rule=('昨晚 16:00 已知条件定清单（首板 + T+1 断板 + 放量 1.1~3.5 倍），'
                      '开盘前挂限价单：限价 = T+1 收盘 × %.3f，且必须高于 T 日最低价 × %.3f；'
                      'T+2 开盘；盘中跌破 T 日最低价止损；+5%% 止盈；T+4 收盘到期平；'
                      '每晚最多挂 8 只，每只占 1/%d 资金。'
                      % (1 + LIMIT_K / 100.0, FLOOR_K, SLOTS)),
                n=fs['n'], win=fs['win'], avg=fs['avg'],
                total=feq['total'], ann=feq['ann'], mdd=feq['mdd'],
                sharpe=feq['sharpe'], slots=SLOTS, limitK=LIMIT_K, tp=0.05,
                holdDays=4, capPerDay=8, planN=n_plan, days=len(plan),
                byYear=by_year, limit=limit_tbl, sorterInvariance=sort_tbl),
        )
        for (bo, so), (rows2, s) in grid.items():
            if not s:
                continue
            out.setdefault('gridCells', {})['%d-%d' % (bo, so)] = dict(
                n=s['n'], win=s['win'], avg=s['avg'], med=s['med'], pf=s['pf'],
                hold=s['days'])
        tmp = a.json + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, a.json)
        print('\n[write] %s' % a.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())

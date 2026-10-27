#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""goldpit_study.py —— 「黄金坑」原型：昨日大跌 + 缩量 + 今日不破 / 今日跳空

## 要回答的问题

2026-10-27 的直觉：隔夜外盘（美股/韩股半导体）大涨，而 A 股半导体昨日资金流出、
价格被砸下来 → 「跌下来的价格 + 外部利好」应判**利好**而非利空。把它变成能下单的
池子，可以拆成两个可验证的子问题：

**Q1（形态）** 昨日大跌 + 缩量 + 今日不破 → 之后几天有没有超额？
**Q2（外盘映射）** 隔夜外盘涨，在 A 股开盘时唯一可观测的落地形式就是**今日跳空**。
    那么「昨日大跌 + 今日高开」该追还是该等？高开多少算过度？

Q2 才是用户原话的核心，而它必须在**T 日开盘买入**口径下测——9:25 集合竞价出来
就知道跳空多少，9:30 可以直接下单，完全合法，不需要任何未来信息。

## 基准：同日同池平盘票（踩了两次坑之后的最终口径）

1. **日频再平衡等权指数**（`backtest_indicators` 里那个）→ 全错。A 股短期反转强，
   日频再平衡本身白捡 0.3~0.4%/5 日，于是**任何**持仓都被判负超额，连平盘票也是。
2. **全市场横截面均值** → 也错。样本含大量微盘股，2023-2026 微盘极强，于是
   「成交额≥2 亿的主板票」这一整个池子被市值因子压死（平盘票超额 −0.23%、t=−3.2），
   测出来的是市值因子不是信号。

最终：**同日、同池（收盘≥2 元 / 成交额≥2 亿 / 主板非 ST）、昨日涨跌在 ±1% 以内
的平盘票**，取它们同一持有期的均值做对照。市场、市值、流动性、波动全控住。

注：超额 = 本组扣成本收益 − 对照组**未扣成本**均值。所以对照组自身的超额恒为
−0.16%（成本），不是 0——这是定义使然。口径是否可信看第 8 节的**跌幅梯度
单调性**（跌得越深超额越高的剂量-反应），那才是真证据。

## 三种入场口径

| 口径 | 买入 | 卖出 | 隐含假设 |
|---|---|---|---|
| `tail` | T 日**尾盘**（收盘价） | T+N 收盘 | 收盘前 5 分钟确认「不破」再成交 |
| `next` | T+1 **开盘** | T+1+N 开盘 | 放弃确认日阳线，只要「不破」的持续性 |
| `gap` | T 日**开盘** | T+N 收盘 | 9:25 看集合竞价跳空，9:30 直接下单（Q2 专用）|

## 信号与可交易

- 昨日跌幅 `c[T-1]/c[T-2]-1 <= DROP`（默认 −2.5%）
  - 或**相对跌幅**：个股昨日跌幅 − 全市场昨日均值，剔掉「全市场一起跌」
- 昨日量比 `a[T-1] / mean(a[T-5..T-1])` 上界 `DRY`（默认 0.8）
- 今日不破：`none` / `low`（不破昨低）/ `close`（收昨收上）/ `hl`（两者）
- 可交易：收盘 ≥2 元、成交额 ≥2 亿、当日涨幅 ≤9.7%、`o/c-1 < 9.5%` 非一字

## 成本与显著性

双边 0.16%。**t 值一律按信号日聚类**（同日几十只票不是独立样本）。

## 用法

    python3 goldpit_study.py               # 全量（约 3 分钟）
    python3 goldpit_study.py --limit 200   # 冒烟
    python3 goldpit_study.py --dump        # 顺带写 goldpit_study.json

**纯本地计算，不写 data.js、不进任何自动化。**
"""
import argparse
import json
import os
import statistics
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import nshape_backtest as NB          # noqa: E402

COST = 0.0016
UNFILLABLE_GAP = 0.095
MIN_PRICE = 2.0
MIN_AMOUNT = 2e8
PCT_HI = 9.7
HORIZONS = (1, 2, 3, 5, 10)
WARM = 260
CTRL_BAND = 0.010      # 对照组：昨日涨跌 ±1% 的平盘票

DROP = -0.025
DRY = 0.80
VOL_LOOK = 5

HOLD_DEFS = (
    ('none', '只看昨日跌'),
    ('low', '跌 + 不破昨低'),
    ('close', '跌 + 收在昨收上'),
    ('hl', '跌 + 不破昨低 + 收在昨收上'),
)
MODES = (('tail', 'T日尾盘'), ('next', 'T+1开盘'), ('gap', 'T日开盘'))


# ---------------------------------------------------------------- 数据
def load(limit=None):
    """主板、剔 ST（ST 涨跌幅 5%，与 ±10% 的信号不可比）。"""
    out = []
    for s in NB.load_all(limit):
        if s.code[:2] not in ('60', '00'):
            continue
        if NB.is_st(s.name):
            continue
        out.append(s)
    return out


class Pool(object):
    """全库对齐到统一日历；`td` 标出每天「可交易」（价格 + 成交额双门槛）。"""

    __slots__ = ('days', 'idx', 'gi', 'op', 'cl', 'td', 'r_cc')

    def __init__(self, stocks):
        days = sorted({d for s in stocks for d in s.d})
        self.days, self.idx = days, {d: i for i, d in enumerate(days)}
        nd = len(days)
        self.gi, self.op, self.cl, self.td = [], [], [], []
        cc = [[0.0, 0] for _ in range(nd)]
        for s in stocks:
            gi = [self.idx[d] for d in s.d]
            o = [None] * nd
            c = [None] * nd
            t = [False] * nd
            for i, j in enumerate(gi):
                o[j], c[j] = s.o[i], s.c[i]
                t[j] = (s.c[i] >= MIN_PRICE and s.c[i] * s.v[i] >= MIN_AMOUNT)
                if i and s.c[i - 1] > 0:
                    cc[j][0] += s.c[i] / s.c[i - 1] - 1
                    cc[j][1] += 1
            self.gi.append(gi)
            self.op.append(o)
            self.cl.append(c)
            self.td.append(t)
        need = max(30, len(stocks) // 3)
        self.r_cc = [(v[0] / v[1]) if v[1] >= need else None for v in cc]


# ---------------------------------------------------------------- 收益
def fwd(s, gi, k, mode, N):
    """k 日信号 → N 日持有收益（不含成本）。返回 (r, 入场日在统一日历下标)。"""
    n = len(s.d)
    if mode == 'tail':
        if k + N >= n or s.c[k] <= 0 or s.c[k + N] <= 0:
            return None, None
        return s.c[k + N] / s.c[k] - 1, gi[k]
    if mode == 'gap':
        if k + N >= n or s.c[k] <= 0 or s.o[k] <= 0 or s.c[k + N] <= 0:
            return None, None
        if not (-0.5 < s.o[k] / s.c[k - 1] - 1 < 0.5):
            return None, None
        return s.c[k + N] / s.o[k] - 1, gi[k]
    # next
    iA = k + 1
    if iA + N >= n or s.o[iA] <= 0 or s.c[k] <= 0:
        return None, None
    gap = s.o[iA] / s.c[k] - 1
    if not (-0.5 < gap < 0.5) or gap >= UNFILLABLE_GAP:
        return None, None
    return s.o[iA + N] / s.o[iA] - 1, gi[iA]


def build_ctrl(stocks, pool):
    """对照组：同日、**同跳空档**、同池、昨日涨跌 ±1% 的平盘票。

    必须按跳空档再分层，否则「高开 1~2% 的票」被拿去和「无条件平盘票」比，
    跳空本身的溢价（或折价）会混进超额，测出来的不是「昨日大跌」的作用。
    对照按 (口径, N, 跳空档, 日) 聚合**原始**均值。
    """
    ctrl = {}
    for s, gi in zip(stocks, pool.gi):
        n = len(s.d)
        for k in range(WARM, n - max(HORIZONS) - 2):
            if not (s.c[k] > 0 and s.c[k - 1] > 0 and s.c[k - 2] > 0):
                continue
            d1 = s.c[k - 1] / s.c[k - 2] - 1
            if not (-CTRL_BAND <= d1 <= CTRL_BAND):
                continue
            if s.c[k] < MIN_PRICE or s.a[k] < MIN_AMOUNT:
                continue
            g = s.o[k] / s.c[k - 1] - 1 if s.o[k] > 0 else None
            gb = gap_bin(g)
            day = s.d[k]
            for mode, _ in MODES:
                if mode == 'gap' and gb is None:
                    continue
                for N in HORIZONS:
                    r, _ = fwd(s, gi, k, mode, N)
                    if r is None:
                        continue
                    ctrl.setdefault((mode, N, gb), {}).setdefault(day, []).append(r)
    return {k: {d: sum(v) / len(v) for d, v in m.items()} for k, m in ctrl.items()}


# 跳空档（9:25 集合竞价即知，无未来信息）
GAP_BINS = ((-9.9, -0.02, '低开>2%'), (-0.02, 0.0, '低开0~2%'),
            (0.0, 0.02, '高开0~2%'), (0.02, 9.9, '高开>2%'))
GAP_EDGES = (-0.02, 0.0, 0.02)


def gap_bin(g):
    if g is None:
        return None
    if g < GAP_EDGES[0]:
        return '低开>2%'
    if g < GAP_EDGES[1]:
        return '低开0~2%'
    if g < GAP_EDGES[2]:
        return '高开0~2%'
    return '高开>2%'


def _t_ci(vals):
    n = len(vals)
    if n < 3:
        return None
    m = sum(vals) / n
    sd = (sum((x - m) ** 2 for x in vals) / (n - 1)) ** 0.5
    if sd <= 0:
        return None
    return m / (sd / (n ** 0.5))


# ---------------------------------------------------------------- 扫信号
def scan(s, gi, pool, ctrl, drop=DROP, dry=None, hold_def=None, rel=False,
         gap_lo=None, gap_hi=None, gap_only=False, vol_lo=None, vol_hi=None):
    """扫信号。

    `gap_only=True` 时走「T 日开盘买」路线，**严格只用 T-1 及之前 + T 日开盘价**：
    昨日跌幅、昨日量比、今日跳空、以及 T-1 的收盘价/成交额（判断 T-1 是否可交易）。
    绝不能用 s.c[k] / s.l[k] / s.a[k]——那是 T 日收盘，9:30 还不知道。
    """
    n = len(s.d)
    out = []
    for k in range(WARM, n - max(HORIZONS) - 2):
        if not (s.c[k] > 0 and s.c[k - 1] > 0 and s.c[k - 2] > 0):
            continue
        d1 = s.c[k - 1] / s.c[k - 2] - 1
        if rel:
            j = gi[k - 1]
            m = pool.r_cc[j] if j < len(pool.r_cc) else None
            if m is None:
                continue
            d1 -= m
        if d1 > drop:
            continue
        need_vr = dry is not None or vol_lo is not None or vol_hi is not None
        vr = None
        if need_vr:
            base = s.a[k - 1 - VOL_LOOK:k - 1]
            if not base or statistics.mean(base) <= 0:
                continue
            vr = s.a[k - 1] / statistics.mean(base)
            if dry is not None and vr >= dry:
                continue
            if vol_lo is not None and vr < vol_lo:
                continue
            if vol_hi is not None and vr >= vol_hi:
                continue
        g = s.o[k] / s.c[k - 1] - 1 if (s.o[k] > 0 and s.c[k - 1] > 0) else None
        if gap_only:
            # T 日开盘买入：可交易性只能用 T-1 数据
            if s.c[k - 1] < MIN_PRICE or s.a[k - 1] < MIN_AMOUNT:
                continue
            if g is None or g >= UNFILLABLE_GAP:
                continue
            if gap_lo is not None and g < gap_lo:
                continue
            if gap_hi is not None and g >= gap_hi:
                continue
        else:
            if hold_def == 'low' and s.l[k] < s.l[k - 1]:
                continue
            if hold_def == 'close' and s.c[k] < s.c[k - 1]:
                continue
            if hold_def == 'hl' and (s.l[k] < s.l[k - 1] or s.c[k] < s.c[k - 1]):
                continue
            if s.c[k] < MIN_PRICE or s.a[k] < MIN_AMOUNT:
                continue
            if s.pct(k) * 100 > PCT_HI:
                continue
            if gap_lo is not None and (g is None or g < gap_lo):
                continue
            if gap_hi is not None and (g is None or g >= gap_hi):
                continue

        day = s.d[k]
        gb = gap_bin(g)
        ent = {}
        modes = ('gap',) if gap_only else ('tail', 'next')
        for mode in modes:
            for N in HORIZONS:
                r, _ = fwd(s, gi, k, mode, N)
                if r is None:
                    continue
                c = ctrl.get((mode, N, gb), {}).get(day)
                if c is None:
                    continue
                ent.setdefault(mode, {})[N] = (r - COST, r - COST - c)
        if ent:
            out.append({'code': s.code, 'day': day, 'k': k, 'ent': ent,
                        'drop': d1, 'gap': g, 'vr': vr})
    return out


def collect(stocks, pool, ctrl, **kw):
    rows = []
    for s, gi in zip(stocks, pool.gi):
        rows.extend(scan(s, gi, pool, ctrl, **kw))
    return rows


def stat(rows, mode, N):
    """统计块。

    ⚠️ 两套均值必须同时报，且**以按日聚类为主**：
    - `ex`：按笔汇总（每只票一票）。大跌日往往同时出 50~200 只票，这一天在汇总里
      被放大 200 倍权重 → 会被少数「暴跌反弹日」主导，天然偏乐观。
    - `ex_day`：先按信号日聚合成日均值，再对这些日均值求平均。**这是能代表
      「随机挑一天买」的数字**，t 值也必须和它同源，否则会出现
      「超额 +0.20% 却 t=−1.51」的自相矛盾（实测踩过）。
    """
    sub = [x for x in rows if x['ent'].get(mode, {}).get(N)]
    if len(sub) < 20:
        return None
    by_day = {}
    for x in sub:
        by_day.setdefault(x['day'], []).append(x['ent'][mode][N])
    de = [sum(v[1] for v in lst) / len(lst) for lst in by_day.values()]
    dr = [sum(v[0] for v in lst) / len(lst) for lst in by_day.values()]
    rs = sorted(x['ent'][mode][N][0] for x in sub)
    nn = len(rs)
    return {
        'n': nn, 'days': len(by_day),
        'avg': sum(rs) / nn,
        'avg_day': sum(dr) / len(dr),
        'med': rs[nn // 2] if nn % 2 else (rs[nn // 2 - 1] + rs[nn // 2]) / 2,
        'win': sum(1 for x in rs if x > 0) / nn,
        'ex': sum(x['ent'][mode][N][1] for x in sub) / nn,
        'ex_day': sum(de) / len(de),
        'win_ex': sum(1 for x in sub if x['ent'][mode][N][1] > 0) / nn,
        't': _t_ci(de),
        'per_day': nn / len(by_day),
    }


def fmt(st):
    if not st:
        return '样本不足'
    t = st['t']
    return ('n=%-5d 日=%-4d 均%+6.2f%% 中%+6.2f%% 胜%5.1f%% | 超额%+6.3f%% '
            '胜%5.1f%% t=%s 日均%.1f只'
            % (st['n'], st['days'], st['avg'] * 100, st['med'] * 100, st['win'] * 100,
               st['ex'] * 100, st['win_ex'] * 100,
               ('%+.2f' % t) if t is not None else ' n/a', st['per_day']))


def block(rows, title, modes=None):
    print('\n  %s' % title)
    for mode, mzh in MODES:
        if modes and mode not in modes:
            continue
        cells, ts = [], []
        for N in HORIZONS:
            st = stat(rows, mode, N)
            cells.append('%d:%s' % (N, ('%+.2f' % (st['ex_day'] * 100))
                                    if st else ' --'))
            ts.append(('t%+.2f' % st['t']) if (st and st['t'] is not None) else 't  n/a')
        print('    %-8s 日均超额%%  %s   %s'
              % (mzh, ' '.join('%-8s' % c for c in cells), ' '.join(ts)), flush=True)


# ---------------------------------------------------------------- 组合净值
def _daily(s, k, mode, N):
    """逐日收益链（不含成本）。"""
    out = []
    if mode == 'tail':
        for j in range(k, k + N):
            if s.c[j] <= 0 or s.c[j + 1] <= 0:
                return None
            out.append(s.c[j + 1] / s.c[j] - 1)
        return out
    if mode == 'gap':
        if s.o[k] <= 0 or s.c[k] <= 0:
            return None
        out.append(s.c[k] / s.o[k] - 1)
        for j in range(k + 1, k + N):
            if s.c[j - 1] <= 0 or s.c[j] <= 0:
                return None
            out.append(s.c[j] / s.c[j - 1] - 1)
        if s.c[k + N - 1] <= 0:
            return None
        out.append(s.c[k + N] / s.c[k + N - 1] - 1)
        return out
    iA = k + 1
    if s.o[iA] <= 0 or s.c[iA] <= 0:
        return None
    out.append(s.c[iA] / s.o[iA] - 1)
    for j in range(iA + 1, iA + N):
        if s.c[j - 1] <= 0 or s.c[j] <= 0:
            return None
        out.append(s.c[j] / s.c[j - 1] - 1)
    if s.c[iA + N - 1] <= 0:
        return None
    out.append(s.o[iA + N] / s.c[iA + N - 1] - 1)
    return out


def equity(rows, mode, N, pool, S, top=None):
    """逐日在仓等权满仓，逐日结算真实贡献。

    早期版本按「出场日一次性结算」把 5 只各亏 3% 叠乘成 −14%，跑出 −82% 的假回撤。
    必须逐日摊。
    """
    nd = len(pool.days)
    acc = [0.0] * nd
    cnt = [0] * nd
    by_day = {}
    for x in rows:
        if x['ent'].get(mode, {}).get(N):
            by_day.setdefault(x['day'], []).append(x)
    for day, lst in by_day.items():
        if top:
            lst = sorted(lst, key=lambda z: z['drop'])[:top]
        for x in lst:
            ds = _daily(S[x['code']], x['k'], mode, N)
            if not ds:
                continue
            g0 = pool.idx[day] + (0 if mode in ('tail', 'gap') else 1)
            for i, r in enumerate(ds):
                j = g0 + i
                if j >= nd:
                    break
                acc[j] += r - (COST if i == 0 else 0.0)
                cnt[j] += 1
    lv = [1.0]
    for j in range(1, nd):
        lv.append(lv[-1] * (1 + (acc[j] / cnt[j] if cnt[j] else 0.0)))
    return lv, cnt


def perf(lv, cnt):
    rets = [lv[i] / lv[i - 1] - 1 for i in range(1, len(lv))]
    tot = lv[-1] - 1
    yrs = len(rets) / 244.0
    cagr = (1 + tot) ** (1 / yrs) - 1 if yrs > 0 and tot > -1 else -1.0
    peak, mdd = 1.0, 0.0
    for v in lv:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    sd = statistics.pstdev(rets)
    sh = (statistics.mean(rets) / sd * 244 ** 0.5) if sd > 0 else 0.0
    busy = sum(1 for c in cnt if c)
    return {'tot': tot, 'cagr': cagr, 'mdd': mdd, 'sharpe': sh,
            'busy': busy / max(1, len(cnt)), 'avg_hold': sum(cnt) / max(1, busy)}


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--dump', action='store_true')
    a = ap.parse_args()

    print('[load] 读取 .kline_hist …', flush=True)
    stocks = load(a.limit or None)
    d0 = sorted({d for s in stocks for d in s.d})
    print('[load] %d 只主板（剔 ST），%s ~ %s 共 %d 个交易日'
          % (len(stocks), d0[0], d0[-1], len(d0)), flush=True)
    pool = Pool(stocks)
    S = {s.code: s for s in stocks}

    print('[ctrl] 对照组：同日同池、昨日涨跌 ±%.0f%% 的平盘票 …'
          % (CTRL_BAND * 100), flush=True)
    ctrl = build_ctrl(stocks, pool)
    print('[ctrl] 覆盖 %d 个交易日' % len(ctrl.get(('next', 5), {})), flush=True)

    out = {}

    # ---------------- 1) 形态主表
    print('\n===== 1) Q1 形态：昨日跌 < %.1f%% × 不破 × 缩量 =====' % (DROP * 100))
    print('    超额 = 本组扣成本收益 − 同日同池平盘票原始均值（已控市场/市值/流动性/波动）')
    for hv, hl in HOLD_DEFS:
        rows = collect(stocks, pool, ctrl, hold_def=hv)
        out['abs_' + hv] = rows
        block(rows, hl)
    print('\n  -- 叠加「昨日量比 < %.2f」（缩量下跌，非放量出货）--' % DRY)
    for hv, hl in HOLD_DEFS:
        rows = collect(stocks, pool, ctrl, hold_def=hv, dry=DRY)
        out['dry_' + hv] = rows
        block(rows, hl)

    # ---------------- 2) 相对跌幅
    print('\n===== 2) 昨**相对**跌幅（个股跌幅 − 全市场均值）< %.1f%% =====' % (DROP * 100))
    for hv, hl in (('none', '只看相对跌'), ('hl', '相对跌 + 不破 + 收昨收上')):
        rows = collect(stocks, pool, ctrl, hold_def=hv, rel=True)
        out['rel_' + hv] = rows
        block(rows, hl)
        rows = collect(stocks, pool, ctrl, hold_def=hv, rel=True, dry=DRY)
        out['rel_dry_' + hv] = rows
        block(rows, hl + '（+缩量）')

    # ---------------- 3) Q2 隔夜外盘 → 今日跳空
    print('\n===== 3) Q2 隔夜外盘映射：昨日大跌 + 缩量，9:25 看跳空决定买不买 =====')
    print('    口径 gap：只用 T-1 及之前 + T 日开盘价（9:25 集合竞价即知），'
          'T 日开盘买、T+N 收盘卖')
    print('    超额 = 减去**同跳空档**的平盘票，跳空本身的溢价已被剔除')
    gaps = ((-9.9, -0.03, '低开>3%'), (-0.03, -0.01, '低开1~3%'),
            (-0.01, 0.0, '低开0~1%'), (0.0, 0.01, '高开0~1%'),
            (0.01, 0.02, '高开1~2%'), (0.02, 0.03, '高开2~3%'),
            (0.03, 9.9, '高开>3%'))
    print('  %-11s %-7s %-10s %-10s %-10s %-8s' %
          ('跳空档', 'n', 'N=1超额', 'N=3超额', 'N=5超额', 'N=5胜'))
    for lo, hi, lbl in gaps:
        rows = collect(stocks, pool, ctrl, dry=DRY, gap_only=True,
                       gap_lo=lo, gap_hi=hi)
        out['gap_' + lbl] = rows
        cells = []
        for N in (1, 3, 5):
            st = stat(rows, 'gap', N)
            cells.append(('%+.3f%%' % (st['ex_day'] * 100)) if st else '  --  ')
        s1 = stat(rows, 'gap', 1)
        s5 = stat(rows, 'gap', 5)
        print('  %-11s %-7s %s %-8s'
              % (lbl, (s1 or {}).get('n', '-'), '  '.join('%-10s' % c for c in cells),
                 ('%.1f%%' % (s5['win'] * 100)) if s5 else '-'), flush=True)
    print('\n  不加跳空档、只看「大跌+缩量」（整池）作对照：')
    rows = collect(stocks, pool, ctrl, dry=DRY, gap_only=True)
    out['gap_全档'] = rows
    print('  %-11s %-7s %-10s %-10s %-10s'
          % ('全档', (stat(rows, 'gap', 1) or {}).get('n', '-'),
             '%+.3f%%' % (stat(rows, 'gap', 1)['ex_day'] * 100),
             '%+.3f%%' % (stat(rows, 'gap', 3)['ex_day'] * 100),
             '%+.3f%%' % (stat(rows, 'gap', 5)['ex_day'] * 100)))

    # ---------------- 4) 二维
    print('\n===== 4) 二维：跌幅 × 量比（不破+收昨收上，T+1开盘 N=1）=====')
    drops = (-0.02, -0.03, -0.04, -0.05, -0.07)
    drys = (0.55, 0.70, 0.85, 1.00, 1.40, 9.9)
    hdr = ['量比<%.2f' % d for d in drys[:-1]] + ['不限']
    print('  跌幅 \\ 量比   ' + '  '.join('%-15s' % h for h in hdr))
    for dd in drops:
        cells = []
        for dv in drys:
            st = stat(collect(stocks, pool, ctrl, hold_def='hl',
                              dry=dv if dv < 9 else None, drop=dd), 'next', 1)
            cells.append(('%d/%+.2f%%' % (st['n'], st['ex_day'] * 100)) if st else '--')
        print('  跌<%4.1f%%      %s' % (dd * 100, '  '.join('%-15s' % c for c in cells)),
              flush=True)

    # ---------------- 4b) 量比五档 × 跌幅（反向验证「缩量更好」）
    print('\n===== 4b) 量比五档 × 跌幅门槛：缩量真的更好吗？=====')
    print('  预期（用户直觉）：缩量=惜售，应该更好。实测：极度缩量最差。')
    print('  %-16s %-7s %-9s %-9s %-8s %-8s' % ('量比档', 'n', '均值', '中位', '超额', 't'))
    bands = ((0.0, 0.5, '<0.5 极度缩量'), (0.5, 0.7, '0.5~0.7'),
             (0.7, 1.0, '0.7~1.0'), (1.0, 1.5, '1.0~1.5'),
             (1.5, 99.0, '>=1.5 显著放量'))
    for dd in (-0.025, -0.04, -0.06):
        print('\n  昨日跌幅 < %.1f%%：' % (dd * 100))
        for lo, hi, lbl in bands:
            rows = collect(stocks, pool, ctrl, hold_def='hl', drop=dd,
                           vol_lo=lo, vol_hi=hi)
            st = stat(rows, 'next', 5)
            if not st:
                print('    %-16s 样本不足' % lbl)
                continue
            print('    %-16s %-7d %+8.2f%% %+8.2f%% %+7.3f%% %s'
                  % (lbl, st['n'], st['avg'] * 100, st['med'] * 100,
                     st['ex_day'] * 100,
                     ('%+.2f' % st['t']) if st['t'] is not None else ' n/a'),
                  flush=True)

    # ---------------- 5) 分年
    print('\n===== 5) 分年（跌<-2.5%% + 不破 + 收昨收上 + 缩量<%.2f）=====' % DRY)
    best = out['dry_hl']
    for y in sorted({x['day'][:4] for x in best}):
        sub = [x for x in best if x['day'][:4] == y]
        print('  %s' % y)
        for N in (1, 5):
            for mode, mzh in (('tail', 'T日尾盘'), ('next', 'T+1开盘')):
                print('    N=%-2d %-8s %s' % (N, mzh, fmt(stat(sub, mode, N))), flush=True)

    # ---------------- 6) 容量
    print('\n===== 6) 池子容量 =====')
    for key, hl in (('abs_none', '只看跌'), ('dry_none', '跌+缩量'),
                    ('dry_hl', '跌+不破+缩量'), ('gap_高开1~2%', '跌+缩量+高开1~2%'),
                    ('gap_全档', '跌+缩量（开盘买，全跳空档）')):
        if key not in out:
            continue
        cnt = {}
        for x in out[key]:
            cnt[x['day']] = cnt.get(x['day'], 0) + 1
        vs = sorted(cnt.values())
        nn = len(vs)
        nd = len(pool.days)
        print('  %-16s 有信号日 %d/%d（%.1f%%） 每日中位 %d / 90分位 %d / 最大 %d  共 %d 笔'
              % (hl, nn, nd, nn / nd * 100, vs[nn // 2], vs[int(nn * 0.9)], vs[-1],
                 sum(vs)))

    # ---------------- 7) 组合层
    print('\n===== 7) 组合层（每日在仓等权满仓，含成本，逐日结算）=====')
    print('    注意：整池日均几十到几百只，等权满仓不现实；看「年化/回撤/夏普」的')
    print('    相对关系，不要当成可执行策略。真实执行需先按跌幅排序取前 N 只。')
    for key, hl in (('dry_none', '跌+缩量'), ('dry_hl', '跌+不破+缩量'),
                    ('gap_全档', '跌+缩量+开盘买')):
        if key not in out:
            continue
        modes = ('gap',) if key.startswith('gap_') else ('tail', 'next')
        for mode, mzh in MODES:
            if mode not in modes:
                continue
            for N in (1, 5, 10):
                lv, cnt = equity(out[key], mode, N, pool, S)
                p = perf(lv, cnt)
                print('  %-16s %-8s N=%-2d 累计%+8.1f%% 年化%+6.1f%% 回撤%6.1f%% '
                      '夏普%5.2f 在仓%3.0f%% 均持%4.1f只'
                      % (hl, mzh, N, p['tot'] * 100, p['cagr'] * 100, p['mdd'] * 100,
                         p['sharpe'], p['busy'] * 100, p['avg_hold']), flush=True)
    print('\n  -- 加排序约束：每日只买跌幅最深的 3 只（才有可执行性）--')
    for key, hl in (('dry_hl', '跌+不破+缩量'), ('gap_全档', '跌+缩量+开盘买')):
        if key not in out:
            continue
        modes = ('gap',) if key.startswith('gap_') else ('next',)
        for mode in modes:
            for N in (1, 5, 10):
                lv, cnt = equity(out[key], mode, N, pool, S, top=3)
                p = perf(lv, cnt)
                print('  %-16s %-8s N=%-2d 累计%+8.1f%% 年化%+6.1f%% 回撤%6.1f%% '
                      '夏普%5.2f 在仓%3.0f%% 均持%4.1f只'
                      % (hl, mode, N, p['tot'] * 100, p['cagr'] * 100, p['mdd'] * 100,
                         p['sharpe'], p['busy'] * 100, p['avg_hold']), flush=True)

    # ---------------- 8) 剂量-反应自检
    print('\n===== 8) 剂量-反应：跌幅梯度（不破+收昨收上，T+1开盘 N=5）=====')
    print('  若「跌得越深超额越高」单调成立，说明是真形态；若乱序，前面全是噪音。')
    print('  均值正而中位负 = 「少数暴涨 + 多数阴跌」的彩票型分布，不是稳健超额。')
    ladder = (0.0, -0.02, -0.04, -0.06, -0.08, -0.095)
    print('  %-12s %-7s %-10s %-9s %-9s %-8s'
          % ('昨日跌幅', 'n', '日均超额', '均值', '中位', 't'))
    for dd in ladder:
        rows = collect(stocks, pool, ctrl, hold_def='hl', drop=dd)
        st = stat(rows, 'next', 5)
        if not st:
            print('  %-12s 样本不足' % ('<%+.1f%%' % (dd * 100)))
            continue
        print('  %-12s %-7d %+9.3f%% %+8.2f%% %+8.2f%% %s'
              % ('<%+.1f%%' % (dd * 100), st['n'], st['ex_day'] * 100,
                 st['avg'] * 100, st['med'] * 100,
                 ('%+.2f' % st['t']) if st['t'] is not None else ' n/a'), flush=True)

    # ---------------- 9) 全样本分档（地基）
    print('\n===== 9) 底层事实：昨日收益 × 未来 5 日（不筛形态，全体可交易票）=====')
    print('  这一节是前面所有结论的地基，回答「A 股到底有没有跌深必反弹」。')
    buck = {}
    for s, gi in zip(stocks, pool.gi):
        n = len(s.d)
        for k in range(WARM, n - 6):
            if not (s.c[k] > 0 and s.c[k - 1] > 0 and s.c[k - 2] > 0):
                continue
            if s.c[k] < MIN_PRICE or s.a[k] < MIN_AMOUNT:
                continue
            iA = k + 1
            if iA + 5 >= n or s.o[iA] <= 0:
                continue
            gap = s.o[iA] / s.c[k] - 1
            if not (-0.5 < gap < 0.5) or gap >= UNFILLABLE_GAP:
                continue
            d1 = s.c[k - 1] / s.c[k - 2] - 1
            b = max(0, min(9, int((d1 + 0.10) / 0.02)))
            buck.setdefault(b, []).append(s.o[iA + 5] / s.o[iA] - 1 - COST)
    print('  %-15s %-8s %-9s %-9s %-9s' % ('昨日收益档', 'n', '均值', '中位', '胜率'))
    for b in sorted(buck):
        v = buck[b]
        if len(v) < 500:
            continue
        rs = sorted(v)
        lo = -0.10 + b * 0.02
        print('  [%+5.1f%%,%+5.1f%%)  %-8d %+8.3f%% %+8.3f%% %7.1f%%'
              % (lo * 100, (lo + 0.02) * 100, len(rs),
                 sum(rs) / len(rs) * 100, rs[len(rs) // 2] * 100,
                 sum(1 for x in rs if x > 0) / len(rs) * 100), flush=True)
    print('  → 均值随跌幅加深而抬升，中位数却始终为负：跌深反弹是**右尾**事件，')
    print('    多数样本仍在亏，靠它做池子必须解决「怎么挑出那少数」和「怎么砍掉多数」。')

    if a.dump:
        # 只落汇总统计，不落逐笔明细：明细 20 组 × 上万笔 ≈ 259MB，
        # 既进不了公开仓库（已在 .gitignore），留着也只会让下次读盘变慢。
        summ = {'meta': {'stocks': len(stocks), 'days': len(pool.days),
                         'start': d0[0], 'end': d0[-1], 'cost': COST,
                         'sample_drop': DROP, 'sample_dry': DRY},
                'groups': {}}
        for k, rows in out.items():
            g = {}
            for mode, _ in MODES:
                for N in HORIZONS:
                    st = stat(rows, mode, N)
                    if st:
                        g['%s_%d' % (mode, N)] = {kk: st[kk] for kk in
                                                  ('n', 'days', 'avg', 'med', 'win',
                                                   'ex', 'ex_day', 't')}
            summ['groups'][k] = g
        p = os.path.join(BASE, 'goldpit_study.json')
        with open(p + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(summ, f, ensure_ascii=False, separators=(',', ':'))
        os.replace(p + '.tmp', p)
        print('\n[dump] %s（%d 组汇总）' % (p, len(summ['groups'])))


if __name__ == '__main__':
    main()

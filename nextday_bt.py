#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nextday_bt.py —— 断板反包 / 陈学长标的：涨停→倍量断板→T+2尾盘买→T+3卖，年化。

用户口径（2026-11-02 12:2x）：
    T 日涨停 → T+1 倍量断板（阴线）→ T+2 尾盘(收盘)买 → T+3 卖。
    问：两套技术池（断板反包 / 陈学长）下，这个短线持股 1 日的年化率是多少。

## 信号定义（两套 universe，只取 60/00 主板，剔 ST/*/退/PT）

| 项 | 断板反包 | 陈学长 |
|---|---|---|
| T 涨停 | 涨幅 ≥ 9.7% | 涨幅 ≥ 9.8% |
| T+1 量比 V(T+1)/V(T) | ∈[1.1, 3.5] 且 T+1 未涨停 | ≥ 2.0 且 T+1 未涨停 |
| T+2 缩量 | V(T+2) < V(T+1) 且收盘不破 T 日低点×0.98 | V(T+2) < V(T+1) 且收盘不破 T 日低点×0.98 |

每只票每个涨停只取一笔（第一次出现该涨停日）。

## 买卖口径（★无未来函数）

- **买**：T+2 **收盘价**（`st.c[T+2]`）。T+2 收盘时 T/T+1/T+2 三根均已收盘，缩量/不破低点可确认。
  若 T+2 一字涨停（open=high=low=涨停价）→ 尾盘买不进，剔除。
- **卖**（`--sell` 三选一，卖出价语义不同，年化差很多，务必分清）：
  | mode | 卖出价 | 性质 |
  |---|---|---|
  | `highest` | T+3 盘中**最高** | **理论上限**——现实中挂条件单成交 ≤ 最高，拿不到 |
  | `close` | T+3 **收盘** | **可执行下限**——T+3 收盘全平，不追价 |
  | `limit`（默认） | T+3 开盘若 ≥ 买价+0.5% 按开盘，否则盘中摸到 +0.5% 按 +0.5% | **可执行**——触发价单（盘中触价才走），最接近「冲高止盈」的实际 |
- **不要求站上 MA5**：用户口径只看涨停→倍量断板→缩量三天，未提 MA5 确认，故不用。
- 持有 1 个交易日，成本双边 0.16%（与全项目一致）。

## 为什么 T+3 卖出不算未来函数

买入时点（T+2 收盘）确定、信号只用 T+2 及之前数据；T+3 卖出是**持仓一整天后**的
离场模拟，不是用未来信息决定买卖点。与项目里 `sim()` 的止盈触价即走同一性质。
但 `highest` 是**事后理想价**（成交价 ≤ 最高），报告里明说是上限；`close`/`limit` 才可执行。

## 年化口径

复用 `duanban_mcap_bt` 组合层：`equity_slot_avg`（可执行，每晚 8 槽、每笔 1/8，随机抽槽 DRAWS 次）。
**年化只对 `limit`/`close` 有意义**；`highest` 的组合年化是复利幻觉（每笔 +2.9% × 天天满仓），
只报单笔不报年化。成本：买/卖两端各扣一半。

用法：
    python3 nextday_bt.py                 # 三档卖出全打（highest 单笔 / limit 年化 / close 年化）
    python3 nextday_bt.py --sell limit    # 只打 limit 档
    python3 nextday_bt.py --quick         # 只打单笔
"""
import argparse
import os
import statistics
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import nshape_backtest as NB
import duanban_mcap_bt as DB   # 复用 _metrics / equity_slot / equity_full

COST = 0.0016
SPLIT = '2026-01-01'
LIMIT_PCT = 0.005   # limit 档的触发幅度：T+3 开盘 ≥ 买价+0.5% 按开盘，否则盘中摸 +0.5% 走

# 单笔：买入 T+2 收盘，卖出 T+3 盘中最高。hold 给 2 让 _seq 覆盖 T+2~T+3。
BO = 2          # 买日在 T 后的偏移
HOLD = 2        # 序列覆盖 [T+2, T+3]


def is_st_name(name):
    n = (name or '').upper()
    return ('ST' in n) or n.startswith('*') or ('退' in n) or ('PT' in n)


def collect(stocks, mode, sell='limit'):
    """扫出两套 universe 的「涨停→倍量断板→T+2缩量」样本。返回 [{st,iT,bo,bx,px,hold,code,name}]。

    sell ∈ {highest, close, limit}，决定卖出价 px（见模块 docstring）。
    """
    if mode == 'duanban':
        LP, V_LO, V_HI = 0.097, 1.1, 3.5
    else:  # chen
        LP, V_LO, V_HI = 0.098, 2.0, None
    out = []
    for st in stocks:
        if not st.code.startswith(('60', '00')):
            continue
        if is_st_name(st.name):
            continue
        n = len(st.c)
        if n < 120:
            continue
        iT = 30
        while iT < n - 4:
            # T 涨停 + 停牌保护
            if st.pct(iT) < LP or not st.gap_ok(iT):
                iT += 1
                continue
            vT = st.v[iT]
            if vT <= 0:
                iT += 1
                continue
            i1 = iT + 1
            r1 = st.v[i1] / vT if vT else 0
            # T+1 倍量断板（未涨停）
            if st.pct(i1) >= LP or r1 < V_LO or (V_HI and r1 > V_HI):
                iT += 1
                continue
            i2 = iT + 2
            i3 = iT + 3
            if i3 >= n:
                break
            # T+2 缩量 + 不破 T 日低点×0.98
            lowT = st.l[iT]
            if lowT <= 0:
                iT += 1
                continue
            if not (0 < st.v[i2] < st.v[i1]):
                iT += 1
                continue
            if st.c[i2] < lowT * 0.98:
                iT += 1
                continue
            # 买入价 T+2 收盘；若 T+2 一字涨停买不进
            buy = st.c[i2]
            if buy <= 0:
                iT += 1
                continue
            _, h2, l2 = st.o[i2], st.h[i2], st.l[i2]
            if l2 == h2 and st.pct(i2) > 0.09:   # 一字涨停
                iT = i2 + 1
                continue
            # 卖出价按 sell 档：highest=盘中最高 / close=收盘 / limit=开盘跳高则开盘、否则盘中摸+0.5%
            o3, h3, l3, c3 = st.o[i3], st.h[i3], st.l[i3], st.c[i3]
            if sell == 'highest':
                sell_px = h3
                if l3 == h3 and st.pct(i3) < -0.09:   # T+3 跌停一字，卖不出
                    iT = i2 + 1
                    continue
            elif sell == 'close':
                sell_px = c3
            else:  # limit：真实触发价单——开盘≥target按开盘成交；盘中摸到target按target；
                #        全天没到target则单没成交、尾盘收盘平仓（不是「假装摸到了+0.5%」）
                target = buy * (1 + LIMIT_PCT)
                if o3 >= target:
                    sell_px = o3            # 开盘即达标，开盘成交
                elif h3 >= target:
                    sell_px = target        # 盘中摸到，触价即走
                else:
                    sell_px = c3            # 全天没到，收盘平仓（持到收盘）
            if sell_px <= 0:
                iT = i2 + 1
                continue
            out.append(dict(st=st, iT=iT, bo=BO, bx=buy, px=sell_px, hold=HOLD,
                            code=st.code, name=st.name))
            iT = i2 + 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--sell', default='all',
                    help='卖出档: all(默认三档全打) / highest / close / limit')
    a = ap.parse_args()

    stocks = NB.load_all()
    print('[load] %d 只' % len(stocks), flush=True)

    cal = sorted({d for s in stocks for d in s.d})
    print('[cal] 区间 %s ~ %s，%d 个交易日' % (cal[0], cal[-1], len(cal)), flush=True)

    # 三档卖出的属性
    SELL_META = {
        'highest': 'T+3盘中最高（理论上限，单笔可看，年化是复利幻觉不报）',
        'limit':   'T+3开盘跳高或盘中摸+0.5%（可执行·触发价单）',
        'close':   'T+3收盘（可执行·不追价，保守下限）',
    }
    sells = [a.sell] if a.sell != 'all' else ['highest', 'limit', 'close']

    for mode, zh in (('duanban', '断板反包'), ('chen', '陈学长')):
        print('\n================ %s（涨停→倍量断板→T+2尾盘买→T+3卖）================' % zh)
        for sell in sells:
            smps = collect(stocks, mode, sell)
            if not smps:
                print('  [%s] 无信号' % sell)
                continue
            print('\n  --- 卖出档 %s：%s ---' % (sell, SELL_META[sell]))
            print('  信号 %d 笔' % len(smps))

            rets = []
            for r in smps:
                s = DB._seq(dict(r))
                if s:
                    rets.append(DB._compound(s[2]))
            if not rets:
                continue
            win = 100 * sum(1 for x in rets if x > 0) / len(rets)
            avg = sum(rets) / len(rets)
            med = statistics.median(rets)
            tr_ = [(r['st'].d[r['iT']], DB._compound(DB._seq(dict(r))[2])) for r in smps if DB._seq(dict(r))]
            train = [x for d, x in tr_ if d < SPLIT]
            test = [x for d, x in tr_ if d >= SPLIT]

            def seg(sub):
                if not sub:
                    return (0, 0, 0)
                return (len(sub), sum(sub) / len(sub), 100 * sum(1 for x in sub if x > 0) / len(sub))

            nt, mt, wt = seg(train)
            ne, me, we = seg(test)
            print('  单笔: %d 笔  胜率 %.1f%%  均 %+.3f%%  中位 %+.3f%%  最大 %+.1f%%  最小 %+.1f%%'
                  % (len(rets), win, avg, med, max(rets), min(rets)))
            print('  分段: 训练(<2026) %d 笔 %+.3f%%/%d%%  | 样本外(2026+) %d 笔 %+.3f%%/%d%%'
                  % (nt, mt, wt, ne, me, we))

            # 年化只有 limit/close 有真实意义；highest 是复利幻觉（每笔 +2.9% × 天天满仓）
            if sell in ('limit', 'close'):
                rows = [dict(s) for s in smps]
                slot = DB.equity_slot_avg(rows, cal)
                print('  8槽可执行年化: %+.2f%%  | 累计 %+.2f%%  | 最大回撤 %.1f%%  | 夏普 %.2f'
                      % (slot['ann'] * 100, slot['total'] * 100, slot['mdd'] * 100, slot['sharpe']))
                print('  年化 10/90 分位 [%+.2f%%, %+.2f%%]  | 占仓率 %.0f%%  | 成交 %d 笔(可用 %d)'
                      % (slot['ann_lo'] * 100, slot['ann_hi'] * 100, slot['occ'] * 100,
                         slot['taken'], slot['n_avail']))
            else:
                print('  年化不报：%s 档单笔 +%.2f%% 是「卖在最高」的理想值，'
                      '8槽满仓天天 +%.2f%% 复利放大成天文数字，不代表可执行年化。'
                      % (sell, avg, avg))

        print()


if __name__ == '__main__':
    sys.exit(main())
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新口径回测（2026-11-28）：用这半个月学到的判据重跑断板反包 / 陈学长。

与旧 `duanban_study` 的差别（都是用户这两周亲自纠正的口径）：
  ① **不再要求「距 MA5 ≥+2%」**（11-26 取消）；MA5 只作「已脱离 / 待启动」标签
  ② 断板量比**不否决**：<1.1（缩量断板）/ 1.1~3.5 / 3.5~8（巨量）/ ≥8 全收，分档看
  ③ 洗盘天数分 1 日 / 2~4 日 / ≥5 日（台账显示 ≥5 日只有 6%）
  ④ 最低量比 <50% / 50~70% / ≥70%（台账显示缩太狠反而差）
  ⑤ 破不破 T 日低点单独成桶
  ⑥ 「反包成功」按用户定义：**买入后 10 个交易日内出现 ≥+7%**（含未封板）
  ⑦ 陈学长单独一组：首板→次日倍量断板→1~3 日缩量洗盘→站上 MA5，滞后 ≤3 日

输出：各桶样本数 / 反包率 / 10 日均最高涨幅 / 5 日 TP3% 胜率。
"""
import argparse
import sys
from collections import defaultdict

import nshape_backtest as NB

MAIN = ("60", "00")


def ma(st, s, n=5):
    return sum(st.c[s - n + 1:s + 1]) / n


def scan(stocks, only_main=True):
    rows = []
    for st in stocks:
        if only_main and not st.code.startswith(MAIN):
            continue
        n = len(st.c)
        if n < 80:
            continue
        for iT in range(10, n - 12):
            if st.pct(iT) < 0.097:
                continue
            vT = st.v[iT]
            if vT <= 0:
                continue
            i1 = iT + 1
            if i1 >= n - 1:
                break
            if st.pct(i1) >= 0.097:                     # 次日仍涨停 → 不是断板
                continue
            r1 = st.v[i1] / vT
            lowT = st.l[iT]
            if lowT <= 0:
                continue
            # 洗盘 1~5 日：每天必须缩量；记录最低量比、是否破前低
            wash = []
            okrun = True
            for d in range(2, 7):
                s = iT + d
                if s >= n - 1:
                    okrun = False
                    break
                if not (0 < st.v[s] < st.v[i1]):        # 当日量小于断板日
                    okrun = False
                    break
                wash.append(s)
                # 每个洗盘日都产生一个「若今天买」的样本（对应不同洗盘天数）
                low_seen = min(st.c[iT + 1:s + 1])
                m5 = ma(st, s)
                rows.append({
                    "code": st.code, "iT": iT, "s": s, "d": d,
                    "r1": round(r1, 2),
                    "minVolPct": round(min(st.v[iT + 2:s + 1]) / st.v[i1] * 100),
                    "brokePreLow": bool(low_seen < lowT * 0.995),
                    "ma5gap": round((st.c[s] / m5 - 1) * 100, 2) if m5 else None,
                    "st": st,                             # 引用，后面算前瞻
                })
                if not okrun:
                    break
    return rows


def forward(st, s, days=10):
    n = len(st.c)
    b = s + 1
    if b >= n:
        return None
    buy = st.o[b]
    if not buy or buy <= 0:
        return None
    hi = max(st.c[b:min(n, b + days)])
    lo = min(st.c[b:min(n, b + days)])
    # TP3% / 止损 -5%，持有 5 日（与旧口径一致，便于对比）
    tp = buy * 1.03
    sl = buy * 0.95
    ret = None
    for j in range(b, min(n, b + 5)):
        if st.h[j] >= tp:
            ret = 0.03
            break
        if st.l[j] <= sl:
            ret = -0.05
            break
    if ret is None:
        ret = (st.c[min(n - 1, b + 4)] / buy - 1)
    return {"rebound": bool(hi >= buy * 1.07), "hiPct": round((hi / buy - 1) * 100, 1),
            "loPct": round((lo / buy - 1) * 100, 1), "ret": round(ret * 100, 2)}


def agg(rows, keyfn, title, cuts=None):
    g = defaultdict(lambda: {"n": 0, "reb": 0, "hi": [], "ret": []})
    for r in rows:
        f = forward(r["st"], r["s"])
        if not f:
            continue
        k = keyfn(r)
        b = g[k]
        b["n"] += 1
        b["reb"] += 1 if f["rebound"] else 0
        b["hi"].append(f["hiPct"])
        b["ret"].append(f["ret"])
    print("\n[%s]" % title)
    order = cuts if cuts else sorted(g)
    for k in order:
        if k not in g:
            continue
        b = g[k]
        if not b["n"]:
            continue
        print("  %-14s n=%5d  反包率 %5.1f%%  均最高 %+5.1f%%  TP3%%胜率 %5.1f%%"
              % (k, b["n"], b["reb"] / b["n"] * 100, sum(b["hi"]) / b["n"],
                 sum(1 for x in b["ret"] if x > 0) / b["n"] * 100))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    stocks = NB.load_all()
    if a.limit:
        stocks = stocks[:a.limit]
    print("[load] %d 只" % len(stocks), flush=True)
    rows = scan(stocks)
    print("[scan] 样本 %d 条" % len(rows), flush=True)
    if not rows:
        return 0

    agg(rows, lambda r: "全部", "基线（全部样本）", ["全部"])
    agg(rows, lambda r: "%d 日" % r["d"], "洗盘天数",
        ["1 日", "2 日", "3 日", "4 日", "5 日"])
    agg(rows, lambda r: ("<1.1×" if r["r1"] < 1.1 else
                         "1.1~3.5×" if r["r1"] < 3.5 else
                         "3.5~8×" if r["r1"] < 8 else "≥8×"), "断板量比",
        ["<1.1×", "1.1~3.5×", "3.5~8×", "≥8×"])
    agg(rows, lambda r: ("<50%" if r["minVolPct"] < 50 else
                         "50~70%" if r["minVolPct"] < 70 else "≥70%"), "洗盘期最低量比",
        ["<50%", "50~70%", "≥70%"])
    agg(rows, lambda r: "破前低" if r["brokePreLow"] else "未破前低", "是否破 T 日低点",
        ["未破前低", "破前低"])
    agg(rows, lambda r: ("已脱离MA5" if (r["ma5gap"] or 0) >= 2 else "待启动"), "距 MA5（旧门槛，仅作标签）",
        ["已脱离MA5", "待启动"])

    # 组合：最低量≥70% + 距MA5≥2 + 洗≤2 日（三条最强因子叠加）
    combo = [r for r in rows if r["minVolPct"] >= 70 and (r["ma5gap"] or 0) >= 2 and r["d"] <= 2]
    agg(combo, lambda r: "三因子叠加", "组合（最低量≥70% + 距MA5≥2 + 洗≤2日）", ["三因子叠加"])
    combo2 = [r for r in rows if r["minVolPct"] >= 70 and (r["ma5gap"] or 0) >= 2]
    agg(combo2, lambda r: "双因子", "组合（最低量≥70% + 距MA5≥2）", ["双因子"])

    # 陈学长：洗 1~3 日 + 站上 MA5 + 断板量比 ≥1.5（倍量）
    chen = [r for r in rows if r["d"] <= 3 and (r["ma5gap"] or 0) >= 0 and r["r1"] >= 1.5]
    agg(chen, lambda r: "陈学长口径", "陈学长（洗≤3日·倍量·站上MA5）", ["陈学长口径"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""edge_study.py — 用**自己的台账**做胜率/期望归因，回答「这套规则哪里在亏钱」。

## 为什么必须有它（2026-11-20 用户：「找出提升胜率的办法」）

台账 202 条结算、196 笔执行，**胜率 62% 却累计 −217.8%**：中位数正好 +5%（止盈封顶），
而最大亏 −29.6%（止损位离买价太远）。这就是「胜率高 ≠ 赚钱」的全部原因。
这个脚本把原因拆成可复核的数字，并给出几种出场/过滤口径的对照，避免凭印象改规则。

## 它算什么

1. **基础面**：胜率 / 均值 / 合计 / 最大亏 / 盈亏比，按池、按口径、按周。
2. **风险归因**：初始风险 R=(买价−止损位)/买价 → 分桶看期望（R 越大，均亏越大）。
3. **出场对照**：现行（形态止损 + 止盈 5%）vs 硬止损 −5% vs 不止盈持 5 日 …
4. **入场因子**：竞价溢价 / 买价距 MA5 / 前 5 日涨幅 / 前 20 日涨幅 / 涨停日量比 / 断板日量比 / 价格带。

K 线取 data.js 顶层 `stkKlines`（90 根），所以只能复算仍在窗口内的样本；
算不了的样本会被排除并计入 `n` 的差。

## 用法
    python3 edge_study.py            # 全部报段
    python3 edge_study.py --brief    # 只看风险归因 + 出场对照
"""
import argparse
import json
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))


def load():
    D = json.loads(open(os.path.join(HERE, "data.js"), encoding="utf-8").read().split("=", 1)[1].rstrip().rstrip(";"))
    L = json.load(open(os.path.join(HERE, "chen_ledger.json"), encoding="utf-8"))["entries"]
    return D, L


def res(e):
    return e.get("result") or {}


def bars(D, code):
    return (D.get("stkKlines") or {}).get(str(code).zfill(6)) or []


def replay(D, e, stop_pct=None, tgt_pct=None, hold=None):
    """按给定出场口径复算这笔的收益（同日先触止损＝悲观假设）。"""
    b = bars(D, e["code"]); r = res(e)
    bd, bp = r.get("buyDay"), r.get("buyPrice")
    if not b or not bd or not bp:
        return None
    idx = next((i for i, x in enumerate(b) if str(x[0])[:10] == bd), -1)
    if idx < 0:
        return None
    sp = bp * (1 - stop_pct / 100.0) if stop_pct is not None else e.get("stopPrice")
    tp = bp * (1 + tgt_pct / 100.0) if tgt_pct is not None else bp * (1 + (e.get("tgtPct") or 5) / 100.0)
    hd = hold if hold is not None else (e.get("holdDays") or 10)
    if not sp:
        return None
    for j in range(idx + 1, min(idx + hd, len(b))):
        lo, hi = float(b[j][4]), float(b[j][3])
        if lo <= sp:
            return round((sp / bp - 1) * 100, 2)
        if hi >= tp:
            return round((tp / bp - 1) * 100, 2)
    j = min(idx + hd, len(b)) - 1
    return round((float(b[j][2]) / bp - 1) * 100, 2) if j > idx else None


def feats(D, e):
    """入场那一刻能看到的因子（全部只用 buyDay 及之前的数据，无未来函数）。"""
    b = bars(D, e["code"]); r = res(e)
    bd, bp = r.get("buyDay"), r.get("buyPrice")
    if not b or not bd or not bp:
        return None
    idx = next((i for i, x in enumerate(b) if str(x[0])[:10] == bd), -1)
    if idx < 1:
        return None
    gap = (float(b[idx][1]) / float(b[idx - 1][2]) - 1) * 100
    closes = [float(x[2]) for x in b[max(0, idx - 5):idx]]
    ma5 = sum(closes) / len(closes) if closes else bp
    ret5 = (float(b[idx - 1][2]) / float(b[max(0, idx - 6)][2]) - 1) * 100 if idx >= 6 else 0.0
    ret20 = (float(b[idx - 1][2]) / float(b[max(0, idx - 21)][2]) - 1) * 100 if idx >= 21 else 0.0
    zt = next((j for j in range(idx - 1, max(0, idx - 12), -1)
               if float(b[j][2]) / float(b[j - 1][2]) - 1 > 0.09), None)
    volr = br = None
    if zt:
        base = [float(x[5]) for x in b[max(0, zt - 5):zt]]
        if base and sum(base) > 0:
            volr = float(b[zt][5]) / (sum(base) / len(base))
        if zt + 1 < len(b):
            base2 = [float(x[5]) for x in b[max(0, zt - 4):zt + 1]]
            if base2 and sum(base2) > 0:
                br = float(b[zt + 1][5]) / (sum(base2) / len(base2))
    sp = e.get("stopPrice")
    return dict(gap=gap, ma5dev=(bp / ma5 - 1) * 100, ret5=ret5, ret20=ret20,
                volr=volr, br=br, px=bp, R=((bp - sp) / bp * 100) if sp else None)


def line(name, vals):
    vals = [x for x in vals if x is not None]
    if len(vals) < 5:
        print("  %-30s n=%d 样本太少" % (name, len(vals)))
        return
    w = [x for x in vals if x > 0]
    print("  %-30s n=%-3d 胜率 %3.0f%% 均值 %+.2f%% 合计 %+7.1f%% 最大亏 %+.1f%%"
          % (name, len(vals), 100 * len(w) / len(vals), st.mean(vals), sum(vals), min(vals)))


def main():
    ap = argparse.ArgumentParser(description="台账胜率/期望归因（只读，不写盘）")
    ap.add_argument("--brief", action="store_true", help="只看风险归因与出场对照")
    ap.add_argument("--data", default="data.js", help="data.js 路径")
    ap.add_argument("--ledger", default="chen_ledger.json", help="台账路径")
    a = ap.parse_args()

    D = json.loads(open(os.path.join(HERE, a.data), encoding="utf-8").read().split("=", 1)[1].rstrip().rstrip(";"))
    L = json.load(open(os.path.join(HERE, a.ledger), encoding="utf-8"))["entries"]
    settled = [e for e in L if res(e).get("status") == "settled"]
    exe = [e for e in settled if res(e).get("result") != "notExecuted"
           and isinstance(res(e).get("retPct"), (int, float))]
    print("台账 %d｜已结算 %d｜执行 %d｜未进场 %d" %
          (len(L), len(settled), len(exe), len(settled) - len(exe)))
    line("现行规则（台账原值）", [res(e)["retPct"] for e in exe])

    rows = [(e, feats(D, e)) for e in exe]
    rows = [(e, f) for e, f in rows if f]
    print("可复算 K 线样本 %d（窗口内）\n" % len(rows))

    print("【风险归因】R=(买价−止损位)/买价 → 期望")
    cuts = [(0, 4, "R 0-4%"), (4, 8, "R 4-8%"), (8, 12, "R 8-12%"),
            (12, 20, "R 12-20%"), (20, 999, "R >20%")]
    for lo, hi, lab in cuts:
        sel = [e for e, f in rows if f["R"] is not None and lo <= f["R"] < hi]
        line("  " + lab, [replay(D, e) for e in sel])
    print()
    print("【出场对照】（同一批入场，只换出场）")
    allsel = [e for e, _f in rows]
    line("  现行（形态止损+止盈5%）", [replay(D, e) for e in allsel])
    line("  硬止损−5% + 止盈5%", [replay(D, e, stop_pct=5) for e in allsel])
    line("  硬止损−5% + 不止盈持5日", [replay(D, e, stop_pct=5, tgt_pct=99, hold=5) for e in allsel])
    line("  硬止损−3% + 止盈5%", [replay(D, e, stop_pct=3) for e in allsel])

    if a.brief:
        return 0

    print()
    print("【入场因子】（口径：硬止损−5% / 止盈+5%）")
    def bucket(name, key, cuts2):
        print("  " + name)
        for lo, hi, lab in cuts2:
            sel = [e for e, f in rows if f[key] is not None and lo <= f[key] < hi]
            line("    " + lab, [replay(D, e, stop_pct=5) for e in sel])
    bucket("前5日涨幅 ret5", "ret5",
           [(-99, 0, "<0"), (0, 10, "0~10%"), (10, 30, "10~30%"), (30, 999, ">30%")])
    bucket("买价距 MA5", "ma5dev",
           [(-99, 0, "<0"), (0, 3, "0~3%"), (3, 8, "3~8%"), (8, 999, ">8%")])
    bucket("前20日涨幅 ret20", "ret20",
           [(-99, 0, "<0"), (0, 20, "0~20%"), (20, 50, "20~50%"), (50, 999, ">50%")])
    bucket("涨停日量能比", "volr", [(0, 1, "<1"), (1, 2, "1~2"), (2, 4, "2~4"), (4, 99, ">4倍")])
    bucket("断板日量能比", "br", [(0, 1.5, "<1.5"), (1.5, 3, "1.5~3"), (3, 5, "3~5"), (5, 99, ">5倍")])
    bucket("买价", "px", [(0, 10, "<10元"), (10, 30, "10~30元"), (30, 999, ">30元")])
    bucket("竞价溢价 gap", "gap",
           [(-99, -2, "低开<-2%"), (-2, 0, "低开-2~0"), (0, 2, "高开0~2%"), (2, 99, "高开>2%")])

    print()
    print("【组合过滤】（前5日<10% 且 距MA5<8%，硬止损−5%）")
    sel = [e for e, f in rows if f["ret5"] < 10 and f["ma5dev"] < 8]
    line("  组合过滤后", [replay(D, e, stop_pct=5) for e in sel])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

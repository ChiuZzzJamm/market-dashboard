#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""counter_check.py — 把「反证」从一句文案变成可核验的命中率（R100z366）。

## 为什么（2026-11-21 00:09 用户：「以交易员身份判断，需要就直接优化」）

现状：五节要闻每条 deduce 都有 `counter`（反证），但它是**自由文本**，没人核验——
「反证有多准」这个问题至今答不出来。方向预测 43% 命中、跑赢基准 49.5%（≈抛硬币），
而反证是全系统唯一「自带证伪条件」的部件：只要把它结构化，就能变成数字。

## 结构（AI 写，脚本核）

每条要闻的 `deduce.falsify` 形如：
    {"code": "600030", "kind": "gap", "value": -3, "why": "若次日竞价低开超 3%"}
  - kind="gap"   ：次一交易日**开盘相对前收**的涨跌幅 ≤ value → 反证成立
  - kind="close" ：次一交易日**收盘价** ≤ value（绝对价）→ 反证成立
  - code 取该条 impacts 里的主推标的（没有 impacts 就用板块内第一只）

## 产出（`ashare.counterScore`，页面在 A 股要闻卡底部一行）

  n / trig / rate          ：有 falsify 的条数、次日触发的条数、触发率
  fwdTrig / fwdNot / edge  ：触发组与未触发组「次日收盘→第 5 个交易日收盘」的均值，
                             edge = fwdNot − fwdTrig（>0 才说明「反证触发」真的对应更差的走势）
  byKind / items           ：分类型与最近 8 条明细（页面悬停可见）

⚠️ 样本 <10 条时只出数字不下结论（页面显示「样本不足」），避免用三条样本宣布反证有效。
"""
import argparse
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common  # noqa: E402

SECS = ("bullNews", "bearNews", "macroNews", "intlNews", "bankViews")
MIN_N = 10          # 低于这个数不给结论
CAP = 8             # 明细最多留 8 条


def _bars(D, code):
    return (D.get("stkKlines") or {}).get(str(code).zfill(6)) or []


def _idx_on(b, day):
    """最后一根 ≤ day 的下标（day 非交易日就取之前最近的一个）。"""
    lo = -1
    for i, x in enumerate(b):
        if str(x[0])[:10] <= day:
            lo = i
        else:
            break
    return lo


def evaluate(D, it, f):
    """返回 dict 或 None（数据不够）。"""
    code = str(f.get("code") or "").strip()
    kind = str(f.get("kind") or "").strip()
    try:
        val = float(f.get("value"))
    except (TypeError, ValueError):
        return None
    if not code or kind not in ("gap", "close"):
        return None
    day = str(it.get("time") or it.get("date") or "")[:10]
    if not day:
        return None
    b = _bars(D, code)
    if len(b) < 5:
        return None
    i = _idx_on(b, day)
    if i < 0 or i + 1 >= len(b):
        return None                      # 次一交易日还没出来 → 未到期
    nxt = b[i + 1]
    o, c, prev_c = float(nxt[1]), float(nxt[2]), float(b[i][2])
    if kind == "gap":
        trig = (o / prev_c - 1) * 100 <= val if prev_c else False
        obs = round((o / prev_c - 1) * 100, 2) if prev_c else None
    else:
        trig = c <= val
        obs = round(c, 2)
    j = min(i + 5, len(b) - 1)
    fwd = round((float(b[j][2]) / c - 1) * 100, 2) if (j > i + 1 and c) else None
    return {"day": day, "code": code, "kind": kind, "value": val,
            "trig": bool(trig), "obs": obs, "fwd": fwd,
            "title": str(it.get("title") or "")[:28]}


def build(D):
    rows, seen = [], set()
    for mk in ("ashare", "us"):
        sec = D.get(mk) or {}
        for key in SECS:
            for it in (sec.get(key) or []):
                f = ((it.get("deduce") or {}).get("falsifyIf"))
                if not isinstance(f, dict):
                    continue
                k = (str(it.get("title"))[:24], str(f.get("code")), str(f.get("kind")))
                if k in seen:
                    continue
                seen.add(k)
                r = evaluate(D, it, f)
                if r:
                    rows.append(r)
    rows.sort(key=lambda z: z["day"], reverse=True)
    trig = [r for r in rows if r["trig"]]
    notr = [r for r in rows if not r["trig"]]
    ft = [r["fwd"] for r in trig if isinstance(r["fwd"], (int, float))]
    fn = [r["fwd"] for r in notr if isinstance(r["fwd"], (int, float))]
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    by_kind = []
    for kd in ("gap", "close"):
        sub = [r for r in rows if r["kind"] == kd]
        if sub:
            by_kind.append({"kind": kd, "n": len(sub),
                            "trig": sum(1 for r in sub if r["trig"])})
    mod = {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "asOf": str((D.get("ashare") or {}).get("tradeDate") or "")[:10],
        "n": len(rows),
        "trig": len(trig),
        "rate": round(len(trig) * 100.0 / len(rows), 1) if rows else None,
        "fwdTrig": avg(ft),
        "fwdNot": avg(fn),
        "edge": (round(avg(fn) - avg(ft), 2) if (avg(fn) is not None and avg(ft) is not None) else None),
        "small": len(rows) < MIN_N,
        "byKind": by_kind,
        "items": rows[:CAP],
        "note": ("反证核验：次一交易日是否满足各条的作废条件；fwd = 该标的从次一日收盘到第 5 个"
                 "交易日收盘的涨跌。edge = 未触发组均值 − 触发组均值，>0 才说明反证有区分度"),
    }
    return mod


def main():
    ap = argparse.ArgumentParser(description="反证（deduce.falsify）核验 → ashare.counterScore")
    ap.add_argument("--write", action="store_true", help="写 data.js（默认干跑）")
    ap.add_argument("--data", default="data.js")
    a = ap.parse_args()
    base = HERE
    D = common.load_dashboard_data(base)
    if not D:
        print("[ERR] 读不到 data.js")
        return 2
    mod = build(D)
    print("[counter] 可核验 %d 条 · 触发 %d 条（%s%%）· 触发后 5 日均值 %s%% vs 未触发 %s%% · edge %s"
          % (mod["n"], mod["trig"], mod["rate"], mod["fwdTrig"], mod["fwdNot"], mod["edge"]))
    for x in mod["items"][:5]:
        print("  %s %s %-6s %s %s=%s → %s | fwd %s" % (
            x["day"], "触发" if x["trig"] else "未触发", x["code"], x["title"][:18],
            x["kind"], x["value"], x["obs"], x["fwd"]))
    if mod["small"]:
        print("[WARN] 样本 < %d，页面只显示数字、不下结论" % MIN_N)
    if not a.write:
        print("（干跑：加 --write 才写 data.js）")
        return 0
    D.setdefault("ashare", {})["counterScore"] = mod
    common.write_data_js(os.path.join(base, "data.js"), D, label="[counter]")
    print("[OK] ashare.counterScore 已写入")
    return 0


if __name__ == "__main__":
    sys.exit(main())

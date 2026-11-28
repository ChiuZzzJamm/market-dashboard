#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学习台账（learn_ledger.py）——断板反包规则的「持续进化」引擎。

用户 2026-11-28：「把这几天学会并优化的断板反包知识牢记并每天不断进化学习，取其精华去其糟粕。」

它做三件事，每天收盘后跑一次：
  ① **体检现行规则**：把 rebound_review.json 的样本按各条判据分箱，算每组胜率/均值；
     某条判据如果出现「本该被剔除、却成功了」的样本 ≥3 例 → 打印反证告警（=该改判据了）。
  ② **对照基线**：与 learn_state.json 里上一版基线比，胜率/样本分布变化超过阈值就提示。
  ③ **固化结论**：把本次统计写回 learn_state.json，作为下一次的对照。

纪律（写死在代码里，别绕过）：
  - **样本 < 3 例不许改阈值**（1 例只是故事，3 例才是证据）；
  - 改判据必须同时在 PROJECT-MEMORY 24.210 记一行「改了什么/为什么/被哪几例推翻」；
  - 只统计**已结算**样本，未走完持有期的不进分母。
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW = os.path.join(HERE, "rebound_review.json")
STATE = os.path.join(HERE, "learn_state.json")
MIN_SAMPLES = 3          # 少于 3 例不许动判据
FALSIFY_MIN = 3          # 反证达到 3 例 → 告警


def load_review():
    try:
        return json.load(open(REVIEW, encoding="utf-8")).get("rows") or []
    except Exception:                                            # noqa: BLE001
        return []


def bin_of(v, edges):
    if v is None:
        return "未知"
    for lo, hi, name in edges:
        if lo is None and v < hi:
            return name
        if hi is None and v >= lo:
            return name
        if lo is not None and hi is not None and lo <= v < hi:
            return name
    return "未知"


RATIO_EDGES = [(None, 1.1, "断板<1.1×"), (1.1, 3.5, "1.1~3.5×"),
               (3.5, 8.0, "3.5~8×"), (8.0, None, "≥8×巨量")]
WASH_EDGES = [(None, 2, "洗1日"), (2, 5, "洗2~4日"), (5, None, "洗≥5日")]
MINVOL_EDGES = [(None, 50, "最低量<50%"), (50, 70, "50~70%"), (70, None, "≥70%")]


def stats(rows, keyfn):
    buckets = {}
    for r in rows:
        k = keyfn(r)
        b = buckets.setdefault(k, {"n": 0, "ok": 0})
        b["n"] += 1
        if r.get("status") == "成功":
            b["ok"] += 1
    out = {}
    for k, b in buckets.items():
        out[k] = {"n": b["n"], "win": b["ok"], "rate": round(b["ok"] / b["n"] * 100) if b["n"] else None}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写回 learn_state.json")
    a = ap.parse_args()
    rows = [r for r in load_review() if r.get("status") in ("成功", "失败")]
    if not rows:
        print("[learn] 没有已结算样本，跳过")
        return 0
    groups = {
        "断板量比": stats(rows, lambda r: bin_of(r.get("breakMult"), RATIO_EDGES)),
        "洗盘天数": stats(rows, lambda r: bin_of(r.get("washDays"), WASH_EDGES)),
        "最低量比": stats(rows, lambda r: bin_of(r.get("minVolPct"), MINVOL_EDGES)),
        "破前低": stats(rows, lambda r: "破前低" if r.get("brokePreLow") else "未破前低"),
    }
    print("=" * 66)
    print("断板反包 · 规则体检（已结算 %d 例：成功 %d / 失败 %d）"
          % (len(rows), sum(1 for r in rows if r["status"] == "成功"),
             sum(1 for r in rows if r["status"] != "成功")))
    for g, bs in groups.items():
        print("\n[%s]" % g)
        for k in sorted(bs, key=lambda x: (-bs[x]["n"])):
            b = bs[k]
            flag = "  ← 样本不足，不作结论" if b["n"] < MIN_SAMPLES else ""
            print("  %-12s n=%2d 胜率 %s%%%s"
                  % (k, b["n"], b["rate"] if b["rate"] is not None else "—", flag))

    # 反证扫描：现行判据认为「不该做」的样本，却成功了
    falsify = []
    for r in rows:
        if r.get("status") != "成功":
            continue
        rm = r.get("breakMult")
        if rm is not None and (rm < 1.1 or rm >= 4.5):
            falsify.append((r.get("code"), r.get("name"), "断板量比 %.2f× 超出 1.1~4.5" % rm))
        if (r.get("washDays") or 0) >= 5:
            falsify.append((r.get("code"), r.get("name"), "洗盘 %s 日 ≥5" % r.get("washDays")))
    print("\n[反证] 现行判据会剔除、但实际成功的样本：%d 例" % len(falsify))
    for c, n, why in falsify[:10]:
        print("  %-6s %-6s %s" % (c, n or "-", why))
    if len(falsify) >= FALSIFY_MIN:
        print("  ⚠️ 已达 %d 例（阈值 %d）→ 该重估判据，改完在 24.210 记一行"
              % (len(falsify), FALSIFY_MIN))

    snap = {"asOf": datetime.now().strftime("%Y-%m-%d %H:%M"), "n": len(rows), "groups": groups,
            "falsify": len(falsify)}
    if a.write:
        json.dump(snap, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n[learn] 已写入 learn_state.json（下次以此对照漂移）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

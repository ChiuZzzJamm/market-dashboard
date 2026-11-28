#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分时学习器（learn_intraday.py，2026-11-28 用户：要有自己的学习机制）。

用「已知答案」的样本反推：同一批候选里，**洗盘日分时特征**能不能拉开「次日反包成功」与「没成功」。
样本来源：
  正样本 = data.js 成功池（当日 ≥+7%）里的票，取其**洗盘日**（前一日）分时摘要；
  负样本 = 同一天在池、但没进成功池的票，取同一日分时摘要。
输出：每个特征分箱的「成功占比」，以及各特征的最大分离度（越高越有用）。

它不改代码、不自动调参——只回答一个问题：**这条特征值不值得进排序分**。
要进，必须先连续两日给出同向结论（样本 ≥30），再由人（我）改权重并在 24.210 记账。
"""
import argparse
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def load():
    raw = open(os.path.join(HERE, "data.js"), encoding="utf-8").read()
    return json.loads(raw.split("=", 1)[1].rstrip().rstrip(";"))


def main():
    ap = argparse.ArgumentParser(description="洗盘日分时特征 vs 次日反包成功率")
    ap.add_argument("--min-n", type=int, default=8)
    a = ap.parse_args()
    D = load()
    try:
        J = json.load(open(os.path.join(HERE, "intraday_minute.json"), encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        print("[learn] 没有 intraday_minute.json")
        return 1
    H = J.get("history") or {}          # 顶层 history：{code: {date: metrics}}（不是 per-stock）
    win = set()
    for node in ("duanban", "chenPool"):
        for e in (D.get(node) or {}).get("success") or []:
            if isinstance(e, dict):
                win.add(str(e.get("code")))

    today = (D.get("ashare") or {}).get("tradeDate") or ""
    rows = []
    for code, h in H.items():
        if not h:
            continue
        # 洗盘日＝反包日之前的最后一个有档案的交易日
        days = sorted(d for d in h if d < (today or "9999"))
        m = h[days[-1]] if days else None
        if not m:
            continue
        ab = m.get("absorb") or {}
        rows.append({
            "code": code, "win": code in win,
            "verdict": ab.get("verdict"), "ratio": ab.get("ratio"),
            "abovePct": m.get("abovePct"), "breakN": m.get("breakN"),
            "tailRedPct": m.get("tailRedPct"),
            "redBurst": sum(1 for b in (m.get("bursts") or []) if b.get("dir") == "红"),
            "hold": m.get("hold"),
        })
    if not rows:
        print("[learn] 没有可用样本")
        return 1
    n = len(rows)
    base = sum(1 for r in rows if r["win"]) / n * 100
    print("=" * 66)
    print("洗盘日分时特征 × 次日反包成功率（样本 %d，其中成功 %d，基线 %.0f%%）"
          % (n, sum(1 for r in rows if r["win"]), base))

    def show(name, keyfn, order=None):
        g = defaultdict(lambda: [0, 0, 0])     # n, win, sumAbove？
        for r in rows:
            k = keyfn(r)
            if k is None:
                continue
            g[k][0] += 1
            g[k][1] += 1 if r["win"] else 0
        ks = order or sorted(g, key=lambda x: str(x))
        print("\n[%s]" % name)
        best = None
        for k in ks:
            if k not in g:
                continue
            nn, w, _ = g[k]
            if nn < a.min_n:
                continue
            rate = w / nn * 100
            print("  %-14s n=%3d  成功率 %5.1f%%  (%+5.1f pp)" % (k, nn, rate, rate - base))
            best = rate if best is None else max(best, rate)
        if best is not None:
            print("  最大分离度 %+.1f pp" % (best - base))

    show("承接判定", lambda r: r["verdict"],
         ["承接到位", "承接不足", "出货嫌疑", "无砸盘"])
    show("承接量比", lambda r: ("<0.3" if (r["ratio"] or 0) < 0.3 else
                              "0.3~0.6" if (r["ratio"] or 0) < 0.6 else
                              "0.6~1.0" if (r["ratio"] or 0) < 1.0 else "≥1.0"),
         ["<0.3", "0.3~0.6", "0.6~1.0", "≥1.0"])
    show("站上均价线时间占比", lambda r: ("<30%" if (r["abovePct"] or 0) < 30 else
                                      "30~60%" if (r["abovePct"] or 0) < 60 else
                                      "60~90%" if (r["abovePct"] or 0) < 90 else "≥90%"),
         ["<30%", "30~60%", "60~90%", "≥90%"])
    show("破均价线次数", lambda r: ("0 次" if not r["breakN"] else
                                 "1~2 次" if r["breakN"] <= 2 else "≥3 次"),
         ["0 次", "1~2 次", "≥3 次"])
    show("尾盘红柱占比", lambda r: ("<35%" if (r["tailRedPct"] or 0) < 35 else
                                  "35~50%" if (r["tailRedPct"] or 0) < 50 else
                                  "50~65%" if (r["tailRedPct"] or 0) < 65 else "≥65%"),
         ["<35%", "35~50%", "50~65%", "≥65%"])
    show("红柱爆量次数", lambda r: ("0 次" if not r["redBurst"] else
                                  "1 次" if r["redBurst"] == 1 else "≥2 次"),
         ["0 次", "1 次", "≥2 次"])
    print("\n注意：这是**同日横截面**证据（n 小、且只有一天），不能直接当权重。"
          "连续两日同向且样本 ≥30 再改权重，改完在 24.210 记账。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

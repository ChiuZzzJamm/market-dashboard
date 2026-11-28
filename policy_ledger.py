#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""政策信号台账（policy_ledger.py，2026-11-28 用户提：政策信号解码是否也需要台账）。

做什么：
  ① 每天把 data.js 的 `policySignals` 里没登记过的信号记进 `policy_ledger.json`
     （带日期 / 层级 / 分数 / 信号分类 / 涉及板块）；
  ② 同时记下当日 «板块涨幅» 快照（name → pct），这是事后结算的唯一依据；
  ③ 结算：信号发布后的**下一个有快照的交易日**，看它点名的板块平均涨幅 →
     平均 >0 记「命中」，≤0 记「落空」；
  ④ 汇总：按层级（S/A/B/C）、按信号分类（货币/财政/产业/其他）算命中率，写回台账。

为什么这么设计：
  政策信号的价值不在「写得多漂亮」，而在「点名的方向第二天有没有资金认」。
  没有事后结算，评分再细也只是自说自话（这正是用户问「要不要台账」的原因）。
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "policy_ledger.json")


def load_data():
    raw = open(os.path.join(HERE, "data.js"), encoding="utf-8").read()
    return json.loads(raw.split("=", 1)[1].rstrip().rstrip(";"))


def board_pcts(D):
    """当日板块涨幅表：sectorsUp / sectorsDown / topBoards 三处合并（同名取先出现的）。"""
    out = {}
    a = D.get("ashare") or {}
    for k in ("sectorsUp", "sectorsDown"):
        for s in a.get(k) or []:
            n, p = s.get("name"), s.get("pct")
            if n and p is not None and n not in out:
                out[n] = round(float(p), 2)
    for b in (D.get("duanban") or {}).get("topBoards") or []:
        n, p = b.get("name"), b.get("pctToday")
        if n and p is not None and n not in out:
            out[n] = round(float(p), 2)
    return out


def main():
    ap = argparse.ArgumentParser(description="政策信号台账（记录 + 次日结算 + 命中率）")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    D = load_data()
    today = (D.get("ashare") or {}).get("tradeDate") or datetime.now().strftime("%Y-%m-%d")
    try:
        led = json.load(open(LEDGER, encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        led = {"marks": {}, "items": []}
    marks, items = led.setdefault("marks", {}), led.setdefault("items", [])

    marks[today] = board_pcts(D)
    have = {(it.get("date"), it.get("title")) for it in items}
    added = 0
    for s in D.get("policySignals") or []:
        key = (s.get("date") or today, s.get("title") or "")
        if not key[1] or key in have:
            continue
        items.append({"date": key[0], "title": key[1], "tier": s.get("tier"),
                      "score": s.get("score"), "cls": s.get("signalClass"),
                      "issuer": s.get("issuer"), "sectors": s.get("sectors") or [],
                      "settle": None})
        have.add(key)
        added += 1

    # 结算：找信号日之后第一个有快照的交易日
    dates = sorted(marks)
    hit = miss = 0
    for it in items:
        if it.get("settle"):
            continue
        later = [d for d in dates if d > str(it["date"]) and marks.get(d)]
        if not later:
            continue
        m = marks[later[0]]
        vals = [m[s] for s in it.get("sectors") or [] if s in m]
        if not vals:
            continue
        avg = round(sum(vals) / len(vals), 2)
        it["settle"] = {"date": later[0], "avgPct": avg, "n": len(vals),
                        "hit": avg > 0}
        hit += 1 if avg > 0 else 0
        miss += 0 if avg > 0 else 1

    settled = [it for it in items if it.get("settle")]
    print("[policy] 信号 %d 条，已结算 %d 条（本次新增 %d / 新结算 %d）"
          % (len(items), len(settled), added, hit + miss))
    if settled:
        ok = sum(1 for it in settled if it["settle"]["hit"])
        print("  总命中率 %.0f%%（%d/%d）" % (ok / len(settled) * 100, ok, len(settled)))
        for key in ("tier", "cls"):
            g = {}
            for it in settled:
                k = it.get(key) or "未标"
                v = g.setdefault(k, [0, 0])
                v[0] += 1
                v[1] += 1 if it["settle"]["hit"] else 0
            print("  按%s：%s" % ("层级" if key == "tier" else "分类",
                                 " ｜ ".join("%s %d/%d" % (k, v[1], v[0]) for k, v in sorted(g.items()))))
    else:
        print("  还没有可结算样本（需要至少两个交易日的板块涨幅快照，次日自动补）")

    if a.write:
        led["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        json.dump(led, open(LEDGER, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[policy] 已写入 %s" % os.path.basename(LEDGER))
    return 0


if __name__ == "__main__":
    sys.exit(main())

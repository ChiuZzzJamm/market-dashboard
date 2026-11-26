#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""反包登记复盘（rebound_review.py）。

用途（用户 2026-11-26）：「看板做一个入口，我把每天反包成功的标的输进去，
盘后更新的时候进行分析学习，以此提高反包胜率」。

设计取舍：
  · **登记库独立成文件**（`rebound_log.json`），不塞进 data.js —— 它是人输入的原始事实，
    不是行情产物；塞进 data.js 会被每次行情重写带着跑，迟早丢。
  · **输出也是文件**（`rebound_review.json`），页面直接 fetch，不新增顶层数据节点，
    免得动 PRODUCT_OWNERSHIP / 写者登记 / 审计那套。
  · 只做**证据提取与对账**，不改选股池、不下结论。规则阈值在
    `rules/duanban-framework.md`，改阈值是人的决定。

用法：
  python3 rebound_review.py --write          # 盘后跑：算证据、写 rebound_review.json
  python3 rebound_review.py --add 002815:2026-11-26,603887:2026-11-25 [--pool 断板反包]
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "rebound_log.json")
OUT = os.path.join(HERE, "rebound_review.json")
DATA = os.path.join(HERE, "data.js")
MAIN_WORDS = ("算力", "CPO", "共封装", "PCB", "机器人", "减速器", "存储", "封装",
              "商业航天", "短剧", "AI", "光伏", "固态", "数据")


def load_data():
    s = open(DATA, encoding="utf-8").read()
    p = s.find("=")
    return json.loads(s[p + 1:].rstrip().rstrip(";"))


def load_log():
    if not os.path.exists(LOG):
        return {"updatedAt": "", "items": []}
    return json.load(open(LOG, encoding="utf-8"))


def features(kl, code, rebound_date):
    """反包日的结构特征。rebound_date 当天的 K 线若还没入库，就取之前最后一根作为「反包前」。"""
    bars = kl.get(code) or []
    if len(bars) < 25:
        return {"ok": False, "why": "K 线不足"}
    rows = [r for r in bars if str(r[0]) <= rebound_date]
    pre = [r for r in bars if str(r[0]) < rebound_date]
    if len(pre) < 21:
        return {"ok": False, "why": "反包日前样本不足"}
    c = [r[2] for r in pre]
    lo = [r[4] for r in pre]
    v = [r[5] for r in pre]
    n = len(c)
    d = -1
    for k in range(n - 1, 20, -1):
        if c[k - 1] / c[k - 2] - 1 < 0.098:
            continue
        if v[k] < 1.10 * v[k - 1]:
            continue
        if c[k] / c[k - 1] - 1 >= 0.098:
            continue
        d = k
        break
    if d < 0:
        return {"ok": False, "why": "未识别到涨停→倍量断板结构"}
    seg = v[d + 1:]
    pre_low = min(lo[max(0, d - 10):d])
    wash_low = min(lo[d + 1:]) if seg else None
    today = [r for r in rows if str(r[0]) == rebound_date]
    reb_vol = today[0][5] if today else None
    return {
        "ok": True,
        "ztDate": str(pre[d - 1][0]),
        "breakDate": str(pre[d][0]),
        "breakMult": round(v[d] / v[d - 1], 2),
        "washDays": len(seg),
        "minVolPct": round(min(seg) / v[d] * 100) if seg else None,
        "brokePreLow": bool(wash_low is not None and wash_low < pre_low * 0.995),
        "preLow": round(pre_low, 2),
        "reboundVolPct": (round(reb_vol / v[d] * 100) if reb_vol else None),
    }


def main():
    ap = argparse.ArgumentParser(description="反包登记复盘")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--add", default="")
    ap.add_argument("--pool", default="断板反包")
    a = ap.parse_args()
    log = load_log()

    if a.add:
        added = []
        for tok in a.add.replace("，", ",").split(","):
            tok = tok.strip()
            if not tok:
                continue
            code, _, day = tok.partition(":")
            code = code.strip()
            day = day.strip() or datetime.now().strftime("%Y-%m-%d")
            if any(x.get("code") == code and x.get("date") == day for x in log["items"]):
                continue
            log["items"].append({"code": code, "name": "", "date": day,
                                 "pool": a.pool, "status": "成功", "note": "手动登记"})
            added.append(code)
        log["updatedAt"] = datetime.now().strftime("%Y-%m-%d")
        json.dump(log, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[add] 新增 %d 条：%s" % (len(added), ",".join(added) or "无"))

    if not a.write:
        return 0

    D = load_data()
    kl = D.get("stkKlines") or {}
    items = log.get("items") or []
    rows = []
    for it in items:
        f = features(kl, it.get("code"), it.get("date"))
        row = dict(it)
        row.update(f)
        rows.append(row)

    ok = [r for r in rows if r.get("ok") and r.get("status") == "成功"]
    fail = [r for r in rows if r.get("ok") and r.get("status") != "成功"]
    def avg(key, arr):
        vals = [r[key] for r in arr if r.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None
    out = {
        "asOf": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n": len(rows),
        "okN": len(ok),
        "failN": len(fail),
        "avg": {
            "breakMult": avg("breakMult", ok),
            "washDays": avg("washDays", ok),
            "minVolPct": avg("minVolPct", ok),
            "reboundVolPct": avg("reboundVolPct", ok),
        },
        "failAvg": {
            "breakMult": avg("breakMult", fail),
            "washDays": avg("washDays", fail),
            "minVolPct": avg("minVolPct", fail),
        },
        "brokeRate": (round(sum(1 for r in ok if r.get("brokePreLow")) / len(ok) * 100)
                      if ok else None),
        "rows": sorted(rows, key=lambda r: (r.get("date") or "", r.get("code") or ""), reverse=True)[:60],
        "note": ("成功样本均值为「成功组」口径；失败组从 failPool 补登记后才有值。"
                 "只有成功样本时会系统性高估——别拿这份均值当门槛。"),
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[write] %d 条（成功 %d / 失败 %d）→ rebound_review.json" % (len(rows), len(ok), len(fail)))
    if ok:
        print("        成功组均值：断板×%.2f 洗盘%.1f天 最低量%.0f%% 反包量%.0f%%"
              % (out["avg"]["breakMult"] or 0, out["avg"]["washDays"] or 0,
                 out["avg"]["minVolPct"] or 0, out["avg"]["reboundVolPct"] or 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())

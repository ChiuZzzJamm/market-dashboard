# -*- coding: utf-8 -*-
"""enrich_policy.py — 把新闻标题确定性解码为政策信号，写回 data.js 顶层 policySignals。

零网络、纯函数（复用 decode_policy.policy_score）。由 16:00 自动化在新闻写回后、校验前调用：
    cd /Users/loccco/WorkBuddy/market dashboard && run-python enrich_policy.py

产出：data.js 顶层 policySignals = [
    {title, issuer, docType, binding, signalClass, tier, score, sectors, targets, date, market}, ...
] （按 score 降序；tier!=C 全留 + 至多补至 12 条）

设计纪律：
- 只保留「真政策源」标题（发文主体命中 中共中央/国务院/央行/部委；媒体/券商/外国源、未识别源一律剔除），
  避免把行情快讯当政策信号。
- 失败绝不阻断流水线：任何异常都兜底写回空数组并 exit 0，不影响后续审计与部署。
- 只新增 policySignals 一个顶层键，不改写任何其它字段（保持 data.js 其余结构不变）。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import decode_policy as dp  # noqa: E402

NEWS_NODES = [
    ("ashare", "bullNews"), ("ashare", "bearNews"), ("ashare", "macroNews"),
    ("ashare", "intlNews"), ("ashare", "bankViews"),
    ("us", "bullNews"), ("us", "bearNews"), ("us", "macroNews"),
    ("us", "intlNews"), ("us", "bankViews"),
]
MAX_KEEP = 12


def _load(path):
    s = open(path, encoding="utf-8").read()
    m = re.search(r"window\.DASHBOARD_DATA\s*=\s*(\{.*\});?\s*$", s, re.S)
    if not m:
        raise RuntimeError("data.js 解析失败")
    return json.loads(m.group(1))


def _dump(path, D):
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.DASHBOARD_DATA = " +
                json.dumps(D, ensure_ascii=False, separators=(",", ":")) + ";\n")


def _decode_all(D):
    items = []
    for mk, node in NEWS_NODES:
        sub = (D.get(mk) or {})
        arr = sub.get(node) or []
        trade_date = sub.get("tradeDate") or D.get("updatedAt") or ""
        for it in arr:
            if not isinstance(it, dict):
                continue
            title = (it.get("title") or "").strip()
            if not title:
                continue
            r = dp.policy_score(title)
            # 只保留真政策源（媒体/券商/外国源、未识别源 decode_issuer 已归 0/1 档）
            if r["issuer"].startswith("媒体") or r["issuer"].startswith("未识别"):
                continue
            items.append({
                "title": title,
                "issuer": r["issuer"],
                "docType": r["docType"],
                "binding": r["binding"],
                "signalClass": r.get("signalClass", "其他"),
                "tier": r["tier"],
                "score": r["score"],
                "sectors": r["sectors"],
                "targets": r["targets"],
                "date": str(trade_date),
                "market": mk,
            })
    # 去重（同标题只留最高分）
    seen = {}
    for it in items:
        k = it["title"]
        if k not in seen or it["score"] > seen[k]["score"]:
            seen[k] = it
    items = sorted(seen.values(), key=lambda x: -x["score"])
    # 保留 tier!=C 的全部 + 补充至最多 MAX_KEEP 条
    kept = [x for x in items if x["tier"] != "C"]
    if len(kept) < MAX_KEEP:
        kept += [x for x in items if x["tier"] == "C"][:MAX_KEEP - len(kept)]
    return kept


def main():
    path = os.path.join(HERE, "data.js")
    if not os.path.exists(path):
        print("[enrich_policy] data.js 不存在，跳过")
        return 0
    try:
        D = _load(path)
        kept = _decode_all(D)
        D["policySignals"] = kept
        _dump(path, D)
        tiers = {t: sum(1 for x in kept if x["tier"] == t) for t in "SABC"}
        print(f"[enrich_policy] 解码政策信号 → 写回 {len(kept)} 条 "
              f"（S{tiers['S']}/A{tiers['A']}/B{tiers['B']}/C{tiers['C']}）")
        return 0
    except Exception as e:  # noqa: BLE001
        # 兜底：写回空数组，绝不阻断流水线
        try:
            D = _load(path)
            D["policySignals"] = []
            _dump(path, D)
        except Exception:
            pass
        print(f"[enrich_policy] 异常已兜底（写空数组）：{e!r}")
        return 0


if __name__ == "__main__":
    sys.exit(main())

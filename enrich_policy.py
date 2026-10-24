# -*- coding: utf-8 -*-
"""enrich_policy.py — 把新闻标题确定性解码为政策信号，写回 data.js 顶层 policySignals。

零网络、纯函数（复用 decode_policy.policy_score）。由 19:30 叙事步在新闻写回后、校验前调用：
    cd /Users/loccco/WorkBuddy/market dashboard && run-python enrich_policy.py

产出：data.js 顶层 policySignals = [
    {title, issuer, docType, binding, signalClass, tier, score, sectors, targets, date, market}, ...
] （按 score 降序；tier!=C 全留 + 至多补至 12 条）

设计纪律：
- 只保留「真政策源」标题（发文主体命中 中共中央/国务院/央行/部委；媒体/券商/外国源、未识别源一律剔除），
  避免把行情快讯当政策信号。
- **降级要显式，不许静默（R100z178，2026-10-23）**：旧版任何异常都「写回空数组 + exit 0」，
  而 G18 只校结构 —— 空数组结构完全合法，于是解码器哪天真的坏了，页面政策面板空着、
  日志有一行字、闸门全绿、**没有任何东西会报警**。这与项目硬规则「抓不到 ≠ 没有，必须显式降级打标」
  相反。现在：解码/写盘异常 → **保留上一版 `policySignals` 不清空**、打印 `[enrich_policy][DEGRADED]`、
  exit 2，由 19:30 叙事步在汇报里点名（deploy.sh 里是 `|| true`，所以不会阻断部署）。
  「解码成功但 0 条」仍是正常结果（今日要闻标题无发文主体，R100z111 口径），照常写空并打印扫描条数。
- 写盘原子化（R100z178）：`.tmp` + 落盘前往返自校验 + `os.replace`。旧版直接 `open(path,'w')`，
  进程被杀就留下 0 字节 data.js、整条流水线断（项目硬约束 11 踩过一次的坑）。
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


def _parse(s):
    m = re.search(r"window\.DASHBOARD_DATA\s*=\s*(\{.*\});?\s*$", s, re.S)
    if not m:
        raise RuntimeError("data.js 解析失败")
    return json.loads(m.group(1))


def _load(path):
    return _parse(open(path, encoding="utf-8").read())


def _dump(path, D):
    """R100z178：.tmp → 落盘前往返自校验 → os.replace，绝不把 data.js 写坏。"""
    out = ("window.DASHBOARD_DATA = " +
           json.dumps(D, ensure_ascii=False, separators=(",", ":")) + ";\n")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(out)
        back = _parse(open(tmp, encoding="utf-8").read())   # 写出去的东西必须能读回来
        miss = [k for k in D if k not in back]
        if miss:
            raise RuntimeError("往返校验失败：顶层键丢失 %s" % (miss[:5],))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _news_count(D):
    """统计参与解码的要闻条数（给「0 条政策」一句可核对的解释，而不是只报个 0）。"""
    n = 0
    for mk, node in NEWS_NODES:
        arr = ((D.get(mk) or {}).get(node) or [])
        n += sum(1 for it in arr if isinstance(it, dict) and (it.get("title") or "").strip())
    return n


def _decode_all(D):
    items = []
    for mk, node in NEWS_NODES:
        sub = (D.get(mk) or {})
        arr = sub.get(node) or []
        # R100z154：us.tradeDate 带「（美东）」后缀（'2026-10-19（美东）'），直接透传会让
        # 全球要闻页政策卡右上角日期渲染成「2026-10-19（美东）」。政策卡是统一口径，
        # 日期一律去掉括号补充说明，只留 YYYY-MM-DD。
        trade_date = re.sub(r"[（(][^）)]*[）)]", "", str(sub.get("tradeDate") or D.get("updatedAt") or "")).strip()
        for it in arr:
            if not isinstance(it, dict):
                continue
            title = (it.get("title") or "").strip()
            if not title:
                continue
            # R100z111：真政策源判定仍只看标题（摘要里提到"央行"的媒体快讯不算政策源），
            # 但其余四维（文种/量化指标/受益板块/时间压缩）用 标题+摘要 联合解码——
            # 此前只扫标题，受益板块/数字目标大多藏在摘要里 → 前端 sectors 渲染恒「—」、
            # 且央行内容若只出现在摘要会漏解（用户 2026-10-10 反馈）。
            tier0, name0, _ = dp.decode_issuer(title)
            if name0.startswith("媒体") or name0.startswith("未识别"):
                continue
            full = (title + "。" + str(it.get("summary") or ""))[:800]
            r = dp.policy_score(full)
            # 主体口径保持标题级别（摘要提到更高层级主体不升级，避免误标发文主体）
            r["issuer"] = f"{name0}(t{tier0})"
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
    except Exception as e:                                   # noqa: BLE001
        print("[enrich_policy][DEGRADED] data.js 读取/解析失败，未做任何写入：%r" % (e,))
        return 2

    prev = D.get("policySignals")
    prev_n = len(prev) if isinstance(prev, list) else 0
    n_news = _news_count(D)
    try:
        kept = _decode_all(D)
    except Exception as e:                                   # noqa: BLE001
        print("[enrich_policy][DEGRADED] 解码异常 → **保留上一版 policySignals %d 条不清空**"
              "（扫描要闻 %d 条）：%r" % (prev_n, n_news, e))
        return 2

    tiers = {t: sum(1 for x in kept if x["tier"] == t) for t in "SABC"}
    if not kept:
        print("[enrich_policy] 解码政策信号 → 写回 0 条（S0/A0/B0/C0）—— 扫描要闻 %d 条，"
              "标题均无发文主体，按 R100z111 口径正确过滤；今日政策面板为空属正常，"
              "不是降级（真降级会打 [DEGRADED] 且 exit 2）" % n_news)
    else:
        print("[enrich_policy] 解码政策信号 → 写回 %d 条 "
              "（S%d/A%d/B%d/C%d；扫描要闻 %d 条）"
              % (len(kept), tiers["S"], tiers["A"], tiers["B"], tiers["C"], n_news))

    try:
        D["policySignals"] = kept
        _dump(path, D)
    except Exception as e:                                   # noqa: BLE001
        print("[enrich_policy][DEGRADED] 写盘失败 → data.js 未被替换，原 policySignals %d 条保留：%r"
              % (prev_n, e))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

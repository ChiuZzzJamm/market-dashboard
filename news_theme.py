#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""news_theme.py — 要闻的「题材标签」+「同题材池内候选」（R100z326）。

## 为什么做（用户 2026-11-10 原话）
「都做，要闻这块原来是行业、板块，现在精确到题材，而不是新增一个模块。」

要闻条目的 `impacts[].theme` 一直是**同花顺一级行业**（煤炭开采加工 / 军工装备 / 元件 / 银行）。
行业是静态分类，跟题材对不上：浙文影业行业「纺织制造」而题材是「短剧游戏/AI应用」。
所以这里**不改 AI 写的 theme**（它还承担板块归属、验证、复盘映射），而是另加一层：
用该条要闻那 8 只标的的**同花顺概念**反推「这条要闻实际在说什么题材」，
再拿这个题材去断板/陈学长池里找同样题材、形态已就绪的标的——**嵌在要闻条目里，不新建模块**。

## 输出（写回 ashare 五节，条目内新增子字段）
```
item.themeTags = ['光伏','储能']            # 该条要闻的题材（≤3，按 8 只标的的概念频次）
item.poolHits  = [{code,name,srcZh,tier,tierZh,hit}]   # 同题材池内候选（≤3）
impacts[i].themeTags = ['光伏']             # 每个影响组的题材（≤2）
```
`theme` 原样保留（行业口径），前端渲染成「题材 + 行业小灰字」。

## 口径
- 题材来自 `data.stockConcepts`（stock_concept.py 抓的同花顺概念），**不是 AI 现编**；
- 只统计**在该条要闻 8 只标的中出现 ≥2 次**的概念才算这条要闻的题材（单只出现是巧合）；
  一条都没有时退回首只标的的第一题材，保证不空；
- 池内候选只取断板双池 + 陈学长三池、剔利空（sentiment=bear）、剔掉本条要闻自己已经列过的标的；
- 排序：已确认档 > 待确认档，同档按池内概率。

异常一律 exit 0，不阻断部署；`stockConcepts` 缺失时整轮跳过（宁可没有题材，也不编）。
"""
import argparse
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402
from stock_concept import is_generic  # noqa: E402  泛概念判定与 pill 共用一份口径

SECTIONS = ("bullNews", "bearNews", "macroNews", "intlNews", "bankViews")
MAX_ITEM_TAGS = 3
MAX_IMP_TAGS = 2
MAX_POOL_HITS = 3
TIER_ZH = {"confirm": "已确认", "watch": "待确认"}


def _help_guard():
    """`--help` 只打用法，绝不执行主流程（K17：写 data.js 的脚本必须接住 --help）。"""
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__ or "(本脚本无说明)")
        raise SystemExit(0)


def _pool(D):
    """池内候选：断板双池 + 陈学长三池 → {code: (entry, srcZh, tier)}。"""
    out = {}
    db = D.get("duanban") or {}
    cp = D.get("chenPool") or {}
    for key, zh, tier in (("confirmed", "断板·确认池", "confirm"),
                          ("watching", "断板·观察池", "watch")):
        for e in (db.get(key) or []):
            if isinstance(e, dict) and e.get("code"):
                out.setdefault(str(e["code"]), (e, zh, tier))
    for key, zh, tier in (("strict", "陈学长·严格池", "confirm"),
                          ("loose", "陈学长·放宽池", "confirm"),
                          ("watching", "陈学长·观察池", "watch")):
        for e in (cp.get(key) or []):
            if isinstance(e, dict) and e.get("code"):
                out.setdefault(str(e["code"]), (e, zh, tier))
    return out


def _concepts_of(sc_items, code):
    """该股的概念（**已剔泛概念**）——国企改革/深股通/中报预增这类是标签不是题材，
    留着会让每条要闻的题材都变成「国企改革」，池内候选也会全员命中（实测 39 只全是假命中）。"""
    it = sc_items.get(str(code)) or {}
    return [str(x.get("n") or "") for x in (it.get("concepts") or [])
            if isinstance(x, dict) and not is_generic(x.get("n"))]


def _top_tags(counter, first_pos, limit, min_n=2):
    """取出现次数≥min_n 的概念，按次数降序、次数相同按首次出现顺序。"""
    picked = [(c, n) for c, n in counter.items() if c and n >= min_n]
    picked.sort(key=lambda x: (-x[1], first_pos.get(x[0], 999)))
    return [c for c, _ in picked[:limit]]


def process(D, verbose=True):
    sc_items = ((D.get("stockConcepts") or {}).get("items")) or {}
    if not sc_items:
        return 0, "stockConcepts 为空，跳过（不编题材）"
    pool = _pool(D)
    ashare = D.get("ashare") or {}
    n_item, n_tagged, n_hits, n_groups = 0, 0, 0, 0
    for sec in SECTIONS:
        for it in (ashare.get(sec) or []):
            if not isinstance(it, dict):
                continue
            n_item += 1
            imps = [im for im in (it.get("impacts") or []) if isinstance(im, dict)]
            codes, seen = [], set()
            for im in imps:
                for st in (im.get("stocks") or []):
                    c = str((st or {}).get("code") or "")
                    if c and c not in seen:
                        seen.add(c)
                        codes.append(c)

            # ---- 每个影响组自己的题材（组内 1 次即算，因为组通常只有几只） ----
            for im in imps:
                gcnt, gpos = Counter(), {}
                for st in (im.get("stocks") or []):
                    c = str((st or {}).get("code") or "")
                    for k in _concepts_of(sc_items, c):
                        gpos.setdefault(k, len(gpos))
                        gcnt[k] += 1
                tags = _top_tags(gcnt, gpos, MAX_IMP_TAGS, min_n=1)
                if tags:
                    im["themeTags"] = tags
                else:
                    im.pop("themeTags", None)

            # ---- 整条要闻的题材：跨组统计，≥2 只标的共享才算 ----
            cnt, pos = Counter(), {}
            for c in codes:
                for k in _concepts_of(sc_items, c):
                    pos.setdefault(k, len(pos))
                    cnt[k] += 1
            tags = _top_tags(cnt, pos, MAX_ITEM_TAGS, min_n=2)
            if not tags and codes:                      # 没有共享概念：退回首只标的的第一题材
                first = _concepts_of(sc_items, codes[0])
                tags = first[:1]
            if tags:
                it["themeTags"] = tags
                n_tagged += 1
            else:
                it.pop("themeTags", None)

            # ---- 同题材池内候选 ----
            hits = []
            tagset = set(tags[:2])
            if tagset:
                for code, (e, zh, tier) in pool.items():
                    if code in seen:
                        continue                        # 本条要闻自己已经列过
                    if str(e.get("sentiment") or "") == "bear":
                        continue                        # 池内利空不进候选
                    # 只在候选的 hot（过滤后的前 2 个题材）里找交集：
                    # 拿全量 concepts 比会把「沾过一点」的票全捞进来（实测一条煤炭要闻
                    # 能匹配出锦和商管/桂林三金这种完全不相关的票）。
                    cs = set((sc_items.get(code) or {}).get("hot") or [])
                    hit = sorted(tagset & cs)
                    if not hit:
                        continue
                    hits.append({
                        "code": code, "name": str(e.get("name") or ""),
                        "srcZh": zh, "tier": tier, "tierZh": TIER_ZH.get(tier, "确认"),
                        "hit": hit[0],
                        "prob": e.get("probability"),
                        "inPicks": False,
                    })
            hits.sort(key=lambda h: (0 if h["tier"] == "confirm" else 1,
                                     -(h["prob"] or 0)))
            hits = hits[:MAX_POOL_HITS]
            picks = {str(p.get("code")) for p in (((D.get("duanban") or {}).get("star") or {})
                                                  .get("picks") or [])}
            for h in hits:
                h["inPicks"] = h["code"] in picks
            if hits:
                it["poolHits"] = hits
                n_hits += len(hits)
            else:
                it.pop("poolHits", None)
            n_groups += len(imps)
    return (n_item, n_tagged, n_groups, n_hits), ""


def main():
    ap = argparse.ArgumentParser(description="要闻题材标签 + 同题材池内候选")
    ap.add_argument("--dry", action="store_true", help="只打印不写回")
    a = ap.parse_args()

    D = common.load_dashboard_data(HERE)
    if not D:
        print("[FAIL] 读不到 data.js")
        return 1
    res, err = process(D)
    if err:
        print("[SKIP]", err)
        return 0
    n_item, n_tagged, n_groups, n_hits = res
    print("[news_theme] 要闻 %d 条（影响组 %d 个）：有题材 %d 条，池内候选 %d 只"
          % (n_item, n_groups, n_tagged, n_hits))
    if n_item and n_tagged * 100 // max(n_item, 1) < 80:
        print("  ⚠️ 题材覆盖偏低（%d/%d），检查 stockConcepts 是否完整" % (n_tagged, n_item))
    if a.dry:
        for sec in SECTIONS:
            for it in ((D.get("ashare") or {}).get(sec) or [])[:1]:
                print("   样例：", it.get("title", "")[:26], "→", it.get("themeTags"),
                      "| 池内", [h["name"] for h in (it.get("poolHits") or [])])
        print("[DRY] 未写回")
        return 0
    if not n_tagged:
        print("[FAIL] 一条题材都没算出来，保留旧数据不覆盖")
        return 0
    common.write_data_js(os.path.join(HERE, "data.js"), D, label="[news_theme]")
    print("[news_theme] 已写回 ashare 五节的 themeTags / impacts[].themeTags / poolHits")
    return 0


if __name__ == "__main__":
    _help_guard()
    sys.exit(main())

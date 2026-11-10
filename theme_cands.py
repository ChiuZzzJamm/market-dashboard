#!/usr/bin/env python3
"""theme_cands.py — 给「题材（跨行业视角）」每一行补上**池内同题材**候选（R100z327）。

为什么要有这一步：
  这张表原来第二行写的是「池内同行业可补涨」，判据是 `候选.sector ∈ 题材的 sectors`。
  两个问题都很硬：
    ① 行业太粗。光伏这个题材的 sectors 里有「通用设备」，而通用设备是几百只票的大类，
       于是雪人集团/利欧股份/柯力传感同时出现在光伏、算力、人形机器人、商业航天、核电
       五行里——同一只票在五个题材下被推了五次，读者第一反应是「这表坏了」。
    ② 同行业 ≠ 同题材。雪人集团（制冷设备）跟光伏没有任何题材关系，
       它只是因为和某只连板同属通用设备。
  现在有 `stockConcepts`（同花顺概念，客观标签），可以直接判「这只票算不算这个题材」：
  候选的**主题材（hot）**里只要有一个命中该题材的概念词（theme_map.json 的 ckw），
  才算同题材。行业仍作兜底，但只在同题材一条都没有时才用，并且页面会标出来是兜底。

口径与别的模块共用：
  · 题材名/关键词只维护 `theme_map.json` 一份（kw 判文本、ckw 判概念）；
  · 泛概念过滤用 `stock_concept.is_generic`（国企改革/中报预增这类不是题材）。

用法：
  python3 theme_cands.py --help
  python3 theme_cands.py            # 写回 data.js（持锁运行）
  python3 theme_cands.py --dry      # 只打印，不落盘
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common  # noqa: E402


def _help_guard():
    ap = argparse.ArgumentParser(description="给题材行补池内同题材候选（只改 duanban.themes[*].cands）")
    ap.add_argument("--dry", action="store_true", help="只打印不写盘")
    ap.add_argument("--data", default="data.js", help="data.js 路径（默认同目录）")
    ap.add_argument("--limit", type=int, default=4, help="每个题材最多几只候选（默认 4）")
    ap.add_argument("--all", action="store_true", help="打印全部题材明细（默认只打汇总）")
    return ap.parse_args()


def _pool(D):
    """断板双池 + 陈学长三池，剔利空。返回 {code: {name, sector, tier, srcZh, total}}。"""
    out = {}
    db = D.get("duanban") or {}
    cp = D.get("chenPool") or {}
    for key, src, tier in (("confirmed", "断板·确认池", "confirm"),
                           ("watching", "断板·观察池", "watch")):
        for e in (db.get(key) or []):
            if not isinstance(e, dict):
                continue
            c = str(e.get("code") or "")
            if not c or c in out:
                continue
            if str(e.get("sentiment") or "") == "bear":
                continue
            out[c] = {"name": str(e.get("name") or ""), "sector": str(e.get("sector") or ""),
                      "tier": tier, "srcZh": src,
                      "total": (e.get("pre") or {}).get("total")}
    for key, src, tier in (("strict", "陈学长·严格池", "confirm"),
                           ("loose", "陈学长·放宽池", "confirm"),
                           ("watching", "陈学长·观察池", "watch")):
        for e in (cp.get(key) or []):
            if not isinstance(e, dict):
                continue
            c = str(e.get("code") or "")
            if not c or c in out:
                continue
            if str(e.get("sentiment") or "") == "bear":
                continue
            out[c] = {"name": str(e.get("name") or ""), "sector": str(e.get("sector") or ""),
                      "tier": tier, "srcZh": src,
                      "total": (e.get("pre") or {}).get("total")}
    return out


def build(D, limit=4):
    """返回 {题材名: (cands, kind)}。kind: theme（同题材）/ sector（同行业兜底）/ ref（词表兜底）。"""
    items = ((D.get("stockConcepts") or {}).get("items")) or {}
    pool = _pool(D)
    ckw = {t["name"]: [str(k) for k in (t.get("ckw") or [t["name"]]) if k] for t in common.theme_map()}
    out = {}
    for name, t in ((D.get("duanban") or {}).get("themes") or {}).items():
        if not isinstance(t, dict):
            continue
        in_lb = set(str(b.get("code") or "") for b in (t.get("boardStocks") or []))
        keys = ckw.get(name) or [name]
        hits = []
        for code, e in pool.items():
            if code in in_lb:
                continue
            hot = ((items.get(code) or {}).get("hot")) or []
            got = [h for h in hot if any(k in str(h) for k in keys)]
            if not got:
                continue
            # 主题材（hot[0]）命中排在前面：那是同花顺给这只票的第一相关度标签，
            # 只命中 hot[1] 的票往往是「沾边」，排在后面即可。
            rank = 0 if hot and any(k in str(hot[0]) for k in keys) else 1
            hits.append((rank, -(float(e["total"]) if isinstance(e["total"], (int, float)) else -1),
                         code, e, got[0]))
        hits.sort(key=lambda z: (z[0], z[1], z[2]))
        cands = [{"code": c, "name": e["name"], "total": e["total"],
                  "srcZh": e["srcZh"], "tier": e["tier"], "hit": hit, "kind": "theme"}
                 for _r, _s, c, e, hit in hits[:limit]]
        kind = "theme"
        if not cands:
            # 兜底一：同行业。只在同题材一条都没有时用，并且页面会写明是「同行业」——
            # 行业是静态分类，跟题材经常对不上，不能当作同题材的等价物。
            sec = [str(x) for x in (t.get("sectors") or []) if x]
            for code, e in pool.items():
                if code in in_lb or e["sector"] not in sec:
                    continue
                cands.append({"code": code, "name": e["name"], "total": e["total"],
                              "srcZh": e["srcZh"], "tier": e["tier"],
                              "hit": e["sector"], "kind": "sector"})
            cands.sort(key=lambda z: -(float(z["total"]) if isinstance(z["total"], (int, float)) else -1))
            cands = cands[:3]
            kind = "sector" if cands else "ref"
        out[name] = (cands, kind)
    return out


def main():
    a = _help_guard()
    path = a.data if os.path.isabs(a.data) else os.path.join(HERE, a.data)
    D = common.load_dashboard_data(os.path.dirname(path))
    res = build(D, limit=a.limit)
    if not res:
        print("[theme_cands] duanban.themes 为空，跳过（16:00 还没跑或今日无连板）")
        return 0
    for name, (cands, kind) in res.items():
        D["duanban"]["themes"][name]["cands"] = cands
        D["duanban"]["themes"][name]["candKind"] = kind
        if a.all or kind != "theme":
            txt = "、".join(f"{c['name']}({c['hit']})" for c in cands) or "（空）"
            print(f"  {name:6} [{kind}] {txt}")
    n_th = sum(1 for _n, (c, k) in res.items() if k == "theme")
    n_c = sum(len(c) for c, _k in res.values())
    print(f"[theme_cands] {len(res)} 个题材 · 同题材命中 {n_th} 个 · 候选共 {n_c} 只"
          f"（兜底 {len(res) - n_th} 个题材走同行业/词表）")
    if a.dry:
        return 0
    common.write_data_js(path, D, label="[theme_cands]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

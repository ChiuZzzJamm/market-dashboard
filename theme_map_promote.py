#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""theme_map_promote.py — 把「今日自动成组的待确认题材」收进正式词表（R100z356）。

## 为什么需要它（用户 2026-11-17 22:22「那就前者」）

词表只有 12 个题材，市场轮动到表外时「题材掘金」整块空着（11-17：6 只连板——
家电、消费电子、变压器、AI应用、GPU芯片，一个都没命中）。用户选定的方案是：

    当日连板 reason **自动成组**（≥2 只同词才成组）→ 页面标「待确认」→ **人工确认后**才进正式词表

自动成组在 `common.auto_theme_groups()`；这个脚本是「确认」那一半的入口。
它只做一件事：把 `data.js` 里某个 auto 组（连同成员、行业）写成 `theme_map.json` 的一条正式题材，
并补齐 kw/ckw（词表里两个字段的语义：kw 判**文本**（连板 reason / 新闻标题），ckw 判**概念**
（同花顺主题材 hot））。写完之后 `theme_cands.py` 下一轮就会按 ckw 正常算补涨候选。

## 用法

    python3 theme_map_promote.py                    # 列出今日可确认的自动组（不写盘）
    python3 theme_map_promote.py --name 家电        # 看这一组收进词表会长什么样（dry）
    python3 theme_map_promote.py --name 家电 --write
    python3 theme_map_promote.py --name 家电 --alias 小家电 --extra-kw 家电下乡 --write
    python3 theme_map_promote.py --help

## 边界（与别处一致）

  · 只从**今天** data.js 的 auto 组里取，不从历史猜——「确认」必须有人看过那天的盘面；
  · 重名直接拒绝（正式题材优先，auto 组不许抢名）；
  · 写盘走 `common.write_json_atomic`（关键 JSON 不收口就会静默降级）；
  · 不做自动确认：`--write` 必须显式给 `--name`，这是「人工」的唯一含义。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common  # noqa: E402

MAP_FILE = os.path.join(HERE, "theme_map.json")


def _slug(name):
    """中文名 → 稳定 id（拼音不可用时就用序号式 hash，只要能唯一）。"""
    h = 0
    for ch in name:
        h = (h * 131 + ord(ch)) % 100000
    return "auto%05d" % h


def _load_map(path=None):
    with open(path or MAP_FILE, encoding="utf-8") as f:
        return json.load(f)


def _auto_groups(D):
    out = {}
    for name, t in (((D.get("duanban") or {}).get("themes")) or {}).items():
        if isinstance(t, dict) and t.get("auto"):
            out[name] = t
    return out


def _build_entry(name, t, aliases, extra_kw):
    """auto 组 → 正式词表条目。kw/ckw 用「组名 + 同日出现的同簇词 + 人工补的词」。"""
    alias = []
    for a in list(t.get("alias") or []) + list(aliases or []):
        a = str(a or "").strip()
        if a and a != name and a not in alias:
            alias.append(a)
    kws = [name] + alias
    for k in (extra_kw or []):
        k = str(k or "").strip()
        if k and k not in kws:
            kws.append(k)
    stocks = []
    for b in (t.get("boardStocks") or []):
        code = str(b.get("code") or "")
        if not code:
            continue
        stocks.append({"code": code, "name": str(b.get("name") or ""),
                       "role": "%s 连板 %d 板（自动成组确认）"
                               % (str(t.get("_asOf") or "当日"), int(b.get("lbc") or 1)),
                       "lbc": int(b.get("lbc") or 1)})
    return {
        "id": _slug(name),
        "name": name,
        "alias": alias,
        "kw": kws,
        "ckw": kws,
        "sectors": list(t.get("sectors") or []),
        "stocks": stocks,
        "exclude": [],
        "note": "R100z356：由 %s 的自动成组确认而来（人工确认后进词表，之后 theme_cands 按 ckw 算候选）"
                % str(t.get("_asOf") or "当日"),
    }


def main():
    ap = argparse.ArgumentParser(description="把今日自动成组的待确认题材收进正式词表")
    ap.add_argument("--name", default="", help="要确认的题材名（必须与页面上的组名一致）")
    ap.add_argument("--alias", action="append", default=[], help="额外别名（可重复）")
    ap.add_argument("--extra-kw", action="append", default=[], help="额外的 kw/ckw（可重复）")
    ap.add_argument("--data", default="data.js", help="data.js 路径")
    ap.add_argument("--map", default=MAP_FILE, help="theme_map.json 路径（测试时可指向副本）")
    ap.add_argument("--write", action="store_true", help="真的写入 theme_map.json（默认 dry）")
    a = ap.parse_args()

    D = common.load_dashboard_data(HERE)
    if not D:
        print("[ERR] 读不到 data.js")
        return 2
    groups = _auto_groups(D)
    td = str((D.get("ashare") or {}).get("tradeDate") or "")
    for t in groups.values():
        t["_asOf"] = td

    if not groups:
        print("今日没有自动成组的题材（duanban.themes 里没有 auto=True 的条目）。")
        print("可能原因：今天没有 ≥2 只同词的连板，或 16:00 数据步还没重算 themes。")
        return 0

    if not a.name:
        print("今日可确认的自动成组题材（%s）：" % (td or "日期未知"))
        for name, t in groups.items():
            print("  %-8s %d 只 · 最高 %s 板 · %s"
                  % (name, t.get("n") or 0, t.get("maxLb") or 1,
                     "、".join(str(b.get("name") or "") for b in (t.get("boardStocks") or []))))
        print("\n确认某一组：python3 theme_map_promote.py --name <组名> --write")
        return 0

    if a.name not in groups:
        print("[ERR] 今日自动组里没有「%s」。现有：%s" % (a.name, "、".join(groups)))
        return 2
    m = _load_map(a.map)
    if any(str(t.get("name")) == a.name for t in (m.get("themes") or [])):
        print("[ERR] 「%s」已经在正式词表里，不需要再确认（重名会让 match_themes 双计）" % a.name)
        return 2

    entry = _build_entry(a.name, groups[a.name], a.alias, a.extra_kw)
    print("将写入 theme_map.json：")
    print(json.dumps(entry, ensure_ascii=False, indent=2)[:1200])
    if not a.write:
        print("\n（dry：加 --write 才落盘）")
        return 0

    m.setdefault("themes", []).append(entry)
    m["updatedAt"] = str((D.get("ashare") or {}).get("tradeDate") or m.get("updatedAt") or "")
    common.write_json_atomic(a.map, m, label="[theme_map_promote]")
    print("\n[OK] 已写入 theme_map.json：%s（共 %d 个题材）" % (a.name, len(m["themes"])))
    print("下一步：python3 theme_cands.py 会按新条目的 ckw 重算补涨候选；"
          "下一轮 16:00 断板步起，这一组就以正式题材渲染（不再带「待确认」）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

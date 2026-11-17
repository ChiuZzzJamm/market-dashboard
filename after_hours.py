#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""after_hours.py — 昨日收盘后至今晨的 A 股公告（07:00 那趟补盲区用，R100z357）。

## 为什么要有它

用户 2026-11-17 23:09：「中国稀土收购这类新闻一般几点出的？会有可能 19:30 之后出吗？」

实测（`stockNews` 4877 条带时间戳的条目，事件型 746 条）：
    19 点 239 / 20 点 179 / 21 点 149 —— **19~21 点占 76%**，19:00 后到次晨 7:00 前占 **82%**。
也就是说：**19:30 叙事步部署完之后，A 股公告还在源源不断地出**（收购/重组/停牌/异动/减持
几乎全在盘后发）。而 `stock_news.py` 16:00 只跑一次，19:30 到次日 16:00 之间 A 股这套
**没有任何入口**——实测 11-16 21:08 的异动公告，直到 11-17 16:00 才进 stockNews（滞后 19 小时）。

07:00 那趟本来就在跑（美股收盘），它以前顺手把这类公告塞进了 `us.bullNews`，于是读者在
「美股利好」里看到「中国稀土收购中稀凉山 51%」。**不是它多管闲事，是只有它在跑、又放错了口袋。**

本脚本负责把它放回该去的地方：读池内标的的公告 → 与 16:00 的 `stockNews.notices` 快照做差 →
把「昨日盘后至今晨新增」的写成 `ashare.afterHours`，页面在 A 股要闻卡顶部单列一节。

## 口径（宁少不编）

- **时间只到日**：东财/巨潮的公告 API 只给披露日期（`notice_date` 恒为 00:00），
  拿不到真实发布分钟。所以页面写「11-16 盘后」，**绝不编一个 21:08 出来**（那是媒体的发稿时间，
  不是公告披露时间，混用会让读者拿错时间线去做决策）。
- 「新增」= 与 16:00 快照的 (date, title) 差集。16:00 就收过的不会再列，避免同一件事说两遍。
- 只覆盖**池内 + 今日候选**（约 100~150 只主板），不是全市场——它是「你关心的票昨晚发了什么」，
  不是公告大全。
- 抓不到就返回空 + `degraded=true`，页面显示「未取到」而不是「暂无公告」（24.65）。

## 用法

    python3 after_hours.py --help
    python3 after_hours.py                # 干跑：只打印，不写盘
    python3 after_hours.py --write        # 写 data.js 的 ashare.afterHours（持锁）
    python3 after_hours.py --hours 16 --write --budget 120
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common  # noqa: E402

CAP = 12            # 页面最多列几条（再多读者也看不完，且会把卡顶撑高）


def _help_guard():
    ap = argparse.ArgumentParser(
        description="昨日收盘后至今晨的 A 股公告（写 data.js 的 ashare.afterHours）")
    ap.add_argument("--write", action="store_true", help="真写 data.js（默认干跑）")
    ap.add_argument("--data", default="data.js", help="data.js 路径")
    ap.add_argument("--hours", type=int, default=16,
                    help="回看小时数（默认 16 = 昨日 15:00 → 今日 07:00）")
    ap.add_argument("--budget", type=float, default=120, help="抓取预算秒（默认 120）")
    ap.add_argument("--cap", type=int, default=CAP, help="最多保留条数")
    ap.add_argument("--all", action="store_true", help="打印全部条目（默认只打汇总）")
    return ap.parse_args()


def targets(D):
    """要盯的票：断板双池 + 陈学长三池 + 昨日精选 + AI 预测（主板 60/00，去重）。"""
    out = {}

    def take(e, zh):
        if not isinstance(e, dict):
            return
        c = str(e.get("code") or "")[-6:]
        if len(c) != 6 or not c.isdigit():
            return
        if not c.startswith(("60", "00")):      # 硬约束 3：只许沪深主板
            return
        if c not in out:
            out[c] = {"code": c, "name": str(e.get("name") or ""), "from": zh}

    db = D.get("duanban") or {}
    cp = D.get("chenPool") or {}
    for k, zh in (("confirmed", "断板·确认"), ("watching", "断板·观察")):
        for e in (db.get(k) or []):
            take(e, zh)
    for k, zh in (("strict", "陈学长·严格"), ("loose", "陈学长·放宽"), ("watching", "陈学长·观察")):
        for e in (cp.get(k) or []):
            take(e, zh)
    for e in ((db.get("star") or {}).get("picks") or []):
        take(e, "开盘精选")
    for sec in ((D.get("aiPrediction") or {}).get("sectors") or []):
        for e in (sec.get("stocks") or []):
            take(e, "AI预测")
    return out


def _shift(day, n):
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def build(D, hours=16, budget=120.0):
    """返回 (模块 dict, 诊断 dict)。不写盘。"""
    import check_duanban as CD          # 复用公告抓取（含当日缓存/并发/预算/分类器）

    now = datetime.now()
    td = str((D.get("ashare") or {}).get("tradeDate") or "")[:10]
    # 昨日收盘 = tradeDate 那天的 15:00；但 07:00 跑的时候 tradeDate 还是**昨日**
    # （16:00 才换日），所以起点直接取「昨天 15:00」，终点取现在。
    since = (now - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    since_day = since[:10]
    tgt = targets(D)

    snaps = {}
    for c, rec in ((D.get("stockNews") or {})).items():
        keys = set()
        for n in (rec.get("notices") or []):
            keys.add((str(n.get("date") or "")[:10], str(n.get("title") or "")[:24]))
        snaps[str(c)[-6:]] = keys

    notices = CD.fetch_notices_batch(list(tgt), budget=budget)
    got = sum(1 for v in notices.values() if v)
    items, dup = [], 0
    for c, lst in notices.items():
        nm = (tgt.get(c) or {}).get("name") or ""
        for n in (lst or []):
            d = str(n.get("date") or "")[:10]
            if not d or d < since_day:
                continue
            title = str(n.get("title") or "").strip()
            if not title:
                continue
            if (d, title[:24]) in (snaps.get(c) or set()):
                dup += 1
                continue
            # 时间槽（R100z357b）：以交易日为界——早于/等于 tradeDate 的是「盘后」，
            # 之后的看是不是周末。周末 22:00 那趟会带出周五盘后+周末公告，不能全叫「早间」。
            _wd = datetime.strptime(d, "%Y-%m-%d").weekday() if d else 0
            slot = ("盘后" if d <= td else ("周末" if _wd >= 5 else "早间"))
            items.append({
                "code": c, "name": nm,
                "day": d,
                "slot": slot,
                "title": title[:60],
                "column": str(n.get("column") or ""),
                "dir": (CD.classify_notice(title) or "neutral"),
                "from": (tgt.get(c) or {}).get("from") or "",
            })
    items.sort(key=lambda x: (x["day"], x["code"]), reverse=True)
    mod = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M"),
        "since": since,
        "sinceDay": since_day,
        "tradeDate": td,
        "n": len(items),
        "items": items[:CAP],
        # 抓不到 ≠ 没有（24.65）：只统计「确实返回过数据的标的数」，否则页面要说「未取到」
        "covered": got,
        "targets": len(tgt),
        "degraded": bool(tgt) and got * 2 < len(tgt),
        "note": ("昨日收盘后至今晨的 A 股公告（与 16:00 快照做差后的新增）；"
                 "时间只到日——公告 API 只给披露日期，页面写「盘后/早间」，不编具体分钟"),
    }
    return mod, {"done": len(tgt), "got": got, "dup": dup, "kept": len(items)}


def main():
    a = _help_guard()
    path = a.data if os.path.isabs(a.data) else os.path.join(HERE, a.data)
    D = common.load_dashboard_data(os.path.dirname(path))
    if not D:
        print("[ERR] 读不到 data.js")
        return 2
    if not targets(D):
        print("[SKIP] data.js 里没有池内/候选标的，无需抓隔夜公告")
        return 0

    mod, diag = build(D, hours=a.hours, budget=a.budget)
    print("[after_hours] 盯 %d 只（主板）· 返回数据 %d 只 · 与 16:00 快照重复 %d 条 · 新增 %d 条"
          % (diag["done"], diag["got"], diag["dup"], diag["kept"]))
    if mod["degraded"]:
        print("[WARN] 只有 %d/%d 只抓到公告 —— 本轮按「未取到」处理，页面不会说「暂无公告」"
              % (diag["got"], diag["done"]))
    show = (mod["items"] if a.all else mod["items"][:6])
    for x in show:
        print("  %s %-6s %s %s | %s" % (x["day"], x["slot"], x["code"], x["name"], x["title"][:34]))

    if not a.write:
        print("（干跑：加 --write 才写 data.js）")
        return 0
    if mod["degraded"] and not mod["items"]:
        # 一只都没抓到：保留旧值，绝不写空覆盖（防清场 R91m）
        print("[WARN] 全批未取到公告，保留旧值不写盘")
        return 1
    D.setdefault("ashare", {})["afterHours"] = mod
    common.write_data_js(path, D, label="[after_hours]")
    print("[OK] ashare.afterHours 已写入：%d 条（截至 %s）" % (mod["n"], mod["updatedAt"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

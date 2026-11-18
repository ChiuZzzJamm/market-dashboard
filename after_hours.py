#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""after_hours.py — 「盘后 · 隔夜」A 股要闻（昨日收盘后到今晨的**全市场**重要公告，R100z359）。

## 它回答什么（用户 2026-11-17 23:09 的原问）

「中国稀土收购这类新闻一般几点出的？会有可能 19:30 之后出吗？如果会，那美股早上更新掉是不是好事？」

实测（stockNews 4877 条带时间戳的条目，事件型 746 条）：**19~21 点占 76%**，19:00 后到次晨 7:00
前占 **82%**。而 A 股这套的入口只有 16:00 的 `stock_news.py`——19:30 部署完到次日 16:00 之间
**没有任何地方能看到昨晚发的公告**（实测 11-16 21:08 的异动公告，直到 11-17 16:00 才进 stockNews）。

## 位置与范围（用户 2026-11-18 09:28 明确纠正过两次，别再自己发挥）

- **位置**：A 股要闻卡里的一节（`#gnews-view` 的 A 股卡），标题「盘后 · 隔夜（N 条 · 截至 HH:MM）」。
  2026-11-18 凌晨我把它挪到了技术看板、还改名「池内 · 昨夜公告」——那是**我自己加的解读**，
  用户原话是「我昨天说的是要 A 股要闻卡加一节」，已改回。
- **范围**：**全市场**（沪深主板），不是只有池内那 90 多只。用户同样点名过「你怎么改的是池内标的」。
- **筛选**：只留**事件型**公告（收购/重组/停牌复牌/异动/减持/增持/回购/控制权/立案/中标/业绩预告/
  风险提示…），程序性公告（股东会/决议/会议资料/说明会/担保/融资租赁/募集资金账户）不列——
  这是「要闻」，不是公告大全（2026-11-17 23:30 截图那次教训）。

## 时间口径（这版能给到分钟）

东财**单只**公告接口的 `notice_date` 恒为 00:00（只有日期），但**全市场**接口带 `display_time`
（形如 `2026-11-17 21:08:09:735`，真实披露时刻）。所以按分钟过滤、页面也显示分钟——
之前「时间只到日」的限制只适用于单只接口。

## 用法

    python3 after_hours.py --help
    python3 after_hours.py                    # 干跑：只打印
    python3 after_hours.py --write            # 写 data.js 的 ashare.afterHours（持锁）
    python3 after_hours.py --hours 60 --write # 周末版（周五收盘 → 周日）
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common  # noqa: E402

CAP = 20          # 页面最多列几条
ANN_API = "https://np-anotice-stock.eastmoney.com/api/security/ann"
PAGE_SIZE = 100
MAX_PAGES = 8     # 800 条封顶：足够覆盖一个夜盘，再多就是爬全站了

# ---- 重大性过滤（R100z357b，用户 2026-11-17 23:30：「都是个股公告吗？」那次）----
MATERIAL_KW = (
    "收购", "重组", "重大合同", "中标", "停牌", "复牌", "异动", "风险提示",
    "减持", "增持", "回购", "控制权", "实际控制人", "股权转让", "资产出售",
    "资产注入", "剥离", "问询", "立案", "诉讼", "仲裁", "业绩预告", "预增",
    "分红", "配股", "定增", "可转债", "对外投资", "签署", "战略合作", "订单",
)
PROCEDURAL_KW = (
    "股东会", "董事会决议", "监事会", "会议资料", "法律意见书", "章程",
    "管理制度", "担保", "融资租赁", "募集资金", "专项账户", "独立董事",
    "会计师事务所", "续聘", "换届", "选举", "说明会",
)


def is_material(title):
    """这条公告算不算「消息」：必须命中 MATERIAL_KW，且不能只命中程序性词。"""
    t = str(title or "")
    if not any(k in t for k in MATERIAL_KW):
        return False
    if any(k in t for k in PROCEDURAL_KW) and not any(
            k in t for k in ("收购", "重组", "停牌", "复牌", "异动", "减持", "增持",
                             "控制权", "立案", "中标", "业绩预告", "风险提示")):
        return False
    return True


# 要闻排序：先「大事」再「时段」。增持计划/董事增持这类天天有几十条，按时间排会把重组/立案淹掉。
# ⚠️ 「实际控制人」不进 TIER1：它更多出现在「实际控制人增持/提供资金支持」这种一般消息里，
# 放进来会把「增持计划」顶到重组前面（11-18 实测第一条就是山鹰国际的增持计划）。
TIER1_KW = ("重组", "收购", "出售", "资产注入", "资产出售", "停牌", "复牌", "控制权",
            "立案", "问询", "重大合同", "中标", "业绩预告", "风险提示",
            "破产", "重整", "仲裁", "诉讼")


def tier_of(title):
    """1=大事（重组/收购/停复牌/控制权/立案/中标/业绩预告…）2=一般（增持/减持/回购/异动…）。"""
    return 1 if any(k in str(title or "") for k in TIER1_KW) else 2


def _help_guard():
    ap = argparse.ArgumentParser(
        description="盘后·隔夜 A 股要闻（全市场重要公告，写 data.js 的 ashare.afterHours）")
    ap.add_argument("--write", action="store_true", help="真写 data.js（默认干跑）")
    ap.add_argument("--data", default="data.js", help="data.js 路径")
    ap.add_argument("--hours", type=int, default=16,
                    help="回看小时数（默认 16 = 昨日 15:00 → 今日 07:00；周末版用 60）")
    ap.add_argument("--cap", type=int, default=CAP, help="最多保留条数")
    ap.add_argument("--all", action="store_true", help="打印全部条目（默认只打前几条）")
    return ap.parse_args()


def _dt(txt):
    """接口时间 → datetime。`2026-11-17 21:08:09:735` / `2026-11-17 00:00:00` 都吃。"""
    s = str(txt or "").strip()
    if not s:
        return None
    s = s[:19].replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if fmt != "%Y-%m-%d" else s[:10], fmt)
        except ValueError:
            continue
    return None


def fetch_market(since, max_pages=MAX_PAGES, page_size=PAGE_SIZE):
    """按页拉全市场公告，直到最老一条早于 since 或页数封顶。返回 (原始条目, 诊断)。"""
    import check_duanban as CD          # 复用 get_json_curl（带 UA/重试/gap）
    out, pages, err = [], 0, ""
    for p in range(1, max_pages + 1):
        url = ("%s?sr=-1&page_size=%d&page_index=%d&ann_type=A&client_source=web"
               "&f_node=0&s_node=0" % (ANN_API, page_size, p))
        try:
            data = CD.get_json_curl(url, retries=1, gap=2)
        except Exception as e:                               # noqa: BLE001
            err = str(e)[:80]
            break
        lst = (((data or {}).get("data") or {}).get("list")) or []
        if not lst:
            break
        pages = p
        out.extend(lst)
        times = [_dt(x.get("display_time") or x.get("notice_date")) for x in lst]
        times = [t for t in times if t]
        if times and min(times) < since:
            break
    return out, {"pages": pages, "raw": len(out), "err": err}


def build(D, hours=16, cap=CAP):
    import check_duanban as CD
    now = datetime.now()
    since = now - timedelta(hours=hours)
    td = str((D.get("ashare") or {}).get("tradeDate") or "")[:10]
    raw, diag = fetch_market(since)

    seen, items, procedural, offboard = set(), [], 0, 0
    for x in raw:
        t = _dt(x.get("display_time") or x.get("notice_date"))
        if not t or t < since:
            continue
        title = str(x.get("title") or "").strip()
        if not title:
            continue
        codes = x.get("codes") or []
        code = str((codes[0] or {}).get("stock_code") or "") if codes else ""
        name = str((codes[0] or {}).get("short_name") or "") if codes else ""
        if len(code) != 6 or not code.isdigit():
            continue
        # 硬约束 3：只许沪深主板（这里虽是新闻不是推荐，但页面出现 300/688 会与「只做主板」冲突）
        if not code.startswith(("60", "00")):
            offboard += 1
            continue
        if not is_material(title):
            procedural += 1
            continue
        # 同一只票在同一分钟常常一口气发 4~5 份（重组报告书 + 摘要 + 财务顾问核查 + 风险提示）——
        # 那是**一个事件**拆成多份文件，逐条列会把整个列表占满（11-18 实测：珠海中富 20:23 连发 4 份）。
        # 按「代码 + 分钟」去重，留第一份（接口按时间倒序，第一份通常是主公告）。
        key = (code, t.strftime("%Y-%m-%d %H:%M"))
        if key in seen:
            continue
        seen.add(key)
        day = t.strftime("%Y-%m-%d")
        slot = "盘后" if day <= td else ("周末" if t.weekday() >= 5 else "早间")
        items.append({
            "code": code, "name": name,
            "day": day, "clock": t.strftime("%H:%M"),
            "slot": slot,
            "title": title[:60],
            "column": str(((x.get("columns") or [{}])[0] or {}).get("column_name") or ""),
            "dir": (CD.classify_notice(title) or "neutral"),
            "tier": tier_of(title),
        })

    # 大事在前、同档按时间倒序（早上扫一眼要看到「谁重组了」，不是「谁增持了 100 万股」）
    items.sort(key=lambda z: (z["day"], z["clock"]), reverse=True)
    items.sort(key=lambda z: z["tier"])
    mod = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M"),
        "since": since.strftime("%Y-%m-%d %H:%M"),
        "sinceDay": since.strftime("%Y-%m-%d"),
        "tradeDate": td,
        "scope": "market",                 # 全市场（沪深主板），不是只池内
        "n": len(items),
        "items": items[:cap],
        "pages": diag["pages"],
        "raw": diag["raw"],
        # 一条都没抓到 ≠ 没有公告（24.65）：raw=0 才标降级，页面写「未取到」
        "degraded": diag["raw"] == 0,
        "note": ("昨日收盘后至今晨的 A 股重要公告（全市场·沪深主板，按披露时间过滤）；"
                 "只列收购/重组/停牌复牌/异动/回购/减持/中标/业绩预告/风险提示这一类消息"),
    }
    return mod, {"kept": len(items), "procedural": procedural, "offboard": offboard, **diag}


def main():
    a = _help_guard()
    path = a.data if os.path.isabs(a.data) else os.path.join(HERE, a.data)
    D = common.load_dashboard_data(os.path.dirname(path))
    if not D:
        print("[ERR] 读不到 data.js")
        return 2

    mod, diag = build(D, hours=a.hours, cap=a.cap)
    print("[after_hours] 近 %dh 全市场公告：翻 %d 页拿到 %d 条 → 程序性剔除 %d、非主板 %d、"
          "留下消息 %d 条" % (a.hours, diag["pages"], diag["raw"], diag["procedural"],
                              diag["offboard"], diag["kept"]))
    if diag["err"]:
        print("[WARN] 抓取中断：%s" % diag["err"])
    if mod["degraded"]:
        print("[WARN] 一条都没取到 —— 本轮按「未取到」处理，页面不会说「暂无公告」")
    for x in (mod["items"] if a.all else mod["items"][:8]):
        print("  %s %s %-6s %s | %s" % (x["day"][5:], x["clock"], x["code"], x["name"],
                                         x["title"][:34]))

    if not a.write:
        print("（干跑：加 --write 才写 data.js）")
        return 0
    if mod["degraded"]:
        print("[WARN] 全批未取到，保留旧值不写盘（防清场 R91m）")
        return 1
    D.setdefault("ashare", {})["afterHours"] = mod
    common.write_data_js(path, D, label="[after_hours]")
    print("[OK] ashare.afterHours 已写入：%d 条（%s → %s）"
          % (mod["n"], mod["since"], mod["updatedAt"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

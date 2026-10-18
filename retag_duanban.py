#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R91p 断板反包重打标（2026-09-23，R100z148 增强）：
用 data.js **当前**要闻/AI预测 + 东财近 7 日个股公告对已部署 duanban 模块
重算 sentiment / bullRefs / bearRefs / story。用途：08:30 隔夜新闻写回后
刷新断板口径，弥补「07:00 复核在 08:30 新闻更新之前」的时序缺口。
打标口径与 check_duanban.tag_entries 完全一致（R91p 加权：个股级×2、板块级×1，
bear 加权 > bull 加权才判 bear）。池成员、K线、板块涨幅等行情字段一律不动。
R100z148 两处变化：① 抓取池内标的近 7 日东财公告（盘后发布的终止收购/
减持/立案等方向性公告按个股级×2 参与打标；公告 API 失败时自动降级为纯
本地新闻匹配，不阻塞）；② story 由 build_story 数据驱动重生成（≥100 字、
【个股消息】/【板块消息】分段），但情绪未变且旧 story 已达标时保留 AI
深化版，不被底稿覆盖。

用法：python3 retag_duanban.py [data.js 路径，默认当前目录]
"""
import json
import os
import sys
from datetime import datetime

# R100z51：原写死旧项目路径 /Users/loccco/WorkBuddy/2026-09-04-11-53-22/market-dashboard
# （目录已删，靠 sys.path[0] 侥幸 import 到本地 check_duanban，换个 cwd 就 ImportError）。
# 改为把本脚本所在目录插到最前，永远 import 到当前项目内的 check_duanban。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_duanban as cd  # noqa: E402

TZ8 = cd.TZ8 if hasattr(cd, "TZ8") else None

# R100z60 清理：BAND（区间判定版）定义后零引用——该方案中途放弃、改用下面的 DEFAULT_P 单点阈值，
# 残留的死常量会让下一个人以为「区间档位」还在用，删掉。
DEFAULT_P = {"bull": 70, "bear": 30, "neutral": 48}


def main():
    data_path = sys.argv[1] if len(sys.argv) > 1 else "data.js"
    s = open(data_path, encoding="utf-8").read()
    i = s.index("{")
    D = json.loads(s[i:s.rindex("}") + 1])
    db = D.get("duanban") or {}
    if not db.get("confirmed") and not db.get("watching"):
        print("[info] duanban 模块为空，跳过重打标")
        return
    changed = []
    # R100z148: 与 check_duanban --module 同一口径——批量抓池内标的近 7 日公告，
    # 让盘后发布的个股公告（终止收购/减持/立案等）在次日 08:30 刷新断板口径时
    # 即可打标，不必等 16:00 数据步。此前 sentiment 唯一数据源是 data.js 要闻，
    # 而 AI 要闻检索覆盖不到单只个股的公司公告（2026-10-15 东百集团「终止收购」
    # 公告后连续跌停、池内仍 neutral 无消息——用户实报缺陷的根因之一）。
    try:
        notices = cd.fetch_notices_batch(
            [e.get("code") for pool in ("confirmed", "watching")
             for e in (db.get(pool) or [])])
        for pool in ("confirmed", "watching"):
            for e in db.get(pool) or []:
                e["notices"] = notices.get(str(e.get("code"))) or []
        n_hit = sum(1 for pool in ("confirmed", "watching")
                    for e in (db.get(pool) or []) if e.get("notices"))
        print("[info] 公告抓取：%d 只近 %d 日有公告（打标依据增强）"
              % (n_hit, cd.NOTICE_WINDOW_DAYS))
    except Exception as ex:
        print("[warn] 公告抓取失败（不影响重打标主流程）: %s" % ex)
    for pool in ("confirmed", "watching"):
        entries = db.get(pool) or []
        if not entries:
            continue
        retagged = cd.tag_entries(entries, D)
        for old, new in zip(entries, retagged):
            if new.get("sentiment") != old.get("sentiment"):
                old_sent, new_sent = old.get("sentiment"), new.get("sentiment")
                bw = sum(2 if r.get("kind") == "stock" else 1 for r in new.get("bearRefs") or [])
                rw = sum(2 if r.get("kind") == "stock" else 1 for r in new.get("bullRefs") or [])
                # 情绪翻转时 probability 重置到新区间参考值，probNote 说明依据，
                # 待下一轮 16:00 数据步 AI 复核校准
                new["probability"] = DEFAULT_P.get(new_sent)
                new["probNote"] = {
                    "bull": f"自动重打标：利好线索加权{rw}压过利空{bw}",
                    "bear": f"自动重打标：利空线索加权{bw}压过利好{rw}",
                    "neutral": "自动重打标：近1日无明确方向新闻",
                }.get(new_sent, "")
                changed.append((pool, old.get("code"), old.get("name"),
                                old_sent, new_sent, rw, bw))
            else:
                # 情绪未变：AI 已校准过（probNote 非空）→ 保留；未校准（如 merge
                # 旧兜底的全 50、probNote 空）→ 保留 tag_entries 新算的确定性基线
                # 并按池微调（确认池 +4 / 观察池 -2，与 build_module 口径一致）。
                if old.get("probability") is not None and str(old.get("probNote") or "").strip():
                    new["probability"] = old.get("probability")
                    new["probNote"] = old.get("probNote")
                else:
                    p = max(15, min(88, round((new.get("probability") or 50)
                                              + (4 if pool == "confirmed" else -2))))
                    new["probability"] = p
                    new["probNote"] = (new.get("probNote") or "") + "（补算）"
            # R100z148：AI 深化版 story 保留——情绪未变且旧 story 已达标
            # （≥100 字 + 【】段首格式，R100z3）时保留旧值，不被 build_story
            # 数据驱动底稿覆盖（16:00/07:00 步 AI 逐只深化的个股消息不因早间
            # 重打标丢失）；情绪翻转（新公告改判）或旧 story 不达标
            # （29 字默认句等）才用新底稿替换。
            old_story = str(old.get("story") or "")
            if (new.get("sentiment") == old.get("sentiment")
                    and len(old_story) >= 100 and old_story.startswith("【")):
                new["story"] = old_story
        db[pool] = retagged
    db["generatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    db["note"] = db.get("note") or ""
    out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ";\n"
    with open(data_path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"[info] 重打标完成：{len(changed)} 只改判")
    for pool, code, name, o, n, rw, bw in changed:
        print(f"  [{pool}] {code} {name}: {o} -> {n}（bull加权 {rw} vs bear加权 {bw}）")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R91p 断板反包重打标（2026-09-23）：
用 data.js **当前**要闻/AI预测对已部署 duanban 模块重算
sentiment / bullRefs / bearRefs / story（纯本地新闻匹配，零东财依赖，
本机沙箱可跑）。用途：08:30 隔夜新闻写回后刷新断板口径，
弥补「07:30 复核在 08:30 新闻更新之前」的时序缺口。
打标口径与 check_duanban.tag_entries 完全一致（R91p 加权：个股级×2、板块级×1，
bear 加权 > bull 加权才判 bear）。池成员、K线、板块涨幅等行情字段一律不动。

用法：python3 retag_duanban.py [data.js 路径，默认当前目录]
"""
import json
import sys
from datetime import datetime

sys.path.insert(0, "/Users/loccco/WorkBuddy/2026-09-04-11-53-22/market-dashboard")
import check_duanban as cd  # noqa: E402

TZ8 = cd.TZ8 if hasattr(cd, "TZ8") else None

BAND = {"bull": (55, 85), "bear": (15, 40), "neutral": (40, 55)}
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
                # 待下一轮 16:00 自动化 AI 复核校准
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

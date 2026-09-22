#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用云端抓取的 64 板块日 kline（/tmp/bk_part1..8.json）计算每只标的的
boardPctZt（涨停日当日板块涨幅）= close(ztDate)/close(前一交易日)-1。
写入 data.js 每标的 boardPctZt 字段。
"""
import json, os, glob

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data.js")
DRAFT = os.path.join(HERE, "draft.json")
PARTS = sorted(glob.glob("/tmp/bk_part*.json"))

# code -> [(date, close), ...] 升序
KL = {}
for pf in PARTS:
    for obj in json.load(open(pf, encoding="utf-8")):
        rec = obj["data"] if "data" in obj else obj
        code = rec["code"]
        kl = []
        for s in rec["klines"]:
            d, c = s.split(",")
            kl.append((d, float(c)))
        kl.sort(key=lambda x: x[0])
        KL[code] = kl

# code -> ztDate
draft = json.load(open(DRAFT, encoding="utf-8"))
ZT = {}
for pool in ("confirmed", "watching"):
    for e in draft.get(pool) or []:
        ZT[str(e.get("code"))] = e.get("ztDate")


def board_pct_on(code, zt_date):
    kl = KL.get(code)
    if not kl or not zt_date:
        return None
    idx = None
    for i, (d, _c) in enumerate(kl):
        if d == zt_date:
            idx = i
            break
    if idx is None or idx == 0:
        return None
    close_zt = kl[idx][1]
    close_prev = kl[idx - 1][1]
    if close_prev == 0:
        return None
    return round((close_zt / close_prev - 1) * 100, 2)


s = open(DATA, encoding="utf-8").read()
i = s.index("{", s.index("window.DASHBOARD_DATA"))
j = s.rindex("}")
D = json.loads(s[i:j + 1])

db = D.get("duanban", {})
done = 0
miss = 0
miss_list = []
for pool in ("confirmed", "watching"):
    for e in db.get(pool) or []:
        code = str(e.get("code"))
        bk = e.get("bkCode")
        zt = ZT.get(code)
        if bk and zt:
            v = board_pct_on(bk, zt)
            if v is not None:
                e["boardPctZt"] = v
                done += 1
                continue
        # 无法计算：保持原值（通常为 null）
        miss += 1
        miss_list.append((code, bk, zt))

D["duanban"] = db
out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, indent=2) + ";\n"
open(DATA, "w", encoding="utf-8").write(out)
print(f"[fill_zt] 计算完成 {done} 只；未计算 {miss} 只: {miss_list[:20]}")
print(f"[fill_zt] 板块 kline 覆盖 {len(KL)} 个")

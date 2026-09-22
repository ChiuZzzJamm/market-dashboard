#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud-filled 申万口径 topBoards + 每只标的 boardPctToday（板块今日涨幅）。
boardPctZt 由后续 kline 批处理脚本写入。
数据来源：东财板块 clist（云端 WebFetch 抓取 2026-09-22 收盘），与 stock sector(申万) 同源。
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data.js")
DRAFT = os.path.join(HERE, "draft.json")

# 64 个板块（BK 码 -> [申万行业名, 今日涨幅f3]），来自云端 clist（2026-09-22 收盘）
BOARDS = {
    "BK0428": ["电力", -0.72], "BK0440": ["家居用品", 1.0], "BK0448": ["通信设备", -0.62],
    "BK0450": ["航运港口", -1.8], "BK0454": ["塑料", -0.27], "BK0457": ["电网设备", -0.68],
    "BK0459": ["元件", 1.1], "BK0465": ["化学制药", 0.74], "BK0473": ["证券Ⅱ", 2.9],
    "BK0476": ["装修建材", -0.44], "BK0481": ["汽车零部件", -0.07], "BK0482": ["一般零售", -0.75],
    "BK0538": ["化学制品", 0.22], "BK0539": ["综合Ⅱ", 0.0], "BK0545": ["通用设备", -0.1],
    "BK0546": ["玻璃玻纤", -0.96], "BK0725": ["装修装饰Ⅱ", -0.36], "BK0727": ["医疗服务", 0.81],
    "BK0731": ["农化制品", -0.73], "BK0734": ["饰品", 0.07], "BK0735": ["计算机设备", 1.09],
    "BK0737": ["软件开发", 1.72], "BK0738": ["多元金融", -0.59], "BK0910": ["专用设备", -0.36],
    "BK1016": ["汽车服务", -0.45], "BK1018": ["橡胶", 0.16], "BK1019": ["化学原料", -0.49],
    "BK1027": ["小金属", -0.76], "BK1030": ["电机Ⅱ", -0.13], "BK1031": ["光伏设备", -0.15],
    "BK1032": ["风电设备", -1.04], "BK1033": ["电池", -0.14], "BK1036": ["半导体", 0.46],
    "BK1037": ["消费电子", 0.3], "BK1038": ["光学光电子", -0.16], "BK1040": ["中药Ⅱ", -0.16],
    "BK1041": ["医疗器械", 1.14], "BK1042": ["医药商业", -0.34], "BK1046": ["游戏Ⅱ", 1.28],
    "BK1220": ["广告营销", 3.45], "BK1221": ["数字媒体", 1.81], "BK1223": ["其他电子Ⅱ", -0.58],
    "BK1225": ["服装家纺", -0.17], "BK1228": ["冶钢原料", -1.27], "BK1233": ["军工电子Ⅱ", -0.06],
    "BK1235": ["环境治理", 0.37], "BK1237": ["自动化设备", 0.22], "BK1238": ["IT服务Ⅱ", 1.72],
    "BK1244": ["小家电", 1.28], "BK1245": ["照明设备Ⅱ", 0.17], "BK1248": ["专业工程", -0.56],
    "BK1255": ["林业Ⅱ", 0.06], "BK1259": ["养殖业", -0.38], "BK1261": ["种植业", -0.24],
    "BK1264": ["商用车", 0.53], "BK1265": ["包装印刷", 0.08], "BK1266": ["文娱用品", 1.29],
    "BK1267": ["造纸", -0.4], "BK1268": ["互联网电商", 1.69], "BK1272": ["旅游及景区", -1.96],
    "BK1274": ["炼化及贸易", -0.29], "BK1279": ["非白酒", -0.51], "BK1281": ["休闲食品", 0.14],
    "BK1287": ["工业金属", 0.51],
}
NAME2CODE = {v[0]: k for k, v in BOARDS.items()}

# 申万口径 TOP10（近3日涨幅居前，与 stock sector 同源；pct3/pctToday 来自云端 clist 2026-09-22）
TOP10 = [
    ["BK0727", "医疗服务", 13.15, 0.81], ["BK1243", "其他家电Ⅱ", 11.93, 5.0],
    ["BK1045", "房地产服务", 11.07, 1.67], ["BK1044", "生物制品", 10.99, 1.64],
    ["BK1268", "互联网电商", 10.97, 1.69], ["BK1218", "出版", 10.09, 2.35],
    ["BK1036", "半导体", 9.78, 0.46], ["BK1266", "文娱用品", 9.77, 1.29],
    ["BK0458", "仪器仪表", 8.73, 1.08], ["BK1220", "广告营销", 8.7, 3.45],
]


def match_board(sector):
    if not sector:
        return None
    s = sector.strip()
    # 精确
    if s in NAME2CODE:
        return NAME2CODE[s]
    # 前缀互配（sector 可能4字截断）
    for name, code in NAME2CODE.items():
        if len(s) >= 2 and (name.startswith(s) or s.startswith(name)):
            return code
    return None


def main():
    s = open(DATA, encoding="utf-8").read()
    i = s.index("{", s.index("window.DASHBOARD_DATA"))
    j = s.rindex("}")
    D = json.loads(s[i:j + 1])
    draft = json.load(open(DRAFT, encoding="utf-8"))
    zt = {}
    for pool in ("confirmed", "watching"):
        for e in draft.get(pool) or []:
            zt[str(e.get("code"))] = e.get("ztDate")

    db = D.get("duanban", {})
    matched = 0
    unmatched = []
    for pool in ("confirmed", "watching"):
        for e in db.get(pool) or []:
            code = str(e.get("code"))
            sec = e.get("sector") or ""
            bk = match_board(sec)
            if bk:
                e["bkCode"] = bk
                e["boardPctToday"] = BOARDS[bk][1]
                matched += 1
            else:
                e["bkCode"] = e.get("bkCode")
                e["boardPctToday"] = e.get("boardPctToday")
                unmatched.append((code, sec))
    db["topBoards"] = [
        {"code": c, "name": n, "pct3": round(p3, 2), "pctToday": round(pt, 2)}
        for c, n, p3, pt in TOP10
    ]
    D["duanban"] = db
    out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, indent=2) + ";\n"
    open(DATA, "w", encoding="utf-8").write(out)
    print(f"[fill] 匹配板块 {matched} 只；未匹配 {len(unmatched)} 只: {unmatched[:20]}")
    print(f"[fill] topBoards 已写 {len(db['topBoards'])} 条（申万口径）")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R100z9x：开盘精选 AI 文案写回（只改 duanban.star 的 sentiment / pushText）。

纪律：唯一写入口 duanban.star；其余字段（ashare/us/断板池/谐波/九门/吸筹/fullScan…）
一律按原样 round-trip 回去，不做任何加工。
"""
import datetime
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data.js")
NODE = "/Users/loccco/.workbuddy/binaries/run-node"

# R100z162：原为硬编码日期，每天必须由 AI 手改，忘了就撞下面的日期断言、白耗一个回合。
# 改为系统日期：守卫（star.date 必须等于今天）照旧生效，但不再需要人工维护。
TODAY = datetime.date.today().isoformat()

# 可选：picks 删减白名单（只删不加）。空集合=保持 update_star.py 原样。
DROP_CODES = set()

SENTIMENT = (
    "开盘板块情绪：白酒+3.06%（主力+8.4亿）一枝独秀，教育、港口航运仅微涨不足1%；"
    "元件-5.09%、贵金属-3.90%、通信设备-3.55%领跌。"
    "资金情绪：净流入仅白酒、汽车零部件、电池为正，食品加工-3.8亿、银行-3.2亿、保险-2.4亿，"
    "缩圈防御而非全面撤退。合格板块唯一白酒：断板池皇台酒业得板块确认但未走完K线，列待确认；"
    "技术池谐波与吸筹多落电子通信链条，须盘口承接才成立。"
)

PUSHTEXT = (
    "①情绪与资金：三大指数低开低走，上证-1.31%、深成-2.15%、创业板-3.17%，半小时5858亿无恐慌放量。"
    "90行业仅白酒真强+3.06%、主力净流入8.4亿，元件-5.09%、通信设备-3.55%领跌。"
    "资金正流入仅白酒、汽车零部件+4.5亿、电池+3.6亿，净流出集中食品加工、银行、保险。\n\n"
    "②推演：白酒唯一双正向，月末防御叠加，日内有望延续，但靠抱团非增量，防午后回吐。"
    "元件、通信设备跌超3.5%且资金未回补，杀估值非错杀，反抽不改趋势。"
    "汽车零部件资金+4.5亿但涨幅未转正，只观察。\n\n"
    "③映射：确认档10只、待确认档6只（陈学长观察5、断板观察1，缩量回踩0只）。"
    "横盘启动三只中鸿路钢构92分、山东路桥78分爆量拉升、均价线上移，洗盘承接转启动成立；"
    "新城控股有量下杀无承接，不接。谐波与吸筹受电子通信拖累，爱旭股份下探站回均价线可观察，"
    "广汇能源等三只整理只能等。待确认档只说差什么：皇台酒业现涨7.2%仍缺一根确认K线，"
    "中重科技、索通发展等5只观察票今天只盯不买。\n\n"
    "④纪律：6只爆量拉升、1只下探站回可观察，1只有量下杀不接，5只整理与3只平衡一律等。"
    "买点5只在陈学长放宽池且均在买区：塞力医疗15.93~16.25、福莱新材47.31~48.27、"
    "良品铺子9.40~9.58、南方传媒14.84~15.14、湘佳股份13.40~13.68。"
    "昨夜挂单8只7只可挂，菲林格尔高于限价不追；"
    "代价照原话：买价相对T日最低价中位溢价+9.2%、43%单子超+10%，回测17.9%触发止损均亏5.5%，"
    "每只1/32资金，别看重就加重。落后股今日无候选（无板块跳空超+1%），这一路不出手。"
)


def main():
    with open(DATA, encoding="utf-8") as f:
        s = f.read()
    i = s.index("{")
    D = json.loads(s[i:s.rindex("}") + 1])

    star = D.setdefault("duanban", {}).get("star")
    if not star:
        print("[FAIL] duanban.star 不存在——须先跑 update_star.py，不允许凭空造 star")
        sys.exit(1)
    if star.get("date") != TODAY:
        print(f"[FAIL] star.date={star.get('date')} 非今日，拒绝改写")
        sys.exit(1)

    if DROP_CODES:
        picks = star.get("picks", [])
        keep = [p for p in picks if p.get("code") not in DROP_CODES]
        dropped = [p.get("code") for p in picks if p.get("code") in DROP_CODES]
        if dropped:
            star["picks"] = keep
            print(f"[star] picks 删减 {dropped}，剩余 {len(keep)} 只")

    star["sentiment"] = SENTIMENT
    star["pushText"] = PUSHTEXT

    out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ";\n"
    # R100z179（L11 硬约束）：先写临时文件、node --check 通过再 os.replace。
    # 原写法 open(DATA,'w') 先截断再写、写完才 check——进程若在 write 中途被杀
    # （平台 90 分钟硬上限 / 超时重试），留下的是残缺 data.js，而 check 这一步
    # 根本没机会执行。临时名必须带 .js，node 22 拒绝未知扩展名。
    _tmp = DATA + ".tmp.js"
    with open(_tmp, "w", encoding="utf-8") as f:
        f.write(out)
    _chk = subprocess.run([NODE, "--check", _tmp], capture_output=True, text=True, timeout=60)
    if _chk.returncode != 0:
        os.remove(_tmp)
        raise SystemExit("[FAIL] 改写结果未通过 node --check，已丢弃、data.js 保持原样：\n"
                         + (_chk.stderr or "")[:800])
    os.replace(_tmp, DATA)
    print(f"[ok] sentiment {len(SENTIMENT)} 字 / pushText {len(PUSHTEXT)} 字，node --check 通过")


if __name__ == "__main__":
    main()
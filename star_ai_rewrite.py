#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R100z9x：开盘精选 AI 文案写回（只改 duanban.star 的 sentiment / pushText）。

纪律：唯一写入口 duanban.star；其余字段（ashare/us/断板池/谐波/九门/吸筹/fullScan…）
一律按原样 round-trip 回去，不做任何加工。
"""
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data.js")
NODE = "/Users/loccco/.workbuddy/binaries/run-node"

TODAY = "2026-10-16"

SENTIMENT = (
    "开盘三大指数小幅红盘（上证+0.28%、创业板+0.28%），两市成交约1.45万亿，"
    "军工产业链领涨（装备+6.49%）、汽车零部件+3.30%跟涨，影视/游戏/元件领跌。"
    "资金面净流入集中于军工装备+23.7亿、汽车零部件+14.7亿、房地产+12.4亿，呈军工+汽车+地产高低切换。"
    "合格板块锁定军工装备、汽车零部件、房地产；断板池中马传动、粤宏远A获双确认，"
    "技术池非合格板块仅看个股竞价强弱。"
)

PUSHTEXT = (
    "【一、开盘板块情绪与资金】今日A股三大指数小幅红盘，上证+0.28%（竞价+0.44%）、"
    "深成+0.46%、创业板+0.28%（竞价+1.75%），两市成交约1.45万亿。"
    "盘面极端分化：军工产业链领涨（装备+6.49%），汽车零部件+3.30%跟涨；"
    "影视院线-1.68%、游戏-1.70%、元件-2.59%领跌。资金面主力净流入集中于军工装备+23.7亿、"
    "汽车零部件+14.7亿、房地产+12.4亿，呈军工+汽车+地产高低切换；化学制品、白色家电净流出。\n\n"
    "【二、板块当日推演】军工装备、汽车零部件开盘强势且资金强配合，日内有望延续；"
    "但军工装备单日+6.49%涨幅偏大，午后谨防冲高回落，汽车零部件趋势更健康。"
    "房地产虽净流入+12.4亿但缺乏板块领涨、多为个股行情；游戏、影视、元件开盘弱势且资金出逃，继续回避。\n\n"
    "【三、精选标的映射】断板池：中马传动（汽车零部件）竞价+1.0%、现价+5.6%翻红放大，"
    "强于板块、获双确认，反包概率提升；粤宏远A（房地产开）竞价与现价双封+10.1%，已涨停追高性价比低。"
    "技术池：安泰集团（九门9分）竞价-1.9%低开、现价+1.3%修复翻红=资金抢筹，确认度最佳；"
    "华林证券（九门9分）竞价-0.7%、现价-1.9%双弱，剔除观察；移远通信（吸筹78分）竞价+4.7%高开、"
    "现价+0.5%高开低走偏弱；和远气体（谐波）竞价+3.5%高开、现价-1.3%翻绿未获确认；"
    "浦发银行、山西焦煤两谐波标的竞价弱修复、信号中性。\n\n"
    "【操作提示】军工主线可顺势不宜追高，断板池两支有板块背书可重点看；"
    "技术池仅安泰集团抢筹信号明确，其余竞价与现价双弱者剔除观察，放量确认再上车。"
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

    star["sentiment"] = SENTIMENT
    star["pushText"] = PUSHTEXT

    out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ";\n"
    with open(DATA, "w", encoding="utf-8") as f:
        f.write(out)
    subprocess.run([NODE, "--check", DATA], check=True, timeout=60)
    print(f"[ok] sentiment {len(SENTIMENT)} 字 / pushText {len(PUSHTEXT)} 字，node --check 通过")


if __name__ == "__main__":
    main()
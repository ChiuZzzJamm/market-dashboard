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

TODAY = "2026-10-20"

SENTIMENT = (
    "三大指数低开弱势震荡，上证-0.27%、深成-0.22%、创业板-0.35%，半小时成交5012亿。"
    "行业52涨70跌：元件+1.78%、电子化学品Ⅱ+1.81%领涨，地面兵装Ⅱ-4.01%、工业金属-2.40%垫底。"
    "资金净流出42.62亿，元件+10.66亿、消费电子+4.54亿、半导体+3.49亿逆势吸筹，弃周期抱电子。"
    "合格板块32个，军工全线杀跌，断板池无合格挂钩，精选全取技术池。"
)

PUSHTEXT = (
    "【开盘板块情绪与资金】三大指数低开弱势震荡，上证-0.27%（竞价-0.33%）、深成-0.22%、创业板-0.35%（竞价-0.50%），"
    "半小时两市成交5012亿，量能中性。行业52涨70跌，结构分化：元件+1.78%、电子化学品Ⅱ+1.81%领涨；"
    "地面兵装Ⅱ-4.01%、工业金属-2.40%垫底。主力合计净流出42.62亿，流出集中："
    "工业金属-8.49亿、电池-7.93亿、电力-5.01亿、证券Ⅱ-4.31亿；元件+10.66亿、消费电子+4.54亿、"
    "半导体+3.49亿逆势加仓，呈弃周期抱电子的高低切换。\n\n"
    "【板块当日推演】电子链（元件、消费电子、半导体）量价资金三共振，日内有望延续，但元件吸金已超10亿，"
    "防午后冲高回落；工程机械+0.54%、自动化设备+0.98%小幅净流入，可延续。"
    "工业金属、电池失血居前，不宜抄底；地面兵装Ⅱ-4.01%破位、军工情绪冰点，相关标的回避。\n\n"
    "【精选标的映射】移远通信竞价+1.2%开到+7.8%全场最强，但通信设备-0.01%、净流出3.27亿，属个股独立行情，防高位滞涨；"
    "楚天龙同板块竞价+0.6%回落-0.1%，弱于移远。"
    "徐工机械竞价-0.3%、现价-0.3%，弱于工程机械板块，等翻红。高斯贝尔竞价-0.4%修复至+1.0%，低开后有资金抢筹，可观察。"
    "甘化科工竞价-5.5%修复至-1.2%，但军工装备全线杀跌、压制未解。"
    "鱼跃医疗竞价+0.1%转-0.6%，医疗器械净流出0.44亿，双弱剔除。\n\n"
    "【操作提示】断板池今日无合格板块挂钩，精选全部沿用前一日16:00筛选的谐波、九门、吸筹技术池；"
    "情绪分化日不追高，以放量确认再上车为准。"
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
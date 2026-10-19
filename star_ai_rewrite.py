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

TODAY = "2026-10-19"

SENTIMENT = (
    "开盘三大指数低开低走，创业板-1.90%最弱、成交5389亿。行业分化：银行+0.91%、白酒+0.60%领涨，"
    "电子化学品-3.10%、元件-3.42%、非金属材料-3.46%领跌。资金避险抱团，主力净流入银行+20.1亿、"
    "白酒+7.7亿、电力+3.1亿居前。合格板块（涨+资金双正向）仅银行、白酒。断板反包仅皇台酒业挂钩白酒获支撑；"
    "技术池九门高分股强势，但移远通信、楚天龙竞价现价双弱待确认。"
)

PUSHTEXT = (
    "【开盘板块情绪与资金】今日三大指数低开低走，上证-0.48%（竞价-0.60%）、深成-0.87%、"
    "创业板-1.90%（竞价-0.43%）显著偏弱，半小时两市成交约5389亿。行业极端分化：银行+0.91%、"
    "白酒+0.60%领涨，电子化学品-3.10%、元件-3.42%、非金属材料-3.46%领跌。资金面主力净流入银行+20.1亿、"
    "白酒+7.7亿、电力+3.1亿居前，净流出汽车零部件-5.0亿、小家电-2.5亿、工业金属-2.3亿，呈明显高低切换与避险抱团。\n\n"
    "【板块当日推演】银行领涨且吸金全场第一，高股息避险逻辑延续，盘中或惯性冲高但连续强势后谨防回吐；"
    "白酒逆势获资金回补、力度弱于银行，属防御+消费修复；电子/元件/非金属材料大幅净流出、汽车零部件分歧加剧，"
    "科技成长线继续回避。合格板块仅银行、白酒（涨+资金双正向）。\n\n"
    "【精选标的映射】断板池皇台酒业（白酒，50%）：竞价-5.8%深低开后翻红至+1.8%，白酒情绪支撑其反包概率，"
    "但板块力度偏弱，需放量确认。技术池：金奥博（九门9分，化工）竞价+1.1%→现涨+8.7%、中信海直（九门9分，交运）"
    "竞价+3.0%→+6.7%，竞价高开高走、量化九分与放量共振，形态强确认；中船防务（谐波Butterfly）竞价-1.0%→+2.7%翻红，"
    "D点获个股资金确认；招商蛇口（谐波Gartley）现价平盘待放量；移远通信（吸筹78分）竞价-3.0%→-1.9%、"
    "楚天龙（吸筹76分）-2.5%→-1.1%双弱，吸筹未获开盘确认，暂列观察。\n\n"
    "【操作提示】创业板偏弱、高位科技回避，精选以低位回补+量化强势为主，放量确认再上车、不追高。"
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
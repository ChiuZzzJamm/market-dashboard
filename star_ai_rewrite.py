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

TODAY = "2026-10-15"

SENTIMENT = (
    "指数大幅低开低走：上证-1.79%、深成-2.52%、创业板-3.15%，竞价已反映跌幅，半小时成交"
    "约11990亿。板块极端分化：白酒+1.78%独领涨，厨卫电器+0.49%、元件+0.11%跟涨，港口"
    "航运-3.73%、油气服务-4.14%、煤炭加工-5.14%领跌。资金高低切换：净流入化学制药"
    "+11.4亿、白酒+8.5亿，净流出电力-15.3亿。合格板块仅白酒——断板池皇台酒业、"
    "张裕A获双确认。"
)

PUSHTEXT = (
    "【开盘情绪与资金】三大指数大幅低开低走，上证-1.79%（竞价-1.50%）、深成-2.52%"
    "（竞价-2.40%）、创业板-3.15%（竞价-3.13%），竞价已反映跌幅，开盘后未现修复，"
    "半小时成交约11990亿。行业极端分化：白酒+1.78%独领涨，厨卫电器+0.49%、元件+0.11%"
    "跟涨；港口航运-3.73%、油气开采及服务-4.14%、煤炭开采加工-5.14%领跌。主力高低切换，"
    "净流入化学制药+11.4亿、白酒+8.5亿、电网设备+3.0亿，净流出电力-15.3亿、"
    "建筑装饰-9.2亿、汽车零部件-8.7亿。\n\n"
    "【板块推演】①白酒是唯一涨跌幅与资金双正向的合格板块，+1.78%配+8.5亿净流入，"
    "防御抱团明确，日内有望延续，但防午后兑现、勿追高；②化学制药净流入居首而涨幅"
    "未入前列，属防御建仓；③煤炭-5.14%领跌且净流出居前，继续回避；"
    "④电力净流出-15.3亿居首，宝新能源方向受压制。\n\n"
    "【标的映射】断板池2只均挂钩白酒：皇台酒业竞价-0.4%、现涨+10.0%封板，情绪与资金双"
    "确认、反包成立，但已涨停只等回踩不追；张裕A竞价-2.3%、现涨-1.6%，同属酒类却弱于"
    "板块，基线54%只观察。技术池4只：九门9分的引力传媒竞价-4.2%、现涨-1.6%，双弱"
    "=资金未抢筹，剔除观察；吸筹76分的锦龙股份竞价-1.8%→现涨+4.6%，低开修复=抢筹，"
    "确认度最高；谐波Cypher浦发银行竞价+0.0%、现涨-1.1%，双弱无防御；谐波Shark"
    "宝新能源现涨-0.3%，电力为最大净流出方向。\n\n"
    "【操作】指数放量杀跌、情绪冰点，勿追高不接飞刀；断板池只做回踩确认后的反包。"
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
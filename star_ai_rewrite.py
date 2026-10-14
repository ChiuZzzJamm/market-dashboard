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

TODAY = "2026-10-14"

SENTIMENT = (
    "指数低开震荡：上证-0.18%、深成-0.58%、创业板-0.89%，成交约4348亿。"
    "游戏+2.38%、油气+2.12%、煤炭+1.16%领涨，风电-3.34%、电商-2.32%领跌。"
    "主力净流入银行+16.0亿、电力+9.8亿、煤炭+4.6亿，净流出电池-9.9亿、建材-5.9亿，"
    "资金切向防御与资源。合格板块（涨幅+资金双正向）：煤炭开采加工、银行、电力——"
    "断板池电力标的获双确认，反包成立。"
)

PUSHTEXT = (
    "【开盘情绪与资金】三大指数低开震荡，上证-0.18%（竞价-0.27%）、深成指-0.58%、"
    "创业板指-0.89%，半小时成交约4348亿。行业分化：游戏+2.38%、油气开采及服务+2.12%、"
    "煤炭开采加工+1.16%领涨，风电设备-3.34%、互联网电商-2.32%、种植业与林业-2.29%领跌。"
    "主力资金净流入银行+16.0亿、电力+9.8亿、煤炭开采加工+4.6亿，净流出电池-9.9亿、"
    "建筑材料-5.9亿、化学制品-4.0亿，资金自高位成长切向防御与资源。\n\n"
    "【板块推演】①电力量价资金三共振，日内有望延续，断板反包逻辑直接受益；"
    "②银行净流入居首但涨幅温和，防御主导，宜低吸不追高；③煤炭涨1.16%配4.6亿净流入，"
    "资源与供暖预期下可看延续；④风电设备领跌且资金流出，继续回避；游戏领涨但资金未进"
    "前列，防冲高回落。\n\n"
    "【标的映射】断板池四只全部挂钩电力合格板块：广西能源平开高走+6.6%最强，反包情绪"
    "确认；杭州热电高开+3.0%站稳；惠天热电竞价-0.7%低开后快速翻红+4.7%，属资金抢筹"
    "特征；宁波能源竞价-0.4%现涨+1.4%跟涨稳健。技术池：阳光乳业九门9分，竞价+10.0%"
    "大幅高开、现涨+7.6%强于板块，高开幅度大需防冲高回落；海欣股份竞价-4.9%深跌、"
    "现价-0.8%修复近半，承接初现但仍双弱，只观察；视觉中国吸筹80分窄幅红盘，形态待"
    "确认；锦龙股份吸筹76分现涨+2.8%，与银行资金流入共振；创新医疗谐波Crab，竞价"
    "+2.9%转+2.0%，量价配合中性偏好。\n\n"
    "【操作】指数未放量、情绪结构分化，不追高开过大标的；电力为今日主攻方向，破竞价位止损。"
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
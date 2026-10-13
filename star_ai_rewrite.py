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

TODAY = "2026-10-13"

SENTIMENT = (
    "开盘全线低开走弱：上证-1.40%、深成-2.55%、创业板-3.37%，半小时成交约5412亿，量能向下。"
    "板块分化：白酒+2.81%、煤炭+0.83%飘红，电子化学品-5.00%、半导体-4.79%、医疗服务"
    "-4.88%领跌。资金切换，军工+4.73亿、煤炭+3.70亿流入，建筑-8.06亿、电力-6.01亿流出。"
    "合格板块仅煤炭（双正向），断板池空缺；两谐波确认（启明星辰、浦发银行）抗跌入选。"
)

PUSHTEXT = (
    "【开盘板块情绪与资金】三大指数全线低开走弱，上证-1.40%（竞价-0.96%）、深成-2.55%、"
    "创业板-3.37%（竞价-2.18%），半小时成交约5412亿，量能未缩但方向向下。行业极致分化："
    "白酒+2.81%、煤炭+0.83%逆势飘红，油气+0.18%勉强收红；另一端电子化学品-5.00%、医疗"
    "服务-4.88%、半导体-4.79%、消费电子-4.41%、电池-4.35%集体重挫。资金高低切换：军工"
    "装备+4.73亿、农产品加工+4.70亿、煤炭+3.70亿小幅净流入，建筑装饰-8.06亿、电力-6.01亿、"
    "文化传媒-3.27亿大幅净流出——钱从高位成长与周期退潮，仅防御与资源微幅承接。\n\n"
    "【板块推演】白酒独立走强但无量配合、追高性价比低；煤炭+0.83%配+3.70亿逆势合力，看"
    "午后量能；半导体/电子/医疗服务深跌且资金大幅流出，情绪冰点延续，继续回避。合格板块"
    "（涨+资金双正向）仅煤炭开采加工，断板反包池无标的与之挂钩，反包侧空缺。\n\n"
    "【标的映射】精选两只均属谐波确认形态、弱市显著抗跌：①启明星辰（电子信息·Gartley确认）"
    "竞价-1.5%、现涨-0.9%，跌幅远小于电子链（电子化学品-5%），属「竞价与现价双弱但强于"
    "板块」的错杀修复候选，待放量站回再确认；②浦发银行（金融·Cypher确认）竞价0.0%、现涨"
    "-0.5%，银行相对抗跌，形态获金融避险情绪支撑，列为低波动观察。其余谐波/九门/吸筹候选"
    "因早盘深跌（多数<-2%）已被守卫剔除，宁缺勿滥。\n\n"
    "【操作】情绪冰点勿追高；白酒/煤炭仅防守观察。谐波两标的形态确认、现价弱但强于板块，"
    "待放量收复竞价位再分批，破位则放弃。"
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
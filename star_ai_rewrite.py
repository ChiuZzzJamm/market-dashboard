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

SENTIMENT = (
    "开盘情绪偏暖但结构集中：通信设备+2.79%、影视院线+2.61%、养殖业+2.59%领涨，"
    "公路铁路运输-0.26%、电池-0.42%、银行-0.45%小幅走弱；资金端通信设备+32.0亿、元件+27.6亿、"
    "半导体+21.3亿净流入，呈科技硬件单极吸金格局。三大指数全线翻红，半小时两市成交5294亿。"
    "合格板块通信设备、半导体、元件对断板反包形成支撑，但元件池内多只高开低走，需防板块强个股弱的背离。"
)

PUSHTEXT = (
    "【板块情绪与资金】三大指数集体翻红，上证+0.03%（竞价+0.14%）、深成+0.62%、创业板+0.52%，"
    "半小时两市成交约5294亿，指数稳而结构强。领涨通信设备+2.79%、影视院线+2.61%、养殖业+2.59%；"
    "公路铁路运输、电池、银行小幅走弱，跌幅均在0.5%以内。主力净流入通信设备+32.0亿、元件+27.6亿、"
    "半导体+21.3亿，流出端建筑装饰-1.4亿、小金属-0.8亿。"
    "资金高度压在科技硬件，权重金融让位，是高低切换初段。\n\n"

    "【板块推演】通信设备、半导体、元件同时满足涨幅与资金双正向，日内延续概率偏高，"
    "但竞价即高开、涨幅已不小，追高性价比下降，宜等回踩确认。元件吸金最猛却个股分化，"
    "要防板块强、个股弱的背离；影视院线、养殖业属情绪脉冲，缺资金持续验证，冲高回落风险大。"
    "公路铁路运输、银行防御失血，本次回避。\n\n"

    "【标的映射】断板池5只全挂钩合格板块：至纯科技（半导体·利好·基线82%）竞价+0.3%、现价平盘，"
    "有半导体+21.3亿托底，反包逻辑最顺；中京电子竞价+1.0%后现涨+0.8%，为元件池内最健康；"
    "骏亚科技、澳弘电子、协和电子同挂元件却从高开回落至-5.7%、-3.6%、-5.9%，属冲高回落，"
    "反包打折，只观察不追。技术池6只：海正药业（九门9分）竞价+3.1%、现涨+4.6%，高开续强为最强共振；"
    "楚天龙（吸筹76分）+1.5%、北方铜业（谐波Crab）+1.2%翻红，形态获确认；"
    "三六零、新宏泰（谐波Butterfly）零轴震荡待方向；南威软件竞价+0.7%后回落-1.6%，弱于竞价，列入观望。\n\n"

    "【操作】情绪暖而不普涨，只做板块共振强票，弱于板块即撤。"
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
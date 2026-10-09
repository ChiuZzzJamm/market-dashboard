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

SENTIMENT = (
    "开盘一路走弱：上证-0.68%（竞价-0.20%）、深成-1.46%、创业板-1.89%，成长端跌幅"
    "接近主板三倍。贵金属+1.24%、种植业与林业+1.06%逆势领涨，元件-3.69%、其他电子"
    "-3.23%领跌，科技链失血。主力净流入仅电池+10.8亿、贵金属+6.6亿、中药+5.7亿，"
    "净流出工业金属-3.8亿、文化传媒-3.2亿，合格板块即三者。断板池仅国轩高科挂钩合格"
    "板块但现跌2.3%；技术池仅天融信翻红。"
)

PUSHTEXT = (
    "【开盘情绪与资金】上证-0.68%（竞价-0.20%）、深成-1.46%（竞价-0.51%）、"
    "创业板-1.89%（竞价-0.50%），低开后逐级下行，成长端跌幅接近主板三倍；沪市成交约"
    "2739亿、深市约3085亿，量能不枯竭但方向向下。行业极端分化：贵金属+1.24%、"
    "种植业与林业+1.06%、影视院线+0.99%逆势红盘，元件-3.69%、其他电子-3.23%居后。"
    "主力与涨幅同向，净流入为电池+10.8亿、贵金属+6.6亿、中药+5.7亿，净流出为"
    "工业金属-3.8亿、文化传媒-3.2亿——资金单向从高位科技抽向资源防御。\n\n"
    "【板块推演】电池资金强度最高且继续放大（+10.8亿），但板块指数未同步走强，属个股"
    "抱团而非行业共振，日内更可能分化，追高谨慎；贵金属在指数下跌中逆势翻红，商品属性"
    "与防御共振，是当前唯一可作防守的方向；元件、其他电子连续第二日领跌且资金流出，"
    "继续回避；中药净流入而涨幅仅+0.74%，属超跌修复。\n\n"
    "【标的映射】断板池仅国轩高科（电池·基线79%）挂钩合格板块，但竞价-3.0%、现价-2.3%，"
    "低开续弱，板块资金流入却拉不动个股，反包逻辑未获确认。技术池竞价强弱分化：九门9分"
    "国新能源竞价+9.6%、现价-1.9%，高开回落属冲高兑现；吸筹82分移远通信竞价-0.7%、"
    "现价-0.1%，吸筹76分天融信竞价-0.9%、现价+0.4%，竞价弱于现价、跌幅收敛；谐波"
    "招商蛇口平开后现跌-1.9%，招金黄金竞价+1.6%回落至-0.6%。竞价强弱：高开且现价"
    "翻红=强于板块，低开快修=资金抢筹，双弱=剔除观察；按此仅天融信获确认。情绪冰点"
    "勿追高，放量翻红再上车。"
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
    if star.get("date") != "2026-10-09":
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
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

TODAY = "2026-10-12"

SENTIMENT = (
    "指数走弱：上证-0.12%（竞价-0.18%）、深成-0.85%、创业板-0.97%。资金高低切换："
    "净流入银行+25.9亿、电力+6.2亿、风电设备+5.9亿；净流出半导体-31.4亿、通信设备"
    "-17.6亿、房地产-15.0亿。合格板块（涨幅与资金双正向）为风电设备+3.70%、银行"
    "+1.42%、电网设备+1.05%。断板池唯一挂钩风电设备的中船科技属利空剔除，反包侧"
    "空缺；技术池仅新筑股份获确认。"
)

PUSHTEXT = (
    "【开盘情绪与资金】上证-0.12%（竞价-0.18%）、深成-0.85%、创业板-0.97%（竞价-0.65%），"
    "低开续弱，半小时两市成交约6755亿、量能不枯竭但方向向下。涨幅居前：风电设备+3.70%、"
    "银行+1.42%、电网设备+1.05%；居后：电子化学品-3.09%、房地产-2.90%、半导体-2.71%、"
    "元件-2.60%。资金同向：银行+25.9亿、电力+6.2亿、风电设备+5.9亿净流入；半导体"
    "-31.4亿、通信设备-17.6亿、房地产-15.0亿净流出——钱从高位科技链抽向银行与电力设备。\n\n"
    "【板块推演】银行41家全红零下跌、配+25.9亿，合力最硬，日内有望延续但空间已透支，"
    "追高需回踩；风电设备+3.70%配+5.9亿、29涨3跌，但近4%涨幅已透支，防冲高回落；"
    "电网设备内部分化，跟风补涨；半导体-2.71%配-31.4亿、171家下跌，继续回避。\n\n"
    "【标的映射】断板池仅风电设备的中船科技（现涨+3.4%）对上合格板块，但属利空剔除，"
    "反包侧空缺；其余非利空标的集中在房地产、化学制药、元件，恰是净流出方向。"
    "技术池5只分三档：①九门9分新筑股份（电力）竞价-2.5%、现涨+5.3%，低开修复超7个百分点，"
    "属「低开修复=资金抢筹」且强于电力板块（+0.39%），涨速过快宜等回踩；②九门9分贝瑞"
    "基因竞价-0.2%、现涨+1.9%，低开微修双稳，列二线；③吸筹76分锦龙股份现跌-1.9%，双弱转"
    "观察；④⑤谐波恺英网络现跌-0.6%、云南铜业回落至-0.8%，撞上小金属-2.36%属冲高兑现。\n\n"
    "【操作】情绪冰点勿追高，风电设备等回踩放量再上车；半导体/元件/通信设备回避。"
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
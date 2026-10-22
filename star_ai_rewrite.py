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
DROP_CODES = {"002085", "605255"}

SENTIMENT = (
    "开盘走弱分化：上证-0.87%、深成-1.54%、创业板-2.33%；电机+4.73%、工程机械+2.29%领涨，"
    "元件-2.63%、文化传媒-2.71%、影视院线-3.04%领跌。资金端电池+6.8亿、汽车零部件+4.4亿、"
    "证券+3.7亿净流入，光学光电子-14.4亿、银行-11.3亿流出。合格板块仅汽车零部件与证券，"
    "前者仅+0.03%、内部130涨136跌，对反包支撑名义大于实质。"
)

PUSHTEXT = (
    "【板块情绪与资金】三大指数低开走弱，上证-0.87%（竞价+0.04%）、深成-1.54%、创业板-2.33%。"
    "半小时成交6489亿，较昨日同期5294亿放量约两成，属放量下跌。"
    "领涨电机+4.73%、工程机械+2.29%；领跌元件-2.63%、文化传媒-2.71%、"
    "影视院线-3.04%。主力净流入电池+6.8亿、汽车零部件+4.4亿、证券+3.7亿；"
    "净流出光学光电子-14.4亿、银行-11.3亿、软件开发-6.0亿。"
    "资金撤离科技硬件与权重金融，转投低位制造与券商，属防御性切换。\n\n"

    "【板块推演】电机、工程机械涨幅居前但资金未同步，电机主力实为净流出，属超跌反弹，"
    "日内防冲高回落。汽车零部件虽涨幅与资金双正向，但板块仅微幅翻红、内部130涨136跌近乎对半，"
    "4.4亿摊到百余只票并不宽裕，属分化行情而非板块级机会，须挑个股。"
    "元件、传媒、影视院线继续失血，回避；证券净流入3.7亿仅涨0.05%，护盘为主。\n\n"

    "【标的映射】断板池7只全挂汽车零部件：浙江荣泰竞价平开后现涨+7.6%、"
    "日盈电子-0.6%快速翻红至+4.5%、亚普股份+2.9%，三只逆板块走强，抢筹明确；"
    "铭科精技、新坐标、旭升集团零轴震荡；天汽模平开后跌2.4%，弱于板块。"
    "天普股份、万丰奥威竞价走弱后跌3.0%、8.0%，背离板块，已剔除。"
    "技术池3只：润建股份（吸筹62分）竞价高开+6.9%回落至+2.8%，未守一半涨幅，资金试盘；"
    "移远通信（九门9分）竞价-0.9%、现跌1.2%，通信设备走弱，未确认；"
    "青山纸业（谐波Butterfly）跌1.9%，观望。\n\n"

    "【操作】放量下跌日不追高，只做逆板块翻红的强票，跌破竞价即撤。"
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
    with open(DATA, "w", encoding="utf-8") as f:
        f.write(out)
    subprocess.run([NODE, "--check", DATA], check=True, timeout=60)
    print(f"[ok] sentiment {len(SENTIMENT)} 字 / pushText {len(PUSHTEXT)} 字，node --check 通过")


if __name__ == "__main__":
    main()
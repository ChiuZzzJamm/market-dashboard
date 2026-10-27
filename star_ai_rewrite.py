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
DROP_CODES = set()

SENTIMENT = (
    "指数高开后回落，上证-0.16%、深成+0.03%、创业板-0.03%，成交 5869 亿。"
    "板块偏防御，影视院线+1.81%、电子化学品+1.47% 领涨，医疗服务-1.72% 领跌；"
    "资金同向，影视院线+7.1亿、汽车零部件+5.7亿、工业金属+4.2亿流入，元件-2.4亿流出，"
    "双正向合格板块只这三个。双池扎堆消费电子、自动化设备、半导体，与合格板块无交集，"
    "本次不参与，10 只全为技术池标的。"
)

PUSHTEXT = (
    "【情绪与资金】三大指数高开后集体回落，上证-0.16%（开盘+0.02%）、深成+0.03%、创业板-0.03%，"
    "半小时成交 5869 亿。影视院线+1.81%、电子化学品+1.47% 领涨，医疗服务-1.72%、化学制药-1.44% 领跌。"
    "资金同向：影视院线+7.1亿、汽车零部件+5.7亿、工业金属+4.2亿流入，元件-2.4亿流出。"
    "双正向合格板块只这三个，撑不起指数。\n\n"

    "【板块推演】影视院线唯一量价齐升，但+7.1亿已属集中买入，日内可延续、防冲高回落。"
    "汽车零部件+5.7亿更像资金低吸，日内横磨，不是进攻主线；工业金属+4.2亿是资源独苗，看铜价配合。"
    "元件、通用设备净流出居前是抽血方向，反弹按修复看；医疗服务领跌无接盘，回避。\n\n"

    "【标的映射】10 只全来自技术池，断板双池不参与——池内扎堆消费电子、自动化设备、半导体，"
    "与合格板块无交集。形态没坏：横盘启动 3 只（鸿路钢构 91、生意宝 89、格尔软件 87 分）是涨停后缩量洗盘，"
    "吸筹 2 只（江钨装备 81、华锡有色 62 分）、谐波 3 只 D 点未破，但开盘没加成，确认等放量。\n\n"

    "【盘口动作】按分时结构落地：百洋股份爆量拉升、均价线同步上移是真买盘，可观察，买区 11.52~11.76、"
    "现价 11.64 在带内；生意宝高开 +6.2% 后有量下杀、无承接，竞价虚高被证实，不接。"
    "其余 7 只均价线下弱势整理、金域医学多空平衡，一律等。\n\n"

    "【强板块落后股】强板块 2 个（半导体+1.4%、供水供气+1.3%），落后股全被筛掉："
    "落后不足 2 个百分点 17 只、昨日没涨 1 只。今日没有强板块配落后股的组合，这一路不出手。"
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
    # R100z179（L11 硬约束）：先写临时文件、node --check 通过再 os.replace。
    # 原写法 open(DATA,'w') 先截断再写、写完才 check——进程若在 write 中途被杀
    # （平台 90 分钟硬上限 / 超时重试），留下的是残缺 data.js，而 check 这一步
    # 根本没机会执行。临时名必须带 .js，node 22 拒绝未知扩展名。
    _tmp = DATA + ".tmp.js"
    with open(_tmp, "w", encoding="utf-8") as f:
        f.write(out)
    _chk = subprocess.run([NODE, "--check", _tmp], capture_output=True, text=True, timeout=60)
    if _chk.returncode != 0:
        os.remove(_tmp)
        raise SystemExit("[FAIL] 改写结果未通过 node --check，已丢弃、data.js 保持原样：\n"
                         + (_chk.stderr or "")[:800])
    os.replace(_tmp, DATA)
    print(f"[ok] sentiment {len(SENTIMENT)} 字 / pushText {len(PUSHTEXT)} 字，node --check 通过")


if __name__ == "__main__":
    main()
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
    "开盘情绪：上证-0.57%、深成-2.03%、创业板-3.14%，权重护盘、成长杀跌。"
    "仅银行+1.23%、煤炭开采加工+0.86% 翻红，通信设备-3.57%、半导体-3.58%、元件-4.45% 领跌。"
    "资金同向避险：银行+21.9亿、煤炭开采加工+2.8亿流入，通用设备-6.3亿、化学制药-5.1亿流出。"
    "合格板块＝银行、煤炭开采加工；断板双池无这两板块标的，断板池空缺，13 只全来自技术池。"
)

PUSHTEXT = (
    "① 情绪与资金：上证-0.57%、深成-2.03%、创业板-3.14%，半小时 6241 亿。"
    "银行+1.23%、煤炭开采加工+0.86% 翻红，通信设备-3.57%、半导体-3.58%、元件-4.45% 领跌。"
    "资金银行+21.9亿、煤炭+2.8亿，通用设备-6.3亿、化学制药-5.1亿流出，高低切换明确。\n\n"
    "② 推演：银行 21.9 亿是避险抱团，日内延续但空间有限，属防守；煤炭有资金真买，"
    "郑州煤电这类谐波票吃到背书，日内可跟；通信设备、半导体、元件杀 3.5~4.5%、未进净流入前列，"
    "反弹不接，等缩量止跌。\n\n"
    "③ 映射与盘口：确认档 10 只、待确认档 3 只。有量下杀的大为股份不接；"
    "万通发展高开+2.99% 后跌 1.83%、破均价线，竞价虚高被证实，放弃。爆量拉升的郑州煤电+0.93%、"
    "华数传媒+2.26%，均价线同步上移才是真买盘，可观察；滨江集团下探站回均价线、+1.05%，可观察。"
    "其余 8 只弱势或多空平衡，等。\n\n"
    "④ 待确认档 3 只（生意宝、北矿科技、华数传媒）出自陈学长观察池，还差一根确认 K 线，只盯不进买点。\n\n"
    "⑤ 买点 9 只全在买区内（陈学长·放宽池），大洋电机 8.25~8.41、南方传媒 14.42~14.72。"
    "昨夜 8 只挂单 5 只可挂；赛福天开盘-7.21% 破下沿、撤单不做；中谷物流、九牧王高于限价、不追。"
    "止损偏宽是形态代价：买价较 T 日最低价中位溢价+9.2%、43% 超+10%，回测 17.9% 触发止损、均亏 5.5%。\n\n"
    "⑥ 落后股亚普股份，落后汽车零部 2.7pp，现-2.8%、较开盘-1.2%，属落后不是低估，等企稳。"
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
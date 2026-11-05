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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402  R100z285：清临时文件用 purge_tmp（不删，见 common.purge_tmp）

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data.js")
NODE = "/Users/loccco/.workbuddy/binaries/run-node"

# R100z162：原为硬编码日期，每天必须由 AI 手改，忘了就撞下面的日期断言、白耗一个回合。
# 改为系统日期：守卫（star.date 必须等于今天）照旧生效，但不再需要人工维护。
TODAY = datetime.date.today().isoformat()

# 可选：picks 删减白名单（只删不加）。空集合=保持 update_star.py 原样。
DROP_CODES = set()

SENTIMENT = (
    "开盘全线低开后被接回：上证-1.07%开盘、现-0.14%，创业板-2.00%转+0.83%，"
    "半小时成交9238亿。光伏设备+7.46%（资金+14.9亿）、贵金属+5.50%、"
    "电池+3.65%（+19.5亿）领涨，半导体+20.7亿吸金最多；煤炭-1.14%、银行-1.18%垫底。"
    "合格板块为光伏设备、电池、半导体：断板池两只光伏标的双确认，"
    "电池四只观察池标的仍差确认K线。"
)

PUSHTEXT = (
    "①全线低开后被接回：上证-1.07%开盘现-0.14%、创业板-2.00%转+0.83%，成交9238亿。"
    "光伏设备+7.46%（+14.9亿）、贵金属+5.50%、电池+3.65%（+19.5亿）领涨，"
    "半导体+20.7亿吸金最多；煤炭-1.14%、银行-1.18%垫底。"
    "合格板块：光伏设备、电池、半导体。\n\n"
    "②推演：光伏设备涨幅资金同步最扎实，但已涨超7%，防冲高回落；"
    "电池涨幅温和而19.5亿仍在流入，是更顺的方向；半导体资金第一、涨幅未跟上，"
    "看午后补涨；银行、煤炭既跌又失血，回避。\n\n"
    "③映射：确认档12只——清源股份（光伏设备·利好·基线80%）高开4.4%现涨5.5%、"
    "亚玛顿（79%）平走，双确认；横盘启动天顺风能、金达威低开被接回，洗盘承接成立；"
    "谐波九安医疗、神驰机电获情绪支撑；吸筹我爱我家、九阳股份有资金配合；"
    "上海电影、中稀有色靠自身板块。待确认档10只只说差什么：电池四只（龙蟠科技、丰元股份、"
    "野马电池、时代万恒）板块最强但都差一根确认K线，今天只盯不动；"
    "陈学长观察五只、缩量回踩的青岛食品同理。\n\n"
    "④盘口：爆量拉升16只均价线上移可观察；下探站回3只（天顺风能、金达威、九安医疗）同理；"
    "多空平衡2只（亚玛顿、曙光股份）等；鸿路钢构有量下杀、量占63%仍在均价线下，不接。\n\n"
    "⑤买点清单15只全在买区内（断板2、陈学长13），按原值。昨夜挂单8只："
    "6只可成交，大西洋开-4.42%破下沿5.55不做，龙蟠科技开15.46高于限价不追。"
    "强板块落后股今日空仓。止损宽是形态固有代价：买价相对T日最低价中位溢价9.2%、"
    "43%超10%，回测17.9%触发止损、均亏5.5%，单笔必须小。"
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
        common.purge_tmp(_tmp)      # R100z285：清空而不删——沙箱把 remove 变废纸篓
        raise SystemExit("[FAIL] 改写结果未通过 node --check，已丢弃、data.js 保持原样：\n"
                         + (_chk.stderr or "")[:800])
    os.replace(_tmp, DATA)
    print(f"[ok] sentiment {len(SENTIMENT)} 字 / pushText {len(PUSHTEXT)} 字，node --check 通过")


def _help_guard():
    """R100z296：`--help` / `-h` 只打用法，绝不执行主流程。

    本脚本会写 data.js / 花 LLM 额度 / 全市场抓 K 线，手写 sys.argv 解析时
    `--help` 会被当普通参数忽略、直奔默认流程（与 validate_stocks 同一类坑：
    探参数变成改数据）。这里显式接住，用法直接打模块 docstring。
    """
    import sys as _sys
    if '--help' in _sys.argv or '-h' in _sys.argv:
        print(__doc__ or '(本脚本无说明)')
        raise SystemExit(0)


if __name__ == "__main__":
    _help_guard()
    main()
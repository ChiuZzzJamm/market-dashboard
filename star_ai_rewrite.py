#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R100z9x：开盘精选 AI 文案写回（只改 duanban.star 的 sentiment / pushText）。

纪律：唯一写入口 duanban.star；其余字段（ashare/us/断板池/谐波/横盘启动/fullScan…）
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
    '开盘板块情绪：影视院线+3.76%、元件+2.26%、自动化设备+2.13%领涨，保险-1.63%、汽车整车-1.71%垫底；资金汽车零部件+9.3亿、通信设备+7.8亿、自动化设备+6.7亿进，工程机械-6.8亿、IT服务-2.2亿出，双正向落在自动化设备、汽车零部件、通信设备。上证-1.20%、创业板+0.10%，半小时5467亿，属权重杀跌、设备接力。断板反包空缺，12只全来自技术池。'
)

PUSHTEXT = (
    '①情绪：上证-1.20%、深成-0.37%、创业板+0.10%，半小时5467亿。影视院线+3.76%、元件+2.26%、自动化设备+2.13%领涨，保险-1.63%、汽车整车-1.71%垫底；资金进汽车零部件+9.3亿、通信设备+7.8亿、自动化设备+6.7亿，出工程机械-6.8亿，权重杀、设备与通信接。\n\n'
    '②推演：自动化设备、汽车零部件、通信设备双正向，日内延续概率高于指数；元件涨而资金未进，防冲高回落；影视院线是情绪脉冲，不追。\n\n'
    '③映射：断板池空缺，确认8只、待确认4只。确认档：光刻胶西陇科学、汽车芯片高新发展、白酒概念古井贡酒爆量拉升·均价线上移，须均价线同步走强；石墨电极尚太科技、猪肉东瑞股份、化肥华鲁恒升、兵装重组长城军工均价线下整理、工业互联网力聚热能平衡，均等。待确认档差一根K线、不给买点：环氧丙烷红宝丽、算力租赁证通电子、算力租赁嘉环科技爆量拉升，财税数字化四川长虹下探站回，只盯不买。\n\n'
    '④连板：昨日3只，已续板2只、走强0只；池内同题材候选1只——真视通4板液冷算力对上观察池中贝通信，中京电子2板算力无票；盛剑科技未续板，题材未确认。\n\n'
    '⑤今日没有可对照的昨日计划。挂单：昨夜6只可挂4只（红宝丽、大港股份、金科股份、兴业股份），广电电气3.23破下沿3.34不做，时空科技47.95超限价47.71不追。买点3只在买区：西陇科学7.75~7.91、高新发展42.50~43.36、野马电池16.12~16.44，过赔率闸可下单1只。止损偏宽是固有代价：买价对T日最低价中位溢价+9.2%、43%超+10%，回测17.9%触发止损、均亏5.5%，仓位须小。'
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
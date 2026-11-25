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
    '开盘分化偏弱：教育+3.06%、旅游及酒店+2.18%、零售+2.08%领涨，半导体-1.26%、影视院线-2.16%垫底；资金通信设备+13.7亿、零售+2.9亿、房地产+1.8亿进，通用设备-4.9亿、养殖业-3.4亿出。指数低开抬起仍全绿，上证-0.91%、创业板-0.97%，半小时4563亿偏缩。合格板块仅零售、房地产、通信设备：零售价格资金同向，房地产资金先行价格未跟，通信设备抗跌承接。'
)

PUSHTEXT = (
    '指数仍全绿：上证-0.91%、创业板-0.97%，半小时4563亿，较昨日1.66万亿缩。旅游及酒店+2.18%、零售+2.08%领涨，半导体-1.26%、影视院线-2.16%垫底；资金通信设备+13.7亿、零售+2.9亿、房地产+1.8亿进，通用设备-4.9亿出。\n\n'
    '零售是唯一价格与资金同向的：杭州解百2板的百货零售已续板，日内可延续；房地产资金进了涨幅没跟，属先行不属确认；通信设备净流入最多却在跌幅榜附近，是抗跌承接非趋势；半导体、影视院线回避。\n\n'
    '确认档8只：横盘启动3只（永创智能、峨眉山Ａ、南矿集团）涨停后仍在洗盘，承接有、启动未到；谐波3只（海天味业、东阿阿胶、平安银行）形态在但都缺板块催化；陈学长2只（华东重机、红宝丽）。待确认档5只（零售、AI视频、MicroLED、算力租赁、财税数字化）差一根确认K线，只盯不出手。盘口：上海九百、芯瑞达爆量拉升且均价线上移、南矿集团下探站回——观察；其余10只均价线下整理或多空平衡——等，无深跌无高开走弱。\n\n'
    '昨日25只连板：已续板6只、盘中走强3只；池内同题材候选11只（已确认1/待确认10）。克来机电3板工业机器人对上埃斯顿，永创智能的人形机器人、减速器正在这条线上。今日无对照昨日计划。\n\n'
    '买点清单2只（华东重机8.70~8.88、红宝丽6.81~6.95）在买区内，赔率闸全不满足、可下单0只。昨夜8只挂单6只可成交，2只高于限价不追（新炬网络、浙江荣泰），0只破下沿。止损偏宽是固有代价：买价相对T日最低价中位溢价+9.2%、43%单子超+10%，回测17.9%触发止损均亏5.5%，单笔仓位必须小（每只1/32资金）。'
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
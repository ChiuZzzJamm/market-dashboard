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
    '三大指数低开续跌：上证-1.52%、深成-2.38%、创业板-2.65%，半小时4689亿。行业近乎全绿，元件+0.16%、银行-0.25%居前，影视院线-5.04%、游戏-5.17%、贵金属-5.70%领跌；资金银行+7.2亿、元件+6.2亿进，半导体-23.2亿、证券-5.7亿出。双正向只剩元件，属防御抱团非进攻主线；断板池无元件标的，不做板块级判定，技术池10只按盘口处理。'
)

PUSHTEXT = (
    '①开盘：上证-1.52%、深成-2.38%、创业板-2.65%，半小时4689亿。元件+0.16%、银行-0.25%居前，影视院线-5.04%、游戏-5.17%、贵金属-5.70%领跌；资金银行+7.2亿、元件+6.2亿进，半导体-23.2亿、证券-5.7亿出，双正向仅元件。\n\n'
    '②推演：院线游戏与贵金属属撤离非洗盘，不接；半导体单边流出，弱市拖累，回避；元件靠资金托不靠涨价，看下午能否放量站稳；银行是避险去处，护指数不护个股。\n\n'
    '③映射：确认档9只、待确认档1只，断板池空（元件无池内票）。沈阳机床（工业母机）爆量拉升，观察，均价线续升方为真买盘；江化微（光刻胶）下探站回均价线，观察；东瑞股份（猪肉）、泸州老窖（白酒概念）平衡；格林达（光刻胶）、比亚迪（充电桩）、海天味业、中泰证券、太阳能弱势整理，等。待确认档差一根K线、不给买点：常山北明（算力租赁）爆量上移，只盯能否站住均价线。\n\n'
    '④连板：昨日5只连板：已续板2只、盘中走强2只；池内同题材候选2只（确认0、待确认2）。真视通3板的液冷算力加华为链，对上观察池美利云、城地香江，但未缩量到位，只算题材对上；全筑股份未续板-8.97%，题材没被确认。\n\n'
    '⑤没有可对照的昨日计划。\n\n'
    '⑥挂单：昨夜8只7只可挂，动力源开-4.30%破下沿4.26不做。买点4只全在买区：格林达29.99~30.59、江化微26.18~26.70、高新发展44.26~45.16、兴业股份12.36~12.60。止损偏宽是形态固有代价：买价对T日最低价中位溢价+9.2%、43%超+10%，回测17.9%触发止损、均亏5.5%，单笔仓位须小（每只1/32资金）。'
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
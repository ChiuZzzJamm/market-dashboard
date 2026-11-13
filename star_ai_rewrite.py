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
    "影视院线+3.20%、军工电子+2.27%、半导体+2.06%领涨，医疗服务-2.15%垫底；"
    "上证+0.31%、创业板-0.01%，7426亿。"
    "资金高低切换：军工电子+7.3亿、计算机设备+5.7亿、通信服务+5.1亿流入，通信设备-15.2亿、文化传媒-12.2亿流出。"
    "合格板块军工电子、通信服务、计算机设备；声迅股份所在计算机设备即资金流入方，"
    "新北洋同板块却-4.05%有量下杀，结构未确认。"
)

PUSHTEXT = (
    "①开盘：上证+0.31%、深成+0.17%、创业板-0.01%，半小时成交7426亿；"
    "影视院线+3.20%、军工电子+2.27%、半导体+2.06%领涨，医疗服务-2.15%垫底。"
    "资金高低切换：军工电子+7.3亿、计算机设备+5.7亿、通信服务+5.1亿流入，"
    "通信设备-15.2亿、文化传媒-12.2亿、通用设备-10.1亿流出。合格板块：军工电子、通信服务、计算机设备。\n\n"
    "②推演：军工电子涨幅与资金同向最扎实，偏延续防回落；"
    "计算机设备资金新进、扩散尚早，回踩优于追高；通信设备个股强但板块-15.2亿流出，背离谨慎。\n\n"
    "③映射：确认档11只、待确认6只。声迅股份（CPO）所在计算机设备获资金确认、基线78%，多空平衡、等；"
    "陕西煤业（煤炭概念）、建设工业（兵装重组概念）均价线上移、可观察；"
    "豪鹏科技（AI眼镜）高开转跌破线，不接；路畅科技、鸣志电器、来伊份弱势，只等。"
    "待确认档不给价：新北洋（无人零售）-4.05%有量下杀、放弃；"
    "返利科技（抖音概念）、剑桥科技（F5G概念）可观察；华脉科技、日发精机、日海智能只能等。\n\n"
    "④连板：昨日11只连板、已续板3只、盘中走强1只；池内同题材候选4只。"
    "金辰股份2板的光伏设备对上安彩高科，天普股份3板的中昊芯英对上世运电路（算力）；"
    "日出东方、大东方、亚玛顿、惠发食品均未续板，不算扩散。\n\n"
    "⑤今日没有可对照的昨日计划。\n\n"
    "⑥昨夜8只挂单3只可成交（凯盛新能、三钢闽光、三峡新材），5只高于限价不追；买点清单13只全在买区。"
    "止损偏宽是形态固有代价：买价相对T日最低价中位溢价+9.2%、43%超+10%，"
    "回测17.9%触发止损、均亏5.5%，单笔仓位必须小。"
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
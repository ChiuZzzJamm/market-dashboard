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
    "开盘三大指数大幅低开后仍深跌，上证-2.11%开→-1.33%、创业板-4.99%开→-3.24%，"
    "低开修复非转强。光伏设备+2.50%、种植业与林业+1.76%、电力+1.24%逆势翻红，"
    "元件-6.44%、通信设备-4.19%续杀。资金情绪：电力+13.8亿、光伏设备+7.0亿净流入，"
    "纺织制造、文化传媒净流出，防御性高低切换。"
    "合格板块仅光伏设备、电力，断板池仅钧达股份挂光伏获确认。"
)

PUSHTEXT = (
    "①指数低开：上证-2.11%、深成-3.72%、创业板-4.99%，半小时收回至-1.33%、-2.03%、-3.24%，"
    "成交8232亿，是放量砸出的反弹非缩量止跌。光伏设备+2.50%、种植业与林业+1.76%、电力+1.24%逆势翻红，"
    "元件-6.44%、通信设备-4.19%续杀；主力资金电力+13.8亿、光伏设备+7.0亿、电池+6.4亿净流入，"
    "纺织制造、文化传媒净流出，防御性切换。\n\n"
    "②光伏设备、电力涨幅资金双正向，日内大概率守强，但属低估值防御、追高不如等回踩；"
    "元件、通信设备跌超4%无承接，反弹只当反抽；种植业靠题材易回落。\n\n"
    "③断板池仅钧达股份，光伏设备·利好·基线78%，开盘-3.4%后现涨+8.3%，情绪与个股同步，"
    "但已拉起11个点，追高风险大。技术池保龄宝、路畅科技、鸿路钢构低开拉回，属洗盘承接未启动，"
    "还差一根确认；桂林三金、新乳业现涨2.8%、2.4%不靠大盘修复，谐波吸筹形态得确认；"
    "华鲁恒升、豪威集团仍在平盘下。待确认档5只全低开2.5%~4.5%，今天只盯不给价，"
    "买点与作废位按昨日「明日可下单」栏。\n\n"
    "④买点14只全在买区（断板1、陈学长13）；昨夜8只挂单6只可挂，"
    "杰瑞股份开80.39破下沿81.26作废、万向德农11.25超限价11.20不追。"
    "止损距离宽是固有代价：买价相对T日最低价中位溢价+9.2%、43%超+10%，"
    "回测17.9%触发止损、均亏5.5%，靠46.5%的+5%止盈覆盖，单笔1/32仓位。\n\n"
    "⑤盘口16只全判爆量拉升·均价线上移，但属全市场同步反弹、区分度低，均价线续升才算真买盘。"
    "强板块落后股0只（无板块跳空过1%），这一路不出手。"
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
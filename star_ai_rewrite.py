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
    '开盘三大指数低开走弱：上证-0.77%、深成-1.06%、创业板-1.34%，半小时4394亿缩量下跌。领涨电子化学品+0.86%、电力+0.74%，影视院线-3.16%、游戏-2.03%退潮；资金元件+4.6亿、电力+4.4亿流入，工程机械-11.3亿、半导体-6.0亿流出。涨幅与资金同正只有电力，是缩量防御轮动而非进攻主线；断板反包无合格板块可挂钩，不做板块级判定，技术池14只按形态盘口处理。'
)

PUSHTEXT = (
    '①开盘：上证-0.77%/深成-1.06%/创业板-1.34%低开走弱，半小时4394亿缩量。领涨电子化学品+0.86%、电力+0.74%，影视院线-3.16%、游戏-2.03%退潮；资金元件+4.6亿、电力+4.4亿进，工程机械-11.3亿、半导体-6.0亿出，双正向仅电力。\n\n'
    '②推演：电子化学品属弱势里的相对强势，更像横盘不是放量突破；电力资金进但涨幅不足1%，看下午量能能否延续；半导体流出，指数弱时不抢反弹；院线游戏连跌回避。\n\n'
    '③映射：确认档9只、待确认档5只，断板池空。峨眉山Ａ（冰雪产业）、东瑞股份（猪肉）、尚太科技（石墨电极）多空平衡，等；海南橡胶（土地流转）下探站回均价线，观察；云天化（磷化工）有量下杀无承接，不接；华菱钢铁（特钢概念）弱势整理、广汇能源（煤炭概念）与康强电子（先进封装）平衡，等。待确认档差一根确认K线、不给买点：江化微（中芯国际概念）、西陇科学（光刻胶）爆量上移，观察；其余三只弱势整理或平衡，只盯不买。\n\n'
    '④连板：昨日7只连板，已续板3只、走强1只；池内同题材候选9只（确认2/待确认7）。真视通2板的液冷算力对上断板确认池华胜天成；盛剑科技2板的光刻胶对上观察池江化微、西陇科学。泰嘉股份、双环科技未续板，题材未被当日确认。\n\n'
    '⑤今日没有可对照的昨日计划。\n\n'
    '⑥挂单：昨夜8只5只开盘可挂；九阳股份开-10.01%破下沿不做；格林达、扬子新材高于限价不追。买点只野马电池15.88~16.20在买区内。止损偏宽是形态固有代价：买价对T日最低价中位溢价+9.2%、43%超+10%，回测17.9%触发止损、均亏5.5%，单笔仓位须小（每只1/32资金）。'
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
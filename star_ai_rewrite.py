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
    "开盘半小时三大指数微幅翻红：上证+0.03%、深成+0.20%、创业板+0.51%。"
    "资金集中电子链，半导体+2.03%、电子化学品+2.33%、元件+1.64% 领涨，半导体主力净流入 30.0 亿居首；"
    "游戏、煤炭走弱，贵金属-2.57% 垫底。"
    "合格板块为电子化学品、半导体，断板池仅 1 只挂钩（国家大基金持股方向），情绪确认成立；"
    "煤炭、航运、影视无资金配合，只按形态与盘口处理。"
)

PUSHTEXT = (
    "①开盘板块情绪与资金：指数微幅翻红（上证+0.03%、深成+0.20%、创业板+0.51%），半小时成交 6871 亿。"
    "资金集中电子链——半导体+2.03%、电子化学品+2.33% 领涨，半导体主力净流入 30.0 亿居首；"
    "游戏、煤炭走弱，贵金属-2.57% 垫底。合格板块：电子化学品、半导体。\n\n"
    "②板块推演：半导体、电子化学品涨幅与资金同向，日内延续概率偏高，但半小时已拉逾 2%，防冲高回落；"
    "煤炭、贵金属资金流出，继续回避；文化传媒、影视走弱，短剧游戏只当反弹。\n\n"
    "③标的映射：精选 16 只，确认档 11 只、待确认档 5 只。"
    "有研新材（国家大基金持股）是断板池唯一挂钩合格板块的确认标的；"
    "横盘启动的京城股份（低空经济）、中鼎股份（液冷服务器）下探后站回均价线；"
    "吸筹的桂林三金（创新药）爆量拉升；谐波招商南油（航运概念）平开偏强。"
    "盘口：鸣志电器有量下杀、今天不接；声迅股份、桂林三金、慈文传媒均价线上移，可观察；"
    "其余多空平衡或均价线下弱势，按「等」处理。待确认档 5 只还差一根确认 K 线，不给买点。\n\n"
    "④连板映射：昨日 19 只连板，已续板 7 只、盘中走强 3 只；池内同题材候选 6 只。"
    "亚玛顿 3 板的光伏玻璃对上赛伍技术，天普股份 2 板的算力对上声迅股份。\n\n"
    "⑤今日没有可对照的昨日计划。昨晚挂单 8 只，7 只开盘在可成交区间，林州重机开 2.85 高于限价 2.82、不追。"
    "止损距离偏宽是这套形态的固有代价：买价相对 T 日最低价中位溢价 +9.2%、43% 的单子超 +10%。"
    "回测里 17.9% 的笔触发止损、均亏 5.5%，靠 46.5% 的 +5% 止盈覆盖。"
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
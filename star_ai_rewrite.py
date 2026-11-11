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
    "指数分化：上证-0.15%、深成-0.31%、创业板+0.22%，6674 亿未放量。"
    "通信设备+2.25%、元件+1.51% 领涨，白酒-2.30%、影视院线-1.84% 垫底。"
    "资金：通信设备+43.7亿、元件+29.6亿、半导体+24.8亿流入，光学光电子-5.0亿流出，高低切换明显。"
    "合格板块即通信设备、元件、半导体，断板池多数同链获双支撑；技术池多在机械、地产链，未获资金确认，按盘口等。"
)

PUSHTEXT = (
    "①指数分化：上证-0.15%、深成-0.31%、创业板+0.22%，6674 亿未放量。"
    "通信设备+2.25%、元件+1.51% 领涨，白酒-2.30%、影视院线-1.84% 垫底；"
    "资金：通信设备+43.7亿、元件+29.6亿、半导体+24.8亿流入，光学光电子-5.0亿流出，高低切换明显。\n\n"
    "②通信设备、元件涨幅资金双正向且量最大，日内延续概率高，但已内部分层（东方通信-3.5%），追高等均价线上移；"
    "半导体只靠资金不涨，属承接型，防冲高回落；白酒、影视院线无资金回流，回避。\n\n"
    "③确认档 11 只：有研新材（国家大基金持股）唯一断板确认，行业合格但均价线下整理，只到等；"
    "爆量拉升 5 只（路畅科技、鸣志电器、海星股份、世运电路、声迅股份）均价线上移可观察，"
    "兖矿能源（煤炭概念）下探被接回可观察；其余 7 只均线下或平衡一律等。"
    "待确认档 10 只（断板观察 8、陈学长 2、缩量回踩 0）还差一根确认 K 线，奥士康、海星股份、世运电路同挂 PCB，只盯不买。\n\n"
    "④昨日 20 只连板：已续板 5 只、盘中走强 4 只；池内同题材候选 6 只（确认 3/待确认 3）。"
    "光洋股份 2 板 PCB 对上奥士康、海星股份、世运电路；亚玛顿 2 板光伏玻璃对上安彩高科。今日无可对照的昨日计划。\n\n"
    "⑤买入清单 8 只全在买区（有研新材 38.05~38.81）；"
    "昨夜 8 只挂单 5 只可挂、0 只破下沿，胜通能源、飞亚达、东方通信高于限价不追。"
    "止损距离偏宽是形态固有代价：买价相对 T 日最低价中位溢价 +9.2%、43% 超 +10%，回测 17.9% 触发止损、均亏 5.5%，单笔仓位必须小。"
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
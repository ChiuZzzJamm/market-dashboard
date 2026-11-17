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
    '开盘板块情绪：环保设备+1.10%、电子化学品+0.79%、化学纤维+0.75%微红，游戏-4.70%、文化传媒-4.71%、影视院线-7.29%领跌。资金：元件+6.6亿、光学光电子+4.5亿、半导体+3.8亿流入，IT服务-7.1亿、银行-5.5亿流出，电子链有钱进价不跟。涨且资金双正向的合格板块为零，断板反包不做板块挂钩、精选全取技术池，微涨方向只算弱支撑、传媒游戏链明确压制。'
)

PUSHTEXT = (
    '①开盘：上证-0.75%、深成-0.53%、创业板-0.81%，半小时6181亿。领涨环保设备+1.10%、电子化学品+0.79%、化学纤维+0.75%，领跌游戏-4.70%、文化传媒-4.71%、影视院线-7.29%；资金元件+6.6亿、光学光电子+4.5亿、半导体+3.8亿进，IT服务-7.1亿、银行-5.5亿出，电子链有钱进价不涨，高低切换。\n\n'
    '②推演：三个微涨方向仅1%上下、无资金配合，是防守位不是发动位，追高风险大；元件、光学光电子资金进价不跟，日内看横；游戏、影视院线、文化传媒连跌带流出，回避。\n\n'
    '③映射：15只全技术池（确认10、待确认5，不给买点）。爆量拉升5只——东瑞股份（猪肉）、剑桥科技（F5G概念）、兴业股份（光刻胶）、飞亚达（人形机器人）、华脉科技（F5G概念），均价线上移只观察；海南橡胶（土地流转）下探站回，观察；峨眉山Ａ、起帆电缆多空平衡，其余弱势整理，一律等。买点只认清单：剑桥科技109.21~111.41、亿嘉和29.11~29.69在买区。\n\n'
    '④连板：昨日11只连板，已续板1只、走强1只；池内没有同题材候选。华东重机（GPU芯片）2板封住、广百股份（百货零售）3板走强未封，题材没对上池内标的；另3只未续板，题材没被确认。\n\n'
    '⑤今日没有可对照的昨日计划。\n\n'
    '⑥挂单：昨夜8只5只开盘可成交（湖北广电、兴业股份、福达股份、皮阿诺、福龙马），3只高于限价（维通利、儒意电影、中马传动）不追，无破位；福达股份现价10.56已低于10.80证伪价，按纪律撤单。止损偏宽是代价：买价对T日最低价中位溢价+9.2%，回测17.9%止损、均亏5.5%，仓位必须小。'
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
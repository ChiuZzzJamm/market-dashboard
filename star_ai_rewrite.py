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
    "开盘情绪偏暖但不齐：影视院线+3.41%、贵金属+2.63%领涨，电机-2.52%垫底；"
    "上证+0.06%、创业板+0.73%，成交4681亿。资金与涨幅错位：半导体+14.3亿、"
    "银行+8.2亿、化学制药+6.9亿流入，证券-4.9亿流出，高位题材换手、低位权重承接。"
    "合格板块仅化学制药、半导体、银行；断板池扎堆的饮料制造、文化传媒、影视院线涨幅有、"
    "资金未跟，故断板反包不入选，技术池只靠自身形态。"
)

PUSHTEXT = (
    "①开盘半小时，影视院线+3.41%、贵金属+2.63%领涨，电机-2.52%、饮料制造-1.68%垫底；"
    "上证+0.06%、创业板+0.73%，成交4681亿。资金与涨幅错位：半导体+14.3亿、银行+8.2亿、"
    "化学制药+6.9亿流入，证券-4.9亿流出——高位题材换手、低位权重承接，不是全面进攻。\n\n"
    "②推演：影视院线涨最猛却无资金跟随，日内防冲高回落；半导体、化学制药有资金、"
    "涨幅未跟上，属慢线；饮料制造、电机既跌又无资金，回避。\n\n"
    "③映射：确认档10只——横盘启动3只仅天顺风能下探被接回，鸿路钢构、滨江集团仍在均价线下，"
    "洗盘承接未确认；吸筹的紫竹高科涨2.7%但盘口平衡、需放量；谐波3只仅绿地控股站回均价线，"
    "另两只D点无资金支撑。待确认档5只只讲差什么：上海电影爆量拉升、均价线上移，最该盯，"
    "但差一根收盘确认K线；另三只形态未走完；宸展光电有量下杀，不接。\n\n"
    "④盘口纪律：宸展光电有量下杀不接；上海电影爆量拉升可观察，须均价线同步上移；"
    "天顺风能、绿地控股下探站回观察；鸿路钢构、滨江集团、锦江航运、华新建材弱势整理、等；"
    "其余多空平衡、等。\n\n"
    "⑤昨夜挂单8只：可挂3只（渤海汽车、清源股份、青岛金王）、无一破下沿，"
    "5只高于限价（宸展光电、安迪苏、华新建材、国金证券、水井坊）不追。"
    "今日买点7只全在陈学长放宽池、全在买区内。强板块落后股今日不出手：落后不足2pp的21只。\n\n"
    "⑥止损距离偏宽是形态固有代价：买价相对T日最低价中位溢价9.2%、43%的单子超10%；"
    "回测17.9%触发止损、均亏5.5%，靠46.5%的+5%止盈覆盖。单笔仓位必须小（每只1/32），"
    "别因看着确定就加重。"
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
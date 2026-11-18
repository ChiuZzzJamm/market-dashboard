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
    '上证-0.00%、深成-0.05%、创业板+0.14%，高开回吐，半小时3401亿。领涨家电：小家电+2.42%、黑色家电+1.92%、白色家电+1.86%；资金却偏通信设备+6.4亿、煤炭开采加工+4.0亿、电力+3.5亿，影视院线-3.00%、贵金属-3.31%垫底。涨与钱分家，合格板块只认煤炭、通信设备、电力。断板双池扎堆游戏、影视院线，与三者零交集，反包不出手；技术池14只不受挂钩约束。'
)

PUSHTEXT = (
    '①开盘：上证-0.00%、深成-0.05%、创业板+0.14%，高开回落，半小时3401亿不算放量。领涨小家电+2.42%、黑色家电+1.92%、白色家电+1.86%；影视院线-3.00%、贵金属-3.31%领跌。资金通信设备+6.4亿、煤炭+4.0亿、电力+3.5亿进，化学制品-6.5亿、IT服务-5.9亿出——家电涨价不涨钱，合格板块只剩煤炭、通信设备、电力。\n\n'
    '②推演：家电是连板带的情绪脉冲（长虹美菱2板、小熊电器走强），资金没跟，防冲高回落；煤炭、电力是低位防守型流入，看量能续不续，不追；影视院线昨日领涨今日-3.00%，退潮没完，回避。\n\n'
    '③映射：确认档9只、待确认档5只。确认档常山北明（ERP概念）、宝新能源（超超临界发电）爆量拉升且均价线上移，观察；尚太科技（石墨电极）下探站回，观察；厦门钨业（MLCC概念）有量下杀无承接，不接；其余均价线下的等。买点只龙建股份（冰雪产业），4.14~4.22、现价4.18在买区内。待确认档返利科技（抖音概念）同样爆量上移，还差一根确认K线，五只只列不买。\n\n'
    '④连板：昨日6只连板，已续板2只、走强1只；池内同题材候选2只（待确认）。华东重机（GPU芯片）3板对上高新发展的汽车芯片，长虹美菱2板对上华帝股份。\n\n'
    '⑤今日没有可对照的昨日计划。\n\n'
    '⑥挂单：昨夜8只，4只开盘在限价内可挂（北巴传媒、紫竹高科、鹏飞绿能、华帝股份）；天虹股份开-9.93%破形态下沿，不做；高新发展、丸美生物、上海物贸高于限价，不追。止损偏宽是代价：买价对T日最低价中位溢价+9.2%、43%超+10%，回测17.9%止损、均亏5.5%，仓位要小。'
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
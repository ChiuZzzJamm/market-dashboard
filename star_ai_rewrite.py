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
    '高开回落：上证-0.01%、深成+0.35%、创业板+0.32%，三者自盘初+0.39%/+0.98%/+1.35%的高点几乎回吐；90 个行业 58 涨、中位数仅+0.17%，领涨贵金属+3.19%、元件+2.88%、油气开采及服务+2.05%，医药链与白酒垫底。资金元件+15.8亿、银行+13.6亿、通信设备+9.3亿进，半导体-18.8亿、零售-7.5亿出：涨价在失血、涨钱不涨价的背离并存。'
)

PUSHTEXT = (
    '指数高开回落：上证-0.01%、深成+0.35%、创业板+0.32%，三者盘初分别+0.39%/+0.98%/+1.35%，半小时内涨幅基本还清；两市半小时成交 4554 亿，与昨日同期 4563 亿基本持平——高开没有增量资金接力，属存量博弈的结构行情。90 个行业 58 个上涨、涨幅中位数仅+0.17%，普涨是假、分化是真。领涨贵金属+3.19%、元件+2.88%、油气开采及服务+2.05%、黑色家电+1.59%、煤炭开采加工+1.47%，资源与上游涨价链占住前排；垫底生物制品-1.49%、机场航运-1.41%、医药商业-1.00%、中药-0.96%、白酒-0.79%，医药链与消费白马集体退潮。资金端元件+15.8亿、银行+13.6亿、通信设备+9.3亿净流入，半导体-18.8亿、零售-7.5亿、光伏设备-4.9亿净流出。\n\n'
    '当日推演：元件是唯一价格与资金同向共振的板块（+2.88%、+15.8亿），日内有延续基础，但指数高开回落之下要看均价线能否守住，若十点后资金转为净流出，冲高回落概率大。银行涨幅仅+0.19%却拿到 13.6 亿净流入，属护盘性质的承接，能托指数但不产生超额，不是进攻信号。半导体价平（+0.18%）而资金流出最多，属高位换手兑现，日内不追；它若继续走弱会直接拖累创业板。零售昨日领涨、今日价还在（+1.14%）但资金撤出 7.5 亿，典型冲高兑现，防回落。医药链与白酒继续回避，暂无资金反向迹象。贵金属虽领涨 3.19%，但未进净流入前三，属避险与涨价驱动的情绪领先，持续性看商品端而非场内资金。'
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
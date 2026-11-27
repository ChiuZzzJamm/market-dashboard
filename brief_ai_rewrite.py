#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R100z9x：开盘精选 AI 文案写回（只改 duanban.brief 的 sentiment / pushText）。

纪律：唯一写入口 duanban.brief；其余字段（ashare/us/断板池/谐波/横盘启动/fullScan…）
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

# 可选：picks 删减白名单（只删不加）。空集合=保持 update_brief.py 原样。
DROP_CODES = set()

SENTIMENT = (
    '指数高开回落：上证-0.23% 翻绿，创业板+0.60% 最强，半小时 4204 亿小幅缩量。'
    '88 个行业 31 涨 57 跌、中位数-0.32%，科技链领涨：IT服务+2.94%、通信服务+2.08%、电子化学品+2.02%；'
    '影视院线-3.57%、种植业与林业-2.76%、养殖业-2.06% 垫底。'
    '资金通信设备+17.9亿、半导体+15.9亿、IT服务+7.5亿净流入，造纸、化学制药小幅流出。'
)

PUSHTEXT = (
    '指数高开回落、内部严重分叉：上证自+0.16% 翻至-0.23%，深成+0.01% 走平，创业板+0.60% 维持红盘。'
    '半小时两市成交 4204 亿，较昨日同期 4554 亿缩量约百分之八——没有增量资金接力，是存量在板块间搬家。'
    '88 个行业 31 涨 57 跌、涨幅中位数-0.32%，普涨是假、分化是真。\n\n'
    '领涨全在科技链：IT服务+2.94%、通信服务+2.08%、电子化学品+2.02%，半导体+1.78%、通信设备+1.50%、软件开发+1.47% 紧随。'
    '资金侧同向验证：通信设备净流入 17.9 亿、半导体 15.9 亿、IT服务 7.5 亿，是今天仅有的三个亿元级吸金方向，价格与资金同向。'
    '领跌集中在消费与农业：影视院线-3.57%、种植业与林业-2.76%、养殖业-2.06%、农产品加工与旅游酒店同步走弱，文化传媒-1.93% 延续退潮。'
    '资金流出前列造纸、光学光电子、化学制药都只有 3 至 4 亿，属小幅撤离而非恐慌抛售；光学光电子价格不弱却净流出，是高位换手。\n\n'
    '当日推演：通信设备、半导体价格与资金双确认，日内延续概率偏高，但缩量背景下必须防午后冲高回落，判断看回踩能否守住均价线，跌破且资金转流出即视为当日结束。'
    'IT服务、软件开发涨幅最大，吸金体量却只有通信设备的四成，属跟随补涨，开盘一小时已透支大部分涨幅，日内只宜观察不宜追。'
    '影视院线、种植业与林业连续走弱且无资金承接，超跌不等于见底，日内继续回避，等放量长下影或资金转净流入再看。'
    '煤炭-1.70% 与昨夜外盘能源大涨反向，说明外部映射今天没被本地资金接受，权重与资源方向只做防守，不指望指数级机会。'
)


def main():
    with open(DATA, encoding="utf-8") as f:
        s = f.read()
    i = s.index("{")
    D = json.loads(s[i:s.rindex("}") + 1])

    star = D.setdefault("duanban", {}).get("brief")
    if not star:
        print("[FAIL] duanban.brief 不存在——须先跑 update_brief.py，不允许凭空造 star")
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
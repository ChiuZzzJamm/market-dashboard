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
    "开盘是普跌后的修复：上证+0.73%、深成+1.09%、创业板+1.48%；行业仅中药、银行、种植业微跌，"
    "电机+3.85%、军工电子+3.58% 领涨。资金不同步，消费电子+17.2亿、汽车零部件+15.1亿流入，"
    "半导体-10.1亿、光伏设备-4.0亿、电池-3.3亿流出，属高低切换。合格板块只有消费电子与汽车零部件，"
    "断板观察池 6 只全在其中、情绪确认成立；技术池的电池、光伏标的资金侧被压制。"
)

PUSHTEXT = (
    "①指数收回周五部分跌幅：上证+0.73%、深成+1.09%、创业板+1.48%，半小时成交 5175 亿、"
    "比上周五同期 8232 亿缩约三成，是缩量修复而非放量反攻；仅中药、银行、种植业微跌，"
    "电机+3.85%、军工电子+3.58% 领涨。资金分化：消费电子+17.2亿、汽车零部件+15.1亿流入，"
    "半导体-10.1亿、光伏设备-4.0亿、电池-3.3亿流出。\n\n"
    "②消费电子与汽车零部件涨幅、资金同向，是今天仅有的两个合格板块，日内延续概率相对高，"
    "但连涨两日防回落；电机、军工电子涨幅最猛却无资金进榜，属脉冲不追；"
    "半导体、光伏设备资金续流出，反弹只当修复。\n\n"
    "③确认档 10 只中，鸣志电器落在领涨的电机板块、现价较开盘再涨 3.1%，横盘启动形态获情绪确认；"
    "广汇能源逆煤炭资金弱势放量拉起、均价线上移；京城股份、我爱我家、宝新能源在均价线下整理，只等不接。"
    "待确认档 10 只（断板观察 6、陈学长观察 1、缩量回踩 3）还差一根确认 K 线，只盯不买。\n\n"
    "④爆量拉升 5 只（广汇能源、三联锻造、徕木股份、宝明科技、山东黄金）可观察，均价线不续升即失效；"
    "多空平衡 11 只、均价线下整理 4 只一律等，无深跌走弱。\n\n"
    "⑤买入清单 9 只全在买区（丰元股份 14.14~14.42 等）；昨夜 8 只挂单全部开盘高于限价，不会成交也不追。"
    "止损距离宽是固有代价：买价相对 T 日最低价中位溢价 +9.2%、43% 超 +10%，回测 17.9% 触发止损、均亏 5.5%，"
    "靠 46.5% 的 +5% 止盈覆盖，单笔 1/32 仓位。落后股点名乐山电力（落后电力 4.4pp），该规则已摊平，只作观察。"
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
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
    "全线高开后涨幅收窄：上证+0.36%、深成+0.92%、创业板+1.72%，半小时 7010 亿；"
    "影视院线+4.20%、自动化设备+3.23%、电机+2.94% 领涨，煤炭-0.31% 垫底。"
    "资金转向设备与汽车链：汽车零部件+25.6亿、自动化设备+11.6亿、通用设备+11.2亿 流入，"
    "半导体-11.4亿 流出。合格板块即这三个，断板反包池 8 只全在其中、情绪确认成立；技术池煤炭侧无资金。"
)

PUSHTEXT = (
    "①全线高开后涨幅收窄：上证+0.36%、深成+0.92%、创业板+1.72%，半小时 7010 亿，"
    "较昨日同期 5175 亿放大三成半。影视院线+4.20%、自动化设备+3.23%、电机+2.94% 领涨，"
    "煤炭-0.31% 垫底；资金偏设备与汽车链：汽车零部件+25.6亿、自动化设备+11.6亿、"
    "通用设备+11.2亿 流入，半导体-11.4亿 流出。\n\n"
    "②自动化设备涨幅与资金同向，日内延续概率偏高，但博杰股份高开走弱、板块分层，午后防冲高回落；"
    "汽车零部件资金 25.6 亿居首而涨幅未进前三，属资金先到、价格后到，回踩承接更可靠；"
    "影视院线涨 4.20% 最猛却无资金进榜，不追。\n\n"
    "③确认档 12 只：断板 3 只全挂合格板块，赛腾股份、中马传动均价线上移、支撑成立，"
    "博杰股份走弱不接；技术池鸣志电器随电机走强，南矿集团、我爱我家、国芳集团站上均价线可观察，"
    "京城股份、东瑞股份、西部材料均线下方只等。待确认档 10 只还差一根确认 K 线，只盯不买。\n\n"
    "④爆量拉升 10 只可观察，均价线同步上移才算真买盘；高开走弱 1 只（博杰股份）不接；"
    "均价线下整理 6 只、多空平衡 5 只一律等，无有量下杀\n\n"
    "⑤买入清单 13 只全在买区（赛腾 37.59~38.35）。昨夜 6 只挂单 3 只可挂"
    "（安彩高科、新赛股份、华升股份），康冠科技、上海电影、侨银股份高于限价不追。"
    "止损宽是固有代价：买价相对 T 日最低价中位溢价 +9.2%、43% 超 +10%，"
    "回测 17.9% 触发止损、均亏 5.5%，每只 1/32 仓位。落后股 3 只只观察，"
    "规则已摊平（次日收盘 -0.19%、胜率 47%）。"
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
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
    "开盘半小时权重护盘、成长杀跌：上证-1.02%、深成-2.02%、创业板-3.10%，成交5794亿。"
    "银行+0.83%、煤炭开采加工+0.72%翻红，自动化设备-3.75%、元件-3.88%领跌；"
    "资金净流入银行+15.1亿、煤炭+2.2亿，小家电-7.2亿、游戏-6.1亿流出。"
    "涨且资金双正向的合格板块只有银行和煤炭，池内获确认的是煤炭概念（兖矿能源、郑州煤电、安泰集团），"
    "其余技术池无板块支撑。"
)

PUSHTEXT = (
    "①开盘：上证-1.02%、深成-2.02%、创业板-3.10%，半小时5794亿，权重护盘成长杀跌。"
    "银行+0.83%、煤炭开采加工+0.72%翻红，自动化设备-3.75%、元件-3.88%领跌；"
    "资金净流入银行+15.1亿、煤炭开采加工+2.2亿，小家电-7.2亿、游戏-6.1亿流出。\n\n"
    "②推演：煤炭开采加工是唯一涨且资金进的进攻方向，日内有望延续，但缩量下防冲高回落；"
    "银行属防御性吸金，护指数不护个股；元件、电子化学品跌幅最深且资金未回补，日内回避，反抽不接。\n\n"
    "③映射：确认档10只、待确认档7只。煤炭概念获板块确认，兖矿能源（煤炭概念）爆量拉升、均价线上移可观察；"
    "待确认档郑州煤电、安泰集团同挂煤炭概念，差一根确认K线，只盯不动。"
    "技术池其余无板块支撑：豪鹏科技（AI眼镜）、东瑞股份（猪肉）、海正药业（减肥药）均价线下整理，等；"
    "宝新能源（超超临界发电）、起帆电缆多空平衡，等。"
    "放弃四只：富春染织有量下杀无承接，阳光照明、维通利高开走弱，不接。\n\n"
    "④连板：昨日7只连板，已续板2只、走强1只；池内同题材候选1只——北纬科技3板的人形机器人对上断板观察池宁波华翔（待确认）。"
    "天普股份4板、金辰股份3板未续板，题材没被当日确认。\n\n"
    "⑤今日无昨日计划可对照。\n\n"
    "⑥买点清单3只全在买区：人民同泰10.51~10.73、阳光照明5.93~6.05、盘龙药业22.23~22.67。"
    "昨夜7只挂单4只可成交，3只高于限价（起帆电缆、成都路桥、双成药业）不追。"
    "止损距离偏宽是形态固有代价：买价相对T日最低价中位溢价+9.2%、43%的单子超+10%，回测17.9%触发止损、均亏5.5%，单笔仓位必须小。"
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
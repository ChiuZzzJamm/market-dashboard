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
    '开盘偏强但已回吐：自动化设备+4.75%、消费电子+4.73%、通信设备+4.69%领涨，创业板自+3.88%收窄至+3.16%。资金在换手：消费电子+10.3亿、汽车零部件+9.2亿进，元件-41.2亿、通信设备-25.1亿出，涨最猛的通信设备反而失血。合格板块仅消费电子、通用设备、汽车零部件；断板池唯一命中的利通电子正落消费电子，情绪有支撑；谐波与横盘启动票不在合格清单内，只能靠盘口自证。'
)

PUSHTEXT = (
    '三大指数高开分化：上证+1.01%（开+0.82%）续升，深成+2.00%、创业板+3.16%均自开盘高点回吐，半小时8207亿。自动化设备+4.75%、消费电子+4.73%、通信设备+4.69%领涨，证券-0.14%、银行-0.45%垫底；资金消费电子+10.3亿、汽车零部件+9.2亿、通用设备+6.0亿进，元件-41.2亿、通信设备-25.1亿出——涨最猛的通信设备反而失血，属高位换手。\n\n'
    '消费电子涨且进钱，日内有望延续，但指数高位横盘、量能放大，防冲高回落；通信设备净流出25亿不追高；元件流出41亿回避。\n\n'
    '确认档9只：利通电子（英伟达概念）落消费电子、情绪有支撑，但跌破均价线4次、多空平衡，只到「等」；沈阳机床（工业母机）、峨眉山Ａ（冰雪产业）爆量拉升且均价线上移，洗盘承接→启动得确认，观察；南矿集团（金属回收）均价线斜率仅+0.01%，承接不实、只观察；江化微（光刻胶）、红宝丽（环氧丙烷）、海格通信（大飞机）、深圳华强（华为昇腾）均价线上移，观察；东软集团（DRG/DIP）高开走弱、跌破均价线，放弃。待确认档3只（万向德农·转基因、证通电子·算力租赁、四川长虹·财税数字化）差一根确认K线，只盯不出手，不给买点。\n\n'
    '买点清单7只在买区但赔率闸全不满足（R>8%），可下单0只；昨夜8只挂单3只可挂、5只高于限价不追、0只破下沿。\n\n'
    '连板15只续板5、走强4，池内同题材候选3，中京电子3板的高阶HDI对芯瑞达；今日无对照昨日计划。\n\n'
    '止损距离宽是固有代价：买价相对涨停日最低价中位溢价+9.2%、43%的单子超+10%，回测17.9%触发止损、均亏5.5%，单笔仓位必须小。'
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
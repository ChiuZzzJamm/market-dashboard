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
    "开盘板块情绪：白酒+5.62%、证券+2.88%领涨，饮料制造+2.77%跟随，"
    "贵金属-4.76%、电机-4.16%、自动化设备-2.18%领跌，权重与消费强、成长弱。"
    "资金情绪：证券+29.3亿、银行+22.2亿、白酒+16.7亿净流入，光学光电子-5.0亿、元件-3.8亿小幅流出，"
    "属高切低而非全面进攻。合格板块＝白酒、证券、银行；断板双池无一板块与之匹配，"
    "本次断板反包池不入选，标的全来自技术池。"
)

PUSHTEXT = (
    "①情绪与资金：上证+0.27%（开盘+0.42%）、深成+0.17%、创业板+0.12%，7531亿成交，高开后收窄。"
    "白酒+5.62%、证券+2.88%领涨，贵金属-4.76%、电机-4.16%领跌。"
    "资金净流入证券+29.3亿、银行+22.2亿、白酒+16.7亿，净流出仅光学光电子-5.0亿、元件-3.8亿，"
    "钱从成长挪向权重。\n\n"
    "②推演：白酒资金同步进16.7亿、有延续基础，但+5.6%已把当日预期打进去，防冲高回落；"
    "证券涨2.88%配29.3亿最健康，看强度延续；银行只托指数、别指望弹性；"
    "贵金属、电机资金离场，继续回避不抢反抽。\n\n"
    "③映射：断板双池无一板块落在白酒/证券/银行，反包池0只属结构无交集。"
    "技术池14只（确认10/待确认4）：新城控股、鸿路钢构、飞科电器、东方雨虹爆量拉升且均价线上移，"
    "可观察；酒鬼酒、海南橡胶、盾安环境多空平衡，等；滨江集团、神州数码、中远海特、方正科技"
    "均价线下整理，等；索通发展、华胜天成有量下杀无承接，不接。"
    "待确认4只（陈学长观察3、缩量回踩1）未走完，只盯不给价。\n\n"
    "④纪律：买入清单7只全在陈学长放宽池、均在买区，但索通发展盘口有量下杀、到价不接。"
    "昨夜挂单8只：厦门信达、航天晨光可挂；惠发食品已破形态下沿、撤单；"
    "中远海特、钧达股份、西部材料、开滦股份、顺灏股份高于限价、不追。"
    "止损偏宽是形态代价：买价相对T日最低价中位溢价+9.2%、43%单子超+10%；"
    "回测17.9%止损、均亏5.5%，46.5%的+5%止盈覆盖，单笔仓位必须小（每只1/32资金）。"
    "强板块落后股今日无组合（通信设备+2.0%、白酒+1.6%里落后2pp且在涨的都没有），不出手。"
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


if __name__ == "__main__":
    main()
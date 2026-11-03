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
    "开盘板块情绪：电池+3.66%、光伏设备+3.40%、贵金属+3.30%领涨，"
    "元件-1.78%、医疗服务-2.10%、油气开采及服务-2.17%领跌；资金同向，"
    "电池净流入18.6亿、光伏设备10.4亿、工业金属7.5亿，文化传媒、零售、影视院线小幅流出。"
    "三项双正向即电池、光伏设备、工业金属，断板反包两只光伏标的获板块级确认；"
    "技术池所在钢铁、房地产、商业百货不在合格清单，只靠自身形态。"
)

PUSHTEXT = (
    "①开盘半小时：电池+3.66%、光伏设备+3.40%、贵金属+3.30%领涨，"
    "元件-1.78%、医疗服务-2.10%、油气开采及服务-2.17%垫底；主力资金电池+18.6亿、"
    "光伏设备+10.4亿、工业金属+7.5亿，文化传媒、零售、影视院线小幅流出。"
    "上证-1.08%、深成-0.05%、创业板-0.62%自低开回升，半小时6193亿。\n\n"
    "②推演：电池与光伏设备涨幅、资金、量能同向，日内偏延续，但已连强，防午后冲高回落；"
    "工业金属资金进、涨幅温和，属补涨位；元件、医疗服务跌幅居前且资金无回补，日内回避。\n\n"
    "③映射：断板反包两只都在光伏设备（亚玛顿、钧达股份，基线均80%），板块情绪加分；"
    "亚玛顿开盘价抓空、不给方向，钧达现涨5.4%高于买区、不追。"
    "技术池中横盘启动的鸿路钢构92分、新城控股88分、吸筹的我爱我家76分均爆量拉升站上均价线，"
    "属洗盘承接转启动，但钢铁、房地产、商业百货非合格板块，只算个股行为。"
    "两档确认12只/待确认6只（陈学长观察5、缩量回踩1），待确认还差一根确认K线，今天只盯不买。\n\n"
    "④盘口：12只爆量拉升、2只下探站回可观察；3只多空平衡、1只弱势整理（酒鬼酒）按等；"
    "无有量下杀、无高开走弱。\n\n"
    "⑤买入清单7只全在买区内（断板确认池2、陈学长放宽池5），如亚玛顿19.09~19.47；"
    "昨夜挂单8只、可挂5只，津滨发展、羚锐制药、锦江航运高于限价不追，无破位。"
    "强板块落后股1只，纵横通信落后文化传媒2.8pp。\n\n"
    "⑥止损距离偏宽是形态固有代价：买价较T日最低价中位溢价9.2%、43%单子超10%；"
    "回测17.9%止损、均亏5.5%，单笔仓位须小。"
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
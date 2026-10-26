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

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data.js")
NODE = "/Users/loccco/.workbuddy/binaries/run-node"

# R100z162：原为硬编码日期，每天必须由 AI 手改，忘了就撞下面的日期断言、白耗一个回合。
# 改为系统日期：守卫（star.date 必须等于今天）照旧生效，但不再需要人工维护。
TODAY = datetime.date.today().isoformat()

# 可选：picks 删减白名单（只删不加）。空集合=保持 update_star.py 原样。
DROP_CODES = set()

SENTIMENT = (
    "⚠️ 数据缺口：同花顺行业资金页抓空（净流入缺失），本次不做板块挂钩判定，断板池不参与，"
    "仅留技术池标的——是取数故障，不是无合格板块。90 个行业 18 涨 71 跌，"
    "医药商业+3.31%、医疗器械+1.27% 领涨，银行+0.77%、白酒+0.62% 跟随，属避险高低切换；"
    "贵金属-5.93%、元件-4.32% 领跌。指数上证-0.44%、创业板-2.13%。技术池 10 只里 5 只放量拉升。"
)

PUSHTEXT = (
    "【开盘情绪与资金】三大指数低开后继续走弱：上证-0.44%、深成-1.33%、创业板-2.13%，"
    "半小时成交 6398 亿。90 个行业仅 18 涨 71 跌，红的清一色防御：医药商业+3.31%、医疗器械+1.27%、"
    "油气开采+1.00%、银行+0.77%、白酒+0.62%；跌的是成长与资源：贵金属-5.93%、元件-4.32%、"
    "通信设备-2.32%。⚠️ 同花顺行业资金页本次抓空、主力净流入判据缺失，"
    "按纪律不做板块挂钩判定，断板反包池不参与，只留技术池形态标的——这是取数故障，"
    "不是「无合格板块」，也不是「板块无机会」。\n\n"

    "【板块推演】医药商业、医疗器械是今日唯一有连续性的方向，避险抱团但涨速缓，"
    "日内更可能高位整理，追高性价比低；银行、白酒属跌市躲避仓，护指数可以、进攻不足，不宜当反包主线。"
    "元件、贵金属、通信设备跌幅都过 2%，是杀跌主力，日内反弹先按修复看，下跌中段不接；"
    "创业板-2.13% 说明小盘成长仍在被减仓，超跌反抽不宜当反转。\n\n"

    "【标的映射】10 只全部来自技术池（九门3+陈学长2+谐波3+吸筹2）。盘口结构 5 只爆量拉升且均价线上移："
    "001298 好上好+6.2%、002696 百洋股份低开-7.3% 后拉到+5.2%、603859 能科科技+7.4%、"
    "002439 启明星辰、000032 深桑达Ａ，可观察；另 5 只不给方向：002612 朗姿股份、002074 国轩高科、"
    "601628 中国人寿、600693 东百集团 多空平衡，600301 华锡有色均价线下弱势整理，一律等、不接不追。"
    "今日无板块开盘跳空超 +1%，没有强板块就谈不上落后股，这一路不出手。"
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
        os.remove(_tmp)
        raise SystemExit("[FAIL] 改写结果未通过 node --check，已丢弃、data.js 保持原样：\n"
                         + (_chk.stderr or "")[:800])
    os.replace(_tmp, DATA)
    print(f"[ok] sentiment {len(SENTIMENT)} 字 / pushText {len(PUSHTEXT)} 字，node --check 通过")


if __name__ == "__main__":
    main()
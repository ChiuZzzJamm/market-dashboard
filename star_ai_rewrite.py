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
    "开盘情绪：84 涨 6 跌，元件+3.72%、半导体+3.62%、电子化学品+3.41% 领涨；"
    "仅旅游及酒店-0.06%、保险-0.19%、白酒-2.33% 下跌。资金同向，半导体+31.4亿、通信设备+27.8亿、"
    "元件+22.1亿流入，流出端贵金属-2.7亿、工业金属-1.6亿，属科技线内部加仓。"
    "合格板块＝元件、半导体、通信设备；断板池唯一标的汇顶科技直接吃到双正向，技术池多数不在其中。"
)

PUSHTEXT = (
    "① 情绪与资金：上证+0.55%、深成+1.36%、创业板+2.19%，半小时 6112 亿。"
    "元件+3.72%、半导体+3.62%、电子化学品+3.41% 领涨，白酒-2.33% 领跌。"
    "资金半导体+31.4亿、通信设备+27.8亿、元件+22.1亿，仅贵金属-2.7亿流出，科技线内部加仓。\n\n"
    "② 推演：元件 62 只全红、一致度太高，午后易分化，追高防回落；通信设备 27.8 亿最猛、涨幅仅 2.1%，位置最低。"
    "房地产翻红但资金近零，两只横盘启动票拿不到助推；零售名义+0.25% 而资金流出 3 亿，国芳集团+4.3% 属个股吸筹；"
    "煤炭此刻才小幅转正，属跟涨。\n\n"
    "③ 映射与盘口：汇顶科技（半导体·利好·基线70%）唯一吃到双正向，平开后+2.9%、站回均价线，反包有背书。"
    "爆量拉升 6 只（鸿路钢构、滨江集团、国芳集团、天娱数科、郑州煤电、新集能源）均价线上移才是真买盘，可观察；"
    "多空平衡 4 只（汇顶科技、新城控股、百洋股份、科瑞技术）等；恒源煤电跌破均价线，等。\n\n"
    "④ 买点 29 只、22 只在买区内（确认池1、横盘启动21、陈学长7），汇顶科技 59.35~60.55。昨夜 8 只挂单 5 只开盘"
    "在可成交区间；良品铺子、生意宝、春光科技高于限价、不追；止损宽是形态代价，买价较 T 日最低价中位溢价+9.2%、"
    "43% 超+10%，回测 17.9% 触发止损、均亏 5.5%。\n\n"
    "⑤ 落后股 3 只：百大集团、东百集团落后零售 5.6pp/4.9pp，实益达落后电子器件 2.4pp；"
    "两只零售股此刻仍-5%、-4%，属落后不是低估，等企稳。"
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
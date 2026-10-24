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
    "开盘单边下挫：上证-1.76%、深成-1.74%、创业板-2.04%，半小时成交5124亿，较昨日同期6489亿缩量约两成。"
    "90个行业仅白酒一档翻红（+0.9%，16涨3跌），军工装备-3.21%、通信设备-3.48%、元件-4.50%领跌。"
    "资金半导体+18.5亿、电池+5.5亿逆势流入，银行-10.2亿、证券-4.5亿流出。涨幅与资金背离，"
    "无板块双正向，断板反包无挂钩板块，仅留技术池3只。"
)

PUSHTEXT = (
    "【开盘情绪与资金】三大指数单边下挫，上证-1.76%、深成-1.74%、创业板-2.04%；"
    "半小时两市成交5124亿，较昨日同期6489亿缩量约两成，属缩量阴跌而非恐慌抛售。"
    "90个行业仅白酒翻红（+0.9%），军工装备-3.21%、通信设备-3.48%、元件-4.50%领跌；"
    "主力资金半导体+18.5亿、电池+5.5亿逆势流入，银行-10.2亿流出。"
    "资金撤离权重金融、转向超跌硬件低吸，股价未跟上，属左侧建仓。\n\n"

    "【板块推演】白酒是唯一逆势红盘，金徽酒+4.87%领涨，防御属性明确；"
    "体量小、涨速缓，二次下探易回吐，只宜低吸。"
    "军工装备、通信设备、元件跌3.21%~4.50%，其中军工装备净流出4.6亿，属杀跌主线，继续回避。"
    "半导体虽获18.5亿净流入仍跌近2%，抄底未换来承接，日内看能否低位放量收下影，未确认前不参与。"
    "银行净流出10.2亿、板块跌1%，权重失血使指数缺护盘。\n\n"

    "【标的映射】断板双池本次无一只入榜：池内标的集中在汽车零部件（-2.10%）、通用设备-2.76%"
    "等全线走弱且资金流出的方向，与唯一翻红的白酒无交集，板块挂钩为空集，属池结构使然。"
    "技术池3只：中国东航（谐波Crab）开盘-0.6%、现跌约1%，机场航运-0.84%且13只全跌，弱于板块；"
    "国投电力（谐波Cypher）开盘-0.4%、现跌约0.9%，电力板块-2.61%、110只下跌，抗跌逾1.7个百分点；"
    "华锡有色（吸筹62分）开盘-0.7%、现跌约1.1%，化工-2.94%中显著抗跌，小金属净流入2.3亿托底。"
    "三只均属弱市抗跌，非强势突破。\n\n"

    # R100z193：这一行原先写「放量收复竞价价位再介入」——把集合竞价当强弱依据，
    # 与用户 2026-10-24 的口径相反（9:15–9:20 可撤单）。示例文本对 AI 的引导力强于规则，
    # 所以必须改成「盘口结构过关才接」的写法，否则规则写十遍也会被示例带回去。
    "【操作】冰点日不追高、不抄跌；开盘看盘口结构，放量站上均价线（结构为爆量拉升/下探被接回）才接，"
    "有量下杀无承接则放弃。"
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
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🌟 开盘精选（R98k→R99，每日 9:45 自动化调用）。

职责（脚本自包含、AI 只复核不改结构）：
  1. 读 data.js 断板反包双池（07:00 复核后的 confirmed/watching，sentiment/probability 已校准）；
  2. 抓开盘半小时盘面：指数涨跌+成交额（腾讯 gtimg，本机可达）、
     同花顺行业涨跌幅（fetch_ths_industries）、行业主力资金净额（fetch_ths_funds）；
  3. 个股当日实时涨幅批量快照（腾讯 gtimg，一次批量）；
  4. 确定性筛选「板块挂钩」标的写入 duanban.brief（R99b）：
     合格板块 = 开盘涨幅>0（板块情绪正向）且 主力净流入>0（资金正向）的板块；
     精选 = 池内非利空标的、其板块与合格板块匹配（互含模糊匹配），按上涨概率降序；
     无合格板块则 picks 为空（宁缺勿滥），绝不放板块不挂钩的标的；
     R100z156：行业/资金接口抓空（连续 2 次）按「数据缺口」降级——不做板块挂钩判定，
     marketLine/sentiment/pushText 三处显式标注，**不写成「无合格板块」**；
  5. marketLine / sentiment 写模板句（R99：只写开盘后板块情绪与资金情绪，不再含集合竞价），
     pushText 留空——由自动化 AI 复核改写为「📖 分析」。

只写 duanban.brief（含 generatedAt 不动），其余字段一律不动；东财被 WAF 不影响（同花顺/腾讯源）。
用法：python3 update_brief.py [--dry]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_ashare_sectors as uas  # noqa: E402  （复用 curl/UA/同花顺解析）
import common  # noqa: E402  R100z285：清临时文件用 purge_tmp（不删，见 common.purge_tmp）

# R100z55：原为无兜底硬编码路径，托管 node 升版后目录消失即崩；改为存在性判断 + PATH 兜底。
_NODE_CAND = "/Users/loccco/.workbuddy/binaries/run-node"
NODE = _NODE_CAND if os.path.exists(_NODE_CAND) else "node"
IDX_QT = "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
IDX_NAMES = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指"}

# R100z192（2026-10-24）：竞价跳空门槛。数字全部来自 24.50 的回测，**不在这里写结论**。
# 回测（1152 个「断板确认池股票-日」样本、开盘买当日收盘卖、扣 0.20%）：
#   竞价跳空 < −2%  → +0.99%、中位 +0.06%、胜率 50.8%、日聚类 t=+2.15  ← 唯一 t>2 的档
#   −2~−1%          → +0.14%、中位 −0.07%、胜率 50.0%、t=+0.55
#   −1~0%           → +0.03%、中位 −0.46%、胜率 45.4%、t=+0.36
#   0~+1%           → −0.16%、中位 −0.25%、胜率 46.9%、t=−0.37
#   +1~+2%          → −0.45%、中位 −0.94%、胜率 40.0%、t=−1.31  ← 最差
#   > +2%           → −0.33%、中位 −0.18%、胜率 48.8%、t=−1.05
# 注意：−5~−3% 那档只有 +0.18%/中位 −0.69%/t=0.04，**所以不是「越低越好」**，
#      这里只做「≥0 不追、≤−2% 可接」的两端标注，中间档如实写「中性」，禁止拿它排序。
GAP_REPAIR = -2.0   # ≤ 此值：低开修复档（唯一过 t=2 的一档）
GAP_NOCHASE = 0.0   # ≥ 此值：高开档（回测全负，最差 +1~+2%）



# R100z254（用户 2026-10-28「回测数据很低要么删掉，要么就辅佐卡片」）：
# **横盘启动·贴沿 不再进买入清单**。理由不是口味，是它自己的回测：
#   实测（N 字形态研究 §8.2）—— 贴沿提前买 70.4% 胜率、**单笔仅 +0.22%**、盈亏比 1.16、均持 3.5 日；
#   `24.87` 同一口径按信号日聚类：T+5 超额 **+0.148%、t=0.41（不显著）**。
# 而它在 10-28 那天占了清单 21/29 只（72%），被「深跌不接」挡掉的 10 只里也有 9 只是它——
# **唯一没有 edge 的一源，贡献了绝大部分噪声，还把有 edge 的断板池/陈学长淹在里面**。
# 所以不是删池子（横盘启动子页原样保留、形态照样看），是**不让它冒充「今天该买什么」**。
# 回退只要把这个常量改 True；改之前请先重跑 chen_study / breakout 口径，别凭印象。
BUY_INCLUDE_BP = False


def _num(x):
    """安全转 float：None / '' / NaN / 非数值一律 None（不做 0 兜底，0 是合法价格）。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


SPILL_MAX_ROWS = 8          # 连板行上限（昨日连板通常 6~12 只，取前 8）
SPILL_MAX_MATCH = 3         # 每只连板最多给 3 只池内候选


def load_data(data_path):
    with open(data_path, encoding="utf-8") as f:
        s = f.read()
    i = s.index("{")
    return s, json.loads(s[i:s.rindex("}") + 1])


def fetch_open_indices():
    """腾讯 gtimg 指数快照 → [{name, pct, amount亿}]；失败 []。"""
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "15", IDX_QT],
                           capture_output=True, timeout=25)
        txt = r.stdout.decode("gbk", errors="ignore")
    except Exception:
        return []
    out = []
    for m in re.finditer(r'v_(sh|sz)(\d{6})="([^"]+)"', txt):
        sym = m.group(1) + m.group(2)
        f = m.group(3).split("~")
        if len(f) <= 37:
            continue
        try:
            cur, prev = float(f[3]), float(f[4])
            pct = round((cur - prev) / prev * 100, 2) if prev else 0.0
            open_pct = None
            try:
                op = float(f[5])
                open_pct = round((op - prev) / prev * 100, 2) if prev and op > 0 else None
            except Exception:
                pass
            amt_yi = round(float(f[37]) / 10000, 0)  # gtimg 成交额单位万元 → 亿
            out.append({"name": IDX_NAMES.get(sym, f[1]), "pct": pct,
                        "openPct": open_pct, "amount": amt_yi})
        except Exception:
            continue
    return out


def fetch_industries_resilient():
    """同花顺行业一览：抓空重试一次，仍空返回 (None, 原因)。

    R100z156（2026-10-20）：`common.fetch_ths_industries` 的契约是「抓空返回 []，
    由调用方自行走备源/兜底」，但本脚本原来只写 `or []`，把「接口抓空」静默当成
    「当日没有合格板块」——10-20 09:54 行业页被反爬打回 17KB 空页（20:05 复测已
    恢复 90 行，属间歇性），star 判定「合格板块：无」，断板池 0 只入选，页面与
    微信推送都看不出是取数故障。现改为：二次重试 → 仍空则显式降级（marketLine /
    sentiment 标数据缺口、pushText 写兜底提示、stdout 打 [WARN]），
    **绝不把「抓不到」写成「没有」**。
    """
    last_n, last_err = 0, None
    for attempt in (1, 2):
        try:
            rows = uas.fetch_ths_industries() or []
            last_err = None
        except Exception as e:  # noqa: BLE001 —— 任何抓取异常都按「抓空」处理
            rows, last_err = [], repr(e)
        last_n = len(rows)
        if last_n >= 10:
            if attempt == 2:
                print(f"[WARN] 同花顺行业接口第 2 次重试成功（{last_n} 行），本次数据可用")
            return rows, None
        if attempt == 1:
            print(f"[WARN] 同花顺行业接口仅返回 {last_n} 行（<10，疑似反爬空页），2s 后重试一次")
            time.sleep(2.0)
    reason = f"同花顺行业接口连续 2 次抓空（末次 {last_n} 行"
    if last_err:
        reason += f"，异常 {last_err}"
    return None, reason + "）"


def build_star(D, base=None):
    """只产出「大盘 / 板块方向 / 资金」三段文字与快照，不产出任何个股名单。

    2026-11-25 用户：「开盘精选不再从技术池推荐标的，只需要说大盘情况、板块方向、
    资金流出流入情况」。所以这里不再读断板池/谐波/横盘启动/陈学长池，也不再算候选、
    买点、档位——只取盘面数据，写 marketLine / sentiment / pushText 三处。
    """
    base = base or os.path.dirname(os.path.abspath(__file__))
    now = datetime.now()

    # ---- 盘面：指数 / 行业涨幅 / 主力资金 ----
    idx = fetch_open_indices()
    ths, ths_err = fetch_industries_resilient()
    sectors = []
    for x in (ths or []):
        try:
            sectors.append({"name": x.get("name") or "", "code": x.get("code") or "",
                            "pct": round(float(x.get("pct") or 0), 2)})
        except Exception:
            continue
    sectors = [x for x in sectors if x["name"]]
    sectors.sort(key=lambda x: -x["pct"])
    try:
        fund_in, fund_out = uas.fetch_ths_funds()
    except Exception:
        fund_in, fund_out = None, None

    # 行业涨幅 / 主力资金 任一条抓空 = 数据缺口，必须显式降级：
    # 「抓不到」不能写成「没有机会」。
    gap_reason = ths_err or (None if fund_in else "同花顺行业资金接口抓空（主力净流入判据缺失）")
    degraded = gap_reason is not None

    # ---- 文案模板（AI 只改写这两段，不新增内容）----
    market_line = "，".join(
        (f"{x['name']}{x['pct']:+.2f}%（开盘{x['openPct']:+.2f}%）"
         if x.get("openPct") is not None else f"{x['name']}{x['pct']:+.2f}%")
        for x in idx) if idx else "指数快照获取失败"
    if idx:
        total_amt = sum(x.get("amount") or 0 for x in idx)
        market_line += f"，半小时两市成交约 {total_amt:.0f} 亿"
    if degraded:
        market_line += "（⚠️ 行业/资金接口抓空，板块判定本次不参与）"

    top3 = "、".join(f"{x['name']}{x['pct']:+.2f}%" for x in sectors[:3]) or "—"
    bot3 = "、".join(f"{x['name']}{x['pct']:+.2f}%" for x in sectors[-3:]) or "—"
    fi_txt = "、".join(f"{x['name']}{x['value']:+.1f}亿" for x in (fund_in or [])) or "—"
    fo_txt = "、".join(f"{x['name']}{x['value']:+.1f}亿" for x in (fund_out or [])) or "—"

    gap_head = ""
    if degraded:
        gap_head = (f"⚠️ 数据缺口：{gap_reason}。板块涨幅与主力净流入两项判据本次缺失，"
                    "按纪律不做板块判定。严禁把「接口抓空」写成「无机会」。")
    sentiment = (gap_head
                 + f"开盘板块情绪：领涨 {top3}；领跌 {bot3}。"
                 f"资金情绪：主力净流入前列 {fi_txt}；净流出前列 {fo_txt}。"
                 + "（AI 复核：改写为完整的大盘与板块方向判断，**不写个股**）")

    push_text = "" if not degraded else (
        "⚠️ 本次开盘精选存在数据缺口：同花顺行业涨幅与主力资金接口连续两次抓取失败，"
        "板块情绪与资金流向两项判据缺失。指数行情取自腾讯，正常可用。")

    star = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "marketLine": market_line,
        "sentiment": sentiment,
        "pushText": push_text,
        "generatedBy": "update_brief.py：只产出大盘/板块/资金三段，不产出个股名单",
    }
    return star, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印不写回")
    args = ap.parse_args()
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.js")
    base = os.path.dirname(data_path)
    _, D = load_data(data_path)
    star, err = build_star(D, base)
    if err:
        print(f"[FAIL] {err}，不写入")
        sys.exit(1)
    print(f"[star] {star['date']} {star['time']}")
    print("[star] marketLine:", star["marketLine"])
    if args.dry:
        print("[DRY-RUN] 未写回")
        return
    s, D = load_data(data_path)
    D.setdefault("duanban", {})["brief"] = star

    out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ";\n"
    # R100z179（L11 硬约束）：先写临时文件、node --check 通过再 os.replace。
    # 原写法 open(data_path,'w') 先截断再写、写完才 check——进程若在 write 中途被杀
    # （平台 90 分钟硬上限 / 超时重试），留下的是残缺 data.js，而 check 这一步
    # 根本没机会执行。临时名必须带 .js，node 22 拒绝未知扩展名。
    _tmp = data_path + ".tmp.js"
    with open(_tmp, "w", encoding="utf-8") as f:
        f.write(out)
    _chk = subprocess.run([NODE, "--check", _tmp], capture_output=True, text=True, timeout=30)
    if _chk.returncode != 0:
        common.purge_tmp(_tmp)      # R100z285：清空而不删——沙箱把 remove 变废纸篓
        raise SystemExit("[FAIL] star 写回未通过 node --check，已丢弃、data.js 保持原样：\n"
                         + (_chk.stderr or "")[:800])
    os.replace(_tmp, data_path)
    print("[star] data.js 写回完成，node --check 通过")


if __name__ == "__main__":
    main()

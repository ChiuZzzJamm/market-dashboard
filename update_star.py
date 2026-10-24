#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🌟 开盘精选（R98k→R99，每日 9:45 自动化调用）。

职责（脚本自包含、AI 只复核不改结构）：
  1. 读 data.js 断板反包双池（07:00 复核后的 confirmed/watching，sentiment/probability 已校准）；
  2. 抓开盘半小时盘面：指数涨跌+成交额（腾讯 gtimg，本机可达）、
     同花顺行业涨跌幅（fetch_ths_industries）、行业主力资金净额（fetch_ths_funds）；
  3. 个股当日实时涨幅批量快照（腾讯 gtimg，一次批量）；
  4. 确定性筛选「板块挂钩」标的写入 duanban.star（R99b）：
     合格板块 = 开盘涨幅>0（板块情绪正向）且 主力净流入>0（资金正向）的板块；
     精选 = 池内非利空标的、其板块与合格板块匹配（互含模糊匹配），按上涨概率降序；
     无合格板块则 picks 为空（宁缺勿滥），绝不放板块不挂钩的标的；
     R100z156：行业/资金接口抓空（连续 2 次）按「数据缺口」降级——不做板块挂钩判定，
     marketLine/sentiment/pushText 三处显式标注，**不写成「无合格板块」**；
  5. marketLine / sentiment 写模板句（R99：只写开盘后板块情绪与资金情绪，不再含集合竞价），
     pushText 留空——由自动化 AI 复核改写为「📖 分析」。

只写 duanban.star（含 generatedAt 不动），其余字段一律不动；东财被 WAF 不影响（同花顺/腾讯源）。
用法：python3 update_star.py [--dry]
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


def gap_tag(open_pct):
    """开盘价相对昨收的位置 → (tag, 中文)。

    R100z193（2026-10-24 用户口径）：**只描述价格事实，不再做「接/不追」判断**。
    9:15–9:20 的竞价参考价可撤单、是假的；但 9:25 的成交价是真成交，所以 openPct
    本身是可靠的事实，不可靠的是「用开盘高低去推断强弱」这个用法。动作交给 tape.py。
    """
    if open_pct is None:
        return "unknown", "开盘价缺失"
    if open_pct <= GAP_REPAIR:
        return "repair", "低开"
    if open_pct > GAP_NOCHASE:
        return "nochase", "高开"
    return "neutral", "平开"


def _gap_summary(picks):
    """开盘价位置分布（低开/平开/高开各几只）。

    R100z193 起**不再承载动作结论**——2026-10-24 用户明确反对「拿竞价定强弱」，
    动作口径移到 tapeSummary（分时盘口结构）。这里只留一个可核对的价格事实分布，
    并配一条 rule 把「不许据此下结论」写死，防止 AI 拿它编「低开可接」。
    """
    pool = [p for p in picks if p.get("src") == "pool"]
    cnt = {"repair": 0, "neutral": 0, "nochase": 0, "unknown": 0}
    for p in pool:
        cnt[p.get("gapTag") or "unknown"] = cnt.get(p.get("gapTag") or "unknown", 0) + 1
    if not pool:
        text = "断板池本次无入选标的（板块不挂钩或均被剔除），无开盘价分布可言。"
    else:
        parts = []
        if cnt["repair"]:
            parts.append(f"低开 {cnt['repair']} 只")
        if cnt["neutral"]:
            parts.append(f"平开 {cnt['neutral']} 只")
        if cnt["nochase"]:
            parts.append(f"高开 {cnt['nochase']} 只")
        if cnt["unknown"]:
            parts.append(f"开盘价取不到 {cnt['unknown']} 只（不许猜方向）")
        text = f"断板池 {len(pool)} 只开盘价位置：" + "；".join(parts) + "。"
    return {"repairN": cnt["repair"], "neutralN": cnt["neutral"],
            "nochaseN": cnt["nochase"], "unknownN": cnt["unknown"],
            "poolN": len(pool),
            "rule": "本段只是开盘价相对昨收的位置（低开/平开/高开），是价格事实、不是强弱信号，"
                    "禁止用它给标的排序，也禁止用「低开=可接、高开=不追」这类结论——"
                    "R100z193 起动作口径改由 tapeSummary 的盘口结构决定",
            "text": text}


def load_data(data_path):
    with open(data_path, encoding="utf-8") as f:
        s = f.read()
    i = s.index("{")
    return s, json.loads(s[i:s.rindex("}") + 1])


def load_blacklist(base):
    """读 risk_blacklist.json 的 blacklist 段 → {code: severity}。

    R100z9y（2026-10-09 实战）：star 精选把硬风险黑名单标的（金溢科技 002869，
    red·两次减持公告）选进了 picks，直到 validate_stocks --scope star 才报 FAIL。
    断板池由 16:00 数据步负责、谐波/九门/吸筹池也不查减持，**开盘精选是唯一有机会
    在选股环节拦住这类标的的闸口**，故在脚本里硬剔（severity=red），而不是让
    每天靠 AI 事后补救（AI 补救还会踩「配额已用尽、下一名也上不了」的坑）。
    文件缺失/损坏一律按空处理（源不可达不等于安全，但也不阻塞本任务）。
    """
    try:
        with open(os.path.join(base, "risk_blacklist.json"), encoding="utf-8") as f:
            raw = json.load(f)
        bl = raw.get("blacklist") or {}
        return {str(c): str(v.get("severity") or "") for c, v in bl.items()
                if isinstance(v, dict)}
    except Exception:
        return {}


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


def fetch_stock_quotes(codes):
    """批量腾讯 gtimg 个股快照 → {code: {pct, openPct}}；失败 {}。"""
    quotes = {}
    for i in range(0, len(codes), 60):
        batch = codes[i:i + 60]
        q = ",".join(("sh" if c.startswith("6") else "sz") + c for c in batch)
        try:
            r = subprocess.run(["curl", "-s", "--max-time", "15",
                                f"https://qt.gtimg.cn/q={q}"],
                               capture_output=True, timeout=25)
            txt = r.stdout.decode("gbk", errors="ignore")
        except Exception:
            continue
        for m in re.finditer(r'v_(?:sh|sz)(\d{6})="([^"]+)"', txt):
            f = m.group(2).split("~")
            if len(f) > 32:
                try:
                    d = {"pct": round(float(f[32]), 2)}  # 32=涨跌幅%
                    try:
                        prev, op = float(f[4]), float(f[5])
                        d["openPct"] = round((op - prev) / prev * 100, 2) if prev and op > 0 else None
                    except Exception:
                        d["openPct"] = None
                    quotes[m.group(1)] = d
                except Exception:
                    continue
    return quotes


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
    base = base or os.path.dirname(os.path.abspath(__file__))
    blacklist = load_blacklist(base)
    db = D.get("duanban") or {}
    pools = []
    for pool in ("confirmed", "watching"):
        pools.extend(db.get(pool) or [])

    # ---- R100m（需求7）：技术池候选——直接从 谐波/九门/吸筹 池取标的（前一日 16:00 数据步结果） ----
    # 这些标的无 sentiment/probability（neutral 兜底），按池内得分排序，取头部，宁缺勿滥。
    # R100z85（量纲修正，2026-10-08 用户质询「标的映射只有断板+九门」暴露）：原实现全池合并
    # 按 score 降序取前 8，但九门量纲 score×10（8~10分=80~100）结构性碾压 谐波(确认65/观察55)
    # 与 吸筹(60~76)——实测九门前 10 全为 90/80，top8 被九门垄断，谐波 12 只/吸筹 6 只候选
    # 一个都进不了终选，AI 映射段随之只写断板+九门。改为**按池配额保底**：九门≤3、谐波≤3、
    # 吸筹≤2（配额决定入选资格，合并后分数只决定展示顺序），总量仍 ≤8；跨池去重按
    # 九门→谐波→吸筹优先级（多池命中取先处理池的 label）；-2% 深跌/≥9.5% 涨停剔除守卫不变。
    hz_list, pw_list, acc_list = [], [], []
    hz = D.get("harmonic") or {}
    _seen_hz = set()
    for it in (hz.get("confirmPool") or [])[:6] + (hz.get("watchPool") or [])[:6]:
        c = str(it.get("code") or "")
        if c.startswith(("60", "00")) and c not in _seen_hz:
            _seen_hz.add(c)
            hz_list.append({"code": c, "name": it.get("name"), "sector": it.get("sector") or "",
                            "src": "hz", "label": f"谐波{it.get('pattern') or ''}",
                            "score": 65 if it.get("stage") == "确认" else 55})
    pw = D.get("powerScreen") or {}
    _seen_pw = set()
    for it in (pw.get("passed") or [])[:10]:
        c = str(it.get("code") or "")
        if c.startswith(("60", "00")) and c not in _seen_pw:
            _seen_pw.add(c)
            pw_list.append({"code": c, "name": it.get("name"), "sector": it.get("sector") or "",
                            "src": "pw", "label": f"九门{it.get('score') or ''}分",
                            "score": int(it.get("score") or 0) * 10})  # 量纲：9分=90（仅池内排序用）
    acc = D.get("accumulation") or {}
    for it in (acc.get("scored") or []):
        try:
            sc = int(it.get("score") or 0)
        except Exception:
            continue
        c = str(it.get("code") or "")
        if sc >= 60 and c.startswith(("60", "00")):
            acc_list.append({"code": c, "name": it.get("name"), "sector": it.get("sector") or "",
                             "src": "acc", "label": f"吸筹{sc}分", "score": sc})
    tech, _used = [], set()
    for _lst, _quota in ((pw_list, 3), (hz_list, 3), (acc_list, 2)):
        _cnt = 0
        for _t in _lst:
            if _cnt >= _quota:
                break
            if _t["code"] in _used:
                continue
            if blacklist.get(_t["code"]) == "red":
                continue  # R100z9y：硬风险黑名单不进精选，且不占配额（顺位递补）
            _used.add(_t["code"])
            tech.append(_t)
            _cnt += 1
    tech.sort(key=lambda x: -x["score"])
    tech = tech[:8]

    if not pools and not tech:
        return None, "断板反包双池与技术池（谐波/九门/吸筹）均为空"

    # ---- 盘面：指数 / 行业涨幅 / 资金 ----
    idx = fetch_open_indices()
    ths, ths_err = fetch_industries_resilient()
    sectors = []
    for x in (ths or []):
        try:
            sectors.append({"name": x.get("name") or "", "code": x.get("code") or "",
                            "pct": round(float(x.get("pct") or 0), 2)})
        except Exception:
            continue
    sectors = [s for s in sectors if s["name"]]
    sectors.sort(key=lambda s: -s["pct"])
    # R100z170：技术池标的（谐波/九门/吸筹）身上只有 sector 名、没有板块涨幅字段，
    # 结果「开盘精选」里来自技术池的条目板块涨幅整片「—」。这里按同花顺一级行业名
    # 精确命中一次补上；名字不属同花顺一级（例如主题名「创新药·CXO」）就留空，
    # 由前端 boardPctOf 三级回落兜底，绝不硬套。
    sec_map = {s["name"]: s for s in sectors}
    try:
        fund_in, fund_out = uas.fetch_ths_funds()
    except Exception:
        fund_in, fund_out = None, None

    # R100z156：行业涨幅 / 主力资金 任一条判据抓空 = 数据缺口，必须显式降级，
    # 不能像过去那样让 good_boards 自然空集、把「抓不到」说成「无合格板块」。
    gap_reason = ths_err or (None if fund_in else
                             "同花顺行业资金接口抓空（主力净流入判据缺失）")
    degraded = gap_reason is not None

    # ---- 个股快照（当日实时涨幅）：断板池 + 技术池一并快照 ----
    quotes = fetch_stock_quotes([str(e.get("code")) for e in pools] + [t["code"] for t in tech])

    # ---- R99c：确定性筛选——仅「板块情绪正向 + 主力净流入正向」板块挂钩的池内标的 ----
    # 合格板块：涨幅>0 且 主力净流入>0；池内板块与合格板块名互含即视为挂钩。
    board_fund = {}
    if fund_in:
        for f in fund_in:
            try:
                board_fund[str(f.get("name") or "")] = float(f.get("value") or 0)
            except Exception:
                continue
    good_boards = []  # [(name, pct, fund亿)]
    for s in sectors:
        if s["pct"] <= 0:
            continue  # 板块情绪须正向
        fv = None
        for bn, bv in board_fund.items():
            if bn and (bn in s["name"] or s["name"] in bn):
                fv = bv
                break
        if fv is None or fv <= 0:
            continue  # 资金须净流入正向
        good_boards.append((s["name"], s["pct"], fv))

    if degraded:
        print(f"[WARN] {gap_reason}")
        print("[WARN] → 本次不做板块挂钩判定（严禁把「抓不到」说成「无合格板块」），"
              "断板池标的不参与精选，picks 仅保留技术池形态标的")

    def _linked(sec):
        for n, _, _ in good_boards:
            if n and sec and (n in sec or sec in n):
                return True
        return False

    sent_rank = {"bull": 0, "neutral": 1, "bear": 2}
    cand = []
    for e in pools:
        sent = e.get("sentiment") or "neutral"
        if sent == "bear":
            continue  # 利空标的不进精选
        if blacklist.get(str(e.get("code"))) == "red":
            continue  # R100z9y：硬风险黑名单（减持实施中/ST/预亏）不进精选
        sec = str(e.get("sector") or "")
        if not _linked(sec):
            continue  # R99b：板块不挂钩的不进精选
        try:
            p = float(e.get("probability") or 50)
        except Exception:
            p = 50.0
        q = quotes.get(str(e.get("code"))) or {}
        cand.append({"code": str(e.get("code")), "name": e.get("name"),
                     "sector": sec, "sentiment": sent, "probability": round(p, 1),
                     "pctLive": q.get("pct"), "openPct": q.get("openPct"),
                     # R100z170：双池标的的板块涨幅必须跟着搬过去。此前只搬 sector，
                     # 导致 picks 里 boardPctToday/boardPctZt 全是 None，前端整齐显示「—」
                     # （用户 2026-10-21 截图：中京电子/澳弘电子「板块涨幅 —」而池里同票有值）。
                     "boardPctToday": e.get("boardPctToday"),
                     "boardPctZt": e.get("boardPctZt"),
                     "bkCode": e.get("bkCode")})

    cand.sort(key=lambda x: (-x["probability"], sent_rank.get(x["sentiment"], 1)))
    picks = []
    for c in cand:  # 按上涨概率降序（概率同则利好优先）
        sent_cn = {"bull": "利好", "neutral": "中性"}.get(c["sentiment"], c["sentiment"])
        note = f"{c['sector']}·{sent_cn}·基线{c['probability']:.0f}%"
        _gt, _gzh = gap_tag(c.get("openPct"))
        if c.get("openPct") is not None:
            note += f"·开盘{c['openPct']:+.1f}%({_gzh})"
        if c.get("pctLive") is not None:
            note += f"·现涨{c['pctLive']:+.1f}%"
        picks.append({"code": c["code"], "name": c["name"], "sector": c["sector"],
                      "sentiment": c["sentiment"], "probability": c["probability"],
                      # R100z192：开盘价位置标注；R100z193 起它**不再是动作依据**
                      # （用户 2026-10-24：竞价可撤单、不可信；动作交给 picks[].tape）。
                      "gapTag": _gt, "gapZh": _gzh, "openPct": c.get("openPct"),
                      "note": note, "src": "pool",
                      "boardPctToday": c.get("boardPctToday"),
                      "boardPctZt": c.get("boardPctZt"),
                      "bkCode": c.get("bkCode")})

    # ---- R100m（需求7）：技术池候选并入精选（排在断板池之后）——开盘现价深跌(<-2%)的剔除，宁缺勿滥 ----
    pool_codes = {p["code"] for p in picks}
    for t in tech:
        if t["code"] in pool_codes or len(picks) >= 12:
            continue
        q = quotes.get(t["code"]) or {}
        live = q.get("pct")
        if live is not None and live < -2.0:
            continue  # 开盘深跌的形态标的暂不进精选
        if live is not None and live >= 9.5:
            continue  # 开盘即涨停（买不进），推荐无意义
        note = f"{t['label']}·前日16:00筛选"
        _gt, _gzh = gap_tag(q.get("openPct"))
        if q.get("openPct") is not None:
            note += f"·开盘{q['openPct']:+.1f}%({_gzh})"
        if live is not None:
            note += f"·现涨{live:+.1f}%"
        picks.append({"code": t["code"], "name": t["name"], "sector": t["sector"],
                      "sentiment": "neutral", "probability": None,
                      # R100z192：技术池同样标注跳空——但**它不是断板池那套结论**（买点逻辑不同），
                      # 所以只标注、不据此剔除；`src` 让前端/推送能区分两种口径。
                      "gapTag": _gt, "gapZh": _gzh, "openPct": q.get("openPct"),
                      # R100z170：技术池标的补板块涨幅（名字精确命中同花顺一级行业才写）
                      "boardPctToday": (sec_map.get(t["sector"]) or {}).get("pct"),
                      "bkCode": (sec_map.get(t["sector"]) or {}).get("code"),
                      "note": note, "src": t["src"]})

    # ---- R100z193（2026-10-24）：分时量价结构诊断 ----
    # 用户原话：「竞价往往都是虚的，9:25 之前都可以撤单……可以看开盘的分时量：
    # 下跌有量且没有承接就不看好；下跌无量、跌破分时线有承接可以观察；开盘爆量拉升也可观察」。
    # 上一版把「竞价跳空」当动作依据（gapTag/gapSummary），用户明确反对，故把解释权
    # 交给 tape.py：用腾讯 1 分钟分时算均价线、跌破次数、下跌分钟量占比、爆量倍数，
    # 输出「此刻的盘口结构 + 纪律档位」。gapTag 保留但降级为价格事实，不再定动作。
    # 失败不阻断选股：picks 只是不带 tape 字段，AI 侧按「结构未知」处理。
    tape_summary = None
    try:
        import tape as TAPE
        tm = TAPE.diagnose_codes([p["code"] for p in picks])
        for p in picks:
            t = tm.get(str(p["code"]))
            if t:
                p["tape"] = {k: v for k, v in t.items() if k != "name"}
                p["note"] += f"·分时[{t['zh']}]"
        tape_summary = TAPE.summarize(tm, picks)
        miss = [p["code"] for p in picks if str(p["code"]) not in tm]
        if miss:
            tape_summary["missing"] = miss
            tape_summary["text"] += (f"（{len(miss)} 只分时抓空：{'、'.join(miss)}，"
                                     "未给结构判定，严禁猜方向）")
        print(f"[tape] {tape_summary['text']}")
    except Exception as e:                                  # noqa: BLE001
        print(f"[tape][WARN] 分时诊断失败，picks 不带 tape 字段：{type(e).__name__}: {e}")

    # ---- 文案模板（AI 复核改写 sentiment/pushText） ----
    now = datetime.now()
    market_line = "，".join(
        (f"{x['name']}{x['pct']:+.2f}%（开盘{x['openPct']:+.2f}%）"
         if x.get("openPct") is not None else f"{x['name']}{x['pct']:+.2f}%")
        for x in idx) if idx else "指数快照获取失败"
    if idx:
        total_amt = sum(x.get("amount") or 0 for x in idx)
        market_line += f"，半小时两市成交约 {total_amt:.0f} 亿"
    if degraded:  # R100z156：marketLine 也要可见地标出缺口，别让页面看不出是取数故障
        market_line += "（⚠️ 行业/资金接口抓空，板块判定本次不参与）"
    top3 = "、".join(f"{s['name']}{s['pct']:+.2f}%" for s in sectors[:3]) or "—"
    bot3 = "、".join(f"{s['name']}{s['pct']:+.2f}%" for s in sectors[-3:]) or "—"
    fi_txt = "、".join(f"{x['name']}{x['value']:+.1f}亿" for x in (fund_in or [])) or "—"
    fo_txt = "、".join(f"{x['name']}{x['value']:+.1f}亿" for x in (fund_out or [])) or "—"
    # R99c：只写开盘后板块情绪与资金情绪（集合竞价强弱关系由 AI 写入 pushText）
    # R100z156：数据缺口时首句直接点明，并把「合格板块」从「无」改成「本次不判定」——
    # AI 改写 sentiment 时会读到这句，避免它把取数故障写成「板块无机会」。
    gap_head = ""
    if degraded:
        gap_head = (f"⚠️ 数据缺口：{gap_reason}。板块涨幅与主力净流入两项判据本次缺失，"
                    "按纪律不做板块挂钩判定（断板池标的不参与精选），picks 仅含技术池形态标的。"
                    "严禁把「接口抓空」写成「无合格板块」或「板块无机会」。")
        board_line = "合格板块：判据缺失，本次不判定。"
    else:
        board_line = f"合格板块（涨+净流入双正向）：{'、'.join(n for n, _, _ in good_boards) or '无'}。"
    sentiment = (gap_head
                 + f"开盘板块情绪：领涨 {top3}；领跌 {bot3}。"
                 f"资金情绪：主力净流入前列 {fi_txt}；净流出前列 {fo_txt}。"
                 + board_line
                 + "（AI 复核：请改写为完整开盘情绪判断与对断板反包标的的影响分析）")

    # R100z156：降级时预置 pushText 兜底——页面「🌟 开盘精选」正文与微信推送共用此字段，
    # 前端只渲染 pushText（不渲染 marketLine/sentiment），AI 若漏改写或改跑失败，
    # 用户仍能看到「这是取数故障」而不是一行空白。正常情况仍留空交 AI 写。
    push_text = "" if not degraded else (
        "⚠️ 本次开盘精选存在数据缺口：同花顺行业涨幅与主力资金接口连续两次抓取失败，"
        "板块情绪与资金流向两项判据缺失。脚本按既定纪律不做板块挂钩判定，"
        "因此断板反包池标的全部不参与本次精选，仅保留前一日 16:00 数据步筛出的"
        "形态类技术池标的（谐波/九门/吸筹）。指数与个股实时涨跌取自腾讯行情，正常可用。"
        "此为取数故障，不代表当日「无合格板块」或市场无机会，请勿据此判断强弱；"
        "可结合盘中资金回流情况另行观察，待接口恢复后按常规口径重跑。"
    )

    star = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "marketLine": market_line,
        "sentiment": sentiment,
        "picks": picks,
        "gapSummary": _gap_summary(picks),
        "tapeSummary": tape_summary,
        "pushText": push_text,
        "generatedBy": "update_star.py 确定性筛选（R99c：断板池板块情绪正向+资金净流入正向挂钩，按上涨概率降序；R100m：并从谐波/九门/吸筹池直接取标的；R100z85：技术池按池配额 九门3/谐波3/吸筹2 防单池量纲垄断；R100z156：行业/资金接口抓空重试+显式降级；R100z192：picks 增 gapTag 开盘价位置标注 + star.gapSummary 分布，只标注不排序；R100z193：接入 tape.py 分时量价结构（均价线/跌破次数/下跌分钟量占比/爆量倍数），picks 增 tape 字段 + star.tapeSummary，动作口径由盘口结构决定，gapTag 降级为价格事实） + 自动化 AI 复核",
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
    print(f"[star] {star['date']} {star['time']} | 精选 {len(star['picks'])} 只:")
    for p in star["picks"]:
        print(f"  {p['code']} {p['name']} | {p['note']} | score源概率 {p['probability']}%")
    print("[star] marketLine:", star["marketLine"])
    if args.dry:
        print("[DRY-RUN] 未写回")
        return
    s, D = load_data(data_path)
    D.setdefault("duanban", {})["star"] = star

    # R100z190：强板块落后股（lead_lag.py）。与精选同一时点、同一份盘面快照，
    # 但选股方向相反：精选买「已经强、概率高」的，这里买「同一强势板块里还没跟上」的。
    # 为什么要并进 9:45 这一步：这条规则的入场条件（开盘跳空 + 现价偏离）只在
    # 开盘半小时内成立，晚一步买入滑点就吃掉全部超额（回测：+0.8% 滑点 → 期望归零）。
    # 失败不能连累 star：精选已经算完了，这块只是加分项。
    ll = None
    try:
        import lead_lag as LL
        ll, llerr = LL.compute(D)
        if llerr:
            print(f"[leadLag][WARN] {llerr}")
            ll = None
    except Exception as e:                                  # noqa: BLE001
        print(f"[leadLag][WARN] 计算失败，跳过（不影响精选）：{type(e).__name__}: {e}")
        ll = None
    if ll:
        bt, bterr, hit = LL.backtest_cached(D)
        if bt:
            m, al = bt["main"], bt["alt"]
            better = al if (al.get("tDaily") or -99) > (m.get("tDaily") or -99) else m
            ll["bt"] = bt
            ll["exit"] = {"main": better, "alt": (m if better is al else al)}
            ll["span"] = {"days": bt["days"], "samples": bt["samples"],
                          "signalDays": bt["signalDays"]}
            print(f"[leadLag] 回测{'(缓存)' if hit else '(已重算)'} "
                  f"{bt['signalDays']}/{bt['days']} 日有信号，"
                  f"{better['name']} {better['ret']:+.2f}% 胜率 {better['win']*100:.0f}% "
                  f"t日聚类={better['tDaily']}")
        else:
            print(f"[leadLag][WARN] 回测未完成：{bterr}")
        ll["mode"] = "live"
        D.setdefault("duanban", {})["leadLag"] = ll
        print(f"[leadLag] {ll['date']} {ll['time']} | 候选 {len(ll['picks'])} 只"
              f"（强板块 {ll['scanned']['strongSectors']} 个 / 扫描 {ll['scanned']['quoted']} 只）")
        for p in ll["picks"]:
            print(f"  {p['code']} {p['name']} | {p['note']}")
        if not ll["picks"]:
            print(f"  （空仓）{ll.get('gapNote') or ''}")

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
        os.remove(_tmp)
        raise SystemExit("[FAIL] star 写回未通过 node --check，已丢弃、data.js 保持原样：\n"
                         + (_chk.stderr or "")[:800])
    os.replace(_tmp, data_path)
    print("[star] data.js 写回完成，node --check 通过")
    # 台账登记放在写盘成功之后：登记了却没上页面 = 台账里多出一批「用户没见过的票」，
    # 后面算胜率时会把它们算成「规则的表现」，其实一次都没出场。
    if ll:
        try:
            import lead_lag as LL
            n = LL.register(ll)
            print(f"[leadLag] 台账登记 {n} 条（signal_ledger.entries.{ll['date']}.leadLag）")
        except Exception as e:                              # noqa: BLE001
            print(f"[leadLag][WARN] 台账登记失败（不影响页面）：{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()

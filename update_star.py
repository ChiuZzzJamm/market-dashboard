#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🌟 开盘半小时精选（R98k，每日 10:00 自动化调用）。

职责（脚本自包含、AI 只复核不改结构）：
  1. 读 data.js 断板反包双池（07:30 复核后的 confirmed/watching，sentiment/probability 已校准）；
  2. 抓开盘半小时盘面：指数涨跌+成交额（腾讯 gtimg，本机可达）、
     同花顺行业涨跌幅（fetch_ths_industries）、行业主力资金净额（fetch_ths_funds）；
  3. 个股当日实时涨幅+集合竞价高低开批量快照（腾讯 gtimg，一次批量）；
  4. 确定性筛选全部非利空「上涨概率最大」标的写入 duanban.star（不限数量，R98l）：
     利空(excluded) → 评分 = probability + 板块热度加成(开盘涨幅前8行业 +6) + 个股当日涨幅微调；
  5. marketLine / sentiment 写模板句，pushText 留空——由自动化 AI 复核改写为深度分析。

只写 duanban.star（含 generatedAt 不动），其余字段一律不动；东财被 WAF 不影响（同花顺/腾讯源）。
用法：python3 update_star.py [--dry]
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_ashare_sectors as uas  # noqa: E402  （复用 curl/UA/同花顺解析）

NODE = "/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node"
IDX_QT = "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
IDX_NAMES = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指"}


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


def build_star(D):
    db = D.get("duanban") or {}
    pools = []
    for pool in ("confirmed", "watching"):
        pools.extend(db.get(pool) or [])
    if not pools:
        return None, "断板反包双池为空"

    # ---- 盘面：指数 / 行业涨幅 / 资金 ----
    idx = fetch_open_indices()
    try:
        ths = uas.fetch_ths_industries() or []
    except Exception:
        ths = []
    sectors = []
    for x in ths:
        try:
            sectors.append({"name": x.get("name") or "", "pct": round(float(x.get("pct") or 0), 2)})
        except Exception:
            continue
    sectors = [s for s in sectors if s["name"]]
    sectors.sort(key=lambda s: -s["pct"])
    hot_secs = [s["name"] for s in sectors[:8] if s["pct"] > 0]
    try:
        fund_in, fund_out = uas.fetch_ths_funds()
    except Exception:
        fund_in, fund_out = None, None

    # ---- 个股快照（当日实时涨幅） ----
    quotes = fetch_stock_quotes([str(e.get("code")) for e in pools])

    # ---- 确定性筛选：利空剔除，评分 = probability + 板块热度 + 个股涨幅微调 ----
    def _hot(sec):
        for h in hot_secs:
            if h and sec and (h in sec or sec in h):
                return 6.0
        return 0.0

    cand = []
    for e in pools:
        sent = e.get("sentiment") or "neutral"
        if sent == "bear":
            continue  # 利空标的不进精选
        try:
            p = float(e.get("probability") or 50)
        except Exception:
            p = 50.0
        sec = str(e.get("sector") or "")
        q = quotes.get(str(e.get("code"))) or {}
        live = q.get("pct")
        open_pct = q.get("openPct")
        live_adj = max(-3.0, min(3.0, (live or 0) / 2.0))  # 开盘半小时涨幅微调 ±3
        score = round(p + _hot(sec) + live_adj, 1)
        cand.append({"code": str(e.get("code")), "name": e.get("name"),
                     "sector": sec, "sentiment": sent, "probability": round(p, 1),
                     "pctLive": live, "openPct": open_pct, "score": score})
    cand.sort(key=lambda x: (-x["score"], -x["probability"]))
    picks = []
    for c in cand:  # R98l：不限数量，全部非利空候选按评分降序入选
        sent_cn = {"bull": "利好", "neutral": "中性"}.get(c["sentiment"], c["sentiment"])
        note = f"{c['sector']}·{sent_cn}·基线{c['probability']:.0f}%"
        if c.get("openPct") is not None:
            note += f"·竞价{c['openPct']:+.1f}%"
        if c.get("pctLive") is not None:
            note += f"·现涨{c['pctLive']:+.1f}%"
        picks.append({"code": c["code"], "name": c["name"], "sector": c["sector"],
                      "sentiment": c["sentiment"], "probability": c["probability"],
                      "note": note})

    # ---- 文案模板（AI 复核改写 sentiment/pushText） ----
    now = datetime.now()
    market_line = "，".join(
        (f"{x['name']}{x['pct']:+.2f}%（竞价{x['openPct']:+.2f}%）"
         if x.get("openPct") is not None else f"{x['name']}{x['pct']:+.2f}%")
        for x in idx) if idx else "指数快照获取失败"
    if idx:
        total_amt = sum(x.get("amount") or 0 for x in idx)
        market_line += f"，半小时两市成交约 {total_amt:.0f} 亿"
    top3 = "、".join(f"{s['name']}{s['pct']:+.2f}%" for s in sectors[:3]) or "—"
    bot3 = "、".join(f"{s['name']}{s['pct']:+.2f}%" for s in sectors[-3:]) or "—"
    fi_txt = "、".join(f"{x['name']}{x['value']:+.1f}亿" for x in (fund_in or [])) or "—"
    fo_txt = "、".join(f"{x['name']}{x['value']:+.1f}亿" for x in (fund_out or [])) or "—"
    opens = [c["openPct"] for c in cand if c.get("openPct") is not None]
    open_up = sum(1 for o in opens if o > 0)
    open_dn = sum(1 for o in opens if o < 0)
    auction_line = (f"集合竞价：池内候选高开 {open_up} 家 / 低开 {open_dn} 家"
                    if opens else "集合竞价：池内个股竞价快照缺失")
    sentiment = (f"开盘半小时板块情绪：领涨 {top3}；领跌 {bot3}。"
                 f"资金情绪：主力净流入前列 {fi_txt}；净流出前列 {fo_txt}。"
                 f"{auction_line}。"
                 "（AI 复核：请改写为完整开盘情绪判断与对断板反包标的的影响分析）")

    star = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "marketLine": market_line,
        "sentiment": sentiment,
        "picks": picks,
        "pushText": "",
        "generatedBy": "update_star.py 确定性筛选（R98l）+ 自动化 AI 复核",
    }
    return star, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印不写回")
    args = ap.parse_args()
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.js")
    _, D = load_data(data_path)
    star, err = build_star(D)
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
    out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, indent=2) + ";\n"
    with open(data_path, "w", encoding="utf-8") as f:
        f.write(out)
    subprocess.run([NODE, "--check", data_path], check=True, timeout=30)
    print("[star] data.js 写回完成，node --check 通过")


if __name__ == "__main__":
    main()

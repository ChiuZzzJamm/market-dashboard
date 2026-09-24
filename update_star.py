#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🌟 开盘精选（R98k→R99，每日 9:45 自动化调用）。

职责（脚本自包含、AI 只复核不改结构）：
  1. 读 data.js 断板反包双池（07:30 复核后的 confirmed/watching，sentiment/probability 已校准）；
  2. 抓开盘半小时盘面：指数涨跌+成交额（腾讯 gtimg，本机可达）、
     同花顺行业涨跌幅（fetch_ths_industries）、行业主力资金净额（fetch_ths_funds）；
  3. 个股当日实时涨幅批量快照（腾讯 gtimg，一次批量）；
  4. 确定性筛选全部非利空池内标的写入 duanban.star（不限数量）：
     利空(excluded)剔除 → **按上涨概率降序**（R99：概率同则利好优先于中性）；
  4b. R99：不再纳入【早盘强势板块领涨股】等池外候选——开盘精选只放断板反包池内标的；
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
from datetime import datetime
import time

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


def fetch_strong_board_leaders(sectors, fund_in, max_boards=4):
    """早盘强势板块领涨成分股（R98n）：从同花顺行业里挑强势板块，拉其领涨股作板块动量候选。
    强势判定：同花顺行业涨幅居前(>1.2%) 或 主力净流入居前。仅取沪深主板(60/00)领涨股。
    返回 [{code,name,sector,boardPct,changePct}]；任一源失败返回 []（不阻塞主流程）。"""
    if not sectors:
        return []
    strong = [s for s in sorted(sectors, key=lambda x: -x["pct"])[:6] if s["pct"] > 1.2]
    fund_names = {str(f.get("name") or "") for f in (fund_in or [])[:5]}
    for s in sectors:
        if str(s["name"]) in fund_names and s not in strong:
            strong.append(s)
    seen = set(); boards = []
    for s in strong:
        if s["name"] in seen:
            continue
        seen.add(s["name"]); boards.append(s)
    boards = boards[:max_boards]
    leads = []
    for b in boards:
        try:
            tops = uas.fetch_ths_tops(b["code"], 1)  # 领涨成分股 TOP
        except Exception:
            continue
        if not tops:
            continue
        cnt = 0
        for t in tops:
            code = str(t.get("code") or "")
            if not code.startswith(("60", "00")):  # 仅沪深主板，遵循项目标的池约束
                continue
            leads.append({"code": code, "name": t.get("name"), "sector": b["name"],
                          "boardPct": b["pct"], "changePct": t.get("changePct")})
            cnt += 1
            if cnt >= 2:
                break
        time.sleep(0.4)
    return leads


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
            sectors.append({"name": x.get("name") or "", "code": x.get("code") or "",
                            "pct": round(float(x.get("pct") or 0), 2)})
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

    # ---- R99：确定性筛选（仅池内标的）——利空剔除，按上涨概率降序（概率同则利好优先） ----
    sent_rank = {"bull": 0, "neutral": 1, "bear": 2}
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
        cand.append({"code": str(e.get("code")), "name": e.get("name"),
                     "sector": sec, "sentiment": sent, "probability": round(p, 1),
                     "pctLive": live, "openPct": open_pct})

    cand.sort(key=lambda x: (-x["probability"], sent_rank.get(x["sentiment"], 1)))
    picks = []
    for c in cand:  # R99：不限数量，全部非利空池内标的按上涨概率降序入选
        sent_cn = {"bull": "利好", "neutral": "中性"}.get(c["sentiment"], c["sentiment"])
        note = f"{c['sector']}·{sent_cn}·基线{c['probability']:.0f}%"
        if c.get("pctLive") is not None:
            note += f"·现涨{c['pctLive']:+.1f}%"
        picks.append({"code": c["code"], "name": c["name"], "sector": c["sector"],
                      "sentiment": c["sentiment"], "probability": c["probability"],
                      "note": note, "src": "pool"})

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
    # R99：只写开盘后板块情绪与资金情绪，不再含集合竞价
    sentiment = (f"开盘板块情绪：领涨 {top3}；领跌 {bot3}。"
                 f"资金情绪：主力净流入前列 {fi_txt}；净流出前列 {fo_txt}。"
                 "（AI 复核：请改写为完整开盘情绪判断与对断板反包标的的影响分析）")

    star = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "marketLine": market_line,
        "sentiment": sentiment,
        "picks": picks,
        "pushText": "",
        "generatedBy": "update_star.py 确定性筛选（R99：仅池内标的、按上涨概率降序）+ 自动化 AI 复核",
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

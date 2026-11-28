#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""反包质量判定（rebound_quality.py）——把「成功池」拆成主动反包 / 情绪反包。

用户 2026-11-28 的要求（原话）：
  「大盘情绪不好的时候也会出现一种全天基本横盘的（±1~3 个点），但在下午 13:30 或 14:30
   突然一个直线拉升涨停导致反包成功的。所以你不能光看封板时间，还是要看分时量能关系和拉升斜率。」

判据不看「封板几点」，只看三件事：
  ① 拉升斜率 slope（%/小时）：从 +3% 到 +9.5% 的速度，≥8 才算直线
  ② 量能集中度：拉升段每分钟量 / 拉升前每分钟量（burst），或拉升段占全天量比（runShare）
  ③ 拉升前结构：横盘振幅 preRange、砸坑深度 preDip —— 区分「砸盘承接」与「横盘突袭」

按「起拉时间」只做命名，不做门槛：
  早盘砸坑后直线 → 砸盘承接｜早盘横盘后直线 → 横盘直线｜午后/尾盘直线 → 午后突袭
全天慢慢爬、7~9% 长时徘徊、或从未封板 → 情绪反包（弱市里最先失效的一类）。
"""
import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_API = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=%s"

VERT_SLOPE = 8.0      # %/小时：6.5% 在 30 分钟内走完 ≈ 13%/小时；8 是底线
VERT_MIN = 30         # 拉升段最长分钟数
BURST_MIN = 2.5       # 拉升段每分钟量 / 拉升前每分钟量
SHARE_MIN = 35        # 拉升段成交量占全天比例（%）
BAND_MAX = 20         # 7~9% 徘徊分钟数上限


def fetch_minutes(code, tries=3):
    sym = ("sh" if code.startswith("6") else "sz") + code
    for _ in range(tries):
        try:
            req = urllib.request.Request(MIN_API % sym,
                                         headers={"Referer": "https://gu.qq.com/",
                                                  "User-Agent": "Mozilla/5.0"})
            j = json.load(urllib.request.urlopen(req, timeout=12))
            node = j["data"][sym]
            return node["data"]["data"], float(node["qt"][sym][4])
        except Exception:                                        # noqa: BLE001
            time.sleep(0.8)
    return [], None


def metrics(rows, prev):
    pts, last = [], 0.0
    for r in rows:
        t, px, cv, _amt = r.split(" ")
        t, px, cv = int(t), float(px), float(cv)
        pts.append((t, px, cv - last))
        last = cv
    pts = [p for p in pts if 930 <= p[0] <= 1500]
    if not pts or not prev:
        return None
    lim = round(prev * 1.10, 2)
    dayv = sum(p[2] for p in pts) or 1
    seal = next((t for t, px, _ in pts if px >= lim - 0.005), None)
    t3 = next((t for t, px, _ in pts if (px / prev - 1) * 100 >= 3), None)
    t95 = next((t for t, px, _ in pts if (px / prev - 1) * 100 >= 9.5), None)
    win = [p for p in pts if t3 and t95 and t3 <= p[0] <= t95]
    runmin = (t95 - t3) if (t3 and t95 and t95 >= t3) else None
    pre = [p for p in pts if p[0] < (t3 or 1000)]
    run_vol = sum(p[2] for p in win)
    pre_vol = sum(p[2] for p in pre)
    return {
        "gap": round((pts[0][1] / prev - 1) * 100, 2),
        "seal": seal, "start": t3, "runMin": runmin,
        "slope": round(6.5 / (runmin / 60), 2) if runmin else None,
        "burst": round((run_vol / max(1, len(win))) / (pre_vol / max(1, len(pre))), 2) if win and pre_vol else None,
        "runShare": round(run_vol / dayv * 100) if win else None,
        "preRange": round((max(p[1] for p in pre) - min(p[1] for p in pre)) / prev * 100, 2) if pre else None,
        "preDip": round((min(p[1] for p in pre) / prev - 1) * 100, 2) if pre else None,
        "band": sum(1 for t, px, _ in pts if 7 <= (px / prev - 1) * 100 < 9.8),
        "closePct": round((pts[-1][1] / prev - 1) * 100, 2),
        "vol30": round(sum(p[2] for p in pts if p[0] <= 1000) / dayv * 100),
    }


def classify(m):
    """返回 (类型, 主动?, 一句话依据)。时间只用于命名，不参与门槛。"""
    if not m:
        return "无分时", None, "没抓到分钟线"
    if m["gap"] >= 9.5:
        return "一字/秒板", None, f"开盘 {m['gap']:+.1f}%，分时无承接段可学"
    sl, rm = m["slope"], m["runMin"]
    br, rs = m["burst"] or 0, m["runShare"] or 0
    vertical = (rm is not None and rm <= VERT_MIN and (sl or 0) >= VERT_SLOPE)
    ignite = (br >= BURST_MIN) or (rs >= SHARE_MIN)
    if vertical and ignite and m["band"] <= BAND_MAX:
        if (m["start"] or 0) >= 1300:
            kind = "午后突袭"
        elif (m["preDip"] or 0) <= -1.0:
            kind = "砸盘承接"
        else:
            kind = "横盘直线"
        return kind, True, "拉升 %d 分/斜率 %.1f/爆量 %.1fx/段量 %d%%" % (rm, sl or 0, br, rs)
    if m["seal"] is None:
        return "情绪反包·未封板", False, "全天未触板，收 %+.1f%%" % m["closePct"]
    return "情绪反包·拖沓", False, "拉升 %s 分/斜率 %.1f/7-9%% %d 分" % (rm, sl or 0, m["band"])


WASH_MIN_JSON = os.path.join(HERE, "intraday_minute.json")


def wash_behavior(code, rebound_date):
    """洗盘日（反包日前一有数据的交易日）的量价行为推断。

    用户 2026-11-28 ③：反包成功不能只入成功池就完事，尤其**大盘情绪差仍反包**的样本，
    要回头研究前一日洗盘的量价关系，反推主力是「承接」还是「派发」。
    数据来自 intraday_minute.json 的 history（每日 16:00 抓洗盘日分时，按键为交易日）。
    """
    try:
        j = json.load(open(WASH_MIN_JSON, encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return None
    hist = (j.get("history") or {}).get(str(code)) or {}
    days = sorted(d for d in hist if str(d) < str(rebound_date))
    if not days:
        return None
    d = days[-1]
    m = hist[d] or {}
    hold, tail = m.get("hold"), m.get("tailRedPct")
    clp, lift = m.get("closeLowPct"), m.get("tailLift")
    sc = 0
    sc += 2 if hold is True else (-1 if hold is False else 0)
    sc += 2 if (lift or 0) >= 1 else (1 if (lift or 0) >= 0 else -1)
    sc += 2 if (clp or 0) >= 3 else (1 if (clp or 0) >= 1.5 else (-1 if (clp or 0) < 0.8 else 0))
    tag = "承接" if sc >= 3 else ("派发" if sc <= -2 else "中性")
    return {"date": d, "tag": tag, "hold": hold, "tailRedPct": tail,
            "closeLowPct": clp, "tailLift": lift, "burstN": m.get("burstN")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--limit", type=int, default=90)
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    import common
    import subprocess
    if a.write:
        subprocess.run([sys.executable, os.path.join(HERE, "pipeline_lock.py"), "acquire",
                        "--task", "rebound-quality", "--wait", "120"], cwd=HERE)
    try:
        D = common.load_dashboard_data(HERE)
        n = 0
        stat = {}
        for node in ("duanban", "chenPool"):
            for e in (D.get(node) or {}).get("success") or []:
                if not isinstance(e, dict) or (e.get("quality") or {}).get("wash"):
                    continue
                rows, prev = fetch_minutes(str(e["code"]))
                m = metrics(rows, prev) if rows else None
                kind, active, why = classify(m)
                # 卡片上直接可读：形态串尾部挂一枚质量标记（主动=★，情绪=·）
                _mark = (" ★" + kind) if active else (" · " + kind)
                if _mark.strip() not in (e.get("form") or ""):
                    e["form"] = (e.get("form") or "") + _mark
                _w = wash_behavior(str(e["code"]), e.get("successDate") or (D.get("ashare") or {}).get("tradeDate") or "9999-12-31")
                if _w:
                    _mk = "｜洗盘日:" + _w["tag"]
                    if _mk not in (e.get("form") or ""):
                        e["form"] = (e.get("form") or "") + _mk
                e["quality"] = {"type": kind, "active": active, "why": why, "wash": _w,
                                "slope": (m or {}).get("slope"), "burst": (m or {}).get("burst"),
                                "runShare": (m or {}).get("runShare"), "seal": (m or {}).get("seal"),
                                "band": (m or {}).get("band")}
                stat[kind] = stat.get(kind, 0) + 1
                n += 1
                time.sleep(0.2)
                if n >= a.limit:
                    break
        if a.write:
            common.write_data_js(os.path.join(HERE, "data.js"), D, label="[rebound-quality]")
        print("[quality] 判定 %d 只：%s" % (n, json.dumps(stat, ensure_ascii=False)))
    finally:
        if a.write:
            subprocess.run([sys.executable, os.path.join(HERE, "pipeline_lock.py"), "release",
                            "--task", "rebound-quality"], cwd=HERE, check=False)


if __name__ == "__main__":
    main()

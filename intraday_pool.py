#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""池内标的·分钟线承接体检（intraday_pool.py）。

用户 2026-11-26 20:5x：
「不要拿量比看得很重……要多个方面一起判断；加强关注分时线上的爆量异动，
  以及如果尾盘分时线有很多红柱可以加强观察。也有标的全天绿色异动多过红色，
  第二天也反包成功——需要以交易员身份判断卖盘是否有承接、或反包日题材情绪是不是很好。」

库里原来只有 `stkIntraday` 的**日级摘要**（20 个数），没有分钟价量，算不出红柱/绿柱。
本脚本按池内标的抓东财 1 分钟线（约 240 点/只），算出四件事，落 `intraday_minute.json`：

  red / green         分钟涨跌家数（红柱 = 分钟收涨）
  bursts[]            爆量分钟（量 ≥ 当日分钟中位数 × 3），带时间/方向/价格反应
  tailRedPct          14:30–15:00 红柱占比（用户点名「尾盘红柱多 → 加强观察」）
  hold                首个爆量后 15 分钟内是否再创新低（承接是否成立）

只覆盖池内 + 近 30 天反包登记的票（几十只），不对全市场跑。
"""
import argparse
import json
import os
import statistics
import sys
import time
import subprocess
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "intraday_minute.json")
BURST_K = 3.0          # 爆量倍数（相对当日分钟成交量中位数）
EARLY = ("09:30", "10:00")
TAIL = ("14:30", "15:00")


def secid(code):
    return ("1." if code.startswith(("5", "6", "9")) else "0.") + code


def _curl(url, referer):
    return subprocess.run(["curl", "-s", "--max-time", "12",
                           "-H", "Referer: " + referer,
                           "-H", "User-Agent: Mozilla/5.0", url],
                          capture_output=True, text=True).stdout


def _tx_prefix(code):
    return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code


def fetch_minutes(code, retries=2):
    """腾讯分钟线为主（cum 量 → 差分），东财为备。两家都限流：失败就退避，不硬打。"""
    for attempt in range(retries):
        # ① 腾讯：data.data.data = ["0930 price cumVol cumAmt", ...]
        try:
            txt = _curl("https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=%s"
                        % _tx_prefix(code), "https://gu.qq.com/")
            j = json.loads(txt)
            node = ((j.get("data") or {}).get(_tx_prefix(code)) or {}).get("data") or {}
            raw = node.get("data") or []
            rows, prev = [], 0.0
            for line in raw:
                p = line.split()
                if len(p) < 3:
                    continue
                hhmm, price = p[0], float(p[1])
                cum = float(p[2])
                v = max(0.0, cum - prev)
                prev = cum
                rows.append({"t": hhmm[:2] + ":" + hhmm[2:], "c": price, "v": v})
            if len(rows) >= 60:
                return rows
        except Exception:                                       # noqa: BLE001
            pass
        # ② 东财兜底
        try:
            txt = _curl("https://push2his.eastmoney.com/api/qt/stock/trends2/get"
                        "?secid=%s&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56,f57"
                        "&ndays=1&iscr=0" % secid(code), "https://quote.eastmoney.com/")
            j = json.loads(txt)
            rows = []
            for t in ((j.get("data") or {}).get("trends") or []):
                p = t.split(",")
                if len(p) >= 7:
                    rows.append({"t": p[0][-5:], "c": float(p[2]), "v": float(p[5])})
            if len(rows) >= 60:
                return rows
        except Exception:                                       # noqa: BLE001
            pass
        time.sleep(2.0 * (attempt + 1))
    return []


def analyse(rows):
    # 腾讯会给到 15:00 之后（15:00–15:30 收盘竞价区，量=0、价不动）——不裁掉会把
    # 「尾盘红柱占比」算成 0%、「尾盘量占比」算成 1%（2026-11-26 实测）。
    rows = [r for r in rows if "09:30" <= r["t"] <= "15:00"]
    if len(rows) < 60:
        return None
    # 基线取 09:40–14:30：开盘 5 分钟与尾盘 30 分钟的量天然偏大，
    # 放进基线会把它们自己标成「爆量」（实测虚高到 35 次）。
    base = [r["v"] for r in rows if "09:40" <= r["t"] <= "14:30" and r["v"] > 0]
    vols = base or [r["v"] for r in rows if r["v"] > 0]
    if not vols:
        return None
    pos = sorted(v for v in vols if v > 0)
    # 基线只用于日志/调试；爆量判定改看「全天最大 5 分钟且 ≥ 日均 2 倍」（见下）。
    _ = statistics.median(pos) if pos else 0
    red = green = flat = 0
    for i in range(1, len(rows)):
        if rows[i]["v"] <= 0:      # 无成交分钟不算红绿（价格只是没动）
            continue
        d = rows[i]["c"] - rows[i - 1]["c"]
        if d > 0:
            red += 1
        elif d < 0:
            green += 1
        else:
            flat += 1
    # 「爆量异动」取全天成交最大的 5 分钟（且 ≥ 当日均量 2 倍），而不是「超过某阈值就算」——
    # 后者在封板股身上会产出 40+ 个「爆量」（封板后量近乎为 0，基线被拉平），没有解释力。
    day_mean = statistics.mean([r["v"] for r in rows if r["v"] > 0]) if rows else 0
    top = sorted([r for r in rows if r["v"] > 0], key=lambda r: r["v"], reverse=True)[:5]
    bursts = []
    for r in sorted(top, key=lambda r: r["t"]):
        if day_mean and r["v"] < day_mean * 2:
            continue
        k = rows.index(r)
        nxt = rows[k + 1]["c"] - r["c"] if k + 1 < len(rows) else 0
        bursts.append({"t": r["t"], "x": round(r["v"] / max(1e-9, day_mean), 1),
                       "dir": "红" if nxt > 0 else ("绿" if nxt < 0 else "平")})
    # 尾盘红柱占比
    tail = [r for r in rows if TAIL[0] <= r["t"] <= TAIL[1] and r["v"] > 0]
    tail_red = sum(1 for i in range(1, len(tail)) if tail[i]["c"] > tail[i - 1]["c"])
    tail_green = sum(1 for i in range(1, len(tail)) if tail[i]["c"] < tail[i - 1]["c"])
    tail_pct = round(tail_red / max(1, tail_red + tail_green) * 100)
    tail_vol = round(sum(r["v"] for r in tail) / max(1e-9, sum(r["v"] for r in rows)) * 100)
    # 承接：首个爆量后 15 分钟是否再创新低
    hold = None
    if bursts:
        i0 = next((i for i, r in enumerate(rows) if r["t"] == bursts[0]["t"]), 0)
        base = min(r["c"] for r in rows[:i0 + 1])
        win = rows[i0 + 1:i0 + 16]
        hold = bool(win) and min(r["c"] for r in win) >= base * 0.995
    limit_up = bool(tail) and len({round(r["c"], 2) for r in tail}) == 1 and tail[-1]["c"] >= max(r["c"] for r in rows)
    early = [b for b in bursts if EARLY[0] <= b["t"] <= EARLY[1]]
    return {
        "date": rows[0]["t"] and datetime.now().strftime("%Y-%m-%d"),
        "n": len(rows), "red": red, "green": green, "flat": flat,
        "burstN": len(bursts), "bursts": bursts[:6],
        "earlyBurst": len(early),
        "tailRedPct": (None if limit_up else tail_pct),
        "tailVolPct": tail_vol, "limitUp": limit_up,
        "hold": hold,
        "verdict": ("红多且尾盘强" if red > green and tail_pct >= 55 else
                    "红绿相当" if abs(red - green) <= 0.2 * max(1, red + green) else
                    "绿多" if green > red else "红多"),
    }


def collect_codes(D):
    codes, names = [], {}
    db = D.get("duanban") or {}
    cp = D.get("chenPool") or {}
    for key in ("confirmed", "watching"):
        for e in db.get(key) or []:
            c = str(e.get("code") or "")
            if c:
                codes.append(c)
                names[c] = e.get("name") or ""
    for key in ("strict", "loose"):
        for e in cp.get(key) or []:
            c = str(e.get("code") or "")
            if c:
                codes.append(c)
                names[c] = e.get("name") or ""
    try:
        log = json.load(open(os.path.join(HERE, "rebound_log.json"), encoding="utf-8"))
        for it in log.get("items") or []:
            c = str(it.get("code") or "")
            if c:
                codes.append(c)
                names.setdefault(c, it.get("name") or "")
    except Exception:                                            # noqa: BLE001
        pass
    return list(dict.fromkeys(codes)), names


def main():
    ap = argparse.ArgumentParser(description="池内标的分时承接体检")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--limit", type=int, default=120)
    a = ap.parse_args()
    if not a.write:
        print(__doc__)
        return 0
    D = json.loads(open(os.path.join(HERE, "data.js"), encoding="utf-8").read()
                   .split("=", 1)[1].rstrip().rstrip(";"))
    codes, names = collect_codes(D)
    codes = codes[:a.limit]
    out = {"asOf": datetime.now().strftime("%Y-%m-%d %H:%M"), "stocks": {}, "miss": []}
    for i, c in enumerate(codes, 1):
        rows = fetch_minutes(c)
        r = analyse(rows)
        if not r:
            out["miss"].append(c)
            continue
        r["name"] = names.get(c, "")
        out["stocks"][c] = r
        if i % 20 == 0:
            print("  ... %d/%d" % (i, len(codes)))
        time.sleep(0.6)           # 两家都限流：0.6s/只，120 只约 75 秒
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("[write] 分时承接 %d 只（缺 %d）→ intraday_minute.json"
          % (len(out["stocks"]), len(out["miss"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

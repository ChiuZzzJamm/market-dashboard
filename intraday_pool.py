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
import statistics
import json
import os
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
    # 爆量判定看「全天最大 5 分钟且 ≥ 日均 2 倍」（见下），不再算基线中位数
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
    # 2026-11-26 21:xx 用户：「也有标的全天绿多红少，第二天照样反包成功，不要一杆子打死」。
    # 红绿数量只作辅助；真正看三件事：收盘离最低多远（托住没有）、尾盘 30 分钟拉起还是砸下、
    # 最后一次爆量谁在进场。三者合起来才叫承接。
    day_low = min(r["c"] for r in rows)
    close_low_pct = round((rows[-1]["c"] / day_low - 1) * 100, 2) if day_low else None
    tail_lift = (round((tail[-1]["c"] / tail[0]["c"] - 1) * 100, 2) if len(tail) >= 2 else None)
    _vn = sum(r["c"] * r["v"] for r in rows); _vd = sum(r["v"] for r in rows)
    vwap = round(_vn / _vd, 3) if _vd else None
    above_vwap = (rows[-1]["c"] >= vwap) if vwap else None
    # 2026-11-28 用户：不要只写「收盘站上均价线」——要说明**盘中**站上没站上、
    # 破了几次、跌破多久才收回、收盘没站上但盘中站上过、还是全天被压制只触碰过几次。
    # 这里按「运行均价线」（每分钟累计成交额/累计量，与行情软件一致）逐分钟判位置。
    run_v, amt, vol = [], 0.0, 0.0
    for r in rows:
        amt += r["c"] * r["v"]; vol += r["v"]
        run_v.append(amt / vol if vol else r["c"])
    above = [rows[i]["c"] >= run_v[i] for i in range(len(rows))]
    above_pct = round(sum(1 for x in above if x) / len(above) * 100)
    # 跌破区间（由上→下，到回线为止）
    segs, inb, st = [], False, 0
    for i, a in enumerate(above):
        if not a and not inb:
            inb, st = True, i
        elif a and inb:
            inb = False; segs.append((st, i))
    if inb:
        segs.append((st, len(above)))
    break_n = len(segs)
    below_max = max((b - a for a, b in segs), default=0)
    first_rec = (segs[0][1] - segs[0][0]) if segs else None
    # 触碰：在下方但距线 ≤0.3%（含刚突破一点又被打回），按连续段计数
    touch_n, prev_t = 0, False
    for i in range(1, len(rows)):
        if not above[i]:
            gap = (run_v[i] - rows[i]["c"]) / max(1e-9, run_v[i]) * 100
            t = gap <= 0.3
            if t and not prev_t:
                touch_n += 1
            prev_t = t
        else:
            prev_t = False
    last_burst_dir = bursts[-1]["dir"] if bursts else None
    early = [b for b in bursts if EARLY[0] <= b["t"] <= EARLY[1]]
    return {
        "date": rows[0]["t"] and datetime.now().strftime("%Y-%m-%d"),
        "n": len(rows), "red": red, "green": green, "flat": flat,
        "burstN": len(bursts), "bursts": bursts[:6],
        "earlyBurst": len(early),
        "tailRedPct": (None if limit_up else tail_pct),
        "tailVolPct": tail_vol, "limitUp": limit_up,
        "closeLowPct": close_low_pct, "tailLift": tail_lift,
        "vwap": vwap, "aboveVwap": above_vwap, "lastBurstDir": last_burst_dir,
        "abovePct": above_pct, "breakN": break_n, "belowMaxMin": below_max,
        "firstRecoverMin": first_rec, "touchN": touch_n,
        "endAbove": bool(above[-1]), "wasAbove": any(above),
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
    for key in ("strict", "loose", "watching"):
        for e in cp.get(key) or []:
            c = str(e.get("code") or "")
            if c:
                codes.append(c)
                names[c] = e.get("name") or ""
    # 2026-11-28 用户「很多标的都没分时」真正的根因：成功池的票当天已从确认/观察池移出，
    # 这里从来没采过它们 → 弹窗「量能解读」只能显示「无分时数据」。成功池也要抓（判定主动/情绪要用）。
    for key in ("success",):
        for e in db.get(key) or []:
            c = str(e.get("code") or "")
            if c:
                codes.append(c)
                names[c] = e.get("name") or ""
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
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--if-fresh", action="store_true", help="当日已抓够就跳过，防止低上限的短跑覆盖长跑成果")
    ap.add_argument("--fill-miss", action="store_true", help="只补上次 miss 的少数标的（被反爬挡掉的），不重抓全部")
    a = ap.parse_args()
    if not a.write:
        print(__doc__)
        return 0
    D = json.loads(open(os.path.join(HERE, "data.js"), encoding="utf-8").read()
                   .split("=", 1)[1].rstrip().rstrip(";"))
    codes, names = collect_codes(D)
    codes = codes[:a.limit]
    # 2026-11-28 用户「很多标的都没分时」根因：deploy 链用 --limit 120 覆盖了 16:00 抓到的 260 份，
    # 池子却有两百多只。① 上限提到 300；② 当日已抓够（≥80% 池内标的）就不重抓，避免覆盖。
    try:
        _old = json.load(open(OUT, encoding="utf-8"))
        _od = (_old.get("asOf") or "")[:10]
        _oc = set((_old.get("stocks") or {}).keys())
        if a.if_fresh and _od == datetime.now().strftime("%Y-%m-%d") and len(_oc & set(codes)) >= int(len(codes) * 0.8):
            print("[skip] 当日分时已覆盖 %d/%d，跳过重抓（--if-fresh）" % (len(_oc & set(codes)), len(codes)))
            return 0
    except Exception:
        pass
    today = datetime.now().strftime("%Y-%m-%d")
    if a.fill_miss:
        # 反爬场景：168/188 已拿到，只剩少数被挡。这里只补 miss，每只间隔 2.5s，
        # 不去碰已抓到的（避免又一次把文件整体重写、也避免继续加剧限流）。
        cur = json.load(open(OUT, encoding="utf-8"))
        miss = list(cur.get("miss") or [])
        if not miss:
            print("[fill] 没有待补项")
            return 0
        got = 0
        for c in miss[:]:
            r = analyse(fetch_minutes(c, retries=2))
            if not r:
                time.sleep(1.0)
                continue
            r["name"] = names.get(c, "")
            r["date"] = today
            cur["stocks"][c] = r
            cur.setdefault("history", {}).setdefault(c, {})[today] = {k: v for k, v in r.items() if k != "name"}
            cur["miss"].remove(c)
            got += 1
            time.sleep(2.5)
        json.dump(cur, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
        print("[fill] 补到 %d 只，仍缺 %d 只" % (got, len(cur.get("miss") or [])))
        return 0
    try:
        old = json.load(open(OUT, encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        old = {}
    hist = old.get("history") or {}
    out = {"asOf": datetime.now().strftime("%Y-%m-%d %H:%M"), "stocks": {}, "miss": [],
           "history": hist}
    def _store(c, r):
        r["name"] = names.get(c, "")
        r["date"] = today
        out["stocks"][c] = r
        # 按日归档：明天再跑一次，这只票就有了「今天」这一条，
        # 后天复盘时「洗盘日 T-1」就是它（用户 2026-11-26：要看的是洗盘那天）。
        hist.setdefault(c, {})[today] = {k: v for k, v in r.items() if k != "name"}
        if len(hist[c]) > 10:
            for d in sorted(hist[c])[:-10]:
                hist[c].pop(d, None)

    for i, c in enumerate(codes, 1):
        r = analyse(fetch_minutes(c))
        if not r:
            out["miss"].append(c)
            continue
        _store(c, r)
        if i % 20 == 0:
            print("  ... %d/%d" % (i, len(codes)))
        time.sleep(0.6)           # 两家都限流：0.6s/只，120 只约 75 秒
    # 2026-11-28：一次跑 188 只必被腾讯限流（实测缺 50 只，冷却后再抓就正常）。
    # 冷却 25s 对缺失名单补抓一轮，仍失败才记 miss。
    if out["miss"]:
        print("[retry] 第一轮缺 %d 只，冷却 25s 后补抓" % len(out["miss"]))
        time.sleep(25)
        still = []
        for c in list(out["miss"]):
            r = analyse(fetch_minutes(c, retries=3))
            if not r:
                still.append(c)
                time.sleep(1.0)
                continue
            _store(c, r)
            time.sleep(0.8)
        out["miss"] = still

    for c, days in hist.items():
        ds = sorted(days)
        prev = [d for d in ds if d != today]
        if prev and c in out["stocks"]:
            out["stocks"][c]["prevDate"] = prev[-1]
            out["stocks"][c]["prev"] = days[prev[-1]]
    # 失败保护：抓取被限流时绝不能用空结果覆盖上一次的成果（2026-11-26 实测：
    # 腾讯连抓 120 只后限流，一次失败就把 108 条记录写成 0 条）。失败的多就整体放弃写盘。
    got = len(out["stocks"])
    if codes and got < max(3, int(len(codes) * 0.3)):
        print("[abort] 只抓到 %d/%d 只（疑似限流），保留上一次的 %s 不覆盖"
              % (got, len(codes), OUT))
        return 2
    # 抓失败的票沿用旧值，并打 stale 标记，不静默丢
    for c in out["miss"]:
        if c in (old.get("stocks") or {}):
            out["stocks"][c] = dict(old["stocks"][c], stale=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("[write] 分时承接 %d 只（缺 %d）→ intraday_minute.json"
          % (len(out["stocks"]), len(out["miss"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

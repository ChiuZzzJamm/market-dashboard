#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""横盘启动「开始启动」预警（R100z320，用户 2026-11-09）。

用户原话：「横盘启动我认为你需要观察，若哪天开始启动了（参考之前给你的例图走势和成交额），你就要提醒我」

## 为什么不能直接用池子自带的「启动」定义

`breakout_pool.py` 的规则是：**收盘较首板 +30% 即算启动、移出本池**。
那是「已经涨完、别再跟了」的**退场**判据，不是「刚开始动、快看」的**进场**判据。

实测京城股份（池内第 1 名）：现价距涨停收盘 +3.2%，启动线在 +30%——
从 3.2% 到 30% 中间还有 26 个点。等它跨过 +30% 再提醒，等于在鱼尾才喊人。

所以这里另立一套**早期信号**（不动 breakout_pool 的既有口径）：

    放量 + 上攻 + 站上洗盘区上沿，三个同时成立才算「开始启动」

  · 放量：当日成交量 / 洗盘期均量 ≥ BREW_VOL（默认 1.5）
  · 上攻：当日涨幅 ≥ BREW_PCT（默认 3.0%）
  · 站上：收盘 > 洗盘期最高收盘（不是最高价——上影线不算站上）

三者缺一不算：只有量没有价是试盘，只有价没有量是缩量反抽，都容易一日游。

## 输出

`data.js` → `breakoutPool.watch`（新增子节点，不动 passed/failPool）：

    {
      "tradeDate": "2026-11-09",
      "asOf": "...",
      "params": {"vol": 1.5, "pct": 3.0, "startLine": 0.30},
      "triggered": [ {code,name,sector,todayPct,todayVolRatio,vsBoxHigh,posPct,why} ],
      "near":      [ ... 只差一档的 ... ],
      "scanned": N
    }

`triggered` 非空时 `push_notify.py` 会推一条；空则静默（不推「今天没有」）。
"""
import argparse
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common  # noqa: E402

BREW_VOL = 1.5      # 当日量 / 洗盘均量
BREW_PCT = 3.0      # 当日涨幅 %
START_LINE = 0.30   # 池子自带的「启动」线（+30%），只用于判断「已经启动、别再跟」
NEAR_VOL = 1.2      # 「接近」档：量比够但涨幅不够，或反之
NEAR_PCT = 1.5


def _kl_map(D):
    """代码 → K 线（升序 [{day,open,close,high,low,volume}]）。"""
    out = {}
    raw = (D.get("stkKlines") or {})
    for code, arr in raw.items():
        rows = []
        for r in (arr or []):
            if isinstance(r, (list, tuple)) and len(r) >= 6:
                rows.append({"day": str(r[0]), "open": float(r[1] or 0), "close": float(r[2] or 0),
                             "high": float(r[3] or 0), "low": float(r[4] or 0),
                             "volume": float(r[5] or 0)})
        if rows:
            out[str(code)] = rows
    return out


def judge(item, kl):
    """单只票 → (档位, 明细)。档位取 'started' / 'brewing' / 'near' / ''。"""
    if not kl or len(kl) < 6:
        return "", {}
    last = kl[-1]
    prev = kl[-2]
    limit_day = str(item.get("limitDay") or "")
    idx = next((n for n, k in enumerate(kl) if k["day"] == limit_day), None)
    if idx is None or idx >= len(kl) - 1:
        return "", {}
    # 洗盘期 = 涨停日之后到前一交易日（不含今日，否则当日的量会污染基准）
    wash = kl[idx + 1:-1]
    if len(wash) < 3:
        return "", {}
    vol_avg = sum(k["volume"] for k in wash) / len(wash)
    if vol_avg <= 0:
        return "", {}
    today_pct = (last["close"] / prev["close"] - 1) * 100 if prev["close"] else 0.0
    vol_ratio = last["volume"] / vol_avg
    box_high = max(k["close"] for k in wash)
    lc = float(item.get("limitClose") or 0)
    pos_pct = (last["close"] / lc - 1) * 100 if lc > 0 else None
    detail = {
        "todayPct": round(today_pct, 2),
        "todayVolRatio": round(vol_ratio, 2),
        "vsBoxHigh": round((last["close"] / box_high - 1) * 100, 2) if box_high > 0 else None,
        "posPct": (round(pos_pct, 2) if pos_pct is not None else None),
        "washDays": len(wash),
    }
    if pos_pct is not None and pos_pct >= START_LINE * 100:
        return "started", detail          # 已过池子启动线：不是「快看」，是「已走完」
    up = today_pct >= BREW_PCT
    vol = vol_ratio >= BREW_VOL
    over = last["close"] > box_high
    # ⭐ 涨停单独放行，不看量比：涨停是当日最强的启动信号，缩量涨停（一字/加速板）
    # 反而是筹码更锁的表现。实测 2026-11-09 的雪人集团/信通电子/锋龙股份都是
    # +10% 涨停、量比只有 1.14~1.47，若仍要求「量比≥1.5」会被挡在「接近」档——
    # 那是判据的洞，不是它们不够强。
    if today_pct >= 9.8:
        detail["why"] = "涨停（不计量比，一字/加速板缩量更锁）"
        return "brewing", detail
    if up and vol and over:
        detail["why"] = "放量上攻并站上洗盘区上沿"
        return "brewing", detail
    if (up and vol) or (vol and over) or (up and over):
        detail["why"] = ("放量+上攻但未站上箱体" if (up and vol) else
                         ("放量+站上箱体但涨幅不足" if (vol and over) else "上攻+站上箱体但没放量"))
        if (today_pct >= NEAR_PCT or vol_ratio >= NEAR_VOL) and over is not False:
            return "near", detail
    return "", detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印不写盘")
    a = ap.parse_args()

    D = common.load_dashboard_data(HERE)
    if not D:
        print("[ERR] 读不到 data.js")
        return 1
    bp = D.get("breakoutPool") or {}
    passed = [x for x in (bp.get("passed") or []) if isinstance(x, dict) and x.get("code")]
    if not passed:
        print("[SKIP] breakoutPool.passed 为空")
        return 0
    kls = _kl_map(D)
    secmap = D.get("sectorMap") or {}
    triggered, near, started = [], [], []
    unscanned = 0
    for it in passed:
        code = str(it["code"])
        grade, det = judge(it, kls.get(code))
        # R100z323：池子里 30 只有 2 只没带 sector（002931/603081），而 sectorMap 里有——
        # 卡片上的板块 pill 靠这个字段，空着就是「有结论没写」。按 sectorMap 兜底，
        # 兜不到才留空（前端会整块不渲染 pill，不会出现空标签）。
        row = {"code": code, "name": it.get("name"),
               "sector": it.get("sector") or secmap.get(code) or "",
               "score": it.get("score")}
        row.update(det)
        if grade == "brewing":
            triggered.append(row)
        elif grade == "near":
            near.append(row)
        elif grade == "started":
            started.append(row)
        if not det:
            unscanned += 1
    triggered.sort(key=lambda x: -(x.get("todayVolRatio") or 0))
    near.sort(key=lambda x: -(x.get("todayVolRatio") or 0))

    out = {
        "tradeDate": (D.get("ashare") or {}).get("tradeDate") or bp.get("tradeDate"),
        # R100z323：原来写 `common.now_str() if hasattr(common,"now_str") else ""`，
        # 而 common 里根本没有 now_str → asOf 恒为空串，页面上的「取数时刻」静默消失
        # （G9 每天报一条 breakoutPool.watch.asOf=空串）。改为本地时间戳，不再依赖不存在的函数。
        "asOf": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "params": {"vol": BREW_VOL, "pct": BREW_PCT, "startLine": START_LINE},
        "triggered": triggered[:8],
        "near": near[:8],
        "started": started[:8],
        "scanned": len(passed) - unscanned,
        "note": ("判据：当日量/洗盘均量 ≥ %.1f 且 涨幅 ≥ %.0f%% 且 收盘站上洗盘期最高收盘；"
                 "「已启动」= 距涨停收盘 ≥ +%.0f%%（池子的移出线，不是买入信号）"
                 % (BREW_VOL, BREW_PCT, START_LINE * 100)),
    }
    print("[横盘启动监控] 扫描 %d 只 → 启动 %d · 接近 %d · 已过启动线 %d"
          % (out["scanned"], len(triggered), len(near), len(started)))
    for r in triggered:
        print("  🔴 %-6s %-6s 今日%+.2f%% 量比%.2f 站上箱体%+.2f%%"
              % (r["name"], r["code"], r["todayPct"], r["todayVolRatio"], r.get("vsBoxHigh") or 0))
    for r in near:
        print("  🟡 %-6s %-6s 今日%+.2f%% 量比%.2f（%s）"
              % (r["name"], r["code"], r["todayPct"], r["todayVolRatio"], r.get("why") or ""))

    if a.dry:
        print("[DRY] 未写盘")
        return 0
    D.setdefault("breakoutPool", {})["watch"] = out
    common.write_data_js(HERE, D, label="[breakout-watch]")
    print("[OK] 已写入 breakoutPool.watch")
    return 0


if __name__ == "__main__":
    sys.exit(main())

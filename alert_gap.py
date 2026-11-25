#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""距异动点数（alert_gap.py）。

用户 2026-11-25：「连板天梯标的这边写下距离异动还有多少个点」。

按交易所口径算**涨幅偏离值**（不是裸涨幅）：
  · 异常波动：连续 3 个交易日收盘价涨幅偏离值累计达 ±20%（主板；ST 为 15%）
  · 严重异常波动：连续 10 个交易日累计偏离达 100%
偏离值 = 个股区间涨幅 − 对应指数区间涨幅（沪市 60 开头 → 上证指数；深市 00/002 → 深证成指）。

输出 `duanban.alertGap[code] = {dev3, gap3, dev10, gap10, stage, text}`：
  stage=normal → 「距异动 2.3 点」（3 日那档还没触发）
  stage=alerted → 「异动中 · 距严重 57.9 点」（3 日已过，看 10 日 100% 那档）

用法：python3 alert_gap.py [--write] [--dry]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common                                                    # noqa: E402

INDEX_SS = "sh000001"      # 上证指数
INDEX_SZ = "sz399001"      # 深证成指
TH_3D = 20.0               # 3 日偏离 20%
TH_10D = 100.0             # 10 日偏离 100%
N_3D, N_10D = 3, 10


def _closes(bars):
    out = {}
    for b in bars or []:
        try:
            out[str(b[0])] = float(b[2])
        except (TypeError, ValueError, IndexError):
            continue
    return out


def _span_pct(closes, n):
    """最近 n 个交易日收盘涨幅（%）。返回 (pct, end_date) 或 (None, None)。"""
    dates = sorted(closes)
    if len(dates) < n + 1:
        return None, None
    a, b = dates[-1 - n], dates[-1]
    c0, c1 = closes[a], closes[b]
    if not c0:
        return None, None
    return (c1 / c0 - 1.0) * 100.0, b


def idx_code_for(code):
    return INDEX_SS if str(code).startswith("6") else INDEX_SZ


def compute(D):
    kl = D.get("stkKlines") or {}
    done, skipped = {}, []
    # 只算连板（lbc>=2）与断板池里的票：天梯上出现的那些
    codes = []
    # 天梯（连板梯队）的数据源是 ashare.lianban；断板池是另一批，一并算
    for e in ((D.get("ashare") or {}).get("lianban") or []):
        if isinstance(e, dict) and e.get("code"):
            codes.append(str(e["code"]))
    for key in ("confirmed", "watching"):
        for e in ((D.get("duanban") or {}).get(key) or []):
            if isinstance(e, dict) and e.get("code"):
                codes.append(str(e["code"]))
    for c in sorted(set(codes)):
        bars = kl.get(c)
        if not bars:
            skipped.append(c)
            continue
        sc = _closes(bars)
        ixc = _closes(kl.get(idx_code_for(c)) or [])
        d3s, end = _span_pct(sc, N_3D)
        d3i, _ = _span_pct(ixc, N_3D)
        d10s, _ = _span_pct(sc, N_10D)
        d10i, _ = _span_pct(ixc, N_10D)
        if d3s is None or d3i is None:
            skipped.append(c)
            continue
        dev3 = round(d3s - d3i, 2)
        dev10 = round(d10s - d10i, 2) if (d10s is not None and d10i is not None) else None
        if abs(dev3) < TH_3D:
            stage = "normal"
            gap = round(TH_3D - abs(dev3), 2)
            text = "距异动 %.1f 点" % gap
        else:
            stage = "alerted"
            if dev10 is None:
                gap = None
                text = "已异动"
            else:
                gap = round(TH_10D - abs(dev10), 2)
                text = ("异动中 · 距严重 %.1f 点" % gap) if gap > 0 else "严重异动"
        done[c] = {"dev3": dev3, "gap3": round(TH_3D - abs(dev3), 2),
                   "dev10": dev10, "gap10": (round(TH_10D - abs(dev10), 2)
                                             if dev10 is not None else None),
                   "stage": stage, "gap": gap, "text": text,
                   "asOf": end, "basis": "3日偏离%.0f%%/10日偏离%.0f%%，个股−指数" % (TH_3D, TH_10D)}
    return done, skipped


def main():
    ap = argparse.ArgumentParser(description="连板标的距异动点数")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    D = common.load_dashboard_data(HERE)
    done, skipped = compute(D)
    print("[alert] 算出 %d 只（跳过 %d 只缺 K 线）" % (len(done), len(skipped)))
    for c, v in list(done.items())[:6]:
        print("  %s 3日偏离 %+.1f%% → %s" % (c, v["dev3"], v["text"]))
    if not a.write:
        print("[dry] 未写回")
        return 0
    import subprocess
    rc = subprocess.run([sys.executable, os.path.join(HERE, "pipeline_lock.py"),
                         "acquire", "--task", "alert-gap", "--wait", "90"],
                        capture_output=True, text=True)
    locked = ("[LOCK][OK]" in rc.stdout) or ("[STALE-STOLEN]" in rc.stdout)
    if not locked:
        print("[FAIL] 拿不到 data 锁：%s" % rc.stdout.strip()[:80])
        return 3
    try:
        D = common.load_dashboard_data(HERE)
        done, _ = compute(D)
        D.setdefault("duanban", {})["alertGap"] = done
        common.write_data_js(os.path.join(HERE, "data.js"), D, label="[alert-gap]")
        print("[alert] 已写入 duanban.alertGap（%d 只）" % len(done))
    finally:
        subprocess.run([sys.executable, os.path.join(HERE, "pipeline_lock.py"),
                        "release", "--task", "alert-gap"], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

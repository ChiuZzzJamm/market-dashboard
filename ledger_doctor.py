#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""台账诊断与优化闭环（ledger_doctor.py）。

解决的问题（用户 2026-11-25）：「台账只展示胜率低，缺少自主优化能力」。
本脚本把「统计」变成「诊断 → 动作 → 记录 → 验证」四步闭环：

  1) 诊断：按模块（AI 预测 / 开盘精选 / 各技术池 / 陈学长池）算 5 日胜率、超额、t 值，
     对照 `ledger_policy.json` 的 warn/fail 阈值分级。
  2) 切片：把每个模块按「板块 / 当日涨幅档 / 星期 / 大盘方向 / R 档 / 持有天数」切开，
     找**显著更差**与**显著更好**的切片——这是「为什么低」的答案，不是再报一遍胜率。
     显著性用 Wilson 下界 + 样本量 + 覆盖交易日数三重护栏，防多重比较挖噪声。
  3) 动作：每个显著切片生成一条可执行动作（剔除 / 优先 / 收紧参数），带证据与预期效果；
     动作写进 `ledger_actions.json`（持久、可回滚、去重）。
  4) 验证：已 applied 的动作到期后，用「动作生效之后」的新样本重算该切片胜率，
     写回 before/after；明显变差 → rolledBack。权重类动作通过 `ledger_weights.json`
     混入 `signal_attribution.suggest_weights`（阻尼 0.5、单次位移封顶），下一轮可测。

用法：
    python3 ledger_doctor.py            # 只诊断，打印结论（不写盘）
    python3 ledger_doctor.py --write    # 写 data.js 的 ledgerDiag + ledger_actions.json
"""
import argparse
import hashlib
import json
import os
import statistics
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common                                                     # noqa: E402

POLICY = os.path.join(HERE, "ledger_policy.json")
ACTIONS = os.path.join(HERE, "ledger_actions.json")
WEIGHTS = os.path.join(HERE, "ledger_weights.json")

LABEL = {
    "ai": "AI 预测", "harmonic": "谐波形态", "duanban_confirmed": "断板·确认池",
    "duanban_watching": "断板·观察池", "breakoutPool": "横盘启动", "chen_strict": "陈学长·严格池",
    "chen_loose": "陈学长·放宽池", "chen_watching": "陈学长·观察池",
}


# ---------------------------------------------------------------- 基础统计
def wilson(k, n, z=1.96):
    """胜率的 Wilson 置信区间下界/上界。小样本直接看比例会被 3 胜 2 负骗到。"""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, (c - r) / d), min(1.0, (c + r) / d)


def bucket_day_pct(x):
    if x is None:
        return "未知"
    if x < -0.03:
        return "当日跌超3%"
    if x < 0:
        return "当日小跌"
    if x < 0.03:
        return "当日小涨"
    return "当日涨超3%"


def bucket_R(r):
    if r is None:
        return "未知"
    if r <= 0.05:
        return "R≤5%"
    if r <= 0.08:
        return "R5-8%"
    return "R>8%"


def bucket_gap(d):
    if d is None:
        return "未知"
    if d <= 1:
        return "隔日进场"
    if d <= 2:
        return "隔2日"
    return "隔3日以上"


# ---------------------------------------------------------------- 装载
def load_inputs():
    D = common.load_dashboard_data(HERE)
    try:
        led = json.load(open(os.path.join(HERE, "signal_ledger.json"), encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        led = {}
    try:
        chen = json.load(open(os.path.join(HERE, "chen_ledger.json"), encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        chen = {}
    try:
        pol = json.load(open(POLICY, encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        pol = {}
    return D, (led.get("entries") or {}), (chen.get("entries") or []), pol


def bars_index(kl):
    """code → {date: idx}，只建一次，几十万次查找靠它。"""
    idx = {}
    for code, bars in (kl or {}).items():
        idx[code] = {b[0]: i for i, b in enumerate(bars)}
    return idx


def fwd_ret(kl, idx, code, date, h):
    bars = (kl or {}).get(code) or []
    i = (idx.get(code) or {}).get(date)
    if i is None or i + h >= len(bars):
        return None
    try:
        c0, c1 = float(bars[i][2]), float(bars[i + h][2])
    except (TypeError, ValueError, IndexError):
        return None
    return (c1 / c0 - 1.0) if c0 else None


def day_pct(kl, idx, code, date):
    bars = (kl or {}).get(code) or []
    i = (idx.get(code) or {}).get(date)
    if i is None or i < 1 or i >= len(bars):
        return None
    try:
        return float(bars[i][2]) / float(bars[i - 1][2]) - 1.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# ---------------------------------------------------------------- 切片
def slice_stats(rows, key_fn, min_n, min_dates):
    """把带 ret 的行按 key 切开，返回 {key: {n, win, avg, dates}}，只留过护栏的切片。"""
    g = {}
    for r in rows:
        k = key_fn(r)
        if k in (None, "", "未知"):
            continue
        g.setdefault(k, []).append(r)
    out = {}
    for k, lst in g.items():
        n = len(lst)
        dates = len({r["date"] for r in lst})
        if n < min_n or dates < min_dates:
            continue
        k_win = sum(1 for r in lst if r["ret"] > 0)
        out[k] = {"n": n, "dates": dates, "win": k_win / n,
                  "avg": sum(r["ret"] for r in lst) / n,
                  "wilsonLo": wilson(k_win, n)[0]}
    return out


def build_src_rows(D, entries, policy):
    """signal_ledger 的日期×src 快照 → 带 5 日收益的行。"""
    kl = D.get("stkKlines") or {}
    idx = bars_index(kl)
    h = int(policy.get("horizon", 5))
    rows = []
    for date, by_src in entries.items():
        # 当日横截面中位数 ≈ criteria 里的「全市场等权」基准
        day_rets = {}
        for src, items in by_src.items():
            for it in items or []:
                code = str(it.get("code") or "")
                r = fwd_ret(kl, idx, code, date, h)
                if r is not None:
                    day_rets.setdefault(code, r)
        mkt = statistics.median(day_rets.values()) if day_rets else None
        for src, items in by_src.items():
            for it in items or []:
                code = str(it.get("code") or "")
                r = fwd_ret(kl, idx, code, date, h)
                if r is None:
                    continue
                rows.append({
                    "date": date, "src": src, "code": code,
                    "sector": (it.get("sector") or "").strip(),
                    "ret": r,
                    "exc": (r - mkt) if mkt is not None else None,
                    "dayPct": day_pct(kl, idx, code, date),
                    "weekday": datetime.strptime(date, "%Y-%m-%d").weekday(),
                    "mktUp": (mkt is not None and mkt > 0),
                })
    return rows


def build_chen_rows(entries):
    rows = []
    for e in entries or []:
        res = e.get("result") or {}
        if res.get("status") != "settled":
            continue
        ret = res.get("retPct")
        if ret is None:
            continue
        entry = e.get("entryDate") or ""
        try:
            gap = (datetime.strptime(entry, "%Y-%m-%d") -
                   datetime.strptime(e.get("ztDate") or entry, "%Y-%m-%d")).days
        except Exception:                                        # noqa: BLE001
            gap = None
        stop = e.get("stopPrice")
        buy = res.get("buyPrice") or e.get("entryPrice")
        R = ((buy - stop) / buy) if (buy and stop and buy > stop) else None
        rows.append({
            "date": entry, "src": e.get("pool") or "chen", "code": str(e.get("code") or ""),
            "ret": float(ret) / 100.0, "exc": None, "dayPct": None,
            "weekday": datetime.strptime(entry, "%Y-%m-%d").weekday() if entry else None,
            "tier": e.get("tier") or "", "R": R, "gap": gap,
            "holdDays": e.get("holdDays"),
        })
    return rows


# ---------------------------------------------------------------- 动作
def action_id(module, dim, slc, kind):
    return hashlib.sha1(("%s|%s|%s|%s" % (module, dim, slc, kind)).encode()).hexdigest()[:10]


def make_actions(module, overall_win, slices, dim, policy, kind_hint="filter"):
    """从切片区生成动作：明显更差 → 剔除；明显更好 → 优先。三重护栏见文件头。"""
    out = []
    delta_min = float(policy.get("sliceDelta", 0.10))
    guard = float(policy.get("sliceGuard", 0.03))
    for slc, st in slices.items():
        d = st["win"] - overall_win
        if d <= -delta_min and st["wilsonLo"] < overall_win - guard:
            out.append({
                "module": module, "dim": dim, "slice": slc, "kind": "filter",
                "rule": "在「%s」里剔除 %s = %s（该切片 %.0f%% vs 整体 %.0f%%）"
                        % (LABEL.get(module, module), dim, slc, st["win"] * 100, overall_win * 100),
                "delta": round(d, 3),
            })
        elif d >= delta_min and st["wilsonLo"] > overall_win + guard:
            out.append({
                "module": module, "dim": dim, "slice": slc, "kind": "prefer",
                "rule": "在「%s」里优先 %s = %s（该切片 %.0f%% vs 整体 %.0f%%）"
                        % (LABEL.get(module, module), dim, slc, st["win"] * 100, overall_win * 100),
                "delta": round(d, 3),
            })
    return out


# ---------------------------------------------------------------- 主流程
def run(write=False, quiet=False):
    D, entries, chen, policy = load_inputs()
    h = int(policy.get("horizon", 5))
    min_n = int(policy.get("minN", 30))
    warn = float(policy.get("warnWR", 0.45))
    fail = float(policy.get("failWR", 0.40))
    smin = int(policy.get("sliceMinN", 15))
    sdates = int(policy.get("sliceMinDates", 3))

    sp = D.get("signalPerf") or {}
    by = sp.get("bySrc") or {}
    # 汇总行（duanban / chen / buy）是 confirmed+watching 之类滚出来的，不是独立策略：
    # 表里保留（读者需要「断板反包整体」），但**不给它们生成动作**，否则同一批票会被建议两次。
    leaf = set()
    for by_src in entries.values():
        leaf |= set((by_src or {}).keys())

    # ---- 模块健康（用 signalPerf 的权威口径，不在页面重算一遍）
    modules, alerts = [], []
    for src, info in by.items():
        if not isinstance(info, dict):
            continue
        hh = (info.get("h") or {}).get(str(h)) or {}
        n = int(hh.get("n") or 0)
        win = hh.get("win")
        exc = hh.get("exc")
        t = hh.get("t")
        if n < min_n or win is None:
            continue
        flag = "fail" if win < fail else ("warn" if win < warn else "ok")
        modules.append({
            "src": src, "label": info.get("label") or LABEL.get(src, src),
            "rollup": src not in leaf,
            "n": n, "days": info.get("days"), "win": round(win, 3),
            "exc": (round(exc, 2) if exc is not None else None),
            "t": (round(t, 2) if t is not None else None), "flag": flag,
        })
        if flag != "ok":
            alerts.append({
                "src": src, "label": modules[-1]["label"], "win": round(win, 3),
                "threshold": (fail if flag == "fail" else warn),
                "level": flag,
                "text": "%s 的 %d 日胜率 %.0f%%，低于%s阈值 %.0f%%（样本 %d 条 / %d 天）"
                        % (modules[-1]["label"], h, win * 100, "失败线" if flag == "fail" else "警戒线",
                           (fail if flag == "fail" else warn) * 100, n, info.get("days") or 0),
            })
    modules.sort(key=lambda x: (x["flag"] != "fail", x["flag"] != "warn", x["win"]))

    # ---- 切片（只切样本够的模块；陈学长走自己的台账）
    rows = build_src_rows(D, entries, policy)
    chen_rows = build_chen_rows(chen)
    raw_actions = []
    for m in modules:
        src = m["src"]
        if m.get("rollup"):
            continue
        mine = [r for r in rows if r["src"] == src]
        if len(mine) < min_n:
            continue
        for dim, key in (("板块", lambda r: r["sector"]),
                         ("当日涨幅", lambda r: bucket_day_pct(r["dayPct"])),
                         ("星期", lambda r: "周" + "一二三四五六日"[r["weekday"]] if r["weekday"] is not None else None),
                         ("大盘方向", lambda r: "大盘红盘" if r["mktUp"] else "大盘绿盘")):
            sl = slice_stats(mine, key, smin, sdates)
            raw_actions += make_actions(src, m["win"], sl, dim, policy)

    # 陈学长：R 档 / 隔日数 / 持有天数（这块是「买点过赔率闸」的证据来源）
    for pool in ("chen_strict", "chen_loose"):
        mine = [r for r in chen_rows if r["src"] == pool]
        if len(mine) < smin:
            continue
        base = sum(1 for r in mine if r["ret"] > 0) / len(mine)
        for dim, key in (("R 档", lambda r: bucket_R(r["R"])),
                         ("进场间隔", lambda r: bucket_gap(r["gap"])),
                         ("持有天数", lambda r: ("%s日" % r["holdDays"]) if r["holdDays"] else None)):
            sl = slice_stats(mine, key, smin, sdates)
            raw_actions += make_actions(pool, base, sl, dim, policy)

    # ---- 去重 + 持久化
    try:
        store = json.load(open(ACTIONS, encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        store = {"actions": []}
    known = {a["id"]: a for a in store.get("actions") or []}
    now = datetime.now()
    cap = int(policy.get("maxActions", 8))
    # 配额分配：每个「低胜率且非汇总」的模块**先保底 1 条**（否则诊断报了病没给方，
    # G82 会直接拦），剩余名额再按证据强度（|delta|）填。上限因此是「软上限」。
    flagged = [m["src"] for m in modules if m["flag"] != "ok" and not m.get("rollup")]
    by_mod = {}
    for a in raw_actions:
        by_mod.setdefault(a["module"], []).append(a)
    picked, used = [], set()
    for src in flagged:
        lst = sorted(by_mod.get(src, []), key=lambda x: -abs(x["delta"]))
        if lst:
            picked.append(lst[0])
            used.add(id(lst[0]))
    rest = sorted([a for a in raw_actions if id(a) not in used], key=lambda x: -abs(x["delta"]))
    for a in rest:
        if sum(1 for x in picked if x["module"] == a["module"]) >= 2:
            continue
        picked.append(a)
    raw_actions = picked[:max(cap, len(flagged))]
    added = 0
    for a in raw_actions:
        aid = action_id(a["module"], a["dim"], a["slice"], a["kind"])
        if aid in known:
            continue
        if added >= cap:
            break
        rec = {
            "id": aid, "module": a["module"], "dim": a["dim"], "slice": a["slice"],
            "kind": a["kind"], "rule": a["rule"],
            "evidence": {"delta": a["delta"], "horizon": h,
                         "basis": "close→close %dd，切片与整体同口径" % h},
            "status": "proposed", "createdAt": now.strftime("%Y-%m-%d %H:%M"),
            "appliedAt": None, "before": None, "after": None,
        }
        store["actions"].append(rec)
        known[aid] = rec
        added += 1

    # ---- 验证到期动作（applied → verified / rolledBack）
    vdays = int(policy.get("verifyDays", 10))
    checked = 0
    for a in store["actions"]:
        if a.get("status") != "applied" or not a.get("appliedAt"):
            continue
        try:
            due = (now - datetime.strptime(a["appliedAt"][:10], "%Y-%m-%d")).days
        except Exception:                                        # noqa: BLE001
            continue
        if due < vdays:
            continue
        after_rows = [r for r in (rows + chen_rows)
                      if r["src"] == a["module"] and r["date"] > a["appliedAt"][:10]]
        dim_key = {"板块": lambda r: r["sector"], "当日涨幅": lambda r: bucket_day_pct(r["dayPct"]),
                   "星期": lambda r: "周" + "一二三四五六日"[r["weekday"]] if r["weekday"] is not None else None,
                   "大盘方向": lambda r: "大盘红盘" if r["mktUp"] else "大盘绿盘",
                   "R 档": lambda r: bucket_R(r["R"]), "进场间隔": lambda r: bucket_gap(r["gap"]),
                   "持有天数": lambda r: ("%s日" % r["holdDays"]) if r["holdDays"] else None}.get(a["dim"])
        if not dim_key:
            continue
        mine = [r for r in after_rows if dim_key(r) == a["slice"]]
        if len(mine) < 8:
            continue
        w = sum(1 for r in mine if r["ret"] > 0) / len(mine)
        a["after"] = {"win": round(w, 3), "n": len(mine), "at": now.strftime("%Y-%m-%d")}
        b = (a.get("before") or {}).get("win")
        if b is not None and w < b - 0.05:
            a["status"] = "rolledBack"
        else:
            a["status"] = "verified"
        checked += 1

    # ---- 权重：把「切片/模块通病」变成有阻尼的权重先验
    weights = {}
    cur_w = sp.get("weights") or {}
    auto = bool(policy.get("autoWeight"))
    aw_min_n = int(policy.get("autoWeightMinN", 80))
    aw_shift = float(policy.get("autoWeightMaxShift", 0.10))
    if auto and cur_w:
        tgt = {}
        for s, wv in cur_w.items():
            m = next((x for x in modules if x["src"] == s), None)
            if not m or m["n"] < aw_min_n:
                tgt[s] = wv
                continue
            # 胜率低于 warn 的模块按缺口比例降权，不低于原值的 50%
            adj = wv * (m["win"] / warn) if m["win"] < warn else wv
            tgt[s] = max(wv * 0.5, adj)
        tot = sum(tgt.values()) or 1.0
        tgt = {s: v / tot for s, v in tgt.items()}
        shift = max(abs(tgt[s] - cur_w.get(s, 0)) for s in tgt)
        if shift <= aw_shift:
            weights = {"before": cur_w, "after": {s: round(v, 3) for s, v in tgt.items()},
                       "maxShift": round(shift, 3), "applied": True,
                       "why": "低于警戒线的模块按缺口降权（下限 50%%），单次位移 %.3f ≤ 上限 %.2f" % (shift, aw_shift)}
            if write:
                json.dump({"asOf": now.strftime("%Y-%m-%d"), "weights": weights["after"],
                           "why": weights["why"]}, open(WEIGHTS, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
        else:
            weights = {"before": cur_w, "after": {s: round(v, 3) for s, v in tgt.items()},
                       "maxShift": round(shift, 3), "applied": False,
                       "why": "单次位移 %.3f 超上限 %.2f，留待下轮（防一次调过头）" % (shift, aw_shift)}

    diag = {
        "asOf": now.strftime("%Y-%m-%d %H:%M"),
        "horizon": h,
        "policy": {k: policy.get(k) for k in
                   ("minN", "warnWR", "failWR", "sliceMinN", "sliceDelta", "verifyDays")},
        "modules": modules,
        "alerts": alerts,
        "actions": sorted(store["actions"], key=lambda a: a.get("createdAt") or "", reverse=True)[:20],
        "weights": weights,
        "counts": {"proposed": sum(1 for a in store["actions"] if a.get("status") == "proposed"),
                   "applied": sum(1 for a in store["actions"] if a.get("status") == "applied"),
                   "verified": sum(1 for a in store["actions"] if a.get("status") == "verified"),
                   "rolledBack": sum(1 for a in store["actions"] if a.get("status") == "rolledBack")},
        "slices": {"srcRows": len(rows), "chenRows": len(chen_rows), "checked": checked,
                   "newActions": added},
    }

    if write:
        store["updatedAt"] = diag["asOf"]
        json.dump(store, open(ACTIONS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        D["ledgerDiag"] = diag
        locked = False
        try:
            import subprocess
            r = subprocess.run([sys.executable, os.path.join(HERE, "pipeline_lock.py"),
                                "acquire", "--task", "ledger-doctor", "--wait", "90"],
                               capture_output=True, text=True)
            locked = ("[LOCK][OK]" in r.stdout) or ("[STALE-STOLEN]" in r.stdout)
            if not locked:
                # 静默跳过等于「诊断只落了一半」：ledger_actions.json 写进去了、data.js 没有，
                # 下一轮 G82 会报「两处动作数不一致」，而根因留在 90 秒前的这行日志里。
                print("[FAIL] 拿不到 data 锁，本次诊断不完整（只写了 ledger_actions.json）：%s"
                      % r.stdout.strip()[:80])
            else:
                D = common.load_dashboard_data(HERE)              # 持锁后重读，防覆盖别人刚写的
                D["ledgerDiag"] = diag
                common.write_data_js(os.path.join(HERE, "data.js"), D, label="[ledger-doctor]")
        finally:
            if locked:
                import subprocess
                subprocess.run([sys.executable, os.path.join(HERE, "pipeline_lock.py"),
                                "release", "--task", "ledger-doctor"], check=False)

    if not quiet:
        print("[诊断] %s｜模块 %d 个，低胜率 %d 个，新增动作 %d 条，验证 %d 条"
              % (diag["asOf"], len(modules), len(alerts), added, checked))
        for a in alerts:
            print("  ⚠️ " + a["text"])
        for a in sorted(store["actions"], key=lambda x: x.get("createdAt") or "", reverse=True)[:6]:
            print("  · [%s] %s" % (a["status"], a["rule"]))
        if weights.get("applied"):
            print("  · 权重已调：%s → %s" % (weights["before"], weights["after"]))
        elif weights:
            print("  · 权重建议（未应用）：%s" % weights.get("why"))
    return diag


def main():
    ap = argparse.ArgumentParser(description="台账诊断与优化闭环")
    ap.add_argument("--write", action="store_true", help="写 data.js 的 ledgerDiag + ledger_actions.json")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    run(write=a.write, quiet=a.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())

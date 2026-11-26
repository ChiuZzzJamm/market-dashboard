#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""反包成功池（pool_success.py）。

用户 2026-11-26：断板反包与陈学长都要多一个「成功池」——
「虽然没涨停但也是反包成功的（如超声电子 +7.51%、奥士康 +9.91%），
  反包成功的就不要放在确认池了」。

口径：候选池（断板 confirmed/watching、陈学长 strict/loose/watching）里，
**当日涨幅 ≥ +7%** 的票 = 已经反包成功 → 移到 success 池，并从原池移除。
涨停是 +10/20%，但很多反包以 +7~9.5% 收，形态上已经兑现，不该再挂「待启动」。

幂等：同一只票同一天重复跑不会重复入池；历史 success 条目保留（带 successDate）。
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data.js")
SUCCESS_PCT = 7.0          # 当日涨幅达到这里即视为「反包成功」


def acquire(task):
    r = subprocess.run([sys.executable, os.path.join(HERE, "pipeline_lock.py"),
                        "acquire", "--task", task, "--wait", "120"],
                       capture_output=True, text=True, cwd=HERE)
    return "[LOCK][OK]" in r.stdout or "STOLEN" in r.stdout


def release(task):
    subprocess.run([sys.executable, os.path.join(HERE, "pipeline_lock.py"),
                    "release", "--task", task], check=False, cwd=HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if not a.write:
        print(__doc__)
        return 0
    sys.path.insert(0, HERE)
    import common
    if not acquire("pool-success"):
        print("[FAIL] 拿不到 data 锁")
        return 3
    try:
        D = common.load_dashboard_data(HERE)
        today = datetime.now().strftime("%Y-%m-%d")
        moved = []
        for node, keys in (("duanban", ("confirmed", "watching")),
                           ("chenPool", ("strict", "loose", "watching"))):
            box = D.setdefault(node, {})
            succ = box.get("success")
            if not isinstance(succ, list):
                succ = []
            have = {str(x.get("code")) for x in succ if isinstance(x, dict)}
            keep = {}
            for k in keys:
                arr = box.get(k) or []
                rest = []
                for e in arr:
                    if not isinstance(e, dict):
                        continue
                    pct = e.get("pct")
                    if isinstance(pct, (int, float)) and pct >= SUCCESS_PCT:
                        c = str(e.get("code"))
                        if c and c not in have:
                            e2 = dict(e)
                            e2["successDate"] = today
                            e2["stage"] = "已反包成功"
                            e2["poolFrom"] = k
                            succ.insert(0, e2)
                            have.add(c)
                            moved.append((node, c, e.get("name"), pct))
                        continue          # 从原池移除
                    rest.append(e)
                keep[k] = rest
            for k, v in keep.items():
                box[k] = v
            box["success"] = succ[:80]    # 只留最近 80 条，防止无限增长
        # G66 要「池内 pct≥7% 的票全部在 compare.today.done 里」。票被移进成功池后
        # 就从 confirmed/watching 消失了，done 若不同步就会报「没进今日已兑现」。
        # 所以每次运行都按 success 池回填一遍（幂等）。
        cp = D.get("chenPool") or {}
        cmp_ = cp.setdefault("compare", {})
        tdy = cmp_.setdefault("today", {})
        done = tdy.get("done")
        if not isinstance(done, list):
            done = []
        have = {str(x.get("code")) for x in done if isinstance(x, dict)}
        added = 0
        # 名称兜底：录入口只有代码时（用户从 🔍 面板复制，历史上只有代码），
        # 用全库名称表补，别让兑现列表出现「601086 601086」。
        _nm = {}
        for _node in ("duanban", "chenPool", "breakoutPool", "harmonic"):
            _b = D.get(_node) or {}
            for _k, _v in _b.items():
                if isinstance(_v, list):
                    for _e in _v:
                        if isinstance(_e, dict) and _e.get("code") and _e.get("name"):
                            _nm.setdefault(str(_e["code"]), str(_e["name"]))
        for _c, _v in ((D.get("stockConcepts") or {}).get("items") or {}).items():
            if isinstance(_v, dict) and _v.get("name"):
                _nm.setdefault(str(_c), str(_v["name"]))
        # 已有 done 条目若缺名也补上（历史那次只有代码）
        for _d in done:
            if isinstance(_d, dict) and not _d.get("name"):
                _d["name"] = _nm.get(str(_d.get("code")), "")
        for e in (D.get("duanban") or {}).get("success") or []:
            c = str(e.get("code"))
            if c and c not in have:
                nm = e.get("name") or _nm.get(c) or ""
                if not e.get("name") and nm:
                    e["name"] = nm
                done.append({"code": c, "name": nm, "pct": e.get("pct")})
                have.add(c); added += 1
        tdy["done"] = done
        if added:
            print("[pool-success] 同步 compare.today.done %d 只" % added)

        common.write_data_js(DATA, D, label="[pool-success]")
        print("[pool-success] 移入成功池 %d 只（≥ +%.0f%%）" % (len(moved), SUCCESS_PCT))
        for m in moved[:10]:
            print("   %s %s %s +%.1f%%" % (m[0], m[1], m[2], m[3]))
    finally:
        release("pool-success")
    return 0


if __name__ == "__main__":
    sys.exit(main())

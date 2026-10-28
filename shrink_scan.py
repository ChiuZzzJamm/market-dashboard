#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缩量回踩扫描：放量阴之后缩量 1~2 天（用户 2026-10-28 口径）。

为什么单独一个模块：这个图形正好被现有池子切在两档之间——断板池的「待缩量」是断板
当天（放量阴本身，还没缩量），「待企稳」要求已经缩到 MA5 下方并站住；中间那段
「放量阴之后缩了 1~2 天、小实体、还没反包」谁的池子都不收。用户 2026-10-28 第 2 条
原话：「不是要反包完成的，是放量阴后面缩量的第一天，这一天可以是阴、可以是十字星、
可以是微红，也可以是缩量的第二天」。

**这是一份孪生实现**：前端 index.html 的 `ordShrinkScan()` 是同一套四条硬条件，从
浏览器的 `D.stkKlines` 现扫给「明日可下单」区块出卡；本模块给 9:45 开盘精选供候选
（那边跑不了 JS）。改条件必须两边一起改，改完跑：

    python3 check_shrink_parity.py

它用 headless Chrome 取前端 poolC 的代码列表与本模块输出逐只对账，不一致就 FAIL。
为什么不做成「一份实现两处读」：前端是单文件内联 JS，没有构建步骤；把结果写进
data.js 字段需要 16:00 数据步新增写口、还要在浏览器侧删掉现扫逻辑（否则同一量两套
来源）。孪生 + 对账脚本是这条约束下代价最小、且能自动化证明一致的做法。

四条硬条件（缺一条就不是这个图形，宁可不出）：
  ① T 日涨停：收盘/前收 − 1 ≥ 9.7%（主板口径）；
  ② T+1 放量收阴：量比 1.1~3.5 倍，且收盘 < 开盘（十字星/微红不算放量阴）；
  ③ T+2 起 1~2 天：每天量都比放量阴那天小、涨跌幅在 −6%~+4%、
     收盘不高于 T 日收盘 3%（超了说明已经反包，不是「还没反包」）；
  ④ 这几天最低收盘没跌破 T 日最低价 2%（破了就是形态作废，与断板池 eval_stage 同一条线）。
只收主板（60/00）、只收有中文名的（印一串代码读者认不出）、只收 25 根以上 K 线的。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MAIN_BOARD = ("60", "00")
ZT_PCT = 9.7            # ① 涨停门槛（%）
VOL_LO, VOL_HI = 1.1, 3.5   # ② 放量阴的量比区间
PCT_LO, PCT_HI = -6.0, 4.0  # ③ 缩量期单日涨跌幅区间（%）
NOT_ZT_OVER = 1.03      # ③ 收盘不得高于 T 日收盘的 3%（= 已反包）
LOW_TOL = 0.98          # ④ 收盘跌破 T 日最低价的容忍线
MIN_BARS = 25           # K 线不足 25 根不判（前高/均线全算不出来）


def _f(v):
    try:
        x = float(v)
    except Exception:
        return None
    return x


def bars_of(D, code):
    """stkKlines 容器 → [(day, open, close, high, low, vol)]，按日期升序。

    与 lead_lag.bars_of / atr_stop.norm_bars 同口径（**close 在下标 2**，不是 OHLC 顺序）；
    与前端 stkParseKline 等价：开收为空的行走丢，不补 0。
    """
    out = []
    for b in ((D.get("stkKlines") or {}).get(str(code)) or []):
        if not isinstance(b, (list, tuple)) or len(b) < 5:
            continue
        o, c, h, l = _f(b[1]), _f(b[2]), _f(b[3]), _f(b[4])
        if o is None or c is None or c <= 0:
            continue
        out.append((str(b[0]), o, c, (h if h and h > 0 else c),
                    (l if l and l > 0 else c), (_f(b[5]) if len(b) > 5 else None) or 0.0))
    out.sort(key=lambda x: x[0])
    return out


def name_map(D):
    """code → 简称。顺序与前端 ordNameMap 一致：stkKlineNames → fullScan.names → 各池条目。"""
    m = {}

    def put(c, n):
        c = str(c or "").strip()
        n = str(n or "").strip()
        if len(c) == 6 and c.isdigit() and n and c not in m:
            m[c] = n

    for c, n in (D.get("stkKlineNames") or {}).items():
        put(c, n)
    for c, n in (((D.get("fullScan") or {}).get("names")) or {}).items():
        put(c, n)
    db, cp = D.get("duanban") or {}, D.get("chenPool") or {}
    for arr in (db.get("confirmed"), db.get("watching"), db.get("failPool"), db.get("mcapCut"),
                cp.get("strict"), cp.get("loose"), cp.get("watching"), cp.get("failPool")):
        for e in (arr or []):
            if isinstance(e, dict):
                put(e.get("code"), e.get("name"))
    return m


def pool_codes(D):
    """已在池里的代码——池里的票在前端①②档已有卡，这里不能重复出。

    只算**前端 ordPoolCore 收的那五个数组**（断板确认/观察 + 陈学长严格/放宽/观察）：
    failPool（形态已失效）不在其中，把它算进来会让缩量回踩档凭空少几只，
    而前端仍然照常给那些票出卡——两边口径必须逐字对齐，否则同一份 data.js 出两个结论。
    """
    db, cp = D.get("duanban") or {}, D.get("chenPool") or {}
    out = set()
    for arr in (db.get("confirmed"), db.get("watching"),
                cp.get("strict"), cp.get("loose"), cp.get("watching")):
        for e in (arr or []):
            if isinstance(e, dict) and e.get("code"):
                out.add(str(e.get("code")))
    return out


def _one(rows):
    """在一只票的 K 线上找「放量阴 + 缩量 1~2 天」的形态，返回命中信息或 None。

    T 日只从「倒数第 3 / 第 4 根」试（缩量 1 天 / 缩量 2 天），再早就不是「刚缩完」了。
    """
    n = len(rows)
    for d in (2, 3):
        iT = n - 1 - d
        if iT < 21:                       # 前 21 根要留给形态/均线参照，与前端同一条
            continue
        # ① T 日涨停
        pc = rows[iT - 1][2]
        if not (pc > 0):
            continue
        if (rows[iT][2] / pc - 1) * 100 < ZT_PCT:
            continue
        # ② T+1 放量收阴
        i1 = iT + 1
        if i1 > n - 1:
            continue
        if not (rows[i1][2] < rows[i1][1]):
            continue
        vt = rows[iT][5]
        vr = (rows[i1][5] / vt) if vt and vt > 0 else 0
        if not (VOL_LO <= vr <= VOL_HI):
            continue
        # ③④ 之后 1~2 天：缩量 + 小实体 + 未反包 + 未破 T 日低点
        ok, min_c = True, 1e9
        for k in range(i1 + 1, n):
            if not (rows[k][5] < rows[i1][5]):
                ok = False
                break
            p0 = rows[k - 1][2]
            pct = ((rows[k][2] / p0 - 1) * 100) if p0 > 0 else 0.0
            if pct > PCT_HI or pct < PCT_LO:
                ok = False
                break
            if rows[k][2] > rows[iT][2] * NOT_ZT_OVER:
                ok = False
                break
            min_c = min(min_c, rows[k][2])
        if not ok:
            continue
        lowT = rows[iT][4]
        if lowT > 0 and min_c < lowT * LOW_TOL:
            continue
        return {"iT": iT, "i1": i1, "shrinkDays": n - 1 - i1,
                "volRatio": round(vr, 2), "ztDate": rows[iT][0],
                "ztHigh": rows[iT][3], "ztClose": rows[iT][2], "lowT": lowT}
    return None


def scan(D, exclude=None, sectors=None):
    """扫全量 K 线 → {"rows": [...], "nCand": 扫描只数}。

    rows 按「缩量天数」降序（缩量越久越接近该动），字段够 update_star 直接出卡：
    code / name / sector / ztDate / shrinkDays / volRatio / pct（最后一根涨跌幅）/ ztHigh / lowT。
    """
    nm = name_map(D)
    sm = sectors if sectors is not None else (D.get("sectorMap") or {})
    skip = set(exclude or ())
    rows, n_cand = [], 0
    for code, raw in ((D.get("stkKlines") or {}).items()):
        code = str(code)
        if not (len(code) == 6 and code.isdigit() and code[:2] in MAIN_BOARD):
            continue
        name = nm.get(code)
        if not name or code in skip:
            continue
        bars = bars_of(D, code)
        if len(bars) < MIN_BARS:
            continue
        n_cand += 1
        hit = _one(bars)
        if not hit:
            continue
        last, prev = bars[-1], bars[-2]
        rows.append({
            "code": code, "name": name, "sector": sm.get(code) or "",
            "ztDate": hit["ztDate"], "shrinkDays": hit["shrinkDays"],
            "volRatio": hit["volRatio"], "ztHigh": hit["ztHigh"], "lowT": hit["lowT"],
            "pct": (round((last[2] / prev[2] - 1) * 100, 2) if prev[2] > 0 else None),
            "close": last[2],
        })
    rows.sort(key=lambda r: (-r["shrinkDays"], r["code"]))
    return {"rows": rows, "nCand": n_cand}


def main():
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.js"))
    ap.add_argument("--all", action="store_true", help="连池内票一起列（默认剔除池内，与前端同口径）")
    a = ap.parse_args()
    with open(a.data, encoding="utf-8") as f:
        s = f.read()
    D = json.loads(s[s.index("{"):s.rindex("}") + 1])
    ex = set() if a.all else pool_codes(D)
    r = scan(D, exclude=ex)
    print(f"[shrink] 扫描 {r['nCand']} 只（主板+有简称+≥{MIN_BARS} 根 K 线）→ 命中 {len(r['rows'])} 只"
          + ("（含池内）" if a.all else "（已剔池内）"))
    for x in r["rows"]:
        print(f"  {x['code']} {x['name'][:8]:<9} {x['sector'][:8]:<9} 缩量第 {x['shrinkDays']} 天 "
              f"放量 {x['volRatio']} 倍 涨停日 {x['ztDate']} 今收 {x['close']} ({x['pct']:+.2f}%)")


if __name__ == "__main__":
    main()

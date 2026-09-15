#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股板块/指数/涨跌家数抓取（零 MCP 依赖，全部 HTTP 直连）：
  - 指数+成交额：腾讯 gtimg（sh000001/sz399001/sz399006/sh000688/sh000016，另取 sz399106 算两市成交额）
  - 涨跌家数：东财 push2ex getTopicZDFenBu；涨跌停数：getTopicZTPool / getTopicDTPool（真实涨跌停池）
  - 行业板块涨跌 TOP5/BOTTOM5：东财 push2delay clist（fs=m:90+t:2 行业板块，注意 + 必须写成 %2B）
  - 主力资金流入/流出 TOP3：同接口按 f62 排序（f62 单位=元，换算亿元）
写入 data.js 的 ashare 字段：tradeDate/status/indices/breadth/sectorsUp/sectorsDown/fundIn/fundOut。
不触碰 summary/outlook/bullNews/bearNews/fundNote（由自动化 AI 步骤撰写/保留）。
失败策略：单个数据源失败保留原值，全部板块数据失败时以退出码 1 退出（自动化据此降级）。
"""
import json, re, subprocess, os, sys, time, argparse
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

TZ8 = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
DELAY = "https://push2delay.eastmoney.com/api/qt/clist/get"
EX = "https://push2ex.eastmoney.com"
UT = "fa5fd1943c7b386f172d6893dbfba10b"
UT_ZT = "7eea3edcaed734bea9cbfc24409ed989"

def curl(url, timeout=20):
    # gtimg 返回 GBK、东财返回 UTF-8，这里统一按字节取回再容错解码
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout), url, "-H", "User-Agent: " + UA],
                       capture_output=True)
    return (r.stdout or b"").decode("utf-8", errors="replace")

def get_json(url, retries=2, gap=3):
    for i in range(retries + 1):
        s = curl(url)
        if s.strip():
            try:
                return json.loads(s)
            except Exception:
                pass
        if i < retries:
            time.sleep(gap)
    return None

# ---------- 1) 指数 + 两市成交额（腾讯 gtimg） ----------
INDEX_CODES = [("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指"),
               ("sh000688", "科创50"), ("sh000016", "上证50")]
VOL_CODE = "sz399106"  # 深证综指，仅用于成交额（代表全深市）

def fetch_indices():
    codes = ",".join(c for c, _ in INDEX_CODES) + "," + VOL_CODE
    s = curl(f"http://qt.gtimg.cn/q={codes}")
    if "v_sh000001" not in s:
        return None, None
    out, vol = [], 0.0
    for m in re.finditer(r'v_(\w+)="([^"]+)"', s):
        p = m.group(2).split("~")
        if len(p) < 38:
            continue
        try:
            point, pct = float(p[3]), float(p[32])
        except Exception:
            continue
        # 成交额（万元）：只取 上证指数（全沪市）+ 深证综指（全深市），
        # 创业板/科创50/上证50 等子指数不参与求和（避免重复计算）
        if m.group(1) in ("sh000001", VOL_CODE):
            try:
                vol += float(p[37])
            except Exception:
                pass
        if m.group(1) == VOL_CODE:
            continue
        name = dict(INDEX_CODES).get(m.group(1), p[1])
        out.append({"name": name, "point": point, "changePct": pct})
    if len(out) < 5:
        return None, None
    return out, (vol or None)

# ---------- 2) 涨跌家数 + 涨跌停（东财 push2ex） ----------
def fetch_breadth():
    today = datetime.now(TZ8).strftime("%Y%m%d")
    d = get_json(f"{EX}/getTopicZDFenBu?ut={UT_ZT}&dpt=wz.ztzt")
    if not d or not (d.get("data") or {}).get("fenbu"):
        return None
    up = down = flat = 0
    for item in d["data"]["fenbu"]:
        for k, v in item.items():
            k = int(k)
            if k > 0:
                up += v
            elif k < 0:
                down += v
            else:
                flat += v
    lu = fetch_pool_count("getTopicZTPool", today, "fbt%3Aasc")
    ld = fetch_pool_count("getTopicDTPool", today, "fund%3Aasc")
    if lu is None:  # 涨停池失败时用分布近似（涨幅>=10% 桶）
        lu = approx_bucket(d, lambda k: k >= 10)
    if ld is None:
        ld = approx_bucket(d, lambda k: k <= -10)
    return {"up": up, "down": down, "flat": flat, "limitUp": lu, "limitDown": ld}

def approx_bucket(fenbu_resp, cond):
    n = 0
    for item in fenbu_resp["data"]["fenbu"]:
        for k, v in item.items():
            if cond(int(k)):
                n += v
    return n

def fetch_pool_count(api, date, sort):
    d = get_json(f"{EX}/{api}?ut={UT_ZT}&dpt=wz.ztzt&Pageindex=0&pagesize=1&sort={sort}&date={date}")
    try:
        data = d["data"] or {}
        # tc = 当日池总数（最可靠）；pool 仅在 pagesize 足够时才全
        tc = data.get("tc")
        if isinstance(tc, int):
            return tc
        pool = data.get("pool")
        return len(pool) if pool else 0
    except Exception:
        return None

# ---------- 3) 行业板块（东财 push2delay，fs=m:90%2Bt:2） ----------
BOARD_FIELDS = "f3,f12,f14,f62,f128,f136"

def fetch_boards(fid, po):
    url = (f"{DELAY}?pn=1&pz=100&po={po}&np=1&fltt=2&invt=2&fid={fid}"
           f"&fs=m:90%2Bt:2&fields={BOARD_FIELDS}&ut={UT}")
    d = get_json(url)
    if not d:
        return None
    rows = (d.get("data") or {}).get("diff") or []
    return rows or None

def to_f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def build_sectors():
    up_rows = fetch_boards("f3", 1)    # 按涨跌幅降序 → 领涨
    time.sleep(2)
    down_rows = fetch_boards("f3", 0)  # 升序 → 领跌
    if not up_rows or not down_rows:
        return None, None
    def mk(r):
        pct = to_f(r.get("f3"))
        if pct is None:
            return None
        item = {"name": r.get("f14"), "pct": round(pct, 2)}
        lp, lname = to_f(r.get("f136")), r.get("f128")
        if lname and lp is not None:
            item["leader"] = {"name": lname, "changePct": round(lp, 2)}
        return item
    ups = [x for x in (mk(r) for r in up_rows) if x][:5]
    downs = [x for x in (mk(r) for r in down_rows) if x][:5]
    if len(ups) < 3 or len(downs) < 3:
        return None, None
    return ups, downs

def build_funds():
    in_rows = fetch_boards("f62", 1)
    time.sleep(2)
    out_rows = fetch_boards("f62", 0)
    if not in_rows or not out_rows:
        return None, None
    def mk(r):
        v = to_f(r.get("f62"))
        if v is None or not r.get("f14"):
            return None
        return {"name": r.get("f14"), "value": round(abs(v) / 1e8, 2)}
    ins = [x for x in (mk(r) for r in in_rows) if x][:3]
    outs = [x for x in (mk(r) for r in out_rows) if x][:3]
    if not ins or not outs:
        return None, None
    return ins, outs

# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印不写回 data.js")
    args = ap.parse_args()

    indices, vol = fetch_indices()
    if indices is None:
        print("[warn] 指数获取失败，保留原值")
    breadth = fetch_breadth()
    if breadth is None:
        print("[warn] 涨跌家数获取失败，保留原值")
    sectors_up, sectors_down = build_sectors()
    if sectors_up is None:
        print("[warn] 行业板块获取失败，保留原值")
    fund_in, fund_out = build_funds()
    if fund_in is None:
        print("[warn] 主力资金获取失败，保留原值")

    if sectors_up is None and indices is None:
        print("[error] 所有 A 股数据源均失败，保留原值")
        sys.exit(1)

    today_md = datetime.now(TZ8).strftime("%m/%d")
    updated_parts = []

    # 成交额文本（亿元 → 万亿）
    volume_text = None
    if vol:
        total_yi = vol / 1e4  # 万元 → 亿元
        volume_text = f"成交约 {total_yi/10000:.2f} 万亿" if total_yi >= 10000 else f"成交约 {total_yi:.0f} 亿"

    if not args.dry_run:
        # 写回 data.js
        s = open("data.js", encoding="utf-8").read()
        m = re.search(r'window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$', s)
        D = json.loads(m.group(1))
        a = D.setdefault("ashare", {})
        now = datetime.now(TZ8).strftime("%Y-%m-%d")
        if indices is not None:
            a["indices"] = indices
            updated_parts.append("指数")
        if breadth is not None:
            a["breadth"] = breadth
            updated_parts.append("涨跌家数")
        if volume_text:
            b = dict(a.get("breadth") or {})
            b["volumeText"] = volume_text
            a["breadth"] = b
        if sectors_up is not None:
            a["sectorsUp"] = sectors_up
            a["sectorsDown"] = sectors_down
            updated_parts.append("行业板块TOP5")
        if fund_in is not None:
            a["fundIn"] = fund_in
            a["fundOut"] = fund_out
            updated_parts.append("主力资金")
        a["tradeDate"] = now
        a["status"] = "收盘"
        if updated_parts:
            D["updatedAt"] = (f"{datetime.now(TZ8).strftime('%Y-%m-%d %H:%M')}"
                              f"（A股收盘已自动更新：{'/'.join(updated_parts)}）")
        out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, indent=2) + ";\n"
        open("data.js", "w", encoding="utf-8").write(out)
        chk = subprocess.run([os.environ.get("NODE_BIN", "node"), "--check", "data.js"],
                             capture_output=True, text=True)
        if chk.returncode != 0:
            print("[error] data.js 语法校验失败：" + chk.stderr[:300])
            sys.exit(2)
        print(f"[info] data.js 已更新（{'/'.join(updated_parts)}）")
    else:
        print("[dry-run] 指数:", json.dumps(indices, ensure_ascii=False))
        print("[dry-run] 涨跌家数:", json.dumps(breadth, ensure_ascii=False), "|", volume_text)
        print("[dry-run] 领涨TOP5:", json.dumps(sectors_up, ensure_ascii=False))
        print("[dry-run] 领跌TOP5:", json.dumps(sectors_down, ensure_ascii=False))
        print("[dry-run] 资金流入TOP3:", json.dumps(fund_in, ensure_ascii=False))
        print("[dry-run] 资金流出TOP3:", json.dumps(fund_out, ensure_ascii=False))

if __name__ == "__main__":
    main()

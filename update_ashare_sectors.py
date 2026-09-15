#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股板块/指数/涨跌家数抓取（零 MCP 依赖，全部 HTTP 直连）：
  - 指数+成交额：腾讯 gtimg（sh000001/sz399001/sz399006/sh000688/sh000016，另取 sz399106 算两市成交额）
  - 涨跌家数：东财 push2ex getTopicZDFenBu（注意：该接口为「涨停板专题」口径，剔除全部 ST 股）
    + 风险警示板(b:BK0511)补充 ST 股涨跌平家数 → 与主流行情 App 的全市场口径一致（实测逐家吻合）；
    涨跌停数：getTopicZTPool（真实涨停池）/ 全市场快照收盘封板计算（跌停，含 ST）
  - 行业板块涨跌 TOP5/BOTTOM5：东财 push2delay clist（fs=m:90+t:2 行业板块，注意 + 必须写成 %2B）
  - 主力资金流入/流出 TOP3：同接口按 f62 排序（f62 单位=元，换算亿元）
写入 data.js 的 ashare 字段：tradeDate/status/indices/breadth/sectorsUp/sectorsDown/fundIn/fundOut。
不触碰 summary/outlook/bullNews/bearNews/fundNote（由自动化 AI 步骤撰写/保留）。
失败策略：单个数据源失败保留原值，全部板块数据失败时以退出码 1 退出（自动化据此降级）。
"""
import json, re, subprocess, os, sys, time, argparse
from datetime import datetime, timezone, timedelta
from common import find_node

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
    # -H 放在 URL 之前；subprocess 额外加 timeout 兜底，杜绝 curl 异常挂死
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout), "-H", "User-Agent: " + UA, url],
                       capture_output=True, timeout=timeout + 10)
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
def fetch_st_breadth():
    """风险警示板（东财 b:BK0511，ST/*ST）补充统计。
    getTopicZDFenBu 属涨停板专题，剔除全部 ST 股（比主流 App 少约 200 家）；
    把 ST 板块内当日正常交易（有成交量）的涨/跌/平家数补回，使 breadth 与
    主流行情 App 的全市场口径一致（2026-09-15 实测：fenbu 1090/4209/49
    + ST 30/167/3 = 1120/4376/52，与东财/同花顺 App 完全一致）。
    当日停牌（vol='-' 或 0）不计——App 同样不计（停牌股平盘不进统计）。
    长期停牌/已退市残留行 f2='-' 自动跳过。失败返回 None。"""
    rows, pn, empty = [], 1, 0
    while pn <= 10:  # ST 板块约 200+ 行，硬上限防异常循环
        d = get_json(f"{DELAY}?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f12&fs=b%3ABK0511"
                     f"&fields=f2,f5,f12,f18&ut={UT}")
        diff = (d.get("data") or {}).get("diff") if d else None
        if not diff:
            empty += 1
            if empty >= 3:
                break
            time.sleep(2)
            continue
        empty = 0
        rows += diff
        if len(diff) < 100:
            break
        pn += 1
        time.sleep(0.1)
    if not rows:
        return None
    up = down = flat = 0
    for r in rows:
        price, prev, vol = r.get("f2"), r.get("f18"), r.get("f5")
        if not isinstance(price, (int, float)) or not isinstance(prev, (int, float)) or prev <= 0:
            continue  # 已退市/长期停牌残留行
        if vol in (0, "0", "-"):
            continue  # 当日停牌不计（与 App 口径一致）
        if price > prev:
            up += 1
        elif price < prev:
            down += 1
        else:
            flat += 1
    return up, down, flat


def fetch_breadth(prev_breadth=None):
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
    # ST 补充：fenbu 剔除 ST，补回风险警示板正常交易家数（与主流 App 全市场口径对齐）
    st = fetch_st_breadth()
    if st:
        up += st[0]
        down += st[1]
        flat += st[2]
    lu_codes = fetch_zt_codes(today)
    comp_zt, comp_ld = fetch_full_market_limits()
    # 涨停：东财涨停池口径准确（tc 与三大 App 一致=32），优先用；全市场计算次之；分布近似兜底
    if lu_codes:
        lu = len(lu_codes)
    elif comp_zt:
        lu = len(comp_zt)
    else:
        lu = approx_bucket(d, lambda k: k >= 10)
    # 跌停：东财跌停池剔除 ST/*ST（tc≈27）不作数据源；App 显示的跌停数还包含
    # 盘中触及跌停后打开的股票（无公开接口可复现该口径，如 2026-09-15 App=33 vs
    # 收盘封板=31，差 2 只为正和生态/杭州热电盘中开板）。这里采用「全市场快照收盘
    # 封板（含 ST）」口径。若快照失败，保留上一轮跌停数（绝不回退到27）。
    if comp_ld:
        ld = len(comp_ld)
    else:
        ld = (prev_breadth or {}).get("limitDown")
    return {"up": up, "down": down, "flat": flat, "limitUp": lu, "limitDown": ld}

def fetch_zt_codes(date):
    """返回东财涨停池代码集（tc 与三大 App 一致）；失败返回 None。
    跌停池(getTopicDTPool)剔除 ST 股（tc≈27，且不含盘中开板股），不作数据源。"""
    d = get_json(f"{EX}/getTopicZTPool?ut={UT_ZT}&dpt=wz.ztzt&Pageindex=0&pagesize=600&sort=fbt%3Aasc&date={date}")
    try:
        data = d["data"] or {}
        return {p["c"] for p in (data.get("pool") or [])}
    except Exception:
        return None

def fetch_full_market_limits():
    """全市场快照（东财 push2delay 分板块翻页），按交易所规则计算收盘涨跌停代码集。
    用于修正东财涨跌停池剔除 ST 股的口径缺陷。失败返回 (None, None)。"""
    from decimal import Decimal, ROUND_HALF_UP
    SEGS = ["m:0%2Bt:6", "m:0%2Bt:80", "m:0%2Bt:3", "m:1%2Bt:2", "m:1%2Bt:23", "m:1%2Bt:3",
            "m:0%2Bt:81%2Bs:20480"]
    rows = []
    for seg in SEGS:
        pn = 1
        empty_retry = 0
        while pn <= 200:  # 翻页硬上限，防止异常时无限循环
            d = get_json(f"{DELAY}/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f12&fs={seg}"
                         f"&fields=f2,f12,f14,f18&ut={UT}")
            diff = (d.get("data") or {}).get("diff") if d else None
            if not diff:
                # 东财偶发限流会返回空页，此时若直接 break 会丢失后续分页的股票（含跌停股）。
                # 改为退避重试，连续 4 次空页才放弃该板块。
                empty_retry += 1
                if empty_retry >= 4:
                    break
                time.sleep(2)  # 限流时加长退避，提高恢复概率
                continue
            empty_retry = 0
            rows += diff
            if len(diff) < 100:
                break
            pn += 1
            time.sleep(0.1)  # 降低限流概率
    if len(rows) < 4000:  # 快照不完整则放弃（全市场约 5600 只，分页偶发截断时保守放弃）
        return None, None
    seen, uniq = set(), []
    for r in rows:
        if r.get("f12") and r["f12"] not in seen:
            seen.add(r["f12"]); uniq.append(r)

    def ratio_of(code, name):
        if code.startswith(("30", "68")): return Decimal("0.20")   # 创业板/科创板（含ST）±20%
        if code.startswith(("4", "8", "92")): return Decimal("0.30")  # 北交所 ±30%
        if "ST" in name.upper() or "退" in name: return Decimal("0.05")
        return Decimal("0.10")

    zt, ld = set(), set()
    for r in uniq:
        code, name = r["f12"], (r.get("f14") or "").replace(" ", "")
        price, prev = r.get("f2"), r.get("f18")
        if not isinstance(price, (int, float)) or not isinstance(prev, (int, float)) or prev <= 0:
            continue
        if name.startswith(("N", "C")) and len(name) <= 3:  # 新股上市初期无涨跌幅限制
            continue
        ratio = ratio_of(code, name)
        q = Decimal("0.01")
        lim_u = float((Decimal(str(prev)) * (1 + ratio)).quantize(q, rounding=ROUND_HALF_UP))
        lim_d = float((Decimal(str(prev)) * (1 - ratio)).quantize(q, rounding=ROUND_HALF_UP))
        # 浮点精确比较可能漏掉边界股（lim 经 Decimal 量化后转 float 与原始 float 有 1e-9 级差异），
        # 用半分钱容差兜底
        if abs(price - lim_u) < 0.005:
            zt.add(code)
        elif abs(price - lim_d) < 0.005:
            ld.add(code)
    return zt, ld

def approx_bucket(fenbu_resp, cond):
    n = 0
    for item in fenbu_resp["data"]["fenbu"]:
        for k, v in item.items():
            if cond(int(k)):
                n += v
    return n

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

    # 先读取现有 data.js，用于保留上一轮字段（如跌停数失败时回退到原值）
    try:
        with open("data.js", encoding="utf-8") as f:
            s = f.read()
        m = re.search(r'window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$', s)
        D = json.loads(m.group(1))
    except Exception as e:
        print("[error] 读取 data.js 失败：" + str(e))
        sys.exit(1)
    prev_breadth = (D.get("ashare") or {}).get("breadth")

    indices, vol = fetch_indices()
    if indices is None:
        print("[warn] 指数获取失败，保留原值")
    breadth = fetch_breadth(prev_breadth)
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
        # 写回 data.js（D 已在 main 开头读取）
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
            # 当日 reason 保留：16:00 自动化由 AI 写入板块 reason，本脚本重建板块时
            # 若直接覆盖会把当日的 reason 洗掉（页面上"异动原因"消失）。
            # 仅当已有数据同为今日（tradeDate 相同）时按板块名保留 reason；
            # 跨日不保留，避免昨天的原因挂今天的行情（跨日 reason 由 16:00 AI 重新生成）。
            if a.get("tradeDate") == now:
                old_reason = {}
                for s in (a.get("sectorsUp") or []) + (a.get("sectorsDown") or []):
                    if isinstance(s, dict) and s.get("reason") and s.get("name"):
                        old_reason[s["name"]] = s["reason"]
                for s in sectors_up + sectors_down:
                    if s.get("name") in old_reason:
                        s["reason"] = old_reason[s["name"]]
                if old_reason:
                    print(f"[info] 保留当日板块 reason {len(old_reason)} 条")
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
        # 语法校验：定位 node（与 deploy.sh/run_push.sh/push_notify.py 一致，避免自动化环境
        # PATH 缺失 node 而崩溃）。node 实在不可用时跳过校验（json.dumps 已保证结构有效），
        # 仅当 node 可用且校验确实失败时仍报错，防止推送坏数据。
        try:
            node_bin = find_node()
            chk = subprocess.run([node_bin, "--check", "data.js"], capture_output=True, text=True)
            if chk.returncode != 0:
                print("[error] data.js 语法校验失败：" + (chk.stderr or "")[:300])
                sys.exit(2)
        except Exception as e:
            print("[warn] 跳过 node --check（node 不可用）：" + str(e))
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

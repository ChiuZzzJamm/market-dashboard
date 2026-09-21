#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""断板反包形态真实核验（确定性脚本，供自动化 AI 生成四类标的时调用）

形态规则（用户定稿口径，R70 放宽）：
  1-5 个交易日前涨停(T) → 涨停次日(T+1) 1.1~3.5 倍量且断板（未续板）
  → T+2 缩量（< T+1）；T+3（若存在）低于断板日量即可（允许小幅反复，不要求逐日递减）
  → 收盘不破 T 日低点、不破 5 日线（MA5，容差 1%）

数据源（零 MCP，纯 HTTP）：
  - 东财 push2ex getTopicZTPool 涨停池（含 hybk 行业板块字段，date=YYYYMMDD 无横线）
  - 新浪 K线 CN_MarketData.getKLineData（urllib + UA，scale=240 日线）

用法：
  python3 check_duanban.py                    # 核验最近 5 个交易日，文本输出
  python3 check_duanban.py --days 5 --limit 80 --json

输出：
  按东财行业板块(hybk)分组的达标候选；每只含 code/name/hybk/ztDate/form。
  form 示例: "9/15涨停→次2.1倍量断板→2日缩量·站稳MA5"
  退出码：恒为 0；无达标时输出空列表（调用方如实写「断板反包(替代)」，禁凑数）。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
EX = "https://push2ex.eastmoney.com"
UT_ZT = "7eea3edcaed734bea9cbfc24409ed989"
SINA_KLINE = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen=40")


def curl_text(url, timeout=15):
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout),
                        "-H", "User-Agent: " + UA, url],
                       capture_output=True, timeout=timeout + 10)
    return (r.stdout or b"").decode("utf-8", errors="replace")


def get_json_curl(url, retries=1, gap=3):
    for i in range(retries + 1):
        s = curl_text(url)
        if s.strip():
            try:
                return json.loads(s)
            except Exception:
                pass
        if i < retries:
            time.sleep(gap)
    return None


def get_json_urllib(url, retries=1, gap=2, timeout=12):
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Referer": "https://finance.sina.com.cn/"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            if i < retries:
                time.sleep(gap)
    return None


# ---------- Plan B (R71): 本地缓存 + 腾讯备用源，抵御新浪限频 ----------
CACHE_DIR = "/tmp/duanban_kline_cache"


def ensure_cache():
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except Exception:
        pass


def _cache_path(code):
    return os.path.join(CACHE_DIR, code + ".json")


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _tencent_code(code):
    if code.startswith(("60", "68", "9", "5")):
        return "sh" + code
    if code.startswith(("00", "30", "2", "1")):
        return "sz" + code
    return code


def fetch_kline_tencent(code):
    """备用 K 线源（腾讯 ifzq），与新浪独立域名，限频时兜底。
    返回统一 schema [{day,open,high,low,close,volume}] 或 None；
    量单位为手，但 check_form 只用比值，不影响形态判定。"""
    tcode = _tencent_code(code)
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
           + tcode + ",day,,,40,qfq")
    raw = curl_text(url, timeout=15)
    if not raw.strip():
        return None
    try:
        j = json.loads(raw)
    except Exception:
        return None
    if j.get("code") not in (0, "0", None) or not j.get("data"):
        return None
    data = (j.get("data") or {}).get(tcode) or {}
    arr = data.get("qfqday") or data.get("day") or []
    out = []
    for a in arr:
        if not isinstance(a, list) or len(a) < 6:
            continue
        try:
            out.append({"day": str(a[0]), "open": float(a[1]),
                        "close": float(a[2]), "high": float(a[3]),
                        "low": float(a[4]), "volume": float(a[5])})
        except Exception:
            continue
    return out if len(out) >= 8 else None


def get_kline(code, sym):
    """带缓存 + 双源容错的 K 线获取：
       当日缓存命中（已含最新K线）→ 新浪主源 → 腾讯备用源 → 过期缓存兜底。
       缓存只作为「限频/网络失败时」的韧性兜底，不跳过当日最新K线。"""
    ensure_cache()
    cp = _cache_path(code)
    fresh = stale = None
    if os.path.exists(cp):
        try:
            c = json.load(open(cp, encoding="utf-8"))
            if isinstance(c, dict) and c.get("bars"):
                if time.time() - c.get("ts", 0) < 24 * 3600:
                    fresh = c["bars"]
                else:
                    stale = c["bars"]
        except Exception:
            pass
    if fresh and (fresh[-1].get("day") or "")[:10] == _today_str():
        return fresh  # 已含当日最新K线，无需重抓（同 run 重试/同日重跑复用）
    kl = get_json_urllib(SINA_KLINE.format(sym=sym), retries=3, gap=1)
    if isinstance(kl, list) and kl:
        try:
            json.dump({"ts": time.time(), "bars": kl},
                      open(cp, "w", encoding="utf-8"))
        except Exception:
            pass
        return kl
    kl2 = fetch_kline_tencent(code)
    if isinstance(kl2, list) and kl2:
        try:
            json.dump({"ts": time.time(), "bars": kl2},
                      open(cp, "w", encoding="utf-8"))
        except Exception:
            pass
        return kl2
    if fresh or stale:
        print(f"[warn] {code} 新浪/腾讯均失败，启用本地缓存兜底", file=sys.stderr)
        return fresh or stale
    return None


def recent_trade_dates(n):
    """最近 n 个「工作日」日期（YYYYMMDD，降序）。节假日由调用方按空池剔除。"""
    out, d = [], datetime.now()
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
    return out


def fetch_zt_pool(date):
    """返回涨停池列表 [{c,n,hybk,...}]。区分失败与空池：失败返回 None（限频/网络），
    空池返回 []（节假日等）。失败自动重试 2 次（退避 3s/6s）。"""
    for attempt in range(3):
        j = get_json_curl(f"{EX}/getTopicZTPool?ut={UT_ZT}&dpt=wz.ztzt"
                          f"&Pageindex=0&pagesize=600&sort=fbt%3Aasc&date={date}")
        if j is not None and j.get("rc") in (0, "0", None):
            return (j.get("data") or {}).get("pool") or []
        if attempt < 2:
            time.sleep(3 * (attempt + 1))
    return None


def sina_symbol(code):
    if code.startswith(("60", "68")):
        return "sh" + code
    if code.startswith(("00", "30")):
        return "sz" + code
    return None  # 4/8/92 北交所等排除（四类标的口径排除北交所）


def limit_ratio(code):
    return 19.8 if code.startswith(("30", "68")) else 9.8


def check_form(code, kline, zt_date_iso):
    """对单只候选做形态核验（全部使用 kline 绝对索引，避免负索引错位）。
    kline: [{day,open,high,low,close,volume}]（升序）。
    返回 form 描述字符串（达标）或 None。"""
    n = len(kline)
    if n < 8:
        return None
    iT = next((i for i, k in enumerate(kline) if k.get("day") == zt_date_iso), None)
    if iT is None:
        return None
    days_ago = n - 1 - iT
    # 至少留 2 根后续K线确认缩量递减；最多 4 日前（倍量后 2-3 日缩量窗口，5 日外形态过期）
    if not (2 <= days_ago <= 5):
        return None

    def C(i):
        return float(kline[i].get("close") or 0)

    def V(i):
        return float(kline[i].get("volume") or 0)

    def pctchg(i):
        if i < 1 or C(i - 1) <= 0:
            return 0.0
        return (C(i) - C(i - 1)) / C(i - 1) * 100

    th = limit_ratio(code)
    # T 日确为涨停（与涨停池口径互验）
    if pctchg(iT) < th:
        return None
    vT = V(iT)
    if vT <= 0:
        return None
    # T+1 约 2 倍量（1.1~3.5x，R70 放宽下限：温和放量亦算）且断板（未再涨停）
    i1 = iT + 1
    r1 = V(i1) / vT
    if not (1.1 <= r1 <= 3.5):
        return None
    if pctchg(i1) >= th:
        return None
    # T+2 起缩量（T+2 < T+1 必查）；T+3 起只要求低于断板日量（允许小幅反复，R70）
    i2 = iT + 2
    if i2 > n - 1 or not (0 < V(i2) < V(i1)):
        return None
    i3 = iT + 3
    if i3 <= n - 1 and not (0 < V(i3) < V(i1)):
        return None
    # 不破 T 日低点（容差 0.5%）
    lowT = float(kline[iT].get("low") or 0)
    if lowT > 0 and min(C(i) for i in range(i1, n)) < lowT * 0.98:
        return None
    # 不破 MA5（最新收盘 ≥ MA5×0.99）
    ma5 = sum(C(i) for i in range(n - 5, n)) / 5.0
    if C(n - 1) < ma5 * 0.97:
        return None
    zt_md = f"{zt_date_iso[5:7]}/{zt_date_iso[8:10]}"
    n_shrink = n - 1 - i1
    return f"{zt_md}涨停→次{r1:.1f}倍量断板→{n_shrink}日缩量·站稳MA5"


def main():
    ap = argparse.ArgumentParser(description="断板反包形态真实核验")
    ap.add_argument("--days", type=int, default=5, help="回看交易日数（默认5）")
    ap.add_argument("--limit", type=int, default=300, help="最多核验的候选股数（默认300，覆盖全池勿低于250）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（供自动化消费）")
    args = ap.parse_args()

    dates = recent_trade_dates(args.days)
    # 1) 收集候选：code -> {name, hybk, zt_dates:[...]}
    cand = {}
    valid_dates = []
    for d in dates:
        pool = fetch_zt_pool(d)
        if pool is None:
            # 限频/网络失败（≠空池）：重试后仍失败则明确告警，避免静默漏候选（R70）
            print(f"[warn] 涨停池 {d} 拉取失败（限频/网络），该日候选缺失", file=sys.stderr)
            continue
        if not pool:
            continue
        valid_dates.append(d)
        for p in pool:
            code = str(p.get("c") or "")
            if not re.fullmatch(r"\d{6}", code):
                continue
            rec = cand.setdefault(code, {"name": p.get("n") or code,
                                         "hybk": p.get("hybk") or "",
                                         "zt": []})
            if d not in rec["zt"]:
                rec["zt"].append(d)
        time.sleep(0.4)  # push2ex 限频保护
    if not valid_dates or not cand:
        print("[]" if args.json else "无达标候选（涨停池为空或不可用）")
        return

    # 最近涨停优先核验
    cands = sorted(cand.items(), key=lambda kv: max(kv[1]["zt"]), reverse=True)[:args.limit]

    results = []
    failed = []  # K线拉取失败的候选（多为新浪限频），冷却后二轮重试（R70）
    for code, rec in cands:
        sym = sina_symbol(code)
        if not sym:
            continue
        # R72 修复：首轮也走 get_kline（含本地缓存 + 腾讯备用源），不再裸打新浪
        # —— 限频窗口下首轮即可借缓存/腾讯兜底，省去整轮失败后再走 15s 冷却二轮。
        kl = get_kline(code, sym)
        if not isinstance(kl, list) or not kl:
            failed.append((code, rec))
            continue
        for d in sorted(rec["zt"], reverse=True):
            iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            try:
                form = check_form(code, kl, iso)
            except Exception:
                form = None
            if form:
                results.append({"code": code, "name": rec["name"],
                                "hybk": rec["hybk"], "ztDate": iso, "form": form})
                break  # 取最近一次达标形态即可
        time.sleep(0.4)  # 新浪限频保护（R70: 0.25→0.4）

    # 二轮重试：冷却 15s 后对失败候选重试一次（新浪限频窗口恢复）
    if failed:
        time.sleep(15)
        for code, rec in failed:
            sym = sina_symbol(code)
            if not sym:
                continue
            kl = get_kline(code, sym)
            if not isinstance(kl, list) or not kl:
                print(f"[warn] {code} {rec['name']} K线两次拉取失败，本候选缺失", file=sys.stderr)
                continue
            for d in sorted(rec["zt"], reverse=True):
                iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
                try:
                    form = check_form(code, kl, iso)
                except Exception:
                    form = None
                if form:
                    results.append({"code": code, "name": rec["name"],
                                    "hybk": rec["hybk"], "ztDate": iso, "form": form})
                    break
            time.sleep(0.6)

    # 按板块分组
    # R77：标的口径升级——只保留沪深主板(60/00 开头)，剔除科创板(688/689)、
    # 创业板(300/301/302)与北交所(4/8/92 开头)——用户要求提供标的均非科创/创业板
    results = [r for r in results
               if str(r["code"]).startswith(("60", "00"))]
    grouped = {}
    for r in results:
        grouped.setdefault(r["hybk"] or "其他", []).append(r)

    if args.json:
        print(json.dumps(grouped, ensure_ascii=False, indent=1))
    else:
        if not grouped:
            print("无达标候选（全部形态核验未通过）")
            return
        for hybk, lst in sorted(grouped.items()):
            print(f"\n【{hybk}】{len(lst)} 只")
            for r in lst:
                print(f"  {r['code']} {r['name']}  {r['form']}")
        print(f"\n合计 {len(results)} 只达标 / 核验 {len(cands)} 只候选")


if __name__ == "__main__":
    main()

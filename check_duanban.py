#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""断板反包形态真实核验（确定性脚本，供自动化 AI 生成四类标的时调用）

形态规则（用户定稿口径）：
  1-5 个交易日前涨停(T) → 涨停次日(T+1)约 2 倍量且断板（未续板）
  → 随后 2-3 日成交量缩量递减 → 收盘不破 T 日低点、不破 5 日线（MA5，容差 1%）

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


def recent_trade_dates(n):
    """最近 n 个「工作日」日期（YYYYMMDD，降序）。节假日由调用方按空池剔除。"""
    out, d = [], datetime.now()
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
    return out


def fetch_zt_pool(date):
    """返回涨停池列表 [{c,n,hybk,...}]；空池/限频返回 []。"""
    j = get_json_curl(f"{EX}/getTopicZTPool?ut={UT_ZT}&dpt=wz.ztzt"
                      f"&Pageindex=0&pagesize=600&sort=fbt%3Aasc&date={date}")
    if not j or j.get("rc") not in (0, "0", None) or not j.get("data"):
        return []
    return j["data"].get("pool") or []


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
    # T+1 约 2 倍量（1.5~3.0x）且断板（未再涨停）
    i1 = iT + 1
    r1 = V(i1) / vT
    if not (1.3 <= r1 <= 3.5):
        return None
    if pctchg(i1) >= th:
        return None
    # T+2 起缩量递减（T+2 < T+1 必查；若存在 T+3 则 T+3 < T+2）
    i2 = iT + 2
    if i2 > n - 1 or not (0 < V(i2) < V(i1)):
        return None
    i3 = iT + 3
    if i3 <= n - 1 and not (0 < V(i3) < V(i2)):
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
    ap.add_argument("--limit", type=int, default=80, help="最多核验的候选股数（默认80）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（供自动化消费）")
    args = ap.parse_args()

    dates = recent_trade_dates(args.days)
    # 1) 收集候选：code -> {name, hybk, zt_dates:[...]}
    cand = {}
    valid_dates = []
    for d in dates:
        pool = fetch_zt_pool(d)
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
    for code, rec in cands:
        sym = sina_symbol(code)
        if not sym:
            continue
        kl = get_json_urllib(SINA_KLINE.format(sym=sym))
        if not isinstance(kl, list) or not kl:
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
        time.sleep(0.25)  # 新浪限频保护

    # 按板块分组
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

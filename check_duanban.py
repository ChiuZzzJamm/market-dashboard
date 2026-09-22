#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""断板反包形态真实核验（确定性脚本，供自动化 AI 生成四类标的时调用）

形态规则（用户定稿口径，R70 放宽）：
  1-5 个交易日前涨停(T) → 涨停次日(T+1) 1.1~3.5 倍量且断板（未续板）
  → T+2 缩量（< T+1）；T+3（若存在）低于断板日量即可（允许小幅反复，不要求逐日递减）
  → 收盘不破 T 日低点、不破 5 日线（MA5，容差 1%）

数据源（零 MCP，纯 HTTP）：
  - 东财 push2ex getTopicZTPool 涨停池（含 hybk 行业板块字段，date=YYYYMMDD 无横线）
  - K 线：腾讯 ifzq fqkline 为主源（web.ifzq.gtimg.cn，独立域名、限频少），
    新浪 CN_MarketData.getKLineData 为备用源（限频时兜底），均 scale=240 日线

用法：
  python3 check_duanban.py                    # 核验最近 5 个交易日，文本输出
  python3 check_duanban.py --days 5 --limit 80 --json

输出：
  按东财行业板块(hybk)分组的达标候选；每只含 code/name/hybk/ztDate/form。
  form 示例: "9/15涨停→次2.1倍量断板→2日缩量·站稳MA5"
  退出码：恒为 0；无达标时输出空列表（调用方如实写「断板反包(替代)」，禁凑数）。

--module 模式（断板反包独立模块数据源，R87 新增）：
  输出 {confirmed(确认池), watching(观察池), excluded} 结构：
  - 确认池：走完「涨停→放量断板→缩量→不破T日低点与MA5」全流程；
  - 观察池：形态进行中、只差最后一根确认K线：
      · 待缩量（断板次日收盘，放量断板达标、未破T日低点，待缩量K线）
      · 待企稳（已现缩量、未破T日低点，仅差收复MA5的确认K线）
  - 利好一致性闸门：与 data.js 要闻/AI预测交叉核验——标的或其板块被任何
    利空要闻/AI预测看空者硬排除；无任何利好依据者进 excluded 供 AI 复核，
    不直接进池（模块内所有标的必须有相关利好新闻/内容/AI预测看涨支撑）。
  每只附带近 14 根日K [day,open,close,high,low,volume]（前端画K线图）。
  probability/probNote 留空，由自动化 AI 基于形态完成度+题材热度填写后经
  merge_duanban.py 合并进 data.js 的 duanban 字段。
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


def fetch_kline_tencent(code, retries=3, gap=1):
    """主用 K 线源（腾讯 ifzq），与新浪独立域名，限频概率更低（R81 起设为主源）。
    带重试，内联解析；返回统一 schema [{day,open,high,low,close,volume}] 或 None。
    check_form 只用成交量比值，同源内单位一致即可，腾讯与新浪切换不影响形态判定。"""
    tcode = _tencent_code(code)
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
           + tcode + ",day,,,40,qfq")
    for i in range(retries + 1):
        raw = curl_text(url, timeout=15)
        if raw.strip():
            try:
                j = json.loads(raw)
            except Exception:
                j = None
            if isinstance(j, dict) and j.get("code") in (0, "0", None) and j.get("data"):
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
                if len(out) >= 8:
                    return out
        if i < retries:
            time.sleep(gap)
    return None


def get_kline(code, sym):
    """带缓存 + 双源容错的 K 线获取（R81：腾讯 ifzq 主源，新浪备用，抵御新浪限频）：
       当日缓存命中（已含最新K线）→ 腾讯主源 → 新浪备用源 → 过期缓存兜底。
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
    # 主源：腾讯 ifzq（独立域名，限频更少，最稳）
    kl = fetch_kline_tencent(code)
    if isinstance(kl, list) and kl:
        try:
            json.dump({"ts": time.time(), "bars": kl},
                      open(cp, "w", encoding="utf-8"))
        except Exception:
            pass
        return kl
    # 备用源：新浪（限频窗口兜底）
    kl2 = get_json_urllib(SINA_KLINE.format(sym=sym), retries=3, gap=1)
    if isinstance(kl2, list) and kl2:
        try:
            json.dump({"ts": time.time(), "bars": kl2},
                      open(cp, "w", encoding="utf-8"))
        except Exception:
            pass
        return kl2
    if fresh or stale:
        print(f"[warn] {code} 腾讯/新浪均失败，启用本地缓存兜底", file=sys.stderr)
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


def eval_stage(code, kline, zt_date_iso):
    """对单只候选做分池评估（R87）：
    - confirmed：全流程达标（直接复用 check_form 结论，口径不变）；
    - watch/待缩量：days_ago==1，放量断板达标、未破T日低点，待缩量K线；
    - watch/待企稳：days_ago 2~4，断板后已现缩量、未破T日低点，
      仅 MA5 条件未满足——只差收复MA5的最后一根确认K线。
    返回 {pool, stage, form, days_ago} 或 None（非候选/形态已失效）。"""
    n = len(kline)
    if n < 8:
        return None
    iT = next((i for i, k in enumerate(kline) if k.get("day") == zt_date_iso), None)
    if iT is None:
        return None
    days_ago = n - 1 - iT
    if not (1 <= days_ago <= 5):
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
    if pctchg(iT) < th:
        return None
    vT = V(iT)
    if vT <= 0:
        return None
    i1 = iT + 1
    if i1 > n - 1:
        return None
    r1 = V(i1) / vT
    if not (1.1 <= r1 <= 3.5) or pctchg(i1) >= th:
        return None
    lowT = float(kline[iT].get("low") or 0)
    if lowT > 0 and min(C(i) for i in range(i1, n)) < lowT * 0.98:
        return None  # 已破T日低点，形态失效（确认/观察两池均排除）

    zt_md = f"{zt_date_iso[5:7]}/{zt_date_iso[8:10]}"
    # 确认池优先：全流程达标（check_form 原口径）
    form_full = check_form(code, kline, zt_date_iso)
    if form_full:
        return {"pool": "confirmed", "stage": "全流程达标",
                "form": form_full, "days_ago": days_ago}
    if days_ago == 1:
        return {"pool": "watch", "stage": "待缩量",
                "form": f"{zt_md}涨停→次{r1:.1f}倍量断板·待缩量",
                "days_ago": 1}
    if 2 <= days_ago <= 4:
        shrunk = any(0 < V(i) < V(i1) for i in range(iT + 2, n))
        if shrunk:
            ma5 = sum(C(i) for i in range(n - 5, n)) / 5.0
            if C(n - 1) < ma5 * 0.97:  # 仅差收复MA5
                return {"pool": "watch", "stage": "待企稳",
                        "form": f"{zt_md}涨停→次{r1:.1f}倍量断板→已缩量·待收复MA5",
                        "days_ago": days_ago}
    return None


# ---------- R87: --module 模式（双池 + 利好一致性闸门） ----------

def load_dashboard(path):
    s = open(path, encoding="utf-8").read()
    i = s.index("{")
    j = s.rindex("}")
    return json.loads(s[i:j + 1])


def collect_sentiment(D):
    """从看板数据收集利好/利空线索（个股级 + 板块关键词级）。
    返回 dict：bull_codes {code:[{src,title}]} 个股级利好依据；
    bull_secs [(kw, ref)] 板块关键词利好；bear_codes set 个股级利空；
    bear_secs [kw] 板块级利空。"""
    B = {"bull_codes": {}, "bull_secs": [], "bear_codes": set(), "bear_secs": []}

    def add_bull_code(code, src, title):
        code = str(code or "")
        if not re.fullmatch(r"\d{6}", code):
            return
        lst = B["bull_codes"].setdefault(code, [])
        if not any(r.get("src") == src for r in lst):
            lst.append({"src": src, "title": title})

    def add_bull_sec(kw, src, title):
        kw = str(kw or "").strip()
        if len(kw) >= 2 and all(kw != k for k, _ in B["bull_secs"]):
            B["bull_secs"].append((kw, {"src": src, "title": title}))

    def bear_item(it):
        B["bear_secs"].append(str(it.get("sector") or ""))
        for im in it.get("impacts") or []:
            B["bear_secs"].append(str(im.get("theme") or ""))
            for st in im.get("stocks") or []:
                c = str(st.get("code") or "")
                if re.fullmatch(r"\d{6}", c):
                    B["bear_codes"].add(c)

    def walk(items, src, field):
        for it in items or []:
            direction = str(it.get("direction") or "")
            sec = str(it.get("sector") or "")
            # bearNews 落利空列即利空；其余节 direction==看跌 亦利空
            if field == "bearNews" or direction == "看跌":
                bear_item(it)
                continue
            # bullNews 落利好列即利好；macro/intl/bank 需显式 direction==看涨
            if field != "bullNews" and direction != "看涨":
                continue
            add_bull_sec(sec, src, sec)
            for im in it.get("impacts") or []:
                add_bull_sec(im.get("theme") or "", src, sec)
                for st in im.get("stocks") or []:
                    add_bull_code(st.get("code"), src, sec)

    for mkt in ("ashare", "us"):
        sec_data = D.get(mkt) or {}
        for field in ("bullNews", "bearNews", "macroNews", "intlNews", "bankViews"):
            walk(sec_data.get(field), f"{mkt}.{field}", field)

    ai = D.get("aiPrediction") or {}
    for s in ai.get("sectors") or []:
        d = str(s.get("direction") or "")
        sec = str(s.get("sector") or "")
        if ("承压" in d) or ("走弱" in d) or ("看跌" in d):
            B["bear_secs"].append(sec)
            for st in s.get("stocks") or []:
                c = str(st.get("code") or "")
                if re.fullmatch(r"\d{6}", c):
                    B["bear_codes"].add(c)
        elif d:
            add_bull_sec(sec, "aiPrediction", sec)
            for st in s.get("stocks") or []:
                add_bull_code(st.get("code"), "aiPrediction", sec)
    return B


def screen_entries(entries, D):
    """利好一致性闸门（模块内所有标的必须有相关利好新闻/内容/AI预测看涨支撑）。
    硬排除：个股被利空要闻/AI预测点名看空 / 所属板块关键词命中看空内容；
    无任何利好依据者进 excluded（不进池，供 AI 复核板块语义后取舍）。
    返回 (kept, excluded)；kept 每项附 bullRefs。"""
    B = collect_sentiment(D)
    kept, excluded = [], []
    for e in entries:
        code, hybk = str(e["code"]), str(e.get("hybk") or "")
        if code in B["bear_codes"]:
            excluded.append(dict(e, excludeReason="个股被利空要闻/AI预测看空点名"))
            continue
        hit_bear = next((kw for kw in B["bear_secs"]
                         if kw and len(kw) >= 2 and hybk and hybk in kw), None)
        if hit_bear:
            excluded.append(dict(e, excludeReason=f"板块「{hybk}」命中看空内容「{hit_bear}」"))
            continue
        refs = list(B["bull_codes"].get(code) or [])
        if not refs:
            kw_hit = next(((kw, ref) for kw, ref in B["bull_secs"]
                           if hybk and hybk in kw), None)
            if kw_hit:
                refs = [{"src": "sector-match", "title": kw_hit[0]}]
        if not refs:
            excluded.append(dict(e, excludeReason="无利好新闻/AI预测看涨依据（AI 可复核板块语义后取舍）"))
            continue
        kept.append(dict(e, bullRefs=refs))
    return kept, excluded


def build_module(pairs, D):
    """pairs: [{code,name,hybk,ztDate,stage_info,kl}] → 断板反包模块 dict。
    每只附近 14 根日K与当日涨跌幅；probability 留空由自动化 AI 填写。"""
    entries = []
    for p in pairs:
        kl = p["kl"][-14:]
        bars = []
        for b in kl:
            try:
                bars.append([str(b.get("day")),
                             round(float(b.get("open") or 0), 2),
                             round(float(b.get("close") or 0), 2),
                             round(float(b.get("high") or 0), 2),
                             round(float(b.get("low") or 0), 2),
                             int(float(b.get("volume") or 0))])
            except Exception:
                continue
        pct = 0.0
        if len(kl) >= 2 and float(kl[-2].get("close") or 0) > 0:
            pct = round((float(kl[-1].get("close") or 0) - float(kl[-2].get("close") or 0))
                        / float(kl[-2].get("close")) * 100, 2)
        entries.append({"code": p["code"], "name": p["name"],
                        "sector": p["hybk"] or "其他", "ztDate": p["ztDate"],
                        "stage": p["stage_info"]["stage"], "form": p["stage_info"]["form"],
                        "pct": pct, "probability": None, "probNote": "",
                        "kline": bars})
    kept, excluded = screen_entries(entries, D)
    confirmed = [e for e in kept if e["stage"] == "全流程达标"]
    watching = [e for e in kept if e["stage"] != "全流程达标"]
    # 观察池内更接近确认的（待企稳）排前
    watching.sort(key=lambda e: 0 if e["stage"] == "待企稳" else 1)
    return {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "确认池=走完「涨停→放量断板→缩量→不破T日低点与MA5」全流程；观察池=形态进行中、只差最后一根确认K线（待企稳＞待缩量）。全部标的经利好一致性闸门核验：须有利好要闻/AI预测看涨支撑，板块被看空者已剔除。probability（0-100 上涨概率）由自动化 AI 基于形态完成度+量价结构+题材热度填写。",
        "confirmed": confirmed,
        "watching": watching,
        "excluded": excluded,
    }


def eval_candidate(code, rec, kl):
    """对单候选的各涨停日做分池评估，取最近命中（确认池优先于观察池）。"""
    best = None
    for d in sorted(rec["zt"], reverse=True):
        iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        try:
            st = eval_stage(code, kl, iso)
        except Exception:
            st = None
        if not st:
            continue
        if best is None or (st["pool"] == "confirmed"
                            and best["stage_info"]["pool"] != "confirmed"):
            best = {"code": code, "name": rec["name"], "hybk": rec["hybk"],
                    "ztDate": iso, "stage_info": st, "kl": kl}
        if st["pool"] == "confirmed":
            break
    return best


def main():
    ap = argparse.ArgumentParser(description="断板反包形态真实核验")
    ap.add_argument("--days", type=int, default=5, help="回看交易日数（默认5）")
    ap.add_argument("--limit", type=int, default=300, help="最多核验的候选股数（默认300，覆盖全池勿低于250）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（供自动化消费）")
    ap.add_argument("--module", action="store_true",
                    help="输出断板反包模块 JSON（确认池/观察池双池+K线+利好一致性筛选，R87）")
    ap.add_argument("--dashboard", default=None,
                    help="看板数据文件路径（--module 一致性核验用，默认同目录 data.js）")
    ap.add_argument("--out", default=None, help="模块 JSON 同时写入该文件（供 AI 补概率后 merge）")
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
        if args.module:
            mod = {"generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                   "note": "涨停池为空或不可用，无候选",
                   "confirmed": [], "watching": [], "excluded": []}
            print(json.dumps(mod, ensure_ascii=False, indent=1))
            return
        print("[]" if args.json else "无达标候选（涨停池为空或不可用）")
        return

    # 最近涨停优先核验
    cands = sorted(cand.items(), key=lambda kv: max(kv[1]["zt"]), reverse=True)[:args.limit]

    results = []   # 确认池记录（--json/文本口径，历史不变）
    pairs = []     # 双池记录（--module 口径：含观察池）
    failed = []  # K线拉取失败的候选（多为新浪限频），冷却后二轮重试（R70）

    def run_one(code, rec, kl, retry_gap):
        """对单候选评估：--module 双池入 pairs，确认池同步入 results（历史口径）。"""
        hit = eval_candidate(code, rec, kl)
        if hit:
            pairs.append(hit)
            if hit["stage_info"]["pool"] == "confirmed":
                results.append({"code": hit["code"], "name": hit["name"],
                                "hybk": hit["hybk"], "ztDate": hit["ztDate"],
                                "form": hit["stage_info"]["form"]})
        time.sleep(retry_gap)

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
        run_one(code, rec, kl, 0.4)  # 新浪限频保护（R70: 0.25→0.4）

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
            run_one(code, rec, kl, 0.6)

    # R77：标的口径——只保留沪深主板(60/00 开头)，剔除科创板(688/689)、
    # 创业板(300/301/302)与北交所(4/8/92 开头)——用户要求提供标的均非科创/创业板
    results = [r for r in results
               if str(r["code"]).startswith(("60", "00"))]
    pairs = [p for p in pairs if str(p["code"]).startswith(("60", "00"))]

    if args.module:
        # R87 断板反包独立模块：双池 + 利好一致性闸门（对照 data.js 要闻/AI预测）
        dash = args.dashboard or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data.js")
        try:
            D = load_dashboard(dash)
        except Exception as e:
            print(f"[warn] data.js 解析失败（{e}），跳过一致性闸门——全部入池不加 bullRefs",
                  file=sys.stderr)
            mod = {"generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                   "note": "（一致性闸门未运行：data.js 不可用）",
                   "confirmed": [{"code": p["code"], "name": p["name"],
                                  "sector": p["hybk"] or "其他", "ztDate": p["ztDate"],
                                  "stage": p["stage_info"]["stage"],
                                  "form": p["stage_info"]["form"], "pct": 0,
                                  "probability": None, "probNote": "",
                                  "bullRefs": [], "kline": []}
                                 for p in pairs if p["stage_info"]["pool"] == "confirmed"],
                   "watching": [{"code": p["code"], "name": p["name"],
                                 "sector": p["hybk"] or "其他", "ztDate": p["ztDate"],
                                 "stage": p["stage_info"]["stage"],
                                 "form": p["stage_info"]["form"], "pct": 0,
                                 "probability": None, "probNote": "",
                                 "bullRefs": [], "kline": []}
                                for p in pairs if p["stage_info"]["pool"] != "confirmed"],
                   "excluded": []}
        else:
            mod = build_module(pairs, D)
        out = json.dumps(mod, ensure_ascii=False, indent=1)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(out + "\n")
            print(f"[module] 草稿已写入 {args.out}", file=sys.stderr)
        print(f"[module] 确认池 {len(mod['confirmed'])} 只 / 观察池 {len(mod['watching'])} 只"
              f" / 闸门排除 {len(mod['excluded'])} 只（详情见 excluded，供 AI 复核）",
              file=sys.stderr)
        for e in mod["excluded"]:
            print(f"[module] 排除 {e['code']} {e['name']}：{e['excludeReason']}", file=sys.stderr)
        print(out)
        return

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

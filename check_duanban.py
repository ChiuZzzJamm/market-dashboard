#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""断板反包形态真实核验（确定性脚本，供自动化 AI 生成四类标的时调用）

形态规则（用户定稿口径，R70 放宽）：
  1-5 个交易日前涨停(T) → 涨停次日(T+1) 1.1~3.5 倍量且断板（未续板）
  → T+2 缩量（< T+1）；T+3（若存在）低于断板日量即可（允许小幅反复，不要求逐日递减）
  → 收盘不破 T 日低点（容差 2%，LOW_TOL）、不破 5 日线（MA5，容差 3%，MA5_TOL）

数据源（零 MCP，纯 HTTP）：
  - 涨停池：同花顺 limit_up_pool 为主源（data.10jqka.com.cn/dataapi/limit_up/limit_up_pool，
    date=YYYYMMDD 无横线）；失败回退东财 push2ex getTopicZTPool。
    同花顺涨停池无申万 hybk 字段，hybk 由东财涨停池 best-effort 映射补充（不影响主源成败）。
  - K 线：腾讯 ifzq fqkline 为主源（web.ifzq.gtimg.cn，独立域名、限频少），
    新浪 CN_MarketData.getKLineData 为备用源（限频时兜底），均 scale=240 日线
  - 板块数据（R98）：同花顺 thshy 行业页面（50个大类行业）+ v4/line 历史K线；
    申万→同花顺行业映射失败时回退东财/申万口径

用法：
  python3 check_duanban.py                    # 核验最近 5 个交易日，文本输出
  python3 check_duanban.py --days 5 --limit 80 --json

输出：
  按行业板块分组的达标候选；每只含 code/name/sector/ztDate/form。
  form 示例: "9/15涨停→次2.1倍量断板→2日缩量·站稳MA5"
  退出码：恒为 0；无达标时输出空列表（调用方如实写「断板反包(替代)」，禁凑数）。

--module 模式（断板反包独立模块数据源，R87 新增，R98 板块口径升级）：
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
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# R100z6：THS 抓取收口到 common（原 curl_ths_text/curl_ths_json/parse_ths_industries/
# fetch_ths_industries 双副本合一，与 update_ashare_sectors 共用单一来源）
from common import (ths_json as curl_ths_json, fetch_ths_industries,
                    is_trade_day)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
EX = "https://push2ex.eastmoney.com"
UT_ZT = "7eea3edcaed734bea9cbfc24409ed989"
SINA_KLINE = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen=40")

# R100z52：形态容差常量（check_form 用）。注释与实现曾长期对不上
# （文档写 MA5 容差 1%、T 日低点容差 0.5%，实现是 ×0.97 / ×0.98）；
# 现在只有这一处定义，文档、注释、实现共用同一数字，改口径只改这里。
MA5_TOL = 0.97      # 收盘 ≥ MA5 × 0.97（容差 3%）才判「站稳 MA5」
LOW_TOL = 0.98      # 收盘 < T 日低点 × 0.98（容差 2%）才判「破 T 日低点」

# ---------------- 断板反包口径（2026-10-26 定稿） ----------------
# 出处：全市场 18,225 笔样本回测（`duanban_study.py` / `duanban_extra.py` /
# `rule_final_check.py`），训练 2024-01~2025-12、样本外 2026-01~ 分开验。
# 加这三个条件前后（zt_low 锚、止盈 5%、持有 10 日）：
#
#   口径                          全样本      训练      样本外     日均只数
#   改前（只看形态）              +0.060%   +0.160%    −0.209%     20.05
#   本口径（三条全开）            +0.763%   +0.712%    +0.874%      2.35
#
# 逐条边际贡献（TP5/H10 基线 +0.144%）：市值≥100 亿 +0.178%、启动前20日≤10% +0.310%
# → 是这两个把期望从 0 拉到正；上影线/首板/断板日跌幅再叠加只有 +0.001~+0.025，
# 且样本量掉一半，**不加**。
#
# 两个反直觉但实测的点，别「好心」改回去：
#   ① 距 MA5 越远越好：0~2% 只有 45.4%/+0.018%，>5% 是 68.6%/+1.078%。
#      刚站上 MA5 就买是弱信号——那是「还没证明有承接」。
#   ② 收紧止损全线更差（wash_low 48.6% vs zt_low 65.7%）：断板后日内波动大，
#      紧止损只是提前打掉会回来的票。
PRE20_MAX = 10.0    # 启动前 20 个交易日涨幅上限（%）——涨过了就不是「启动」是「延续」
MA5_GAP_MIN = 2.0   # 收盘相对 MA5 的最小溢价（%）——刚贴住 MA5 不算有效承接
# ---- 市值带：上限 400 亿，**下限已于 2026-10-28 21:5x 按要求放开** ----
# 放开是**产品决定，不是回测结论**（用户 2026-10-28 21:5x 明确「直接放开到 400 亿以内」）。
# 代价必须留在这里，别让下一个人以为这是优化过的结果：
#   只留上限 ≤400 亿（无下限）：7,859 笔 / 全样本 +0.096%? / **样本外 +0.058%** / 盈亏比 1.02
#   100~400 亿（原口径）：      1,890 笔 / 全样本 +0.732% / 样本外 +0.912% / 盈亏比 1.37
# 也就是说这条路在样本外几乎不赚钱。代价换来的是「形态成立的小盘票也能进池」——
# 用户要的是形态本身，接受这个取舍。**要回滚只改 CAP_MIN 一个数**（回 100.0），代码不用动。
# 另：放开下限后 `mcapCut` 里 side='lo' 的条目会归零，前端那枚「低于下限」徽章成了死代码，
# 留着不删——回滚时不用改前端。前端靠 `duanban.mcapBand` 判「这批数据里 lo 还算不算数」
# （R100z274），所以真正回滚就是改这一个数，其余全自动跟。 
CAP_MIN = 0.0       # 流通市值下限（亿元）——0 表示不设下限（2026-10-28 放开，见上）
CAP_MAX = 400.0     # 流通市值上限（亿元）——R100z250（用户 2026-10-28 定「400 亿以内」）
#
# 上限这一条的实测依据（现行条件 + 只动上限，zt_low / TP5 / H10，18,225 笔）：
#
#   市值区间        笔数    全样本     训练      样本外    盈亏比
#   100 ~ ∞        2,226   +0.763%  +0.712%   +0.874%   1.35   ← 加上限前
#   100 ~ 600 亿   2,049   +0.740%  +0.667%   +0.906%   1.37
#   100 ~ 400 亿   1,890   +0.732%  +0.654%   +0.912%   1.37   ← 原口径（下限放开前）
#   100 ~ 300 亿   1,721   +0.752%  +0.680%   +0.919%   1.37
#
# 样本外小幅变好（+0.038pp）、样本掉 15%，属于「削不掉收益的减法」，不是靠它提收益。
# 反面证据在分档里：600 亿+ 全样本 +1.029% 但样本外只有 +0.580%（不稳），
# 300~400 亿全样本最差（+0.533%）——大盘断板的弹性撑不起 10 日持有。
#
# ⚠️ 口径差异（别拿两个数直接比）：回测的 mcap 取 `.kline_hist` 的**总市值**，
# 这里 f[44] 是**流通市值**，后者恒 ≤ 前者，所以线上这道上限实际比回测略松。
# 实测 100~∞ 与 100~400 的差只有 0.038pp，这点口径漂移不影响结论。


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
           + tcode + ",day,,,70,qfq")
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
    """最近 n 个「交易日」日期（YYYYMMDD，降序），节假日感知。

    R100z51：原实现只判 `d.weekday() < 5`，把国庆/春节长假整段数成交易日。
    10-08 首跑时 recent_trade_dates(5) 会返回 ['20261007','20261006','20261005',
    '20261003','20261002'] —— 5 天全部落在 10-01~10-07 休市区间，
    而 fetch_zt_pool 对休市日返回 []（不是 None）→ 不置 any_fetch_failed →
    绕过 R91m「涨停池不可达则保留既有模块」闸门 → 写出 confirmed/watch 双空模块，
    再被 merge_duanban.py:188 `D["duanban"]=mod` 覆盖，把已部署的断板池清空。
    改为复用 common.is_trade_day（与 common.trading_days_between 同一日历口径）。
    """
    out, d = [], datetime.now()
    while len(out) < n:
        d -= timedelta(days=1)
        if is_trade_day(d):
            out.append(d.strftime("%Y%m%d"))
    return out


# ---------- R99i: 涨停池（主源=同花顺 limit_up_pool，兜底=东财 push2ex） ----------
THS_LIMITUP_API = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"

# 同花顺 limit_up_pool 历史日期用 YYYYMMDD（无横线）；内容含 code/name/change_rate/
# high_days(连板描述)/reason_type，但不含申万 hybk，hybk 由东财涨停池 best-effort 补。
THS_LIMITUP_FIELDS = "199112,10,9001,330323,330324,330325,9002,330329,133971"


def _ths_lbc_of(it):
    """连板天数：从 high_days（如 '3天3板'）提取首日数字；'首板'→1。"""
    hd = str(it.get("high_days") or "")
    for pat in (r"(\d+)\s*天", r"(\d+)\s*连板"):
        m = re.search(pat, hd)
        if m:
            return int(m.group(1))
    return 1


def fetch_ths_zt_pool(date):
    """同花顺涨停池（主源）。成功返回 [{c,n,hybk,lbc,reason,pct}]（hybk 初始为空），
    失败（限频/网络/空响应）返回 None。limit 上限 200，分页拉全。"""
    out = []
    seen = 0
    for page in range(1, 10):
        d = curl_ths_json(
            f"{THS_LIMITUP_API}?page={page}&limit=200&field={THS_LIMITUP_FIELDS}"
            f"&filter=HS,GEM2STAR&order_field=330324&order_type=0&date={date}")
        if not d or d.get("status_code") not in (0, None) \
                or not (d.get("data") or {}).get("info"):
            # 首页就失败 → 主源失败；后续页为空视为拉完
            if page == 1:
                return None
            break
        info = d["data"]["info"]
        for it in info:
            code = str(it.get("code") or "")
            if not re.fullmatch(r"\d{6}", code):
                continue
            pct = it.get("change_rate")
            try:
                pct = float(pct) if pct not in (None, "") else None
            except Exception:
                pct = None
            out.append({
                "c": code,
                "n": str(it.get("name") or code),
                "hybk": "",  # 同花顺无申万行业字段，下方由东财 best-effort 补充
                "lbc": _ths_lbc_of(it),
                "reason": str(it.get("reason_type") or ""),
                "pct": pct,
            })
        seen += len(info)
        total = ((d.get("data") or {}).get("page") or {}).get("total")
        if not total or seen >= int(total):
            break
    return out


def fetch_zt_pool_em(date):
    """东财 push2ex 涨停池（兜底 / hybk 映射用）。失败返回 None（限频/网络），
    空池返回 []（节假日等）。失败自动重试 2 次（退避 3s/6s）。"""
    for attempt in range(3):
        j = get_json_curl(f"{EX}/getTopicZTPool?ut={UT_ZT}&dpt=wz.ztzt"
                          f"&Pageindex=0&pagesize=600&sort=fbt%3Aasc&date={date}")
        if j is not None and j.get("rc") in (0, "0", None):
            return (j.get("data") or {}).get("pool") or []
        if attempt < 2:
            time.sleep(3 * (attempt + 1))
    return None


def _em_hybk_map(date):
    """东财涨停池 -> {code: hybk(申万)} 映射（best-effort，失败返回 {}）。"""
    pool = fetch_zt_pool_em(date)
    if not pool:
        return {}
    m = {}
    for p in pool:
        code = str(p.get("c") or "")
        if code:
            m[code] = p.get("hybk") or ""
    return m


def fetch_zt_pool(date):
    """涨停池：主源=同花顺 limit_up_pool；失败回退东财 push2ex（含 hybk）。
    同花顺无申万 hybk，主源成功时也 best-effort 用东财映射补 hybk（失败不影响主源）。
    区分失败与空池：失败返回 None，空池返回 []。"""
    items = fetch_ths_zt_pool(date)
    if items is None:
        # 主源失败 → 回退东财整池（自带 hybk）
        return fetch_zt_pool_em(date)
    # 主源成功：补充 hybk 映射（best-effort）
    try:
        hmap = _em_hybk_map(date)
    except Exception:
        hmap = {}
    if hmap:
        for it in items:
            if not it.get("hybk"):
                it["hybk"] = hmap.get(it["c"], "")
    return items


# ---------- R91i: 板块实时涨幅 + 涨停日板块涨幅（东财行业板块，与 hybk 同口径） ----------
DELAY = "https://push2delay.eastmoney.com/api/qt/clist/get"
HIS = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EM_UT = "fa5c3db4f1ca16c5b6e3b9a3f3ae3dfa"

# ---------- R98: 同花顺行业板块口径（断板池板块相关数据） ----------
# R100z6：curl_ths_text/parse_ths_industries/fetch_ths_industries 已收口到 common
# （文件头部 from common import），THS_INDUSTRY_URL 一并由 common.THS_URL 提供。
# R100z60 清理：原 THS_KLINE_REFERER 在这之后零引用（收口到 common 后残留的死常量），删掉。

_board_map_cache = None
_board_pct_hist_cache = {}
_THS_BOARD_CACHE = None
_THS_KLINE_CACHE = {}


def fetch_ths_board_map():
    """同花顺行业板块 {name: {code, pct}}。"""
    global _THS_BOARD_CACHE
    if _THS_BOARD_CACHE is not None:
        return _THS_BOARD_CACHE
    ths = fetch_ths_industries()
    _THS_BOARD_CACHE = {b["name"]: b for b in ths}
    return _THS_BOARD_CACHE


def fetch_ths_board_pct_on(ths_code, date_iso):
    """同花顺行业板块在 date_iso(涨停日) 当日涨幅%。失败返回 None。"""
    if not ths_code or not date_iso:
        # date_iso 为空（ztDate 缺失）时必须早退：否则下方 .replace 抛
        # AttributeError 把整条订正链带崩（R100z170 实测踩过）
        return None
    cache_key = f"{ths_code}_{date_iso}"
    if cache_key in _THS_KLINE_CACHE:
        return _THS_KLINE_CACHE[cache_key]
    url = f"https://d.10jqka.com.cn/v4/line/bk_{ths_code}/01/last.js"
    raw = curl_text(url, timeout=15)
    if not raw.strip():
        return None
    m = re.search(r'\((\{.*\})\)', raw)
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
    except Exception:
        return None
    data = d.get("data", "")
    bars = data.split(";")
    hist = {}
    prev_close = None
    for bar in bars:
        parts = bar.split(",")
        if len(parts) < 5:
            continue
        day = parts[0]
        try:
            close = float(parts[4])
        except Exception:
            continue
        if prev_close and prev_close > 0:
            hist[day] = round((close / prev_close - 1) * 100, 2)
        prev_close = close
    _THS_KLINE_CACHE[ths_code] = hist
    return hist.get(date_iso.replace("-", ""))


def fetch_ths_kline_full(ths_code, days=30):
    """获取同花顺行业板块近 N 根日K线。
    返回 [[date(YYYY-MM-DD), open, close, high, low, volume], ...] 或 None。"""
    if not ths_code:
        return None
    url = f"https://d.10jqka.com.cn/v4/line/bk_{ths_code}/01/last.js"
    raw = curl_text(url, timeout=15)
    if not raw.strip():
        return None
    m = re.search(r'\((\{.*\})\)', raw)
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
    except Exception:
        return None
    data = d.get("data", "")
    bars = data.split(";")
    out = []
    for bar in bars:
        parts = bar.split(",")
        if len(parts) < 6:
            continue
        day = parts[0]
        if len(day) == 8:
            day = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        try:
            o = float(parts[1])
            h = float(parts[2])
            l = float(parts[3])
            c = float(parts[4])
            v = float(parts[5])
        except Exception:
            continue
        out.append([day, o, c, h, l, v])
    if len(out) < 2:
        return None
    return out[-days:] if len(out) > days else out


def match_ths_industry(hybk, ths_names):
    """申万行业名 → 同花顺行业名（前缀匹配+特殊映射）。失败返回 None。"""
    if not hybk or not ths_names:
        return None
    clean = re.sub(r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]$', '', hybk)
    if clean in ths_names:
        return clean
    if hybk in ths_names:
        return hybk
    SPECIAL = {
        # R100z5d：同花顺行业一览表分页修复后（90 行业抓全）仍存在的命名差异映射
        "林业Ⅱ": "种植业与林业", "航运港口": "港口航运", "饮料乳品": "饮料制造",
        "煤炭开采": "煤炭开采加工", "一般零售": "零售", "装修装饰": "建筑装饰",
        "水泥": "建筑材料", "基础建设": "建筑装饰", "饲料": "养殖业",
        "出版": "文化传媒", "广告营销": "文化传媒",
        "证券Ⅱ": "证券", "装修装饰Ⅱ": "建筑装饰", "其他电子Ⅱ": "其他电子",
        "军工电子Ⅱ": "军工电子", "商用车": "汽车整车", "休闲食品": "食品加工制造",
        "旅游及景区": "旅游及酒店", "风电设备": "光伏设备", "电机Ⅱ": "电机",
        "装修建材": "建筑材料", "塑料": "塑料制品", "家居用品": "家居用品",
        "化学制药": "化学制药", "汽车零部件": "汽车零部件", "化学制品": "化学制品",
        "通用设备": "通用设备", "医疗服务": "医疗服务", "计算机设备": "计算机设备",
        "软件开发": "计算机设备", "专用设备": "专用设备", "化学原料": "化学原料",
        "光伏设备": "光伏设备", "半导体": "半导体", "消费电子": "消费电子",
        "光学光电子": "光学光电子", "医疗器械": "医疗器械", "电池": "电池",
        "白酒": "白酒", "小家电": "小家电", "通信设备": "通信设备", "钢铁": "钢铁",
        "证券": "证券", "汽车整车": "汽车整车", "银行": "银行",
        "建筑装饰": "建筑装饰", "房地产": "房地产", "元件": "元件",
        "房地产开发": "房地产",  # R100z6：同花顺行业已改名「房地产开发」→「房地产」
        "生物制品": "生物制品", "自动化设备": "自动化设备", "环境治理": "环境治理",
        "橡胶制品": "橡胶制品", "机场航运": "机场航运", "环保设备": "环保设备",
        "公路铁路运输": "公路铁路运输", "食品加工制造": "食品加工制造",
        "小金属": "金属新材料", "工业金属": "金属新材料", "金属新材料": "金属新材料",
        "轨交设备": "轨交设备", "工程机械": "工程机械",
        "汽车服务及其他": "汽车服务及其他", "非金属材料": "非金属材料",
        "厨卫电器": "厨卫电器", "电子化学品": "电子化学品", "其他电子": "其他电子",
        "其他社会服务": "其他社会服务", "塑料制品": "塑料制品",
        "其他电源设备": "其他电源设备", "化学纤维": "化学纤维",
        "白色家电": "白色家电", "建筑材料": "建筑材料", "军工电子": "军工电子",
        "旅游及酒店": "旅游及酒店", "饮料制造": "饮料制造",
        # R100z170：申万二级 → 同花顺一级的语义归并（原来缺这 5 条，整只标的连板块涨幅都取不到）。
        # 依据申万行业分类：机场归「航空机场」、按摩椅/清洁电器归「小家电」、
        # 专业工程与工程咨询服务业归「建筑装饰」、家电零部件随整机归「小家电」。
        "航空机场": "机场航运", "其他家电": "小家电", "专业工程": "建筑装饰",
        "工程咨询": "建筑装饰", "家电零部": "小家电",
        # R100z170 补：申万国防军工二级「地面兵装」→ 同花顺「军工装备」；
        # 申万传媒二级「数字媒体」→ 同花顺「文化传媒」（同花顺 90 一级无对应细分，
        # 归入其上位大类）。缺这两条时二者只能退回东财板块口径，且东财板块日K
        # 接口已失效（实测 BK1382/BK1221/BK1471 全返回 None），涨停日板块涨幅长期空。
        "地面兵装": "军工装备", "数字媒体": "文化传媒",
        # R100z179：申万传媒二级「电视广播」同属传媒大类，同花顺 90 一级归「文化传媒」。
        # 缺这条时 000156 华数传媒 只能落到东财兜底，而东财板块日K 已失效 → 板块涨幅「—」。
        "电视广播": "文化传媒",
        # R100z180：申万「炼化及贸易」→ 同花顺同名口径叫「石油加工贸易」；
        # 东财涨停池给的 hybk 常截成「炼化及贸」（4 字），两个键都要写，
        # 否则 600506 统一股份 整只标的板块涨幅取不到（story 只能写「板块当日数据暂缺」）。
        "炼化及贸易": "石油加工贸易", "炼化及贸": "石油加工贸易",
    }
    if clean in SPECIAL and SPECIAL[clean] in ths_names:
        return SPECIAL[clean]
    if hybk in SPECIAL and SPECIAL[hybk] in ths_names:
        return SPECIAL[hybk]
    # R100z4k：前缀匹配取「最长」候选——防止当日抓取列表含被截断的行业名（如「计算机设」）
    # 抢先匹配、把截断名写进池内 sector（页面板块徽章随之被截断）。
    best = None
    for name in ths_names:
        if len(clean) >= 2 and (name.startswith(clean) or clean.startswith(name)):
            if best is None or len(name) > len(best):
                best = name
    return best


def fetch_board_map():
    """东财行业板块全量（m:90+t:2），返回 {name: {code, pct}}。hybk 取自同一数据源，名称可精确匹配。
    返回空字典表示抓取失败（本地沙箱可能不可达 push2delay），调用方按 None 处理。"""
    global _board_map_cache
    if _board_map_cache is not None:
        return _board_map_cache
    m = {}
    pn = 1
    while pn <= 12:
        url = (f"{DELAY}?pn={pn}&pz=500&po=1&np=1&fltt=2&invt=2&fid=f3"
               f"&fs=m:90%2Bt:2&fields=f12,f14,f3&ut={EM_UT}")
        j = get_json_curl(url, retries=2, gap=2)
        rows = (j.get("data") or {}).get("diff") or [] if j else []
        if not rows:
            break
        for r in rows:
            name = (r.get("f14") or "").strip()
            code = (r.get("f12") or "").strip()
            pct = r.get("f3")
            if name and code:
                m[name] = {"code": code,
                           "pct": float(pct) if pct not in (None, "") else None}
        if len(rows) < 500:
            break
        pn += 1
        time.sleep(0.3)
    _board_map_cache = m
    return m


def resolve_board(hybk, board_map):
    """hybk(东财行业板块名) → (bk_code, pct_today) 或 (None, None)。"""
    if not hybk:
        return None, None
    if hybk in board_map:
        b = board_map[hybk]
        return b["code"], b["pct"]
    # 截断名/别名兜底：东财涨停池 hybk 有时比板块列表名短
    for name, b in board_map.items():
        if name and hybk and (name.startswith(hybk) or hybk.startswith(name)) and len(hybk) >= 3:
            return b["code"], b["pct"]
    return None, None


def fetch_board_pct_on(bk_code, date_iso):
    """板块在 date_iso(涨停日) 当日涨幅%（收盘/前收-1）。失败返回 None。"""
    if not bk_code:
        return None
    hist = _board_pct_hist_cache.get(bk_code)
    if hist is not None:
        return hist.get(date_iso)
    url = (f"{HIS}?secid=90.{bk_code}&fields1=f1,f2,f3&fields2=f51,f53"
           f"&klt=101&fqt=0&beg=0&end=20500101&ut={EM_UT}")
    j = get_json_curl(url, retries=2, gap=2)
    kls = (((j or {}).get("data") or {}).get("klines") or []) if j else []
    hist = {}
    prev_close = None
    for kl in kls:
        parts = kl.split(",")
        if len(parts) < 3:
            continue
        day = parts[0]
        try:
            close = float(parts[1])
        except Exception:
            continue
        if prev_close and prev_close > 0:
            hist[day] = round((close / prev_close - 1) * 100, 2)
        prev_close = close
    _board_pct_hist_cache[bk_code] = hist
    return hist.get(date_iso)


# ---------- R91j: 近3日涨幅居前板块 TOP10（东财行业板块口径） ----------
# R100z60 清理：BOARD_KLINE_CACHE / HIS_HOSTS 定义后全项目零引用（CLIST_HOSTS 是活的，别一起删），
# 连同死常量一起删，避免下一个人再照着它接东财 push2his。
CLIST_HOSTS = ["https://push2delay.eastmoney.com",
               "https://82.push2.eastmoney.com",
               "https://push2.eastmoney.com"]


def _stock_clist_agg():
    """R91m 主源：clist 一次拉全量沪深A股，按 **申万行业（f100，与涨停池 hybk 同口径）**
    聚合等权平均（f3 今日涨幅 / f109 3日涨幅）。
    返回 {行业名: {"pct": 均值, "pct3": 均值, "n": 成分数}}，全部失败返回 None。"""
    for host in CLIST_HOSTS:
        rows, pn = [], 1
        while pn <= 8:
            url = (f"{host}/api/qt/clist/get?pn={pn}&pz=1000&po=1&np=1&fltt=2&invt=2"
                   f"&fid=f12&fs=m:0%2Bt:6%2Cm:0%2Bt:80%2Cm:1%2Bt:2%2Cm:1%2Bt:23"
                   f"&fields=f12,f14,f100,f3,f109&ut={EM_UT}")
            j = get_json_curl(url, retries=1, gap=2)
            page = (j.get("data") or {}).get("diff") or [] if j else []
            if not page:
                rows = []
                break
            rows.extend(page)
            if len(page) < 1000:
                break
            pn += 1
            time.sleep(0.3)
        if rows:
            agg = {}
            for r in rows:
                name = str(r.get("f100") or "").strip()
                if not name:
                    continue
                try:
                    pct = float(r.get("f3"))
                    pct3 = float(r.get("f109"))
                except (TypeError, ValueError):
                    continue
                a = agg.setdefault(name, {"sp": 0.0, "sp3": 0.0, "n": 0})
                a["sp"] += pct
                a["sp3"] += pct3
                a["n"] += 1
            if agg:
                return {k: {"pct": v["sp"] / v["n"], "pct3": v["sp3"] / v["n"], "n": v["n"]}
                        for k, v in agg.items()}
        time.sleep(1)  # 换下一主机前稍歇
    return None


MCAP_CACHE_DIR = "/tmp/duanban_mcap_cache"


def fetch_market_caps(codes, today=None):
    """候选票的**流通市值**（亿元）→ {code: mcap}。当日缓存，取不到返回已取到的部分。

    本口径的第三条（市值带 CAP_MIN~CAP_MAX）需要市值，而 data.js 里没有（`ashare` 只有
    指数/板块/资金，`fullScan.candidates` 只有代码）。所以按需拉腾讯行情
    `qt.gtimg.cn`——**只拉候选票**、60 只/批（与 `common.fetch_names` 同一源同一批长，
    实测可达），结果当日缓存到 `/tmp/duanban_mcap_cache/<日期>.json`。

    腾讯返回的 `~` 分隔字段：`f[44]` = 流通市值（亿元）、`f[45]` = 总市值（亿元）。
    已用 600900 长江电力（7306.21 亿）/ 000096 广聚能源（39.80 亿）实测核对过。

    ⚠️ 取不到时返回 {}/部分结果，调用方必须显式降级（跳过市值过滤 + 打标记），
    不能把「抓不到」当成「全部不达标」——那会把池子清空还看不出原因（24.16）。
    """
    import pathlib
    day = today or _today_str()
    try:
        pathlib.Path(MCAP_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    cache = os.path.join(MCAP_CACHE_DIR, f"{day}.json")
    out = {}
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                out = json.load(f) or {}
        except Exception:
            out = {}
    cs = [str(c).strip() for c in (codes or []) if str(c).strip()]
    todo = [c for c in dict.fromkeys(cs) if c not in out]
    if todo:
        for i in range(0, len(todo), 60):
            chunk = todo[i:i + 60]
            q = ",".join(("sh" if c.startswith(("6", "9")) else "sz") + c for c in chunk)
            url = "https://qt.gtimg.cn/q=" + q
            txt = None
            try:
                txt = curl_text(url, timeout=12)
            except Exception:
                txt = None
            if not txt:
                continue
            for line in txt.split(";"):
                if '="' not in line:
                    continue
                try:
                    f = line.split('="', 1)[1].rstrip('"').split("~")
                    c, mv = f[2].strip(), float(f[44])
                except (IndexError, TypeError, ValueError):
                    continue
                if len(c) == 6 and c.isdigit() and mv > 0:
                    out[c] = round(mv, 2)
            time.sleep(0.15)
        if out:
            try:
                with open(cache, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False)
            except Exception:
                pass
    return out


def filter_by_mcap(cands, caps, lo=None, hi=None):
    """按流通市值带筛候选（亿元）。→ (保留的 cands, 统计 dict)

    R100z250 从主流程抽出来，为的是能离线单测：`caps` 传当天的缓存文件就行，
    不必真去抓 420s 的 K 线。上下限分开计数是刻意的——只报一个「剔除 N 只」，
    用户看到池子变小根本判断不出是被「太小」还是「太大」卡的。

    · 取不到市值的票**保留**（宁松不误杀，24.16 显式降级）
    · `caps` 为空时由调用方决定整体跳过，这里不兜底

    R100z262（用户 2026-10-28 20:32「这些标的为什么不在断板反包确认池或陈学长池」）：
    原来这里只回计数、**不保留被剔的是谁**。市值过滤发生在抓 K 线之前，票一被剔就从
    系统里彻底消失——不进确认池、不进观察池、也**不进失效池**（失效池只收「曾经在池、
    今天滑出」的票）。结果是 24.105 自己写下的那句「被任何一条卡住的票在失效池可见并
    注明是哪一条」根本没有兑现：被市值卡掉的票在页面上不存在，用户只能看到「今日可下单
    收了它、确认池没有它」这个矛盾，而且**没有任何地方能查到原因**。
    所以现在把明细带出去（cutList），由主流程落进 duanban.mcapCut。
    """
    lo = CAP_MIN if lo is None else lo
    hi = CAP_MAX if hi is None else hi
    keep, cut_lo, cut_hi, no_cap, cut, non_main = [], 0, 0, 0, [], 0
    for code, rec in cands:
        mc = caps.get(code)
        if mc is None:
            no_cap += 1
            keep.append((code, rec))
        elif mc < lo or mc > hi:
            side = 'lo' if mc < lo else 'hi'
            if side == 'lo':
                cut_lo += 1
            else:
                cut_hi += 1
            # R100z262b：**名单只收沪深主板**。涨停池里混着 300/301/688 —— 它们本来就被
            # 「只做 60/00 主板」这条线挡在外面，跟市值门槛毫无关系。把它们列进
            # 「被市值门槛剔掉」的名单，等于编一个不存在的原因（用户会以为放开市值就能买）。
            # 计数仍含它们（cut_lo/cut_hi 与日志口径不变，不破坏计数守恒）。
            if not re.match(r"^(60|00)\d{4}$", str(code)):
                non_main += 1
                continue
            r = rec if isinstance(rec, dict) else {}
            # 保留 2 位：边界票（400.01 / 99.99）只留 1 位会四舍五入成 400.0/100.0，
            # 页面上就变成「400.0 亿却超过上限 400 亿」——看着像门槛算错了。
            cut.append({'code': str(code), 'name': r.get('name') or str(code),
                        'sector': r.get('hybk') or '', 'mcap': round(float(mc), 2),
                        'side': side})
        else:
            keep.append((code, rec))
    # 离门槛越近的越值得看（用户想知道「差多少」）：小盘按市值降序、大盘按市值升序，
    # 两组各在前，等价于「按与门槛的距离升序」。
    cut.sort(key=lambda x: abs(float(x['mcap']) - (lo if x['side'] == 'lo' else hi)))
    return keep, {"cut_lo": cut_lo, "cut_hi": cut_hi, "no_cap": no_cap,
                  "total": len(cands), "kept": len(keep), "lo": lo, "hi": hi,
                  "nonMain": non_main, "cutList": cut}


def prioritize_cut(cut, D, limit=60):
    """把「被市值剔除」的名单按**页面正在展示的票优先**排序，再截断。

    R100z263（2026-10-28 22:0x，修的是「明天就复发」的坑）：
    `filter_by_mcap` 的排序（离门槛由近到远）本身是对的——读者最想知道差一点点的
    那几只。但**单靠它，这次修复在第二天就失效**：用户在「今日可下单」卡片上看到的票，
    流通市值多在 30~60 亿，离 100 亿下限还有 40~70 亿，按距离排到 100 名开外，
    截断到前 40 条里一只都不剩 —— 名单照旧摆着，他要问的那几只照旧不在里面。
    2026-10-28 首版实测就是这个结果（45 条里对展示票命中 0 只，靠手工重排才救回来）。

    所以顺序改成**三级**：
      第 1 级「今日可下单」三源（用户正看着的那几张卡）
      → 第 2 级 其它正在展示的清单（精选／上一版池子）
      → 第 3 级 其余，同层内保持离门槛由近到远。
    展示源取 16:00 那一刻 data.js 里活着的清单——用户就是从这些地方点进来的。

    R100z263b（同日 22:2x 实测纠错）：**第一版取源取错了**。我按顶层 `timingExec`
    取，而它当天是空对象 `{}`；「今日可下单」那 8 张卡的数据其实在
    `duanban.star.timingExec.rows`（9:45 写）。结果是名单照旧按距离排、用户点名的
    8 只排到第 10~19 位 —— 修了跟没修一样，而且日志里看不出错（取的源不报错、只是空）。
    `ordPrep()` 的真实取数是三处：`duanban.star.timingExec.rows`（今早执行）／
    顶层 `timingPlan.orders`（16:00 生成的明日挂单）／`duanban.star.buyList`（盘中买点）。
    三处都得算，且**空源不许当"没有"**——必须三处都试（见 24.16 显式降级同理）。
    """
    if not cut:
        return []
    hot = set()    # 第 1 级：「今日可下单」三源——用户就是从这几张卡上点进来的
    warm = set()   # 第 2 级：其它正在展示的清单

    def _add(v, box):
        c = re.sub(r"\D", "", str(v or ""))
        if re.match(r"^\d{6}$", c):
            box.add(c)

    def _scan(seq, box):
        for r in (seq or []):
            if isinstance(r, dict):
                _add(r.get('code'), box)

    D = D if isinstance(D, dict) else {}
    _db = D.get('duanban') or {}
    _st = _db.get('star') or {}
    # ① 断板页「今日可下单」的真实来源（9:45 执行结果 + 盘中买点）→ 最高优先
    _scan((_st.get('timingExec') or {}).get('rows'), hot)
    _scan(_st.get('buyList'), hot)
    # ② 顶层明日挂单（16:00 生成）与顶层执行结果（历史结构，留着不吃亏）
    _scan((D.get('timingPlan') or {}).get('orders'), hot)
    _scan((D.get('timingExec') or {}).get('rows'), hot)
    # ③ 开盘精选项、断板页上一版池子 → 次优先
    _scan(_st.get('picks'), warm)
    _scan(_db.get('confirmed'), warm)
    _scan(_db.get('watching'), warm)
    _scan((D.get('star') or {}).get('buyList'), warm)
    _scan((D.get('star') or {}).get('picks'), warm)

    def _tier(x):
        c = str(x.get('code') or '')
        if c in hot:
            return 0
        if c in warm:
            return 1
        return 2

    # 同层内保持 filter_by_mcap 的「离门槛由近到远」，只按层做稳定排序
    return sorted(cut, key=_tier)[:limit]


def mcap_band_txt(lo, hi):
    """市值带的**唯一文案出口**。→ 带名，如「流通市值 400 亿以内」/「流通市值 100~400 亿」

    R100z266（2026-10-28 22:5x 放开下限到 400 亿以内）：同一道门槛在页面说明、运行日志、
    失效池原因三个地方各写一遍文案，放开下限后三处都得改——只要漏一处，用户就在某个角落
    看到「流通市值 0~400 亿」，读不出这是"没有下限"还是"下限是 0 亿"。抽成函数，
    改口径只改这里。

    · lo<=0 → 「400 亿以内」，明确是"没有下限"
    · lo>0  → 「100~400 亿」
    """
    lo = float(lo or 0)
    hi = float(hi or 0)
    return ('流通市值 %.0f 亿以内' % hi) if lo <= 0 else ('流通市值 %.0f~%.0f 亿' % (lo, hi))


def mcap_cut_detail(lo, cut_lo, cut_hi):
    """明细括号里的内容。`过小` 只在**真的有下限**时才印。

    `过小 0 / 过大 42` 会让读者以为"过小"这条路还在跑、只是今天恰好没命中，
    实际上放开下限后它已经不存在了（24.111「空着=有结论没写」的反面：
    这里是"写着=有一条不存在的路"）。
    """
    if float(lo or 0) <= 0:
        return '过大 %d' % int(cut_hi or 0)
    return '过小 %d / 过大 %d' % (int(cut_lo or 0), int(cut_hi or 0))


def mcap_cut_note(stat, kept_n):
    """生成 duanban.mcapCutNote。**必须由脚本生成，不许手填**。

    R100z263：首版的 total / nonMain / note 三个字段是我手工写进 data.js 的，
    而自动化只重写 `mcapCut` 数组 —— 第二天名单换了，数字和说明还是昨天的，
    这正好是 24.110 的「同一量两套数」：标题写 161、名单里 45 条、note 里 198 只候选，
    三个数一个都对不上当天。所以四个字段必须同一批数据、同一时刻生成。

    R100z266：下限放开（CAP_MIN=0）后，`lo<=0` 必须走「只留上限」的说法——
    否则页面会印出「流通市值 0~400 亿」，读者读不出这是"没有下限"还是"下限是 0 亿"，
    而这句是用户唯一能看到门槛口径的地方。
    """
    if not stat:
        # 注意：这句会经前端 esc() 直接印到页面，**不许带 markdown 记法**（G50）。
        return ('本轮市值接口没取到数，这道门槛没有生效（显式降级）：'
                '下面的池子是按未过滤市值选出来的，偏松。不是没有票被卡，是没判。')
    lo_n = int(stat.get('cut_lo') or 0)
    hi_n = int(stat.get('cut_hi') or 0)
    nm = int(stat.get('nonMain') or 0)
    nb = int(stat.get('nBefore') or 0)
    tot = lo_n + hi_n
    _lo = CAP_MIN if stat.get('lo') is None else float(stat['lo'])
    _hi = CAP_MAX if stat.get('hi') is None else float(stat['hi'])
    s = ('今天 %d 只涨停候选里有 %d 只被「%s」这道门槛剔掉（%s'
         % (nb, tot, mcap_band_txt(_lo, _hi), mcap_cut_detail(_lo, lo_n, hi_n)))
    # 非主板单独说清：它们是被「只做 60/00 主板」挡的，跟市值没关系。
    # 混进这份名单等于给了一个错的原因，读者会以为放开市值就能买。
    s += ('；另有 %d 只是创业板/科创板，本来就不做，不算在内' % nm) if nm else ''
    s += ('）。市值过滤发生在抓 K 线之前，所以这些票的形态压根没被判定过——'
          '不在确认池不等于形态不成立。此处按「页面正在展示的票优先、其余离门槛由近到远」'
          '列出 %d 只，为的是让「形态看着挺好、为什么不在池里」有一个能查到的答案。'
          % max(0, int(kept_n or 0)))
    return s


def _industry_of(hybk, agg):
    """hybk（涨停池申万行业，可能4字截断）→ agg 里的完整行业名（前缀互配）。"""
    if not hybk or not agg:
        return None
    if hybk in agg:
        return hybk
    for name in agg:
        if len(hybk) >= 3 and (name.startswith(hybk) or hybk.startswith(name)):
            return name
    return None


def fetch_top_boards(D, top=10, days=3):
    """近 N 个交易日涨幅居前板块 TOP10（**同花顺行业口径**，R98；R98g 改按 pct3 选取+排序）。
    主源：THS thshy 行业页面（50个同花顺大类行业）；对**全部**行业取 K 线计算
    近 N 日累计涨幅 pct3=(close[-1]/close[-N-1]-1)*100，按 pct3 降序取 TOP10——
    （R98 旧逻辑按当日涨幅选取排序、pct3 仅附带计算，导致榜内顺序与标题「近3日累计」不符）。
    备源：保留既有 topBoards（R91m 防护）。全部失败返回 []（前端隐藏模块）。
    每个板块附带近30根日K线（kline字段），供前端弹窗展示。"""
    # ---- 主源：同花顺行业页面（当日涨幅，仅作行业清单与代码来源） ----
    ths = fetch_ths_industries()
    if len(ths) >= 10:
        # 全量取 K 线算 pct3（50 个行业 × max_workers=5，数秒内完成）
        def _mk(b):
            kl = fetch_ths_kline_full(b["code"], days=30)
            pct3 = None
            if kl and len(kl) >= days + 1:
                try:
                    pct3 = round((kl[-1][2] / kl[-(days + 1)][2] - 1) * 100, 2)
                except Exception:
                    pass
            return {"code": b["code"], "name": b["name"],
                    "pct3": pct3, "pctToday": b["pct"],
                    "kline": kl}
        with ThreadPoolExecutor(max_workers=5) as exe:
            ranked = list(exe.map(_mk, ths))
        # 按 pct3 降序选取 TOP10（pct3 缺失者排尾部兜底补位）
        ranked.sort(key=lambda x: (x["pct3"] is None, -(x["pct3"] or -1e9)))
        picked = [x for x in ranked if x["pct3"] is not None][:top]
        if len(picked) < top:  # K线缺失的板块补位（保底凑满，pct3=None 前端不显示近3日值）
            picked += [x for x in ranked if x["pct3"] is None][:top - len(picked)]
        return picked
    # ---- 备源：保留既有 topBoards（R91m 防护） ----
    old = (D.get("duanban") or {}).get("topBoards") if isinstance(D, dict) else None
    if old:
        print("[warn] THS 板块榜不可达，保留既有 topBoards %d 条" % len(old), file=sys.stderr)
        return old
    return []


# ---------- R100z148: 个股公告抓取（东财公告 API）——个股级利好/利空确定性打标 ----------
# 背景：断板池 sentiment 原唯一数据源是 data.js 五节要闻 + aiPrediction
# （collect_sentiment），而 AI 要闻检索面向板块级/市场级事件，**抓不到单只
# 个股的公司公告**——2026-10-15 东百集团「终止收购杭州快灵光电」公告后
# 连续跌停，池内仍标 neutral、无任何消息面依据（用户实报缺陷）。
# 本模块用东财公告 API 对池内标的批量拉近 3 日公告标题，按保守关键词表
# 分类为 bear/bull（确定性规则，零 AI），经 tag_entries 既有加权逻辑
# （个股级×2）参与打标，并进 story 小作文的【个股消息】段。
NOTICE_API = "https://np-anotice-stock.eastmoney.com/api/security/ann"
NOTICE_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.notice_cache')
# 回看 7 日：与断板候选回看窗口（--days 7）对齐——池内标的可能是 5-7 天前
# 涨停的，关键公告（终止收购/减持等）常发生在 T 日~T+2，3 日窗口会漏
# （2026-10-19 实测：东百 10-15「终止收购」公告在 3 日窗口外）。
NOTICE_WINDOW_DAYS = 7
NOTICE_PAGE_SIZE = 30       # 单只拉取条数（7 日窗口需覆盖 20+ 条）
# 利空关键词（保守：只收确定性实质事件）。
# 注意：**不含**「风险提示/异常波动/停牌」——R91j 口径：涨跌幅与走势形态
# 不构成利好/利空依据，此类公告本质是对股价异动的提示，纳入会违背该口径。
# R100z276：**业绩类一律不进利空词表**（预亏/业绩预减/业绩快亏/下调业绩/下修业绩
#   全部摘除）。用户口径「就算亏，也可能利空落地开始涨，不看业绩炒股」——
#   一只票刚发预亏就被标 bear、排到观察池末位，是这条口径被反向执行。
NOTICE_BEAR_KW = ("终止收购", "终止协议", "终止合作", "终止框架", "终止实施",
                  "终止本次", "终止重大", "终止筹划", "减持",
                  "处罚", "立案", "问询", "关注函", "退市", "违规",
                  "败诉", "清算", "停产", "停工",
                  "被冻结", "轮候冻结", "被动减持", "违约", "否决")
# 利好关键词（同样保守）
# 同理：业绩预增也不给利好加分（不因业绩加减分，两个方向都摘）。
NOTICE_BULL_KW = ("中标", "签约", "签订", "涨价", "提价", "回购", "增持",
                  "获批", "投产", "量产", "定增", "重组",
                  "获奖", "通过认证", "战略合作", "中标候选")
# 「终止减持/增持/回购计划」是中性偏好事件，含「减持」会被错标 bear——显式排除。
# 「解除冻结/解除质押/解除限售」同属解除类公告（利好方向但不标 bull，保守不标）。
NOTICE_NEUTRAL_OVERRIDE = ("终止减持", "终止增持", "终止回购", "减持计划", "增持计划",
                           "解除冻结", "解除质押", "解除限售", "解除冻结及")


# ---------- R100z276 已退役：业绩方向判定（原 R100z223 的 classify_earnings） ----------
# 原实现在标题里抓「同比降 70%」「净亏损 1.2 亿」这类数值方向，直接判 bear/bull。
# 为什么整段删掉（2026-10-29 用户口径）：「关于业绩的公告、预公告不用管，就算是亏的
#   A 股也可能利空落地开始涨，所以不看业绩炒股」——业绩**不是**判方向的依据，两个方向
#   都不该加减分。实测后果：一只票刚发预亏就被标 bear（个股级×2 权重）、观察池排到末位、
#   probability −2，等于把用户明确否掉的口径在选股链里反向执行了一遍。
# 摘除范围：本段的 _EPS_* 正则与 classify_earnings()、NOTICE_BEAR_KW 的
#   预亏/业绩预减/业绩快亏/下调业绩/下修业绩、NOTICE_BULL_KW 的 业绩预增/预增。
# 别再以「AI 手写的 bear 和脚本对不上」为理由把它加回来（那是 R100z223 的动机，现已被
#   用户口径覆盖）：现在两侧的正确行为都是——业绩类公告一律 neutral，不参与方向判定。
# 减持词表未动：只按「减持」二字命中的仍标 bear，「减持计划」走 NOTICE_NEUTRAL_OVERRIDE。


def classify_notice(title):
    """公告标题 → 'bear' / 'bull' / None（保守：一词多义或歧义时不打标）。"""
    t = str(title or "")
    if not t:
        return None
    if any(k in t for k in NOTICE_NEUTRAL_OVERRIDE):
        return None
    # R100z276：业绩类公告**不走任何方向判定**（原 R100z223 的 classify_earnings 已整段退役）。
    # 业绩标题在这里就落到下面的词表判定，而词表里的业绩词已全部摘除 → 结果恒为 None 或
    # 被「终止/处罚」这类真事件命中，不会因为「预亏/净利降 70%」被打成 bear/bull。
    bear = any(k in t for k in NOTICE_BEAR_KW)
    bull = any(k in t for k in NOTICE_BULL_KW)
    if bear and not bull:
        return "bear"
    if bull and not bear:
        return "bull"
    return None  # 都命中/都不命中：宁可不标，不可错标


def fetch_stock_notices(code, days=NOTICE_WINDOW_DAYS):
    """东财公告 API 拉个股近 days 日公告（当日缓存，失败静默返回 []）。"""
    code = str(code or "")
    if not re.fullmatch(r"\d{6}", code):
        return []
    try:
        os.makedirs(NOTICE_CACHE_DIR, exist_ok=True)
        cp = os.path.join(NOTICE_CACHE_DIR, code + ".json")
        today = _today_str()
        if os.path.exists(cp):
            try:
                cached = json.load(open(cp, encoding='utf-8'))
                if cached.get('date') == today:
                    return cached.get('list') or []
            except Exception:
                pass
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        url = (f"{NOTICE_API}?sr=-1&page_size={NOTICE_PAGE_SIZE}&page_index=1"
               f"&ann_type=A&client_source=web&stock_list={code}"
               f"&f_node=0&s_node=0")
        data = get_json_curl(url, retries=1, gap=2)
        lst = ((data or {}).get('data') or {}).get('list') or []
        out = []
        for x in lst:
            nd = str(x.get('notice_date') or '')[:10]
            if not nd or nd < cutoff:
                continue
            title = str(x.get('title') or '').strip()
            # 去掉「公司名:公司名关于…」式冗余前缀（只去掉第一段）
            if ':' in title:
                title = title.split(':', 1)[1].strip() or title
            col = ''
            for c in x.get('columns') or []:
                col = str(c.get('column_name') or '')
                break
            out.append({'date': nd, 'title': title, 'column': col})
        # R100z149b：只在 API 确实返回了数据（data 真值）时才写缓存。
        # 背景：批量并发下公告接口偶发返回 None（超时/限流）→ out=[] → 空列表被
        #       TTL 当日缓存，这只票当天永久「无个股消息」，重跑也读缓存不重试。
        #       同日 stock_news.py 新闻路径实测出过同一坑（4 只标的缓存 {"list":[]}
        #       但直打 API hitsTotal=52/322 有数据）。失败不落盘 → 下次运行自然重试。
        if not data:
            return out
        with open(cp, 'w', encoding='utf-8') as f:
            json.dump({'date': today, 'list': out}, f, ensure_ascii=False)
        return out
    except Exception:
        return []


def fetch_notices_batch(codes, budget=90):
    """并发抓池内标的公告 → {code: [notice,...]}。
    内部 90s 预算（NOTICE_BUDGET 环境变量可调），到点放弃剩余——
    公告是增强项，绝不能拖垮断板主流程（K线抓取另有 DUANBAN_DEADLINE 预算）。"""
    out = {}
    todo = [c for c in dict.fromkeys(str(x) for x in (codes or [])) if re.fullmatch(r"\d{6}", c)]
    if not todo:
        return out
    try:
        budget = float(os.environ.get("NOTICE_BUDGET", str(budget)))
    except ValueError:
        budget = 90.0
    t0 = time.time()

    def work(c):
        return c, fetch_stock_notices(c)

    n_fail = 0
    try:
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = [ex.submit(work, c) for c in todo]
            for fut in as_completed(futs):
                try:
                    c, lst = fut.result()
                    out[c] = lst
                except Exception as e:
                    # R100z273：单只公告抓取抛错原来静默 pass，out 里便没有这只票；
                    # 下游把它读成「该股无公告」——正是 24.65「AI 禁断言暂无公告」的成因之一。
                    # 只计数 + 首条打样，末尾汇总总数，避免 300 只刷屏。
                    n_fail += 1
                    if n_fail == 1:
                        print("[warn] 公告单只抓取异常（同类只计数，不逐条打）: %s"
                              % str(e)[:120], file=sys.stderr)
                if time.time() - t0 > budget:
                    print("[warn] 公告抓取超预算 %ds，剩余 %d 只放弃（不影响主流程）"
                          % (budget, len(todo) - len(out)), file=sys.stderr)
                    break
    except Exception as ex:
        print("[warn] 公告批量抓取异常（不影响主流程）: %s" % ex, file=sys.stderr)
    if n_fail:
        print("[warn] 公告批量：%d/%d 只抓取异常，这些标的本次消息面为空"
              "（是『没抓到』，不是『没有公告』）" % (n_fail, len(todo)), file=sys.stderr)
    return out


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
    # 不破 T 日低点（容差 2%：收盘 < 低点×LOW_TOL 才算破位）
    # R100z52：原注释写「容差 0.5%」是过时口径——实现一直是 ×0.98（2%）。改注释不改实现。
    lowT = float(kline[iT].get("low") or 0)
    if lowT > 0 and min(C(i) for i in range(i1, n)) < lowT * LOW_TOL:
        return None
    # 不破 MA5（最新收盘 ≥ MA5×MA5_TOL）
    # R100z52：原注释写「≥ MA5×0.99」同样过时，实现一直是 ×0.97（容差 3%）。
    # 口径统一提到模块级常量，避免注释再和实现分家。
    ma5 = sum(C(i) for i in range(n - 5, n)) / 5.0
    if C(n - 1) < ma5 * MA5_TOL:
        return None
    # ---- 距 MA5 的最小溢价（见 CAP_MIN 上方注释 ①）----
    if ma5 > 0 and (C(n - 1) / ma5 - 1) * 100 < MA5_GAP_MIN:
        return None
    # ---- 启动前 20 日涨幅上限 ----
    # 取 T 日**之前**的 20 日（iT-21 → iT-1），不含涨停日本身，
    # 否则涨停那 10% 会把所有票都顶到阈值以上。
    if iT - 21 >= 0 and C(iT - 21) > 0:
        pre20 = (C(iT - 1) / C(iT - 21) - 1) * 100
        if pre20 > PRE20_MAX:
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
    # ⚠️ 2026-10-27 修「死区」（用户问「大洋电机为什么没进断板反包确认池」时查出来）：
    # 原写法 2<=days_ago<=4 时只判「已缩量 + 收盘 < MA5×0.97 → 待企稳」，把**站上 MA5 但
    # 溢价不足 2%** 的票漏在两条分支之间：
    #   · 不满足 confirmed —— check_form 里 MA5_GAP_MIN=2.0 卡住；
    #   · 不满足待企稳   —— 待企稳要求收盘 < MA5×0.97，而它是站上的；
    #   → eval_stage 返回 None → 掉出 confirmed/watching → 进 failPool，
    #     理由还写「形态失效」。形态没失效，是判定留了个洞。
    # 实测同形状样本（只差 MA5 溢价 0~2pp 的票）：002077/002747/002881/002945/600698，
    # 其中 002945 华林证券、002747 埃斯顿 已被写进失效池归因「不再符合当前判定口径」。
    # 修法：加一档「待脱离」——形态全达标、只差最后这口脱离幅度的，留在观察池等。
    # 不直接放 confirmed（那等于把 2% 门槛废掉，与回测口径脱钩）。
    if 2 <= days_ago <= 4:
        shrunk = any(0 < V(i) < V(i1) for i in range(iT + 2, n))
        if shrunk:
            ma5 = sum(C(i) for i in range(n - 5, n)) / 5.0
            gap = ((C(n - 1) / ma5 - 1) * 100) if ma5 > 0 else -99.0
            if C(n - 1) < ma5 * MA5_TOL:
                return {"pool": "watch", "stage": "待企稳",
                        "form": f"{zt_md}涨停→次{r1:.1f}倍量断板→已缩量·待收复MA5",
                        "days_ago": days_ago}
            if gap < MA5_GAP_MIN:
                # R100z223：负溢价（收盘在 MA5 之下但没破 MA5_TOL）也会落到这档，
                # 原先固定 "+%.1f%%" 会写出「+-0.1%」这种双符号（实测 5 只里 3 只）。
                # 统一用带符号格式，正负各自正确。
                # ⚠️ 这段必须与上面「待企稳」同级（同一层 if/if），
                # 曾是「待企稳」return 之后的缩进内嵌块 → 永远不可达（2026-10-27 自查发现）。
                return {"pool": "watch", "stage": "待脱离",
                        "form": f"{zt_md}涨停→次{r1:.1f}倍量断板→已缩量·站稳MA5（距MA5 {gap:+.1f}%，需 +{MA5_GAP_MIN:.0f}%）",
                        "days_ago": days_ago}
    return None


# ---------- R87: --module 模式（双池 + 利好一致性闸门） ----------

def load_dashboard(path):
    s = open(path, encoding="utf-8").read()
    i = s.index("{")
    j = s.rindex("}")
    return json.loads(s[i:j + 1])


def collect_sentiment(D):
    """从看板数据收集利好/利空线索（个股级 + 板块关键词级）。R89：
    线索不再只带板块名，而是携带命中的真实新闻条目详情（title/summary/source/time/sec），
    供前端小作文式展示与自动化 AI 改写 story（个股财报/大单/业绩消息由 AI 检索补充）。
    返回 dict：
      bull_codes {code:[newsdict]} 个股级利好依据；
      bull_secs  [(kw, newsdict)]  板块关键词利好；
      bear_codes {code:[newsdict]} 个股级利空；
      bear_secs  [(kw, newsdict)]  板块级利空。"""
    B = {"bull_codes": {}, "bull_secs": [], "bear_codes": {}, "bear_secs": []}

    def nd(src, it):
        return {"src": str(src or ""), "sec": str(it.get("sector") or ""),
                "title": str(it.get("title") or ""),
                "summary": str(it.get("summary") or ""),
                "source": str(it.get("source") or ""),
                "time": str(it.get("time") or "")}

    def add_bull_code(code, src, it):
        code = str(code or "")
        if not re.fullmatch(r"\d{6}", code):
            return
        d = nd(src, it)
        lst = B["bull_codes"].setdefault(code, [])
        if not any(r["src"] == d["src"] and r["title"] == d["title"] for r in lst):
            lst.append(d)

    def add_bull_sec(kw, src, it):
        kw = str(kw or "").strip()
        if len(kw) >= 2 and all(kw != k for k, _ in B["bull_secs"]):
            B["bull_secs"].append((kw, nd(src, it)))

    def bear_item(src, it):
        d = nd(src, it)
        sec = str(it.get("sector") or "")
        if all(sec != k for k, _ in B["bear_secs"]):
            B["bear_secs"].append((sec, d))
        for im in it.get("impacts") or []:
            theme = str(im.get("theme") or "")
            if theme and all(theme != k for k, _ in B["bear_secs"]):
                B["bear_secs"].append((theme, d))
            for st in im.get("stocks") or []:
                c = str(st.get("code") or "")
                if re.fullmatch(r"\d{6}", c):
                    lst = B["bear_codes"].setdefault(c, [])
                    if not any(r["title"] == d["title"] for r in lst):
                        lst.append(d)

    def walk(items, src, field):
        for it in items or []:
            direction = str(it.get("direction") or "")
            sec = str(it.get("sector") or "")
            # bearNews 落利空列即利空；其余节 direction==看跌 亦利空
            if field == "bearNews" or direction == "看跌":
                bear_item(src, it)
                continue
            # bullNews 落利好列即利好；macro/intl/bank 需显式 direction==看涨
            if field != "bullNews" and direction != "看涨":
                continue
            add_bull_sec(sec, src, it)
            for im in it.get("impacts") or []:
                add_bull_sec(im.get("theme") or "", src, it)
                for st in im.get("stocks") or []:
                    add_bull_code(st.get("code"), src, it)

    for mkt in ("ashare", "us"):
        sec_data = D.get(mkt) or {}
        for field in ("bullNews", "bearNews", "macroNews", "intlNews", "bankViews"):
            walk(sec_data.get(field), f"{mkt}.{field}", field)

    ai = D.get("aiPrediction") or {}
    for s in ai.get("sectors") or []:
        d = str(s.get("direction") or "")
        sec = str(s.get("sector") or "")
        if ("承压" in d) or ("走弱" in d) or ("看跌" in d):
            B["bear_secs"].append((sec, {"src": "aiPrediction", "sec": sec,
                                         "title": f"AI预测看空：{sec}",
                                         "summary": str(s.get("reason") or d),
                                         "source": "aiPrediction", "time": ""}))
            for st in s.get("stocks") or []:
                c = str(st.get("code") or "")
                if re.fullmatch(r"\d{6}", c):
                    lst = B["bear_codes"].setdefault(c, [])
                    if not any(r["title"] == f"AI预测看空：{sec}" for r in lst):
                        lst.append({"src": "aiPrediction", "sec": sec,
                                    "title": f"AI预测看空：{sec}",
                                    "summary": str(s.get("reason") or d),
                                    "source": "aiPrediction", "time": ""})
        elif d:
            add_bull_sec(sec, "aiPrediction",
                         {"sector": sec, "title": f"AI预测看多：{sec}",
                          "summary": str(s.get("reason") or d),
                          "source": "aiPrediction", "time": ""})
            for st in s.get("stocks") or []:
                add_bull_code(st.get("code"), "aiPrediction",
                              {"sector": sec, "title": f"AI预测看多：{sec}",
                               "summary": str(s.get("reason") or d),
                               "source": "aiPrediction", "time": ""})

    # ⚠️ 2026-10-27 补：个股消息（stockNews）—— 原先这里完全不读，是个长期漏项。
    # 后果：AI 在 16:00/19:30 从 stockNews 里挖出的个股利空（业绩暴雷、立案、减持等）
    # 只写在当次 data.js 里，**任何一次脚本重跑都会把它冲成 neutral**，且不报错。
    # 实测 2026-10-27 大洋电机 002249：断板池判 bear（引用「三季度净利同比降 70.58%」），
    # 同一天重跑 chen_pool 后陈学长池判 neutral（bearRefs 空）——同一只票两个池结论相反。
    #
    # 判据：**不信 stockNews 里的历史 dir 字段，用 classify_notice 现场重判**。
    #   - dir 是写入时的快照，词表/规则一改就过时，且不会自动回溯；
    #     实测 2026-10-27 有 48 条业绩利空在存量里是 neutral（词表漏「下降/减少」）。
    #   - 现场重判让「词表修复」立即对全部存量生效，不必等 stock_news 全量重抓。
    #   - 只判标题：摘要常混入别家公司新闻（恒邦「净利增69.86%」因摘要含「减持」判成 bear）。
    # 只在 classify_notice 给出确定方向时才算：null 一律不参与（宁缺毋滥，与全站口径一致）。
    sn = D.get("stockNews") or {}
    if isinstance(sn, dict):
        for code, v in sn.items():
            if not re.fullmatch(r"\d{6}", str(code)) or not isinstance(v, dict):
                continue
            nm = str(v.get("name") or "")
            for key, bucket in (("notices", "notice"), ("news", "news")):
                for it in v.get(key) or []:
                    ttl = str((it or {}).get("title") or "")
                    dr = classify_notice(ttl)
                    if dr not in ("bull", "bear"):
                        continue
                    item = {"src": "stockNews", "sec": "",
                            "title": str(it.get("title") or ""),
                            "summary": str(it.get("summary") or ""),
                            "source": str(it.get("source") or nm),
                            "time": str(it.get("date") or it.get("time") or ""),
                            "kind": "stock"}
                    tgt = B["bull_codes"] if dr == "bull" else B["bear_codes"]
                    lst = tgt.setdefault(str(code), [])
                    if not any(r["title"] == item["title"] for r in lst):
                        lst.append(item)
    return B


# ---- R100z148：story 小作文数据驱动生成（底稿即达 G16 ≥100 字 / G17 防复读）----
# 治本背景：2026-10-16 周末档 AI 未逐只改写 story，54/58 只停留在 29 字默认句；
# 且 retag_duanban 每次重打标都会用 tag_entries 拼装的短 story 覆盖已修复数据
# ——底稿必须在脚本内生成到位，AI 复核只做「逐只过目、必要时加深」。
# G17 真实口径（audit_pipeline.collect_g17_owners）：按 。！？ 后向切**整句**
# （不含 ；，），整句 ≥14 字被 ≥3 个不同字段共用才 FAIL。
# → 安全条件：每个整句必须含个股特定数据（公告标题/要闻标题/板块名/涨跌幅/
#   ztDate/form），或固定部分 <14 字。短语池按 code 取模轮换，实测 64 只零复读。
NO_NEWS_PHRASE = ["走势主要由板块联动贡献", "板块逻辑是当前主要驱动",
                  "断板后资金接力跟随板块节奏",
                  "公司事件层面暂无新增变量", "板块联动是当前主导因素",
                  "催化沿产业链逐级传导", "盘面驱动让位于板块节奏",
                  "个股独立行情弱于板块联动", "题材热度暂无二次扩散"]
BEAR_PHRASE = ["短期确定性下降", "短期不确定性上升", "短期抛压增加",
               "短期情绪面承压", "短期风险暴露", "短期预期回落",
               "短期博弈难度上升", "短期预期需下修"]
BULL_PHRASE = ["短期确定性提升", "短期情绪面改善", "短期基本面改善",
               "短期信心增强", "短期预期上修", "短期催化落地",
               "短期博弈价值凸显", "短期关注度提升"]
# R100z153：neutral 标的曾误用 BEAR_PHRASE（sent=="bull" 才取 BULL 池，其余一律
# 落空头池）→ 中性标的写出「短期情绪面承压」等反向措辞，与 sentiment 自相矛盾。
# 中性必须走中性池（全部 ≤14 字，G17 整句复读不触发）。
NEUTRAL_PHRASE = ["多空力量暂处相持", "方向选择待资金确认", "短线驱动尚未定型",
                  "多空分歧仍待收敛", "定价权交回市场博弈", "方向待量能确认"]
# R100z153：禁断言「个股暂无独立公告」——有无公告以东财公告+stockNews 为准，
# AI 写死该结论会与下方个股消息列表自相矛盾。改为描述消息来源，不描述「没有」。
# 各短语 ≤14 字，防 G17 整句复读。
STOCK_DRIVER_PHRASE = ["议题沿板块与产业链传导", "消息沿产业链向个股传导",
                       "定价锚定板块与产业链", "个股随产业链消息定价"]
SECTOR_UP_PHRASE = ["板块仍处资金关注期", "板块联动效应延续", "板块情绪有支撑",
                    "板块保持活跃"]
SECTOR_DOWN_PHRASE = ["板块整体承压", "板块氛围偏弱", "板块同步调整",
                      "板块动能不足"]
# 要闻 ref 版尾巴（轮换防复读，固定部分各 <14 字）
REF_TAIL_PHRASE = ["映射逻辑主要来自该条消息", "驱动力集中在该条消息的传导",
                   "逻辑支点即该条消息的板块映射", "短期节奏看该消息的资金定价"]
# directional 公告版尾巴（轮换防复读；并入主句、不独立成句——
# 独立尾巴句无个股特定数据，短语按 code 取模碰撞即踩 G17，R100z148b 治本）
NOTICE_TAIL_PHRASE = ["后续看该事项兑现进度", "博弈焦点在该事的结果",
                      "定价取决于该事项落地", "预期随该事项进展修正",
                      "关注点已转向该事项后续", "波动或随该事项放大"]
STAGE_PHRASE = {
    "全流程达标": ["形态已走完确认全流程", "反包前确认步骤已齐",
                   "确认流程已全部走完"],
    "待缩量": ["待缩量确认", "处于缩量确认阶段", "等待缩量信号确认"],
    "待企稳": ["待收复MA5确认", "等待企稳确认K线", "差最后一根确认K线"],
    # 2026-10-27 新增档：形态已全达标、只差 MA5 溢价那 2 个百分点（原先是判定死区，会被误归失效）
    "待脱离": ["已站稳MA5、待脱离幅度确认", "缩量与站稳已过，只差脱离MA5的幅度",
               "待脱离MA5确认（未达最小溢价）"],
}


def _pick(lst, code, salt=0):
    """短语轮换：按 code 取模——同池不同标的句子不同，防 G17 整句复读。"""
    return lst[(int(code) + salt) % len(lst)]


def _story_first_sentence(text, minlen=20, maxlen=90):
    """取 summary 首句（禁省略号截断——R91j）；过短则并第二句。

    句号改逗号：整段保持**一句**——G17 按 。！？ 切句，若 body 自带句号会把
    后面的短语切成独立句，失去个股名称保护，多股共用同一条要闻时即复读。
    """
    parts = [p for p in re.split(r"(?<=[。！？])", str(text or "")) if p.strip()]
    out = ""
    for p in parts:
        p = p.rstrip("。！？")
        if not p:
            continue
        out = (out + "，" + p) if out else p
        if len(out) >= minlen:
            break
    s = out.rstrip("，")
    if len(s) > maxlen:
        # R100z154：曾在 maxlen 处硬切 → 产出「理由是AI服务器供应」这类半句
        # （用户可见语病）。改为回退到最后一个完整分句边界（，、；），保留语义完整。
        cut = s[:maxlen]
        idx = max(cut.rfind("，"), cut.rfind("、"), cut.rfind("；"))
        s = cut[:idx] if idx >= minlen else cut
    return s


def _story_sector_seg(e, code):
    """【板块消息】段：板块当日/涨停日涨幅 + 形态 stage（全部个股特定数据）。"""
    sector = e.get("sector") or "所属"
    bpt = e.get("boardPctToday")
    bpz = e.get("boardPctZt")
    form = e.get("form") or ""
    stage = e.get("stage") or ""
    # R100z154：行业板块单日涨跌幅不可能超 12%（实测 IT服务Ⅱ 取到 17.55% 属错映射），
    # 异常值按「暂缺」处理，宁缺勿错（不编也不展示假数据）。
    if bpt is not None and abs(float(bpt)) > 12:
        bpt = None
    if bpt is None:
        sec = f"所属{sector}板块当日数据暂缺"
    elif bpt >= 0:
        sec = f"所属{sector}今日涨{bpt}%，{_pick(SECTOR_UP_PHRASE, code, 1)}"
    else:
        sec = f"所属{sector}今日跌{abs(bpt)}%，{_pick(SECTOR_DOWN_PHRASE, code, 1)}"
    if bpz is None:
        bz = ""
    elif bpz >= 0:
        bz = f"，涨停日板块涨{bpz}%"
    else:
        bz = f"，涨停日板块跌{abs(bpz)}%"
    st0 = _pick(STAGE_PHRASE.get(stage, STAGE_PHRASE["待缩量"]), code, 2)
    # R100z154：形态参数偶然全同（同日涨停/同倍数/同缩量天数）时 3 只以上会共用同一
    # 整句 → 踩 G17 复读闸。把个股名称并入形态句，保证整句唯一（G17 按句切分）。
    nm = e.get("name") or ""
    st = f"{nm}{st0}" if nm else st0
    tail = f"；{form}，{st}。" if form else f"；{st}。"
    return f"【板块消息】{sec}{bz}{tail}"


def build_story(e, refs=None, bear_refs=None):
    """R100z148：数据驱动 story 底稿（素材全部来自真实字段，零编造）。

    优先级：① directional 个股公告（东财近 7 日，标题天然唯一）
            ② 要闻 ref（bullRefs/bearRefs 真实新闻标题）
            ③ 非方向性公告存在 → 如实写「无方向性催化」
            ④ 全无 → 板块联动表述
    每段以【个股消息】/【板块消息】开头（R100z3 格式）；不足 100 字时用
    ztDate/stage/sector 兜底加长（个股特定数据，不踩 G17）。
    """
    code = str(e.get("code") or "")
    # 股票名称是唯一标识：多家公司会发布同名公告（如「关于股东减持股份计划的
    # 公告」），仅靠公告标题不足以保证句子唯一——G17 切句后必须带个股标识。
    name = e.get("name") or code
    sent = e.get("sentiment") or "neutral"
    refs = refs if refs is not None else (e.get("bullRefs") or [])
    bear_refs = bear_refs if bear_refs is not None else (e.get("bearRefs") or [])
    notices = e.get("notices") or []
    directional = [n for n in notices
                   if classify_notice(n.get("title")) in ("bear", "bull")]

    if directional:
        n = directional[0]
        direction = classify_notice(n.get("title"))
        pool = BEAR_PHRASE if direction == "bear" else BULL_PHRASE
        phrase = _pick(pool, code)
        date = str(n.get("date") or "")[5:].replace("-", "/")
        col = n.get("column") or "公司公告"
        # 尾巴并入主句（；前是同一句，G17 不切）——主句带个股名称，天然防复读
        tail = _pick(NOTICE_TAIL_PHRASE, code, 1)
        seg1 = (f"【个股消息】{name}{date}东方财富公告《{n.get('title')}》："
                f"{col}，{phrase}，{tail}。")
    else:
        # 优先用要闻 ref（真实新闻标题）。
        # R100z223：**必须按 sentiment 同向取 ref**。原实现先无条件取 refs（利好池），
        # 读到票判 bear 时就用一条利好公告去论证「短期抛压增加」——实测 002929 润建股份：
        # bear 来自「AI 算力链估值下移」（加权4），story 却引用「签订销售服务合同」
        # （利好，加权1）配 BEAR_PHRASE。方向自相矛盾，读者会当成利空公告。
        if sent == "bear":
            ref = next((r for r in bear_refs if r.get("title")), None) \
                or next((r for r in refs if r.get("title")), None)
        else:
            ref = next((r for r in refs if r.get("title")), None) \
                or next((r for r in bear_refs if r.get("title")), None)
        if ref:
            # R100z223：原实现 "【个股消息】{name}{head}《标题》" 没留分隔符，
            # 且 aiPrediction 来源的 time 为空 → head 只剩内部标记 "aiPrediction"，
            # 拼出来是「华林证券aiPrediction《AI预测看多：证券》」，像排版错误。
            # 改：head 前统一加空格；内部标记译成读者看得懂的来源名（全站禁英文行话）。
            _SRC_ZH = {"aiPrediction": "AI 预测"}
            _src_raw = str(ref.get("source") or "").strip()
            _src_show = _SRC_ZH.get(_src_raw, _src_raw)
            head = " ".join(x for x in (str(ref.get("time") or "")[5:].replace("-", "/"),
                                        _src_show) if x)
            head = (" " + head) if head else ""
            body = _story_first_sentence(ref.get("summary"))
            phrase = _pick(BULL_PHRASE if sent == "bull"
                           else (BEAR_PHRASE if sent == "bear" else NEUTRAL_PHRASE),
                           code, 1)
            tail = _pick(REF_TAIL_PHRASE, code, 2)
            drv = _pick(STOCK_DRIVER_PHRASE, code, 3)
            # R100z154：drv(4)×tail(4) 按 code 取模组合有限 → 实测 11 只 refs 标的里
            # 有 3 组整句被 ≥3 只共用（踩 G17）。把个股名称并入 tail 段（整句带唯一
            # 标识即可消除复读；并入 drv 会让名称在相邻两处重复出现，读感冗余）。
            # R100z223：summary 为空时原样拼出「《标题》：，短期抛压增加」——冒号后直接逗号。
            # 公告类 ref 的 summary 存的是 column（栏目名），常为空。
            seg1 = (f"【个股消息】{name}{head}《{ref.get('title')}》"
                    + (f"：{body}" if body else "")
                    + f"，{phrase}；{drv}，{name}{tail}。")
        elif notices:
            n0 = notices[0]
            d0 = str(n0.get("date") or "")[5:].replace("-", "/")
            zt = str(e.get("ztDate") or "")[5:].replace("-", "/")
            phrase = _pick(NO_NEWS_PHRASE, code)
            # R100z223：原写「未见方向性催化」——但"本函数认定的方向性公告"与
            # "读者点开弹窗看到的公告列表"不是同一个集合：stockNews/news 里的
            # 业绩类条目（如大洋电机「净利同比降70.58%」）不走 notices 通道，
            # 于是同屏出现「未见方向性催化」+ 一条业绩暴雷，自相矛盾。
            # 改为只陈述"本条公告的属性"，不对整体下"无方向"的断言。
            seg1 = (f"【个股消息】{name}近期信披以{n0.get('column') or '常规公告'}类为主（{d0}），"
                    f"该条公告本身不含涨跌方向指向，{phrase}；该股{zt}涨停入池。")
        else:
            zt = str(e.get("ztDate") or "")[5:].replace("-", "/")
            phrase = _pick(NO_NEWS_PHRASE, code)
            seg1 = (f"【个股消息】{name}的公开信息焦点仍在板块与产业链层面，{phrase}，"
                    f"短期观察点集中在{e.get('sector') or '所属'}板块风向；该股{zt}涨停入池。")
    story = seg1 + _story_sector_seg(e, code)
    if len(story) < 100:
        # 兜底加长（含 zt/stage/sector 个股特定数据，防 G17）
        stage = e.get("stage") or ""
        zt = str(e.get("ztDate") or "")[5:].replace("-", "/")
        stage_zh = {"全流程达标": "确认流程已走完", "待缩量": "断板后缩量确认阶段",
                    "待企稳": "缩量后待企稳确认阶段"}.get(stage, "断板跟踪阶段")
        story += (f"{name}{zt}涨停入池，当前处于{stage_zh}，"
                  f"{e.get('sector') or '所属'}板块联动强度是后续确认的关键变量。")
    return story


def baseline_prob(sent, rw, bw):
    """R98k：确定性上涨概率基线（修复「全池 50%」——merge 曾对 AI 未填概率一律补 50）。
    利好 69~78（利好加权净胜越多越高）、利空 22~31、中性 50。
    自动化 AI 复核时可 ±10 校准，但严禁全池拉平。"""
    if sent == "bull":
        return min(80, 66 + min(max(rw - bw, 1), 4) * 3)
    if sent == "bear":
        return max(22, 34 - min(max(bw - rw, 1), 4) * 3)
    return 50


def baseline_note(sent, rw, bw):
    if sent == "bull":
        return f"脚本基线：利好加权{rw}/利空加权{bw}（AI 可校准）"
    if sent == "bear":
        return f"脚本基线：利空加权{bw}/利好加权{rw}（AI 可校准）"
    return "脚本基线：近1日无明确方向新闻（AI 可校准）"


def tag_entries(entries, D):
    """R88 口径打标 + R89 依据小作文化（--module 口径，取代 R87 硬排除闸门）；
    R91j 修订：利好/利空判定只依据新闻内容——
      - 利好：真实个股/板块级新闻（走势形态描述不构成利好，过滤）；
      - 利空：个股/板块被利空新闻或 AI预测看空点名（当日涨跌不构成利空/利好，
        15点后利好新闻而当日下跌属洗盘，应标利好）；
      - 中性：暂无明确方向新闻依据（AI 可复核板块语义后改判）。
    每项附 sentiment / bullRefs / bearRefs（携带命中的真实新闻 title/summary/
    source/time）/ story（build_story 数据驱动小作文底稿：公告/要闻/板块等真实
    字段拼装，R100z3 段落格式、底稿即 ≥100 字过 G16，AI 复核逐只过目、必要时加深）。"""
    def _src_tag(src, kw, hybk, D):
        """利好/利空依据来源标签：🇨🇳 国内新闻 / 隔夜美股 / 🌍 全球新闻。
        （R91j：判定只看新闻，跌幅类依据已废除，📉板块标签随之移除）"""
        if src == "aiPrediction":
            return "🇨🇳"
        if src and str(src).startswith("ashare."):
            return "🇨🇳"
        if src and str(src).startswith("us."):
            su = ((D.get("us") or {}).get("sectorsUp") or [])

            def hit(name):
                if not name:
                    return False
                for s in su:
                    sn = str(s.get("name") or s.get("sector") or "")
                    if sn == name or (len(name) >= 3 and (sn.startswith(name) or name.startswith(sn))):
                        try:
                            return float(s.get("pct") or 0) > 0
                        except Exception:
                            return False
                return False
            if hit(hybk) or (kw and hit(kw)):
                return "隔夜美股"
            return "🌍"
        return "🇨🇳"

    B = collect_sentiment(D)
    # R91j：纯走势/形态描述（断板反包形态、缩量、MA5、企稳、二次启动等）不构成
    # 个股利好依据——利好必须有真实公司级/板块级新闻，走势只是形态背景。
    FORM_ONLY = re.compile(
        r"断板反包|倍量断板|缩量|站稳MA5|待收复MA5|二次启动|已确认企稳|技术面呈|涨停→次")

    def _is_form_only(r):
        txt = (str(r.get("title") or "") + str(r.get("summary") or ""))
        return bool(FORM_ONLY.search(txt))

    out = []
    for e in entries:
        code, hybk = str(e["code"]), str(e.get("hybk") or "")
        # ---- 利空依据（R91j：只看新闻，当日涨跌不构成利空——15点后利好新闻
        #      而当日下跌属洗盘情形，绝不因跌幅标利空） ----
        bear_refs = []
        for d in B["bear_codes"].get(code) or []:
            nd2 = dict(d, kind="stock"); nd2["srcTag"] = _src_tag(d.get("src"), None, hybk, D)
            bear_refs.append(nd2)
        for kw, d in B["bear_secs"]:
            if kw and len(kw) >= 2 and hybk and (hybk in kw or kw in hybk) and \
                    not any(b["title"] == d["title"] for b in bear_refs):
                nd2 = dict(d, kind="sector", kw=kw); nd2["srcTag"] = _src_tag(d.get("src"), kw, hybk, D)
                bear_refs.append(nd2)
        # ---- 利好依据（个股级须为真实新闻，纯走势描述过滤；板块关键词兜底） ----
        refs = [d for d in (B["bull_codes"].get(code) or []) if not _is_form_only(d)]
        for d in refs:
            d.setdefault("kind", "stock"); d["srcTag"] = _src_tag(d.get("src"), None, hybk, D)
        if not refs:
            kw_hit = next(((kw, d) for kw, d in B["bull_secs"]
                           if hybk and (hybk in kw or kw in hybk)), None)
            if kw_hit:
                nd2 = dict(kw_hit[1], kind="sector", kw=kw_hit[0])
                nd2["srcTag"] = _src_tag(kw_hit[1].get("src"), kw_hit[0], hybk, D)
                refs = [nd2]
        # ---- R100z148: 个股公告（东财公告 API）转个股级线索（确定性抓取，
        #      弥补 AI 要闻检索覆盖不到单只个股公司公告的缺口） ----
        for nt in e.get("notices") or []:
            direction = classify_notice(nt.get("title") or "")
            if not direction:
                continue
            ref = {"src": "notice", "sec": e.get("sector") or "",
                   "title": nt.get("title") or "",
                   "summary": nt.get("column") or "",
                   "source": "东方财富公告", "time": nt.get("date") or "",
                   "kind": "stock"}
            if direction == "bear":
                if not any(b.get("title") == ref["title"] for b in bear_refs):
                    bear_refs.append(ref)
            else:
                if not any(b.get("title") == ref["title"] for b in refs):
                    refs.append(ref)
        bull_type = "stock" if any(r.get("kind") == "stock" for r in refs) else ("sector" if refs else None)
        # R91p 加权打标（2026-09-23 用户反馈修复：有研新材 12 条利好被 1 条间接利空
        # 一票否决）：个股级线索×2、板块级线索×1，bear 加权 > bull 加权才判 bear；
        # 否则有利好线索判 bull、无线索判 neutral。直接点名的个股级利空（bearNews
        # impacts stocks / AI预测看空个股）权重高，间接板块利空不再压过压倒性利好。
        def _w(rs):
            return sum(2 if r.get("kind") == "stock" else 1 for r in rs)
        bw, rw = _w(bear_refs), _w(refs)
        sent = "bear" if bw > rw else ("bull" if rw > bw else "neutral")
        # 小作文化：R100z148 起改为 build_story 数据驱动生成——底稿即达
        # G16 ≥100 字与 R100z3 段落格式（【个股消息】/【板块消息】），
        # 按 code 轮换短语防 G17；AI 复核只做「逐只过目、必要时加深」。
        story = build_story(dict(e, sentiment=sent), refs, bear_refs)  # R100z154：原传 e 无 sentiment → bull 也用中性尾巴，与标注矛盾
        # R98k：概率不再留空待 AI（AI 曾全填 50 / merge 曾补 50）——先给确定性基线，
        # AI 复核仍可 ±10 校准，但 probNote 已非空，retag 不会重算覆盖。
        # notices 是中间字段（build_module 批量抓取、本函数消费后即弃，
        # 不进 data.js——merge 不建该字段白名单，出口统一剥掉）
        prob = baseline_prob(sent, rw, bw)
        note = baseline_note(sent, rw, bw)
        # R100z148：中性标的按「近7日有/无信披·新闻活动」+2 微调，打破全池同值
        # （有 directional 公告的已由基线区分 bear 22-34 / bull 66-80，不动）
        if sent == "neutral" and (e.get("notices") or refs or bear_refs):
            prob = min(88, prob + 2)
            note = ((note.rstrip("；;") + "；近7日有信披/新闻活动+2")
                    if note else "近7日有信披/新闻活动+2")
        out.append({k: v for k, v in dict(e, sentiment=sent, bullType=bull_type,
                                          bullRefs=refs, bearRefs=bear_refs, story=story,
                                          probability=prob,
                                          probNote=note).items()
                    if k != "notices"})
    return out


def build_module(pairs, D):
    """pairs: [{code,name,hybk,ztDate,stage_info,kl}] → 断板反包模块 dict。
    每只附近 60 根日K（前端渲染近 30 根并计算 MA5/10/20/30）与当日涨跌幅；
    sentiment 由 tag_entries 按要闻/AI预测打标；probability 留空由自动化 AI 填写。"""
    entries = []
    # R98: 同花顺行业口径优先（topBoards/boardPctToday/boardPctZt）
    ths_map = fetch_ths_board_map()  # {THS行业名: {code,pct}}
    ths_names = set(ths_map.keys())
    # 东财/申万兜底（THS 匹配失败时使用）
    board_map = fetch_board_map()
    ind_agg = _stock_clist_agg()
    for p in pairs:
        kl = p["kl"][-60:]
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
        # R98: 优先同花顺行业口径
        ths_name = match_ths_industry(p["hybk"], ths_names)
        # R100z4k：截断护栏——**东财 hybk 被截断**（如「计算机设」⊂ 同花顺「计算机设备」）
        # 说明匹配是拿残缺名硬套，回退用完整东财行业名，杜绝池内 sector 存截断值。
        #
        # ★R100z170 修：原判据写反了方向（`hybk.startswith(ths_name)`），后果是
        #   ① 真正要拦的截断名（hybk 比 ths_name 短）完全拦不住 → R100z4k 等于没修；
        #   ② 反过来把**带申万二级后缀**的正当匹配全部误杀：
        #      电机Ⅱ / IT服务Ⅱ / 贸易Ⅱ / 白酒Ⅱ / 燃气Ⅱ 全部 'XⅡ'.startswith('X') → 置 None，
        #      退回东财口径后一批标的连板块涨幅都取不到（用户 2026-10-21 截图「板块涨幅 —」）。
        #   正确方向：只有 ths_name 是 hybk 的**延长**才算截断。
        # R100z179：护栏前先留一份完整同花顺行业名——后面若东财兜底也取不到涨幅，
        # 宁可用「完整名 + 真实涨幅」，也不能写「截断名 + 板块数据暂缺」（用户实报过板块—）。
        _ths_full = ths_name
        # ★R100z180：截断名（hybk 比同花顺行业名短，如「自动化设」⊂「自动化设备」）**不再丢弃同花顺匹配**。
        # 原 R100z4k 护栏把 THS 完整名置 None，随后退回东财口径，而东财分支又拿 p["hybk"]（截断名）当 sector
        # → 板块名截断 + 涨幅换成东财等权口径（2026-10-29 实测「自动化设 −1.99%」，同花顺同行业 −5.17%），
        # 页面徽章与 story 的板块句双双失真。ths_names 来自 90 行业全量列表，前缀匹配到的必是完整名，
        # 与 hybk 的完整度无关，故此处保留匹配结果、不再置 None。
        if ths_name:
            bpt = round(ths_map[ths_name]["pct"], 2)
            ths_code = ths_map[ths_name]["code"]
            bpz = fetch_ths_board_pct_on(ths_code, p["ztDate"])
            sector = ths_name
            bk_code = ths_code
        else:
            # 回退东财/申万口径（R91m 兜底）
            ind_name = _industry_of(p["hybk"], ind_agg)
            if ind_name:
                bpt = round(ind_agg[ind_name]["pct"], 2)
                bk_code = None
                bpz = None
            else:
                bk_code, bpt = resolve_board(p["hybk"], board_map)
                bpz = fetch_board_pct_on(bk_code, p["ztDate"]) if bk_code else None
            if bpt is None and _ths_full:
                # R100z179：东财兜底（push2delay / 板块日K 常不可达）也拿不到涨幅时，
                # 退回同花顺完整行业名口径。截断名（汽车零部/自动化设）匹配到的
                # 完整行业名与涨幅都是真实数据，比「板块数据暂缺」更接近事实。
                ths_name = _ths_full
                bpt = round(ths_map[ths_name]["pct"], 2)
                ths_code = ths_map[ths_name]["code"]
                bpz = fetch_ths_board_pct_on(ths_code, p["ztDate"])
                sector = ths_name
                bk_code = ths_code
            else:
                sector = p["hybk"] or "其他"
        entries.append({"code": p["code"], "name": p["name"],
                        "sector": sector, "ztDate": p["ztDate"],
                        "stage": p["stage_info"]["stage"], "form": p["stage_info"]["form"],
                        "pct": pct, "boardPctToday": bpt, "boardPctZt": bpz, "bkCode": bk_code,
                        "probability": None, "probNote": "",
                        "kline": bars})
    # R100z148: 批量抓池内标的近 3 日公告（个股级利好/利空确定性打标，
    # 弥补 AI 要闻检索只覆盖板块级事件、抓不到单只个股公告的缺口）。
    # notices 是中间字段（不进 data.js），tag_entries 消费后由 merge 的白名单丢弃。
    try:
        notices = fetch_notices_batch([p["code"] for p in pairs])
        for e in entries:
            e["notices"] = notices.get(str(e["code"])) or []
        n_hit = sum(1 for e in entries if e["notices"])
        print("[info] 公告抓取：%d/%d 只近 %d 日有公告"
              % (n_hit, len(entries), NOTICE_WINDOW_DAYS), file=sys.stderr)
    except Exception as ex:
        print("[warn] 公告抓取失败（不影响主流程）: %s" % ex, file=sys.stderr)
    tagged = tag_entries(entries, D)
    confirmed = [e for e in tagged if e["stage"] == "全流程达标"]
    watching = [e for e in tagged if e["stage"] != "全流程达标"]
    # R98k：池级微调——确认池走完全流程形态更完整 +4；观察池仍在形成中 -2
    # R100z197（2026-10-24 用户拍板）：**只调 AI 真给过的值**。
    # 原写法 `(e.get("probability") or 50) ± 4/2` 才是「基线 48%/54%」的真正产地——
    # 池创建时 probability=None，一过这里就凭空变成 54/48，读者以为是 AI 判断，
    # 其实是 50 的衍生物。实测 12 个交易日 27 个「基线」里绝大多数正是这两个数。
    # 缺失一律留 None：精选排序置末、页面显示「—」，由 AI 按形态/题材自己填。
    for e in confirmed:
        _p = e.get("probability")
        if isinstance(_p, (int, float)):
            e["probability"] = max(15, min(88, round(_p + 4)))
    for e in watching:
        _p = e.get("probability")
        if isinstance(_p, (int, float)):
            e["probability"] = max(15, min(88, round(_p - 2)))
    # 观察池内更接近确认的（待企稳）排前，同阶段利空靠后
    watching.sort(key=lambda e: (0 if e["stage"] == "待企稳" else 1,
                                 {"bull": 0, "neutral": 1, "bear": 2}[e["sentiment"]]))
    return {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "确认池=走完「涨停→放量断板→缩量→不破T日低点与MA5」全流程；观察池=形态进行中、只差最后一根确认K线（待企稳＞待缩量）。形态之外另有三条量化门槛，同时满足才进池：流通市值 100~400 亿、启动前 20 个交易日涨幅 ≤10%、收盘距 MA5 ≥+2%；被任何一条卡住的票在失效池可见并注明是哪一条。每只标的按当日要闻/AI预测自动标注口径：利好（有利好新闻依据且未被看空）/利空（被利空新闻或AI预测看空点名）/中性（暂无明确新闻依据）；利好/利空只看新闻内容，当日涨跌与走势形态不作为依据（R91j）。口径与 probability 由自动化 AI 复核校准。",
        "topBoards": fetch_top_boards(D),
        "confirmed": confirmed,
        "watching": watching,
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


# ---------------- 失败池（R100i）：破位 / 形态失效归档，5 交易日后自动清理 ----------------
_DUANBAN_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.duanban_state.json')


def _trading_days_between(d0, d1):
    """真实交易日数量（含端点，节假日感知）。R100z44：委托 common.trading_days_between——
    旧实现只数周一~周五，国庆/春节长假会被数成交易日，节后首日运行时失效池
    被提前清空（09-30 归档到 10-08 被数成 8>5 误删）。"""
    from common import trading_days_between
    return trading_days_between(d0, d1)


def _load_duanban_state():
    try:
        with open(_DUANBAN_STATE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_duanban_state(st):
    try:
        with open(_DUANBAN_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(st, f, ensure_ascii=False)
    except Exception as e:
        # R100z273：写状态失败原来静默 pass。状态文件是「首次入池日 / 连续在池天数」的
        # 记忆锚，写失败不会让本次结果错，但下一次跑会把所有标的当新入池（entryDate 重置、
        # 天数从 0 数），属于跨日才显形的错——必须留一句痕迹，否则查不到原因。
        print("[warn] 断板状态文件写入失败（下次运行入池日期/天数会被重置）: %s"
              % str(e)[:120], file=sys.stderr)


def _clean_duanban_fail(fp, today):
    if not fp:
        return []
    out = []
    for f in fp:
        ed = f.get('entryDate')
        try:
            if ed and _trading_days_between(ed, today) > 5:
                continue
        except Exception:
            pass
        out.append(f)
    return out


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
    any_fetch_failed = False
    for d in dates:
        pool = fetch_zt_pool(d)
        if pool is None:
            # 限频/网络失败（≠空池）：重试后仍失败则明确告警，避免静默漏候选（R70）
            any_fetch_failed = True
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
        time.sleep(0.4)  # 同花顺/东财 限频保护
    if not valid_dates or not cand:
        if args.module:
            if any_fetch_failed:
                # 涨停池（同花顺+东财）全源拉取失败：保留既有 duanban 模块，
                # 避免把已部署的确认池/观察池/topBoards 清成空（R91m 防护）。
                # 仅在「网络失败」时触发；若涨停池真实为空（非失败）仍按原逻辑写空模块。
                try:
                    old_mod = load_dashboard(args.dashboard or os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), "data.js")).get("duanban") or {}
                    if old_mod.get("confirmed") or old_mod.get("watching") or old_mod.get("topBoards"):
                        old_mod.setdefault("generatedAt", "")
                        old_mod.setdefault("note", "涨停池（同花顺+东财）不可达，保留既有模块")
                        print("[warn] 涨停池全源不可达，保留既有 duanban 模块"
                              "（确认池 %d / 观察池 %d）"
                              % (len(old_mod.get("confirmed") or []),
                                 len(old_mod.get("watching") or [])), file=sys.stderr)
                        old_mod['failPool'] = _clean_duanban_fail(old_mod.get('failPool', []), datetime.now().strftime('%Y-%m-%d'))
                        print(json.dumps(old_mod, ensure_ascii=False, indent=1))
                        # R100z72：即便涨停池全源不可达，也把保留下来的既有模块落到 --out，
                        # 避免下游 merge_duanban 因 --out 缺失而 FileNotFoundError 崩掉 16:00 数据步。
                        if args.out:
                            with open(args.out, "w", encoding="utf-8") as _f:
                                _f.write(json.dumps(old_mod, ensure_ascii=False, indent=1) + "\n")
                            print(f"[module] 草稿已写入 {args.out}（保留既有模块，涨停池不可达）", file=sys.stderr)
                        return
                except Exception as e:
                    print("[warn] 保留既有模块失败，回退空模块：%s" % e, file=sys.stderr)
            mod = {"generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                   "note": "涨停池为空或不可用，无候选",
                   "topBoards": [], "confirmed": [], "watching": [], "excluded": []}
            try:
                mod["topBoards"] = fetch_top_boards(load_dashboard(
                    args.dashboard or os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), "data.js")))
            except Exception as e:
                # R100z273：涨停池全不可达时，仍想补出「近3日板块 TOP10」，补不出来原来静默 pass，
                # 前端就只剩空列表（显示「—」），和「今天真的没有板块」长得一模一样（24.16）。
                print("[warn] 涨停池不可达时补 topBoards 失败，模块 topBoards 留空"
                      "（是『没抓到』不是『没有板块』）: %s" % str(e)[:120], file=sys.stderr)
            print(json.dumps(mod, ensure_ascii=False, indent=1))
            # R100z72：空模块也落 --out，保证下游 merge_duanban 永远有草稿可读。
            if args.out:
                with open(args.out, "w", encoding="utf-8") as _f:
                    _f.write(json.dumps(mod, ensure_ascii=False, indent=1) + "\n")
                print(f"[module] 草稿已写入 {args.out}（空模块）", file=sys.stderr)
            return
        print("[]" if args.json else "无达标候选（涨停池为空或不可用）")
        return

    # 最近涨停优先核验
    cands = sorted(cand.items(), key=lambda kv: max(kv[1]["zt"]), reverse=True)[:args.limit]

    # R93 时间预算（2026-09-23）：自动化环境对脚本有超时限制（此前串行抓 300 只候选
    # K 线 + 二轮重试常超 6 分钟，16:00 数据步中被 SIGTERM 杀掉、duanban 模块整体跳过）。
    # 现并行抓取 + 全局时间预算（默认 420s，可用环境变量 DUANBAN_DEADLINE 覆盖）：
    # 到点后取消剩余任务、跳过二轮重试，保证预算内必出结果（候选按最近涨停优先排序，
    # 被截断的是最旧的候选，影响最小）。
    t0 = time.time()
    try:
        deadline = float(os.environ.get("DUANBAN_DEADLINE", "420"))
    except ValueError:
        deadline = 420.0

    results = []   # 确认池记录（--json/文本口径，历史不变）
    pairs = []     # 双池记录（--module 口径：含观察池）
    failed = []  # K线拉取失败的候选（多为新浪限频），预算允许时二轮重试（R70）
    res_lock = threading.Lock()

    # ---- 市值条件（CAP_MIN / CAP_MAX）：放在抓 K 线**之前**，因为 K 线是 420s 预算的瓶颈。
    # 这一步能把候选数砍掉一大半，等于给后面的并行抓取留出余量。
    # 取不到市值的票**保留**（不误杀）；整批都取不到就整体跳过并打警告（24.16 显式降级）。
    # R100z250：上下限分开计数——只报一个「剔除 N 只」的话，用户看到池子变小
    # 根本判断不出是被「太小」还是「太大」卡的，这正是上一轮归因文案骗人的同一个毛病。
    _caps = {}
    _mcap_cut = []          # R100z262：被市值剔除的明细，落进 duanban.mcapCut
    _mcap_stat = {}         # R100z263：同批数字，total/nonMain/note 必须一起写，不许手填
    _n_before = len(cands)  # 过滤前候选数（note 里的「今天 N 只涨停候选」）
    try:
        _caps = fetch_market_caps([c for c, _ in cands])
    except Exception as _e:
        print("[duanban] 市值接口异常：%s" % _e, file=sys.stderr)
    if _caps:
        cands, _stat = filter_by_mcap(cands, _caps)
        _mcap_cut = _stat.get("cutList") or []
        _mcap_stat = dict(_stat)
        _mcap_stat['nBefore'] = _n_before
        print("[duanban] 市值过滤（%s）：过小剔除 %d 只、过大剔除 %d 只，"
              "无市值保留 %d 只，候选 → %d 只"
              % (mcap_band_txt(CAP_MIN, CAP_MAX), _stat["cut_lo"], _stat["cut_hi"],
                 _stat["no_cap"], len(cands)),
              file=sys.stderr)
    else:
        print("[duanban] ⚠️ 市值全部取不到，本轮跳过市值过滤（显式降级，"
              "池子会偏松——市值条件未生效）", file=sys.stderr)

    def run_one(code, rec, kl):
        """对单候选评估：--module 双池入 pairs，确认池同步入 results（历史口径）。"""
        hit = eval_candidate(code, rec, kl)
        if hit:
            with res_lock:
                pairs.append(hit)
                if hit["stage_info"]["pool"] == "confirmed":
                    results.append({"code": hit["code"], "name": hit["name"],
                                    "hybk": hit["hybk"], "ztDate": hit["ztDate"],
                                    "form": hit["stage_info"]["form"]})

    def kline_worker(item):
        """K 线抓取工作线程：返回 (code, rec, kl|None)。get_kline 内部含缓存+腾讯主源+新浪备源。"""
        code, rec = item
        sym = sina_symbol(code)
        if not sym:
            return code, rec, None
        # R72：首轮也走 get_kline（含本地缓存 + 腾讯备用源），不再裸打新浪
        kl = get_kline(code, sym)
        return code, rec, (kl if isinstance(kl, list) and kl else None)

    n_done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(kline_worker, it) for it in cands]
        for fut in as_completed(futs):
            code, rec, kl = fut.result()
            n_done += 1
            if kl:
                run_one(code, rec, kl)
            else:
                with res_lock:
                    failed.append((code, rec))
            if n_done % 60 == 0:
                print(f"[info] 已核验 {n_done}/{len(cands)} 只候选，用时 {time.time()-t0:.0f}s",
                      file=sys.stderr)
            if time.time() - t0 > deadline:
                left = sum(1 for f in futs if not f.done())
                for f in futs:
                    f.cancel()
                print(f"[warn] 达到时间预算 {deadline:.0f}s，取消剩余 {left} 只候选（最近涨停优先，"
                      "被截断的为最旧候选）", file=sys.stderr)
                break

    # 二轮重试：冷却 15s 后对失败候选重试一次（新浪限频窗口恢复）；仅当预算充足（剩余 >25%）
    if failed and time.time() - t0 < deadline * 0.75:
        time.sleep(15)
        for code, rec in list(failed):
            if time.time() - t0 > deadline:
                print("[warn] 达到时间预算，放弃二轮重试剩余候选", file=sys.stderr)
                break
            sym = sina_symbol(code)
            if not sym:
                continue
            kl = get_kline(code, sym)
            if not isinstance(kl, list) or not kl:
                print(f"[warn] {code} {rec['name']} K线两次拉取失败，本候选缺失", file=sys.stderr)
                continue
            run_one(code, rec, kl)
            time.sleep(0.4)

    # R77：标的口径——只保留沪深主板(60/00 开头)，剔除科创板(688/689)、
    # 创业板(300/301/302)与北交所(4/8/92 开头)——用户要求提供标的均非科创/创业板
    results = [r for r in results
               if str(r["code"]).startswith(("60", "00"))]
    pairs = [p for p in pairs if str(p["code"]).startswith(("60", "00"))]

    if args.module:
        # R88 断板反包独立模块：双池 + 口径打标（对照 data.js 要闻/AI预测，
        # R87 的硬排除闸门改为打标不排除——利空/中性标的保留入池并如实标注）
        dash = args.dashboard or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data.js")
        try:
            D = load_dashboard(dash)
        except Exception as e:
            print(f"[warn] data.js 解析失败（{e}），跳过口径打标——全部按 neutral 标注",
                  file=sys.stderr)
            mod = {"generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                   "note": "（口径打标未运行：data.js 不可用，全部按中性标注）",
                   "topBoards": [],
                   "confirmed": [{"code": p["code"], "name": p["name"],
                                  "sector": p["hybk"] or "其他", "ztDate": p["ztDate"],
                                  "stage": p["stage_info"]["stage"],
                                  "form": p["stage_info"]["form"],                                   "pct": 0, "boardPctToday": None, "boardPctZt": None,
                                  "bkCode": None, "bullType": None,
                                  "probability": None, "probNote": "",
                                  "sentiment": "neutral",
                                  "bullRefs": [], "bearRefs": [], "story": "", "kline": []}
                                 for p in pairs if p["stage_info"]["pool"] == "confirmed"],
                   "watching": [{"code": p["code"], "name": p["name"],
                                 "sector": p["hybk"] or "其他", "ztDate": p["ztDate"],
                                 "stage": p["stage_info"]["stage"],
                                 "form": p["stage_info"]["form"], "pct": 0,
                                 "probability": None, "probNote": "",
                                 "sentiment": "neutral",
                                 "bullRefs": [], "bearRefs": [], "story": "", "kline": []}
                                for p in pairs if p["stage_info"]["pool"] != "confirmed"]}
        else:
            mod = build_module(pairs, D)
        # R100z262：把「被市值剔除」的票写进模块。前端拿它在卡片上直接回答
        # 「形态看着只差最后一步，为什么不在池里」——此前这道门槛只存在于
        # stderr 的一行日志里，用户端完全看不到。
        # R100z263：四个字段一次写全（列表/总数/非主板数/口径说明）。历史教训：
        # 只写列表、其余三个手填进 data.js，第二天就变成「名单是今天的、数字是昨天的」。
        # 排序走 prioritize_cut（展示票优先），否则用户正在看的那几只排到 100 名开外。
        _cut = prioritize_cut(_mcap_cut, D, 60)
        mod['mcapCut'] = _cut
        mod['mcapCutTotal'] = (_mcap_stat.get('cut_lo', 0) + _mcap_stat.get('cut_hi', 0)) \
            if _mcap_stat else 0
        mod['mcapCutNonMain'] = int(_mcap_stat.get('nonMain') or 0)
        mod['mcapCutNote'] = mcap_cut_note(_mcap_stat, len(_cut))
        # R100z274（用户 2026-10-29 00:06）：「断板·市值剔除」徽章印在 30 亿的永和智控上。
        # 根因不是判定错了，是**数据和规则不同龄**：16:02 那次运行还在旧口径（100~400 亿）下，
        # 159 只小盘票全带 side='lo'；21:5x 下限撤到 0 之后代码不再产出 lo，但 data.js 里
        # 那份名单要等下一次 16:00 才会被覆盖——中间这段时间页面照旧说「市值剔除了它」。
        # 堵法：让数据**自带口径**（这批名单是按哪条带子算的），前端只认数据里的带子判
        # 「side='lo' 还算不算数」。这样既不在前端硬编码 400/0（那是第二套口径），
        # 也不会在回滚 CAP_MIN 时要求改前端。
        mod['mcapBand'] = {'lo': float(_mcap_stat.get('lo', CAP_MIN)),
                           'hi': float(_mcap_stat.get('hi', CAP_MAX))}
        # R100i：断板反包失败池——原在池内、今日滑出的标的归因失败归档，5 交易日后清理
        today_str = datetime.now().strftime('%Y-%m-%d')
        try:
            prev_ds = _load_duanban_state()
        except Exception:
            prev_ds = {}
        prev_pools = prev_ds.get('pools', {})
        prev_codes = set(prev_pools.keys())
        cur_codes = set(str(e.get('code') or '') for e in (mod.get('confirmed') or []) + (mod.get('watching') or []))
        cand_codes = set(str(c) for c in cand.keys())
        failed_codes = set(str(c) for c, _ in failed)
        _mc_map = {str(x.get('code')): x for x in _mcap_cut}
        new_fails = []
        for code in prev_codes - cur_codes:
            info = prev_pools.get(code, {})
            if code in failed_codes:
                reason = 'K线获取失败，形态校验缺失，暂归失效'
            elif code in _mc_map:
                # R100z262：出池原因要**指名是哪一条**。原来这句是「形态走完，或市值／
                # 启动幅度／距MA5 未达标」——三个候选并列，等于什么都没说：读者没法据此
                # 判断该不该继续看这只票。市值这条能精确算出来，就先把它精确掉。
                _mc = _mc_map[code]
                # R100z266：放开下限后 side 恒为 'hi'，这句的「低于/下限」分支已成死路，
                # 但留着——回滚（CAP_MIN 改回 100.0）时不用再动这里。
                reason = ('流通市值 %.1f 亿，%s%s'
                          % (_mc['mcap'],
                             '低于' if _mc['side'] == 'lo' else '高于',
                             ('下限 %.0f 亿' % CAP_MIN) if _mc['side'] == 'lo'
                             else ('上限 %.0f 亿（%s）' % (CAP_MAX, mcap_band_txt(CAP_MIN, CAP_MAX)))))
            elif code in cand_codes:
                # 口径收紧后（加了市值/启动前20日/距MA5 三道），出池不等于「形态走坏」。
                # R100z262：市值这条已经由上一条精确回答，这里不再把它并列进来——
                # 原来那句「形态走完，或市值／启动幅度／距MA5 未达标」三选一，读者没法
                # 据此判断该不该继续跟这只票。
                reason = '形态不达标（启动前 20 日涨幅 >10%，或距 MA5 溢价 <2%），形态本身没坏'
            else:
                reason = '超过观察窗口，形态失效'
            new_fails.append({'code': code, 'name': info.get('name', ''),
                              'sector': info.get('sector', ''), 'form': info.get('form', ''),
                              'reason': reason, 'entryDate': today_str})
        carried = _clean_duanban_fail(prev_ds.get('failPool', []), today_str)
        # R100z120（用户 2026-10-12 截图「同一标的既在确认池又在失效池」）：旧归档滚动保留 5 日，
        # 若某票先失效归档、之后又重新走完形态达标进池（本节 cur_codes），旧归档不该再留——
        # 失效池语义 = 已失效且当前不在池。故把当前在池 code 一律从 carried 剔除。
        carried = [f for f in carried if str(f.get('code') or '') not in cur_codes]
        carried_codes = set(f.get('code') for f in carried)
        failPool = carried + [f for f in new_fails if f['code'] not in carried_codes]
        # R100z44：入池首日标记——新入池标的 entryDate=今日，老标的沿用首次入池日（state 逐日携带）。
        # 前端据此给首日新入池卡片整体黄色高亮+「新」徽章，次日自动恢复（用户 2026-10-04 要求）。
        # ⚠️ R100z57：旧写法 `(prev_pools[code].entryDate) or today_str` 会在上一轮没留下 entryDate
        # 时一律写今天 → 整屏刷「新」（老 data.js 正是这种形态）。改三分支，与 accumulation_score /
        # power_screener / harmonic_detect 同口径：① 旧池有 entryDate → 沿用；② 旧池没见过 → 写 today；
        # ③ 旧池见过但无 entryDate（老数据、生成代码早于 R100z44）→ 不写，前端不挂徽章（宁缺毋滥）。
        for e in (mod.get('confirmed') or []) + (mod.get('watching') or []):
            _c = str(e.get('code') or '')
            _pv = prev_pools.get(_c) or {}
            if _c in prev_pools and _pv.get('entryDate'):
                e['entryDate'] = _pv['entryDate']
            elif _c not in prev_pools:
                e['entryDate'] = today_str
        cur_pools = {str(e.get('code') or ''): {'name': e.get('name', ''), 'sector': e.get('sector', ''),
                                                 'form': e.get('form', ''),
                                                 'entryDate': e.get('entryDate') or today_str}
                     for e in (mod.get('confirmed') or []) + (mod.get('watching') or [])}
        _save_duanban_state({'pools': cur_pools, 'failPool': failPool})
        mod['failPool'] = failPool

        # R100z223 自证：失效池与在池必须互斥（audit G 闸门同判据）。
        # 2026-10-27 踩过：把「待脱离」5 只手工追加进 data.js 的 watching，但没同步
        # 从 failPool 摘掉 → 同一只票既在观察池又在失效池，闸门 FAIL 才发现。
        # 这里在写草稿前自检一遍，越早炸越好过跑到 deploy 才炸。
        _inpool = {str(e.get('code') or '') for e in mod["confirmed"] + mod["watching"]}
        _dup = sorted(_inpool & {str(f.get('code') or '') for f in failPool})
        if _dup:
            print(f"[warn] 失效池与在池重叠 {_dup}（应为互斥，检查 roll/merge 路径）",
                  file=sys.stderr)

        out = json.dumps(mod, ensure_ascii=False, indent=1)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(out + "\n")
            print(f"[module] 草稿已写入 {args.out}", file=sys.stderr)
        pools = mod["confirmed"] + mod["watching"]
        n_bull = sum(1 for e in pools if e.get("sentiment") == "bull")
        n_bear = sum(1 for e in pools if e.get("sentiment") == "bear")
        n_neu = len(pools) - n_bull - n_bear
        print(f"[module] 确认池 {len(mod['confirmed'])} 只 / 观察池 {len(mod['watching'])} 只"
              f"（口径：利好 {n_bull} 只 · 中性 {n_neu} 只 · 利空 {n_bear} 只，供 AI 复核校准）",
              file=sys.stderr)
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

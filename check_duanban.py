#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""断板反包形态真实核验（确定性脚本，供自动化 AI 生成四类标的时调用）

形态规则（用户定稿口径，R70 放宽）：
  1-5 个交易日前涨停(T) → 涨停次日(T+1) 1.1~3.5 倍量且断板（未续板）
  → T+2 缩量（< T+1）；T+3（若存在）低于断板日量即可（允许小幅反复，不要求逐日递减）
  → 收盘不破 T 日低点、不破 5 日线（MA5，容差 1%）

数据源（零 MCP，纯 HTTP）：
  - 涨停池：东财 push2ex getTopicZTPool（含 hybk 申万行业字段，date=YYYYMMDD 无横线）
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


# ---------- R91i: 板块实时涨幅 + 涨停日板块涨幅（东财行业板块，与 hybk 同口径） ----------
DELAY = "https://push2delay.eastmoney.com/api/qt/clist/get"
HIS = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EM_UT = "fa5c3db4f1ca16c5b6e3b9a3f3ae3dfa"

# ---------- R98: 同花顺行业板块口径（断板池板块相关数据） ----------
THS_INDUSTRY_URL = "https://q.10jqka.com.cn/thshy/"
THS_KLINE_REFERER = "https://stockpage.10jqka.com.cn/"

_board_map_cache = None
_board_pct_hist_cache = {}
_THS_BOARD_CACHE = None
_THS_KLINE_CACHE = {}


def curl_ths_text(url, timeout=20):
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout),
                       "-H", "User-Agent: " + UA, url],
                      capture_output=True, timeout=timeout + 10)
    return (r.stdout or b"")


def parse_ths_industries(html):
    """解析同花顺行业一览表。返回 [{code,name,pct}]；失败/不足返回 []。
    移植自 update_ashare_sectors.py（R97g）。"""
    if isinstance(html, (bytes, bytearray)):
        try:
            text = html.decode("utf-8")
        except UnicodeDecodeError:
            text = html.decode("gbk", errors="replace")
    else:
        text = str(html)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S)
    out = []
    for row in rows:
        m = re.search(r'thshy/detail/code/(\d+)[^>]*>([^<]+)</a>', row)
        if not m:
            continue
        code, name = m.group(1), m.group(2).strip()
        if not name:
            continue
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        tds = [re.sub(r"<[^>]+>", "", t).strip() for t in tds]
        ni = None
        for i, t in enumerate(tds):
            if t == name:
                ni = i
                break
        if ni is None or ni + 1 >= len(tds):
            continue
        def _num(s):
            s = s.replace("%", "").replace("亿", "").replace("万", "").replace(",", "")
            try:
                return float(s)
            except Exception:
                return None
        pct = _num(tds[ni + 1])
        if pct is None or pct < -15 or pct > 15:
            continue
        out.append({"code": code, "name": name, "pct": round(pct, 2)})
    return out


def fetch_ths_industries():
    raw = curl_ths_text(THS_INDUSTRY_URL, timeout=20)
    return parse_ths_industries(raw)


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
    if not ths_code:
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
        "证券Ⅱ": "证券", "装修装饰Ⅱ": "建筑装饰", "其他电子Ⅱ": "其他电子",
        "军工电子Ⅱ": "军工电子", "商用车": "汽车整车", "休闲食品": "食品加工制造",
        "旅游及景区": "旅游及酒店", "风电设备": "光伏设备", "电机Ⅱ": "通用设备",
        "装修建材": "建筑材料", "塑料": "塑料制品", "家居用品": "家居用品",
        "化学制药": "化学制药", "汽车零部件": "汽车零部件", "化学制品": "化学制品",
        "通用设备": "通用设备", "医疗服务": "医疗服务", "计算机设备": "计算机设备",
        "软件开发": "计算机设备", "专用设备": "专用设备", "化学原料": "化学原料",
        "光伏设备": "光伏设备", "半导体": "半导体", "消费电子": "消费电子",
        "光学光电子": "光学光电子", "医疗器械": "医疗器械", "电池": "电池",
        "白酒": "白酒", "小家电": "小家电", "通信设备": "通信设备", "钢铁": "钢铁",
        "证券": "证券", "汽车整车": "汽车整车", "银行": "银行",
        "建筑装饰": "建筑装饰", "房地产": "房地产", "元件": "元件",
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
    }
    if clean in SPECIAL and SPECIAL[clean] in ths_names:
        return SPECIAL[clean]
    if hybk in SPECIAL and SPECIAL[hybk] in ths_names:
        return SPECIAL[hybk]
    for name in ths_names:
        if len(clean) >= 2 and (name.startswith(clean) or clean.startswith(name)):
            return name
    return None


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
BOARD_KLINE_CACHE = "/tmp/duanban_board_kline_cache"
CLIST_HOSTS = ["https://push2delay.eastmoney.com",
               "https://82.push2.eastmoney.com",
               "https://push2.eastmoney.com"]
HIS_HOSTS = ["https://push2his.eastmoney.com",
             "https://92.push2his.eastmoney.com",
             "https://23.push2his.eastmoney.com"]


def _board_clist_all():
    """主源：clist 一次拉全量行业板块（f3 今日涨幅 / f109 3日涨幅），按主机轮询容错。
    成功返回 {name: {code, pct, pct3}}，全部失败返回 None。"""
    for host in CLIST_HOSTS:
        rows, pn = [], 1
        while pn <= 4:
            url = (f"{host}/api/qt/clist/get?pn={pn}&pz=500&po=1&np=1&fltt=2&invt=2"
                   f"&fid=f109&fs=m:90%2Bt:2&fields=f12,f14,f3,f109&ut={EM_UT}")
            j = get_json_curl(url, retries=1, gap=2)
            page = (j.get("data") or {}).get("diff") or [] if j else []
            if not page:
                rows = []
                break
            rows.extend(page)
            if len(page) < 500:
                break
            pn += 1
            time.sleep(0.3)
        if rows:
            m = {}
            for r in rows:
                name = str(r.get("f14") or "").strip()
                code = str(r.get("f12") or "").strip()
                try:
                    pct = float(r.get("f3"))
                    pct3 = float(r.get("f109"))
                except (TypeError, ValueError):
                    continue
                if name and code:
                    m[name] = {"code": code, "pct": pct, "pct3": pct3}
            if m:
                return m
        time.sleep(1)  # 换下一主机前稍歇
    return None


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
    """近 N 个交易日涨幅居前板块 TOP10（**同花顺行业口径**，R98）。
    主源：THS thshy 行业页面（50个同花顺大类行业，按当日涨幅排序）；
    备源：保留既有 topBoards（R91m 防护）。全部失败返回 []（前端隐藏模块）。
    每个板块附带近30根日K线（kline字段），供前端弹窗展示。"""
    # ---- 主源：同花顺行业页面（当日涨幅） ----
    ths = fetch_ths_industries()
    if len(ths) >= 10:
        ths.sort(key=lambda x: -x["pct"])
        picked = ths[:top]
        # 并行获取各板块 K 线（max_workers=5 防限频）
        def _mk(b):
            kl = fetch_ths_kline_full(b["code"], days=30)
            pct3 = None
            if kl and len(kl) >= 4:
                try:
                    pct3 = round((kl[-1][2] / kl[-4][2] - 1) * 100, 2)
                except Exception:
                    pass
            return {"code": b["code"], "name": b["name"],
                    "pct3": pct3, "pctToday": b["pct"],
                    "kline": kl}
        with ThreadPoolExecutor(max_workers=5) as exe:
            return list(exe.map(_mk, picked))
    # ---- 备源：保留既有 topBoards（R91m 防护） ----
    old = (D.get("duanban") or {}).get("topBoards") if isinstance(D, dict) else None
    if old:
        print("[warn] THS 板块榜不可达，保留既有 topBoards %d 条" % len(old), file=sys.stderr)
        return old
    return []


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
    return B


def _fmt_ref(r):
    """单条线索 → 小作文段落（kind 区分个股/板块消息；srcTag 标注来源）。"""
    kind = "个股消息" if r.get("kind") == "stock" else \
        f"板块消息（{r.get('kw') or r.get('sec') or ''}）"
    tag = (" " + str(r["srcTag"])) if r.get("srcTag") else ""
    head = " ".join(x for x in (r.get("time"), r.get("source") or r.get("src")) if x)
    title = f"《{r['title']}》" if r.get("title") else ""
    body = r.get("summary") or ""
    return f"{kind}{tag}：{head}{title}" + (f"：{body}" if body else "")


def tag_entries(entries, D):
    """R88 口径打标 + R89 依据小作文化（--module 口径，取代 R87 硬排除闸门）；
    R91j 修订：利好/利空判定只依据新闻内容——
      - 利好：真实个股/板块级新闻（走势形态描述不构成利好，过滤）；
      - 利空：个股/板块被利空新闻或 AI预测看空点名（当日涨跌不构成利空/利好，
        15点后利好新闻而当日下跌属洗盘，应标利好）；
      - 中性：暂无明确方向新闻依据（AI 可复核板块语义后改判）。
    每项附 sentiment / bullRefs / bearRefs（携带命中的真实新闻 title/summary/
    source/time）/ story（由命中的新闻摘要自动拼装的小作文底稿，
    自动化 AI 复核时须补充个股层面消息后改写）。"""
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
        bull_type = "stock" if any(r.get("kind") == "stock" for r in refs) else ("sector" if refs else None)
        # R91p 加权打标（2026-09-23 用户反馈修复：有研新材 12 条利好被 1 条间接利空
        # 一票否决）：个股级线索×2、板块级线索×1，bear 加权 > bull 加权才判 bear；
        # 否则有利好线索判 bull、无线索判 neutral。直接点名的个股级利空（bearNews
        # impacts stocks / AI预测看空个股）权重高，间接板块利空不再压过压倒性利好。
        def _w(rs):
            return sum(2 if r.get("kind") == "stock" else 1 for r in rs)
        bw, rw = _w(bear_refs), _w(refs)
        sent = "bear" if bw > rw else ("bull" if rw > bw else "neutral")
        # 小作文化：按主导方向拼装（bear 主导用利空依据，否则用利好依据）
        if sent == "bear" and bear_refs:
            story = "\n".join(_fmt_ref(r) for r in bear_refs[:3])
        elif refs:
            story = "\n".join(_fmt_ref(r) for r in refs[:3])
        else:
            story = "暂无明确利好/利空消息（板块与个股近1日无点名新闻；自动化 AI 复核时请检索个股公告/财报/大单等补充）"
        out.append(dict(e, sentiment=sent, bullType=bull_type,
                        bullRefs=refs, bearRefs=bear_refs, story=story))
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
            sector = p["hybk"] or "其他"
        entries.append({"code": p["code"], "name": p["name"],
                        "sector": sector, "ztDate": p["ztDate"],
                        "stage": p["stage_info"]["stage"], "form": p["stage_info"]["form"],
                        "pct": pct, "boardPctToday": bpt, "boardPctZt": bpz, "bkCode": bk_code,
                        "probability": None, "probNote": "",
                        "kline": bars})
    tagged = tag_entries(entries, D)
    confirmed = [e for e in tagged if e["stage"] == "全流程达标"]
    watching = [e for e in tagged if e["stage"] != "全流程达标"]
    # 观察池内更接近确认的（待企稳）排前，同阶段利空靠后
    watching.sort(key=lambda e: (0 if e["stage"] == "待企稳" else 1,
                                 {"bull": 0, "neutral": 1, "bear": 2}[e["sentiment"]]))
    return {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "确认池=走完「涨停→放量断板→缩量→不破T日低点与MA5」全流程；观察池=形态进行中、只差最后一根确认K线（待企稳＞待缩量）。每只标的按当日要闻/AI预测自动标注口径：利好（有利好新闻依据且未被看空）/利空（被利空新闻或AI预测看空点名）/中性（暂无明确新闻依据）；利好/利空只看新闻内容，当日涨跌与走势形态不作为依据（R91j）。口径与 probability 由自动化 AI 复核校准。",
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
        time.sleep(0.4)  # push2ex 限频保护
    if not valid_dates or not cand:
        if args.module:
            if any_fetch_failed:
                # 东财涨停池全源拉取失败（本地沙箱封锁东财域名）：保留既有 duanban 模块，
                # 避免把已部署的确认池/观察池/topBoards 清成空（R91m 防护）。
                # 仅在「网络失败」时触发；若涨停池真实为空（非失败）仍按原逻辑写空模块。
                try:
                    old_mod = load_dashboard(args.dashboard or os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), "data.js")).get("duanban") or {}
                    if old_mod.get("confirmed") or old_mod.get("watching") or old_mod.get("topBoards"):
                        old_mod.setdefault("generatedAt", "")
                        old_mod.setdefault("note", "东财涨停池不可达，保留既有模块")
                        print("[warn] 涨停池全源不可达，保留既有 duanban 模块"
                              "（确认池 %d / 观察池 %d）"
                              % (len(old_mod.get("confirmed") or []),
                                 len(old_mod.get("watching") or [])), file=sys.stderr)
                        print(json.dumps(old_mod, ensure_ascii=False, indent=1))
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
            except Exception:
                pass
            print(json.dumps(mod, ensure_ascii=False, indent=1))
            return
        print("[]" if args.json else "无达标候选（涨停池为空或不可用）")
        return

    # 最近涨停优先核验
    cands = sorted(cand.items(), key=lambda kv: max(kv[1]["zt"]), reverse=True)[:args.limit]

    # R93 时间预算（2026-09-23）：自动化环境对脚本有超时限制（此前串行抓 300 只候选
    # K 线 + 二轮重试常超 6 分钟，16:00 自动化中被 SIGTERM 杀掉、duanban 模块整体跳过）。
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

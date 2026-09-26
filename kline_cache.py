#!/usr/bin/env python3
"""K 线缓存共享模块（2026-09-26 谐波/吸筹扩展新增）。
harmonic_detect.py 与 accumulation_score.py 共用，避免同日重复抓取。

- 数据源：腾讯 ifzq fqkline（主源，与 check_duanban.py R81 口径一致），新浪备用。
- 缓存：项目内 .kline_cache/<code>.json，TTL 默认 20h（CACHE_TTL_H 可覆盖）；
  已在 .gitignore 中排除，不进仓库。
- 并行：ThreadPoolExecutor（默认 8 线程），总时间预算 DEADLINE_S（默认 300s，
  可用 KLINE_DEADLINE 环境变量覆盖），超时未抓到的标的按缺失处理。
- 约定（R91m/R91n）：抓取失败一律返回 None，由调用方保留旧值，禁止静默清场。
"""
import json, os, time, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE, '.kline_cache')
try:
    CACHE_TTL_H = float(os.environ.get('CACHE_TTL_H', '20'))
except ValueError:
    CACHE_TTL_H = 20.0
try:
    DEADLINE_S = float(os.environ.get('KLINE_DEADLINE', '300'))
except ValueError:
    DEADLINE_S = 300.0

T_START = time.time()


def _time_left():
    return max(1.0, DEADLINE_S - (time.time() - T_START))


def _curl_text(url, timeout=15, referer=None):
    import subprocess
    cmd = ['curl', '-s', '--max-time', str(timeout),
           '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)']
    if referer:
        cmd += ['-H', 'Referer: ' + referer]
    try:
        p = subprocess.run(cmd + [url], capture_output=True, timeout=timeout + 5)
    except Exception:
        return None
    if p and p.returncode == 0 and p.stdout.strip():
        return p.stdout.decode('utf-8', errors='replace')
    return None


def _tencent_code(code):
    return ('sh' if code.startswith('6') else 'sz') + code


def fetch_kline_tencent(code, days=320, retries=2):
    """腾讯 ifzq 日 K（前复权），返回 [{day,open,close,high,low,volume}] 或 None。"""
    tcode = _tencent_code(code)
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
           + tcode + ",day,,," + str(days) + ",qfq")
    for i in range(retries + 1):
        raw = _curl_text(url, timeout=15)
        if raw:
            try:
                j = json.loads(raw)
            except Exception:
                j = None
            if isinstance(j, dict) and str(j.get('code')) == '0' and j.get('data'):
                d = (j.get('data') or {}).get(tcode) or {}
                arr = d.get('qfqday') or d.get('day') or []
                out = []
                for a in arr:
                    if not isinstance(a, list) or len(a) < 6:
                        continue
                    try:
                        out.append({'day': str(a[0]), 'open': float(a[1]),
                                    'close': float(a[2]), 'high': float(a[3]),
                                    'low': float(a[4]), 'volume': float(a[5])})
                    except Exception:
                        continue
                if len(out) >= 30:
                    return out
        if i < retries:
            time.sleep(1)
    return None


def fetch_kline_sina(code, days=320, retries=1):
    """新浪日 K 备用源（jsonp 回调名固定，defineProperty 拦截无需——python 直接正则取 JSON）。"""
    import re
    tcode = _tencent_code(code)
    scale, dlen = 240, min(320, max(64, days))
    url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php=/CN_MarketDataService.getKLineData"
           f"?symbol={tcode}&scale={scale}&ma=no&datalen={dlen}")
    for i in range(retries + 1):
        raw = _curl_text(url, timeout=15, referer='https://finance.sina.com.cn')
        if raw:
            m = re.search(r'\[.*\]', raw, re.S)
            if m:
                try:
                    arr = json.loads(m.group(0))
                except Exception:
                    arr = None
                if isinstance(arr, list) and len(arr) >= 30:
                    out = []
                    for a in arr:
                        try:
                            out.append({'day': str(a.get('day')), 'open': float(a.get('open')),
                                        'close': float(a.get('close')), 'high': float(a.get('high')),
                                        'low': float(a.get('low')), 'volume': float(a.get('volume'))})
                        except Exception:
                            continue
                    if out:
                        return out
        if i < retries:
            time.sleep(1)
    return None


def _cache_path(code):
    return os.path.join(CACHE_DIR, code + '.json')


def _cache_valid(path):
    if not os.path.exists(path):
        return False
    try:
        age_h = (time.time() - os.path.getmtime(path)) / 3600.0
        return age_h < CACHE_TTL_H
    except Exception:
        return False


def get_kline(code, days=320):
    """带缓存取单只 K 线；先腾讯后新浪；失败 None。"""
    cp = _cache_path(code)
    if _cache_valid(cp):
        try:
            with open(cp, 'r', encoding='utf-8') as f:
                arr = json.load(f)
            if isinstance(arr, list) and len(arr) >= 30:
                return arr
        except Exception:
            pass
    if _time_left() <= 2:
        return None
    arr = fetch_kline_tencent(code, days=days)
    if not arr:
        arr = fetch_kline_sina(code, days=days)
    if arr:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = cp + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(arr, f, ensure_ascii=False)
            os.replace(tmp, cp)
        except Exception:
            pass
    return arr


def get_klines_bulk(codes, days=320, workers=8):
    """并行批量取 K 线，返回 {code: bars 或 None}。遵守总时间预算。"""
    out = {c: None for c in codes}
    todo = [c for c in codes if not _cache_valid(_cache_path(c))]
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(get_kline, c, days): c for c in todo}
            for fut in as_completed(futs):
                c = futs[fut]
                try:
                    out[c] = fut.result()
                except Exception:
                    out[c] = None
    for c in codes:
        if out.get(c) is None and _cache_valid(_cache_path(c)):
            try:
                with open(_cache_path(c), 'r', encoding='utf-8') as f:
                    out[c] = json.load(f)
            except Exception:
                pass
    return out


def collect_pool_codes(D):
    """从 data.js 收集 A 股标的池 code 集合（主板 60/00 过滤）。"""
    codes = set()

    def walk(o):
        if not o or isinstance(o, (str, int, float, bool)):
            return
        if isinstance(o, dict):
            c = o.get('code')
            if isinstance(c, str) and len(c) == 6 and c.isdigit():
                codes.add(c)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for k in ('ashare', 'aiPrediction', 'duanban'):
        walk(D.get(k))
    return sorted(c for c in codes if c.startswith('60') or c.startswith('00'))


def _kline_arr_to_dicts(arr):
    """把 [[day,o,c,h,l,v], ...] 转成 {day,open,close,high,low,volume} 列表。"""
    out = []
    for a in arr:
        if not isinstance(a, list) or len(a) < 6:
            continue
        try:
            out.append({'day': str(a[0]), 'open': float(a[1]), 'close': float(a[2]),
                        'high': float(a[3]), 'low': float(a[4]), 'volume': float(a[5])})
        except Exception:
            continue
    return out


def get_bars(code, D, days=320):
    """取单只 K 线（dict 列表）。优先用 data.js 内嵌 stkKlines（离线、无频限），
    其次走 get_kline 网络兜底。返回 list 或 None。"""
    sk = (D or {}).get('stkKlines') or {}
    arr = sk.get(code)
    if isinstance(arr, list) and len(arr) >= 30:
        bars = _kline_arr_to_dicts(arr)
        if bars:
            return bars[-days:] if days else bars
    return get_kline(code, days=days)

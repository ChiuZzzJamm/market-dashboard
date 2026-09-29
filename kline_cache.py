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


def collect_pool_names(D):
    """从 data.js 收集 code→name 映射（R100g：与 collect_pool_codes 同 walk 域，
    供 accumulation/power/harmonic 脚本补简称——stkKlineNames 缺名时不再渲染成
    「600613600613」双重代码）。"""
    names = {}

    def walk(o):
        if not o or isinstance(o, (str, int, float, bool)):
            return
        if isinstance(o, dict):
            c = o.get('code')
            if isinstance(c, str) and len(c) == 6 and c.isdigit():
                nm = o.get('name')
                if isinstance(nm, str) and nm and nm != c:
                    names.setdefault(c, nm)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for k in ('ashare', 'aiPrediction', 'duanban'):
        walk(D.get(k))
    return names


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


# ---------------- R100q：code→行业板块 共享映射（弹窗板块徽章全覆盖） ----------------

INDUSTRY_CACHE = os.path.join(CACHE_DIR, 'industry_map.json')


def sina_industry_map(max_nodes=999, node_deadline=None):
    """R100q：新浪行业分类全量 code→行业名 映射。

    源：newSinaHy.php（节点列表，GBK）+ Market_Center.getHQNodeData（成分，JSON/ASCII）。
    本机沙箱封锁东财 push2，新浪行业快照是唯一可全量覆盖任意 A 股代码的行业源。
    缓存 .kline_cache/industry_map.json（当日有效）；任一环节失败返回 {}（调用方
    退回站内字段映射，不清场）。"""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        today = time.strftime('%Y-%m-%d')
        if os.path.exists(INDUSTRY_CACHE):
            try:
                with open(INDUSTRY_CACHE, encoding='utf-8') as f:
                    cj = json.load(f)
                if cj.get('date') == today and isinstance(cj.get('map'), dict) and cj['map']:
                    return cj['map']
            except Exception:
                pass

        import subprocess
        def _gbk_curl(url, timeout=15):
            cmd = ['curl', '-s', '--max-time', str(timeout),
                   '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', url]
            p = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
            if not p or p.returncode != 0 or not p.stdout.strip():
                return None
            return p.stdout.decode('gbk', errors='replace')

        raw = _gbk_curl('http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php')
        if not raw or 'sinaindustry' not in raw:
            return {}
        import re as _re
        nodes = _re.findall(r'"(new_[A-Za-z0-9]+)"\s*:', raw)
        nodes = sorted(set(nodes))[:max_nodes]
        if not nodes:
            return {}
        t0 = time.time()
        out = {}
        completed = True
        for node in nodes:
            if node_deadline is not None and time.time() - t0 > node_deadline:
                completed = False  # R100q：超时截断 → 部分结果只本次可用，不写当日缓存
                break
            page = 1
            while True:
                url = ('http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
                       'Market_Center.getHQNodeData?page=%d&num=100&node=%s&sort=amount&asc=0'
                       % (page, node))
                txt = _gbk_curl(url)
                arr = None
                if txt:
                    try:
                        arr = json.loads(txt)
                    except Exception:
                        arr = None
                if not isinstance(arr, list) or not arr:
                    break
                for r in arr:
                    c = str((r or {}).get('code') or '')
                    if len(c) == 6 and c.isdigit():
                        out.setdefault(c, node)
                if len(arr) < 100 or page >= 30:
                    break
                page += 1
                time.sleep(0.12)
        if not out:
            return {}
        # 节点 id → 行业名（从 newSinaHy.php 原文解析，GBK 已解码）
        nm = {}
        for m in _re.finditer(r'"(new_[A-Za-z0-9]+)"\s*:\s*"\1,([^,]+),', raw):
            nm[m.group(1)] = m.group(2).strip()
        code_map = {c: nm.get(nd, nd) for c, nd in out.items()}
        if completed:  # 只有完整跑完全部节点才写当日缓存（部分结果不缓存，避免覆盖残缺）
            try:
                with open(INDUSTRY_CACHE, 'w', encoding='utf-8') as f:
                    json.dump({'date': today, 'count': len(code_map), 'map': code_map},
                              f, ensure_ascii=False)
            except Exception:
                pass
        return code_map
    except Exception:
        return {}


def fetch_sector_single(code, timeout=10):
    """R100z4y：单票行业兜底——新浪行业全量映射存在缺口（分页静默失败，~3000/5000 只），
    对缺口代码逐只抓「所属行业板块」页（vCI_CorpOtherInfo menu_num/2）提取行业名。
    返回行业名或 ''。任一环节失败返回 ''（调用方保留原值不清场）。"""
    code = str(code or '')
    if not (len(code) == 6 and code.isdigit()):
        return ''
    url = ('https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpOtherInfo/'
           'stockid/%s/menu_num/2.phtml' % code)
    try:
        import subprocess
        p = subprocess.run(['curl', '-s', '--max-time', str(timeout),
                            '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', url],
                           capture_output=True, timeout=timeout + 5)
        if not p or p.returncode != 0 or not p.stdout.strip():
            return ''
        txt = p.stdout.decode('gbk', errors='replace')
    except Exception:
        return ''
    import re
    for m in re.finditer(r'所属行业板块[\s\S]*?<td[^>]*>\s*([^<]+?)\s*</td>', txt):
        val = (m.group(1) or '').strip()
        if val and val not in ('所属行业板块', '同行业个股', '点击查看'):
            return val
    return ''


def collect_sectors(D, use_sina=True, node_deadline=180):
    """R100q：code→行业 映射（供 harmonic/power/accumulation 三脚本写 sector 字段，
    与前端 stkSecOf 同口径）。优先级：站内真实字段（断板池/精选/谐波池/连板 hybk/
    板块TOP成分/AI预测成分/已有池 sector）→ 新浪行业全量兜底。失败只降级不清场。"""
    sec = {}
    wanted = []  # R100z4y：站内池内代码清单（供单票行业兜底定位缺口）

    def put(c, s):
        c, s = str(c or ''), str(s or '').strip()
        if len(c) == 6 and c.isdigit():
            if c not in wanted:
                wanted.append(c)
            if s and s != c:
                sec.setdefault(c, s)

    db = (D or {}).get('duanban') or {}
    for key in ('confirmed', 'watching'):
        for e in db.get(key) or []:
            put(e.get('code'), e.get('sector'))
    for e in (db.get('star') or {}).get('picks') or []:
        put(e.get('code'), e.get('sector'))
    hz = (D or {}).get('harmonic') or {}
    for e in (hz.get('confirmPool') or []) + (hz.get('watchPool') or []):
        put(e.get('code'), e.get('sector'))
    A = (D or {}).get('ashare') or {}
    for e in A.get('lianban') or []:
        put(e.get('code'), e.get('hybk'))
    for s in (A.get('sectorsUp') or []) + (A.get('sectorsDown') or []):
        for t in s.get('tops') or []:
            put(t.get('code'), s.get('name'))
    for sct in ((D or {}).get('aiPrediction') or {}).get('sectors') or []:
        for st in sct.get('stocks') or []:
            put(st.get('code'), sct.get('sector'))
    for key, field in (('powerScreen', 'passed'), ('accumulation', 'scored')):
        for e in ((D or {}).get(key) or {}).get(field) or []:
            put(e.get('code'), e.get('sector'))

    if use_sina:
        sm = sina_industry_map(node_deadline=node_deadline)
        for c, s in sm.items():
            put(c, s)
    # R100z4y：单票行业兜底——池内仍缺行业的（新浪全量映射有缺口）逐只抓所属行业页。
    # 上限 40 只 + 总预算 45s 防拖垮 16:00 流水线；失败保留缺省（前端另有 stkSectorOf 兜底）。
    missing = [c for c in wanted if c not in sec][:40]
    t_single = time.time()
    for c in missing:
        if node_deadline is not None and time.time() - t_single > 45:
            break
        s = fetch_sector_single(c)
        if s:
            sec.setdefault(c, s)
        time.sleep(0.25)
    return sec

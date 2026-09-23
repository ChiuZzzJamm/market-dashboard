#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股板块/指数/涨跌家数抓取（零 MCP 依赖，全部 HTTP 直连）：
  - 指数+成交额：腾讯 gtimg（sh000001/sz399001/sz399006/sh000688/sh000016，另取 sz399106 算两市成交额）
  - 涨跌家数：R97g 起主源=同花顺行业一览表（q.10jqka.com.cn/thshy，行业 up/down 家数求和，含 ST 口径）；
    涨停数=同花顺涨停池 total；跌停数=同花顺无公开源，沿用上一轮（东财 push2delay 快照作兜底）。
    兜底：同花顺不可达时回退东财 push2ex getTopicZDFenBu + 风险警示板(b:BK0511) 补充 ST。
  - 行业板块涨跌 TOP5/BOTTOM5 + 全景：R97f 起主源=同花顺行业一览表（q.10jqka.com.cn/thshy，数字准+字段全：涨跌幅/净流入/涨跌家数/领涨股），
    R98d 起领涨/领跌板块的 tops=同花顺行业详情页成分股 TOP5（desc/asc 各取 5 只，替代一览表单条领涨股），
    R98h 起一览表分页抓全（90 个行业，原只取第 1 页 50 个）且 allBoards/行业全景富化字段：
    主力净流入(netInflow)/涨停数(ztCount,详情页降序首页涨幅>=阈值计数)/领涨股(lead,leadPct)/
    涨跌家数(upCount,downCount)/近5日涨幅(pct5,板块日K close[-1]/close[-6]-1)，
    备源=东财 push2delay clist（fs=m:90+t:2 行业板块，注意 + 必须写成 %2B）/ 新浪行业，全失败保留上一轮(R91m)；
    sectorSource 字段记录实际命中源（ths/em/prev）供前端标注口径。断板池/个股 hybk 仍用申万，不在本脚本改动范围。
  - 主力资金流入/流出 TOP3：R97g 起主源=同花顺行业资金流页（data.10jqka.com.cn/funds/hyzjl，净额(亿)排序）；
    兜底：同花顺不可达时回退东财 push2 clist 按 f62 排序（f62 单位=元，换算亿元）。
写入 data.js 的 ashare 字段：tradeDate/status/indices/breadth/sectorsUp/sectorsDown/fundIn/fundOut。
不触碰 summary/outlook/bullNews/bearNews/fundNote（由自动化 AI 步骤撰写/保留）。
失败策略：单个数据源失败保留原值（并在 updatedAt 诚实注明保留项）；全部主源(同花顺)+兜底(东财)
  数据源（涨跌家数/行业板块/主力资金/连板梯队）均失败时以退出码 1 退出且不写 data.js（防旧数据
  伪装当日收盘），并输出 [EM_FAIL] 标记行供自动化 AI 走 WebFetch 云端兜底回填（R91n/R97g）。
"""
import json, re, subprocess, os, sys, time, argparse, random
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutTimeout
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
        it = {"name": name, "point": point, "changePct": pct}
        try:
            # 开盘涨跌幅：今开(p[5])/昨收(p[4])-1，供盘面叙事区分高开/低开与高走/低走
            _prev, _open = float(p[4]), float(p[5])
            if _prev > 0 and _open > 0:
                it["openPct"] = round((_open / _prev - 1) * 100, 2)
        except Exception:
            pass
        out.append(it)
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

# 涨停池缓存：同一交易日内多次调用只请求一次东财（push2ex 限频 rc:102，避免重复打）。
_zt_pool_cache = {}

def fetch_zt_pool_raw(date):
    """返回东财涨停池原始记录列表；失败(get_json 返回 None)返回 None。
    跌停池(getTopicDTPool)剔除 ST 股，不作数据源。"""
    if date in _zt_pool_cache:
        return _zt_pool_cache[date]
    d = get_json(f"{EX}/getTopicZTPool?ut={UT_ZT}&dpt=wz.ztzt&Pageindex=0&pagesize=600&sort=fbt%3Aasc&date={date}")
    try:
        pool = (d.get("data") or {}).get("pool") or []
    except Exception:
        pool = None
    _zt_pool_cache[date] = pool
    return pool

def fetch_zt_codes(date):
    """返回东财涨停池代码集（tc 与三大 App 一致）；失败返回 None。"""
    pool = fetch_zt_pool_raw(date)
    if pool is None:
        return None
    try:
        return {p["c"] for p in pool}
    except Exception:
        return None

def fetch_zt_ladder(date):
    """连板梯队：从东财涨停池提取 {code,name,pct,lbc,reason,hybk,fund}，按 lbc 降序、pct 降序。
    失败返回 None（调用方保留原值）；成功但空池返回 []。
    连板数字段为东财标准 lbc；涨停原因字段为 reason（东财整理的涨停揭秘）。二者均防御式读取，
    字段缺失不崩（lbc 缺省按 1 计，reason 缺省空串）。"""
    pool = fetch_zt_pool_raw(date)
    if pool is None:
        return None
    try:
        out = []
        for p in pool:
            raw_lbc = p.get("lbc")
            try:
                lbc = int(raw_lbc) if raw_lbc not in (None, "") else 1
            except Exception:
                lbc = 1
            pct = p.get("zdp")
            try:
                fpct = float(pct) if pct not in (None, "") else 0
            except Exception:
                fpct = 0
            out.append({
                "code": str(p.get("c") or ""),
                "name": str(p.get("n") or ""),
                "pct": pct,
                "lbc": lbc,
                "reason": str(p.get("reason") or "").strip(),
                "hybk": str(p.get("hybk") or "").strip(),
                "fund": p.get("fund"),
            })
        out.sort(key=lambda x: (-(x["lbc"] or 1), -fpct_of(x)))
        # 调试输出实际字段，便于核对 lbc/reason 是否存在（生产日志可见）
        keys = set()
        for p in pool:
            keys.update(p.keys())
        print("[info] 涨停池字段: " + ",".join(sorted(keys)))
        return out
    except Exception as e:
        print("[warn] 连板梯队解析失败：" + str(e))
        return None

def fpct_of(x):
    try:
        return float(x["pct"]) if x.get("pct") not in (None, "") else 0
    except Exception:
        return 0

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
            d = get_json(f"{DELAY}?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f12&fs={seg}"
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
        # 2026-07-06 新规：沪深主板 ST/*ST 涨跌幅 5%→10%，与主板普通股并轨；
        # 创业板/科创板 ST 仍 ±20%、北交所 ±30%。全市场已无 ±5% 档，按板块判定即可。
        if code.startswith(("30", "68")): return Decimal("0.20")   # 创业板/科创板 ±20%（含 ST）
        if code.startswith(("4", "8", "92")): return Decimal("0.30")  # 北交所 ±30%
        return Decimal("0.10")                                     # 沪深主板 ±10%（含 ST/*ST）

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

def fetch_top5(board_code, po):
    """板块成分股 TOP5：po=1 涨幅前5（领涨）；po=0 跌幅前5（领跌）。过滤退市残留/停牌/无成交量行。"""
    url = (f"{DELAY}?pn=1&pz=6&po={po}&np=1&fltt=2&invt=2&fid=f3"
           f"&fs=b%3A{board_code}&fields=f2,f3,f5,f14,f17,f18&ut={UT}")
    d = get_json(url)
    if not d:
        return []
    rows = (d.get("data") or {}).get("diff") or []
    out = []
    for r in rows:
        pct, vol, name = to_f(r.get("f3")), to_f(r.get("f5")), r.get("f14")
        if pct is None or vol is None or vol <= 0 or not name:
            continue
        # 同向过滤：领涨板块(po=1)只取涨幅>=0、领跌板块(po=0)只取跌幅<=0，避免混入反向股
        if po == 1 and pct < 0:
            continue
        if po == 0 and pct > 0:
            continue
        op = None
        _prev, _open = to_f(r.get("f18")), to_f(r.get("f17"))
        if _prev and _open and _prev > 0 and _open > 0:
            op = round((_open / _prev - 1) * 100, 2)  # 个股开盘涨跌幅
        entry = {"name": name, "changePct": round(pct, 2)}
        if op is not None:
            entry["openPct"] = op
        out.append(entry)
        if len(out) >= 5:
            break
    return out

def build_sectors():
    up_rows = fetch_boards("f3", 1)    # 按涨跌幅降序 → 领涨
    time.sleep(2)
    down_rows = fetch_boards("f3", 0)  # 升序 → 领跌
    if up_rows and down_rows:
        r = _build_sectors_em(up_rows, down_rows)
        # 东财数据不足（ups/downs < 3）时也回退新浪，避免返回 None 触发 em_failed
        if r[0] is not None and r[1] is not None:
            return r
    # ---- R93 新浪备用源（2026-09-23）：东财 push2/push2delay 被 WAF 按 IP 段封锁
    # （本地+WebFetch 云端均空回复，push2ex 幸存），行业板块/全板块榜改用新浪行业
    # 板块口径兜底（~90 个细分行业，含板块涨跌幅；成分股 TOP5 走新浪节点接口）。
    print("[info] 东财 clist 行业板块不可达/不足，改用新浪行业板块备用源")
    return build_sectors_sina()

def _build_sectors_em(up_rows, down_rows):
    def mk(r, po):
        pct = to_f(r.get("f3"))
        if pct is None:
            return None
        item = {"name": r.get("f14"), "pct": round(pct, 2)}
        # tops = 该板块成分股 TOP5（涨板块取涨幅前5，跌板块取跌幅前5）
        if r.get("f12"):
            item["tops"] = fetch_top5(r["f12"], po)
        # 板块开盘涨跌近似 = 成分股今开涨跌幅均值（东财板块级无今开字段），供盘面叙事用
        _ops = [t["openPct"] for t in item.get("tops", []) if isinstance(t, dict) and "openPct" in t]
        if len(_ops) >= 2:
            item["openPct"] = round(sum(_ops) / len(_ops), 2)
        return item
    ups = [x for x in (mk(r, 1) for r in up_rows) if x][:5]
    downs = []
    for r in down_rows:
        if len(downs) >= 5:
            break
        x = mk(r, 0)
        if x:
            downs.append(x)
            time.sleep(1)  # 成分股请求间隔，防限流
    if len(ups) < 3 or len(downs) < 3:
        return None, None
    return ups, downs

# ---------- R93: 新浪行业板块备用源（东财 push2 被 WAF 封锁时兜底） ----------
SINA_BOARD_LIST = "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param=hangye"
SINA_NODE = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData?page=1&num=40&sort=changepercent&asc={asc}&node={node}")
SINA_REFERER = "https://finance.sina.com.cn"

def curl_referer(url, timeout=15):
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout),
                        "-H", "User-Agent: " + UA, "-H", "Referer: " + SINA_REFERER, url],
                       capture_output=True, timeout=timeout + 10)
    return r.stdout or b""

def fetch_sina_boards():
    """新浪行业板块列表（GBK），返回 {name: {"code": node, "pct": 涨跌幅}}；失败返回 {}。
    字段: code,name,家数,均价,涨跌额,涨跌幅%,总成交量,总成交额,领涨股code,领涨股pct,..."""
    raw = curl_referer(SINA_BOARD_LIST).decode("gbk", errors="replace")
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return {}
    out = {}
    for v in obj.values():
        parts = str(v).split(",")
        if len(parts) < 6:
            continue
        name = parts[1].strip()
        try:
            pct = float(parts[5])
        except (ValueError, IndexError):
            continue
        if name:
            out[name] = {"code": parts[0].strip(), "pct": pct}
    return out

def fetch_sina_node_tops(node, po):
    """新浪板块成分股 TOP5（po=1 涨幅前5 / po=0 跌幅前5），口径同 fetch_top5。失败返回 []。"""
    txt = curl_referer(SINA_NODE.format(asc=0 if po == 1 else 1, node=node))
    txt = txt.decode("utf-8", errors="replace").strip()
    try:
        rows = json.loads(txt)
    except Exception:
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            pct = float(r.get("changepercent"))
            vol = float(r.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if vol <= 0 or not r.get("name"):
            continue
        if po == 1 and pct < 0:
            continue
        if po == 0 and pct > 0:
            continue
        entry = {"name": r["name"], "changePct": round(pct, 2)}
        try:
            _o, _p = float(r.get("open") or 0), float(r.get("settlement") or 0)
            if _o > 0 and _p > 0:
                entry["openPct"] = round((_o / _p - 1) * 100, 2)
        except Exception:
            pass
        out.append(entry)
        if len(out) >= 5:
            break
    return out

def build_sectors_sina():
    """新浪行业板块口径的领涨/领跌 TOP5（含 tops/openPct）。失败返回 (None, None)。"""
    boards = fetch_sina_boards()
    if len(boards) < 10:
        return None, None
    ranked = sorted(boards.items(), key=lambda kv: -kv[1]["pct"])
    sel = [(n, b, 1) for n, b in ranked[:5]] + [(n, b, 0) for n, b in ranked[-5:]]
    ups, downs = [], []
    for name, b, po in sel:
        item = {"name": name, "pct": round(b["pct"], 2)}
        tops = fetch_sina_node_tops(b["code"], po)
        if tops:
            item["tops"] = tops
            _ops = [t["openPct"] for t in tops if isinstance(t, dict) and "openPct" in t]
            if len(_ops) >= 2:
                item["openPct"] = round(sum(_ops) / len(_ops), 2)
        (ups if po == 1 else downs).append(item)
        time.sleep(0.3)  # 新浪限频保护
    if len(ups) < 3 or len(downs) < 3:
        return None, None
    return ups, downs

# ---------- R97f 方案A：同花顺行业板块（主源，数字准+字段全；云端可达，HTML 解析） ----------
# 用户选定方案 A：全景/异动/TOP 用同花顺行业口径（涨跌幅+净流入+涨跌家数+领涨股），
# 断板池/个股保持申万。多源兜底：同花顺(主) → 东财 push2delay(备) → 新浪(备) → 保留上一轮(R91m)。
# 注意：沙箱出口 IP 被同花顺 Nginx forbidden，故本地跑会落备源；云端自动化 IP 通常可达。
THS_URL = "https://q.10jqka.com.cn/thshy/"
THS_REFERER = "https://q.10jqka.com.cn/"

def curl_ths_url(url, timeout=20):
    """通用同花顺页面抓取（R98h）：与 curl_ths 同头，URL 可变。返回 bytes。"""
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout),
                       "-H", "User-Agent: " + UA,
                       "-H", "Referer: " + THS_REFERER,
                       "-H", "Accept-Language: zh-CN,zh;q=0.9",
                       url], capture_output=True, timeout=timeout + 10)
    return r.stdout or b""

def curl_ths(timeout=20):
    return curl_ths_url(THS_URL, timeout=timeout)

def parse_ths_industries(html):
    """解析同花顺行业一览表。返回 [{code,name,pct,netInflow,up,down,lead,leadPct}]；失败/不足返回 []。
    列顺序（相对行业名所在列 ni）：ni+1 涨跌幅 / ni+4 净流入(亿) / ni+5 上涨家数 / ni+6 下跌家数
        / ni+8 领涨股 / ni+10 领涨股涨跌幅。涨跌幅做 [-15,15] 合理性校验防列偏移错位。"""
    if isinstance(html, (bytes, bytearray)):
        # 同花顺行业页为 GBK 编码，utf-8 直接解会乱码；先试 utf-8 失败回退 gbk
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
        if ni is None or ni + 10 >= len(tds):
            continue
        def num(s):
            s = s.replace("%", "").replace("亿", "").replace("万", "").replace(",", "")
            try:
                return float(s)
            except Exception:
                return None
        pct = num(tds[ni + 1])
        if pct is None or pct < -15 or pct > 15:
            continue
        def i2(s):
            v = num(s)
            return int(v) if v is not None else None
        out.append({
            "code": code, "name": name, "pct": round(pct, 2),
            "netInflow": num(tds[ni + 4]),
            "up": i2(tds[ni + 5]),
            "down": i2(tds[ni + 6]),
            "lead": (tds[ni + 8] if ni + 8 < len(tds) else None),
            "leadPct": num(tds[ni + 10]),
        })
    return out

def fetch_ths_industries():
    """R98h：一览表分页抓全（第1页50个 + 第2页40个 = 90 个同花顺行业，原来只取第1页漏 40 个）。
    第2页用非 ajax 整页 URL（/thshy/index/page/2/，实测可用；ajax/1/ 会触发反爬跳转）。
    按 code 去重合并；页数异常时至少保留第 1 页（>=10 个即视为成功）。"""
    seen, out = set(), []
    html1 = curl_ths()
    for x in parse_ths_industries(html1):
        if x["code"] not in seen:
            seen.add(x["code"])
            out.append(x)
    if len(out) >= 10:
        for page in range(2, 4):  # 最多抓到第3页防死循环
            time.sleep(1.0 + random.random())
            html_p = curl_ths_url(f"https://q.10jqka.com.cn/thshy/index/page/{page}/")
            rows = parse_ths_industries(html_p)
            fresh = [x for x in rows if x["code"] not in seen]
            if not fresh:
                break
            for x in fresh:
                seen.add(x["code"])
                out.append(x)
            if len(rows) < 10:  # 不足一页说明已到尾页
                break
    return out

def fetch_ths_tops(ths_code, po, timeout=15):
    """同花顺行业详情页成分股 TOP5（R98d）：po=1 领涨（详情页默认按涨跌幅 desc）、
    po=0 领跌（/order/asc/）。解析 m-pager-table 行（列：0序号 1代码 2名称 3现价 4涨跌幅）。
    反爬跳转页约 412 字节，用长度阈值过滤。失败返回 []（调用方回退单条领涨股）。
    注意：成分股含创业板/北交所（如实展示板块构成，非四类标的池，不受 60/00 硬约束）。"""
    if not ths_code:
        return []
    url = (f"https://q.10jqka.com.cn/thshy/detail/order/asc/page/1/code/{ths_code}/"
           if po == 0 else
           f"https://q.10jqka.com.cn/thshy/detail/code/{ths_code}/")
    try:
        r = subprocess.run(["curl", "-s", "--max-time", str(timeout),
                            "-H", "User-Agent: " + UA,
                            "-H", "Referer: " + THS_REFERER,
                            "-H", "Accept-Language: zh-CN,zh;q=0.9",
                            url], capture_output=True, timeout=timeout + 10)
        raw = r.stdout or b""
    except Exception:
        return []
    if len(raw) < 5000:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    i = text.find("m-pager-table")
    if i < 0:
        return []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", text[i:i + 12000], re.S)
    out = []
    for row in rows:
        tds = [re.sub(r"<[^>]+>", "", t).strip()
               for t in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(tds) < 5 or not re.fullmatch(r"\d{6}", tds[1] or ""):
            continue
        try:
            pct = float(tds[4].replace("%", ""))
        except Exception:
            continue
        if not tds[2]:
            continue
        # 同向过滤：领涨页只取涨幅>=0、领跌页只取跌幅<=0，避免混入反向股
        if po == 1 and pct < 0:
            continue
        if po == 0 and pct > 0:
            continue
        # R98i：带 code——前端 tops 芯片点击直接拿代码取K线，免 smartbox 反查（峆一药业等冷门名反查会失败）
        out.append({"name": tds[2], "code": tds[1], "changePct": round(pct, 2)})
        if len(out) >= 5:
            break
    return out

def enrich_ths_tops(ups, downs, gap=0.8):
    """为同花顺口径的领涨/领跌板块逐个拉取成分股 TOP5（R98d）。
    成功则覆盖单条领涨股 tops；失败保留原 tops（单条领涨股），不阻塞。"""
    jobs = [(u, 1) for u in (ups or [])] + [(d, 0) for d in (downs or [])]
    ok = 0
    for idx, (it, po) in enumerate(jobs):
        t5 = fetch_ths_tops(it.get("thsCode"), po)
        if t5:
            it["tops"] = t5
            ok += 1
        if idx < len(jobs) - 1:
            time.sleep(gap)
    print(f"[info] 同花顺成分股 TOP5 拉取：{ok}/{len(jobs)} 个板块成功")

def _zt_threshold(code):
    """按代码前缀取涨停幅度阈值：主板 10%、创业/科创 20%、北交 30%（R98h）。
    用于从涨幅近似判定涨停（>=阈值即视为涨停，误差 <=0.1 个百分点）。"""
    c = str(code or "")
    if c.startswith(("300", "301", "688", "689")):
        return 19.9
    if c.startswith(("43", "82", "83", "87", "88", "92")):
        return 29.9
    return 9.9

def fetch_ths_board_stats(ths_code, timeout=15):
    """R98h：单板块富化 {ztCount, pct5}。
    ztCount=板块详情页涨跌幅降序首页中涨停家数（涨幅>=该股阈值即计入，降序遇首个低于阈值即停；
    首页 21 行，涨停股必然在列；极端日单板块 >21 只涨停会低估，属可接受近似）。
    pct5=近5个交易日累计涨幅（板块日K close[-1]/close[-6]-1；K线不足6根时用首根兜底）。
    任一失败字段留 None（前端显示 --），绝不抛错。"""
    res = {"ztCount": None, "pct5": None}
    if not ths_code:
        return res
    # 1) 涨停数：详情页降序首页
    try:
        raw = curl_ths_url(
            f"https://q.10jqka.com.cn/thshy/detail/order/desc/page/1/code/{ths_code}/",
            timeout=timeout)
        if len(raw) >= 5000:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="replace")
            i = text.find("m-pager-table")
            if i >= 0:
                zt = 0
                for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text[i:i + 12000], re.S):
                    tds = [re.sub(r"<[^>]+>", "", t).strip()
                           for t in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
                    if len(tds) < 5 or not re.fullmatch(r"\d{6}", tds[1] or ""):
                        continue
                    try:
                        pct = float(tds[4].replace("%", ""))
                    except Exception:
                        continue
                    if pct >= _zt_threshold(tds[1]):
                        zt += 1
                    else:
                        break  # 降序排列，低于阈值即可停止
                res["ztCount"] = zt
    except Exception:
        pass
    # 2) 近5日涨幅：板块日K线（d.10jqka CDN，与 check_duanban 板块K线同源）
    try:
        r = subprocess.run(["curl", "-s", "--max-time", str(timeout),
                            "-H", "User-Agent: " + UA,
                            f"https://d.10jqka.com.cn/v4/line/bk_{ths_code}/01/last.js"],
                           capture_output=True, timeout=timeout + 10)
        m = re.search(r"\((\{.*\})\)", (r.stdout or b"").decode("utf-8", errors="replace"))
        if m:
            d = json.loads(m.group(1))
            closes = []
            for bar in (d.get("data") or "").split(";"):
                p = bar.split(",")
                if len(p) >= 5:
                    try:
                        closes.append(float(p[4]))
                    except Exception:
                        pass
            if len(closes) >= 6 and closes[-6] > 0:
                res["pct5"] = round((closes[-1] / closes[-6] - 1) * 100, 2)
            elif len(closes) >= 2 and closes[0] > 0:
                res["pct5"] = round((closes[-1] / closes[0] - 1) * 100, 2)
    except Exception:
        pass
    return res

def enrich_boards_full(all_b, deadline=None):
    """R98h：为 allBoards 全部板块并行补 ztCount/pct5（3 线程 + 全局时间预算）。
    预算默认 240s（环境变量 THS_ENRICH_DEADLINE 可覆盖），超时未完成板块字段留空。
    单板块失败留 None 不影响整体；本函数自身不抛错（调用方再兜一层）。"""
    if not all_b:
        return
    if deadline is None:
        deadline = float(os.environ.get("THS_ENRICH_DEADLINE") or 240)
    t0 = time.time()
    targets = [b for b in all_b if b.get("code")]
    def _job(b):
        time.sleep(random.uniform(0.1, 0.4))  # 轻微抖动降反爬风险
        st = fetch_ths_board_stats(b["code"])
        b["ztCount"] = st["ztCount"]
        b["pct5"] = st["pct5"]
    done = 0
    try:
        with ThreadPoolExecutor(max_workers=3) as exe:
            futs = [exe.submit(_job, b) for b in targets]
            for fut in as_completed(futs, timeout=deadline):
                try:
                    fut.result()
                    done += 1
                except Exception:
                    pass
    except FutTimeout:
        print(f"[warn] allBoards 富化超时（预算 {deadline}s），未完成板块涨停数/近5日留空")
    print(f"[info] allBoards 富化（涨停数/近5日）：{done}/{len(targets)} 个板块成功，"
          f"耗时 {round(time.time() - t0, 1)}s")

def _ths_to_item(x):
    it = {"name": x["name"], "pct": x["pct"], "thsCode": x["code"]}
    if x.get("lead"):
        it["tops"] = [{"name": x["lead"], "changePct": x["leadPct"] if x["leadPct"] is not None else 0}]
    if x.get("netInflow") is not None:
        it["netInflow"] = x["netInflow"]
    if x.get("up") is not None:
        it["upCount"] = x["up"]
    if x.get("down") is not None:
        it["downCount"] = x["down"]
    return it

def build_sectors_unified(prev_up, prev_down, prev_all, ths_list=None):
    """行业板块统一构建：同花顺(主)→东财push2delay(备)→新浪(备)→保留上一轮(R91m)。
    返回 (sectorsUp, sectorsDown, allBoards, source)；source∈{ths,em,prev}。
    ths_list: 可选预取的同花顺行业列表（避免重复请求）。"""
    # 1) 同花顺主源
    ths = ths_list if ths_list is not None else fetch_ths_industries()
    if len(ths) >= 10:
        ranked = sorted(ths, key=lambda x: -x["pct"])
        ups = [_ths_to_item(x) for x in ranked[:5]]
        downs = [_ths_to_item(x) for x in sorted(ths, key=lambda x: x["pct"])[:5]]
        # R98d：领涨/领跌板块补齐成分股 TOP5（详情页 desc/asc），失败保留单条领涨股
        enrich_ths_tops(ups, downs)
        all_b = [{"code": x["code"], "name": x["name"], "pct": x["pct"],
                  "netInflow": x["netInflow"], "upCount": x["up"], "downCount": x["down"],
                  "lead": x.get("lead"), "leadPct": x.get("leadPct")}
                 for x in ths]
        # R98h：全板块富化（涨停数 ztCount / 近5日涨幅 pct5），失败字段留空不阻塞主流程
        try:
            enrich_boards_full(all_b)
        except Exception as _e:
            print(f"[warn] allBoards 富化异常（保留基础字段）：{_e}")
        print(f"[info] 行业板块主源=同花顺（{len(ths)} 个行业）")
        return ups, downs, all_b, "ths"
    # 2) 东财 push2delay 备源（原有逻辑）
    print("[info] 同花顺不可达/不足，回退东财 push2delay 行业板块")
    up_rows = fetch_boards("f3", 1)
    time.sleep(1)
    down_rows = fetch_boards("f3", 0)
    if up_rows and down_rows:
        r = _build_sectors_em(up_rows, down_rows)
        if r[0] is not None and r[1] is not None:
            all_b = build_all_boards()
            if all_b:
                return r[0], r[1], all_b, "em"
    # 3) 新浪备源
    r2 = build_sectors_sina()
    if r2[0] is not None and r2[1] is not None:
        sb = fetch_sina_boards()
        all_b = [{"code": (b.get("code") or n), "name": n, "pct": round(b["pct"], 2)}
                 for n, b in sb.items() if isinstance(b, dict) and b.get("pct") is not None][:120]
        print(f"[info] 行业板块回退新浪口径（{len(all_b)} 个板块）")
        return r2[0], r2[1], all_b, "em"
    # 4) 保留上一轮（R91m）
    print("[warn] 行业板块全部源失败，保留上一轮数据")
    return (prev_up or []), (prev_down or []), (prev_all or []), "prev"

# ---------- R97g: 同花顺涨停池/连板/资金流（替代东财，除断板池 topBoards 外全换同花顺） ----------
THS_DATACENTER_REFERER = "https://data.10jqka.com.cn/datacenterph/limitup/limtupInfo.html"
THS_LIMITUP_API = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
THS_FUND_URL = "https://data.10jqka.com.cn/funds/hyzjl/"
THS_FUND_REFERER = "https://data.10jqka.com.cn/"

def curl_ths_json(url, timeout=20):
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout),
                       "-H", "User-Agent: " + UA,
                       "-H", "Referer: " + THS_DATACENTER_REFERER, url],
                      capture_output=True, timeout=timeout + 10)
    try:
        return json.loads((r.stdout or b"").decode("utf-8", errors="replace"))
    except Exception:
        return None

def curl_ths_html(url, referer, timeout=20):
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout),
                       "-H", "User-Agent: " + UA,
                       "-H", "Referer: " + referer, url],
                      capture_output=True, timeout=timeout + 10)
    raw = r.stdout or b""
    try:
        return raw.decode("gbk", errors="replace")
    except Exception:
        return raw.decode("utf-8", errors="replace")

def _ths_lbc_of(item):
    """从 high_days_value 解连板板数：高 16 位=涨停板数（65537=首板,196611=3天3板,458761=9天7板）。"""
    v = item.get("high_days_value") or 0
    try:
        lbc = (int(v) >> 16) & 0xFFFF
    except Exception:
        lbc = 0
    if lbc <= 0:  # 兜底：从 high_days 文本解析
        s = str(item.get("high_days") or "")
        m = re.search(r"(\d+)\s*连板", s)
        if m:
            lbc = int(m.group(1))
        elif "首板" in s:
            lbc = 1
        else:
            lbc = 1
    return lbc

def fetch_ths_limit_up(date=None):
    """同花顺涨停池 JSON。返回 (items, total)；失败返回 (None, None)。
    items: [{code,name,pct,lbc,reason,high_days,open_num,limit_up_type,hybk}]。"""
    if date is None:
        date = datetime.now(TZ8).strftime("%Y%m%d")
    d = curl_ths_json(f"{THS_LIMITUP_API}?page=1&limit=200&field=199112,10,9001,330323,330324,330325,9002,330329,133971"
                      f"&filter=HS,GEM2STAR&order_field=330324&order_type=0&date={date}")
    if not d or d.get("status_code") not in (0, None) or not (d.get("data") or {}).get("info"):
        return None, None
    info = d["data"]["info"]
    total = ((d.get("data") or {}).get("page") or {}).get("total", len(info))
    out = []
    for it in info:
        code = str(it.get("code") or "")
        if not code:
            continue
        pct = it.get("change_rate")
        try:
            pct = float(pct) if pct not in (None, "") else 0
        except Exception:
            pct = 0
        out.append({
            "code": code,
            "name": str(it.get("name") or ""),
            "pct": pct,
            "lbc": _ths_lbc_of(it),
            "reason": str(it.get("reason_type") or "").strip(),
            "high_days": str(it.get("high_days") or ""),
            "open_num": it.get("open_num"),
            "limit_up_type": str(it.get("limit_up_type") or ""),
            "hybk": None,
        })
    return out, total

def fetch_ths_lianban(date=None):
    """连板梯队：同花顺涨停池中 lbc>=2 的标的，按 lbc 降序、pct 降序。失败返回 None。"""
    items, _ = fetch_ths_limit_up(date)
    if items is None:
        return None
    lb = [x for x in items if (x.get("lbc") or 1) >= 2]
    lb.sort(key=lambda x: (-(x["lbc"] or 1), -fpct_of(x)))
    return lb

def fetch_ths_funds():
    """同花顺行业资金流（hyzjl，GBK 服务端渲染）。返回 (fundIn, fundOut) 各 TOP3 [{name,value}]；失败 (None,None)。
    列序：序号(0)/行业(1)/行业指数(2)/涨跌幅(3)/流入(4)/流出(5)/净额(6)/公司家数(7)/领涨股(8)/领涨股涨跌幅(9)/当前价(10)；
    净额(亿)=列6=主力净流入。"""
    html = curl_ths_html(THS_FUND_URL, THS_FUND_REFERER)
    if not html:
        return None, None
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    data = []
    for row in rows:
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 7:
            continue
        name = cells[1]
        if not name or not re.search(r"[\u4e00-\u9fa5]", name):
            continue
        def _f(s):
            s = s.replace("%", "").replace("亿", "").replace(",", "").strip()
            try:
                return float(s)
            except Exception:
                return None
        net = _f(cells[6])
        if net is None:
            continue
        pct = _f(cells[3])
        data.append({"name": name, "value": round(net, 2), "pct": pct})
    if len(data) < 5:
        return None, None
    data.sort(key=lambda x: -x["value"])
    ins = data[:3]
    data.sort(key=lambda x: x["value"])
    outs = data[:3]
    return ins, outs

def build_breadth_ths(ths_list=None, prev_breadth=None):
    """同花顺口径涨跌家数：行业页 up/down 家数求和（含 ST，与东财剔除 ST 口径不同）。
    涨停数=同花顺涨停池 total；跌停数=同花顺无源，沿用上一轮（prev）。flat 同花顺不提供→沿用上一轮。
    返回 dict 或 None。"""
    if ths_list is None:
        ths_list = fetch_ths_industries()
    if not ths_list or len(ths_list) < 10:
        return None
    up = sum(x["up"] for x in ths_list if isinstance(x.get("up"), int))
    down = sum(x["down"] for x in ths_list if isinstance(x.get("down"), int))
    if up <= 0 and down <= 0:
        return None
    lu_items, lu_total = fetch_ths_limit_up()
    b = {"up": up, "down": down}
    if lu_items is not None and lu_total is not None:
        b["limitUp"] = lu_total
    ld = (prev_breadth or {}).get("limitDown")
    if ld is not None:
        b["limitDown"] = ld
    pf = (prev_breadth or {}).get("flat")
    if isinstance(pf, int):
        b["flat"] = pf
    return b

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
        return {"name": r.get("f14"), "value": round(v / 1e8, 2)}
    ins = [x for x in (mk(r) for r in in_rows) if x][:3]
    outs = [x for x in (mk(r) for r in out_rows) if x][:3]
    if not ins or not outs:
        return None, None
    return ins, outs

def build_all_boards():
    """约前 120 个板块（涨前60+跌前60）完整涨幅榜，供 16:00 AI 预测验证按板块名匹配实际涨跌幅；不参与页面展示。"""
    out = {}
    for po in (1, 0):
        rows = fetch_boards("f3", po)
        if not rows:
            continue
        for r in rows[:60]:
            code = r.get("f12")
            name = r.get("f14")
            pct = to_f(r.get("f3"))
            if not code or not name or pct is None:
                continue
            out[code] = {"code": code, "name": name, "pct": round(pct, 2)}
    # R93b 新浪备用源兜底：东财 push2/push2delay 被 WAF 按 IP 段封锁时（本地+CF 反代+WebFetch 云端均不可达），
    # 行业板块已走新浪口径，这里全板块榜同样回退新浪，避免全景 A股 tab 残留 9/18 旧值。
    if not out:
        sb = fetch_sina_boards()
        if len(sb) >= 10:
            for name, b in sb.items():
                if isinstance(b, dict) and b.get("pct") is not None:
                    cid = b.get("code") or name
                    out[cid] = {"code": cid, "name": name, "pct": round(b["pct"], 2)}
            print(f"[info] allBoards 走新浪备用源（{len(out)} 个板块）")
    return list(out.values())

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
    # 失败清单：updatedAt 诚实标注 + [EM_FAIL] 标记供自动化 AI 走 WebFetch 兜底（R91n/R97g）。
    # R97g 起涨跌家数/连板/主力资金主源=同花顺，东财 push2ex/push2 作兜底；
    # 仅当同花顺+东财全部失败才记为 failed（保留上一轮）。
    em_failed = []
    notes_extra = []
    # 同花顺行业页只取一次，板块/涨跌家数共用（避免重复请求、限频）
    ths_ind = fetch_ths_industries()
    # ---- 涨跌家数：同花顺(行业页 up/down 求和) 主源 → 东财 push2ex 兜底 ----
    breadth = build_breadth_ths(ths_ind, prev_breadth)
    if breadth is None:
        breadth = fetch_breadth(prev_breadth)  # 东财兜底
    if breadth is None:
        em_failed.append("涨跌家数")
        print("[warn] 涨跌家数获取失败（同花顺+东财均失败），保留原值")
    elif breadth.get("limitDown") is not None:
        notes_extra.append("跌停数沿用上一轮(同花顺无跌停接口)")
    # R97f 方案A：行业板块统一构建（同花顺主源 → 东财push2delay备 → 新浪备 → 保留上一轮）
    prev_sec_up = (D.get("ashare") or {}).get("sectorsUp")
    prev_sec_down = (D.get("ashare") or {}).get("sectorsDown")
    prev_all = (D.get("ashare") or {}).get("allBoards")
    sectors_up, sectors_down, all_boards, sector_src = build_sectors_unified(prev_sec_up, prev_sec_down, prev_all, ths_ind)
    if sector_src == "prev":
        em_failed.append("行业板块")
        print("[warn] 行业板块全部源失败，保留上一轮值")
    # ---- 主力资金：同花顺行业资金流(hyzjl) 主源 → 东财 push2 f62 兜底 ----
    fund_in, fund_out = fetch_ths_funds()
    if fund_in is None:
        fund_in, fund_out = build_funds()  # 东财兜底
    if fund_in is None:
        em_failed.append("主力资金")
        print("[warn] 主力资金获取失败，保留原值")

    # 连板梯队：同花顺涨停池(主) → 东财涨停池(兜底)，含连板数 lbc + 题材 reason_type
    today_str = datetime.now(TZ8).strftime("%Y%m%d")
    lianban = fetch_ths_lianban(today_str)
    if lianban is None:
        lianban = fetch_zt_ladder(today_str)  # 东财兜底
    if lianban is None:
        em_failed.append("连板梯队")
        print("[warn] 连板梯队获取失败，保留原值")

    # 东财源失败兜底（R91n，修复原 L464 死条件）：
    # 原判断 `sectors_up is None and indices is None` 因 indices 来自腾讯 gtimg（几乎总
    # 成功）而永不触发 → 东财全挂时仍照刷 tradeDate/status/updatedAt，把上一交易日旧
    # 板块数据伪装成当日收盘（freshness 谎报）。现改为：
    #   - 任一东财源失败：打印 [EM_FAIL] 标记行，自动化 AI 检测后可对失败项走 WebFetch 兜底；
    #   - 全部 4 项东财源失败：exit 1 且不写 data.js（完整保留既有值，绝不静默清场/谎报当日）。
    if em_failed:
        print("[EM_FAIL] " + "/".join(em_failed))
    if len(em_failed) >= 4:  # 恰好 4 项东财源全失败
        print("[error] 东财数据源全部失败（涨跌家数/行业板块/主力资金/连板梯队），"
              "不写 data.js，保留既有值（自动化可对上述项走 WebFetch 兜底）")
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
            a["sectorSource"] = sector_src
            updated_parts.append("行业板块TOP5")
            # 全板块涨幅榜（同花顺/东财push2delay/新浪，供 16:00 AI 预测验证按板块名匹配实际涨跌幅；不参与页面展示）
            all_b = all_boards
            if all_b:
                a["allBoards"] = all_b
                # 行业全景 A股 tab：全板块涨幅同步到 panorama.markets（前端 A股 tab 板块网格）。
                # 与 us/kr/jp 市场项并列，in-place 更新，不动其他市场项。
                pano_mk = D.setdefault("panorama", {}).setdefault("markets", [])
                ash_m = None
                for _m in pano_mk:
                    if isinstance(_m, dict) and _m.get("key") == "ashare":
                        ash_m = _m
                        break
                if ash_m is None:
                    ash_m = {"key": "ashare", "name": "A股"}
                    pano_mk.insert(0, ash_m)
                try:
                    ash_m["date"] = f"{int(now[5:7])}/{int(now[8:10])} 收盘"
                except Exception:
                    ash_m["date"] = f"{now} 收盘"
                if indices:
                    ash_m["indices"] = [
                        {"name": _i.get("name"), "changePct": _i.get("changePct")}
                        for _i in indices
                        if isinstance(_i, dict) and _i.get("changePct") is not None
                    ]
                _items = []
                for _b in all_b:
                    if not (isinstance(_b, dict) and _b.get("name") is not None
                            and isinstance(_b.get("pct"), (int, float))):
                        continue
                    _it = {"name": _b["name"], "pct": _b["pct"]}
                    # R98h：行业全景 A股条目透传富化字段（缺省字段不写，前端条件渲染）
                    for _k in ("netInflow", "upCount", "downCount",
                               "lead", "leadPct", "ztCount", "pct5"):
                        if _b.get(_k) is not None:
                            _it[_k] = _b[_k]
                    _items.append(_it)
                _items.sort(key=lambda x: -x["pct"])
                ash_m["items"] = _items
        if fund_in is not None:
            a["fundIn"] = fund_in
            a["fundOut"] = fund_out
            updated_parts.append("主力资金")
            a.pop("fundNote", None)
        else:
            # R93b 诚实降级：板块主力资金流（f62）唯一源是东财 push2/push2delay，已被 WAF 按 IP 段封锁；
            # 已验证新浪/通达信/腾讯/Cloudflare 反代均无法提供板块级资金流字段。保留最近一次成功取值
            # （2026-09-18）并在卡片上加可见备注，绝不在无数据源时编造或清空。
            a["fundNote"] = ("板块主力资金流数据源（东财 push2）自 2026-09-18 起受网络层风控，"
                             "当前新浪/通达信/腾讯/Cloudflare 反代均无法提供该字段，"
                             "数据沿用最近一次成功取值（2026-09-18），待数据源恢复后自动刷新。")
        if lianban is not None:
            # 空池且已有原值时保留（避免限频 rc:102 空池清空真实数据）；非空或首次则写回
            if lianban or not a.get("lianban"):
                # 同日 reason/story 保留：16:00 自动化由 AI 为连板标的补异动原因（reason）
                # 与深度解读（story），本脚本若同日重跑会重建 lianban（脚本侧 reason/story 恒为空），
                # 把刚补的内容洗掉——与板块 reason 同类防护。
                # 仅当已有数据同为今日（tradeDate 相同）时按 code 保留；跨日不保留
                # （昨日理由不挂今日涨停池，跨日由 16:00 AI 重新补全）。
                if a.get("tradeDate") == now:
                    old_reason = {}
                    for s in (a.get("lianban") or []):
                        if isinstance(s, dict) and s.get("code") and (s.get("reason") or s.get("story")):
                            old_reason[s["code"]] = s
                    for s in lianban:
                        if isinstance(s, dict) and s.get("code") in old_reason:
                            old = old_reason[s["code"]]
                            if old.get("reason"):
                                s["reason"] = old["reason"]
                            if old.get("story"):
                                s["story"] = old["story"]
                    if old_reason:
                        print(f"[info] 保留当日连板 reason/story {len(old_reason)} 条")
                a["lianban"] = lianban
                updated_parts.append("连板梯队")
            else:
                print("[info] 连板梯队空池，保留原值")
        a["tradeDate"] = now
        a["status"] = "收盘"
        if updated_parts:
            note = f"（A股收盘已自动更新：{'/'.join(updated_parts)}）"
            if em_failed:
                # 诚实标注：失败项沿用上一轮数据，页面读者/自动化 AI 可识别哪些字段是旧的
                note = (f"（A股收盘已自动更新：{'/'.join(updated_parts)}；"
                        f"{'/'.join(em_failed)}数据源失败，保留上一轮数据）")
            if notes_extra:
                note += "；" + "/".join(notes_extra)
            D["updatedAt"] = datetime.now(TZ8).strftime('%Y-%m-%d %H:%M') + note
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
        msg = f"[info] data.js 已更新（{'/'.join(updated_parts)}）"
        if em_failed:
            msg += f"；东财源失败保留原值：{'/'.join(em_failed)}"
        print(msg)
    else:
        print("[dry-run] 指数:", json.dumps(indices, ensure_ascii=False))
        print("[dry-run] 涨跌家数:", json.dumps(breadth, ensure_ascii=False), "|", volume_text)
        print("[dry-run] 领涨TOP5:", json.dumps(sectors_up, ensure_ascii=False))
        print("[dry-run] 领跌TOP5:", json.dumps(sectors_down, ensure_ascii=False))
        print("[dry-run] 资金流入TOP3:", json.dumps(fund_in, ensure_ascii=False))
        print("[dry-run] 资金流出TOP3:", json.dumps(fund_out, ensure_ascii=False))

if __name__ == "__main__":
    main()

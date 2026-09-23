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
失败策略：单个数据源失败保留原值（并在 updatedAt 诚实注明保留项）；全部东财数据源
  （涨跌家数/行业板块/主力资金/连板梯队）失败时以退出码 1 退出且不写 data.js（防旧数据
  伪装当日收盘），并输出 [EM_FAIL] 标记行供自动化 AI 走 WebFetch 云端兜底回填（R91n）。
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
    if not up_rows or not down_rows:
        return None, None
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
    # 东财源失败清单：updatedAt 诚实标注 + [EM_FAIL] 标记供自动化 AI 走 WebFetch 兜底（R91n）
    em_failed = []
    breadth = fetch_breadth(prev_breadth)
    if breadth is None:
        em_failed.append("涨跌家数")
        print("[warn] 涨跌家数获取失败，保留原值")
    sectors_up, sectors_down = build_sectors()
    if sectors_up is None:
        em_failed.append("行业板块")
        print("[warn] 行业板块获取失败，保留原值")
    fund_in, fund_out = build_funds()
    if fund_in is None:
        em_failed.append("主力资金")
        print("[warn] 主力资金获取失败，保留原值")

    # 连板梯队（东财涨停池，含连板数 lbc + 涨停原因 reason；数据驱动，不依赖 AI）
    today_str = datetime.now(TZ8).strftime("%Y%m%d")
    lianban = fetch_zt_ladder(today_str)
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
            updated_parts.append("行业板块TOP5")
            # 全板块涨幅榜（约前120个板块，供 16:00 AI 预测验证按板块名匹配实际涨跌幅；不参与页面展示）
            all_b = build_all_boards()
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
                _items = [{"name": _b["name"], "pct": _b["pct"]} for _b in all_b if isinstance(_b, dict) and _b.get("name") is not None and isinstance(_b.get("pct"), (int, float))]
                _items.sort(key=lambda x: -x["pct"])
                ash_m["items"] = _items
        if fund_in is not None:
            a["fundIn"] = fund_in
            a["fundOut"] = fund_out
            updated_parts.append("主力资金")
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

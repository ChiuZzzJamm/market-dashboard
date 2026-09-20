#!/usr/bin/env python3
"""
根据腾讯实时行情更新 data.js 中的 us 与 panorama.us。
美股板块采用「中文概念 -> 真实成分股」聚合口径（见 US_SECTORS）：板块涨跌幅 = 成分股涨跌幅均值，
成分股 TOP 直接展示真实个股（不再用 ETF 充当板块或成分股）。指数参考（道指/纳指/标普/SOXX/金龙）
单独抓取，不混入板块。行情优先用 /tmp 预取文件，缺失成分股/指数时代码自动实时抓取兜底（零新增依赖）。
"""
import json, re, os, argparse
from datetime import datetime, timezone, timedelta
from common import load_dashboard_data, http_get

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

# 测试用：--dry-run 仅打印计算结果、不写回 data.js，并跳过美股盘中闸门（生产调用不传此参数）
_PARSER = argparse.ArgumentParser(description='美股收盘数据更新（零 MCP 依赖）')
_PARSER.add_argument('--dry-run', action='store_true', help='只打印计算结果，不写回 data.js（并跳过盘中闸门，仅供测试）')
ARGS = _PARSER.parse_args()

QUOTE_FILES = ['/tmp/us_quote.txt', '/tmp/us_stocks.txt']

def fmt_pct(x):
    try:
        return f"{float(x):+.2f}%"
    except Exception:
        return str(x)


def _walk_desc(op, chg):
    """开盘→收盘的盘中走势短语：|openPct|<=1 视为平开；收盘相对开盘 >0.3pp 走高、<-0.3pp 走低。"""
    try:
        if op is None or chg is None:
            return ""
        if abs(float(op)) <= 1:
            o = "平开"
        else:
            o = ("高开" + fmt_pct(op)) if op > 0 else ("低开" + fmt_pct(op))
        d = float(chg) - float(op)
        w = "走高" if d > 0.3 else ("走低" if d < -0.3 else "窄幅震荡")
        return f"{o}{w}"
    except Exception:
        return ""


def _us_summary(trade_md, trend, sorted_up, sorted_down):
    """安全构造美股收盘 summary：带盘中走势（openPct×changePct，高开高走/低走等），
    领涨/领跌板块各最多取前 2，避免极端单边市（某一侧仅 1 个板块）触发 IndexError 崩溃。"""
    def _pair(items):
        return "、".join(f"{it['name']}{fmt_pct(it['pct'])}" for it in items[:2])

    lead, trail = _pair(sorted_up), _pair(sorted_down)
    if not (sorted_up or sorted_down):
        return f"{trade_md} 美股收盘数据已更新。"
    dw = _walk_desc(open_pct('usDJI'), pct('usDJI'))
    nw = _walk_desc(open_pct('usIXIC'), pct('usIXIC'))
    sw = _walk_desc(open_pct('usINX'), pct('usINX'))
    head = (f"{trade_md} 美股收盘：三大指数{trend}"
            f"（道指{dw}收{fmt_pct(pct('usDJI'))} / 纳指{nw}收{fmt_pct(pct('usIXIC'))} / 标普{sw}收{fmt_pct(pct('usINX'))}）。")
    tail = (f"领涨：{lead}；" if lead else "") + (f"领跌：{trail}。" if trail else "。")
    return head + tail

def parse_quote_text(text):
    out = {}
    for code, line in re.findall(r'v_([^=]+)="([^"]+)"', text):
        parts = line.split('~')
        if len(parts) < 34:
            continue
        try:
            pct = float(parts[32]) if parts[32] else None
            point = float(parts[3]) if parts[3] else None
        except Exception:
            pct = None
            point = None
        # 开盘涨跌幅：今开(parts[5])/昨收(parts[4])-1，供盘面叙事区分高开/低开与高走/低走
        open_pct = None
        try:
            if parts[4] and parts[5]:
                _prev, _open = float(parts[4]), float(parts[5])
                if _prev > 0 and _open > 0:
                    open_pct = round((_open / _prev - 1) * 100, 2)
        except Exception:
            open_pct = None
        out[code] = {'name': parts[1], 'point': point, 'pct': pct, 'openPct': open_pct}
    return out


def parse_quote_file(path):
    if not os.path.exists(path):
        return {}
    return parse_quote_text(open(path, encoding='utf-8').read())


# ---------- 美股板块：中文概念 -> 真实成分股（腾讯代码，不带后缀）----------
# 板块涨跌幅 = 成分股涨跌幅简单平均（更接近板块真实表现，避免单一 ETF 的跟踪误差/持仓偏差）；
# 成分股 TOP 直接展示这些真实个股，不再出现任何 ETF（ETF 仅作指数参考，不进板块）。
US_SECTORS = [
    {"name": "AI 算力", "constituents": [
        {"code": "usNVDA", "name": "NVDA"}, {"code": "usAMD", "name": "AMD"},
        {"code": "usAVGO", "name": "AVGO"}, {"code": "usARM", "name": "ARM"},
        {"code": "usSMCI", "name": "SMCI"}]},
    {"name": "CPO / 光模块", "constituents": [
        {"code": "usCOHR", "name": "COHR"}, {"code": "usLITE", "name": "LITE"},
        {"code": "usCIEN", "name": "CIEN"}, {"code": "usFN", "name": "FNSR"},
        {"code": "usANET", "name": "ANET"}]},
    {"name": "半导体", "constituents": [
        {"code": "usINTC", "name": "INTC"}, {"code": "usTSM", "name": "TSM"},
        {"code": "usQCOM", "name": "QCOM"}, {"code": "usTXN", "name": "TXN"},
        {"code": "usMRVL", "name": "MRVL"}]},
    {"name": "存储", "constituents": [
        {"code": "usMU", "name": "MU"}, {"code": "usWDC", "name": "WDC"},
        {"code": "usSTX", "name": "STX"}, {"code": "usSNDK", "name": "SNDK"}]},
    {"name": "电网 / 电力设备", "constituents": [
        {"code": "usETN", "name": "ETN"}, {"code": "usHUBB", "name": "HUBB"},
        {"code": "usPWR", "name": "PWR"}, {"code": "usGEV", "name": "GEV"}]},
    {"name": "核电", "constituents": [
        {"code": "usCEG", "name": "CEG"}, {"code": "usVST", "name": "VST"},
        {"code": "usTLN", "name": "TLN"}, {"code": "usOKLO", "name": "OKLO"},
        {"code": "usSMR", "name": "SMR"}]},
    {"name": "数据中心", "constituents": [
        {"code": "usDLR", "name": "DLR"}, {"code": "usEQIX", "name": "EQIX"},
        {"code": "usVRT", "name": "VRT"}]},
    {"name": "云计算 / 软件", "constituents": [
        {"code": "usMSFT", "name": "MSFT"}, {"code": "usAMZN", "name": "AMZN"},
        {"code": "usGOOGL", "name": "GOOGL"}, {"code": "usORCL", "name": "ORCL"},
        {"code": "usCRM", "name": "CRM"}]},
    {"name": "商业航天", "constituents": [
        {"code": "usRKLB", "name": "RKLB"}, {"code": "usLUNR", "name": "LUNR"},
        {"code": "usASTS", "name": "ASTS"}, {"code": "usSPCE", "name": "SPCE"}]},
    {"name": "机器人", "constituents": [
        {"code": "usROK", "name": "ROK"}, {"code": "usISRG", "name": "ISRG"},
        {"code": "usTER", "name": "TER"}, {"code": "usZBRA", "name": "ZBRA"}]},
    {"name": "自动驾驶", "constituents": [
        {"code": "usMBLY", "name": "MBLY"}, {"code": "usAPTV", "name": "APTV"},
        {"code": "usGOOGL", "name": "GOOGL"}]},
    {"name": "军工", "constituents": [
        {"code": "usLMT", "name": "LMT"}, {"code": "usRTX", "name": "RTX"},
        {"code": "usNOC", "name": "NOC"}, {"code": "usGD", "name": "GD"},
        {"code": "usHWM", "name": "HWM"}]},
    {"name": "新能源 / 光伏", "constituents": [
        {"code": "usFSLR", "name": "FSLR"}, {"code": "usENPH", "name": "ENPH"},
        {"code": "usSEDG", "name": "SEDG"}, {"code": "usRUN", "name": "RUN"},
        {"code": "usNEE", "name": "NEE"}]},
    {"name": "锂 / 电池材料", "constituents": [
        {"code": "usALB", "name": "ALB"}, {"code": "usSQM", "name": "SQM"},
        {"code": "usLAC", "name": "LAC"}]},
    {"name": "铜 / 有色", "constituents": [
        {"code": "usFCX", "name": "FCX"}, {"code": "usSCCO", "name": "SCCO"},
        {"code": "usTECK", "name": "TECK"}, {"code": "usRIO", "name": "RIO"}]},
    {"name": "石油", "constituents": [
        {"code": "usXOM", "name": "XOM"}, {"code": "usCVX", "name": "CVX"},
        {"code": "usCOP", "name": "COP"}, {"code": "usEOG", "name": "EOG"}]},
    {"name": "天然气", "constituents": [
        {"code": "usLNG", "name": "LNG"}, {"code": "usEQT", "name": "EQT"},
        {"code": "usKMI", "name": "KMI"}, {"code": "usOKE", "name": "OKE"}]},
    {"name": "黄金 / 贵金属", "constituents": [
        {"code": "usNEM", "name": "NEM"}, {"code": "usAEM", "name": "AEM"},
        {"code": "usKGC", "name": "KGC"}, {"code": "usGFI", "name": "GFI"}]},
    {"name": "银行金融", "constituents": [
        {"code": "usJPM", "name": "JPM"}, {"code": "usBAC", "name": "BAC"},
        {"code": "usWFC", "name": "WFC"}, {"code": "usGS", "name": "GS"},
        {"code": "usMS", "name": "MS"}]},
    {"name": "生物医药", "constituents": [
        {"code": "usLLY", "name": "LLY"}, {"code": "usJNJ", "name": "JNJ"},
        {"code": "usPFE", "name": "PFE"}, {"code": "usMRK", "name": "MRK"}]},
    {"name": "消费", "constituents": [
        {"code": "usWMT", "name": "WMT"}, {"code": "usCOST", "name": "COST"},
        {"code": "usPG", "name": "PG"}, {"code": "usKO", "name": "KO"},
        {"code": "usMCD", "name": "MCD"}]},
    {"name": "稀土 / 战略金属", "constituents": [
        {"code": "usMP", "name": "MP"}, {"code": "usUUUU", "name": "UUUU"}]},
    {"name": "加密货币 / 比特币", "constituents": [
        {"code": "usCOIN", "name": "COIN"}, {"code": "usMSTR", "name": "MSTR"},
        {"code": "usRIOT", "name": "RIOT"}, {"code": "usMARA", "name": "MARA"}]},
    {"name": "中概股", "constituents": [
        {"code": "usBABA", "name": "BABA"}, {"code": "usJD", "name": "JD"},
        {"code": "usPDD", "name": "PDD"}, {"code": "usBIDU", "name": "BIDU"},
        {"code": "usNIO", "name": "NIO"}]},
    {"name": "电动车", "constituents": [
        {"code": "usTSLA", "name": "TSLA"}, {"code": "usRIVN", "name": "RIVN"},
        {"code": "usF", "name": "F"}, {"code": "usGM", "name": "GM"}]},
]

# 指数参考（用于 us.indices 展示，独立口径，不混入板块）
US_INDEX_CODES = ['usDJI', 'usIXIC', 'usINX', 'usSOXX', 'usKWEB']

# 全部需抓代码（指数 + 所有成分股），分块用于实时兜底抓取
_ALL_US_CODES = list(US_INDEX_CODES)
for _s in US_SECTORS:
    for _c in _s['constituents']:
        if _c['code'] not in _ALL_US_CODES:
            _ALL_US_CODES.append(_c['code'])
US_QUOTE_SETS = [','.join(_ALL_US_CODES[i:i + 50]) for i in range(0, len(_ALL_US_CODES), 50)]


def fetch_us_quotes_live():
    """实时抓取腾讯美股行情，作为 /tmp 行情文件缺失时的兜底（去掉对自动化预取文件的硬依赖，杜绝静默跳过）"""
    out = {}
    for s in US_QUOTE_SETS:
        text = http_get(f'http://qt.gtimg.cn/q={s}', decode='gb2312')
        if text:
            out.update(parse_quote_text(text))
    return out


def fetch_trade_date():
    """从实时 usDJI 行情 parts[30] 取交易日期（美东日期），兜底用"""
    text = http_get('http://qt.gtimg.cn/q=usDJI', decode='gb2312')
    if not text:
        return None
    m = re.search(r'v_usDJI="([^"]*)"', text)
    if not m:
        return None
    parts = m.group(1).split('~')
    if len(parts) > 30 and re.match(r'\d{4}-\d{2}-\d{2}', parts[30] or ''):
        return parts[30][:10]
    return None


# ---------- 美股节假日历（脚本级判定，避免休市/旧文件把旧收盘误写为新交易日数据） ----------
def _nth_weekday(year, month, weekday, n):
    """返回 year 年 month 月第 n 个 weekday（0=周一…6=周日）的日期"""
    d = datetime(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d = d + timedelta(days=offset) + timedelta(days=7 * (n - 1))
    return d.date()


def _last_weekday(year, month, weekday):
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return (d - timedelta(days=offset)).date()


def _easter(year):
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime(year, month, day).date()


def us_holidays(year):
    """返回该年度美股休市日期集合（NYSE 规则，含周末顺延）"""
    h = set()
    jan1 = datetime(year, 1, 1).date()
    if jan1.weekday() == 5:
        h.add(jan1 - timedelta(days=1))
    elif jan1.weekday() == 6:
        h.add(jan1 + timedelta(days=1))
    else:
        h.add(jan1)
    h.add(_nth_weekday(year, 1, 0, 3))              # 马丁路德金日（1月第3周一）
    h.add(_nth_weekday(year, 2, 0, 3))              # 总统日（2月第3周一）
    h.add(_easter(year) - timedelta(days=2))        # 耶稣受难日
    h.add(_last_weekday(year, 5, 0))                # 阵亡将士纪念日（5月最后周一）
    j6 = datetime(year, 6, 19).date()               # 六月节
    if j6.weekday() == 5:
        h.add(j6 - timedelta(days=1))
    elif j6.weekday() == 6:
        h.add(j6 + timedelta(days=1))
    else:
        h.add(j6)
    j4 = datetime(year, 7, 4).date()                # 独立日
    if j4.weekday() == 5:
        h.add(j4 - timedelta(days=1))
    elif j4.weekday() == 6:
        h.add(j4 + timedelta(days=1))
    else:
        h.add(j4)
    h.add(_nth_weekday(year, 9, 0, 1))              # 劳动节（9月第1周一）
    h.add(_nth_weekday(year, 11, 3, 4))             # 感恩节（11月第4周四）
    d25 = datetime(year, 12, 25).date()             # 圣诞
    if d25.weekday() == 5:
        h.add(d25 - timedelta(days=1))
    elif d25.weekday() == 6:
        h.add(d25 + timedelta(days=1))
    else:
        h.add(d25)
    return h


_HOLIDAYS = {}
def _holidays_for(year):
    if year not in _HOLIDAYS:
        _HOLIDAYS[year] = us_holidays(year)
        _HOLIDAYS[year + 1] = us_holidays(year + 1)
    return _HOLIDAYS[year]


def is_us_trading_day(d):
    if d.weekday() >= 5:
        return False
    return d not in _holidays_for(d.year)


def prior_us_trading_day(from_date):
    """返回 from_date 之前（不含 from_date 当天）最近的一个美股交易日"""
    d = from_date - timedelta(days=1)
    for _ in range(14):
        if is_us_trading_day(d):
            return d
        d -= timedelta(days=1)
    return None


def us_market_open(now_utc):
    """判断当前是否处于美股常规交易时段（美东 9:30-16:00 且为交易日）。
    盘中行情是动态数据而非收盘数据，此时写入会把盘中误标为「收盘」；
    排程任务（07:30 / 周日23:00）均在休市时段，不受影响。"""
    try:
        from zoneinfo import ZoneInfo
        et = now_utc.astimezone(ZoneInfo('America/New_York'))
    except Exception:
        # 兜底：美国夏令时约 3 月中旬~11 月上旬（UTC-4），其余 UTC-5
        off = -4 if 3 <= now_utc.month <= 11 else -5
        et = (now_utc + timedelta(hours=off)).replace(tzinfo=None)
    if not is_us_trading_day(et.date()):
        return False
    t = et.hour * 60 + et.minute
    return 9 * 60 + 30 <= t < 16 * 60


# 读取 data.js
D = load_dashboard_data(BASE)

# 合并行情：优先用自动化预取的 /tmp 文件，缺失时实时抓取兜底（避免静默跳过）
q = {}
for path in QUOTE_FILES:
    q.update(parse_quote_file(path))
if not q:
    print('[info] /tmp 美股行情缺失，改为实时抓取腾讯行情...')
    q = fetch_us_quotes_live()

# 补全行情：/tmp 预取文件未含的成分股/指数代码时，实时抓取合并（避免静默缺失）
_missing = [c for c in _ALL_US_CODES if c not in q]
if _missing:
    print(f'[info] 预取文件缺失 {len(_missing)} 个代码，实时补抓中...')
    for i in range(0, len(_missing), 50):
        try:
            _txt = http_get('http://qt.gtimg.cn/q=' + ','.join(_missing[i:i + 50]), decode='gb2312')
            if _txt:
                q.update(parse_quote_text(_txt))
        except Exception as _e:
            print(f'[warn] 补抓成分股行情异常（第 {i // 50 + 1} 块）: {_e}')

if not q:
    print('[ERROR] 美股行情全量缺失（/tmp 预取失败且实时兜底也失败），保留上一交易日 us 数据，不写盘')
    raise SystemExit(0)

# 美股盘中闸门：美东常规交易时段内（工作日 9:30-16:00 ET）行情为盘中数据，
# 写入会把盘中误标为「收盘」并污染看板（曾致 9/15 晚手动实跑写入盘中数据）。
# 直接保留原数据退出，不写 data.js 任何字段。
if us_market_open(datetime.now(timezone.utc)) and not ARGS.dry_run:
    print('[warn] 当前处于美股常规交易时段（美东），行情为盘中数据而非收盘，保留原数据不写入')
    raise SystemExit(0)

def get(code):
    return q.get(code, {})

def pct(code):
    v = get(code).get('pct')
    return round(v, 2) if v is not None else None

def point(code):
    return get(code).get('point')

def open_pct(code):
    v = get(code).get('openPct')
    return round(v, 2) if v is not None else None

# 板块映射：panorama.us.items 与 us.sectorsUp/Down 共用同一组口径（真实成分股聚合，非 ETF）
pano_items = []
for cfg in US_SECTORS:
    name = cfg['name']
    vals = []  # (个股名, 涨跌幅, 今开涨跌幅)
    opens = []  # 成分股开盘涨跌幅
    for c in cfg['constituents']:
        cp = pct(c['code'])
        op = open_pct(c['code'])
        if cp is not None:
            vals.append((c['name'], cp, op))
        if op is not None:
            opens.append(op)
    if not vals:
        continue  # 成分股全无行情则跳过该板块
    avg = round(sum(v[1] for v in vals) / len(vals), 2)
    _item = {
        "name": name,
        "pct": avg,
        "ref": "、".join(f"{n}{p_:+.2f}%" for n, p_, _o in vals) + f"（{len(vals)}只均值）",
        "constituents": [{"name": n, "changePct": p, **({"openPct": o} if o is not None else {})} for n, p, o in vals],
    }
    if len(opens) >= 2:  # 板块开盘涨跌近似 = 成分股今开涨跌幅均值
        _item["openPct"] = round(sum(opens) / len(opens), 2)
    pano_items.append(_item)

# 用于 us.sectorsUp/Down：排除没有 pct 的项目
valid_items = [it for it in pano_items if it['pct'] is not None]
# 涨榜只收正值的板块、跌榜只收负值的板块，避免微涨板块因排序被误列入「领跌」
sorted_up = sorted([it for it in valid_items if it['pct'] > 0], key=lambda x: x['pct'], reverse=True)
sorted_down = sorted([it for it in valid_items if it['pct'] < 0], key=lambda x: x['pct'])

# 覆盖守卫：有效板块数过低说明行情抓取残缺（限流/网络抖动），宁可保留上一交易日数据，
# 避免「半空」的美股看板上线（页面不崩但板块残缺，肉眼难以及时发现）。正常约 24 个板块。
if len(valid_items) < 15:
    print(f'[ERROR] 美股有效板块仅 {len(valid_items)} 个（阈值 15），疑似行情抓取残缺，保留上一交易日数据，不覆盖')
    raise SystemExit(0)

# 保留旧 us 中各板块的 reason（周一等场景下，reason 由 07:30 任务写入，周末脚本不应覆盖）
old_reason_map = {}
for s in D.get('us', {}).get('sectorsUp', []) + D.get('us', {}).get('sectorsDown', []):
    if 'reason' in s:
        old_reason_map[s['name']] = s['reason']

def to_us_sector(it, is_up):
    # 成分股 TOP：展示该板块真实成分股；涨板块按涨幅降序取前5（板块领涨），跌板块按涨幅升序取前5（板块领跌）
    cons = it.get("constituents") or []
    if is_up:
        tops = sorted(cons, key=lambda x: x["changePct"], reverse=True)[:5]
    else:
        tops = sorted(cons, key=lambda x: x["changePct"])[:5]
    obj = {"name": it["name"], "pct": it["pct"], "tops": tops, "ref": it.get("ref")}
    if it.get("openPct") is not None:
        obj["openPct"] = it["openPct"]
    if it["name"] in old_reason_map:
        obj["reason"] = old_reason_map[it["name"]]
    return obj

# 交易日期：优先用 /tmp 文件时间戳（自动化预取），缺失时从实时行情 parts[30]（美东日期）兜底
trade_iso = "未知日期"
raw = open('/tmp/us_quote.txt', encoding='utf-8').read() if os.path.exists('/tmp/us_quote.txt') else ''
m = re.search(r'~(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}~', raw)
if m:
    trade_iso = m.group(1)
if trade_iso == "未知日期":
    td = fetch_trade_date()
    if td:
        trade_iso = td
if trade_iso != "未知日期":
    trade_md = f"{int(trade_iso[5:7])}/{int(trade_iso[8:10])}"
else:
    trade_md = "未知"

# 脚本级美股节假日/过期判定：若抓取到的行情日期早于「应更新的最近交易日」，
# 说明是休市或数据过期（避免把旧收盘误写为新交易日数据），保留上一交易日数据并退出。
_now_bj = datetime.now(timezone(timedelta(hours=8)))
if trade_iso != "未知日期":
    try:
        _data_date = datetime.strptime(trade_iso, "%Y-%m-%d").date()
        _expected = prior_us_trading_day(_now_bj.date())
        if _expected and _data_date < _expected:
            print(f"[warn] 行情日期 {trade_iso} 早于应更新的最近交易日 {_expected.isoformat()}，"
                  f"判定为休市/数据过期，保留上一交易日数据，不覆盖")
            D['updatedAt'] = f"{_now_bj.strftime('%Y-%m-%d %H:%M')}（美股休市/行情过期，保留上一交易日数据）"
            if not ARGS.dry_run:
                with open('data.js', 'w', encoding='utf-8') as f:
                    f.write('window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, indent=2) + ';\n')
            print('us market closed/stale, kept previous us data')
            raise SystemExit(0)
    except Exception:
        pass

# 中文名称映射
index_name_map = {
    'usDJI': '道琼斯',
    'usIXIC': '纳斯达克',
    'usINX': '标普500',
    'usSOXX': '费城半导体',
    'usKWEB': '中概互联网',
}

# 三大指数涨跌趋势：三大齐涨/齐跌才用“收涨/收跌”，否则“涨跌不一”
_di, _ix, _sp = pct('usDJI'), pct('usIXIC'), pct('usINX')
_trend = '涨跌不一'
if None not in (_di, _ix, _sp):
    if _di > 0 and _ix > 0 and _sp > 0:
        _trend = '收涨'
    elif _di < 0 and _ix < 0 and _sp < 0:
        _trend = '收跌'

# 题材掘金同日保留：themePicks 由 07:30/周日 AI 基于当日真实新闻生成，本脚本同日重跑时
# 不得洗掉；跨日（trade_iso 变化）则清空，由当日 AI 重新生成，避免旧事件挂新行情。
_old_tp = D.get('us', {}).get('themePicks') or []
_old_us_date = (D.get('us', {}).get('tradeDate') or '')[:10]
keep_tp = _old_tp if (trade_iso != "未知日期" and _old_us_date == trade_iso) else []
if not keep_tp and _old_tp:
    print('[info] us.themePicks 跨日，清空待当日 AI 重新生成')

us_obj = {
    "tradeDate": f"{trade_iso}（美东，北京时间 次日 凌晨收盘）" if trade_iso != "未知日期" else "未知日期",
    "status": "收盘",
    "summary": _us_summary(trade_md, _trend, sorted_up, sorted_down),
    "indices": [
        {"name": index_name_map.get('usDJI','道琼斯'), "point": point('usDJI'), "changePct": pct('usDJI'), "openPct": open_pct('usDJI')},
        {"name": index_name_map.get('usIXIC','纳斯达克'), "point": point('usIXIC'), "changePct": pct('usIXIC'), "openPct": open_pct('usIXIC')},
        {"name": index_name_map.get('usINX','标普500'), "point": point('usINX'), "changePct": pct('usINX'), "openPct": open_pct('usINX')},
        {"name": index_name_map.get('usSOXX','费城半导体'), "point": point('usSOXX'), "changePct": pct('usSOXX'), "openPct": open_pct('usSOXX'), "note": "SOXX"},
    ],
    "breadth": {
        "up": None, "down": None, "flat": None,
        "limitUp": None, "limitDown": None,
        "volumeText": "板块涨跌为美股成分股均值口径，覆盖 AI/半导体/能源/金融等 20+ 产业方向。"
    },
    # 与 A 股口径一致：领涨/领跌板块各只取 TOP5（防止板块全量写入导致列表过长）
    "sectorsUp": [to_us_sector(s, True) for s in sorted_up[:5]],
    "sectorsDown": [to_us_sector(s, False) for s in sorted_down[:5]],
    "sectorNote": "板块涨跌幅为同板块多只美股真实成分股涨跌幅均值（非 ETF 口径），更贴近板块真实表现。",
    "bullNews": D.get('us', {}).get('bullNews', []),
    "bearNews": D.get('us', {}).get('bearNews', []),
    # 保留 08:30 任务写入的当日 AI 利好/利空主题，不得覆盖（否则微信推送会回退到周末旧数据）
    "bullish": D.get('us', {}).get('bullish'),
    "bearish": D.get('us', {}).get('bearish'),
    "outlook": D.get('us', {}).get('outlook', "关注美联储议息、美债收益率与地缘风险对高估值板块的影响。"),
    "themePicks": keep_tp,
    "source": "美股数据来自腾讯行情实时接口"
}

# 兜底：如果主要指数缺失，则不覆盖
if pct('usDJI') is None and pct('usIXIC') is None and pct('usINX') is None:
    print('main indices missing, skip us update')
else:
    D['us'] = us_obj
    for m in D.get('panorama', {}).get('markets', []):
        if m.get('key') == 'us':
            m['date'] = f"{trade_md} 收盘（北京时间 次日 凌晨）" if trade_md != "未知" else "未知日期"
            m['items'] = sorted(pano_items, key=lambda x: (x.get('pct') is None, -(x.get('pct') or 0)))
    D['updatedAt'] = f"{datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')}（美股收盘数据已自动更新）"
    print(f"updated us/panorama.us for {trade_iso}")

# 写回 data.js（--dry-run 仅打印，不写盘）
if ARGS.dry_run:
    print('[dry-run] 不写回 data.js')
    print(json.dumps({
        'tradeDate': us_obj.get('tradeDate'),
        'sectorsUp': [{'name': s['name'], 'pct': s['pct'], 'openPct': s.get('openPct'), 'tops': s.get('tops')} for s in us_obj.get('sectorsUp', [])],
        'sectorsDown': [{'name': s['name'], 'pct': s['pct'], 'openPct': s.get('openPct'), 'tops': s.get('tops')} for s in us_obj.get('sectorsDown', [])],
        'panorama_us_items': [{'name': i['name'], 'pct': i['pct'], 'openPct': i.get('openPct'), 'n': len(i.get('constituents', []))} for i in pano_items],
    }, ensure_ascii=False, indent=2))
else:
    with open('data.js', 'w', encoding='utf-8') as f:
        f.write('window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, indent=2) + ';\n')

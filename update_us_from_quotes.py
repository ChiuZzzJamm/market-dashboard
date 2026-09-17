#!/usr/bin/env python3
"""
根据腾讯实时行情文件（/tmp/us_quote.txt、/tmp/us_stocks.txt）更新 data.js 中的 us 与 panorama.us。
行情文件通过以下命令获取：
  curl -s --max-time 30 "http://qt.gtimg.cn/q=usDJI,usIXIC,usINX,usSOXX,usXLK,usXLF,usXLE,usXLU,usXLC,usXRT,usCLOU,usBOTZ,usBITO,usGLD,usCOPX,usREMX,usMOO,usMAGS,usKWEB,usSMH,usUSO,usTLT" | iconv -f gb2312 -t utf-8 > /tmp/us_quote.txt
  curl -s --max-time 30 "http://qt.gtimg.cn/q=usTSLA,usAMZN,usNVDA" | iconv -f gb2312 -t utf-8 > /tmp/us_stocks.txt
"""
import json, re, os
from datetime import datetime, timezone, timedelta
from common import load_dashboard_data, http_get

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

QUOTE_FILES = ['/tmp/us_quote.txt', '/tmp/us_stocks.txt']

def fmt_pct(x):
    try:
        return f"{float(x):+.2f}%"
    except Exception:
        return str(x)

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
        out[code] = {'name': parts[1], 'point': point, 'pct': pct}
    return out


def parse_quote_file(path):
    if not os.path.exists(path):
        return {}
    return parse_quote_text(open(path, encoding='utf-8').read())


US_QUOTE_SETS = [
    'usDJI,usIXIC,usINX,usSOXX,usXLK,usXLF,usXLE,usXLU,usXLC,usXRT,usCLOU,usBOTZ,usBITO,usGLD,usCOPX,usREMX,usMOO,usMAGS,usKWEB,usSMH,usUSO,usTLT',
    'usTSLA,usAMZN,usNVDA',
]


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

if not q:
    print('no quote data found, skip update')
    raise SystemExit(0)

# 美股盘中闸门：美东常规交易时段内（工作日 9:30-16:00 ET）行情为盘中数据，
# 写入会把盘中误标为「收盘」并污染看板（曾致 9/15 晚手动实跑写入盘中数据）。
# 直接保留原数据退出，不写 data.js 任何字段。
if us_market_open(datetime.now(timezone.utc)):
    print('[warn] 当前处于美股常规交易时段（美东），行情为盘中数据而非收盘，保留原数据不写入')
    raise SystemExit(0)

def get(code):
    return q.get(code, {})

def pct(code):
    v = get(code).get('pct')
    return round(v, 2) if v is not None else None

def point(code):
    return get(code).get('point')

# 板块映射：panorama.us.items 与 us.sectorsUp/Down 共用同一组口径
PANO_ITEMS = [
    {"name": "AI 软件 / 云", "code": "usCLOU", "ref": "CLOU 【主题ETF】"},
    {"name": "AI 硬件 / 英伟达", "code": "usNVDA", "ref": "NVDA 个股【代表标的】"},
    {"name": "科技七巨头", "code": "usMAGS", "ref": "MAGS 【主题ETF】"},
    {"name": "电动汽车 / 特斯拉", "code": "usTSLA", "ref": "TSLA 个股【代表标的】"},
    {"name": "机器人 / 自动化", "code": "usBOTZ", "ref": "BOTZ 【主题ETF】"},
    {"name": "加密货币", "code": "usBITO", "ref": "BITO 【主题ETF】"},
    {"name": "综合电商 / 亚马逊", "code": "usAMZN", "ref": "AMZN 个股【代表标的】"},
    {"name": "消费 / 零售", "code": "usXRT", "ref": "XRT 【行业ETF】"},
    {"name": "金融", "code": "usXLF", "ref": "XLF 【行业ETF】"},
    {"name": "信息技术", "code": "usXLK", "ref": "XLK 【行业ETF】"},
    {"name": "通信服务", "code": "usXLC", "ref": "XLC 【行业ETF】"},
    {"name": "半导体", "code": "usSOXX", "ref": "SOXX 【商品ETF】"},
    {"name": "黄金 / 贵金属", "code": "usGLD", "ref": "GLD 【商品ETF】"},
    {"name": "小金属 / 铜", "code": "usCOPX", "ref": "COPX 【主题ETF】"},
    {"name": "稀土 / 战略金属", "code": "usREMX", "ref": "REMX 【主题ETF】"},
    {"name": "石油 / 能源", "code": "usUSO", "codes": ["usUSO", "usXLE"], "ref_tpl": "USO {usUSO:+.2f}%【商品ETF】；XLE {usXLE:+.2f}%【行业ETF】"},
    {"name": "粮食 / 农业", "code": "usMOO", "ref": "MOO 【主题ETF】"},
    {"name": "电力 / 公用事业", "code": "usXLU", "ref": "XLU 【行业ETF】"},
    {"name": "中概股", "code": "usKWEB", "ref": "KWEB 【主题ETF】"},
]

pano_items = []
for cfg in PANO_ITEMS:
    name = cfg['name']
    c = cfg['code']
    p = pct(c)
    if 'codes' in cfg:
        vals = {cd: pct(cd) for cd in cfg['codes']}
        try:
            ref = cfg['ref_tpl'].format(**vals)
        except Exception:
            ref = cfg['ref_tpl']
    else:
        ref = cfg['ref']
    pano_items.append({"name": name, "pct": p, "ref": ref})

# 用于 us.sectorsUp/Down：排除没有 pct 的项目
valid_items = [it for it in pano_items if it['pct'] is not None]
sorted_up = sorted(valid_items, key=lambda x: x['pct'], reverse=True)[:5]
sorted_down = sorted(valid_items, key=lambda x: x['pct'])[:5]

# 保留旧 us 中各板块的 reason（周一等场景下，reason 由 07:30 任务写入，周末脚本不应覆盖）
old_reason_map = {}
for s in D.get('us', {}).get('sectorsUp', []) + D.get('us', {}).get('sectorsDown', []):
    if 'reason' in s:
        old_reason_map[s['name']] = s['reason']

def to_us_sector(it):
    lead = it["ref"]
    # leader 必须带涨跌幅（页面异动原因块直接展示）；ref 模板未含 % 时用板块自身涨跌幅补上
    if it["pct"] is not None and '%' not in lead:
        tk = lead.split(' ', 1)[0]
        rest = lead[len(tk):].strip()
        lead = f"{tk} {it['pct']:+.2f}% {rest}".rstrip()
    obj = {"name": it["name"], "pct": it["pct"], "leader": lead}
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

us_obj = {
    "tradeDate": f"{trade_iso}（美东，北京时间 次日 凌晨收盘）" if trade_iso != "未知日期" else "未知日期",
    "status": "收盘",
    "summary": (
        f"{trade_md} 美股收盘：三大指数{_trend}（"
        f"道指{fmt_pct(pct('usDJI'))} / 纳指{fmt_pct(pct('usIXIC'))} / 标普{fmt_pct(pct('usINX'))}）。"
        f"领涨：{sorted_up[0]['name']}{fmt_pct(sorted_up[0]['pct'])}、{sorted_up[1]['name']}{fmt_pct(sorted_up[1]['pct'])}；"
        f"领跌：{sorted_down[0]['name']}{fmt_pct(sorted_down[0]['pct'])}、{sorted_down[1]['name']}{fmt_pct(sorted_down[1]['pct'])}。"
    ) if (sorted_up and sorted_down) else f"{trade_md} 美股收盘数据已更新。",
    "indices": [
        {"name": index_name_map.get('usDJI','道琼斯'), "point": point('usDJI'), "changePct": pct('usDJI')},
        {"name": index_name_map.get('usIXIC','纳斯达克'), "point": point('usIXIC'), "changePct": pct('usIXIC')},
        {"name": index_name_map.get('usINX','标普500'), "point": point('usINX'), "changePct": pct('usINX')},
        {"name": index_name_map.get('usSOXX','费城半导体'), "point": point('usSOXX'), "changePct": pct('usSOXX'), "note": "SOXX"},
        {"name": "罗素2000", "point": None, "changePct": None, "note": "小盘股"},
        {"name": "纳斯达克金龙指数", "point": None, "changePct": pct('usKWEB'), "note": "KWEB 中概互联网 ETF 口径"},
    ],
    "breadth": {
        "up": None, "down": None, "flat": None,
        "limitUp": None, "limitDown": None,
        "volumeText": "标普500 板块涨跌互现，ETF 口径仅供参考"
    },
    "sectorsUp": [to_us_sector(s) for s in sorted_up],
    "sectorsDown": [to_us_sector(s) for s in sorted_down],
    "sectorNote": "板块涨跌幅为 SPDR 行业 ETF 口径与主题 ETF 口径，与路透、华尔街见闻等媒体报道口径基本一致。",
    "fundFlows": [
        {"title": "隔夜美股主线", "detail": f"领涨 {sorted_up[0]['name']}{fmt_pct(sorted_up[0]['pct'])}，资金偏好{'AI硬件与周期' if sorted_up[0]['name'] in ['半导体','机器人 / 自动化','AI 硬件 / 英伟达'] else sorted_up[0]['name']}方向。"},
        {"title": "承压方向", "detail": f"{sorted_down[0]['name']}{fmt_pct(sorted_down[0]['pct'])}领跌，注意对 A 股映射拖累。"}
    ] if (sorted_up and sorted_down) else [],
    "bullNews": D.get('us', {}).get('bullNews', []),
    "bearNews": D.get('us', {}).get('bearNews', []),
    # 保留 08:30 任务写入的当日 AI 利好/利空主题，不得覆盖（否则微信推送会回退到周末旧数据）
    "bullish": D.get('us', {}).get('bullish'),
    "bearish": D.get('us', {}).get('bearish'),
    "outlook": D.get('us', {}).get('outlook', "关注美联储议息、美债收益率与地缘风险对高估值板块的影响。"),
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
            m['items'] = pano_items
    D['updatedAt'] = f"{datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')}（美股收盘数据已自动更新）"
    print(f"updated us/panorama.us for {trade_iso}")

# 写回 data.js
with open('data.js', 'w', encoding='utf-8') as f:
    f.write('window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, indent=2) + ';\n')

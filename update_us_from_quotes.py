#!/usr/bin/env python3
"""
根据腾讯实时行情文件（/tmp/us_quote.txt、/tmp/us_stocks.txt）更新 data.js 中的 us 与 panorama.us。
行情文件通过以下命令获取：
  curl -s --max-time 30 "http://qt.gtimg.cn/q=usDJI,usIXIC,usINX,usSOXX,usXLK,usXLF,usXLE,usXLU,usXLC,usXRT,usCLOU,usBOTZ,usBITO,usGLD,usCOPX,usREMX,usMOO,usMAGS,usKWEB,usSMH,usUSO,usTLT" | iconv -f gb2312 -t utf-8 > /tmp/us_quote.txt
  curl -s --max-time 30 "http://qt.gtimg.cn/q=usTSLA,usAMZN,usNVDA" | iconv -f gb2312 -t utf-8 > /tmp/us_stocks.txt
"""
import json, re, subprocess, os, shutil, glob
from common import find_node, load_dashboard_data

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

QUOTE_FILES = ['/tmp/us_quote.txt', '/tmp/us_stocks.txt']

NODE = find_node()

def fmt_pct(x):
    try:
        return f"{float(x):+.2f}%"
    except Exception:
        return str(x)

def parse_quote_file(path):
    if not os.path.exists(path):
        return {}
    s = open(path, encoding='utf-8').read()
    out = {}
    for code, line in re.findall(r'v_([^=]+)="([^"]+)"', s):
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

# 读取 data.js
D = load_dashboard_data(BASE)

# 合并行情
q = {}
for path in QUOTE_FILES:
    q.update(parse_quote_file(path))

if not q:
    print('no quote data found, skip update')
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
    obj = {"name": it["name"], "pct": it["pct"], "leader": it["ref"]}
    if it["name"] in old_reason_map:
        obj["reason"] = old_reason_map[it["name"]]
    return obj

# 尝试读取日期，形如 2026-09-11 -> 9/11
match_date = re.search(r'(\d{4})-(\d{2})-(\d{2})', get('usDJI').get('name', '') or '')
# 若行情文件是从 Tencent 获取，col30 为更新时间，形如 2026-09-11 16:46:29
time_str = ''
raw = open('/tmp/us_quote.txt', encoding='utf-8').read() if os.path.exists('/tmp/us_quote.txt') else ''
m = re.search(r'~(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}~', raw)
if m:
    trade_iso = m.group(1)
    trade_md = f"{int(trade_iso[5:7])}/{int(trade_iso[8:10])}"
else:
    trade_iso = "未知日期"
    trade_md = "未知"

# 中文名称映射
index_name_map = {
    'usDJI': '道琼斯',
    'usIXIC': '纳斯达克',
    'usINX': '标普500',
    'usSOXX': '费城半导体',
    'usKWEB': '中概互联网',
}

us_obj = {
    "tradeDate": f"{trade_iso}（美东，北京时间 次日 凌晨收盘）" if trade_iso != "未知日期" else "未知日期",
    "status": "收盘",
    "summary": (
        f"{trade_md} 美股收盘：三大指数{('收跌' if pct('usDJI') and pct('usDJI') < 0 else '收涨')}（"
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
    "aShareMapping": (
        f"对 A 股开盘映射：①正向——{sorted_up[0]['name']}{fmt_pct(sorted_up[0]['pct'])}、"
        f"{sorted_up[1]['name']}{fmt_pct(sorted_up[1]['pct'])}；②负向——{sorted_down[0]['name']}{fmt_pct(sorted_down[0]['pct'])}、"
        f"{sorted_down[1]['name']}{fmt_pct(sorted_down[1]['pct'])}；③关注——美联储议息与美债走势。"
    ) if (sorted_up and sorted_down) else "",
    "fundFlows": [
        {"title": "隔夜美股主线", "detail": f"领涨 {sorted_up[0]['name']}{fmt_pct(sorted_up[0]['pct'])}，资金偏好{'AI硬件与周期' if sorted_up[0]['name'] in ['半导体','机器人 / 自动化','AI 硬件 / 英伟达'] else sorted_up[0]['name']}方向。"},
        {"title": "承压方向", "detail": f"{sorted_down[0]['name']}{fmt_pct(sorted_down[0]['pct'])}领跌，注意对 A 股映射拖累。"}
    ] if (sorted_up and sorted_down) else [],
    "bullNews": D['us'].get('bullNews', []),
    "bearNews": D['us'].get('bearNews', []),
    "outlook": D['us'].get('outlook', "关注美联储议息、美债收益率与地缘风险对高估值板块的影响。"),
    "source": "美股数据来自腾讯行情实时接口"
}

# 兜底：如果主要指数缺失，则不覆盖
if pct('usDJI') is None and pct('usIXIC') is None and pct('usINX') is None:
    print('main indices missing, skip us update')
else:
    D['us'] = us_obj
    for m in D['panorama']['markets']:
        if m.get('key') == 'us':
            m['date'] = f"{trade_md} 收盘（北京时间 次日 凌晨）" if trade_md != "未知" else "未知日期"
            m['items'] = pano_items
    print(f"updated us/panorama.us for {trade_iso}")

# 写回 data.js
with open('data.js', 'w', encoding='utf-8') as f:
    f.write('window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, indent=2) + ';\n')

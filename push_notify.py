#!/usr/bin/env python3
# 固化微信推送脚本：从 data.js 读最新数据，按模块空行排版，推送给 .notify-config.json 中所有人
# 用法: python3 push_notify.py [ashare|us|weekend]
import subprocess, json, sys, urllib.request, time, os, re
from common import find_node, load_dashboard_data

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

# 定位 node：优先 PATH，其次 workbuddy 管理的多版本目录（避免定时任务环境 PATH 缺失导致崩溃）
NODE = find_node()
print('[info] using node:', NODE)

# 1) 用 node 把 data.js 转成 JSON
D = load_dashboard_data(BASE)

mode = sys.argv[1] if len(sys.argv) > 1 else 'ashare'

def fmt_pct(x):
    try: return f"{float(x):+.2f}%"
    except: return str(x)

def fmt_panorama_items(market, max_items=4):
    """把 panorama 一个市场的 items 格式化为紧凑字符串"""
    if not market or not market.get('items'):
        return ''
    parts = []
    for it in market['items'][:max_items]:
        name = it.get('name', '')
        pct = fmt_pct(it.get('pct', 0))
        ref = it.get('ref', '')
        # 去掉 ref 末尾的"（2龙头均值）""（单一龙头）"等口径后缀，保留龙头列表
        ref = re.sub(r'[（(][^（）()]*[）)]', '', ref).strip('、 ')
        if ref:
            parts.append(f"{name}{pct}（{ref}）")
        else:
            parts.append(f"{name}{pct}")
    return ' '.join(parts)

def build_panorama_line(panorama):
    """生成 🌏 日韩 推送行；数据缺失才 fallback 详见网页"""
    if not panorama or not panorama.get('markets'):
        return '🌏 日韩：详见网页'
    markets = {m['key']: m for m in panorama['markets']}
    kr = markets.get('kr')
    jp = markets.get('jp')
    if not kr and not jp:
        return '🌏 日韩：详见网页'
    pieces = []
    if kr:
        kr_date = kr.get('date','') or ''
        # 休市日不展示过期板块数据，只标「休市」
        if '休市' in kr_date:
            pieces.append(f"韩股 {kr_date}")
        else:
            kr_txt = fmt_panorama_items(kr)
            if kr_txt:
                pieces.append(f"韩股 {kr_date}：{kr_txt}")
    if jp:
        jp_date = jp.get('date','') or ''
        if '休市' in jp_date:
            pieces.append(f"日经 {jp_date}")
        else:
            jp_txt = fmt_panorama_items(jp)
            if jp_txt:
                pieces.append(f"日经 {jp_date}：{jp_txt}")
    if pieces:
        return '🌏 日韩：' + ' | '.join(pieces)
    return '🌏 日韩：详见网页'

def fmt_ai_line(ap, label):
    """AI 预测紧凑摘要：每板块「板块名方向(置信度)」，一行带过（微信不宜展开 6×8 明细）。
    ap 缺失或 sectors 为空返回 None，调用方跳过该段。"""
    if not ap or not ap.get('sectors'):
        return None
    items = []
    for s in ap['sectors'][:6]:
        sec = (s.get('sector') or '').strip()
        if not sec:
            continue
        d = (s.get('direction') or '').strip()
        c = (s.get('confidence') or '').strip()
        items.append(f"{sec}{d}({c})" if c else f"{sec}{d}")
    if not items:
        return None
    return f"🎯 {label}：{'｜'.join(items)}"

def fmt_bullish(t, idx):
    """利好板块：保留括号内板块说明 + 核心受益股"""
    theme = t.get('theme','').strip()
    theme = re.sub(r'\s+', ' ', theme)
    m = re.match(r'^(.+?)[（(]([^）)]+)[）)](.*)$', theme)
    if m:
        before = m.group(1).strip()
        inner = m.group(2).strip()
        before = before[:18] + ('…' if len(before) > 18 else '')
        inner = inner[:22] + ('…' if len(inner) > 22 else '')
        theme = f"{before}（{inner}）"
    elif len(theme) > 34:
        theme = theme[:34] + '…'
    stocks = ' '.join([s.get('name','') for s in t.get('stocks',[])[:3]])
    if stocks:
        return f"{idx}. {theme}｜{stocks[:28]}"
    return f"{idx}. {theme}"

def fmt_bearish(t, idx):
    """利空板块：保留括号内板块/方向说明，明确利空哪些板块"""
    theme = t.get('theme','').strip()
    theme = re.sub(r'\s+', ' ', theme)
    m = re.match(r'^(.+?)[（(]([^）)]+)[）)](.*)$', theme)
    if m:
        before = m.group(1).strip()
        inner = m.group(2).strip()
        before = before[:18] + ('…' if len(before) > 18 else '')
        inner = inner[:26] + ('…' if len(inner) > 26 else '')
        theme = f"{before}（{inner}）"
    elif len(theme) > 42:
        theme = theme[:42] + '…'
    return f"{idx}. {theme}"

# 国内/国际要闻识别关键词（用于给要闻打 🇨🇳/🌍 图标；面向新闻 title/summary 文本，比 weekend 模式的 theme 关键词更宽）
DOM_KEYS = ['国常会','证监会','央行','国务院','工信部','财政部','发改委','中方','我国','国内','A股','港股','政策','十五五','券商','印花税','北向','两融','逆回购','LPR','光博会','算力网','算力大会','人民币','出口','关税']
INTL_KEYS = ['美联储','美股','纳指','道指','标普','美债','美元','加息','降息','特朗普','白宫','中东','沙特','伊朗','以色列','俄乌','俄罗斯','乌克兰','地缘','油价','原油','黄金','能源','油运','日本','日经','日元','韩国','KOSPI','亚太','欧洲','英国','英伟达','苹果','特斯拉','半导体','芯片']

def news_tag(item):
    """按 title/summary 识别国内(🇨🇳)/国际(🌍)要闻，未命中给 🌐。
    title 优先（title 更能代表新闻归属，避免国际新闻因 summary 提及 A 股映射被误判为国内）"""
    title = (item.get('title','') or '').strip()
    summary = (item.get('summary','') or '').strip()
    if any(k in title for k in DOM_KEYS):
        return '🇨🇳'
    if any(k in title for k in INTL_KEYS):
        return '🌍'
    text = title + ' ' + summary
    if any(k in text for k in DOM_KEYS):
        return '🇨🇳'
    if any(k in text for k in INTL_KEYS):
        return '🌍'
    return '🌐'


GROUP_TAG = {'macroNews': '宏观', 'intlNews': '国际', 'bankViews': '投行'}

def collect_news(src, max_n=6, groups=('bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews')):
    """汇总多组要闻，去重后取前 max_n 条，带 🇨🇳/🌍/🌐 图标；宏观/国际/投行组追加【宏观】【国际】【投行】标签。
    分配策略（修复：此前按组顺序取前 5，bull/bear 两组共 8-10 条把宏观/国际/投行全部挤出推送）：
    先保底 bull×2、bear×1、宏观×1、国际×1、投行×1（共 6 条），某组不足时名额轮转给其余组；
    组内去重，组间按 title 全局去重。"""
    pools, seen = {}, set()
    for grp in groups:
        lst = []
        for n in (src.get(grp) or []):
            if isinstance(n, dict):
                t = (n.get('title') or '').strip()
                if t and t not in seen:
                    seen.add(t)
                    lst.append(n)
        pools[grp] = lst
    picked = []
    def take(grp, n):
        for x in pools[grp][:n]:
            picked.append((grp, x))
        pools[grp] = pools[grp][n:]
    take('bullNews', 2)
    take('bearNews', 1)
    take('macroNews', 1)
    take('intlNews', 1)
    take('bankViews', 1)
    # 名额补足：保底组不足时轮转其余组，直到满 max_n 或无料可取
    while len(picked) < max_n:
        before = len(picked)
        for grp in groups:
            if pools[grp]:
                picked.append((grp, pools[grp].pop(0)))
                break
        if len(picked) == before:
            break
    out = []
    for grp, n in picked[:max_n]:
        tag = news_tag(n)
        label = GROUP_TAG.get(grp)
        t = (n.get('title') or '').strip()
        out.append(f"{tag}【{label}】{t}" if label else f"{tag} {t}")
    return out


if mode == 'ashare':
    a = D.get('ashare')
    if not a or not a.get('indices'):
        print('[warn] ashare 数据缺失，跳过推送'); sys.exit(0)
    idx0 = (a.get('indices') or [{}])[0]
    title = f"[A股收盘] {a.get('tradeDate','')[-5:]} 沪指{fmt_pct(idx0.get('changePct'))}"
    leaders = '  '.join([f"{s.get('name','')}{fmt_pct(s.get('pct'))}" for s in (a.get('sectorsUp') or [])[:5]])
    laggards = '  '.join([f"{s.get('name','')}{fmt_pct(s.get('pct'))}" for s in (a.get('sectorsDown') or [])[:5]])
    fi = ' '.join([f"{s.get('name','')}{s.get('value',0):+.1f}亿" for s in (a.get('fundIn') or [])[:3]])
    fo = ' '.join([f"{s.get('name','')}{s.get('value',0):+.1f}亿" for s in (a.get('fundOut') or [])[:3]])
    # 微信推送保持连续自然段，避免主动换行被截断；data.js 里 outlook 为 {date,content} 对象，优先取 content
    _ol = a.get('outlook') or ''
    if isinstance(_ol, dict):
        _ol = _ol.get('content') or _ol.get('text') or ''
    outlook_text = str(_ol).replace('\n', ' ').strip()
    # A股要闻：合并利好/利空/宏观/国际/投行五组，去重后取 5 条，带 🇨🇳/🌍 图标（修复 B）
    news_parts = collect_news(a, 5)
    # 利好/利空方向（与美股/周末推送一致）
    bullish_lines = [fmt_bullish(t, i+1) for i, t in enumerate((a.get('bullish') or [])[:3])]
    bearish_lines = [fmt_bearish(t, i+1) for i, t in enumerate((a.get('bearish') or [])[:3])]
    desp_parts = [
        '🌐 https://chiuzzzjamm.github.io/market-dashboard',
        f"📊 A股：{a.get('summary','')}",
        f"📈 领涨：{leaders}",
        f"📉 领跌：{laggards}",
        f"💰 资金：流入 {fi} | 流出 {fo}",
        build_panorama_line(D.get('panorama')),
        f"📰 要闻：{'  '.join(news_parts)}" if news_parts else '📰 要闻：详见网页',
        f"💡 研判：{outlook_text}" if outlook_text else '',
    ]
    if bullish_lines:
        desp_parts.append("✅ 利好：\n" + '\n'.join(bullish_lines))
    if bearish_lines:
        desp_parts.append("⚠️ 利空：\n" + '\n'.join(bearish_lines))
    desp = '\n\n'.join(desp_parts)

elif mode == 'us':
    u = D.get('us')
    if not u:
        print('[warn] us 数据缺失，跳过推送'); sys.exit(0)
    us_trade = u.get('tradeDate', '') or ''
    us_date = (us_trade.split('（')[0][5:] if us_trade else '')
    idx0 = (u.get('indices') or [{}])[0]
    title = f"[美股] {us_date} 道指{fmt_pct(idx0.get('changePct'))}"
    # 微信端保持连续自然段，避免被截断；outlook 为 {date,content} 对象时取 content
    _ol = u.get('outlook') or ''
    if isinstance(_ol, dict):
        _ol = _ol.get('content') or _ol.get('text') or ''
    outlook_text = str(_ol).replace('\n', ' ').strip()
    # 利好/利空板块：优先取当日 us.bullish/bearish（若 08:30 任务已生成），否则回退周末消息，格式同周日
    w = D.get('weekendNews') or {}
    bull_src = (u.get('bullish') or w.get('bullish', []))
    bear_src = (u.get('bearish') or w.get('bearish', []))
    bullish_lines = [fmt_bullish(t, i+1) for i, t in enumerate(bull_src[:3])]
    bearish_lines = [fmt_bearish(t, i+1) for i, t in enumerate(bear_src[:3])]
    # 要闻：合并利好/利空/宏观/国际/投行五组，保底分配取 6 条，带 🇨🇳/🌍 图标与【宏观】【国际】【投行】标签（修复 B）
    news_parts = collect_news(u, 6)
    parts = [
        '🌐 https://chiuzzzjamm.github.io/market-dashboard',
        f"📊 美股：{u.get('summary','')}",
        f"📰 要闻：{'  '.join(news_parts)}" if news_parts else '📰 要闻：详见网页',
        build_panorama_line(D.get('panorama')),
        f"💡 研判：{outlook_text}" if outlook_text else '',
    ]
    # 当日 AI 预测（08:30 任务已写入顶层 aiPrediction）：紧凑一行，与 prompt 口径一致
    ai_line = fmt_ai_line(D.get('aiPrediction'), '今日AI预测')
    if ai_line:
        parts.append(ai_line)
    if bullish_lines:
        parts.append("✅ 利好：\n" + '\n'.join(bullish_lines))
    if bearish_lines:
        parts.append("⚠️ 利空：\n" + '\n'.join(bearish_lines))
    desp = '\n\n'.join(parts)

elif mode == 'weekend':
    # 周末推送改源：周末消息面前瞻卡已删除（与全球要闻重合），推送改用
    # 周末更新的全球要闻（R68 起周日写入美股卡：us.bullNews/bearNews/intlNews/bankViews/macroNews，
    # 旧数据兜底读 ashare 同名字段）+ 周一开盘预判（weekendNews.mondayOutlook）。
    a = D.get('ashare') or {}
    w = D.get('weekendNews') or {}
    u = D.get('us') or {}
    src = u if (u.get('bullNews') or u.get('macroNews') or u.get('intlNews') or u.get('bankViews')) else a
    title = f"[周末要闻] {(w.get('date') or a.get('tradeDate') or '')[5:]} 汇总"

    # 美股周五收盘（直接取 us 最新数据，周日脚本已更新到周五）
    idx_part = ' '.join([f"{i.get('name','')}{fmt_pct(i.get('changePct'))}" for i in (u.get('indices') or [])[:3]])
    up_str = '、'.join([f"{s.get('name','')}{fmt_pct(s.get('pct'))}" for s in (u.get('sectorsUp') or [])[:3]])
    down_str = '、'.join([f"{s.get('name','')}{fmt_pct(s.get('pct'))}" for s in (u.get('sectorsDown') or [])[:3]])
    if up_str and down_str:
        us_line = f"{idx_part}。领涨：{up_str}；领跌：{down_str}。"
    elif idx_part:
        us_line = idx_part
    else:
        us_line = '（详见网页）'

    # 要闻：从周末全球要闻标题中分国内/国际各取 2 条（news_tag 识别， title 截 40 字）
    pool = []
    for grp in ('bullNews', 'bearNews', 'intlNews', 'bankViews', 'macroNews'):
        for n in (src.get(grp) or []):
            if isinstance(n, dict) and n.get('title'):
                pool.append(n)
    domestic, international, seen_titles = [], [], set()
    for n in pool:
        t = (n.get('title') or '').strip()
        if not t or t in seen_titles:
            continue
        seen_titles.add(t)
        tag = news_tag(n)
        short = t[:40] + ('…' if len(t) > 40 else '')
        if tag == '🇨🇳' and len(domestic) < 3:
            domestic.append(short)
        elif tag in ('🌍', '🌐') and len(international) < 2:
            international.append(short)
        if len(domestic) + len(international) >= 5:
            break
    news_parts = [f"🇨🇳 {d}" for d in domestic] + [f"🌍 {i}" for i in international]
    weekend_news = ' '.join(news_parts) if news_parts else '（详见网页）'

    # 利好/利空：映射 bullNews/bearNews → fmt_bullish/fmt_bearish 所需 {theme, stocks}
    def news_to_theme(it):
        return {'theme': f"{(it.get('sector') or '').strip()}（{(it.get('title') or '').strip()}）",
                'stocks': ((it.get('impacts') or [{}])[0].get('stocks') or [])}
    bullish_lines = [fmt_bullish(news_to_theme(t), i+1) for i, t in enumerate((src.get('bullNews') or [])[:3])]
    bearish_lines = [fmt_bearish(news_to_theme(t), i+1) for i, t in enumerate((src.get('bearNews') or [])[:3])]

    # 开盘前瞻（R72 起为顶层 openOutlook.content，带日期；周末/周一均适用）
    _oo = D.get('openOutlook') or {}
    monday_outlook = (_oo.get('content') or '').replace('\n', ' ').strip()

    # 顺序：美股 → 要闻 → 周一研判 → AI预测（周一板块） → 利好 → 利空
    parts = [
        '🌐 https://chiuzzzjamm.github.io/market-dashboard',
        f"📊 美股：{us_line}",
        f"📰 要闻：\n{weekend_news}",
        f"💡 周一研判：{monday_outlook}",
    ]
    ai_line = fmt_ai_line(D.get('aiPrediction'), '周一AI预测')
    if ai_line:
        parts.append(ai_line)
    if bullish_lines:
        parts.append("✅ 利好：\n" + '\n'.join(bullish_lines))
    if bearish_lines:
        parts.append("⚠️ 利空：\n" + '\n'.join(bearish_lines))
    desp = '\n\n'.join(parts)

else:
    print('unknown mode'); sys.exit(1)

# 推送
try:
    cfg = json.load(open('.notify-config.json'))
    sendkeys = [r['sendkey'] for r in (cfg.get('recipients') or [])]
except Exception as e:
    print('[warn] 读取 .notify-config.json 失败，跳过推送:', e); sys.exit(0)
if not sendkeys:
    print('[warn] 无有效 sendkey，跳过推送'); sys.exit(0)
DRY = os.environ.get('PUSH_DRY') == '1'
print('=== TITLE ===')
print(title)
print('=== DESP ===')
print(desp)
print('=== sendkeys ===', sendkeys)
if DRY:
    print('[DRY-RUN] 未实际发送')
    sys.exit(0)

for sendkey in sendkeys:
    url = f'https://sctapi.ftqq.com/{sendkey}.send'
    data = json.dumps({'title':title,'desp':desp}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type':'application/json'})
    last_err = ''
    for attempt in range(1,4):
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            print('push ok:', sendkey, resp.read().decode('utf-8')[:120]); break
        except Exception as e:
            last_err = str(e)
            print('push attempt', attempt, 'failed:', sendkey, e)
            if attempt < 3: time.sleep(3)
    else:
        print('push failed after 3 attempts:', sendkey, last_err)

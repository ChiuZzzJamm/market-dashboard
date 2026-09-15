#!/usr/bin/env python3
# 固化微信推送脚本：从 data.js 读最新数据，按模块空行排版，推送给 .notify-config.json 中所有人
# 用法: python3 push_notify.py [ashare|us|weekend]
import subprocess, json, sys, urllib.request, time, os, shutil, glob, re

BASE = '/Users/loccco/WorkBuddy/2026-09-04-11-53-22/market-dashboard'
os.chdir(BASE)

# 定位 node：优先 PATH，其次 workbuddy 管理的多版本目录（避免定时任务环境 PATH 缺失导致崩溃）
def find_node():
    p = shutil.which('node')
    if p:
        return p
    cands = sorted(glob.glob('/Users/loccco/.workbuddy/binaries/node/versions/*/bin/node'))
    if cands:
        return cands[-1]
    # 兜底：常见系统路径
    for c in ['/usr/local/bin/node', '/usr/bin/node']:
        if os.path.exists(c):
            return c
    raise RuntimeError('node not found in PATH or known locations')

NODE = find_node()
print('[info] using node:', NODE)

# 1) 用 node 把 data.js 转成 JSON
node_src = r'''
const fs = require('fs');
let s = fs.readFileSync('data.js','utf8');
let m = s.match(/window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
process.stdout.write(JSON.stringify(eval('(' + m[1] + ')')));
'''
p = subprocess.run([NODE,'-e',node_src], capture_output=True, text=True, cwd=BASE)
if p.returncode != 0:
    print('node parse failed:', p.stderr); sys.exit(1)
D = json.loads(p.stdout)

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
        kr_txt = fmt_panorama_items(kr)
        if kr_txt:
            pieces.append(f"韩股 {kr.get('date','')}：{kr_txt}")
    if jp:
        jp_txt = fmt_panorama_items(jp)
        if jp_txt:
            pieces.append(f"日经 {jp.get('date','')}：{jp_txt}")
    if pieces:
        return '🌏 日韩：' + ' | '.join(pieces)
    return '🌏 日韩：详见网页'

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

if mode == 'ashare':
    a = D['ashare']
    idx0 = a['indices'][0]
    title = f"[A股收盘] {a['tradeDate'][5:]} 沪指{fmt_pct(idx0['changePct'])}"
    leaders = '  '.join([f"{s['name']}{fmt_pct(s['pct'])}" for s in a['sectorsUp'][:5]])
    laggards = '  '.join([f"{s['name']}{fmt_pct(s['pct'])}" for s in a['sectorsDown'][:5]])
    fi = ' '.join([f"{s['name']}{s['value']:+.1f}亿" for s in a['fundIn'][:3]])
    fo = ' '.join([f"{s['name']}{s['value']:+.1f}亿" for s in a['fundOut'][:3]])
    # 微信推送保持连续自然段，避免主动换行被截断；data.js 里 outlook 本身无换行，这里做兜底
    outlook_text = a.get('outlook','').replace('\n',' ').strip()
    desp = '\n\n'.join([
        '🌐 https://chiuzzzjamm.github.io/market-dashboard',
        f"📊 A股：{a['summary']}",
        f"📈 领涨：{leaders}",
        f"📉 领跌：{laggards}",
        f"💰 资金：流入 {fi} | 流出 {fo}",
        build_panorama_line(D.get('panorama')),
        f"💡 研判：{outlook_text}",
    ])

elif mode == 'us':
    u = D['us']
    us_date = u['tradeDate'].split('（')[0][5:]
    title = f"[美股] {us_date} 道指{fmt_pct(u['indices'][0]['changePct'])}"
    # 微信端保持连续自然段，避免被截断
    outlook_text = u.get('outlook','').replace('\n',' ').strip()
    # 利好/利空板块：优先取当日 us.bullish/bearish（若 08:30 任务已生成），否则回退周末消息，格式同周日
    w = D.get('weekendNews') or {}
    bull_src = (u.get('bullish') or w.get('bullish', []))
    bear_src = (u.get('bearish') or w.get('bearish', []))
    bullish_lines = [fmt_bullish(t, i+1) for i, t in enumerate(bull_src[:3])]
    bearish_lines = [fmt_bearish(t, i+1) for i, t in enumerate(bear_src[:3])]
    parts = [
        '🌐 https://chiuzzzjamm.github.io/market-dashboard',
        f"📊 美股：{u['summary']}",
        f"📰 要闻：{'  '.join([n['title'] for n in (u.get('bullNews',[])[:2] + u.get('bearNews',[])[:2])])}",
        build_panorama_line(D.get('panorama')),
        f"💡 研判：{outlook_text}" if outlook_text else '',
    ]
    if bullish_lines:
        parts.append("✅ 利好：\n" + '\n'.join(bullish_lines))
    if bearish_lines:
        parts.append("⚠️ 利空：\n" + '\n'.join(bearish_lines))
    desp = '\n\n'.join(parts)

elif mode == 'weekend':
    w = D.get('weekendNews') or {}
    ap = D.get('aiPrediction') or {}
    title = f"[周末消息] {w.get('date','')[5:]} 要闻汇总"
    # 控制长度，避免微信折叠/截断；每个字段都加防御性 .get
    uf = w.get('usFriday') or {}

    # 美股周五：三大指数 + 领涨板块 + 领跌板块（参考工作日 us 模式完整格式）
    us_summary = uf.get('summary','').strip()
    if us_summary:
        us_line = us_summary
    else:
        up_str = '、'.join([f"{s.get('name','')}{fmt_pct(s.get('pct'))}" for s in uf.get('sectorsUp',[])[:3]])
        down_str = '、'.join([f"{s.get('name','')}{fmt_pct(s.get('pct'))}" for s in uf.get('sectorsDown',[])[:3]])
        idx_part = ' '.join([f"{i.get('name','')}{fmt_pct(i.get('changePct'))}" for i in uf.get('indices',[])[:3]])
        if up_str and down_str:
            us_line = f"{idx_part}。领涨：{up_str}；领跌：{down_str}。"
        elif idx_part:
            us_line = idx_part
        else:
            us_line = '（详见网页）'

    # 周末要闻：从利好/利空主题中分国内/国际各取 2 条短摘要（控制每段长度避免微信截断）
    def short_theme(t, max_len=26):
        theme = t.get('theme','').strip()
        theme = re.sub(r'\s+', ' ', theme)
        # 识别括号内容：若括号内是事件描述则保留，否则只取括号前
        m = re.match(r'^(.+?)[（(]([^）)]+)[）)](.*)$', theme)
        if m:
            before = m.group(1).strip()
            inner = m.group(2).strip()
            event_keys = ['证监会','央行','国常会','改革','冲突','加息','油价','概率','沙特','霍尔木兹','战争','管道','遭袭','谈判','制裁']
            if any(k in inner for k in event_keys):
                # 组合为 板块（事件）
                if len(before) + len(inner) + 3 <= max_len:
                    return f"{before}（{inner}）"
                inner_short = inner[:max(6, max_len - len(before) - 3)]
                if len(inner) > max_len - len(before) - 3:
                    inner_short += '…'
                return f"{before}（{inner_short}）"
            else:
                # 括号内只是板块说明，取括号前
                if len(before) <= max_len:
                    return before
                return before[:max_len] + '…'
        if len(theme) > max_len:
            return theme[:max_len] + '…'
        return theme

    dom_keys = ['国常会','证监会','央行','工信部','国务院','A股','政策','十五五','券商','算力网','算力大会']
    intl_keys = ['美联储','中东','亚太','油价','能源','油运','战争','沙特','俄乌','日元','日本','美元','加息','美债']
    domestic = []
    international = []
    seen_themes = set()

    for t in w.get('bullish',[]) + w.get('bearish',[]):
        theme_full = t.get('theme','').strip()
        if theme_full in seen_themes:
            continue
        seen_themes.add(theme_full)
        if any(k in theme_full for k in dom_keys) and len(domestic) < 2:
            domestic.append(short_theme(t, 100))
        elif any(k in theme_full for k in intl_keys) and len(international) < 2:
            international.append(short_theme(t, 100))

    news_parts = []
    for d in domestic:
        news_parts.append(f"🇨🇳 {d}")
    for i in international:
        news_parts.append(f"🌍 {i}")
    # 微信对主动换行的单行有截断阈值；合并成连续自然段，让微信自动换行，显示更完整
    weekend_news = ' '.join(news_parts) if news_parts else '（详见网页）'

    # 完整周一研判；微信端保持连续自然段，避免被截断
    monday_outlook = w.get('mondayOutlook','').replace('\n',' ').strip()

    bullish_lines = [fmt_bullish(t, i+1) for i, t in enumerate(w.get('bullish',[])[:3])]
    bearish_lines = [fmt_bearish(t, i+1) for i, t in enumerate(w.get('bearish',[])[:3])]

    # 顺序：美股 → 要闻 → 研判 → 利好 → 利空
    parts = [
        '🌐 https://chiuzzzjamm.github.io/market-dashboard',
        f"📊 美股：{us_line}",
        f"📰 要闻：\n{weekend_news}",
        f"💡 研判：{monday_outlook}",
    ]
    if bullish_lines:
        parts.append("✅ 利好：\n" + '\n'.join(bullish_lines))
    if bearish_lines:
        parts.append("⚠️ 利空：\n" + '\n'.join(bearish_lines))
    desp = '\n\n'.join(parts)

else:
    print('unknown mode'); sys.exit(1)

# 推送
cfg = json.load(open('.notify-config.json'))
sendkeys = [r['sendkey'] for r in cfg['recipients']]
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

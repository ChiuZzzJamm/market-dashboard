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

if mode == 'ashare':
    a = D['ashare']
    idx0 = a['indices'][0]
    title = f"[A股收盘] {a['tradeDate'][5:]} 沪指{fmt_pct(idx0['changePct'])}"
    leaders = '  '.join([f"{s['name']}{fmt_pct(s['pct'])}" for s in a['sectorsUp'][:5]])
    laggards = '  '.join([f"{s['name']}{fmt_pct(s['pct'])}" for s in a['sectorsDown'][:5]])
    fi = ' '.join([f"{s['name']}{s['value']:+.1f}亿" for s in a['fundIn'][:3]])
    fo = ' '.join([f"{s['name']}{s['value']:+.1f}亿" for s in a['fundOut'][:3]])
    ai = D['aiPrediction'].get('verification')
    ai_txt = ai['summary'] if ai else '（待16:00验证）'
    desp = '\n\n'.join([
        '🌐 https://chiuzzzjamm.github.io/market-dashboard',
        f"📊 大盘趋势：{a['summary']}",
        f"🎯 AI验证：{ai_txt}",
        f"📈 领涨：{leaders}",
        f"📉 领跌：{laggards}",
        f"💰 资金：流入 {fi} | 流出 {fo}",
        build_panorama_line(D.get('panorama')),
        f"💡 一句话：{a.get('outlook','')}",
    ])

elif mode == 'us':
    u = D['us']
    us_date = u['tradeDate'].split('（')[0][5:]
    title = f"[美股] {us_date} 道指{fmt_pct(u['indices'][0]['changePct'])}"
    up = '  '.join([f"{s['name']}{fmt_pct(s['pct'])}" for s in u['sectorsUp'][:4]])
    down = '  '.join([f"{s['name']}{fmt_pct(s['pct'])}" for s in u['sectorsDown'][:4]])
    ai = D['aiPrediction']
    ai_txt = ai.get('summary','')
    desp = '\n\n'.join([
        '🌐 https://chiuzzzjamm.github.io/market-dashboard',
        f"📊 大盘研判：{u['summary']}",
        f"🎯 AI预测今日：{ai_txt}",
        f"📈 隔夜美股：{'  '.join([i['name']+fmt_pct(i['changePct']) for i in u['indices'][:3]])}",
        f"📰 要闻：{'  '.join([n['title'] for n in (u.get('bullNews',[])[:2] + u.get('bearNews',[])[:2])])}",
        build_panorama_line(D.get('panorama')),
    ])

elif mode == 'weekend':
    w = D.get('weekendNews') or {}
    title = f"[周末消息] {w.get('date','')[5:]} 要闻汇总"
    ap = D.get('aiPrediction') or {}
    ai_sectors = '  '.join([f"{s.get('sector','')}({s.get('direction','')})" for s in ap.get('sectors',[])])
    ai_summary = ap.get('summary','')
    desp = '\n\n'.join([
        '🌐 https://chiuzzzjamm.github.io/market-dashboard',
        f"📊 美股周五：{w.get('summary','')[:120]}",
        f"✅ 利好：{'  '.join([t.get('theme','') for t in w.get('bullish',[])[:3]])}",
        f"⚠️ 利空：{'  '.join([t.get('theme','') for t in w.get('bearish',[])[:3]])}",
        f"🔮 周一预判：{w.get('mondayOutlook','')}",
        f"🎯 周一板块：{ai_sectors}",
        f"🧭 AI研判：{ai_summary}",
    ])

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

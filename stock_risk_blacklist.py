#!/usr/bin/env python3
"""个股硬风险黑名单（R100z13-t2o，用户 2026-10-01 点名「最大缺口」）。

背景：
    选股对不对原先只看 8 只标的当天涨跌幅，但一只票遭大股东减持 / 限售解禁 / ST /
    业绩预减，方向说得再对也会被市场锤。本脚本把「确定性风险」变成机械闸门：

    1. ST / *ST / 退市（名称带 ST，行情名或 data.js 内 name 判定）
    2. 限售解禁落在 T+2 ~ T+5 窗口（东财 RPT_LIFT_STAGE 的 FREE_DATE）
    3. 减持公告 30 日内（东财 np-anotice-stock 公告标题）
    4. 业绩预减 / 预亏公告 30 日内（同上，负面词表命中）

    附带「参考」不硬命中：股东质押比例（RPT_CSDC_LIST），且只认 1 年内的行——
    2014 年的质押记录不代表今天的风险，硬命中会天天误杀。

    硬约束（R91m/n）：源失败一律如实进 failed / unknown，绝不补 0、绝不把取不到写成「无风险」。

    用法：
    python3 stock_risk_blacklist.py                 # 默认扫 data.js：aiPrediction + us/ashare 五节要闻
                                                    #   + bullish/bearish + 四技术池（R100z70 扩域）
    python3 stock_risk_blacklist.py --json          # 只输出 JSON
    python3 stock_risk_blacklist.py --write         # 同时写 risk_blacklist.json（前端+闸门读）
    python3 stock_risk_blacklist.py --codes-file c.txt
    python3 stock_risk_blacklist.py --lift-lo 2 --lift-hi 5   # 解禁窗口（自然日，默认 T+2~T+5）
    python3 stock_risk_blacklist.py --pledge 30                # 质押比例阈值（%）
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(ROOT, 'data.js')
OUT_FILE = os.path.join(ROOT, 'risk_blacklist.json')

NODE_SRC = r"""
const fs = require('fs');
const p = process.env.DATA_PATH;
let s = fs.readFileSync(p, 'utf8');
let m = s.match(/window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!m) { console.error('data.js parse failed'); process.exit(1); }
let d; try { d = eval('(' + m[1] + ')'); } catch (e) { console.error('eval failed', e.message); process.exit(1); }
// R100z70：扫描域从「仅 aiPrediction」扩为 aiPrediction + us/ashare 五节要闻(含 impacts) +
// bullish/bearish + 四技术池(断板/谐波/吸筹/九门)。此前 validate 的新闻闸/9:45 star 闸
// 都拿本文件当日标的对象做命中，扫描域不含这些 code 时闸门等于对它们失明。
const codes = []; const seen = {}; const names = {};
function add(st) {
  if (!st || typeof st !== 'object') return;
  const c = String(st.code || '').replace(/\D/g, '');
  if (c.length !== 6) return;
  if (!seen[c]) { seen[c] = 1; codes.push(c); }
  if (st.name && !names[c]) names[c] = st.name;
}
// 1) AI 预测（原口径）
for (const sec of ((d.aiPrediction || {}).sectors || [])) for (const st of (sec.stocks || [])) add(st);
// 2) us/ashare 五节要闻 + bullish/bearish
for (const mk of ['us', 'ashare']) {
  const sec = d[mk] || {};
  for (const key of ['bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews']) {
    for (const nw of (sec[key] || [])) {
      if (!nw || typeof nw !== 'object') continue;
      for (const st of (nw.stocks || [])) add(st);
      for (const imp of (nw.impacts || [])) { if (imp && typeof imp === 'object') for (const st of (imp.stocks || [])) add(st); }
    }
  }
  for (const key of ['bullish', 'bearish']) for (const g of (sec[key] || [])) { if (g && typeof g === 'object') for (const st of (g.stocks || [])) add(st); }
}
// 3) 四技术池（前端卡片 ⚠️ 角标也读这份黑名单）
const db = d.duanban || {};
for (const st of (db.confirmed || [])) add(st);
for (const st of (db.watching || [])) add(st);
const hz = d.harmonic || {};
for (const st of (hz.confirmPool || [])) add(st);
for (const st of (hz.watchPool || [])) add(st);
const acc = d.accumulation || {};
for (const st of (acc.scored || [])) add(st);
for (const st of (((d.powerScreen || {}).passed) || [])) add(st);
process.stdout.write(JSON.stringify({ date: (d.aiPrediction || {}).date || null, codes: codes, names: names }));
"""

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36'}
EM_DC = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
EM_PUSH2 = 'https://push2.eastmoney.com/api/qt/ulist.np/get'
EM_ANN = 'https://np-anotice-stock.eastmoney.com/api/security/ann'


def http_json(url, referer=None, timeout=12, params=None):
    if params:
        sep = '&' if ('?' in url) else '?'
        url = url + sep + params
    headers = dict(UA)
    if referer:
        headers['Referer'] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'ignore'))


def to_secid(code):
    code = str(code).replace('.', '')
    if code.startswith(('sh', 'sz')):
        code = code[2:]
    return ('1' if code.startswith('60') else '0') + '.' + code


def d8(v):
    """'2010-08-23 00:00:00' / '2010-08-23' → date；解析不出返回 None。"""
    s = str(v or '').strip()[:10]
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except Exception:
        return None


def load_stocks(path=DATA_JS):
    import subprocess
    node = ('/Users/loccco/.workbuddy/binaries/run-node'
            if os.path.exists('/Users/loccco/.workbuddy/binaries/run-node') else 'node')
    env = dict(os.environ, DATA_PATH=path)
    pr = subprocess.run([node, '-e', NODE_SRC], env=env, capture_output=True, text=True)
    if pr.returncode != 0:
        print('[ERR] 读取 data.js 失败：' + (pr.stderr or '').strip()[:200])
        return None
    return json.loads(pr.stdout)


def fetch_names(codes):
    """行情名（判 ST 用），一次批量；失败返回 {}（此时用 data.js 内的 name 兜底）。"""
    if not codes:
        return {}
    secids = ','.join(to_secid(c) for c in codes)
    try:
        js = http_json(EM_PUSH2, referer='https://quote.eastmoney.com/',
                       params='fields=f12,f14&secids=' + secids)
        rows = (js.get('data') or {}).get('diff') or []
        out = {}
        for r in rows:
            if r.get('f12'):
                out[str(r['f12']).replace('.', '')] = r.get('f14') or ''
        if out:
            return out
    except Exception as e:
        print('[EM_NAMES_FAIL] push2 取名失败：' + repr(e)[:120])
    return {}


def fetch_lift(code, lo, hi, today):
    """限售解禁：FREE_DATE 落在 [today+lo, today+hi] → 命中窗口。"""
    try:
        js = http_json(EM_DC, referer='https://data.eastmoney.com/', params=(
            'columns=ALL&pageSize=60&pageNumber=1&reportName=RPT_LIFT_STAGE'
            '&filter=(SECURITY_CODE%3D%22' + str(code) + '%22)'
            '&sortColumns=FREE_DATE&sortTypes=1&source=WEB&client=WEB'))
    except Exception as e:
        return None, repr(e)[:100]
    rows = ((js.get('result') or {}).get('data') or [])
    if not isinstance(rows, list) or not rows:
        return [], None
    lo_d, hi_d = today + timedelta(days=lo), today + timedelta(days=hi)
    hits = []
    for r in rows:  # 只扫最新 60 条里最近的若干解禁 batches
        d = d8(r.get('FREE_DATE'))
        if d and lo_d <= d <= hi_d:
            hits.append('解禁 %s（%s，占总股本 %.1f%%）' % (
                d.isoformat(), r.get('FREE_SHARES_TYPE') or '限售股',
                round(float(r.get('TOTAL_RATIO') or 0) * 100, 1)))
        if len(hits) >= 3:
            break
    return hits, None


def fetch_pledge(code, within_days=365):
    """股东质押（★只作参考、不硬命中）：1 年内的高质押行。返回 (文案列表, err)。

    2014 年的质押记录不代表今天的风险，硬命中会天天误杀（2026-10-01 实测踩过）。
    """
    try:
        js = http_json(EM_DC, referer='https://data.eastmoney.com/', params=(
            'columns=ALL&pageSize=20&pageNumber=1&reportName=RPT_CSDC_LIST'
            '&filter=(SECURITY_CODE%3D%22' + str(code) + '%22)'
            '&sortColumns=TRADE_DATE&sortTypes=-1&source=WEB&client=WEB'))
    except Exception as e:
        return None, repr(e)[:100]
    rows = ((js.get('result') or {}).get('data') or [])
    if not isinstance(rows, list) or not rows:
        return [], None
    cutoff = d8(date.today()) - timedelta(days=within_days)
    newest, maxv = None, 0.0
    for r in rows:
        d = d8(r.get('TRADE_DATE'))
        if d and d >= cutoff:
            newest = d
            try:
                maxv = max(maxv, float(r.get('PLEDGE_RATIO') or 0))
            except Exception:
                pass
    if newest is None or maxv <= 0:
        return [], None
    return ['参考·质押 %.1f%%（截至 %s）' % (maxv, newest.isoformat())], None


def fetch_ann(code, days=30):
    """公告源：一次取最近 50 条，按关键词拆成 减持 / 业绩预减 两类。源不可达返回 None。

    ★np-anotice 在部分网络下会被拦（沙箱实测 http 567），不可达时进 unknown 明确写
    「未下结论」——绝不因为取不到就当这只票没风险。
    """
    try:
        js = http_json(EM_ANN, referer='https://data.eastmoney.com/', params=(
            'sr=-1&page_size=50&page_index=1&ann_type=A&client_source=web&stock_list=' + str(code)))
    except Exception as e:
        return None, repr(e)[:100]
    rows = (js.get('data') or {}).get('list') or []
    if not isinstance(rows, list) or not rows:
        return [], None
    CUT_KW = ('减持', '减仓', '减持计划', '减持股份')
    LOSS_KW = ('业绩预减', '预亏', '首亏', '业绩下滑', '净利润较上年同期下降',
               '归母净利润为负值', '业绩预告为亏损', '净利润同比下降')
    cutoff = d8(date.today()) - timedelta(days=days)
    cuts, losses = [], []
    for r in rows[:50]:
        title = str(r.get('title') or '')
        d = d8(r.get('notice_date')) or d8(r.get('display_time'))
        if d is None or d < cutoff:
            continue
        if any(k in title for k in CUT_KW):
            cuts.append('%s 减持公告' % d.isoformat())
        elif any(k in title for k in LOSS_KW):
            losses.append('%s 业绩预减公告' % d.isoformat())
        if len(cuts) + len(losses) >= 4:
            break
    return cuts + losses, None


def grade_reasons(reasons, watch):
    """R100z76-④：把命中原因分级为 severity（red/orange/yellow），并抽减持进度。

    red   = 硬剔（ST/退市、业绩预减/预亏、减持实施中）
    orange = 警示但可保留（减持预披露/计划、限售解禁窗口）
    yellow = 参考（股东质押≥阈值，仅作风险参考）
    默认 none；调用方对 red 才 FAIL、orange 给 WARN、yellow 跳过。
    """
    sev = 0  # 0 none, 1 yellow, 2 orange, 3 red
    progress = []
    for r in (reasons or []):
        if 'ST' in r or '退' in r:
            sev = max(sev, 3)
        elif '业绩预减' in r or '预亏' in r:
            sev = max(sev, 3)
        elif '减持计划' in r:
            sev = max(sev, 2)
            if '减持预披露' not in progress:
                progress.append('减持预披露')
        elif '减持' in r or '减仓' in r:
            sev = max(sev, 3)
            if '减持实施中' not in progress:
                progress.append('减持实施中')
        elif '解禁' in r:
            sev = max(sev, 2)
    for w in (watch or []):
        if '质押' in w:
            sev = max(sev, 1)
    name = {3: 'red', 2: 'orange', 1: 'yellow', 0: 'none'}[sev]
    return name, progress


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--write', action='store_true', help='写 risk_blacklist.json')
    ap.add_argument('--codes-file')
    ap.add_argument('--lift-lo', type=int, default=2)
    ap.add_argument('--lift-hi', type=int, default=5)
    ap.add_argument('--pledge', type=float, default=30.0)
    ap.add_argument('--cut-days', type=int, default=30)
    args = ap.parse_args()

    today = date.today()
    info = load_stocks()
    if args.codes_file:
        codes = [c.strip() for c in open(args.codes_file, encoding='utf-8') if c.strip().isdigit()]
        local_names = {}
        date_tag = None
    elif not info:
        return 1
    else:
        codes = info.get('codes') or []
        local_names = info.get('names') or {}
        date_tag = info.get('date')

    if not codes:
        msg = 'data.js 里没找到 aiPrediction 标的（或 --codes-file 为空）'
        print(msg)
        if args.json:
            print(json.dumps({'ok': False, 'reason': msg}, ensure_ascii=False))
        return 1

    names = fetch_names(codes)
    for c in codes:
        names.setdefault(c, local_names.get(c, ''))

    blacklist, unknown, failed = {}, [], []

    def one(code):
        lift, e1 = fetch_lift(code, args.lift_lo, args.lift_hi, today)
        ple, e2 = fetch_pledge(code)
        ann, e3 = fetch_ann(code, args.cut_days)
        return code, lift, e1, ple, e2, ann, e3

    with ThreadPoolExecutor(max_workers=12) as ex:  # R100z70 扩域后标的量数倍增，8→12
        for code, lift, e1, ple, e2, ann, e3 in ex.map(one, codes):
            reasons, watch = [], []
            if e1:
                failed.append('%s 解禁源：%s' % (code, e1))
            else:
                reasons += (lift or [])
            if e2:
                failed.append('%s 质押源：%s' % (code, e2))
            else:
                watch += (ple or [])
            if e3:
                unknown.append('%s 公告源不可达（np-anotice %s），减持/业绩预减未下结论' % (code, e3))
            else:
                reasons += (ann or [])
            nm = names.get(code, '')
            if nm and ('ST' in nm.upper() or '退' in nm):
                reasons.insert(0, 'ST/退市（当前名称 %s）' % nm)
            if reasons:
                sev, prog = grade_reasons(reasons, watch)
                blacklist[code] = {'name': nm, 'reasons': reasons,
                                   'count': len(reasons), 'watch': watch,
                                   'severity': sev, 'progress': prog}

    res = {
        'ok': True,
        'date': date_tag or today.isoformat(),
        'checked': len(codes),
        'liftWindow': [args.lift_lo, args.lift_hi],
        'watchPledgeWithin': 365,
        'cutDays': args.cut_days,
        'blacklist': blacklist,
        'unknown': sorted(set(unknown)),
        'failed': sorted(set(failed)),
        'note': '硬风险黑名单（R100z76-④ severity 分级）：red=硬剔(ST/退市·业绩预减/预亏·减持实施中)、'
                 'orange=警示可保留(减持预披露/计划·限售解禁窗口)、yellow=参考(质押≥%.0f%%)；'
                 'ST/退市、T+%d~T+%d 解禁、减持公告 %d 日内；源不可达不判安全' % (
            args.pledge, args.lift_lo, args.lift_hi, args.cut_days),
    }

    if args.write:
        with open(OUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)

    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print('标的 %d 只 / 硬风险命中 %d 只' % (len(codes), len(blacklist)))
        for c, v in sorted(blacklist.items()):
            sev = v.get('severity', 'red')
            prog = (' · ' + '、'.join(v.get('progress') or [])) if v.get('progress') else ''
            print('  [%s] %s %s → %s%s' % (sev.upper(), c, v['name'], '；'.join(v['reasons']), prog))
        if res['unknown']:
            print('[UNKNOWN] ' + '；'.join(res['unknown']))
        if res['failed']:
            print('[FAILED] ' + '；'.join(res['failed']))
        if not blacklist:
            print('  本次无确定性硬风险命中（但见上方 UNKNOWN：源不可达不等于安全）')
    return 0


if __name__ == '__main__':
    sys.exit(main())

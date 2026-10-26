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
import re
import sys
import time
import urllib.error
import urllib.parse
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
for (const st of (((d.breakoutPool || {}).passed) || [])) add(st);
process.stdout.write(JSON.stringify({ date: (d.aiPrediction || {}).date || null, codes: codes, names: names }));
"""

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36'}
EM_DC = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
EM_PUSH2 = 'https://push2.eastmoney.com/api/qt/ulist.np/get'
EM_ANN = 'https://np-anotice-stock.eastmoney.com/api/security/ann'
TX_Q = 'https://qt.gtimg.cn/q='                      # R100z167：行情名主源（GBK）
SINA_Q = 'https://hq.sinajs.cn/list='                # R100z167：行情名备源（GBK，必须带 Sina Referer）


def http_json(url, referer=None, timeout=12, params=None, retries=1):
    """取 JSON。retries=1 表示最多 2 次尝试——**只对网络类抖动重试**。

    R100z160：此前无重试，一次并发抖动就把整维度判成「不可达」。但 np-anotice 的
    HTTP 567 是站点/WAF 层拒绝，重试纯属浪费，故 HTTPError 直接抛出、不进重试循环。
    """
    if params:
        sep = '&' if ('?' in url) else '?'
        url = url + sep + params
    headers = dict(UA)
    if referer:
        headers['Referer'] = referer
    req = urllib.request.Request(url, headers=headers)
    last = None
    for att in range(max(1, retries + 1)):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8', 'ignore'))
        except urllib.error.HTTPError:
            raise                      # 状态码类拒绝（如 567）重试无意义
        except Exception as e:         # noqa: BLE001  超时/连接重置等
            last = e
            if att < retries:
                time.sleep(0.4 * (att + 1))
    raise last


def http_text(url, referer=None, timeout=12, encoding='utf-8', retries=1):
    """取纯文本（腾讯 qt.gtimg / 新浪 hq.sinajs 都是 GBK 文本，不是 JSON）。"""
    headers = dict(UA)
    if referer:
        headers['Referer'] = referer
    req = urllib.request.Request(url, headers=headers)
    last = None
    for att in range(max(1, retries + 1)):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode(encoding, 'ignore')
        except urllib.error.HTTPError:
            raise
        except Exception as e:         # noqa: BLE001
            last = e
            if att < retries:
                time.sleep(0.4 * (att + 1))
    raise last


# R100z160：公告源熔断器。2026-10-21 实测 np-anotice 对全部 343 只连续返回 567，
# 旧逻辑会打 343 次请求并在 unknown 里堆 188 条近似重复行（报告里只看到「大面积 567」，
# 既看不出「哪些维度没核查」，又白等几十秒）。连续失败达阈值即整体熔断，
# 把「这一维度整体未核查」变成一条可机读结论。
ANN_BREAK = {'run': 0, 'open': False, 'msg': '', 'limit': 5, 'skipped': 0, 'src': 'em'}
ANN_BREAK_TAG = '__ANN_BREAKER__'


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


def _pref(code):
    """6 位代码 → 腾讯/新浪的交易所前缀（sh/sz/bj）。"""
    c = str(code).strip().replace('.', '')[-6:]
    if c.startswith(('60', '68', '90')):
        return 'sh' + c
    if c.startswith(('00', '30', '20')):
        return 'sz' + c
    if c.startswith(('4', '8')):
        return 'bj' + c
    return 'sh' + c


NAME_SRC_STAT = {'tx': 0, 'sina': 0, 'em': 0}


def _names_tx(part):
    """腾讯 qt.gtimg.cn（GBK）：v_sh600000="1~浦发银行~600000~…" """
    txt = http_text(TX_Q + ','.join(_pref(c) for c in part),
                    referer='https://gu.qq.com/', encoding='gbk')
    got = {}
    for seg in txt.split(';'):
        if '="' not in seg:
            continue
        f = seg.split('="', 1)[1].strip().rstrip('"').split('~')
        if len(f) > 2 and f[1].strip() and f[2].strip().isdigit():
            got[f[2].strip()[-6:]] = f[1].strip()
    return got


def _names_sina(part):
    """新浪 hq.sinajs.cn（GBK，必须带 Sina Referer，否则 Forbidden）：
    var hq_str_sh600000="浦发银行,9.72,…" """
    txt = http_text(SINA_Q + ','.join(_pref(c) for c in part),
                    referer='https://finance.sina.com.cn/', encoding='gbk')
    got = {}
    for seg in txt.split(';'):
        if 'hq_str_' not in seg or '="' not in seg:
            continue
        head, body = seg.split('="', 1)
        code = head.strip().split('hq_str_')[-1][-6:]
        nm = body.split(',')[0].strip().strip('"')
        if code.isdigit() and nm:
            got[code] = nm
    return got


def _names_em(part):
    """东财 push2（末位兜底）。2026-10-21 起本机对 push2 全断（RemoteDisconnected）。"""
    secids = ','.join(to_secid(c) for c in part)
    js = http_json(EM_PUSH2, referer='https://quote.eastmoney.com/',
                   params='fields=f12,f14&secids=' + secids)
    rows = (js.get('data') or {}).get('diff') or []
    return {str(r['f12']).replace('.', '')[-6:]: (r.get('f14') or '')
            for r in rows if r.get('f12')}


def fetch_names(codes):
    """行情名（判 ST 用）：腾讯 gtimg 主源 → 新浪备源 → 东财 push2 末位。

    R100z167（2026-10-21 用户实报「怎么那么多遗留问题」）：
        旧实现**只用东财 push2**。push2 已在本机整段被封（连续 3 次
        RemoteDisconnected），于是每轮 ST/退市都是 0/365，报告只能写「该维度未核查」——
        这不是数据源抖一下，是**唯一源死了没有备源**，把兜底做成了装饰。
        实测：腾讯 qt.gtimg.cn 单批 80 只秒回（有名 75/80，缺的是退市/停牌代码）、
        新浪 hq.sinajs.cn 80/80 全回（两者都需 GBK 解码，新浪还需 Sina Referer）。
    逐批补漏：一批里谁缺就换下一个源补，三个源都缺才回退 data.js 旧名并如实标未核查。
    """
    if not codes:
        return {}
    out, CHUNK = {}, 60
    for i in range(0, len(codes), CHUNK):
        part = codes[i:i + CHUNK]
        for src, fn in (('tx', _names_tx), ('sina', _names_sina), ('em', _names_em)):
            miss = [c for c in part if str(c)[-6:] not in out]
            if not miss:
                break
            for attempt in (0, 1):
                try:
                    got = fn(miss)
                except Exception as e:                              # noqa: BLE001
                    if attempt == 1:
                        print('[NAME_SRC_FAIL] %s 取名失败（第 %d 批，%d 只）：%s'
                              % (src, i // CHUNK + 1, len(miss), repr(e)[:100]))
                    else:
                        time.sleep(0.6)
                    continue
                for c, nm in got.items():
                    c6 = str(c)[-6:]
                    if nm and c6 not in out:
                        out[c6] = nm
                        NAME_SRC_STAT[src] = NAME_SRC_STAT.get(src, 0) + 1
                break
    return out


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
            # R100z167：TOTAL_RATIO 缺失时旧版写「0.0%」——把「未披露」伪装成「解禁量为零」，
            # 这种命中既无法量化也无法解释（2026-10-21 华勤技术实报）。缺就如实写未披露。
            ratio = r.get('TOTAL_RATIO')
            try:
                ratio_txt = '占总股本 %.1f%%' % (float(ratio) * 100)
            except (TypeError, ValueError):
                ratio_txt = '占总股本比例未披露'
            hits.append('解禁 %s（%s，%s）' % (
                d.isoformat(), r.get('FREE_SHARES_TYPE') or '限售股', ratio_txt))
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


CNINFO = 'http://www.cninfo.com.cn/new/hisAnnouncement/query'
CNINFO_ORG = 'http://www.cninfo.com.cn/new/information/topSearch/query'
ANN_STAT = {'em_ok': 0, 'em_fail': 0, 'cn_ok': 0, 'cn_fail': 0}
_ORG_CACHE = {}


def http_post_form(url, data, referer=None, timeout=12, retries=1):
    """表单 POST（巨潮公告查询只认 POST + 表单编码）。"""
    headers = dict(UA)
    headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
    if referer:
        headers['Referer'] = referer
    last = None
    for att in range(max(1, retries + 1)):
        try:
            req = urllib.request.Request(url, data=data.encode('utf-8'), headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8', 'ignore')
        except urllib.error.HTTPError as e:
            last = e
            if att < retries and e.code in (429, 567):
                time.sleep(1.0 * (att + 1))
                continue
            raise
        except Exception as e:         # noqa: BLE001
            last = e
            if att < retries:
                time.sleep(0.5 * (att + 1))
    raise last


def _ann_rows_cninfo(code, days):
    """巨潮资讯（法定披露渠道）→ [(date, title)]。

    R100z167：np-anotice 在本机实测 19/20 请求返回 HTTP 567（WAF 段封），
    旧逻辑熔断后直接放弃 → 减持/业绩预减两个维度整轮未核查。巨潮是该数据的
    **一手源**（沪深交易所指定披露平台），实测稳定，故作为 np-anotice 的接手源。
    """
    c = str(code)[-6:]
    col = 'sse' if c.startswith(('6', '9')) else 'szse'
    org = _ORG_CACHE.get(c)
    if not org:
        try:
            txt = http_post_form(CNINFO_ORG, urllib.parse.urlencode(
                {'keyWord': c, 'maxNum': '10'}), referer='http://www.cninfo.com.cn/')
            rows = json.loads(txt) or []
            for r in rows:
                if str(r.get('code')) == c and r.get('orgId'):
                    org = r['orgId']
                    break
        except Exception:                                        # noqa: BLE001
            org = None
        if not org:                     # 兜底：市场前缀 + 补零（多数标的可用，个别 orgId 为纯数字）
            org = ('gssh0' if col == 'sse' else 'gssz0') + c[:6]
        _ORG_CACHE[c] = org
    lo = (date.today() - timedelta(days=days)).isoformat()
    hi = (date.today() + timedelta(days=1)).isoformat()   # 含次日预披露（晚间披露按次日计）
    body = urllib.parse.urlencode({
        'pageNum': '1', 'pageSize': '30', 'column': col, 'tabName': 'fulltext',
        'plate': '', 'stock': '%s,%s' % (c, org), 'searchkey': '',
        'seDate': '%s~%s' % (lo, hi), 'sortName': '', 'sortType': '',
        'isHLtitle': 'false'})
    txt = http_post_form(
        CNINFO, body,
        referer='http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice')
    js = json.loads(txt)
    out = []
    for a in (js.get('announcements') or []):
        title = re.sub(r'<[^>]+>', '', str(a.get('announcementTitle') or '')).strip()
        ts = a.get('announcementTime')
        d = None
        if isinstance(ts, (int, float)) and ts > 0:
            d = datetime.fromtimestamp(ts / 1000).date()
        if d and title:
            out.append((d, title))
    return out


def fetch_ann(code, days=30):
    """公告源：np-anotice（快、易被 567）→ 连续失败即切巨潮（稳、一手）。

    ★R100z167：旧逻辑把「熔断」当成「本维度放弃」（剩余标的直接返回标记），
    结果减持/业绩预减永远 partial。熔断应该只是**换源**，不是弃查。
    """
    rows, err = None, None
    if ANN_BREAK.get('src', 'em') == 'em':
        if ANN_BREAK['open']:
            ANN_BREAK['src'] = 'cninfo'
        else:
            try:
                js = http_json(EM_ANN, referer='https://data.eastmoney.com/', retries=0, params=(
                    'sr=-1&page_size=50&page_index=1&ann_type=A&client_source=web&stock_list='
                    + str(code)))
                ANN_BREAK['run'] = 0        # 有成功即清零（偶发抖动不该熔断整轮）
                ANN_STAT['em_ok'] += 1
                rows = []
                for r in ((js.get('data') or {}).get('list') or []):
                    d = d8(r.get('notice_date')) or d8(r.get('display_time'))
                    rows.append((d, str(r.get('title') or '')))
            except Exception as e:                                  # noqa: BLE001
                err = repr(e)[:100]
                ANN_STAT['em_fail'] += 1
                ANN_BREAK['run'] += 1
                if not ANN_BREAK['msg']:
                    ANN_BREAK['msg'] = err
                if ANN_BREAK['run'] >= ANN_BREAK['limit']:
                    ANN_BREAK['open'] = True
                    ANN_BREAK['src'] = 'cninfo'
                    print('[ANN_SWITCH] np-anotice 连续 %d 只失败（%s）→ 本轮回退巨潮资讯'
                          % (ANN_BREAK['run'], ANN_BREAK['msg']))

    if rows is None and ANN_BREAK.get('src', 'em') == 'cninfo':
        try:
            rows = _ann_rows_cninfo(code, days)
            ANN_STAT['cn_ok'] += 1
            err = None
        except Exception as e:                                      # noqa: BLE001
            err = repr(e)[:100]
            ANN_STAT['cn_fail'] += 1

    if rows is None:
        return None, err or '公告源不可达'

    CUT_KW = ('减持', '减仓', '减持计划', '减持股份')
    PLAN_KW = ('减持计划', '预披露', '拟减持', '减持股份计划')
    LOSS_KW = ('业绩预减', '预亏', '首亏', '业绩下滑', '净利润较上年同期下降',
               '归母净利润为负值', '业绩预告为亏损', '净利润同比下降')
    cutoff = d8(date.today()) - timedelta(days=days)
    cuts, losses = [], []
    for d, title in rows[:50]:
        if d is None or d < cutoff:
            continue
        if any(k in title for k in CUT_KW):
            # R100z167：必须保留「计划 / 实施」区别。旧版一律写成「减持公告」，
            # grade_reasons 认不出「减持计划」→ 预披露被误判 red 硬剔（把好票杀掉）。
            tag = '减持计划（预披露）' if any(k in title for k in PLAN_KW) else '减持公告'
            cuts.append('%s %s' % (d.isoformat(), tag))
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

    NAME_SRC_STAT.update({'tx': 0, 'sina': 0, 'em': 0})     # R100z167：逐轮重计
    names = fetch_names(codes)
    em_names_cnt = len(names)              # R100z167：真实行情名覆盖率（已含腾讯/新浪）
    if names:
        name_src = max(NAME_SRC_STAT, key=lambda k: NAME_SRC_STAT[k]) or 'quote'
    else:
        name_src = 'local'
    for c in codes:   # 兜底仅用于显示名称；此时 ST 判定降级为存疑（见下方 unknown，绝不静默当安全）
        names.setdefault(str(c)[-6:], local_names.get(c, '') or local_names.get(str(c)[-6:], ''))

    blacklist, unknown, failed = {}, [], []
    stat = {'ann_ok': 0, 'ann_fail': 0, 'lift_fail': 0, 'pledge_fail': 0}

    def one(code):
        lift, e1 = fetch_lift(code, args.lift_lo, args.lift_hi, today)
        ple, e2 = fetch_pledge(code)
        ann, e3 = fetch_ann(code, args.cut_days)
        return code, lift, e1, ple, e2, ann, e3

    with ThreadPoolExecutor(max_workers=12) as ex:  # R100z70 扩域后标的量数倍增，8→12
        for code, lift, e1, ple, e2, ann, e3 in ex.map(one, codes):
            reasons, watch = [], []
            if e1:
                stat['lift_fail'] += 1
                failed.append('%s 解禁源：%s' % (code, e1))
            else:
                reasons += (lift or [])
            if e2:
                stat['pledge_fail'] += 1
                failed.append('%s 质押源：%s' % (code, e2))
            else:
                watch += (ple or [])
            if e3 == ANN_BREAK_TAG:
                stat['ann_fail'] += 1        # 熔断后不再逐只堆 unknown，出口统一成一条
            elif e3:
                stat['ann_fail'] += 1
                unknown.append('%s 公告源不可达（np-anotice %s），减持/业绩预减未下结论' % (code, e3))
            else:
                stat['ann_ok'] += 1
                reasons += (ann or [])
            nm = names.get(str(code)[-6:], '')
            if nm and ('ST' in nm.upper() or '退' in nm):
                reasons.insert(0, 'ST/退市（当前名称 %s）' % nm)
            if reasons:
                sev, prog = grade_reasons(reasons, watch)
                blacklist[code] = {'name': nm, 'reasons': reasons,
                                   'count': len(reasons), 'watch': watch,
                                   'severity': sev, 'progress': prog}

    if name_src == 'local':   # R100z129：行情名源不可达时不得静默给「无 ST 命中」的假阴性
        unknown.append('行情名源（腾讯 gtimg / 新浪 / 东财 push2 三源全不可达）'
                       '：ST/退市判定已回退 data.js 名称'
                       '（AI 写回的可能是更名前旧名，会漏判 ST），该维度本次不判安全')
    elif em_names_cnt < len(codes):   # R100z160：只取到一部分时，余下静默回退旧名 = 假阴性
        unknown.append('行情名源（腾讯 gtimg / 新浪 / 东财 push2）仅取到 %d/%d 只：'
                       '余下 %d 只的 ST/退市判定回退 data.js 旧名，该部分不判安全'
                       % (em_names_cnt, len(codes), len(codes) - em_names_cnt))

    if ANN_BREAK['open']:
        unknown.append('公告源（np-anotice）本轮连续 %d 只失败（%s）→ 已切巨潮资讯；'
                       '巨潮成功 %d 只 / 失败 %d 只，失败部分减持·业绩预减维度未核查'
                       % (ANN_BREAK['run'], ANN_BREAK['msg'],
                          ANN_STAT['cn_ok'], ANN_STAT['cn_fail']))

    # ---------------- 维度核查台账（R100z160） ----------------
    # 用户 2026-10-21 实报：push2 与 np-anotice 同时不可达，报告却写成「已核查无风险」。
    # 根因是脚本只给 unknown 明细、不给「哪些维度整体没核查」的结论层，报告层就自由发挥了。
    # 现在把每个维度显式标 ok / partial / unchecked，并给总口径 verdict，
    # 汇报必须照抄 uncheckedDims 原话，禁止绝对化表述（audit G24 会按 data.js 文本拦）。
    n_all = len(codes)
    ann_ok = stat['ann_ok']
    ann_cov = 'ok' if ann_ok >= n_all else ('partial' if ann_ok > 0 else 'unchecked')
    lift_ok = n_all - stat['lift_fail']
    lift_cov = 'ok' if lift_ok >= n_all else ('partial' if lift_ok > 0 else 'unchecked')
    ple_ok = n_all - stat['pledge_fail']
    ple_cov = 'ok' if ple_ok >= n_all else ('partial' if ple_ok > 0 else 'unchecked')
    st_cov = ('ok' if em_names_cnt >= n_all
              else ('partial' if em_names_cnt > 0 else 'unchecked'))
    dims = {
        'st': {'status': st_cov,
               'cover': '行情名源（腾讯%s/新浪%s/东财%s）%d/%d 只取到' % (
                   NAME_SRC_STAT['tx'], NAME_SRC_STAT['sina'], NAME_SRC_STAT['em'],
                   em_names_cnt, n_all)},
        'cut': {'status': ann_cov,
                'cover': '公告源 %d/%d 只取到（东财%d/巨潮%d）' % (
                    ann_ok, n_all, ANN_STAT['em_ok'], ANN_STAT['cn_ok'])},
        'loss': {'status': ann_cov,
                 'cover': '公告源 %d/%d 只取到（东财%d/巨潮%d）' % (
                     ann_ok, n_all, ANN_STAT['em_ok'], ANN_STAT['cn_ok'])},
        'lift': {'status': lift_cov, 'cover': '解禁源 %d/%d 只取到' % (lift_ok, n_all)},
        'pledge': {'status': ple_cov, 'cover': '质押源 %d/%d 只取到（仅供参考）' % (ple_ok, n_all)},
    }
    core = [dims[k]['status'] for k in ('st', 'cut', 'loss', 'lift')]
    verdict = ('full' if all(x == 'ok' for x in core)
               else ('none' if all(x == 'unchecked' for x in core) else 'partial'))
    label = {'st': 'ST/退市（行情名源）', 'cut': '减持公告', 'loss': '业绩预减/预亏',
             'lift': '限售解禁', 'pledge': '股东质押（参考）'}
    unchecked = ['%s——%s' % (label[k], dims[k]['cover'])
                 for k in ('st', 'cut', 'loss', 'lift', 'pledge')
                 if dims[k]['status'] != 'ok']

    res = {
        'ok': True,
        'nameSrc': name_src,
        'nameSrcDetail': dict(NAME_SRC_STAT),
        'date': date_tag or today.isoformat(),
        'checked': len(codes),
        'liftWindow': [args.lift_lo, args.lift_hi],
        'watchPledgeWithin': 365,
        'cutDays': args.cut_days,
        'dims': dims,
        'verdict': verdict,
        'uncheckedDims': unchecked,
        'blacklist': blacklist,
        'unknown': sorted(set(unknown)),
        'failed': sorted(set(failed)),
        'note': '硬风险黑名单（R100z76-④ severity 分级）：red=硬剔(ST/退市·业绩预减/预亏·减持实施中)、'
                 'orange=警示可保留(减持预披露/计划·限售解禁窗口)、yellow=参考(质押≥%.0f%%)；'
                 'ST/退市、T+%d~T+%d 解禁、减持公告 %d 日内；源不可达不判安全（verdict=full 才算各维度全核查）' % (
            args.pledge, args.lift_lo, args.lift_hi, args.cut_days),
    }

    if args.write:
        with open(OUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)

    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print('标的 %d 只 / 硬风险命中 %d 只 ｜ 维度核查 verdict=%s'
              % (len(codes), len(blacklist), res['verdict']))
        print('[维度] ' + '｜'.join('%s=%s' % (k, v['status']) for k, v in res['dims'].items()))
        for c, v in sorted(blacklist.items()):
            sev = v.get('severity', 'red')
            prog = (' · ' + '、'.join(v.get('progress') or [])) if v.get('progress') else ''
            print('  [%s] %s %s → %s%s' % (sev.upper(), c, v['name'], '；'.join(v['reasons']), prog))
        if res['verdict'] != 'full':
            print('=' * 72)
            print('[!] 本次未真正核查的维度（汇报必须原样点名，'
                  '严禁写「已核查无风险」「黑名单全部通过」这类绝对表述）：')
            for u in res['uncheckedDims']:
                print('    - ' + u)
            print('    verdict=%s —— 黑名单结论只覆盖已核查维度，其余维度「未核查」≠「无风险」'
                  % res['verdict'])
            print('=' * 72)
        if res['unknown']:
            print('[UNKNOWN] ' + '；'.join(res['unknown']))
        if res['failed']:
            print('[FAILED] ' + '；'.join(res['failed']))
        if not blacklist:
            if res['verdict'] == 'full':
                print('  各维度均已核查，本次无确定性硬风险命中')
            else:
                print('  本次无命中，但 verdict=%s：未核查维度不得当作「无风险」，须按上方清单原样汇报'
                      % res['verdict'])
    return 0


if __name__ == '__main__':
    sys.exit(main())

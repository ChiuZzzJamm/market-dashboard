#!/usr/bin/env python3
"""个股主力资金维度（R100z13）——回答「钱是不是真进了这 8 只票」。

背景：看板原来只有行业级主力资金（fundIn/fundOut），行业资金能解释板块、解释不了选股。
行业说「有色净流入 20 亿」，不等于你挑的那 8 只每只都有大单进——这是「方向对而选股错」的另一半解释。

数据源（★2026-10-01 实测：东财 push2 在该网络下只能偶发连通，故设双源）
    主源 东财 push2 ulist.np  一次批量拿全部  f62 主力净额 f66 超大单 f72 大单 f78 中单 f84 小单
         https://push2.eastmoney.com/api/qt/ulist.np/get?fields=f2,f3,f12,f14,f62,f66,f72,f78,f84&secids=1.600000,...
         secid 前缀：1. 沪（60）/ 0. 深（00、30）
    兜底 新浪 逐只 ssl_qsfx_zjlrqs  取最新一条
         https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs?daima=sh600000
         netamount 主力净额 / r0_net 超大单净额 / r1_net 大单 / r2_net 中单 / r3_net 小单
    兜底源会在东财失败时自动启用（逐只并发，只取最新一条历史记录）

硬约束（R91m/n）：任一源失败一律保留既有值、如实进 failed 列表，绝不补 0。

用法：
    python3 stock_fundflow.py                        # 默认从 data.js 的 aiPrediction 标的里自动取码
    python3 stock_fundflow.py --codes-file codes.txt # 指定标的（每行一个 6 位码）
    python3 stock_fundflow.py --json                 # 只输出 JSON
"""
import argparse
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(ROOT, 'data.js')
NODE_SRC = r"""
const fs = require('fs');
const p = process.env.DATA_PATH;
let s = fs.readFileSync(p, 'utf8');
let m = s.match(/window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!m) { console.error('data.js parse failed'); process.exit(1); }
let d; try { d = eval('(' + m[1] + ')'); } catch (e) { console.error('eval failed', e.message); process.exit(1); }
const ap = d.aiPrediction || {};
const codes = []; const seen = {};
for (const sec of (ap.sectors || [])) {
  for (const st of (sec.stocks || [])) {
    const c = String(st.code || '').replace(/\D/g, '');
    if (c.length === 6 && !seen[c]) { seen[c] = 1; codes.push(c); }
  }
}
const names = {};
for (const sec of (ap.sectors || [])) {
  for (const st of (sec.stocks || [])) {
    const c = String(st.code || '').replace(/\D/g, '');
    if (c.length === 6 && st.name) { names[c] = st.name; }
  }
}
process.stdout.write(JSON.stringify({ date: ap.date || null, codes: codes, names: names }));
"""
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36',
      'Referer': 'https://finance.sina.com.cn'}


def http_json(url, referer=None, timeout=12):
    headers = dict(UA)
    if referer:
        headers['Referer'] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'ignore'))


def to_sina_code(code):
    code = str(code).split('.')[0]
    if code.startswith(('sh', 'sz')):
        code = code[2:]
    return ('sh' if code.startswith('60') else 'sz') + code


def to_secid(code):
    code = str(code).split('.')[0]
    return ('1' if code.startswith('60') else '0') + '.' + code


def yi(v):
    """元 → 亿元（两位小数）；非数值原样返回 None。"""
    if not isinstance(v, (int, float)):
        return None
    return round(v / 1e8, 2)


def norm(code, **kw):
    return dict({'code': str(code)}, **kw)


# ---------- 主源：东财 push2（一次批量） ----------
def fetch_eastmoney(codes):
    url = ('https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2'
           '&fields=f2,f3,f12,f14,f62,f66,f72,f78,f84'
           '&secids=' + ','.join(to_secid(c) for c in codes) +
           '&ut=fa5fd1943c7b386f172d6893dbfba10b')
    raw = http_json(url, referer='https://quote.eastmoney.com/', timeout=12)
    out = {}
    for it in ((raw.get('data') or {}).get('diff') or []):
        f = lambda k: it.get(k) if isinstance(it.get(k), (int, float)) else None
        out[str(it.get('f12'))] = norm(str(it.get('f12')), name=it.get('f14'), price=f('f2'), pct=f('f3'),
                                       main=f('f62'), super=f('f66'), big=f('f72'), mid=f('f78'), small=f('f84'))
    return out


# ---------- 兜底：新浪逐只（只取最新一条） ----------
def fetch_sina_one(code):
    raw = http_json('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
                    'MoneyFlow.ssl_qsfx_zjlrqs?daima=' + to_sina_code(code),
                    referer='https://finance.sina.com.cn', timeout=15)
    rows = raw if isinstance(raw, list) else [raw]
    if not rows:
        return None
    r = rows[0] or {}
    def n(k):
        try:
            return float(r.get(k))
        except (TypeError, ValueError):
            return None
    return norm(code, name=r.get('name'), price=n('trade'), pct=n('changeratio'),
                main=n('netamount'), super=n('r0_net'), big=n('r1_net'), mid=n('r2_net'), small=n('r3_net'),
                date=r.get('opendate'))


def fetch_sina(codes, workers=8):
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(fetch_sina_one, codes):
            if res:
                out[res['code']] = res
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--codes-file', default=None, help='每行一个 6 位代码；不指定则取 data.js 的预测标的')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    codes = []
    if a.codes_file:
        codes = [''.join(ch for ch in open(a.codes_file, encoding='utf-8').read() if ch.isdigit())[:6]]
        codes = [c for c in codes if len(c) == 6]
    else:
        import subprocess
        node = ('/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node'
                if os.path.exists('/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node') else 'node')
        pr = subprocess.run([node, '-e', NODE_SRC], env=dict(os.environ, DATA_PATH=DATA_JS),
                            capture_output=True, text=True)
        if pr.returncode != 0:
            print('[ERR] 读取 data.js 失败：' + (pr.stderr or '').strip()[:200], file=sys.stderr)
            print(json.dumps({'ok': False, 'items': [], 'failed': [], 'reason': 'data.js read failed'},
                             ensure_ascii=False))
            return 1
        _m = json.loads(pr.stdout)
        codes = _m.get('codes') or []
        names = _m.get('names') or {}

    got, src = {}, ''
    if codes:
        try:
            got = fetch_eastmoney(codes)
            if got:
                src = 'eastmoney'
        except Exception as e:
            print('[EM_FAIL] 东财 push2 不可用，降级新浪：' + str(e)[:100], file=sys.stderr)
        if not got:
            try:
                got = fetch_sina(codes)
                src = 'sina' if got else ''
            except Exception as e:
                print('[EM_FAIL] 新浪资金流也不可用：' + str(e)[:100], file=sys.stderr)

    failed, items = [], []
    for c in codes:
        d = got.get(c)
        if not d:
            failed.append(c)          # ★ 取不到就是取不到，绝不补 0（R91m）
            continue
        d = dict(d)
        # 新浪该接口不带股票简称，用 data.js 里标的池的既有名补上（对不上就留空，不编造）
        d['name'] = d.get('name') or names.get(c, '')
        d['mainYi'] = yi(d.get('main'))
        d['superYi'] = yi(d.get('super'))
        d['bigYi'] = yi(d.get('big'))
        d['src'] = src
        items.append(d)

    print(json.dumps({'ok': True, 'src': src, 'count': len(items), 'items': items, 'failed': failed,
                      'note': '取不到的一律记 failed，绝不补 0（R91m）'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())

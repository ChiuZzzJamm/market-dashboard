#!/usr/bin/env python3
"""
自包含日韩数据更新脚本（不依赖任何 MCP，供自动化直接调用）。

数据源（全部为 curl HTTP 直连，任何会话/自动化环境均可用）：
- 个股（三星电子/SK海力士/软银/东京电子/铠侠）：腾讯 qt.gtimg.cn，
  代码前缀 kr005930 / kr000660 / jp9984 / jp8035 / jp285A
- 指数（KOSPI/日经225）：东方财富 push2 HTTP 直连（secid=100.KS11 / 100.N225），
  Yahoo chart API（^KS11 / ^N225）兜底——带时间戳校验，杜绝旧数据
- 注意：这是 HTTP 行情接口，与「东财 MCP（mx-ds-mcp）」完全无关，后者已证明不可靠并禁用

流程：抓取 → 时间戳校验（必须今日）→ 方向一致性/异常值校验 → 写回 data.js 的 panorama.kr/jp。
用法：python3 update_kr_jp.py --label 早盘|收盘（默认按时间：北京时间>=14:00 为收盘，否则早盘）
"""
import json, os, sys, re, subprocess, argparse, time
from datetime import datetime, timezone, timedelta
from common import load_dashboard_data

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
TZ8 = timezone(timedelta(hours=8))  # 北京时间

# 腾讯代码 -> 中文名 -> 标准代码 / Yahoo 代码
STOCKS = [
    ('kr005930', '三星电子', '005930.KS', '005930.KS'),
    ('kr000660', 'SK海力士', '000660.KS', '000660.KS'),
    ('kr105560', 'KB金融',   '105560.KS', '105560.KS'),
    ('jp9984',   '软银集团', '9984.T',   '9984.T'),
    ('jp8035',   '东京电子', '8035.T',   '8035.T'),
    ('jp285A',   '铠侠',     '285A.T',   '285A.T'),
]

TODAY = datetime.now(TZ8).strftime('%Y-%m-%d')
TS_RE = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')


def curl_get(url, headers=None, retries=3, timeout=12):
    """用 curl 抓取（urllib 会被部分源拒连，curl 在所有环境可用）；失败返回 None"""
    cmd = ['curl', '-s', '--max-time', str(timeout), '-H', f'User-Agent: {UA}']
    for k, v in (headers or {}).items():
        cmd += ['-H', f'{k}: {v}']
    cmd.append(url)
    for i in range(1, retries + 1):
        p = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout
        print(f'[warn] curl attempt {i} failed ({url[:60]}...)')
        if i < retries:
            time.sleep(3)
    return None


def bj_date_from_ts(unix_ts):
    return datetime.fromtimestamp(int(unix_ts), TZ8).strftime('%Y-%m-%d')


# ---------- 个股：腾讯 ----------
def fetch_stocks_tencent():
    codes = ','.join(c for c, _, _, _ in STOCKS)
    raw = curl_get(f'http://qt.gtimg.cn/q={codes}')
    if not raw:
        return None
    text = raw.decode('gb2312', errors='replace')
    out = {}
    for m in re.finditer(r'v_(\w+)="([^"]*)"', text):
        body = m.group(2)
        ts_match = TS_RE.search(body)
        if not ts_match:
            continue
        ts = ts_match.group(0)
        if not ts.startswith(TODAY):
            print(f'[warn] 腾讯 {m.group(1)} 时间戳 {ts} 非今日，丢弃')
            continue
        f = body[body.index(ts):].split('~')
        if len(f) < 3:
            continue
        try:
            pct = float(f[2])
        except ValueError:
            continue
        for code, cn, std, _ in STOCKS:
            if m.group(1).lower() == code.lower():
                out[std] = {'name': cn, 'changePct': round(pct, 2)}
                break
    return out or None


def fetch_stock_yahoo(std):
    """腾讯失败时的单只兜底：Yahoo chart API（带时间戳校验）"""
    host = 'query1' if std.endswith('.KS') else 'query2'
    raw = curl_get(f'https://{host}.finance.yahoo.com/v8/finance/chart/{std}?interval=1d&range=1d', retries=2)
    if not raw:
        return None
    try:
        meta = json.loads(raw)['chart']['result'][0]['meta']
        if bj_date_from_ts(meta['regularMarketTime']) != TODAY:
            print(f'[warn] yahoo {std} 非今日数据，丢弃')
            return None
        pct = meta.get('regularMarketChangePercent')
        if pct is None:
            return None
        cn = next((c for _, c, s, _ in STOCKS if s == std), std)
        return {'name': cn, 'changePct': round(float(pct), 2)}
    except Exception as e:
        print(f'[warn] yahoo {std} parse failed: {e}')
        return None


# ---------- 指数：东财 push2 直连 + Yahoo 兜底 ----------
def fetch_index_eastmoney(secid):
    raw = curl_get(
        f'https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f43,f58,f60,f86,f170',
        headers={'Referer': 'https://quote.eastmoney.com/'}, retries=1)
    if not raw:
        return None
    try:
        d = json.loads(raw).get('data')
        if not d:
            return None
        if bj_date_from_ts(d['f86']) != TODAY:
            print(f'[warn] eastmoney {secid} 非今日数据，丢弃')
            return None
        return {'name': d['f58'], 'changePct': round(d['f170'] / 100, 2)}
    except Exception as e:
        print(f'[warn] eastmoney {secid} parse failed: {e}')
        return None


YAHOO_INDEX = {'KS11.GI': ('^KS11', '韩国综合指数 KOSPI'), 'N225.GI': ('^N225', '日经225')}
EASTMONEY_SECID = {'KS11.GI': '100.KS11', 'N225.GI': '100.N225'}


def fetch_index_yahoo(sym, name):
    raw = curl_get(f'https://query2.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1d', retries=2)
    if not raw:
        return None
    try:
        meta = json.loads(raw)['chart']['result'][0]['meta']
        if bj_date_from_ts(meta['regularMarketTime']) != TODAY:
            print(f'[warn] yahoo {sym} 非今日数据，丢弃')
            return None
        pct = meta.get('regularMarketChangePercent')
        if pct is None:
            return None
        return {'name': name, 'changePct': round(float(pct), 2)}
    except Exception as e:
        print(f'[warn] yahoo {sym} parse failed: {e}')
        return None


def collect():
    """返回 (indices, stocks)"""
    stocks = fetch_stocks_tencent()
    for _, cn, std, _ in STOCKS:
        if stocks is None or std not in stocks:
            fb = fetch_stock_yahoo(std)
            if fb:
                stocks = stocks or {}
                stocks[std] = fb
                print(f'[info] {cn} 使用 Yahoo 兜底')

    indices = {}
    for key in ('KS11.GI', 'N225.GI'):
        idx = fetch_index_eastmoney(EASTMONEY_SECID[key])
        if not idx:
            sym, name = YAHOO_INDEX[key]
            idx = fetch_index_yahoo(sym, name)
            if idx:
                print(f'[info] {name} 使用 Yahoo 兜底')
        if idx:
            indices[key] = idx
        else:
            print(f'[warn] {name} 所有数据源失败')
    return indices, stocks


def validate(indices, stocks):
    """方向一致性 + 异常值校验；不通过返回 False"""
    def same_direction(a, b):
        return (a >= 0) == (b >= 0)

    # 指数涨跌幅绝对值 < 0.8% 时方向属噪音（个股独立行情常见），不做否决
    checks = [
        ('KS11.GI', ['005930.KS', '000660.KS'], 'KOSPI', '韩股龙头'),
        ('N225.GI', ['9984.T', '8035.T', '285A.T'], '日经225', '日经龙头'),
    ]
    for idx_key, stock_keys, idx_name, leader_name in checks:
        idx_pct = indices.get(idx_key, {}).get('changePct')
        if idx_pct is None or abs(idx_pct) < 0.8:
            continue
        leaders = [stocks.get(s, {}).get('changePct') for s in stock_keys]
        leaders = [x for x in leaders if x is not None]
        if leaders:
            matched = sum(1 for x in leaders if same_direction(idx_pct, x))
            if matched / len(leaders) < 0.5:
                print(f'[warn] {idx_name} {idx_pct:+.2f}% 与{leader_name} {leaders} 方向严重背离，放弃写入')
                return False

    for k, v in list(indices.items()):
        if abs(v['changePct']) > 15:
            print(f'[warn] 指数 {k} {v["changePct"]}% 异常，丢弃')
            del indices[k]
    for k, v in list(stocks.items()):
        if abs(v['changePct']) > 20:
            print(f'[warn] 个股 {k} {v["changePct"]}% 异常，丢弃')
            del stocks[k]
    return True


def build_kr(indices, stocks):
    kospi = indices.get('KS11.GI', {}).get('changePct')
    samsung = stocks.get('005930.KS', {}).get('changePct')
    skhynix = stocks.get('000660.KS', {}).get('changePct')

    items = []
    if samsung is not None and skhynix is not None:
        avg = round((samsung + skhynix) / 2, 2)
        items.append({'name': '半导体/存储', 'pct': avg,
                      'ref': f'SK海力士 {skhynix:+.2f}%、三星电子 {samsung:+.2f}%（2龙头均值）'})
    elif skhynix is not None:
        items.append({'name': '半导体/存储', 'pct': skhynix,
                      'ref': f'SK海力士 {skhynix:+.2f}%（单一龙头）'})
    elif samsung is not None:
        items.append({'name': '半导体/存储', 'pct': samsung,
                      'ref': f'三星电子 {samsung:+.2f}%（单一龙头）'})

    kb = stocks.get('105560.KS')
    if kb is not None:
        items.append({'name': '金融', 'pct': kb['changePct'],
                      'ref': f"KB金融 {kb['changePct']:+.2f}%（单一龙头）"})

    return {'key': 'kr', 'name': '韩股',
            'indices': [{'name': '韩国综合指数 KOSPI', 'changePct': kospi}] if kospi is not None else [],
            'items': items}


def build_jp(indices, stocks):
    n225 = indices.get('N225.GI', {}).get('changePct')
    softbank = stocks.get('9984.T', {}).get('changePct')
    tokyoelec = stocks.get('8035.T', {}).get('changePct')
    kioxia = stocks.get('285A.T', {}).get('changePct')

    items = []
    if softbank is not None:
        items.append({'name': 'AI/科技投资', 'pct': softbank,
                      'ref': f'软银集团 {softbank:+.2f}%（单一龙头）'})
    if tokyoelec is not None:
        items.append({'name': '半导体设备', 'pct': tokyoelec,
                      'ref': f'东京电子 {tokyoelec:+.2f}%（单一龙头）'})
    if kioxia is not None:
        items.append({'name': '存储', 'pct': kioxia,
                      'ref': f'铠侠 {kioxia:+.2f}%（单一龙头）'})

    return {'key': 'jp', 'name': '日经',
            'indices': [{'name': '日经225', 'changePct': n225}] if n225 is not None else [],
            'items': items}


def main():
    parser = argparse.ArgumentParser(description='自包含日韩数据更新（零 MCP 依赖）')
    parser.add_argument('--label', choices=['早盘', '收盘'], default=None,
                        help='数据时点标签；默认北京时间>=14:00 为收盘，否则早盘')
    parser.add_argument('--dry-run', action='store_true', help='只打印，不写回 data.js')
    args = parser.parse_args()

    if args.label is None:
        args.label = '收盘' if datetime.now(TZ8).hour >= 14 else '早盘'
    label = args.label

    indices, stocks = collect()
    if not indices and not stocks:
        print('[error] 所有数据源均失败，保留 panorama.kr/jp 原值')
        sys.exit(1)
    if not validate(indices, stocks):
        print('[info] 校验未通过，保留原值')
        sys.exit(0)

    date_display = f"{int(TODAY[5:7])}/{int(TODAY[8:10])} {label}"
    kr = build_kr(indices, stocks)
    kr['date'] = date_display
    jp = build_jp(indices, stocks)
    jp['date'] = date_display

    print(f'[info] panorama.kr/jp -> {date_display}')
    for k, v in indices.items():
        print(f"       {v['name']} {v['changePct']:+.2f}%")
    for _, v in stocks.items():
        print(f"       {v['name']} {v['changePct']:+.2f}%")

    if args.dry_run:
        print('[dry-run] 不写回 data.js')
        print(json.dumps({'kr': kr, 'jp': jp}, ensure_ascii=False, indent=2))
        return

    D = load_dashboard_data(BASE)
    markets = D.setdefault('panorama', {}).setdefault('markets', [])
    replaced = {'kr': False, 'jp': False}
    for i, m in enumerate(markets):
        if m.get('key') == 'kr':
            markets[i] = kr
            replaced['kr'] = True
        elif m.get('key') == 'jp':
            markets[i] = jp
            replaced['jp'] = True
    if not replaced['kr']:
        markets.append(kr)
    if not replaced['jp']:
        markets.append(jp)

    with open('data.js', 'w', encoding='utf-8') as f:
        f.write('window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, indent=2) + ';\n')
    print('[info] data.js 已更新')


if __name__ == '__main__':
    main()

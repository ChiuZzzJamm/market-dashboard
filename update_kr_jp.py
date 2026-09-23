#!/usr/bin/env python3
"""
自包含日韩数据更新脚本（不依赖任何 MCP，供自动化直接调用）。

数据源（全部为 curl HTTP 直连，任何会话/自动化环境均可用）：
- 个股（三星电子/SK海力士/软银/东京电子/铠侠）：腾讯 qt.gtimg.cn，
  代码前缀 kr005930 / kr000660 / jp9984 / jp8035 / jp285A
- 指数（KOSPI/日经225）：Yahoo chart API（^KS11 / ^N225）主用（更稳定），
  东方财富 push2 HTTP 直连（secid=100.KS11 / 100.N225）兜底——两者均带时间戳校验，杜绝旧数据
- 注意：这是 HTTP 行情接口，与「东财 MCP（mx-ds-mcp）」完全无关，后者已证明不可靠并禁用

流程：抓取 → 时间戳校验（必须今日）→ 异常值校验 → 写回 data.js 的 panorama.kr/jp。
用法：python3 update_kr_jp.py --label 早盘|收盘（默认按时间：北京时间>=14:00 为收盘，否则早盘）
"""
import json, os, sys, re, subprocess, argparse, time
from datetime import datetime, timezone, timedelta
from common import load_dashboard_data

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
TZ8 = timezone(timedelta(hours=8))  # 北京时间

# 腾讯代码 -> 中文名 -> 标准代码（Yahoo 兜底用）。板块 -> 龙头个股聚合，比单只龙头更贴近板块真实表现。
KR_BOARDS = [
    ('半导体/存储', [('kr005930', '三星电子', '005930.KS'), ('kr000660', 'SK海力士', '000660.KS'), ('kr009150', '三星电机', '009150.KS')]),
    ('汽车', [('kr005380', '现代汽车', '005380.KS'), ('kr000270', '起亚', '000270.KS'), ('kr012330', '现代摩比斯', '012330.KS')]),
    ('金融', [('kr105560', 'KB金融', '105560.KS'), ('kr055550', '新韩金融', '055550.KS'), ('kr086790', '韩亚金融', '086790.KS'), ('kr316140', '三星生命', '316140.KS')]),
    ('电池/化工', [('kr051910', 'LG化学', '051910.KS'), ('kr096770', 'SK Innovation', '096770.KS'), ('kr373220', 'LG能源', '373220.KS'), ('kr003670', 'SKC', '003670.KS')]),
    ('钢铁', [('kr005490', 'POSCO', '005490.KS'), ('kr015760', '现代制铁', '015760.KS')]),
    ('医药', [('kr068270', 'Celltrion', '068270.KS'), ('kr207940', '三星生物', '207940.KS')]),
    ('通信', [('kr005935', 'SK电信', '005935.KS'), ('kr017670', 'KT', '017670.KS'), ('kr030200', 'LG Uplus', '030200.KS')]),
    ('零售', [('kr004170', '乐天购物', '004170.KS'), ('kr139480', '新世界', '139480.KS'), ('kr004100', 'CJ', '004100.KS')]),
    ('造船', [('kr009540', 'HD现代重工', '009540.KS'), ('kr042660', '三星重工', '042660.KS')]),
]
JP_BOARDS = [
    ('汽车', [('jp7203', '丰田', '7203.T'), ('jp7267', '本田', '7267.T'), ('jp7201', '日产', '7201.T')]),
    ('电子元件/设备', [('jp8035', '东京电子', '8035.T'), ('jp6857', 'Advantest', '6857.T'), ('jp6981', '村田', '6981.T'), ('jp6273', 'SMC', '6273.T')]),
    ('银行金融', [('jp8316', '三菱UFJ', '8316.T'), ('jp8411', '瑞穗', '8411.T'), ('jp8306', '三井住友', '8306.T')]),
    ('通信', [('jp9432', 'NTT', '9432.T'), ('jp9433', 'KDDI', '9433.T'), ('jp9434', 'SoftBank电信', '9434.T')]),
    ('医药', [('jp4568', '第一三共', '4568.T'), ('jp4502', '武田', '4502.T'), ('jp4519', 'Chugai', '4519.T')]),
    ('零售消费', [('jp9983', '迅销', '9983.T'), ('jp8267', '永旺', '8267.T'), ('jp3382', '7&i', '3382.T')]),
    ('重工机械', [('jp6301', '小松', '6301.T'), ('jp6954', '发那科', '6954.T'), ('jp6305', '日立建机', '6305.T')]),
    ('化工', [('jp4063', '信越化学', '4063.T'), ('jp3407', '旭化成', '3407.T'), ('jp4452', '花王', '4452.T')]),
    ('电力能源', [('jp9501', '东京电力', '9501.T'), ('jp9503', '关西电力', '9503.T'), ('jp9508', '九州电力', '9508.T')]),
    ('地产', [('jp8801', '三菱地所', '8801.T'), ('jp8802', '三井不动产', '8802.T'), ('jp8804', '东京建物', '8804.T')]),
]
# 扁平化（腾讯代码, 中文名, 标准代码）供抓取与兜底
ALL_STOCKS = [(tc, cn, std) for boards in (KR_BOARDS, JP_BOARDS) for _, members in boards for tc, cn, std in members]

TODAY = datetime.now(TZ8).strftime('%Y-%m-%d')
TS_RE = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')


# ---------- 日本 / 韩国法定休市表（脚本级判定，休市日不抓取、保留上一交易日、date 标注「休市」） ----------
# 2026 日本法定休市（含振替休日、国民の休日）。盂兰盆(8月)非法定休市，不列入。
# TODO: 2027 起需更新此集合（或改为动态计算）。
JP_HOLIDAYS_2026 = {
    '2026-01-01', '2026-01-02', '2026-01-03',   # 元日 + 银行休业日
    '2026-01-12',                               # 成人の日（1月第2月曜）
    '2026-02-11',                               # 建国記念の日
    '2026-02-23',                               # 天皇誕生日
    '2026-03-20',                               # 春分の日
    '2026-04-29',                               # 昭和の日
    '2026-05-03', '2026-05-04', '2026-05-05',   # 憲法記念日 / みどりの日 / こどもの日（黄金周）
    '2026-07-20',                               # 海の日（7月第3月曜）
    '2026-08-11',                               # 山の日
    '2026-09-21', '2026-09-22', '2026-09-23',   # 敬老の日(3rd月9/21) + 国民の休日(9/22) + 秋分の日(9/23)
    '2026-10-12',                               # スポーツの日（10月第2月曜）
    '2026-11-03',                               # 文化の日
    '2026-11-23',                               # 勤労感謝の日
    '2026-12-31',                               # 大晦日
}
# 2026 韩国法定休市（含代替休日）。설날/추석 前后连休。
KR_HOLIDAYS_2026 = {
    '2026-01-01',                               # 元旦
    '2026-02-17', '2026-02-18', '2026-02-19',   # 설날（农历1/1 = 2/17，连休）
    '2026-03-01',                               # 三一節
    '2026-05-05',                               #  어린이날
    '2026-05-24',                               #  부처님 오신 날（佛诞，农历4/8）
    '2026-06-06',                               #  현충일
    '2026-08-15',                               #  광복절
    '2026-09-25', '2026-09-26', '2026-09-27',   # 추석（农历8/15 = 9/25，连休）
    '2026-10-03',                               #  개천절
    '2026-10-09',                               #  한글날
    '2026-12-25',                               #  크리스마스
}


def is_jp_holiday(d):
    return d.strftime('%Y-%m-%d') in JP_HOLIDAYS_2026


def is_kr_holiday(d):
    return d.strftime('%Y-%m-%d') in KR_HOLIDAYS_2026


def _upsert(markets, entry):
    """按 key 替换 markets 中的市场项，不存在则追加。"""
    for i, m in enumerate(markets):
        if m.get('key') == entry.get('key'):
            markets[i] = entry
            return
    markets.append(entry)


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
    codes = ','.join(c for c, _, _ in ALL_STOCKS)
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
        for code, cn, std in ALL_STOCKS:
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
        cn = next((c for tc, c, s in ALL_STOCKS if s == std), std)
        return {'name': cn, 'changePct': round(float(pct), 2)}
    except Exception as e:
        print(f'[warn] yahoo {std} parse failed: {e}')
        return None


# ---------- 指数：Yahoo 主用 + 东财 push2 直连兜底 ----------
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
    # 腾讯全失败时返回 None，统一转成空 dict，避免后续 validate()/build_* 对 None 调用 .items()/.get() 崩溃
    stocks = fetch_stocks_tencent() or {}
    # Yahoo 单只兜底带连续失败熔断：全部数据源故障时避免 50 只个股逐只慢超时拖长整体耗时
    fb_fail = 0
    for _, cn, std in ALL_STOCKS:
        if stocks is None or std not in stocks:
            if fb_fail >= 6:
                print(f'[warn] Yahoo 兜底连续失败 {fb_fail} 次，跳过剩余个股兜底')
                continue
            fb = fetch_stock_yahoo(std)
            if fb:
                stocks = stocks or {}
                stocks[std] = fb
                fb_fail = 0
                print(f'[info] {cn} 使用 Yahoo 兜底')
            else:
                fb_fail += 1

    indices = {}
    for key in ('KS11.GI', 'N225.GI'):
        # Yahoo 主用（更稳），东财 push2 兜底；两者口径一致且均带「必须今日」时间戳校验
        sym, name = YAHOO_INDEX[key]
        idx = fetch_index_yahoo(sym, name)
        if not idx:
            idx = fetch_index_eastmoney(EASTMONEY_SECID[key])
            if idx:
                print(f'[info] {name} 东财兜底')
        if idx:
            indices[key] = idx
        else:
            print(f'[warn] {name} 所有数据源失败')
    return indices, stocks


def validate(indices, stocks, label='收盘'):
    """异常值校验；不通过返回 False。
    方向一致性校验已按用户要求移除（2026-09-18）：龙头与指数短期背离属常态，
    该校验多次误杀正常收盘数据（如日经 +1.38% 与龙头背离）导致日韩板块停留上一交易日；
    时间戳校验（必须当日）仍保留，足以杜绝旧数据混入。"""
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
    items = []
    for bname, members in KR_BOARDS:
        pts, detail = [], []
        for tc, cn, std in members:
            sp = stocks.get(std, {}).get('changePct')
            if sp is None:
                continue
            pts.append(sp)
            detail.append(f'{cn}{sp:+.2f}%')
        if not pts:
            continue
        avg = round(sum(pts) / len(pts), 2)
        items.append({'name': bname, 'pct': avg, 'ref': '、'.join(detail) + f'（{len(pts)}只均值）'})
    items.sort(key=lambda x: -x['pct'])
    return {'key': 'kr', 'name': '韩股',
            'indices': [{'name': '韩国综合指数 KOSPI', 'changePct': kospi}] if kospi is not None else [],
            'items': items}


def build_jp(indices, stocks):
    n225 = indices.get('N225.GI', {}).get('changePct')
    items = []
    for bname, members in JP_BOARDS:
        pts, detail = [], []
        for tc, cn, std in members:
            sp = stocks.get(std, {}).get('changePct')
            if sp is None:
                continue
            pts.append(sp)
            detail.append(f'{cn}{sp:+.2f}%')
        if not pts:
            continue
        avg = round(sum(pts) / len(pts), 2)
        items.append({'name': bname, 'pct': avg, 'ref': '、'.join(detail) + f'（{len(pts)}只均值）'})
    items.sort(key=lambda x: -x['pct'])
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

    # 休市判定：日本/韩国法定休市日不抓取、保留上一交易日、date 标注「休市」（修复 A2）
    today_d = datetime.now(TZ8).date()
    kr_holiday = is_kr_holiday(today_d)
    jp_holiday = is_jp_holiday(today_d)
    date_display = f"{int(TODAY[5:7])}/{int(TODAY[8:10])} {label}"

    # 两市场均休市：无需抓取，仅标注休市并保留上一交易日数据
    if kr_holiday and jp_holiday:
        D = load_dashboard_data(BASE)
        markets = D.setdefault('panorama', {}).setdefault('markets', [])
        for hk, nm in (('kr', '韩股'), ('jp', '日经')):
            for i, m in enumerate(markets):
                if m.get('key') == hk:
                    mk = dict(m)
                    mk['date'] = f"{int(TODAY[5:7])}/{int(TODAY[8:10])} 休市"
                    markets[i] = mk
                    break
            else:
                markets.append({'key': hk, 'name': nm, 'indices': [], 'items': [],
                                'date': f"{int(TODAY[5:7])}/{int(TODAY[8:10])} 休市"})
        D['updatedAt'] = (f"{datetime.now(TZ8).strftime('%Y-%m-%d %H:%M')}"
                          f"（韩日 {date_display} 休市，保留上一交易日数据）")
        with open('data.js', 'w', encoding='utf-8') as f:
            f.write('window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, indent=2) + ';\n')
        print('[info] 韩日均休市，date 标注休市，保留上一交易日数据')
        return

    indices, stocks = collect()
    if not indices and not stocks:
        print('[warn] 所有数据源均失败，保留 panorama.kr/jp 原值')
        sys.exit(0)
    if not validate(indices, stocks, label):
        print('[info] 校验未通过，保留原值')
        sys.exit(0)

    print(f'[info] panorama.kr/jp -> {date_display}'
          + ('（韩股休市，保留上一交易日）' if kr_holiday else '')
          + ('（日经休市，保留上一交易日）' if jp_holiday else ''))
    for k, v in indices.items():
        print(f"       {v['name']} {v['changePct']:+.2f}%")
    for _, v in stocks.items():
        print(f"       {v['name']} {v['changePct']:+.2f}%")

    D = load_dashboard_data(BASE)
    markets = D.setdefault('panorama', {}).setdefault('markets', [])

    # 休市市场：保留上一交易日 items/indices，仅把 date 改为「休市」（不抓取、不写早盘/收盘）
    for hk, nm in (('kr', '韩股'), ('jp', '日经')):
        if (hk == 'kr' and kr_holiday) or (hk == 'jp' and jp_holiday):
            found = False
            for i, m in enumerate(markets):
                if m.get('key') == hk:
                    mk = dict(m)
                    mk['date'] = f"{int(TODAY[5:7])}/{int(TODAY[8:10])} 休市"
                    markets[i] = mk
                    found = True
                    break
            if not found:
                markets.append({'key': hk, 'name': nm, 'indices': [], 'items': [],
                                'date': f"{int(TODAY[5:7])}/{int(TODAY[8:10])} 休市"})
            print(f"[info] {nm} {TODAY} 休市，保留上一交易日数据，date 标注休市")

    # 正常市场：抓取并重建（跳过已休市市场）
    if not kr_holiday:
        kr = build_kr(indices, stocks)
        kr['date'] = date_display
        kr_ok = len(kr['items']) >= (len(KR_BOARDS) + 1) // 2
        if not kr_ok:
            print(f"[warn] 韩股有效板块 {len(kr['items'])}/{len(KR_BOARDS)} 不足半数，保留原值不覆盖")
        else:
            _upsert(markets, kr)
    if not jp_holiday:
        jp = build_jp(indices, stocks)
        jp['date'] = date_display
        jp_ok = len(jp['items']) >= (len(JP_BOARDS) + 1) // 2
        if not jp_ok:
            print(f"[warn] 日股有效板块 {len(jp['items'])}/{len(JP_BOARDS)} 不足半数，保留原值不覆盖")
        else:
            _upsert(markets, jp)

    if args.dry_run:
        print('[dry-run] 不写回 data.js')
        out = {}
        if not kr_holiday:
            out['kr'] = kr
        if not jp_holiday:
            out['jp'] = jp
        if kr_holiday:
            out['kr_holiday'] = True
        if jp_holiday:
            out['jp_holiday'] = True
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    # updatedAt：覆盖式（单一固定描述，不累积、不拼长括号说明）—— 修复 A1 累积污染
    # 修复：逐市场写明「已更新/休市」，不使用易误读的「X（Y休市）」缩写（此前映射写反：
    # 日经休市时误写「日经（韩股休市）」，读者会误解为日经已更新）
    parts = []
    for hk, nm in (('kr', '韩股'), ('jp', '日经')):
        holi = kr_holiday if hk == 'kr' else jp_holiday
        if holi:
            parts.append(f"{nm} {int(TODAY[5:7])}/{int(TODAY[8:10])} 休市")
        else:
            parts.append(f"{nm} {date_display} 已更新")
    ts = datetime.now(TZ8).strftime('%Y-%m-%d %H:%M')
    D['updatedAt'] = f"{ts}（{'；'.join(parts)}）"

    with open('data.js', 'w', encoding='utf-8') as f:
        f.write('window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, indent=2) + ';\n')
    print('[info] data.js 已更新')


if __name__ == '__main__':
    main()

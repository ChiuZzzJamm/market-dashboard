#!/usr/bin/env python3
"""N 字形态研究：全市场日 K 抓取器（2026-10-26 新建）。

为什么另起一份缓存而不是复用 .kline_cache：
  看板用的 .kline_cache 只有 320 根（2025-06-30 起），且被 harmonic/accumulation
  等线上模块共用；本次研究要 2024-01-01 至今日 + 前置窗口算 MA250，
  不能动线上缓存的 TTL / 条数语义。故独立目录 .kline_hist/。

数据源：东财 push2his（klt=101 日线，fqt=1 前复权），一次请求给全区间，
  比腾讯 ifzq 的 320 根上限省 3 倍请求；且带成交额与换手率（判断「倍量」「放量」
  用成交额比用成交量更抗送转）。
字段：f51 日期, f52 开, f53 收, f54 高, f55 低, f56 成交量(手), f57 成交额(元), f58 换手率

约定：
- 存储 [date, open, close, high, low, volume(股), amount(元), turnover(%)]
- 只保留 <= LAST_DAY（默认最近一个已收盘交易日）的 K 线，盘中未收盘的那根剔除。
- 抓取失败按缺失处理，不写空文件（沿用 kline_cache R91m 约定）。
"""
import json
import os
import sys
import time
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, '.kline_hist')
LIST_PATH = os.path.join(BASE, '.kline_hist_list.json')

BEG = '20230101'          # 前置窗口：算 MA250 / 60 日均额 / 上市满一年
END = '20261101'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
# 东财单节点会限流（2026-10-26 实测：连续 1300 只后主域返回空串）。
# 数字子域是同一份数据的不同接入节点，轮询可把限流摊平；失效节点自动跳过。
NODES = ['7', '23', '45', '13', '29', '36', '54', '61', '75', '88']
_nrr = [0]


def next_host():
    _nrr[0] += 1
    return 'https://%s.push2his.eastmoney.com' % NODES[_nrr[0] % len(NODES)]


def _curl(url, timeout=20):
    import subprocess
    cmd = ['curl', '-s', '--max-time', str(timeout),
           '-H', 'User-Agent: ' + UA,
           '-H', 'Referer: https://quote.eastmoney.com/']
    try:
        p = subprocess.run(cmd + [url], capture_output=True, timeout=timeout + 6)
    except Exception:
        return None
    if p and p.returncode == 0 and p.stdout.strip():
        return p.stdout.decode('utf-8', errors='replace')
    return None


def secid(code):
    return ('1.' if code.startswith('6') else '0.') + code


def fetch_list():
    """全市场股票列表（沪深主板/创业板/科创板），分页抓。"""
    out, pn, total = [], 1, None
    while True:
        url = ('https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=0&np=1'
               '&fltt=2&invt=2&fid=f12&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23'
               '&fields=f12,f14,f2,f20' % pn)
        raw = _curl(url)
        if not raw:
            break
        try:
            d = (json.loads(raw) or {}).get('data') or {}
        except Exception:
            break
        if total is None:
            total = int(d.get('total') or 0)
        diff = d.get('diff') or []
        if not diff:
            break
        for r in diff:
            code = str(r.get('f12') or '')
            name = str(r.get('f14') or '').strip()
            if not code or len(code) != 6:
                continue
            out.append({'code': code, 'name': name,
                        'mcap': r.get('f20') if isinstance(r.get('f20'), (int, float)) else None})
        pn += 1
        if len(out) >= total or pn > 80:
            break
    # 去重
    seen, uniq = set(), []
    for r in out:
        if r['code'] in seen:
            continue
        seen.add(r['code'])
        uniq.append(r)
    return uniq


def _tencent_seg(code, beg, end, last_day):
    """腾讯 ifzq 单段日 K（前复权）。返回 [(day,o,c,h,l,vol_hands)]。"""
    tcode = ('sh' if code.startswith('6') else 'sz') + code
    url = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param='
           + tcode + ',day,' + beg + ',' + end + ',640,qfq')
    for _ in range(3):
        raw = _curl(url, timeout=15)
        if raw:
            try:
                j = json.loads(raw)
                d = (j.get('data') or {}).get(tcode) or {}
                arr = d.get('qfqday') or d.get('day') or []
            except Exception:
                arr = []
            out = []
            for x in arr:
                try:
                    if x[0] > last_day:
                        continue
                    out.append((str(x[0]), float(x[1]), float(x[2]),
                                float(x[3]), float(x[4]), float(x[5])))
                except Exception:
                    continue
            if out:
                return out
        time.sleep(0.3)
    return []


def fetch_kline(code, last_day):
    """腾讯分两段拼接（单次上限 640 根）：2023-01 ~ 2024-02 + 2024-03 ~ 今。

    2026-10-26 实测：东财 push2his 抓满 ~1500 只后整段限流（全节点返回空串），
    腾讯 web.ifzq 不受影响，代价是单次只给 640 根、且无成交额字段
    （成交额在 load 时用 volume×close 统一重算，两源口径对齐）。
    """
    seg1 = _tencent_seg(code, '2023-01-01', '2024-02-29', last_day)
    seg2 = _tencent_seg(code, '2024-03-01', '2026-12-31', last_day)
    merged = {}
    for row in seg1 + seg2:
        merged[row[0]] = row
    rows = []
    for day in sorted(merged):
        d, o, c, h, l, v = merged[day]
        if min(o, c, h, l) <= 0:
            continue
        vol = v * 100.0                 # 手 -> 股
        rows.append([d, o, c, h, l, vol, vol * c, 0.0])
    return rows if len(rows) >= 60 else None


def last_trade_day():
    """最近一个已收盘交易日：今天 15:05 前则取前一交易日。直接按 K 线末根对齐。"""
    today = datetime.date.today()
    _now = datetime.datetime.now()
    if _now.hour * 60 + _now.minute >= 15 * 60 + 5:
        return today.strftime('%Y-%m-%d')
    d = today - datetime.timedelta(days=1)
    for _ in range(10):
        if d.weekday() < 5:
            return d.strftime('%Y-%m-%d')
        d -= datetime.timedelta(days=1)
    return d.strftime('%Y-%m-%d')


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    last_day = last_trade_day()
    print('[fetch] last_day =', last_day)

    if os.path.exists(LIST_PATH) and '--refresh-list' not in sys.argv:
        lst = json.load(open(LIST_PATH, encoding='utf-8'))
        print('[fetch] 复用列表 %d 只' % len(lst))
    else:
        lst = fetch_list()
        json.dump(lst, open(LIST_PATH, 'w', encoding='utf-8'), ensure_ascii=False)
        print('[fetch] 列表 %d 只' % len(lst))

    # 需要剔除的：退市/PT/长期停牌（K 线抓不到自然被过滤），名字含 PT 的跳过
    lst = [r for r in lst if 'PT' not in r['name'].upper()]
    todo = [r for r in lst if not os.path.exists(os.path.join(OUT_DIR, r['code'] + '.json'))]
    todo.sort(key=lambda r: (0 if (r['code'].startswith('60') or r['code'].startswith('00')) else 1, r['code']))
    if '--all' in sys.argv:
        todo = lst
    print('[fetch] 待抓 %d 只' % len(todo))

    ok, miss = 0, []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(fetch_kline, r['code'], last_day): r for r in todo}
        for i, f in enumerate(as_completed(futs), 1):
            r = futs[f]
            try:
                rows = f.result()
            except Exception:
                rows = None
            if rows:
                tmp = os.path.join(OUT_DIR, r['code'] + '.json.tmp')
                with open(tmp, 'w', encoding='utf-8') as fp:
                    json.dump({'code': r['code'], 'name': r['name'],
                               'mcap': r.get('mcap'), 'k': rows}, fp,
                              ensure_ascii=False, separators=(',', ':'))
                os.replace(tmp, os.path.join(OUT_DIR, r['code'] + '.json'))
                ok += 1
            else:
                miss.append(r['code'])
            if i % 300 == 0:
                print('[fetch] %d/%d ok=%d miss=%d %.0fs' %
                      (i, len(todo), ok, len(miss), time.time() - t0))

    print('[fetch] 完成 ok=%d miss=%d 用时 %.1fs' % (ok, len(miss), time.time() - t0))
    if miss:
        print('[fetch] 缺失样例:', miss[:20])
        json.dump(miss, open(os.path.join(BASE, '.kline_hist_miss.json'), 'w'))


if __name__ == '__main__':
    main()

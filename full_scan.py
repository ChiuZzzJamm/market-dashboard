#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量扫（R100m，2026-09-28 新增）：沪深主板 60/00 全市场扫描，产出候选集供
harmonic_detect.py / accumulation_score.py 复用（用户需求：全量扫安排在交易日 16 点，
之后谐波和主力吸筹都从全量扫里筛选标的）。

流水线（16:00 自动化先跑本脚本，再跑 harmonic/accumulation/power）：
  1. 新浪 Market_Center.getHQNodeData 分页拉全 A 快照（约 5600 只，56 页）；
  2. 预筛（快照级，零 K 线成本）：
     - 仅沪深主板 60/00（项目标的池铁律，排除 688/300/301 等）；
     - 剔除 ST/*ST/退/N 开头（新股）；
     - 现价 >= 2 元；当日成交额 >= 2 亿（F6 流动性硬门禁同口径）；
     - 当日涨幅 ∈ [-6%, +9.7%]（涨停票归断板池管、跌停票无形态意义）；
  3. 按成交额降序截前 FULLSCAN_MAX（默认 500）只；
  4. 用 kline_cache.get_klines_bulk 预抓日 K 进 .kline_cache（下游脚本零网络等待），
     剔除日 K 不足 60 根的标的；
  5. 写 data.js 的 fullScan 字段：{date(运行日), tradeDate(快照交易日), universe,
     scanned, candidates, names, note}。

失败处理（R91m/R91n 风）：任一环节整体失败 → 不写 fullScan、exit 1；
下游 harmonic/accumulation 检测到 fullScan 缺失或非今日时自动退回「池内扫描域」，不清场。

用法：FULLSCAN_MAX=500 KLINE_DEADLINE=900 python3 full_scan.py [--dry]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, date

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import kline_cache as K  # noqa: E402

NODE = "/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node"
SINA_LIST = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
             "/Market_Center.getHQNodeData?page={page}&num=100&node=hs_a"
             "&sort=amount&asc=0")
try:
    FULLSCAN_MAX = int(os.environ.get('FULLSCAN_MAX', '500'))
except ValueError:
    FULLSCAN_MAX = 500

MIN_AMOUNT = 2e8      # 成交额 2 亿（元），与吸筹 F6 硬门禁同口径
PCT_LO, PCT_HI = -6.0, 9.7
MIN_BARS = 60


def _curl_json(url, timeout=15):
    cmd = ['curl', '-s', '--max-time', str(timeout),
           '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
           '-H', 'Referer: https://finance.sina.com.cn', url]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
    except Exception:
        return None
    if not p or p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        return json.loads(p.stdout.decode('utf-8', errors='replace'))
    except Exception:
        return None


def fetch_all_sina():
    """分页拉全 A 快照 → [{code,name,trade,changepercent,amount}]。任一页失败抛异常。"""
    first = _curl_json(SINA_LIST.format(page=1))
    if not first:
        raise RuntimeError('新浪快照首页拉取失败')
    rows = list(first)
    page = 2
    failed_pages = []
    while len(rows) < 6000 and page <= 80:  # 全 A 约 5600 只，num=100 → ≤57 页
        batch = None
        for attempt in range(3):  # 单页重试 3 次（防瞬时限流）
            batch = _curl_json(SINA_LIST.format(page=page))
            if batch is not None:
                break
            time.sleep(0.8 * (attempt + 1))
        if batch is None:
            failed_pages.append(page)  # 容错：跳过该页（约损失 100 只），失败页过多则中止
            if len(failed_pages) > 6:
                raise RuntimeError(f'新浪快照失败页过多：{failed_pages}')
            page += 1
            continue
        if not batch or len(batch) < 100:
            if batch:
                rows.extend(batch)
            break  # 末页（不足 100 条）→ 停止
        rows.extend(batch)
        page += 1
        time.sleep(0.25)
    if failed_pages:
        print(f'[FULLSCAN][WARN] 跳过失败页：{failed_pages}（不影响整体扫描）')
    return rows


def prefilter(rows):
    """快照级预筛 → 按成交额降序截前 FULLSCAN_MAX 只。"""
    out = []
    for r in rows:
        code = str(r.get('code') or '')
        name = str(r.get('name') or '').strip()
        if not (code.startswith('60') or code.startswith('00')):
            continue  # 仅沪深主板
        if 'ST' in name or '退' in name or name.startswith('N'):
            continue  # 剔除 ST / 退市整理 / 上市首日新股
        try:
            price = float(r.get('trade') or 0)
            pct = float(r.get('changepercent') or 0)
            amt = float(r.get('amount') or 0)
        except (TypeError, ValueError):
            continue
        if price < 2 or amt < MIN_AMOUNT:
            continue
        if pct < PCT_LO or pct > PCT_HI:
            continue  # 涨停/跌停附近不进技术面扫描（断板池另管）
        out.append({'code': code, 'name': name, 'pct': pct, 'amount': amt})
    out.sort(key=lambda x: -x['amount'])
    return out[:FULLSCAN_MAX]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true', help='只打印不写回')
    args = ap.parse_args()

    print(f'[FULLSCAN] 开始：拉取全 A 快照（预筛上限 {FULLSCAN_MAX} 只）…')
    t0 = time.time()
    try:
        rows = fetch_all_sina()
    except Exception as e:
        print(f'[FULLSCAN][FAIL] 快照拉取失败：{e}；不写 fullScan（下游退回池内扫描域）')
        sys.exit(1)
    universe = sum(1 for r in rows
                   if str(r.get('code') or '').startswith(('60', '00')))
    cand = prefilter(rows)
    print(f'[FULLSCAN] 全 A {len(rows)} 只 / 主板60-00 {universe} 只 → 预筛通过 {len(cand)} 只'
          f'（耗时 {time.time() - t0:.0f}s）')
    if not cand:
        print('[FULLSCAN][FAIL] 预筛后候选为空，不写 fullScan')
        sys.exit(1)

    # K 线预抓进缓存（下游 harmonic/accumulation 直接读缓存，零网络等待）
    codes = [c['code'] for c in cand]
    print(f'[FULLSCAN] 预抓 {len(codes)} 只日K进 .kline_cache（预算 {K.DEADLINE_S:.0f}s）…')
    bars_map = K.get_klines_bulk(codes, days=320, workers=8)
    n_bars = sum(1 for v in bars_map.values() if v and len(v) >= MIN_BARS)
    print(f'[FULLSCAN] K线可用 {n_bars}/{len(codes)} 只（≥{MIN_BARS} 根）')
    if n_bars < len(codes) * 0.6:
        print('[FULLSCAN][FAIL] K线抓取成功率 <60%，疑似数据源故障，不写 fullScan（R91n）')
        sys.exit(1)
    cand = [c for c in cand if bars_map.get(c['code']) and len(bars_map[c['code']]) >= MIN_BARS]

    trade_dates = [v[-1]['day'] for v in bars_map.values() if v]
    trade_date = max(trade_dates) if trade_dates else ''

    # 读改写 data.js（只动 fullScan 字段）
    data_path = os.path.join(BASE, 'data.js')
    with open(data_path, encoding='utf-8') as f:
        s = f.read()
    D = json.loads(s[s.index('{'):s.rindex('}') + 1])
    names = {c['code']: c['name'] for c in cand}
    field = {
        'updatedAt': f"{datetime.now().strftime('%Y-%m-%d %H:%M')}（全量扫 {date.today().strftime('%Y-%m-%d')} 运行，快照交易日 {trade_date}）",
        'date': date.today().strftime('%Y-%m-%d'),
        'tradeDate': trade_date,
        'universe': universe,
        'scanned': len(cand),
        'candidates': [c['code'] for c in cand],
        'names': names,
        'note': f'沪深主板全市场快照预筛（成交额≥2亿/涨幅{PCT_LO}~{PCT_HI}%/非ST）后按成交额降序取前 {FULLSCAN_MAX} 只，K线≥{MIN_BARS}根；供谐波/吸筹筛选',
    }
    D['fullScan'] = field
    print(f"[FULLSCAN][DRY] 候选 {len(cand)} 只，快照交易日 {trade_date}，未写回"
          if args.dry else
          f"[FULLSCAN] 写回 data.js：候选 {len(cand)} 只（快照交易日 {trade_date}）")
    if args.dry:
        print(json.dumps(cand[:15], ensure_ascii=False))
        return
    out = 'window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False,
                                                  separators=(',', ':')) + ';\n'
    with open(data_path, 'w', encoding='utf-8') as f:
        f.write(out)
    subprocess.run([NODE, '--check', data_path], check=True, timeout=30)
    print('[FULLSCAN] data.js 写回完成，node --check 通过')


if __name__ == '__main__':
    main()

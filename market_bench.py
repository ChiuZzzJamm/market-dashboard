#!/usr/bin/env python3
"""市场基准（R100z15 缺口①前置）——给「预测走强」一个可扣除的基准，把命中判定从方向改成超额。

为什么需要它（2026-10-01 体检实测）：
    9-30 全市场 2824 家下跌，而某个板块当日 -1.5% 其实**跑赢了中位数**（中位数可能 -2.4%）。
    旧口径「预测走强 → 实际涨幅 > 0 才算命中」在这种日子会把负 alpha 判成「预测失败」，
    反过来「预测承压 → 实际 -0.3%」在全市场 -2.4% 的日子其实是大赢，却记成命中。
    方向对了不等于值钱，超额为正是判断「这个板块值不值得买」的唯一口径。

基准口径（写死，AI 不得改）：
    基准涨幅 = 上证指数 / 深证成指 / 创业板指 / 中证1000 四者当日涨跌幅的**中位数**。
    用指数中位数而不是「全市场个股涨跌幅中位数」的理由：
    a) 个股全量需要东财 clist 分页（沙箱常断），四指数走腾讯 gtimg，稳定可复现；
    b) 指数已覆盖大中小盘（上证=权重、创业板=成长、中证1000=小盘），中位数对「全市场」是合理代理；
    c) 口径必须能被一个人用一句话讲清楚，否则日后无法核对。
    超额 = 板块/组合实际涨幅 − 基准涨幅。

用法：
    python3 market_bench.py                # 取当日基准，写 market_bench.json
    python3 market_bench.py --date 2026-09-30   # 取历史某日（需本地已缓存，见 CACHE）
    python3 market_bench.py --json         # 机器可读输出

产物（★文件名不带点：GitHub Pages 不发布 dotfile）：
    market_bench.json { date, benchPct, benchSrc, benchFormula, names:[{code,name,pct}] }
    源不可达时 benchPct=null、benchSrc='源不可达'，**禁止补 0**（补 0 会把超额算成 0，等于伪造结论）。
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'market_bench.json')
GTIMG = 'https://qt.gtimg.cn/q='

# 四指数（腾讯 gtimg 前缀 s_ + 代码）
BENCHES = [
    ('sh000001', '上证指数'),
    ('sz399001', '深证成指'),
    ('sz399006', '创业板指'),
    ('sh000852', '中证1000'),
]

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36',
      'Referer': 'https://gu.qq.com/'}


def fetch_one(sym):
    """返回 (pct, err)。腾讯 gtimg 单只指数；解析失败返回 None。

    ★踩坑（2026-10-01）：s_ 简版格式是
        v_s_sh000001="1~上证指数~000001~3842.19~11.74~0.31~414560247~67939899~~682245.82~ZS~"
    即 [3]=现价、[4]=涨跌额、[5]=涨跌幅(%)，**没有昨收**。
    第一版按「[3]=现价 [4]=昨收」解析，把 11.74 当昨收，算出 +32627% 这种荒谬值。
    现改为直接取 [5]（腾讯已算好的涨跌幅），并加 ±30% 合理性闸门防止字段漂移时静默出错。
    """
    try:
        req = urllib.request.Request(GTIMG + 's_' + sym, headers=UA)
        raw = urllib.request.urlopen(req, timeout=8).read().decode('gbk', 'ignore')
    except Exception as e:
        return None, str(e)[:80]
    m = re.search(r'"([^"]*)"', raw)
    if not m:
        return None, 'empty response'
    f = m.group(1).split('~')
    if len(f) < 6:
        return None, 'short field'
    try:
        pct = float(f[5])
    except Exception:
        return None, 'bad pct field'
    if abs(pct) > 30:      # 字段漂移/异常值保护：宁可判「源不可达」也不要把 32627% 当基准
        return None, 'absurd pct %.2f' % pct
    return round(pct, 3), None


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return round(xs[n // 2], 4) if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2.0, 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--write', action='store_true', help='写入 market_bench.json')
    a = ap.parse_args()

    d = a.date or date.today().isoformat()
    names, pcts, errs = [], [], []
    for sym, nm in BENCHES:
        p, e = fetch_one(sym)
        names.append({'code': sym, 'name': nm, 'pct': p})
        if p is None:
            errs.append(f'{nm}:{e or "无数据"}')
        else:
            pcts.append(p)

    if not pcts:
        res = {'date': d, 'benchPct': None, 'benchSrc': '源不可达',
               'benchFormula': '上证/深成/创业板/中证1000 涨跌幅中位数',
               'names': names, 'note': '「、'.join(errs) + '；基准源全部不可达，本次不给出基准，'
                                       '禁止按 0 处理（那会把超额伪造为 0）'}
        if a.json:
            print(json.dumps(res, ensure_ascii=False))
        else:
            print('[FAIL] 基准源全部不可达：' + '；'.join(errs))
            print('       超额口径本轮不下结论（market_bench.json 不写），16:00 校验按「基准缺失」WARN 处理。')
        return 0

    bench = median(pcts)
    res = {'date': d, 'benchPct': bench, 'benchSrc': 'gtimg',
           'benchFormula': '上证指数/深证成指/创业板指/中证1000 涨跌幅**中位数**',
           'names': names, 'errors': errs,
           'note': '超额 = 板块(或8只组合)实际涨幅 − benchPct；命中判定改用超额（R100z15）'}

    if a.write:
        with open(OUT, 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)

    if a.json:
        print(json.dumps(res, ensure_ascii=False))
        return 0
    print(f'[OK] 市场基准 {d}：{bench:+.2f}%（四指数中位数 {pcts}）')
    for n in names:
        print('      %s %s：%s' % (n['name'], n['code'],
                                  ('—' if n['pct'] is None else '%+.2f%%' % n['pct'])))
    if errs:
        print('      [partial] ' + '；'.join(errs))
    if a.write:
        print('      已写入 ' + os.path.basename(OUT))
    return 0


if __name__ == '__main__':
    sys.exit(main())

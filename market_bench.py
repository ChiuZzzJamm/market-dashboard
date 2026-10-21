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


def load_sector_bench():
    """R100z158：从 data.js 的 allBoards 取**全行业涨跌幅中位数**——板块预测的同源基准。

    为什么要加这个（2026-10-20 实测）：此前板块方向判定用的是「四指数中位数」，但被判定
    的对象是**行业板块**。两者量纲不一致，实测差异很大：
        10-20  行业中位 +0.94%  四指数中位 -0.14%（差 1.08pp）
        10-15  行业中位 -2.94%  四指数中位 -1.59%（差 1.35pp）
        10-08  行业中位 -0.70%  四指数中位 -2.07%（差 1.38pp）
    普涨日行业涨幅天然高于综合指数（小盘/题材权重更大），拿指数当基准会让**每一个板块都
    轻松「跑赢」**，超额虚高；普跌日反过来让超额虚低。同源比较才谈得上 alpha。

    只在 data.js 里 allBoards 有 ≥10 个有效 pct 时才返回，否则 (None, 0) —— 不猜、不补 0。
    """
    try:
        with open(os.path.join(ROOT, 'data.js'), encoding='utf-8') as f:
            s = f.read()
        m = re.search(r"window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$", s)
        if not m:
            return None, 0
        d = json.loads(m.group(1))
        ab = [x['pct'] for x in ((d.get('ashare') or {}).get('allBoards') or [])
              if isinstance(x, dict) and isinstance(x.get('pct'), (int, float))]
        if len(ab) < 10:
            return None, len(ab)
        return median(ab), len(ab)
    except Exception:
        return None, 0


def _datajs_boards(d):
    """从已解析的 data.js 字典里取 (tradeDate, 行业涨跌幅中位数, 行业数)。"""
    a = (d.get('ashare') or {})
    ab = [x['pct'] for x in (a.get('allBoards') or [])
          if isinstance(x, dict) and isinstance(x.get('pct'), (int, float))]
    if len(ab) < 10:
        return a.get('tradeDate'), None, len(ab)
    return a.get('tradeDate'), median(ab), len(ab)


def _parse_datajs(raw):
    m = re.search(r"window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$", raw)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def backfill_history(limit=0):
    """R100z158：从 git 历史 data.js 回填**历史行业基准**，写进 market_bench.json 的 history 层。

    为什么必须回填而不是「当天跑一次就算了」（2026-10-20 实测）：
      台账里 8 个预测日，只有最近一天能拿到实时基准。历史日子要么没有基准（超额留空），
      要么（更糟）被 code 拿「当天的尺子」去量——回填前 backfill 路径就是这么错的。
      行业基准 benchSecPct 是超额口径的尺子，尺子缺失 → 整段回测结论不可信。

    口径：同一 tradeDate 取**最新一个**提交（越晚的提交越是修完数据后的定稿），只补不改。
    09-30 无提交（git 历史重建时丢了），永远补不出来——那天的超额只能留空，不猜。
    """
    import subprocess
    try:
        hashes = subprocess.run(['git', '-C', ROOT, 'log', '--format=%H', '--', 'data.js'],
                                capture_output=True, text=True, timeout=120).stdout.split()
    except Exception as e:
        print('[FAIL] git 历史读取失败：' + str(e)[:120])
        return 1
    if limit:
        hashes = hashes[:limit]
    got, seen = {}, set()
    for h in hashes:
        try:
            raw = subprocess.run(['git', '-C', ROOT, 'show', f'{h}:data.js'],
                                 capture_output=True, text=True, timeout=60).stdout
        except Exception:
            continue
        d = _parse_datajs(raw or '')
        if not d:
            continue
        dt, med, n = _datajs_boards(d)
        if not dt or med is None or dt in seen:
            continue
        seen.add(dt)
        got[dt] = {'date': dt, 'benchPct': None, 'benchSectorPct': med, 'sectorN': n,
                   'src': 'git-backfill:%s' % h[:7]}
    if not got:
        print('[WARN] 历史 data.js 里没找到可用的 allBoards，未写入')
        return 0
    cur = {}
    if os.path.exists(OUT):
        try:
            cur = json.load(open(OUT, encoding='utf-8'))
        except Exception:
            cur = {}
    hist = cur.get('history') or {}
    hist.update(got)
    cur['history'] = {k: hist[k] for k in sorted(hist)}
    # R100z165（L10 写盘原子化）：先 .tmp 再 os.replace
    _tmp = OUT + '.tmp'
    with open(_tmp, 'w', encoding='utf-8') as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    os.replace(_tmp, OUT)          # ← 少了这一行，回填永远不落地（R100z170 修）
    print('[OK] 历史行业基准已回填 %d 天 → market_bench.json[history]' % len(got))
    for k in sorted(got):
        print('      %s  行业中位 %+.2f%%  (%d 个行业)' % (k, got[k]['benchSectorPct'], got[k]['sectorN']))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--write', action='store_true', help='写入 market_bench.json')
    ap.add_argument('--backfill-bench', action='store_true',
                    help='R100z158：从 git 历史 data.js 回填历史行业基准到 market_bench.json[history]，只补不改')
    ap.add_argument('--limit', type=int, default=0, help='回填时最多扫多少个 data.js 提交（0=全部）')
    a = ap.parse_args()

    if a.backfill_bench:
        return backfill_history(a.limit)

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
            print('       超额口径本轮不下结论（market_bench.json 不写），17:00 校验按「基准缺失」WARN 处理。')
        return 0

    bench = median(pcts)
    sec_bench, sec_n = load_sector_bench()
    res = {'date': d, 'benchPct': bench, 'benchSrc': 'gtimg',
           'benchFormula': '上证指数/深证成指/创业板指/中证1000 涨跌幅**中位数**',
           'benchSectorPct': sec_bench, 'sectorN': sec_n,
           'benchSectorFormula': '同花顺一级行业（allBoards）当日涨跌幅**中位数**',
           'names': names, 'errors': errs,
           'note': ('基准双轨：benchPct=四指数中位数（描述大盘）；benchSectorPct=全行业中位数。'
                    '★板块方向与组合的超额判定以 benchSectorPct 为准（同源比较），'
                    'benchPct 仅作大盘参照；benchSectorPct 缺失时退回 benchPct 并在台账标注。')}

    if a.write:
        # R100z158：写盘时**保留 history 层**——否则每跑一次实时基准就把回填的历史尺子冲掉，
        # 回测又要从头补。（踩过同型：整文件重写把别的节点带没了）
        hist = {}
        if os.path.exists(OUT):
            try:
                hist = (json.load(open(OUT, encoding='utf-8')) or {}).get('history') or {}
            except Exception:
                hist = {}
        if hist:
            res['history'] = {k: hist[k] for k in sorted(hist)}
        # R100z165（L10 写盘原子化）：先 .tmp 再 os.replace
        # ★R100z170 修：原代码这两行缩进掉到 if 之外（不带 --write 也会写盘），
        #   且**漏了 os.replace** —— 每次都只写 market_bench.json.tmp，正式文件永不更新。
        #   后果：10-21 起市场基准/行业基准全部冻结在 10-20，组合净值曲线当天行业基准断线。
        _tmp = OUT + '.tmp'
        with open(_tmp, 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        os.replace(_tmp, OUT)

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

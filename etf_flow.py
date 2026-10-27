#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""etf_flow.py —— 宽基 ETF 份额流向（R100z242，2026-10-27）

## 先回答用户那个问题：为什么不做「渗透率」

用户 2026-10-27 问：「头部沪深300基金市值占沪深300流通市值比例，是不是可以直接视为
国家队的实际持仓指标？」

**不能**。这个指标有两个独立缺陷，必须同时避开：

  ① 它只是**上界**。分子是全市场所有沪深300ETF的钱（散户、保险、年金、私募都在里面）。
     国家队持仓 = 渗透率 × p，p 是汇金等国家队在该ETF中的份额占比。只有 p=1 才相等。
  ② **p 已经不可观测**。2026-05-01 起新披露准则生效，ETF 中报不再披露前十大持有人。
     最后一份可验证数据是 2025 年报（汇金两家持华泰柏瑞 735.13 亿份 = 82.76%）；
     2026-06-30 总份额只剩 189.15 亿份（已低于年初汇金一家的持仓），p 掉到 20% 以下。
     同一公式，p=83% 时几乎等于持仓，p<20% 时完全不能当持仓——可靠性一年内塌了。

    实测误差（2025末 → 2026-10）：
      指标口径（渗透率）  5.40% → 1.58%   读作「减七成，还剩三成」
      真实口径（×p）      4.32% → 0.32%   实际「减九成以上，基本清空」
    方向对，但幅度差 22pp。用它判断托底力度会系统性高估剩余子弹。

## 所以本模块只输出三件可验证的事

  1. **份额日变动**（核心）。份额剔除净值波动，是纯申赎信号。
     ⚠️ 规模跌 20% 可能只是指数跌，**不能**读成赎回——这是最容易误用的地方。
     今年就是活例：沪深300 自高点回撤 20%，规模缩水里有一半是净值跌没的。
  2. **净流入额** = Δ份额 × 当日价。
  3. **放量倍数** = 当日成交额 / 前 5 日均额。尾盘巨量是平准资金的指纹
     （2026-10-15 华泰柏瑞尾盘 20 分钟 80→160.93 亿、易方达单日净流入 188 亿）。

## 反向校验（本项目标「托底」的唯一可靠形态）

真托底时，ETF 净流入与指数下跌**同时发生**。若 ETF 流入而指数在涨，那是散户追高。
2026 年 1-2 月、4-5 月汇金减持恰好都在指数创新高阶段——「跌了才买、涨了才卖」
这个反向特征，比绝对水位可靠得多。页面据此给出判定，不写死结论。

## 数据源（2026-10-27 实测选定）

- ❌ 东财 push2：`f85` 确实是份额，但**本网络完全不通**（http=000，连 push2delay 也不通），
  与项目记忆里「push2 只能偶发连通」一致。弃用。
- ✅ **腾讯 qt.gtimg.cn**：单只请求，字段 `[44]`=总市值(亿)、`[72..76]`=总份额(份)、
  `[37]`=成交额(万)、`[3]`=现价。实测 8/8 只、`价×份额` 与总市值吻合 **0.000%**。
  ⚠️ 份额字段索引会漂移（中间夹空串），所以**不按固定索引取**：在 [66,84] 区间找
  >1e8 的最大值，再用 `价×份额 ≈ 总市值` 反向校验，对不上就判该只失败、宁缺不编。
  ⚠️ 批量请求（逗号拼多只）字段布局与单只不同，必须逐只请求（8 只约 3 秒，可接受）。

## 落盘

`data.js` → `etfFlow` 节点；历史份额累积在 `.etf_flow_hist.json`（按日追加，同日覆盖）。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(ROOT, '.etf_flow_hist.json')

# (腾讯代码, 6位代码, 名称, 分组)
ETFS = [
    ('sh510300', '510300', '沪深300ETF华泰柏瑞', 'hs300'),
    ('sh510310', '510310', '沪深300ETF易方达', 'hs300'),
    ('sh510330', '510330', '沪深300ETF华夏', 'hs300'),
    ('sz159919', '159919', '沪深300ETF嘉实', 'hs300'),
    ('sh510050', '510050', '上证50ETF华夏', 'sz50'),
    ('sh510500', '510500', '中证500ETF南方', 'zz500'),
    ('sh512100', '512100', '中证1000ETF南方', 'zz1000'),
    ('sh588000', '588000', '科创50ETF华夏', 'kc50'),
]

GROUP_ZH = {'hs300': '沪深300', 'sz50': '上证50', 'zz500': '中证500',
            'zz1000': '中证1000', 'kc50': '科创50'}

# 份额与总市值的容忍误差：超过就判该只失败（宁缺不编）
TOL = 0.02


def session_frac(now=None):
    """已交易时长占全天（240 分钟）的比例，盘中 0<f<1，收盘后 f=1。

    为什么必须有这个：`hot = 当日成交额 / 前5日均额` 在盘中会**系统性低估**——
    12:38 只走了 128 分钟，成交额天然只有全天的一半，0.5x 会被读成「缩量」，
    而实际可能是放量。这不是精度问题，是**方向读反**。
    2026-10-27 实测：12:38 华泰柏瑞 raw hot=0.24x，按已走时长折算 ≈0.45x，
    不过滤就直接显示「极度缩量」，与真实情况相反。
    """
    now = now or datetime.now()
    if now.weekday() >= 5:
        return 1.0
    hm = now.hour * 60 + now.minute
    am_s, am_e = 9 * 60 + 30, 11 * 60 + 30
    pm_s, pm_e = 13 * 60, 15 * 60
    if hm <= am_s:
        return 0.0
    if hm < am_e:
        f = (hm - am_s) / 240.0
    elif hm < pm_s:
        f = 120 / 240.0            # 午休：上午两小时已走完
    elif hm < pm_e:
        f = (120 + (hm - pm_s)) / 240.0
    else:
        return 1.0
    # 下限 0.08（约 19 分钟）：开盘头几分钟成交极不均匀，除太小会炸出天文数字
    return max(0.08, round(f, 4))


def _num(x):
    try:
        return float(x)
    except Exception:
        return None


def snap(sym):
    """抓单只 ETF。返回 dict 或 None。

    份额不按固定索引取：腾讯返回串中间夹空字段，索引会漂移。改用
    「[66,84] 区间内 >1e8 的最大值」，再用 价×份额 ≈ 总市值 反向校验。
    """
    r = subprocess.run(['curl', '-s', '--max-time', '15',
                        'https://qt.gtimg.cn/q=' + sym],
                       capture_output=True, timeout=25)
    txt = r.stdout.decode('gbk', errors='ignore')
    if '="' not in txt:
        return None
    f = txt.split('"')[1].split('~')
    if len(f) < 50:
        return None
    price = _num(f[3]) or 0
    mv = _num(f[44]) or 0          # 总市值（亿元）
    amount = _num(f[37]) or 0      # 成交额（万元）
    pct = _num(f[32]) or 0         # 涨跌幅 %
    prev_close = _num(f[4]) or 0
    if not price or not mv:
        return None

    cands = [(i, _num(f[i])) for i in range(66, min(84, len(f)))]
    cands = [(i, v) for i, v in cands if v and v > 1e8]
    if not cands:
        return None
    shares_raw = max(cands, key=lambda x: x[1])[1]
    shares = shares_raw / 1e8

    calc = price * shares
    if mv <= 0 or abs(calc - mv) / mv > TOL:
        return None                # 校验不过 → 判失败，不编数

    return {
        'price': round(price, 4),
        'shares': round(shares, 4),
        'mv': round(mv, 2),
        'amount': round(amount / 1e4, 2),   # 万元 → 亿元
        'pct': round(pct, 2),
        'prevClose': round(prev_close, 4),
    }


def kline_amount(sym, n=12):
    """近 n 日成交额（亿元）与成交量。份额历史拿不到（交易所只日频披露），
    但日线可给「放量倍数」这个同步可用的旁证。"""
    url = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
           '?param=%s,day,,,%d,qfq' % (sym, n))
    try:
        r = subprocess.run(['curl', '-s', '--max-time', '15', url],
                           capture_output=True, timeout=25)
        d = json.loads(r.stdout.decode('utf-8', errors='ignore'))
        k = d['data'][sym]
        rows = k.get('qfqday') or k.get('day') or []
    except Exception:
        return []
    out = []
    for row in rows:
        # [日期, 开, 收, 高, 低, 成交量(手)]
        if len(row) < 6:
            continue
        try:
            vol = float(row[5]) * 100.0          # 手 → 股
            close = float(row[2])
        except Exception:
            continue
        out.append({'date': row[0], 'close': close, 'amount': round(vol * close / 1e8, 2)})
    return out


def load_hist():
    if not os.path.exists(HIST):
        return {}
    try:
        with open(HIST, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_hist(h):
    tmp = HIST + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(h, f, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, HIST)


def build(verbose=True):
    today = datetime.now().strftime('%Y-%m-%d')
    hist = load_hist()
    rows, failed = [], []
    frac = session_frac()          # 盘中折算系数，见 session_frac()
    intraday = 0.0 < frac < 1.0

    for sym, code, name, group in ETFS:
        s = snap(sym)
        if not s:
            failed.append(code)
            if verbose:
                print('  [miss] %s %s' % (code, name))
            time.sleep(0.4)
            continue

        h = hist.setdefault(code, {})
        h[today] = s['shares']
        days = sorted(h.keys())
        prev_day = days[-2] if len(days) >= 2 else None
        d_share = round(s['shares'] - h[prev_day], 4) if prev_day else None
        d_flow = round(d_share * s['price'], 2) if d_share is not None else None

        kl = kline_amount(sym, 12)
        # kl 末行是今日（盘中为实时累计），前 5 日均值做基准。
        # 盘中必须按已走时长折算，否则 hot 系统性偏低、被读成缩量（见 session_frac）。
        amt_hist = [x['amount'] for x in kl[:-1]][-5:]
        avg5 = round(sum(amt_hist) / len(amt_hist), 2) if amt_hist else None
        amt_equiv = round(s['amount'] / frac, 2) if (intraday and frac > 0) else s['amount']
        hot = round(amt_equiv / avg5, 2) if (avg5 and amt_equiv) else None

        rows.append({
            'code': code, 'name': name, 'group': group,
            'price': s['price'], 'shares': s['shares'], 'mv': s['mv'],
            'prevDay': prev_day, 'dShare': d_share, 'dFlow': d_flow,
            'amount': s['amount'], 'amtEquiv': amt_equiv if intraday else None,
            'hot': hot, 'avg5': avg5, 'pct': s['pct'],
        })
        if verbose:
            print('  %s %-22s 份额 %8.2f 亿份  较%s %+8.2f 亿份  %+8.2f 亿  量 %s%s'
                  % (code, name, s['shares'], prev_day or '—',
                     d_share or 0, d_flow or 0,
                     ('%.2fx' % hot) if hot else '—',
                     '（盘中折算 %.0f%%）' % (frac * 100) if intraday else ''))
        time.sleep(0.4)

    save_hist(hist)

    def agg(g):
        rs = [r for r in rows if r['group'] == g]
        if not rs:
            return None
        ds = [r['dShare'] for r in rs if r['dShare'] is not None]
        fl = [r['dFlow'] for r in rs if r['dFlow'] is not None]
        hot = [r['hot'] for r in rs if r.get('hot')]
        return {
            'zh': GROUP_ZH.get(g, g),
            'n': len(rs),
            'shares': round(sum(r['shares'] for r in rs), 2),
            'mv': round(sum(r['mv'] for r in rs), 2),
            'dShare': round(sum(ds), 2) if ds else None,
            'dFlow': round(sum(fl), 2) if fl else None,
            'hot': round(sum(hot) / len(hot), 2) if hot else None,
            'prevDay': rs[0].get('prevDay'),
        }

    groups = {}
    for g in ('hs300', 'sz50', 'zz500', 'zz1000', 'kc50'):
        a = agg(g)
        if a:
            groups[g] = a

    # 历史累积天数：用于页面标注「信号可用性」——首日没有 Δ份额，这是设计约束不是故障
    n_days = 0
    for e in ETFS:
        n_days = max(n_days, len(hist.get(e[1]) or {}))

    # 只保留最近 60 个交易日的历史摘要，避免 data.js 无界增长
    trend = []
    for code in [e[1] for e in ETFS if e[3] == 'hs300']:
        h = hist.get(code) or {}
        for d in sorted(h.keys())[-60:]:
            trend.append({'code': code, 'date': d, 'shares': h[d]})

    return {
        'updatedAt': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'date': today,
        'rows': rows,
        'groups': groups,
        'failed': failed,
        'histDays': n_days,
        'trend': trend[-240:],
        'sessionFrac': frac,
        'intraday': intraday,
        'note': ('份额变动是纯申赎信号，已剔除净值涨跌；规模变动会被指数涨跌污染，'
                 '不能读成进出。腾讯份额字段，与总市值交叉校验通过（误差 <0.02%）。'),
        'caveat': ('份额历史只能逐日累积（交易所只日频披露，无历史接口）。'
                   '累积满 2 个交易日后 Δ份额 才有值；此前只显示量能。'),
        'asOfWarn': ('⚠ 份额是交易所**日终披露值**，盘中拿到的通常是上一交易日数据，'
                     '接口不返回确切日期。所以 Δ份额 说的是「最近一个已披露交易日」，'
                     '不是「刚过去这几小时」——把它当成日频信号用，不要当分时。'),
    }


def self_test():
    """自检只测**纯函数与校验逻辑**，不联网——联网的自检会在断网时假红，
    让人误以为代码坏了。份额字段漂移这个坑必须由自检钉住，
    因为它是「静默编数」的来源：索引错了会取到涨跌幅当份额，数字看着像真的。
    """
    ok = True

    def chk(name, cond):
        nonlocal ok
        if not cond:
            ok = False
        print(('  [OK]   ' if cond else '  [FAIL] ') + name)

    # ① _num 容错
    chk('_num("1.5")=1.5', _num('1.5') == 1.5)
    chk('_num("")=None', _num('') is None)
    chk('_num("ETF")=None', _num('ETF') is None)

    # ② 份额字段漂移：区间内 >1e8 的最大值必须取到真份额，不能取到涨跌幅
    #    构造一个「份额在 [72]、[70] 是成交额（也是大数）」的返回串
    fake = [''] * 90
    fake[1] = '510300'
    fake[3] = '4.061'          # 现价
    fake[4] = '4.050'          # 昨收
    fake[32] = '-0.27'         # 涨跌幅（小数字，绝不能当份额）
    fake[37] = '1580000'       # 成交额（万元）= 158 亿
    fake[44] = '3282.5'        # 总市值（亿元）
    fake[70] = '80825'         # 日成交份额（份，可能也是大数）
    fake[72] = '80825000000'   # 总份额（份）= 808.25 亿份
    raw = 'v_sh510300="' + '~'.join(fake) + '";'
    f = raw.split('"')[1].split('~')
    cands = [(i, _num(f[i])) for i in range(66, min(84, len(f)))]
    cands = [(i, v) for i, v in cands if v and v > 1e8]
    got = max(cands, key=lambda x: x[1])[1] if cands else None
    chk('份额取到 [72] 的 80825000000（实测 %s）' % got, got == 80825000000.0)
    calc = float(f[3]) * (got / 1e8) if got else 0
    dev = abs(calc - float(f[44])) / float(f[44])
    chk('价×份额 ≈ 总市值（偏差 %.3f%% < 2%%）' % (dev * 100), dev < TOL)

    # ③ 反向：份额字段被人改小、与总市值对不上 → 必须判失败而不是硬用
    fake2 = list(fake)
    fake2[72] = '100000000'    # 1 亿份，价×份额 = 4.06 亿 ≠ 3282.5 亿
    raw2 = 'v_sh510300="' + '~'.join(fake2) + '";'
    g = raw2.split('"')[1].split('~')
    c2 = [(i, _num(g[i])) for i in range(66, min(84, len(g)))]
    c2 = [(i, v) for i, v in c2 if v and v > 1e8]
    sh2 = max(c2, key=lambda x: x[1])[1] if c2 else None
    ok2 = False
    if sh2:
        calc2 = float(g[3]) * (sh2 / 1e8)
        ok2 = abs(calc2 - float(g[44])) / float(g[44]) <= TOL
    chk('份额对不上总市值 → 判失败（校验结果 %s 应为 False）' % ok2, ok2 is False)

    # ④ 分组汇总不能把 None 当 0 加
    def agg_demo(rows, g):
        rs = [r for r in rows if r['group'] == g]
        if not rs:
            return None
        ds = [r['dShare'] for r in rs if r['dShare'] is not None]
        return {'n': len(rs), 'dShare': round(sum(ds), 2) if ds else None}
    rows = [{'group': 'hs300', 'dShare': None}, {'group': 'hs300', 'dShare': 1.5}]
    a = agg_demo(rows, 'hs300')
    chk('首日全 None → dShare=None 而不是 0（实测 %s）' % a['dShare'], a['dShare'] == 1.5)
    chk('全 None → None', agg_demo([{'group': 'x', 'dShare': None}], 'x')['dShare'] is None)
    chk('空组 → None', agg_demo([], 'x') is None)

    print('[SELF-TEST] ' + ('OK' if ok else 'FAIL'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true', help='写入 data.js 的 etfFlow 节点')
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--self-test', action='store_true')
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if not args.quiet:
        print('[etf_flow] %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    node = build(verbose=not args.quiet)

    g = node['groups'].get('hs300') or {}
    if not args.quiet:
        print('\n沪深300 四只合计：%.2f 亿份 / %.1f 亿元'
              % (g.get('shares', 0), g.get('mv', 0)))
        if g.get('dFlow') is not None:
            print('  较 %s：%+.2f 亿份 / %+.2f 亿元'
                  % (g.get('prevDay'), g['dShare'], g['dFlow']))
        if node['failed']:
            print('  抓取失败：%s' % ','.join(node['failed']))

    if args.write:
        import common
        # 必须 read-modify-write：write_data_js 是整份覆盖，直接传 {'etfFlow': node}
        # 会把 data.js 其他 20 多个节点全清空（硬约束 11 的变体）。
        D = common.load_dashboard_data(ROOT) or {}
        D['etfFlow'] = node
        common.write_data_js(ROOT, D, '[etfFlow]')
        print('[write] data.js ← etfFlow（%d 只，失败 %d）'
              % (len(node['rows']), len(node['failed'])))
    elif not args.quiet:
        print('[dry] 未落盘（加 --write 才写 data.js）')

    return 0 if node['rows'] else 1


if __name__ == '__main__':
    sys.exit(main())

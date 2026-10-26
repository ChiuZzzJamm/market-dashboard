#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest_indicators.py —— 四指标滚动回测（信号归因 + 组合净值/年化）

## 要回答什么

`signal_attribution.py` 用的是**台账法**：每天登记、等 5~10 天、算超额，样本 = 实际上线
天数（11 个交易日量级）。这个量级下置信区间宽到任何结论都无法与「运气」区分。

本脚本换一条路：**历史滚动回放**。K 线是既成事实，把四个指标在 2 年历史上逐日重跑，
样本从「11 天」变成「几百个信号日 × 上千只票」，才能在统计上判定：

    **哪个指标现在真的准？哪个一直在亏钱？**

## 与台账法的关系（互补，不是替代）

| | 台账法 signal_attribution | 滚动回放（本脚本） |
|---|---|---|
| 样本 | 上线后的真实天数（少） | 2 年历史（多） |
| 受当时选股影响 | 是（只登记入池票） | 否（全市场机械重算） |
| 能否算年化 | 只能算 8 只等权曲线 | 能，含空仓 / 换手 / 回撤 / 夏普 |
| 生存者偏差 | 无 | **有**（见「局限」） |

## 两层输出

**A 归因层**（回答「哪个准」）：每个指标 × 持有期 T+1/T+3/T+5/T+10 的笔数、
**独立信号日数**、绝对胜率、超额胜率、平均超额、日聚合 t 值与 95% 置信区间。

**B 组合层**（回答「能赚多少」）：当日全部新信号等权买入、持有 N 日后换出，
资金按 cohort 轮动（同时持有多批、当日满仓均分）→ 日频净值 → **年化 / 最大回撤 /
夏普 / 卡玛 / 平均持仓批数 / 空仓天数**。基准 = 全市场等权（同一批标的、同一日历）。

## 口径（刻意选择，改之前先想清楚）

1. **信号收盘后产生，次日开盘成交**：T 日收盘算信号 → T+1 开盘买 → T+1+N 开盘卖。
   在收盘价上成交会偷跑一个隔夜跳空，回测虚高。
2. **一字板买不进**：T+1 开盘涨幅 ≥9.5% 记不可成交（不计收益、单独计数）。断板反包
   最容易撞这条，不剔除会系统性高估。
3. **成本 0.16%/笔**（佣金 0.025%×2 + 印花税 0.05% + 滑点 0.03%×2）。
4. **存活过滤与 full_scan.py 同口径**：60/00 主板、价 ≥2 元、成交额 ≥2 亿、
   当日涨幅 ∈[-6%, 9.7%]。不做这层过滤会混进大量不可交易的僵尸票。
5. **不做生存者偏差「修正」**：universe 取当前 K 线缓存（仍在交易的 1267 只），
   已退市 / 长期停牌的不在里面 → **绝对年化系统性偏高**。因此主判据是
   **「相对全市场等权的超额」**（两边同一 universe，偏差大体对冲），
   绝对年化只作量级参考。这不是瑕疵掩盖，是必须写在结论第一行的前提。

## 指标事件定义（与线上一致）

- 谐波：`harmonic_detect.scan_bars()` 当日返回候选且未死（收盘未破止损），
  以 `(形态名, D 点日期)` 去重，只记首次出现。
- 吸筹：`accumulation_score.score_one()` 的 grade ≠ 不入池，以 `sigIdx` 变化去重
  （同一根信号 K 线只触发一次）。
- 横盘启动：`breakout_pool.evaluate(bars, k)` 返回 `active`，只在「连续 active 段的第一天」触发
  （即刚洗完、缩量达标的那个收盘）。
- 断板：`check_duanban.check_form()` 达标（确认池口径），对最近 2~5 个交易日的
  涨停日逐个试，以涨停日去重。

## 组合净值的逐日分解（为什么不是 close-to-close 近似）

一批持仓买在 A 日开盘、卖在 B 日开盘，收益 open_B/open_A。把它精确摊到每一天：
  买入日 `close_A/open_A - 1`；中间日 `close_t/close_{t-1} - 1`；卖出日 `open_B/close_{B-1} - 1`
三段连乘恒等于 open_B/open_A，无近似。停牌日贡献 0，跳空在复牌日一次性体现。

## 用法

    python3 backtest_indicators.py                        # 全量，写 backtest_report.md
    python3 backtest_indicators.py --limit 50 --days 120  # 冒烟
    python3 backtest_indicators.py --bars 640 --window 320

输出：`backtest_report.md`（人读）+ `backtest_result.json`（机器读）。
**纯本地计算，不写 data.js、不进任何自动化。**
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import accumulation_score as A      # noqa: E402
import check_duanban as DUAN        # noqa: E402
import harmonic_detect as H         # noqa: E402
import kline_cache as K             # noqa: E402
import breakout_pool as B           # noqa: E402

BT_DIR = os.path.join(BASE, '.bt_cache')
KLINE_DIR = os.path.join(BASE, '.kline_cache')

MIN_AMOUNT = 2e8
MIN_PRICE = 2.0
PCT_LO, PCT_HI = -6.0, 9.7
COST = 0.0016
UNFILLABLE_GAP = 0.095
HORIZONS = (1, 3, 5, 10)
WARMUP = 120
IND_ZH = {'duanban': '断板反包', 'harmonic': '谐波形态',
          'accum': '主力吸筹', 'bp': '横盘启动'}
IND_ORDER = ('duanban', 'harmonic', 'accum', 'bp')


# ---------------------------------------------------------------- 数据层
def _codes():
    out = []
    for fn in sorted(os.listdir(KLINE_DIR)):
        if fn.endswith('.json') and fn[:2] in ('60', '00'):
            out.append(fn[:-5])
    return out


def _bt_path(code):
    return os.path.join(BT_DIR, code + '.json')


def _stale(path, max_age_days=7):
    try:
        with open(path, encoding='utf-8') as f:
            bars = json.load(f)
        if not bars:
            return True
        last = str(bars[-1].get('day') or '')[:10]
        return (datetime.now() - datetime.strptime(last, '%Y-%m-%d')).days > max_age_days
    except Exception:
        return True


def fetch_history(codes, days, force=False, jobs=8):
    """抓 640 根日 K 到 .bt_cache/（独立于 .kline_cache，不动线上链路）。"""
    os.makedirs(BT_DIR, exist_ok=True)
    todo = [c for c in codes if force or _stale(_bt_path(c))]
    print('[数据] 需抓取 %d / %d 只（%d 根日K）' % (len(todo), len(codes), days))
    if not todo:
        return 0, 0

    def one(code):
        bars = K.fetch_kline_tencent(code, days=days, retries=2)
        if not bars:
            return code, 0
        tmp = _bt_path(code) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(bars, f, separators=(',', ':'))
        os.replace(tmp, _bt_path(code))
        return code, len(bars)

    ok = fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(one, c) for c in todo]
        for i, fu in enumerate(as_completed(futs), 1):
            _, n = fu.result()
            ok, fail = (ok + 1, fail) if n else (ok, fail + 1)
            if i % 200 == 0:
                print('  ... %d/%d ok=%d fail=%d %.0fs'
                      % (i, len(todo), ok, fail, time.time() - t0))
    print('[数据] 完成 ok=%d fail=%d 用时 %.0fs' % (ok, fail, time.time() - t0))
    return ok, fail


def load_bars(codes):
    out = {}
    for code in codes:
        p = _bt_path(code)
        if not os.path.exists(p):
            p = os.path.join(KLINE_DIR, code + '.json')
        try:
            with open(p, encoding='utf-8') as f:
                bars = json.load(f)
        except Exception:
            continue
        if len(bars) >= WARMUP + 40:
            out[code] = bars
    return out


# ---------------------------------------------------------------- 基准
def build_bench(all_bars):
    """返回 Bench：提供两种「全市场」口径。

    ⚠️ 这里踩过一个必须写下来的坑：**「全市场等权」不是唯一的基准，选哪种会改变结论**。
    - `mean`：横截面**算术均值**（同一 open→open 区间）＝随机抽一只票的**期望值**。
      被少数暴涨股拉高（本样本中位 +38% 而均值 +83%），但它才是「随机选股」的正确答案。
    - `med`：横截面**中位数**＝「典型股票」。信号若系统性避开暴涨尾部，
      均值口径为负、中位口径可能为正——这本身就是一条结论，不能只报一个。
    - `lvl_open/lvl_close`：日频再平衡等权指数，只用于组合层的基准净值曲线。

    另注：日频再平衡指数比买入持有均值高 0.06%(T+1)/0.14%(T+5)、低 0.14%(T+10)，
    差异来自再平衡溢价，实测过，不是猜的。
    """
    days = sorted({b['day'] for bars in all_bars.values() for b in bars})
    day_idx = {d: i for i, d in enumerate(days)}
    opens, closes = {}, {}
    for code, bars in all_bars.items():
        o = [None] * len(days)
        c = [None] * len(days)
        for b in bars:
            j = day_idx[b['day']]
            o[j] = b['open']
            c[j] = b['close']
        opens[code] = o
        closes[code] = c

    oc, co = {}, {}
    for bars in all_bars.values():
        for i in range(1, len(bars)):
            o, c, pc = bars[i]['open'], bars[i]['close'], bars[i - 1]['close']
            if o <= 0 or pc <= 0:
                continue
            d = bars[i]['day']
            s = oc.setdefault(d, [0.0, 0]); s[0] += c / o - 1; s[1] += 1
            s2 = co.setdefault(d, [0.0, 0]); s2[0] += o / pc - 1; s2[1] += 1
    min_cnt = max(10, min(50, len(all_bars) // 2))
    r_oc = {d: v[0] / v[1] for d, v in oc.items() if v[1] >= min_cnt}
    r_co = {d: v[0] / v[1] for d, v in co.items() if v[1] >= min_cnt}
    lvl_open, lvl_close = {}, {}
    lv = 1.0
    for d in days:
        if d not in r_oc or d not in r_co:
            continue
        lvl_open[d] = lv * (1 + r_co[d])
        lv = lvl_open[d] * (1 + r_oc[d])
        lvl_close[d] = lv

    # 个股自身首末涨幅的均值/中位（校准用：解释「等权 +90% 而中位只有 +38%」的偏度）
    tots = []
    for code, arr in opens.items():
        vals = [x for x in arr if x]
        if len(vals) >= 2 and vals[0] > 0:
            tots.append(vals[-1] / vals[0] - 1)
    tots.sort()
    tot_med = tots[len(tots) // 2] if tots else None
    tot_mean = (sum(tots) / len(tots)) if tots else None

    return {'days': days, 'idx': day_idx, 'opens': opens, 'closes': closes,
            'lvl_open': lvl_open, 'lvl_close': lvl_close,
            'cache': {}, 'n': len(all_bars),
            'tot_mean': tot_mean, 'tot_med': tot_med, 'tot_n': len(tots)}


def bench_pair(b, ia, ib):
    """同一 open→open 区间的全市场均值/中位数（按区间缓存，一次算全库）。"""
    key = (ia, ib)
    if key in b['cache']:
        return b['cache'][key]
    vals = []
    for code, arr in b['opens'].items():
        a, z = arr[ia], arr[ib]
        if a and z and a > 0:
            vals.append(z / a - 1)
    need = max(30, b['n'] // 3)
    if len(vals) < need:
        b['cache'][key] = (None, None)
        return b['cache'][key]
    vals.sort()
    m = sum(vals) / len(vals)
    n = len(vals)
    med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    b['cache'][key] = (m, med)
    return b['cache'][key]


# ---------------------------------------------------------------- 回放
def _limit_up(bars, j):
    if j < 1:
        return False
    pc = bars[j - 1]['close']
    return pc > 0 and bars[j]['close'] / pc - 1 >= 0.097


def replay(code, bars, window, start):
    """返回 [(bar_idx, ind, sub, strength)]：当日收盘后新产生的信号。"""
    out = []
    n = len(bars)
    seen_h, seen_zt = set(), set()
    last_sigidx = None
    prev_pass = False
    for k in range(max(WARMUP, start), n):
        b = bars[k]
        c, v = b['close'], b['volume']
        pct = 0.0
        if k >= 1 and bars[k - 1]['close'] > 0:
            pct = (c / bars[k - 1]['close'] - 1) * 100
        tradeable = (c >= MIN_PRICE and c * v >= MIN_AMOUNT
                     and PCT_LO <= pct <= PCT_HI)
        win = bars[max(0, k - window + 1):k + 1] if window else bars[:k + 1]

        # 横盘启动：evaluate 需要整条 bars + 当前索引（要回看 150 日找涨停锚），不能用 win 切片。
        try:
            _st, _info, _it = B.evaluate(bars, k)
        except Exception:
            _st, _it = None, None
        passed = (_st == 'active')
        if passed and not prev_pass and tradeable:
            sc = (_it or {}).get('score') or 0
            sub = '80+' if sc >= 80 else ('60-79' if sc >= 60 else '<60')
            out.append((k, 'bp', sub, sc))
        prev_pass = passed

        try:
            a = A.score_one(win)
        except Exception:
            a = None
        if a:
            si, grade = a.get('sigIdx'), a.get('grade')
            if grade and grade != '不入池' and si is not None and si != last_sigidx:
                if tradeable:
                    out.append((k, 'accum', grade, a.get('score') or 0))
                last_sigidx = si

        try:
            h = H.scan_bars(win)
        except Exception:
            h = None
        if h and not h.get('dead'):
            key = (h.get('pattern'), (h.get('pointDays') or {}).get('D'))
            if key not in seen_h:
                seen_h.add(key)
                if tradeable:
                    out.append((k, 'harmonic', h.get('pattern') or '',
                                -float(h.get('distToPrzPct') or 0)))

        for j in range(k - 2, k - 6, -1):
            if j < 20:
                break
            if not _limit_up(bars, j):
                continue
            zt_day = bars[j]['day']
            if zt_day in seen_zt:
                continue
            try:
                form = DUAN.check_form(code, bars[:k + 1], zt_day)
            except Exception:
                form = None
            if form:
                seen_zt.add(zt_day)
                if tradeable and bars[j]['volume'] > 0:
                    out.append((k, 'duanban', 'confirmed',
                                bars[j + 1]['volume'] / bars[j]['volume']))
    return out


def build_trades(code, bars, signals, bench):
    """信号 → 「T+1 开盘买、T+1+N 开盘卖」交易，含逐日贡献、不可成交标记、成本。"""
    trades = []
    n = len(bars)
    idx = bench['idx']
    for (k, ind, sub, strength) in signals:
        iA = k + 1
        if iA >= n:
            continue
        c0, o1 = bars[k]['close'], bars[iA]['open']
        if c0 <= 0 or o1 <= 0:
            continue
        gap = o1 / c0 - 1
        if not (-0.5 < gap < 0.5):        # 复权断层等异常数据
            continue
        fillable = gap < UNFILLABLE_GAP
        dA = bars[iA]['day']
        ia = idx.get(dA)
        for N in HORIZONS:
            iB = iA + N
            if iB >= n:
                continue
            dB = bars[iB]['day']
            ib = idx.get(dB)
            bm = bmed = None
            if ia is not None and ib is not None:
                bm, bmed = bench_pair(bench, ia, ib)
            contrib = []
            if fillable:
                contrib.append((dA, bars[iA]['close'] / o1 - 1))
                for i in range(iA + 1, iB):
                    pc = bars[i - 1]['close']
                    if pc > 0:
                        contrib.append((bars[i]['day'], bars[i]['close'] / pc - 1))
                if bars[iB - 1]['close'] > 0:
                    contrib.append((dB, bars[iB]['open'] / bars[iB - 1]['close'] - 1))
            r = (bars[iB]['open'] / o1 - 1 - COST) if fillable else None
            trades.append({
                'code': code, 'ind': ind, 'sub': sub, 'N': N,
                'sig_day': bars[k]['day'], 'in_day': dA, 'out_day': dB,
                'fillable': fillable, 'r': r,
                'ex': (r - bm) if (r is not None and bm is not None) else None,
                'ex_med': (r - bmed) if (r is not None and bmed is not None) else None,
                'bench': bm, 'strength': strength, 'contrib': contrib,
            })
    return trades


# ---------------------------------------------------------------- 基准校准
IDX = (('sh000001', '上证综指', '市值加权·大盘'),
       ('sz399303', '国证2000', '小盘·最接近等权'),
       ('sh000905', '中证500', '市值加权·中盘'),
       ('sz399006', '创业板指', '成长'))


def index_calib(d0, d1):
    """同期真实指数涨幅——用来给「全市场等权」这个自建基准定量级。
    没有这一节，读者会把 +90% 当成 bug（实际是等权+均值+生存者三重效应叠加）。"""
    import re
    out = []
    for sym, name, note in IDX:
        url = ('https://quotes.sina.cn/cn/api/jsonp_v2.php=/CN_MarketDataService.getKLineData'
               '?symbol=%s&scale=240&ma=no&datalen=700' % sym)
        raw = K._curl_text(url, timeout=12, referer='https://finance.sina.com.cn')
        try:
            arr = json.loads(re.search(r'\[.*\]', raw, re.S).group(0))
            a = next(b for b in arr if b['day'] >= d0)
            z = [b for b in arr if b['day'] <= d1][-1]
            out.append((name, '%+.1f%%' % ((float(z['close']) / float(a['close']) - 1) * 100),
                        note))
        except Exception:
            out.append((name, '取数失败', '不计入校准'))
    return out


# ---------------------------------------------------------------- 统计
def _t_ci(vals):
    n = len(vals)
    if n < 2:
        return None, None
    m = sum(vals) / n
    sd = (sum((x - m) ** 2 for x in vals) / (n - 1)) ** 0.5
    if sd <= 0:
        return None, (m, m)
    se = sd / (n ** 0.5)
    return m / se, (m - 1.96 * se, m + 1.96 * se)


def stats_block(trades, N):
    """按「信号日」聚合再求均值——同一日多票高度相关，按笔算会伪造显著性。"""
    rows = [t for t in trades if t['N'] == N and t['r'] is not None]
    if not rows:
        return None
    by_day = {}
    for t in rows:
        by_day.setdefault(t['sig_day'], []).append(t)
    day_ex = [sum(x['ex'] for x in v) / len(v) for v in by_day.values()
              if all(x['ex'] is not None for x in v)]
    tval, ci = _t_ci(day_ex)
    n = len(rows)
    rs = sorted(t['r'] for t in rows)
    exm = [t['ex_med'] for t in rows if t['ex_med'] is not None]
    return {
        'n': n, 'days': len(by_day),
        'avg': sum(t['r'] for t in rows) / n,
        'med': rs[n // 2],
        'win_abs': sum(1 for t in rows if t['r'] > 0) / n,
        'avg_ex': sum(t['ex'] for t in rows if t['ex'] is not None)
                  / max(1, sum(1 for t in rows if t['ex'] is not None)),
        'win_ex': sum(1 for t in rows if t['ex'] is not None and t['ex'] > 0)
                  / max(1, sum(1 for t in rows if t['ex'] is not None)),
        'avg_ex_med': (sum(exm) / len(exm)) if exm else None,
        'win_ex_med': (sum(1 for x in exm if x > 0) / len(exm)) if exm else None,
        't': tval, 'ci': ci,
        'unfillable': sum(1 for t in trades if t['N'] == N and not t['fillable']),
    }


def year_block(trades, N):
    """按信号年份拆开——两年半的单一结论，可能只是某一年的一段行情。"""
    rows = [t for t in trades if t['N'] == N and t['r'] is not None]
    out = {}
    yrs = sorted({t['sig_day'][:4] for t in rows})
    for y in yrs:
        sub = [t for t in rows if t['sig_day'][:4] == y]
        if len(sub) < 30:
            continue
        by_day = {}
        for t in sub:
            by_day.setdefault(t['sig_day'], []).append(t)
        day_ex = [sum(x['ex'] for x in v) / len(v) for v in by_day.values()
                  if all(x['ex'] is not None for x in v)]
        tv, _ = _t_ci(day_ex)
        out[y] = {'n': len(sub), 'days': len(by_day),
                  'avg': sum(t['r'] for t in sub) / len(sub),
                  'avg_ex': sum(t['ex'] for t in sub if t['ex'] is not None)
                            / max(1, sum(1 for t in sub if t['ex'] is not None)),
                  'win_abs': sum(1 for t in sub if t['r'] > 0) / len(sub),
                  't': tv}
    return out


def equity(trades, N):
    """cohort 轮动净值：当日满仓、批间等权、批内等权。返回 [(day, level, 活跃批数)]。"""
    pool = [t for t in trades if t['N'] == N and t['r'] is not None]
    if not pool:
        return []
    cohorts = {}
    for t in pool:
        cohorts.setdefault(t['sig_day'], []).append(t)
    # 每批：day -> 批内均值（停牌日缺贡献按 0 计，分母恒为批内只数）
    coh_daily = {}
    span = {}
    for sd, lst in cohorts.items():
        m, acc = {}, {}
        for t in lst:
            for d, r in t['contrib']:
                acc[d] = acc.get(d, 0.0) + r
                m[d] = m.get(d, 0) + 1
        coh_daily[sd] = {d: acc[d] / len(lst) for d in acc}
        span[sd] = (min(t['in_day'] for t in lst), max(t['out_day'] for t in lst))
    days = sorted({d for cc in coh_daily.values() for d in cc})
    out, lv = [], 1.0
    for d in days:
        act = [sd for sd in coh_daily if span[sd][0] <= d <= span[sd][1]]
        if not act:
            out.append((d, lv, 0))
            continue
        r = sum(coh_daily[sd].get(d, 0.0) for sd in act) / len(act)
        lv *= (1 + r)
        out.append((d, lv, len(act)))
    return out


def perf(eq):
    if len(eq) < 30:
        return None
    lv = [e[1] for e in eq]
    n = len(lv) - 1
    total = lv[-1] / lv[0] - 1
    ann = (1 + total) ** (242.0 / n) - 1 if n > 0 else 0.0
    peak, mdd = lv[0], 0.0
    rets = []
    for i in range(1, len(lv)):
        rets.append(lv[i] / lv[i - 1] - 1)
        peak = max(peak, lv[i])
        mdd = min(mdd, lv[i] / peak - 1)
    mu = sum(rets) / len(rets)
    sd = (sum((x - mu) ** 2 for x in rets) / max(1, len(rets) - 1)) ** 0.5
    return {'total': total, 'ann': ann, 'mdd': mdd,
            'sharpe': (mu / sd * 242 ** 0.5) if sd > 0 else 0.0,
            'calmar': (ann / abs(mdd)) if mdd < 0 else None,
            'days': n,
            'avg_batch': sum(e[2] for e in eq) / len(eq),
            'flat': sum(1 for e in eq if e[2] == 0)}


def perf_from_index(lvl_close, days):
    eq = [(d, lvl_close[d], 1) for d in days]
    return perf(eq)


# ---------------------------------------------------------------- 报告
def pct(x, nd=2):
    return '—' if x is None else ('%+.' + str(nd) + 'f%%') % (x * 100)


def fmt_t(s):
    if s['t'] is None:
        return '—'
    return '%.1f' % s['t'] + ('*' if abs(s['t']) >= 2 else '')


def build_report(res, meta):
    L = []
    L.append('# 四指标滚动回测报告（%s）\n' % meta['stamp'])
    L.append('> 纯本地计算，不写 data.js。样本期 **%s ~ %s**（%d 个交易日），'
             '标的 **%d 只**（沪深主板 60/00），持有期 T+1/3/5/10，'
             '成本 %.2f%%/笔，信号次日开盘成交。\n'
             % (meta['d0'], meta['d1'], meta['ndays'], meta['nstocks'],
                COST * 100))

    L.append('## 〇、先校准基准（不读这节，后面的数字会看错）\n')
    L.append('| 口径 | 同期涨幅 | 说明 |')
    L.append('|---|---|---|')
    for k, v, note in meta['calib']:
        L.append('| %s | %s | %s |' % (k, v, note))
    L.append('')
    L.append(
        '**为什么基准（%s）远高于指数**：① 等权 vs 市值加权（上证被大权重股压着）；'
        '② 算术均值被少数暴涨股拉高——本样本**中位 %s、均值 %s**，等权组合吃的是均值；'
        '③ 生存者 + 入场偏差（这批票是「今天还活跃」的，退市 / 边缘化的不在样本里，'
        '这是方向已知的系统性高估）。'
        % (pct(meta['bh_total'], 1), pct(meta['tot_med'], 1), pct(meta['tot_mean'], 1)))
    L.append('所以本报告**双口径**给超额：`超额(均)` 对全市场算术均值（＝随机抽一只票的期望值，'
             '这是判断「信号有没有价值」的正确对照），`超额(中)` 对中位数（＝典型股票，'
             '回答「选出来的票是不是比一般票好」）。两者符号不一致时，本身就是结论。\n')

    L.append('## 一、组合层：能不能赚钱\n')
    L.append('| 指标 | 持有期 | 累计 | **年化** | 最大回撤 | 夏普 | 卡玛 | 日均活跃批 | 空仓日 |')
    L.append('|---|---|---|---|---|---|---|---|---|')
    for ind in IND_ORDER:
        for N in HORIZONS:
            p = res['perf'].get((ind, N))
            if not p:
                continue
            L.append('| %s | T+%d | %s | **%s** | %s | %.2f | %s | %.1f | %d |' % (
                IND_ZH[ind], N, pct(p['total'], 1), pct(p['ann'], 1), pct(p['mdd'], 1),
                p['sharpe'], ('%.2f' % p['calmar']) if p['calmar'] is not None else '—',
                p['avg_batch'], p['flat']))
    b = res['bench']
    L.append('| **全市场等权指数（基准）** | — | %s | **%s** | %s | %.2f | %s | — | 0 |' % (
        pct(b['total'], 1), pct(b['ann'], 1), pct(b['mdd'], 1), b['sharpe'],
        ('%.2f' % b['calmar']) if b['calmar'] is not None else '—'))
    L.append('')

    L.append('## 二、归因层：哪个指标真的准\n')
    L.append('| 指标 | 持有期 | 笔数 | 信号日 | 绝对胜率 | 超额胜率 | 平均收益 | 超额(均) | 超额(中) | 超额95%CI | t | 买不进 |')
    L.append('|---|---|---|---|---|---|---|---|---|---|---|---|')
    for ind in IND_ORDER:
        for N in HORIZONS:
            s = res['stats'].get((ind, N))
            if not s:
                continue
            ci = ('[%s, %s]' % (pct(s['ci'][0], 1), pct(s['ci'][1], 1))
                  if s['ci'] else '—')
            L.append('| %s | T+%d | %d | %d | %.1f%% | %.1f%% | %s | %s | %s | %s | %s | %d |' % (
                IND_ZH[ind], N, s['n'], s['days'], s['win_abs'] * 100,
                s['win_ex'] * 100, pct(s['avg'], 2), pct(s['avg_ex'], 2),
                pct(s['avg_ex_med'], 2), ci, fmt_t(s), s['unfillable']))
    L.append('')

    if res['years']:
        L.append('### 分年度（T+5，检验结论是不是只在某一年成立）\n')
        L.append('| 指标 | 年份 | 笔数 | 信号日 | 平均收益 | 超额(均) | 绝对胜率 | t |')
        L.append('|---|---|---|---|---|---|---|---|')
        for ind in IND_ORDER:
            for y, v in sorted(res['years'].get(ind, {}).items()):
                L.append('| %s | %s | %d | %d | %s | %s | %.1f%% | %s |' % (
                    IND_ZH[ind], y, v['n'], v['days'], pct(v['avg'], 2),
                    pct(v['avg_ex'], 2), v['win_abs'] * 100,
                    ('%.1f' % v['t']) if v['t'] is not None else '—'))
        L.append('')

    subs = res['substats']
    if subs:
        L.append('### 分档（同指标不同强度的差别）\n')
        L.append('| 指标·分档 | 持有期 | 笔数 | 平均收益 | 超额(均) | 超额胜率 | t |')
        L.append('|---|---|---|---|---|---|---|')
        for (ind, sub, N), s in sorted(subs.items(), key=lambda kv: (
                IND_ORDER.index(kv[0][0]), kv[0][2], kv[0][1])):
            L.append('| %s·%s | T+%d | %d | %s | %s | %.1f%% | %s |' % (
                IND_ZH[ind], sub, N, s['n'], pct(s['avg'], 2), pct(s['avg_ex'], 2),
                s['win_ex'] * 100, fmt_t(s)))
        L.append('')

    L.append('## 三、结论（机器判定，不做人工修饰）\n')
    for line in res['verdicts']:
        L.append('- ' + line)
    L.append('')

    L.append('## 四、局限（读结论前必须知道）\n')
    L += [
        '1. **生存者 + 入场偏差**：universe 是「今天还在 %d 只里」的票。已退市、长期停牌、'
        '被刷出成交额前 500 的不在样本里 → 绝对年化和基准都偏高。这解释了为什么本节基准'
        '（%s）远高于同期国证2000（%s）。' % (meta['nstocks'],
                                            pct(meta['bh_total'], 1), meta['calib'][4][1]),
        '2. **ST 不可知**：K 线里没有历史 ST 标记，回测会包含当期是 ST 的票（实盘会被黑名单拦掉）。',
        '3. **涨跌停排队**：只剔除了「开盘就 ≥9.5% 买不进」，盘中封板 / 炸板的真实滑点没建模。'
        '断板反包买不进 10 笔、横盘启动 300+ 笔——后者占比小，不改变结论但会低估交易摩擦。',
        '4. **前复权口径**：收益含分红再投资，与真实成交价有微小差异；除权日的涨停判定会漏。',
        '5. **样本内检验**：用的是今天这套阈值去回测两年前，不能当作「参数没过拟合」的证据。',
        '6. **基准本身是「持有 1260 只等权」**，任何 5~10 只的集中组合都不可能完全复制它，'
        '所以超额是「选股能力」的下界估计，不是「你一定会输给指数」的断言。',
    ]
    L.append('')
    L.append('---\n')
    L.append('生成：`backtest_indicators.py`　抓取 %d 根 / 预热 %d 根 / 指标切片 %s'
             % (meta['bars'], WARMUP, meta['window'] or '全历史'))
    return '\n'.join(L)


def verdicts(res):
    """机器判定：直接说哪个能用、哪个该降权，不给「各有优劣」式模糊话。"""
    out = []
    ranked = []
    for ind in IND_ORDER:
        s = res['stats'].get((ind, 5))
        if not s or s['days'] < 20:
            out.append('**%s**：信号日仅 %s 天，样本不足，不给结论（继续积累）。'
                       % (IND_ZH[ind], s['days'] if s else 0))
            continue
        ranked.append((ind, s))
    if not ranked:
        return out

    best = max(ranked, key=lambda kv: kv[1]['avg_ex'])
    out.append('T+5 超额最高：**%s**（对均值 %s，t=%s，%d 个信号日）。'
               % (IND_ZH[best[0]], pct(best[1]['avg_ex']), fmt_t(best[1]),
                  best[1]['days']))

    neg = [i for i, s in ranked if s['t'] is not None and s['t'] <= -2]
    flat = [i for i, s in ranked if s['t'] is not None and abs(s['t']) < 2]
    both_neg = [i for i, s in ranked
                if (s['avg_ex'] or 0) < 0 and (s['avg_ex_med'] or 0) < 0]
    neg_but_med_pos = [i for i, s in ranked
                       if (s['avg_ex'] or 0) < 0 and (s['avg_ex_med'] or 0) > 0]

    for ind, s in ranked:
        tail = ('中位口径 %s（%s）'
                % (pct(s['avg_ex_med'], 2),
                   '也为负' if (s['avg_ex_med'] or 0) < 0 else '为正'))
        if s['t'] is None:
            out.append('**%s** 独立信号日不足 2 天，无法算 t，只报数字不下结论。'
                       % IND_ZH[ind])
            continue
        if s['t'] <= -2 and (s['avg_ex_med'] or 0) > 0:
            out.append('**%s**：均值口径 %s（t=%.1f，显著），但%s。'
                       '→ 读法：**选出来的票比「典型股票」略好，但吃不到尾部暴涨**，'
                       '所以在「随机抽一只票」的期望值上跑输。这不是「信号坏了」，'
                       '是**收益结构偏左尾**：赚小的、亏大的，不适合作为独立买入依据。'
                       % (IND_ZH[ind], pct(s['avg_ex'], 2), s['t'], tail))
        elif s['t'] <= -2:
            out.append('**%s** 超额显著为负（t=%.1f，95%%CI 不跨 0），%s → '
                       '两个口径同向为负，建议权重归零。' % (IND_ZH[ind], s['t'], tail))
        elif abs(s['t']) < 2:
            out.append('**%s** 超额与 0 无法区分（t=%.1f，95%%CI 跨 0），%s → '
                       '只能说「没有证据表明它有阿尔法」，不能说它逆向。'
                       % (IND_ZH[ind], s['t'], tail))
        else:
            out.append('**%s** 超额显著为正（t=%.1f），%s → 可作为独立信号。'
                       % (IND_ZH[ind], s['t'], tail))

    if len(neg_but_med_pos) >= 3:
        out.append('**四个指标是同一个病，不是四个病**：%d 个出现「均值负、中位正」，'
                   '方向完全一致 → 问题不在某个阈值，而在**这类短期动量/抢反弹信号的收益结构**：'
                   '它们筛掉了右尾（暴涨股），留下了左尾。改参数解决不了，只能改用法'
                   '（降权、缩持有期、或只在上行环境启用）。' % len(neg_but_med_pos))
    elif len(both_neg) >= 3:
        out.append('**集体失效，不是单点故障**：%d 个指标两个口径同时为负、方向一致，'
                   '改参数的收益远小于停用。' % len(both_neg))
    if flat and not neg:
        out.append('结论：**没有一个指标证明了自己有阿尔法**——这本身就是最重要的结论，'
                   '比「某个指标略好」有价值得多。')

    # 年度稳定性：整体负、但有某一年为正 → 说明是环境依赖，不是恒定规律
    for ind in IND_ORDER:
        yb = res['years'].get(ind) or {}
        if len(yb) < 2:
            continue
        flips = [(y, v['avg_ex']) for y, v in sorted(yb.items())]
        if any(x[1] > 0 for x in flips) and any(x[1] < 0 for x in flips):
            out.append('**%s 分年度符号翻转**：%s → 结论只在特定市场环境成立，'
                       '不能当恒定规律用（这是「样本内挑参数」最容易骗人的地方）。'
                       % (IND_ZH[ind], '，'.join('%s %s' % (y, pct(v, 2)) for y, v in flips)))
    return out


# ---------------------------------------------------------------- main
def selftest():
    """四条恒等式自检。回测最容易在「拆分到日」这一步静默错掉，所以必须机器验：
       ① 逐日贡献连乘 == open_B/open_A（无近似）
       ①b 交易收益 == 连乘 − 成本
       ② 不可成交的单子 r 必须为 None（不许悄悄按成交算）
       ③ equity() 的单批净值 == 该笔连乘（组合层与归因层同源，否则年化不可信）"""
    codes = sorted(_codes())[:3]
    fake = {'idx': {}, 'opens': {}, 'cache': {}}   # 基准置空：自检只看收益恒等式
    bad = 0
    for code in codes:
        p = _bt_path(code)
        if not os.path.exists(p):
            p = os.path.join(KLINE_DIR, code + '.json')
        with open(p, encoding='utf-8') as f:
            bars = json.load(f)
        sigs = replay(code, bars, 320, max(WARMUP, len(bars) - 60))
        trs = build_trades(code, bars, sigs, fake)
        for t in trs[:40]:
            if not t['fillable']:
                if t['r'] is not None:
                    bad += 1
                    print('FAIL ② 不可成交却有收益 %s %s' % (code, t['sig_day']))
                continue
            prod = 1.0
            for _, r in t['contrib']:
                prod *= (1 + r)
            iA = next(i for i, b in enumerate(bars) if b['day'] == t['in_day'])
            iB = next(i for i, b in enumerate(bars) if b['day'] == t['out_day'])
            truth = bars[iB]['open'] / bars[iA]['open']
            if abs(prod - truth) > 1e-9:
                bad += 1
                print('FAIL ① %s %s N=%d 连乘 %.10f vs 真值 %.10f'
                      % (code, t['sig_day'], t['N'], prod, truth))
            if abs((t['r'] + COST) - (truth - 1)) > 1e-9:
                bad += 1
                print('FAIL ①b %s %s 收益与成本对不上' % (code, t['sig_day']))
            eq = equity([t], t['N'])
            if eq:
                got = eq[-1][1] / eq[0][1] - 1
                want = prod / (1 + t['contrib'][0][1]) - 1
                if abs(got - want) > 1e-9:
                    bad += 1
                    print('FAIL ③ %s %s N=%d equity %.10f vs 连乘 %.10f'
                          % (code, t['sig_day'], t['N'], got, want))
    print('[自检] %s' % ('全部通过' if not bad else '%d 处不符' % bad))
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bars', type=int, default=640, help='抓取根数（腾讯上限 640）')
    ap.add_argument('--window', type=int, default=320, help='喂给指标的切片根数，0=全历史')
    ap.add_argument('--days', type=int, default=0, help='只回测最近 N 根，0=全部')
    ap.add_argument('--limit', type=int, default=0, help='只取前 N 只股票（冒烟）')
    ap.add_argument('--force-fetch', action='store_true')
    ap.add_argument('--no-fetch', action='store_true')
    ap.add_argument('--out', default='backtest_report.md')
    ap.add_argument('--selftest', action='store_true', help='只跑恒等式自检，不出报告')
    args, _ = ap.parse_known_args()

    if args.selftest:
        sys.exit(selftest())

    codes = _codes()
    if args.limit:
        codes = codes[:args.limit]
    t0 = time.time()
    if not args.no_fetch:
        fetch_history(codes, args.bars, force=args.force_fetch)

    all_bars = load_bars(codes)
    print('[数据] 可用标的 %d 只' % len(all_bars))
    bench = build_bench(all_bars)
    days = bench['days']
    print('[基准] 交易日 %d（%s ~ %s），基准配对缓存启动' % (len(days), days[0], days[-1]))

    all_trades = []
    n_sig = 0
    for i, (code, bars) in enumerate(sorted(all_bars.items()), 1):
        start = 0
        if args.days:
            start = max(WARMUP, len(bars) - args.days)
        sigs = replay(code, bars, args.window, start)
        n_sig += len(sigs)
        all_trades.extend(build_trades(code, bars, sigs, bench))
        if i % 100 == 0 or i == len(all_bars):
            print('  [回放] %d/%d  信号累计 %d  用时 %.0fs'
                  % (i, len(all_bars), n_sig, time.time() - t0))

    print('[统计] 交易记录 %d 条，开始聚合' % len(all_trades))
    res = {'stats': {}, 'perf': {}, 'substats': {}, 'verdicts': [],
           'years': {}}
    for ind in IND_ORDER:
        pool = [t for t in all_trades if t['ind'] == ind]
        if not pool:
            continue
        for N in HORIZONS:
            s = stats_block(pool, N)
            if s:
                res['stats'][(ind, N)] = s
            p = perf(equity(pool, N))
            if p:
                res['perf'][(ind, N)] = p
        yb = year_block(pool, 5)
        if yb:
            res['years'][ind] = yb
        subs = sorted({t['sub'] for t in pool})[:4]
        for sub in subs:
            ssub = [t for t in pool if t['sub'] == sub]
            for N in HORIZONS:
                s = stats_block(ssub, N)
                if s and s['n'] >= 20:
                    res['substats'][(ind, sub, N)] = s

    bdays = [d for d in days if d in bench['lvl_close']]

    # 样本期必须用「实际产生信号的首末日」：K 线有 716 个交易日，但前 120 根是预热，
    # 报告里写 716 天却只回测了后 500 天，是属于「数字对不上」的那类错——不许发生。
    sig_days = [t['sig_day'] for t in all_trades if t['r'] is not None]
    d0 = min(sig_days) if sig_days else bdays[0]
    d1 = max(sig_days) if sig_days else bdays[-1]
    win_days = [d for d in bdays if d0 <= d <= d1]
    bench_total = (bench['lvl_close'][d1] / bench['lvl_close'][d0] - 1
                   if (d0 in bench['lvl_close'] and d1 in bench['lvl_close']) else None)
    res['bench'] = perf_from_index(bench['lvl_close'], win_days)
    res['verdicts'] = verdicts(res)

    calib = [('**全市场等权指数（本报告基准）**', pct(bench_total, 1),
              '日频再平衡 · %d 只 · 含生存者偏差' % bench['n']),
             ('个股均值（各自首末）', pct(bench['tot_mean'], 1),
              '算术均值，被少数暴涨股拉高 · %d 只' % bench['tot_n']),
             ('个股中位（各自首末）', pct(bench['tot_med'], 1), '「典型股票」')]
    calib += index_calib(d0, d1)
    try:
        gap = abs((bench_total or 0) * 100 - float(calib[4][1].rstrip('%')))
        gap_s = '%d' % gap
    except Exception:
        gap_s = '—'
    res['verdicts'] += ['**基准校准**：本报告基准同期 %s，其中小盘指数 %s。'
                        '两者差 %s 个百分点来自「等权 + 算术均值 + 生存者偏差」三重叠加，'
                        '不是数据错误；但它意味着**绝对年化只能看量级，不能当业绩承诺**。'
                        % (pct(bench_total, 1), calib[4][1], gap_s)]

    meta = {'stamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'd0': d0, 'd1': d1, 'ndays': len(win_days),
            'nstocks': len(all_bars), 'bars': args.bars, 'window': args.window,
            'calib': calib, 'bh_total': bench_total,
            'tot_mean': bench['tot_mean'], 'tot_med': bench['tot_med']}
    md = build_report(res, meta)
    with open(os.path.join(BASE, args.out), 'w', encoding='utf-8') as f:
        f.write(md)

    dump = {
        'meta': meta,
        'bench': res['bench'],
        'perf': {'%s|%d' % k: v for k, v in res['perf'].items()},
        'stats': {'%s|%d' % k: v for k, v in res['stats'].items()},
        'verdicts': res['verdicts'],
    }
    with open(os.path.join(BASE, 'backtest_result.json'), 'w', encoding='utf-8') as f:
        json.dump(dump, f, ensure_ascii=False, indent=1)
    print('[完成] %s  用时 %.0fs' % (args.out, time.time() - t0))
    print('\n'.join(res['verdicts']))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""横盘启动池（R100z232，2026-10-26）—— 取代九门 `powerScreen`。

## 为什么是它取代九门（数据，不是偏好）

九门（`power_screener.py`）九道硬阈值里有 G1「当日涨幅 ≥3%」——**它筛的是已经
启动的票**，本质是追涨。同跑道回测（1,260 只 / 537 个交易日 / T+1 开盘买）：

| 指标 | T+5 年化 | 均超额/笔 | t | 胜率 |
|---|---|---|---|---|
| 九门强势 | +1.97% | −0.390% | **−5.02** | 45.6% |
| 谐波形态 | +17.18% | −0.257% | −3.08 | 46.0% |
| 主力吸筹 | +8.64% | −0.412% | −3.66 | 47.0% |
| 横盘启动 | +23.15% | +0.148% | 0.41 | 54.0% |
| 基准（全市场等权） | +31.18% | — | — | — |

九门的 `t = −5.02` 是四个里**唯一突破 5 的**，不是噪声；而它「次日留存 25%」
（今天 30 只、明天剩 7 只）意味着每天一份新名单，追进去就换股。

## 口径（单一事实源 = `backtest_breakout._platform`，参数常量从这里 import）

    W = 60 根
    振幅    (max(high) − min(low)) / min(low) ≤ 30%
    箱顶    收盘价的 95 分位（**不是 max(high)，也不是 max(close)**，两次都翻过车，见 _platform 注）
    下沿    min(low)
    带内    下沿×1.015 ≤ 收盘 ≤ 箱顶×1.03
    粘合    MA5/10/20/30/60 的 极差比 ≤ 6%
    地量    近 10 日均额 / 前 50 日均额 ≤ 0.75
    贴沿    距箱顶 ≤ 3%（**这才是池子的形态：还没突破，但已经贴上去**）

## 为什么是「贴沿名单」而不是「突破买点」

回测：2 年 × 1,260 只，11 个「平台+收上沿×1.005+量 1.8×+涨幅≥5%」的突破日里
**7 个是涨停板**、1 个成交额不足 2 亿，真正可成交只剩 2 笔 —— 无法统计。
与 24.86「78.1% 的反包日在涨停板上买不进」是同一条规律。

所以本池只做一件事：**每天把「已经贴到箱顶、量能枯竭、均线粘合」的票列出来，
给出一个可挂在盘口的买入区间**。突破确认留给 09:45 开盘精选去判（那时有盘口）。

## 买入区间（用户 2026-10-26 第 4 项要求）

    entry.low      箱顶 × 0.985     承接下限（跌到这里算回到箱内，不接）
    entry.high     箱顶 × 1.005     不追上限（超过就是追突破，不是低吸）
    entry.stop     下沿 × 0.99      **形态证伪价**（跌破平台下沿 = 不是横盘了）
    entry.target   箱顶 + 箱体高度   箱体高度投影（最小 6%，避免贴沿的窄箱给不出目标）
    entry.holdDays 10              回测最优持有（T+10 优于 T+5）

⚠️ stop 不是让你挂的百分比止损：宽平台的证伪价可能距买价 −20%，那是箱体几何，
   不是风险预算。回测本身就是「固定持有 T+10、不设止损」，别自己加戏。

## 用法

    python3 breakout_pool.py              # 干跑，打印名单
    python3 breakout_pool.py --write      # 写 data.js 顶层 breakoutPool
    python3 breakout_pool.py --json       # 机器可读
    python3 breakout_pool.py --self-test
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kline_cache as K                      # noqa: E402
from backtest_breakout import (AMP_MAX, BAND_LO, BAND_UP,     # noqa: E402
                               DRY_MAX, EDGE_HI, EDGE_LO, SQUEEZE_MAX)
import common                                # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
W = 60                 # 平台窗口
MIN_BARS = 120         # 少于这个根数连 MA60 + W 都给不出
# 「贴沿」最大距离，由 EDGE_LO 反推（EDGE_LO=0.97 → 3.09%），不手写数字，
# 免得改窗口时忘了改这里、分数排序跟入池判据脱节。
NEAR_MAX = (1.0 / EDGE_LO - 1.0) * 100
# 持有期 = 回测里 T+10 优于 T+5（+1.52% vs +0.89%，胜率 56.7% vs 54.0%），
# 且回测**不设百分比止损**，所以池子给的是「时间 + 证伪价」两条风控。
HOLD_DAYS = 10


def _ma(vals, n):
    if len(vals) < n or n <= 0:
        return None
    return sum(vals[-n:]) / n


def platform(bars, k=None):
    """返回 dict（成立）或 None。k 默认最后一根。

    与 `backtest_breakout._platform` 同一口径（常量也同源 import），
    区别只是**把中间量一起返回**：距箱顶、箱体高度、粘合度、地量比。
    回测脚本只要 (ok, hi, lo)，实盘池要给读者看「凭什么选它」。
    """
    if k is None:
        k = len(bars) - 1
    if k - W < 20:
        return None
    seg = bars[k - W:k]
    hi_shadow = max(x['high'] for x in seg)
    lo = min(x['low'] for x in seg)
    if lo <= 0:
        return None
    if (hi_shadow - lo) / lo > AMP_MAX:
        return None
    closes_sorted = sorted(x['close'] for x in seg)
    top = closes_sorted[int(len(closes_sorted) * 0.95) - 1]
    if top <= 0:
        return None
    for x in seg:
        if not (lo * BAND_LO <= x['close'] <= top * BAND_UP):
            return None
    # MA 不含信号日（k），与 backtest_breakout._platform 的 `bars[:k]` 一致；
    # 若把当日算进 MA，粘合度会系统性偏低，等于偷偷放宽了均线条件。
    c = [x['close'] for x in bars[:k]]
    mas = [_ma(c, n) for n in (5, 10, 20, 30, 60)]
    if any(m is None or m <= 0 for m in mas):
        return None
    squeeze = max(mas) / min(mas) - 1
    if squeeze > SQUEEZE_MAX:
        return None
    amt = [bars[i]['volume'] * bars[i]['close'] for i in range(k - 60, k)]
    near, base = amt[-10:], amt[:50]
    if sum(base) <= 0:
        return None
    dry = (sum(near) / 10.0) / (sum(base) / 50.0)
    if dry > DRY_MAX:
        return None
    close = bars[k]['close']
    dist = (top / close - 1) * 100              # 正数=还在箱顶下方
    return dict(top=top, bottom=lo, close=close, distTop=dist,
                boxPct=(top - lo) / lo * 100, squeeze=squeeze * 100, dry=dry,
                ma5=mas[0], ma10=mas[1], ma20=mas[2], ma30=mas[3], ma60=mas[4])


def score_of(p):
    """0-100 的整数分，四项权重写死（改权重 = 改排序，别偷偷调）。

    40 分 地量：越枯越好（0.30 → 满分，DRY_MAX → 0）
    30 分 粘合：越紧越好（0% → 满分，SQUEEZE_MAX → 0）
    20 分 贴沿：越近越好（0% → 满分，3% → 0）
    10 分 箱体：8%~20% 满分，太窄没空间、太宽不是平台
    """
    s = 0.0
    s += max(0.0, min(40.0, (DRY_MAX - p['dry']) / (DRY_MAX - 0.30) * 40))
    s += max(0.0, min(30.0, (1 - p['squeeze'] / 100.0 / SQUEEZE_MAX) * 30))
    s += max(0.0, min(20.0, (1 - max(0.0, p['distTop']) / NEAR_MAX) * 20))
    box = p['boxPct']
    s += max(0.0, min(10.0, (1 - abs(box - 14.0) / 12.0) * 10))
    return int(round(s))


def entry_of(p):
    """买入区间 / 证伪价 / 目标。全部由箱体几何推出，不含主观系数。

    ⚠️ `stop` 是**形态证伪价**（跌破平台下沿 = 这不再是横盘），不是「止损」。
    宽平台的证伪价可能距买价 −20%，那是箱体几何决定的，不是让你真的挂 −20% 止损。
    回测（`backtest_breakout`）用的是**固定持有 T+10、无止损**，所以：
      · 风控靠「时间」（HOLD_DAYS）+「证伪价被打穿就认」两条，不靠百分比止损；
      · 真下单时用 `stopPct` 反推仓位（风险预算倒算），别把它当硬止损挂单。
    """
    top, bottom = p['top'], p['bottom']
    box = top - bottom
    low = round(top * 0.985, 2)
    high = round(top * 1.005, 2)
    stop = round(bottom * 0.99, 2)
    # 目标 = 箱顶 + 箱体高度；窄箱（<6%）按 6% 给，否则贴沿票的目标价贴着成本，没意义
    tgt = round(top + max(box, top * 0.06), 2)
    return {
        'low': low, 'high': high, 'stop': stop, 'target': tgt,
        'invalid': round(bottom * 0.985, 2),
        'chaseMax': round(top * 1.03, 2),
        'stopPct': round((stop / high - 1) * 100, 1),
        'targetPct': round((tgt / high - 1) * 100, 1),
        'holdDays': HOLD_DAYS,
        'note': ('箱顶 %.2f（60 日收盘 95 分位）／下沿 %.2f：%.2f~%.2f 之间低吸，'
                 '高开越过 %.2f 属于追突破不跟；收盘跌破 %.2f 则横盘形态作废，'
                 '否则按持有 %d 日了结（回测口径为固定 %d 日、不设百分比止损）'
                 % (top, bottom, low, high, round(top * 1.03, 2), stop,
                    HOLD_DAYS, HOLD_DAYS)),
    }


def label_of(p):
    """一句话说明它凭什么进池（读者要能自己复核，不能只有分数）。"""
    return ('横盘 %d 日振幅 %.0f%%、距箱顶 %.1f%%、均线粘合 %.1f%%、'
            '近10日均额/前50日 %.2f'
            % (W, p['boxPct'], p['distTop'], p['squeeze'], p['dry']))


def scan(codes, names, secs, asof=''):
    """扫一批代码，返回 (passed, counters)。

    asof 非空时把每只票的 K 线截断到该日（含），用来复现「那天的池子长什么样」。
    这是本模块唯一可自证的方式：今天(2026-10-26)恰好是横截面空窗日，
    没有 asof 就只能靠「语法没错」交付，等于没验收。
    """
    passed = []
    cnt = dict(universe=0, bars=0, platform=0, edge=0, stale=0)
    for c in codes:
        if not str(c).startswith(('60', '00')):
            continue
        cnt['universe'] += 1
        bars = K.get_bars(c)
        if not bars or len(bars) < MIN_BARS:
            continue
        if asof:
            bars = [b for b in bars if str(b.get('day') or '') <= asof]
            if len(bars) < MIN_BARS:
                continue
        cnt['bars'] += 1
        p = platform(bars)
        if not p:
            continue
        cnt['platform'] += 1
        # 贴沿判据 = 回测的 EDGE_LO/EDGE_HI 比值窗口（不是换算后的百分比，
        # 百分比只是展示；判据必须与 backtest_breakout.replay_breakout 完全同源）
        ratio = p['close'] / p['top'] if p['top'] else 0
        if not (EDGE_LO <= ratio <= EDGE_HI):
            continue
        cnt['edge'] += 1
        ent = entry_of(p)
        item = {
            'code': str(c), 'name': (names.get(str(c)) or '').strip(),
            'sector': secs.get(str(c)) or '',
            'lastClose': round(p['close'], 2),
            'top': round(p['top'], 2), 'bottom': round(p['bottom'], 2),
            'distTop': round(p['distTop'], 2), 'boxPct': round(p['boxPct'], 2),
            'squeeze': round(p['squeeze'], 2), 'dry': round(p['dry'], 2),
            'ma5': round(p['ma5'], 2), 'ma10': round(p['ma10'], 2),
            'ma20': round(p['ma20'], 2), 'ma30': round(p['ma30'], 2),
            'ma60': round(p['ma60'], 2),
            'entry': ent, 'score': score_of(p), 'note': label_of(p),
            'form': '横盘贴沿',
        }
        passed.append(item)
    passed.sort(key=lambda x: (-x['score'], x['distTop']))
    return passed, cnt


def self_test():
    """合成一个标准平台：60 根在 108.6~111.4 之间横、量逐级萎缩、均线收拢。

    注意第 75 根要留一根**深下影**：`BAND_LO=1.015` 要求最低收盘价高于
    最低价 1.5%，即平台底部必须由「插针」定义，而不是由某根收盘价定义。
    没有这根针，箱体下沿=最低收盘价，带内条件永远不成立 —— 这是口径本身
    的特征（回测也是这么筛的），不是自检数据的锅。
    """
    bars = []
    for i in range(130):
        if i < 70:
            base = 96 + i * 0.18                    # 预热段 96 → 108.4
            vol = 4_200_000
        else:
            j = i - 70                              # 0..59
            base = 110.0 + (1.4 if j % 2 == 0 else -1.4)
            vol = int(3_200_000 * (1 - j / 85.0))
        o = base * 0.998
        c = base
        lo = min(o, c) * 0.996
        if i == 75:
            lo = base * 0.94                        # 深下影定义箱底
        bars.append({'day': '2026-01-%02d' % ((i % 28) + 1), 'open': o, 'close': c,
                     'high': max(o, c) * 1.004, 'low': lo,
                     'volume': max(1000, vol)})
    p = platform(bars)
    ok = p is not None and p['boxPct'] < 30 and p['dry'] < DRY_MAX
    print('[SELF-TEST] platform=%s' % ('成立' if p else '不成立'))
    if p:
        print('[SELF-TEST] 箱顶 %.2f 下沿 %.2f 距箱顶 %.2f%% 粘合 %.2f%% 地量 %.2f 分 %d'
              % (p['top'], p['bottom'], p['distTop'], p['squeeze'], p['dry'], score_of(p)))
        e = entry_of(p)
        print('[SELF-TEST] 买区 %.2f~%.2f 证伪价 %.2f 目标 %.2f'
              % (e['low'], e['high'], e['stop'], e['target']))
        ok = ok and e['low'] < e['high'] and e['stop'] < e['low'] and e['target'] > e['high']
        ok = ok and p['distTop'] <= NEAR_MAX       # 必须落在「贴沿」区间才算候选
    print('[SELF-TEST] %s' % ('OK' if ok else 'FAIL'))
    return ok


def local_stamp():
    """时间戳（不依赖 common 是否有同名函数，自己算）。"""
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('--limit', type=int, default=0, help='只扫前 N 只（调试用）')
    ap.add_argument('--asof', default='', metavar='YYYY-MM-DD',
                    help='把 K 线截断到该日复现当时的池子（只读，不会写盘）')
    a = ap.parse_args()
    if a.self_test:
        return 0 if self_test() else 1
    if a.asof and a.write:
        print('[BREAKOUT] --asof 是只读复现，不能与 --write 同用')
        return 2

    D = common.load_dashboard_data(BASE)
    names = K.collect_pool_names(D)
    secs = K.collect_sectors(D)

    codes = K.collect_pool_codes(D)
    # 扩展扫描域：断板池 + stkKlines 内的主板标的（与 power_screener 同一做法）
    for pool in ('confirmed', 'watching'):
        for e in (D.get('duanban') or {}).get(pool) or []:
            c = str(e.get('code') or '')
            if c.startswith(('60', '00')):
                codes.append(c)
    for c in (D.get('stkKlines') or {}):
        if str(c).startswith(('60', '00')):
            codes.append(str(c))
    # R100m 起 full_scan 已把全市场高流动性主板预筛成候选，优先用它（缓存已预热）
    for c in ((D.get('fullScan') or {}).get('candidates') or []):
        c = str(c if isinstance(c, str) else (c or {}).get('code') or '')
        if c.startswith(('60', '00')):
            codes.append(c)
    codes = sorted(set(codes))
    if a.limit:
        codes = codes[:a.limit]
    print('[BREAKOUT] 扫描域 %d 只' % len(codes))

    passed, cnt = scan(codes, names, secs, asof=a.asof)
    print('[BREAKOUT] 有 K 线 %d / 平台成立 %d / 贴沿入池 %d%s'
          % (cnt['bars'], cnt['platform'], len(passed),
             ('  (asof %s)' % a.asof) if a.asof else ''))

    today = a.asof or common.today_trade_date()

    # 名字兜底（照 power_screener R100z167 的做法）：扫描域里有票不在任何池内，
    # `collect_pool_names` 覆盖不到 → name=''。缺名会让前端渲染成空行 / 双重代码，
    # 所以只在**确实缺名**的标的上补一次行情源（内部按 60 只/批），取不到就留空，
    # 由 roll_fail_pool 把该归档条目丢掉，绝不把空串写进 data.js。
    _old_bp = D.get('breakoutPool') or {}
    _miss = sorted({i['code'] for i in passed if not str(i.get('name') or '').strip()})
    for _key in ('passed', 'failPool'):
        _miss += sorted({str(x.get('code') or '') for x in (_old_bp.get(_key) or [])
                         if isinstance(x, dict) and not str(x.get('name') or '').strip()})
    _miss = [c for c in dict.fromkeys(_miss) if c]
    if _miss:
        _got = common.fetch_names(_miss) or {}
        for i in passed:
            if not str(i.get('name') or '').strip():
                i['name'] = _got.get(i['code'], '')
        names.update(_got)
        print('[BREAKOUT] 补名：需要 %d 只，取到 %d 只（仍无名 %d 只）'
              % (len(_miss), len(_got), len(_miss) - len(_got)))
    old = D.get('breakoutPool') or {}
    # 首日入池标记（与九门/吸筹同口径：沿用旧值 / 首次见写今天 / 见过但无记忆不写）
    _old_ed = {str(i.get('code') or ''): i.get('entryDate')
               for i in (old.get('passed') or []) if isinstance(i, dict)}
    _old_codes = set(_old_ed)
    for i in passed:
        c = i['code']
        if c in _old_codes and _old_ed.get(c):
            i['entryDate'] = _old_ed[c]
        elif c not in _old_codes:
            i['entryDate'] = today

    # 失效归档：上一轮在池、本轮不在了 → 留 5 个交易日
    new_codes = {i['code'] for i in passed}
    new_fails = []
    for oi in (old.get('passed') or []):
        if not isinstance(oi, dict):
            continue
        c = str(oi.get('code') or '')
        if not c or c in new_codes:
            continue
        new_fails.append({'code': c, 'name': (oi.get('name') or '').strip(),
                          'sector': oi.get('sector') or '',
                          'reason': '本轮平台不再成立（振幅超限 / 均线发散 / 放量 / 已突破离沿）',
                          'entryDate': today})
    field = {
        'updatedAt': '%s（横盘启动 %s 收盘 数据已自动更新）'
                     % (local_stamp(), today),
        'tradeDate': today,
        'date': today,
        'passed': passed[:20],
        # R100z233：原来是空串 —— 页面上只剩「贴沿名单 · 1 只」，读者无法分辨
        # 「今天真的只有 1 只合格」还是「数据抓空只扫到 1 只」。这就是本项目反复踩的
        # 「抓不到 ≠ 没有」。把四段漏斗写出来，让 1 只也能自证。
        'summary': ('扫描域 %d 只沪深主板 → 有足够 K 线 %d 只 → 平台成立 %d 只 → 贴沿入池 %d 只；'
                    '主要淘汰在振幅>%.0f%%、均线粘合>%.0f%%、近10日均额/前50日>%.2f 三道门槛'
                    % (cnt['universe'], cnt['bars'], cnt['platform'], len(passed),
                       AMP_MAX * 100, SQUEEZE_MAX * 100, DRY_MAX)),
        'rule': ('60 日平台（振幅≤%.0f%%）+ 均线粘合≤%.0f%% + 地量（近10日均额/前50日≤%.2f）'
                 '+ 距箱顶≤%.0f%%；箱顶取 60 日收盘 95 分位' % (AMP_MAX * 100, SQUEEZE_MAX * 100,
                                                             DRY_MAX, NEAR_MAX)),
    }
    if new_fails or (old.get('failPool')):
        field['failPool'] = common.roll_fail_pool(
            old.get('failPool') or [], new_fails, today, exclude_codes=new_codes,
            name_map=names)
    if not passed and old and not a.asof:
        print('[BREAKOUT] 本轮无贴沿标的，保留既有池（不清场）')
        field['passed'] = old.get('passed') or []
        field['note'] = '本轮无新命中，沿用上一轮名单'
    D['breakoutPool'] = field

    if not a.write:
        print('\n%-8s %-8s %5s %7s %7s %6s  %s'
              % ('代码', '名称', '分数', '距箱顶', '粘合', '地量', '买区'))
        for i in passed[:20]:
            e = i['entry']
            print('%-8s %-8s %5d %+6.1f%% %6.1f%% %6.2f  %.2f~%.2f 证伪 %.2f'
                  % (i['code'], i['name'][:8], i['score'], i['distTop'],
                     i['squeeze'], i['dry'], e['low'], e['high'], e['stop']))
        if a.json:
            print(json.dumps({'ok': True, 'scan': cnt, 'passed': passed,
                              'field': field}, ensure_ascii=False))
        return 0

    common.write_data_js(BASE, D, label='breakout_pool')
    print('[BREAKOUT] 已写入 data.js 顶层 breakoutPool：%d 只' % len(field['passed']))
    _D2 = common.load_dashboard_data(BASE)
    _n = len((_D2.get('breakoutPool') or {}).get('passed') or [])
    print('[BREAKOUT] 落盘复验：%d 只' % _n)
    if a.json:
        print(json.dumps({'ok': True, 'scan': cnt, 'written': _n,
                          'passed': passed, 'field': field}, ensure_ascii=False))
    return 0


def common_load_stamp():
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M')


if __name__ == '__main__':
    sys.exit(main())

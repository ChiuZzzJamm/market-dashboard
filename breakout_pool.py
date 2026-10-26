#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""横盘启动池（data.js 顶层 breakoutPool）—— 涨停锚口径。

形态定义（2026-10-26 用户口述，已用他给的 4 张图校验，4/4 命中）：
  先有一根涨停（异动）→ 缩量横盘洗盘，股价一般不跌破涨停日最低价
  （盘中破了、只要当日至 1~2 个交易日内收盘收回上方，就算没破）→ 洗完再启动。
本模块只抓「洗完、还没启动」那一批，在涨停收盘附近给次日挂单区间。

================================================================================
三条红线（不许改回去，都是实测踩过的）
================================================================================
  1. 锚点必须是**涨停事件**。不许退化成「横着走 60 天就行」——那等于把形态的定义丢掉；
     而且 60 日窗口会被涨停那根大阳线撑高箱体，**反而把刚洗完盘的票筛出去**。
  2. 不许改成纯几何平台（60 日箱体 + 均线粘合 + 地量 + 贴沿）。同跑道回测里它的
     +15% 命中率只有 **20.3%**，低于随机买入的 23.5%——没有 edge。本口径 **33.5%**。
  3. 锚点取「这一波的起点涨停」，不是最近那根。上海洗霸 8/24 起爆、之后又有三个涨停，
     只看最近那根会把它判成「刚洗完 7 天」，而整波已涨 41.9%、早该失效。
  校验口径时先拿用户给的 4 张正样本当靶子跑：603200 / 002696 / 002674 / 603528。

================================================================================
口径（2026-10-26 用户口述 + 回测校准，冻结；改数字前先看 §回测依据）
================================================================================
  A1 主板 60/00，收盘 ≥ 4 元（与陈学长池同一门槛）
  A2 **T 日涨停**：收盘 / 前收盘 − 1 ≥ 9.8%。缩量涨停、放量涨停、一字板、烂板都算。
     连板时锚点取**这一串涨停的第一根**（连续涨停属于同一次异动，不是多次）。
  T+1 起进入洗盘期，最长跟踪 30 个交易日：

  B1 洗盘天数 5 ~ 30 个交易日（用户：「可能持续 30 个交易日，也可能持续 10 个交易日」）
  B2 缩量：洗盘期**均量 / max(涨停日量, 涨停前 5 日均量) ≤ 0.80**
     ⚠️ 分母取两者较大值，两种涨停各自自洽：放量涨停（多数）是相对涨停那天回弱，
        用 T 日量做分母；缩量涨停（一字板）T 日量是地量，用它当分母会算出天文量比、
        把这一类整批误杀，所以退回前 5 日均量。**只取 T 日量、或只取前 5 日均量，都是错的**
        （原型两版各踩一次）。
  B3 不破底：洗盘期收盘价**允许**跌破涨停日最低价，但必须在该日或其后的 1~2 个
     交易日（RECOVER=2）内**收盘**收回上方（用户：「破底那一下可能是故意砸，让散户恐慌出去」）。
     **盘中破不算破，只看收盘**；超过 2 个交易日仍未收回 → 形态作废。
     证伪价 = 涨停日最低 × STOP_TOL(0.98)，留 2 个点给插针（与陈学长池 S7 同口径）。
  B4 **已启动即失效**：收盘 ≥ 涨停日收盘 × 1.30 → 失效（用户：「超出 30% 就算失效，
     因为已经启动了，我再买已经晚了」）。他给的三只图例现在都已越线。
  B5 超期失效：洗盘 > 30 个交易日仍未启动 → 失效（形态过期，不再是「待启动」）。

  入池（新进）：B1~B5 全成立，且 现价 ≥ 涨停日收盘 ×0.92（POS_MIN，贴回平台、不在坑里接刀）。
     设 0.95 会把用户举例的上海洗霸（比涨停收盘低 5.5 个点）刚好筛掉——校验门槛时
     先拿用户给的正样本当靶子跑一遍，别用「看起来合理」的数。
  出池（归档 failPool）：**只有三类算失效** —— 破底未收回 / 走高 +30% / 洗盘超 30 日。
     已经回落到 −5% 以下但事件仍有效的票**留在池里**，只降分并显示位置、不踢出去，
     因为用户要的是「跌破或走高才失效」，不是「每天都得符合」。

================================================================================
回测依据（backtest_indicators 同一跑道：1313 只 / 640 根日K / T+1 开盘买 / 成本 0.16%）
================================================================================
  | 口径                        | 笔数 | 20日内触及 +15% | +30% | 均最大浮盈 | 均最大浮亏 |
  | 随机买（同批票任意日）       | 787k | 23.5%          | 8.1% | +10.50%   | −8.01%    |
  | **本模块（涨停锚）**        | 4702 | **33.5%**      |13.2% | +13.59%   | −10.63%   |
  | 纯几何平台（不采用）         | 1815 | 20.3%          | 6.1% | +9.60%    | −4.19%    |

  （上表本模块那一行是最终落地的数：分母 max(涨停日量, 前5日均量)、POS_MIN=0.92。
    更严的「只用前 5 日均量做分母」版本是 1037 笔 / 37.9%，但会把缩量涨停那一类整批挡在
    门外，密度只有 3 只/日；落地版 25 只/日，命中率 33.5%，仍比随机高 10 个百分点。）

  固定持有（不看盘、到期卖）超额 ≈ 0（T+5 +0.001%、T+10 +0.006%）——**这套形态不能机械
  持有**，它的价值在「更容易走出 +15%/+30%」，不在「放 10 天就有 alpha」。止盈与离场由
  用户自己决定（2026-10-26「止盈你不用管」），所以本模块**不给目标价**，只给证伪价与失效线。

================================================================================
用法
================================================================================
    python3 breakout_pool.py                    # 干跑，打印名单
    python3 breakout_pool.py --write            # 写 data.js 顶层 breakoutPool
    python3 breakout_pool.py --json             # 机器可读
    python3 breakout_pool.py --self-test        # 合成样本自检
    python3 breakout_pool.py --asof 2026-10-23  # 复现历史某天的池子（只读）
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kline_cache as K                      # noqa: E402
import common                                # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))

LIMIT_PCT = 9.8        # 主板涨停判定（收盘口径）
MIN_N, MAX_N = 5, 30   # 洗盘窗口（用户：10~30 日，也可能更短，含 5~10 日）
RECOVER = 2            # 破底后允许 1~2 个交易日内收回
VOL_MAX = 0.80         # 洗盘期均量 / 涨停前 5 日均量
POS_MIN = 0.92         # 入池要求：现价 ≥ 涨停日收盘 ×0.92（别在坑里接刀；破底另有 B3 判）
INVALID_UP = 0.30      # 收盘 ≥ 涨停日收盘 ×1.30 → 已启动，失效
STOP_TOL = 0.98        # 证伪价 = 涨停日最低 ×0.98（留 2% 给插针，与陈学长 S7 同口径）
MIN_BARS = 40          # 少于这个根数给不出「涨停前 5 日均量 + 洗盘窗口」
# 2026-10-26 18:0x 用户抓到的真 bug：上海洗霸 8/24 第一根涨停 → 之后 3 个涨停、区间涨幅 41.7%，
# 慈文传媒 9/8 → 7 个涨停，早就「已启动」，而我的池子把它们判成「没启动」。
# 根因：只回看 30 日找锚，8/24 那根已在窗口外，于是 10/15 的新涨停被当成一次全新异动。
# 修法：先把涨停按「一轮」归并 —— 两个涨停之间若已走出 +30%，才算两轮；锚点取**本轮起点**，
# 再拿它去做 +30% 失效判定。这样「累计涨幅」的口径和用户看图一致。
CHAIN_LOOKBACK = 150   # 往前找「这一轮涨停链起点」的回看天数（覆盖一整波行情）
CHAIN_UP = 0.30        # 同一轮内允许的最大涨幅；超过即视为进入了新一轮
BUY_LOW, BUY_HIGH = 0.990, 1.020   # 次日挂单区间：今收下方 1% ~ 上方 2%

STATE_CN = {
    'launched': '收盘较涨停板高出 +30%（已启动，本轮不再跟踪）',
    'broken': '收盘跌破涨停日最低价且 %d 个交易日内未收回（形态作废）' % RECOVER,
    'expired': '洗盘超过 %d 个交易日仍未启动（形态过期）' % MAX_N,
    'lost': '不再满足涨停锚 / 缩量 / 主板条件',
}


# ---------------------------------------------------------------- 基础
def _pct(a, b):
    return (a / b - 1) * 100 if b else 0


def _vol(b):
    return float(b.get('volume') or 0)


def _is_limit(bars, t):
    return t >= 1 and _pct(bars[t]['close'], bars[t - 1]['close']) >= LIMIT_PCT


def _base_vol(bars, t):
    """缩量的分母：max(涨停日量, 涨停前 5 日均量)。

    为什么要取 max —— 两种涨停的基准完全不同：
      · 放量涨停（多数）：用户说的「次日量能回弱」是相对**涨停那天**，所以分母用涨停日量；
      · 缩量涨停（一字板）：涨停日量是地量，拿它当分母会算出天文数字的量比，
        把「缩量涨停」这一类整批误杀（原型第一版踩过）。这种情形退回涨停前 5 日均量。
    取两者的较大值，两种情形都自洽。
    """
    prev = bars[max(0, t - 5):t]
    tvol = _vol(bars[t])
    if len(prev) < 3:
        return tvol
    s = sum(_vol(b) for b in prev)
    pavg = s / len(prev) if s > 0 else 0.0
    return max(pavg, tvol)


def track(bars, t, k):
    """从涨停日 t 推演到信号日 k，返回 (status, info)。

    status ∈ {active, launched, broken, expired}

    ⚠️ 判定顺序很重要（2026-10-26 踩过）：**先查「这一波有没有涨过 +30%」，再查破底**。
       慈文传媒 9/8 起爆、走到 10 月已 +75%，但中途有一次收盘跌破涨停日最低且 2 日没收回；
       如果先判破底，它会被打成「形态作废」，而用户看到的是「7 个涨停，早失效了」。
       用户的失效口径是「较第一个涨停板超出 30%」——只问**曾经**有没有涨上去。
    """
    T = bars[t]
    lim_close, lim_low, base = T['close'], T['low'], _base_vol(bars, t)
    seg = bars[t + 1:k + 1]
    # ① 曾经涨过 +30% → 已启动（这一波做完了，别再当埋伏标的）
    for b in seg:
        if b['close'] >= lim_close * (1 + INVALID_UP):
            return 'launched', dict(day=b['day'], close=b['close'])
    # ② 破底与容忍期
    vols, hi, lo = [], T['high'], T['low']
    pending = False
    for i in range(t + 1, k + 1):
        b = bars[i]
        if b['close'] < lim_low:
            rec = any(bars[j]['close'] >= lim_low
                      for j in range(i + 1, min(len(bars), i + 1 + RECOVER)))
            if rec:
                pass                                          # 1~2 日收回，形态继续
            elif k >= i + RECOVER:
                return 'broken', dict(day=b['day'], close=b['close'])
            else:
                pending = True                                # 刚破，还在容忍期
        vols.append(_vol(b))
        hi = max(hi, b['high'])
        lo = min(lo, b['low'])
    nd = k - t
    if nd > MAX_N:                                            # ③ 超期
        return 'expired', dict(washDays=nd)
    vr = (sum(vols) / len(vols)) / base if (vols and base > 0) else None
    return 'active', dict(limitDay=T['day'], limitClose=lim_close, limitLow=lim_low,
                          baseVol=base, washDays=nd, hi=hi, lo=lo, vr=vr,
                          pending=pending, lastClose=bars[k]['close'])


def _limit_starts(bars, k, lookback, upto=None):
    """最近 lookback 日内的涨停链起点（升序）。连板串只认第一根。

    `upto` 用于排除「最近几天刚发生的涨停」——洗盘不足 MIN_N 日的不做锚，
    否则刚拉起来那根会把 nd 打成 0，反而判成「没洗」。
    """
    end = k if upto is None else min(k, upto)
    out = []
    for t in range(max(1, k - lookback), end + 1):
        if not _is_limit(bars, t):
            continue
        if _is_limit(bars, t - 1):
            continue                                          # 连板第 2 根起不算新事件
        out.append(t)
    return out


def _wave_anchor(bars, k, lookback):
    """找「这一波上涨的起点涨停」——从最近 lookback 日内的**最低收盘价**往后，
    第一根涨停链起点就是这波的起爆点。

    为什么不用「相邻涨停之间是否已 +30%」逐段归并：上海洗霸 8/24 → 9/15 → 9/29 → 10/8 → 10/15，
    每段之间都没到 30%，但整波到 10/16 已经比 8/24 高出 41.8%，用户看的是「整波涨幅」。
    以「最低点之后的第一根涨停」为锚，才和用户看图的口径一致（4 只样本全部对上）。
    """
    lo = max(1, k - lookback)
    if k - lo < MIN_N:
        return None
    i_low = min(range(lo, k + 1), key=lambda i: bars[i]['close'])
    starts = _limit_starts(bars, k, lookback, upto=k - MIN_N)
    after = [t for t in starts if t >= i_low]
    if after:
        return after[0]
    before = [t for t in starts if t < i_low]
    return before[-1] if before else None


def evaluate(bars, k):
    """返回 (status, info, item)。取最近一次仍有效的事件定状态。"""
    t = _wave_anchor(bars, k, CHAIN_LOOKBACK)
    if t is None:
        return None, {}, None
    st, info = track(bars, t, k)
    if st != 'active':
        return st, info, None
    if info.get('washDays', 0) < MIN_N:
        return 'lost', info, None                             # 洗盘不足 5 日，还没洗
    if info.get('vr') is None:
        return 'lost', info, None
    if info['vr'] > VOL_MAX:
        return 'lost', info, None                             # B2 缩量不达标，不新进
    close = info['lastClose']
    if close < info['limitClose'] * POS_MIN:
        return 'lost', info, None                             # 还在坑里，不新进
    item = dict(
        code='', name='', sector='',
        lastClose=round(close, 2),
        limitDay=info['limitDay'],
        limitClose=round(info['limitClose'], 2),
        limitLow=round(info['limitLow'], 2),
        invalidPrice=round(info['limitLow'] * STOP_TOL, 2),                # 证伪价
        invalidUp=round(info['limitClose'] * (1 + INVALID_UP), 2),         # 走高失效线
        washDays=info['washDays'],
        volRatio=round(info['vr'], 2),
        posPct=round(_pct(close, info['limitClose']), 1),
        pending=bool(info.get('pending')),
        rangePct=round((info['hi'] - info['lo']) / info['lo'] * 100, 1) if info['lo'] else 0,
    )
    return 'active', info, item


def score_of(it):
    """0-100，四项权重写死（改权重 = 改排序，别偷偷调）。

    40 分 缩量：越枯越好（0.30 → 满分，VOL_MAX → 0）
    30 分 贴沿：回到涨停日收盘附近最好（0% → 满分，−5% → 0）
    20 分 洗盘天数：10~25 日最像「洗透」，太短没洗够、太长要过期
    10 分 未破底：全程没破底满分，今天刚破还在容忍期给 5 分
    """
    s = 0.0
    vr = it.get('volRatio')
    if vr is not None:
        s += max(0.0, min(40.0, (VOL_MAX - vr) / (VOL_MAX - 0.30) * 40))
    pos = it.get('posPct')
    if pos is not None:
        s += max(0.0, min(30.0, (1 - max(0.0, -pos) / 5.0) * 30))
    nd = it.get('washDays') or 0
    if 10 <= nd <= 25:
        s += 20.0
    elif nd < 10:
        s += max(0.0, (nd - 5) / 5.0 * 20)
    else:
        s += max(0.0, (MAX_N - nd) / (MAX_N - 25) * 20)
    s += 5.0 if it.get('pending') else 10.0
    return int(round(s))


def entry_of(it):
    """次日挂单区间 + 证伪价。**不给目标价** —— 用户 2026-10-26：「止盈你不用管」。"""
    c = it['lastClose']
    return dict(
        low=round(c * BUY_LOW, 2), high=round(c * BUY_HIGH, 2),
        stop=it['invalidPrice'], invalidUp=it['invalidUp'],
        stopPct=round(_pct(it['invalidPrice'], c), 1),
        note=('洗盘 %d 日、量比 %.2f：明日在 %.2f~%.2f 挂低吸；收盘跌破 %.2f'
              '（涨停日最低×0.98）形态作废；收盘站上 %.2f（较涨停板 +30%%）算已经启动，'
              '本池不再跟踪。不设目标价，离场自己看。'
              % (it['washDays'], it['volRatio'] or 0, c * BUY_LOW, c * BUY_HIGH,
                 it['invalidPrice'], it['invalidUp'])),
    )


def label_of(it):
    """一句话说明它凭什么进池（读者要能自己复核，不能只有分数）。"""
    return ('%s 涨停 %.2f → 洗盘 %d 个交易日、量比 %.2f、现价距涨停收盘 %+.1f%%、'
            '涨停日最低 %.2f（证伪 %.2f）'
            % (it['limitDay'], it['limitClose'], it['washDays'], it['volRatio'] or 0,
               it['posPct'], it['limitLow'], it['invalidPrice']))


# ---------------------------------------------------------------- 扫描
def scan(codes, names, secs, asof=''):
    """扫一批代码，返回 (passed, counters)。"""
    passed = []
    cnt = dict(universe=0, bars=0, limit=0, active=0, pool=0)
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
        k = len(bars) - 1
        if bars[k]['close'] < 4:
            continue
        if _limit_starts(bars, k, MAX_N):
            cnt['limit'] += 1
        st, _info, item = evaluate(bars, k)
        if st != 'active' or not item:
            continue
        cnt['active'] += 1
        item['code'] = str(c)
        item['name'] = (names.get(str(c)) or '').strip()
        item['sector'] = secs.get(str(c)) or ''
        item['entry'] = entry_of(item)
        item['score'] = score_of(item)
        item['note'] = label_of(item)
        item['form'] = '涨停后洗盘'
        passed.append(item)
    passed.sort(key=lambda x: (-x['score'], abs(x.get('posPct') or 0)))
    cnt['pool'] = len(passed)
    return passed, cnt


def diagnose(bars, item):
    """老成员掉出池子时给出具体原因（写进 failPool.reason，别写笼统话）。"""
    ld = str(item.get('limitDay') or '')
    t = next((i for i, b in enumerate(bars) if str(b.get('day') or '') == ld), None)
    if t is None:
        return STATE_CN['lost']
    st, _info = track(bars, t, len(bars) - 1)
    return STATE_CN.get(st, STATE_CN['lost'])


def self_test():
    """合成「涨停 + 缩量横盘 + 破底收回」，验证三个分支都判对。"""
    bars, px = [], 10.0
    for i in range(60):
        px *= 1.002
        bars.append(dict(day='2026-01-%02d' % (i + 1), open=px * 0.995, close=px,
                         high=px * 1.01, low=px * 0.985, volume=5_000_000))
    base = px
    bars.append(dict(day='2026-03-02', open=base * 1.02, close=base * 1.10,
                     high=base * 1.10, low=base * 1.01, volume=18_000_000))   # 涨停
    t = len(bars) - 1
    lim_close, lim_low = bars[t]['close'], bars[t]['low']
    px = lim_close
    for j in range(12):                       # 缩量横盘，第 6 根收盘破底、次日收回
        v = int(3_600_000 * (1 - j / 20.0))
        lo = lim_low * 0.97 if j == 6 else px * 0.985
        cl = lim_low * 0.99 if j == 6 else px * (1 + (0.004 if j % 2 else -0.004))
        bars.append(dict(day='2026-03-%02d' % (3 + j), open=px, close=cl,
                         high=max(px, cl) * 1.006, low=lo, volume=v))
    k = len(bars) - 1
    st, info, item = evaluate(bars, k)
    ok = (st == 'active' and item is not None and 5 <= item['washDays'] <= 30
          and item['invalidPrice'] < item['lastClose'])
    print('[SELF-TEST] 正常样本 → status=%s 洗盘=%s 量比=%s 证伪=%.2f 现价=%.2f'
          % (st, info.get('washDays'), (item or {}).get('volRatio'),
             (item or {}).get('invalidPrice', 0), (item or {}).get('lastClose', 0)))
    bars2 = [dict(b) for b in bars]
    bars2[k]['close'] = lim_close * 1.31
    st2, _, _ = evaluate(bars2, k)
    bars3 = [dict(b) for b in bars]
    for j in (6, 7, 8):
        bars3[t + 1 + j]['close'] = lim_low * 0.95
    st3, _, _ = evaluate(bars3, k)
    print('[SELF-TEST] 走高 +31%% → %s ；破底连续 3 日不收回 → %s' % (st2, st3))
    ok = ok and st2 == 'launched' and st3 == 'broken'
    print('[SELF-TEST] %s' % ('OK' if ok else 'FAIL'))
    return ok


def local_stamp():
    return datetime.now().strftime('%Y-%m-%d %H:%M')


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
    for pool in ('confirmed', 'watching'):
        for e in (D.get('duanban') or {}).get(pool) or []:
            c = str(e.get('code') or '')
            if c.startswith(('60', '00')):
                codes.append(c)
    for c in (D.get('stkKlines') or {}):
        if str(c).startswith(('60', '00')):
            codes.append(str(c))
    for c in ((D.get('fullScan') or {}).get('candidates') or []):
        c = str(c if isinstance(c, str) else (c or {}).get('code') or '')
        if c.startswith(('60', '00')):
            codes.append(c)
    codes = sorted(set(codes))
    if a.limit:
        codes = codes[:a.limit]
    print('[BREAKOUT] 扫描域 %d 只' % len(codes))

    passed, cnt = scan(codes, names, secs, asof=a.asof)
    print('[BREAKOUT] 有 K 线 %d / 30 日内有涨停事件 %d / 入池 %d%s'
          % (cnt['bars'], cnt['limit'], len(passed),
             ('  (asof %s)' % a.asof) if a.asof else ''))

    today = a.asof or common.today_trade_date()

    # 名字兜底：扫描域里有票不在任何池内 → name=''，缺名会让前端渲染成空行。
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
    old_passed = [x for x in (old.get('passed') or []) if isinstance(x, dict)]
    new_codes = {i['code'] for i in passed}

    # ① 首日入池标记：沿用旧值 / 首次见写今天
    _old_ed = {str(i.get('code') or ''): i.get('entryDate') for i in old_passed}
    for i in passed:
        i['entryDate'] = _old_ed.get(i['code']) or today

    # ② 老成员：事件仍有效、只是今天不贴沿 → 留在池里（用户口径：只有跌破/走高才算失效）
    kept, new_fails = [], []
    for oi in old_passed:
        c = str(oi.get('code') or '')
        if not c or c in new_codes:
            continue
        bars = K.get_bars(c)
        if not bars:
            continue
        # 2026-10-26 修：老成员必须用**同一套口径重算**（波起点 + 先判 +30%），
        # 不能拿它入库时记下的 limitDay 去 track —— 否则「已经又涨了 30%」的票会被误留。
        st, _info, item2 = evaluate(bars, len(bars) - 1)
        if st == 'active' and item2:
            # 事件还活着：留在池里，位置/分数按今天刷新，不踢出去
            for k2 in ('lastClose', 'limitDay', 'limitClose', 'limitLow', 'invalidPrice',
                       'invalidUp', 'washDays', 'volRatio', 'posPct', 'pending', 'rangePct'):
                oi[k2] = item2[k2]
            oi['entry'] = entry_of(item2)
            oi['score'] = score_of(item2)
            oi['note'] = label_of(item2)
            kept.append(oi)
            continue
        new_fails.append({
            'code': c, 'name': (oi.get('name') or '').strip(),
            'sector': oi.get('sector') or '',
            'reason': (STATE_CN.get(st, STATE_CN['lost']) if st
                       else diagnose(bars, oi)),
            # R100z233：带上涨停日/洗盘天数/分数——失效卡要能复盘「它当初凭什么进池」
            'limitDay': oi.get('limitDay') or '',
            'washDays': oi.get('washDays'),
            'score': oi.get('score'),
            'entryDate': today,
        })
    if kept:
        passed = passed + kept
        passed.sort(key=lambda x: (-x['score'], abs(x.get('posPct') or 0)))
        print('[BREAKOUT] 池内沿用 %d 只（事件仍有效、但今天不贴沿）' % len(kept))

    field = {
        'updatedAt': '%s（横盘启动 %s 收盘 数据已自动更新）' % (local_stamp(), today),
        'tradeDate': today,
        'date': today,
        'passed': passed[:30],
        # 筛选漏斗：没有它，「今天只有 N 只」无法区分是筛得严还是 K 线抓空。
        'summary': ('扫描域 %d 只沪深主板 → 有 K 线 %d 只 → 30 日内有涨停事件 %d 只 → '
                    '洗盘/缩量/未启动同时成立 %d 只 → 入池 %d 只'
                    % (cnt['universe'], cnt['bars'], cnt['limit'], cnt['active'], len(passed))),
        'rule': ('T 日涨停（≥%.1f%%）为锚 → 洗盘 %d~%d 个交易日、均量/涨停前 5 日均量≤%.2f → '
                 '破底允许 %d 个交易日内收回 → 收盘较涨停板 +%.0f%% 即算启动、移出本池；'
                 '证伪价＝涨停日最低×%.2f'
                 % (LIMIT_PCT, MIN_N, MAX_N, VOL_MAX, RECOVER, INVALID_UP * 100, STOP_TOL)),
    }
    if new_fails or (old.get('failPool')):
        field['failPool'] = common.roll_fail_pool(
            old.get('failPool') or [], new_fails, today, exclude_codes=new_codes,
            name_map=names)
    if not passed and old_passed and not a.asof:
        print('[BREAKOUT] 本轮无标的，保留既有池（不清场）')
        field['passed'] = old_passed
        field['note'] = '本轮无新命中，沿用上一轮名单'
    # R100z233：老归档补字段——今天之前归档的条目没有 limitDay/score，
    # 失效卡第二格会显示「—」。用同一套波起点规则回填一次（幂等，已有值不动）。
    for f in (field.get('failPool') or []):
        if not isinstance(f, dict) or f.get('limitDay'):
            continue
        _b = K.get_bars(str(f.get('code') or ''))
        if not _b or len(_b) < MIN_BARS:
            continue
        _k = len(_b) - 1
        _t = _wave_anchor(_b, _k, CHAIN_LOOKBACK)
        if _t is not None:
            f['limitDay'] = _b[_t]['day']
            f['washDays'] = _k - _t
    D['breakoutPool'] = field

    if not a.write:
        print('\n%-8s %-9s %5s %6s %6s %8s %10s  %s'
              % ('代码', '名称', '分数', '洗盘', '量比', '距涨停收', '涨停日', '买区/证伪'))
        for i in passed[:30]:
            e = i['entry']
            print('%-8s %-9s %5d %5d日 %6.2f %+7.1f%% %10s  %.2f~%.2f / %.2f'
                  % (i['code'], (i['name'] or '')[:9], i['score'], i['washDays'],
                     i['volRatio'] or 0, i['posPct'], i['limitDay'],
                     e['low'], e['high'], e['stop']))
    if a.json:
        print(json.dumps({'ok': True, 'scan': cnt, 'passed': passed,
                          'failPool': field.get('failPool') or []}, ensure_ascii=False))
    if a.write:
        common.write_data_js(BASE, D, label='breakoutPool')
        chk = (common.load_dashboard_data(BASE) or {}).get('breakoutPool') or {}
        print('[BREAKOUT] 已写入 data.js 顶层 breakoutPool：%d 只'
              % len(chk.get('passed') or []))
        print('[BREAKOUT] 落盘复验：%d 只 / failPool %d 只'
              % (len(chk.get('passed') or []), len(chk.get('failPool') or [])))
    return 0


if __name__ == '__main__':
    sys.exit(main())

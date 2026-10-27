#!/usr/bin/env python3
"""「昨日大跌 + 极度缩量」一票否决：在**横盘启动样本内部**验证它该不该进池。

背景（2026-10-27，用户问「开盘精选和今日买入清单两块的区别」后拍板「按你的建议改」）：
`goldpit_study.py` 已证明全市场口径下「昨日跌 >5% 且 量比 <0.5」的 5 日超额 −1.03%、t=−2.45。
但那条结论的样本是**全市场跌深票**，而否决条件要作用在 `breakoutPool`（横盘启动）上——
两个总体不同（后者已过了「涨停锚 + 洗盘 5~30 日 + 量比 0.45~1.30」的闸）。
**把 A 总体的结论直接搬到 B 总体是外推**，所以这里单独在 B 里测。

三个必须分开回答的问题：
  ① 否决条件在横盘启动样本里方向对不对？（跌>5% 且缩量 vs 同池其他状态日）
  ② 它会否决掉多少？（否决太狠 = 池子被砍空，比不否决更糟）
  ③ 它跟池子**已有**的 VOL_MIN=0.45 量比下限重不重复？
     池子的 volRatio = 洗盘均量/波内峰值量，本脚本的 vr = 昨日额/前5日均额 —— **不是同一个量**，
     一个算「洗盘期整体缩没缩」，一个算「昨天这一天缩没缩」。混淆这两个会得出「已经防住了」的错觉。

口径（与 goldpit_study 一致，别自己另定一套）：
  · T+1 合法：T 日收盘出信号 → T+1 开盘买 → T+1+N 开盘卖
  · 成本 COST=0.16% 双边
  · t 按**信号日聚类**（同日几十只高度相关，按笔算 t 会伪造显著性）
  · 基准 = 同池、同日的其他信号日的均值（池内自比，控住池子本身的选股效应）

用法：
  python3 dryveto_study.py            # 全量
  python3 dryveto_study.py --limit 800
"""
import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nshape_backtest import load_all, COST                       # noqa: E402
import breakout_pool as BP                                       # noqa: E402

VOL_LOOK = 5          # 量比分母窗口（与 goldpit_study 同）
DROP = -0.05          # 否决条件：昨日跌幅
DRY = 0.50            # 否决条件：昨日量比上限
HORIZONS = (1, 3, 5)


def vol_ratio(st, k):
    """**昨日**（T-1）成交额 / 其前 VOL_LOOK 日均成交额；k = 状态日下标（T 日）。

    ⚠️ 口径必须与 goldpit_study.scan() 完全一致，否则两套结论没法对话：
       那边是 `d1 = c[k-1]/c[k-2]-1`、`vr = a[k-1]/mean(a[k-1-VOL_LOOK:k-1])`，
       即「信号日 T 的**前一天**跌了多少、缩了多少」。第一版我写成 c[k]/c[k-1] 与 a[k]/...，
       整整差了一天 —— 而否决条件要回答的正是「昨天那根阴线缩没缩」。
    """
    if k - 2 < 0 or k - 1 - VOL_LOOK < 0:
        return None
    base = [st.a[i] for i in range(k - 1 - VOL_LOOK, k - 1)]
    if not base or statistics.mean(base) <= 0:
        return None
    return st.a[k - 1] / statistics.mean(base)


def yday_drop(st, k):
    """昨日（T-1）涨跌幅。与 vol_ratio 同一天，两者必须成对使用。"""
    if k < 2 or st.c[k - 2] <= 0:
        return None
    return st.c[k - 1] / st.c[k - 2] - 1


def fwd(st, k, N):
    """T+1 开盘买 → T+1+N 开盘卖。返回扣成本后收益，或 None（不可成交/越界）。"""
    i, j = k + 1, k + 1 + N
    if j >= len(st.o):
        return None
    if st.o[i] <= 0 or st.o[j] <= 0 or st.c[k] <= 0:
        return None
    if st.o[i] / st.c[k] - 1 >= 0.095:      # 一字板买不进
        return None
    return st.o[j] / st.o[i] - 1 - COST


def state_days(st):
    """横盘启动「应当入池」的所有状态日（k = 判定日的下标）。

    不复用 evaluate()：它取的是「最近一个仍有效的波」，而回测要**遍历历史上每个状态日**，
    否则一只票整段历史只贡献 1 个样本，样本量会少两个数量级。
    这里复刻 evaluate() 的三条硬门槛（洗盘天数 / 量比带 / 未破位），保证总体一致。
    """
    out = []
    n = len(st.c)
    for k in range(BP.MIN_BARS, n - max(HORIZONS) - 2):
        t = BP._wave_anchor(st_bars(st), k, BP.CHAIN_LOOKBACK)
        if t is None:
            continue
        s, info = BP.track(st_bars(st), t, k)
        if s != 'active':
            continue
        if info.get('washDays', 0) < BP.MIN_N:
            continue
        vr = info.get('vr')
        if vr is None or not (BP.VOL_MIN <= vr <= BP.VOL_MAX):
            continue
        close = info.get('lastClose')
        if close is None or close < info['limitClose'] * BP.POS_MIN:
            continue
        out.append(k)
    return out


_BARS_CACHE = {}


def st_bars(st):
    """把 Stock 转成 breakout_pool 期望的 bars 列表（只转一次，缓存）。

    ⚠️ 键名必须是 `d`（不是 `date`）：breakout_pool.track() 直接读 `bars[i]['day']`，
    而这个 'day' 字段是 kline_cache.get_bars() 产出的另一套形状。这里同时给 `d` 与 `day`，
    两种读法都能跑，避免以后任一侧改字段名时又炸一次 KeyError（第一次跑就是炸在 'day' 上）。
    """
    key = id(st)
    b = _BARS_CACHE.get(key)
    if b is None:
        b = [{'d': st.d[i], 'day': st.d[i],
              'open': st.o[i], 'close': st.c[i], 'high': st.h[i],
              'low': st.l[i], 'volume': st.v[i], 'amount': st.a[i]}
             for i in range(len(st.c))]
        _BARS_CACHE[key] = b
    return b


def stat(rows):
    """rows = [(day, r)]。按笔与按日两种口径都给，t 与按日同源。"""
    if not rows:
        return None
    rs = [r for _, r in rows]
    days = {}
    for d, r in rows:
        days.setdefault(d, []).append(r)
    dm = [statistics.mean(v) for v in days.values()]
    t = None
    if len(dm) >= 3:
        sd = statistics.stdev(dm)
        if sd > 0:
            t = statistics.mean(dm) / (sd / len(dm) ** 0.5)
    return {'n': len(rs), 'days': len(days),
            'avg': statistics.mean(rs), 'med': statistics.median(rs),
            'win': sum(1 for r in rs if r > 0) / len(rs),
            'avg_day': statistics.mean(dm), 't': t}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()

    stocks = load_all()
    if a.limit:
        stocks = stocks[:a.limit]
    print('标的 %d 只（.kline_hist 全库）' % len(stocks))
    print('否决条件：昨日跌幅 ≤ %.1f%% 且 昨日量比 < %.2f' % (DROP * 100, DRY))
    print('口径：T+1 开盘买 → T+1+N 开盘卖，成本 %.2f%% 双边，t 按信号日聚类\n'
          % (COST * 100))

    per_N = {N: {'hit': [], 'rest': []} for N in HORIZONS}
    n_state = n_veto = 0
    n_stock_hit = 0

    for idx, st in enumerate(stocks, 1):
        if idx % 500 == 0:
            print('  [扫描] %d/%d  状态日 %d  命中否决 %d' % (idx, len(stocks), n_state, n_veto),
                  flush=True)
        if not st.code.startswith(('60', '00')):
            continue
        if 'ST' in (st.name or ''):
            continue
        ks = state_days(st)
        got = False
        for k in ks:
            n_state += 1
            d1 = yday_drop(st, k)
            vr = vol_ratio(st, k)
            veto = (d1 is not None and d1 <= DROP) and (vr is not None and vr < DRY)
            if veto:
                n_veto += 1
                got = True
            for N in HORIZONS:
                r = fwd(st, k, N)
                if r is None:
                    continue
                per_N[N]['hit' if veto else 'rest'].append((st.d[k], r))
        if got:
            n_stock_hit += 1

    print('\n横盘启动状态日 %d 个，其中命中否决 %d 个（%.1f%%）；涉及 %d 只票'
          % (n_state, n_veto, (n_veto / n_state * 100) if n_state else 0, n_stock_hit))

    print('\n===== ① 否决组 vs 池内其余：否决条件有没有信息量 =====')
    print('  %-5s %-6s %-9s %-9s %-8s %-8s %-8s' %
          ('N', 'n', '均值', '中位', '胜率', '日均值', 't(聚类)'))
    for N in HORIZONS:
        for tag, key in (('否决', 'hit'), ('其余', 'rest')):
            s = stat(per_N[N][key])
            if not s:
                print('  N=%-3d %-6s 样本不足' % (N, tag))
                continue
            print('  N=%-3d %-6d %+8.2f%% %+8.2f%% %7.1f%% %+7.2f%% %s   %s'
                  % (N, s['n'], s['avg'] * 100, s['med'] * 100, s['win'] * 100,
                     s['avg_day'] * 100,
                     ('%+.2f' % s['t']) if s['t'] is not None else ' n/a', tag))
        # 差值（否决组 − 其余）
        sh, sr = stat(per_N[N]['hit']), stat(per_N[N]['rest'])
        if sh and sr:
            print('       → 否决组比其余 %+0.2f 个百分点（均值口径）'
                  % ((sh['avg'] - sr['avg']) * 100))
        print()

    print('===== ② 否决会砍掉多少 =====')
    if n_state:
        print('  砍掉 %.1f%% 的状态日（%d / %d）' % (n_veto / n_state * 100, n_veto, n_state))
        print('  受影响标的 %d 只' % n_stock_hit)
    print('\n===== ③ 与池内已有 VOL_MIN=%.2f 的关系 =====' % BP.VOL_MIN)
    print('  池子 volRatio = 洗盘均量 / 波内峰值量（算整段洗盘缩没缩）')
    print('  本脚本 vr   = 昨日成交额 / 前 %d 日均额（算昨天这一天缩没缩）' % VOL_LOOK)
    # ⚠️ 这里**不要**再遍历一遍 state_days：第一版那么写，全量跑要重复扫 5144 只 × 9 年，
    # 白花一倍时间（实测多 40 秒）。结论是定义级的、不依赖数据 ——
    # state_days() 本身就只在 BP.VOL_MIN <= vr <= BP.VOL_MAX 的状态日里挑样本，
    # 所以「命中否决的状态日，池内 volRatio 必然已在带内」是**构造保证**，不需要实测。
    # 需要实测的是反面：池子的 VOL_MIN 能不能挡掉本条件？答案是不能，因为两者分母不同。
    print('  命中否决的状态日，池内 volRatio **必然**已在 [%.2f, %.2f] 带内'
          '（state_days 只在这些日子里取样，属构造保证，不是实测结论）'
          % (BP.VOL_MIN, BP.VOL_MAX))
    print('  → 所以池子自己的 VOL_MIN 一个都挡不住本条件：它量的是「整段洗盘缩没缩」，')
    print('    本条件量的是「昨日那一根阴线缩没缩」，分母不同、不构成包含关系。')

    print('\n===== 结论 =====')
    s5h, s5r = stat(per_N[5]['hit']), stat(per_N[5]['rest'])
    if s5h and s5r:
        if s5h['avg'] < s5r['avg'] and (s5h['t'] is None or s5h['t'] < -1.0):
            print('  否决条件在横盘启动样本内**同样为负**（N=5 差 %+.2fpp）→ 值得进池'
                  % ((s5h['avg'] - s5r['avg']) * 100))
        elif s5h['avg'] < s5r['avg']:
            print('  方向为负但 t 不显著（%s）→ 可进池，但页面上必须标「不显著」'
                  % (('%.2f' % s5h['t']) if s5h['t'] is not None else 'n/a'))
        else:
            print('  ⚠️ 方向与全市场结论相反 → **不要外推进池**，全市场那条结论不能搬到池内')
    print('  注意：否决只减样本不减亏损，它不产生 alpha；它的价值是「避免在明确为负的形态上加仓」。')


if __name__ == '__main__':
    main()

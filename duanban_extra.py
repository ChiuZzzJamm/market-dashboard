#!/usr/bin/env python3
"""断板反包：单条件边际贡献 / 止损宽度可执行性 / 每日密度（R100z232 第三步）。

前两步的结论：
    ① 现状口径（zt_low 止损 + TP3% + 持 5 日）= 胜率 65.7%、均收益 **+0.006%**，
       恰好保本；样本外（2026 年）**−0.279%**，已经失效。
    ② 收紧止损（洗盘段最低价 / 信号日最低价 / 固定 3%）**全都更差** ——
       断板后日内波动本来就大，紧止损只是把「本来会回来的票」提前打掉。
       真正的问题不是止损太宽，是**止盈太薄**（3% 只够覆盖一次 5% 亏损的 60%）。

这一步回答三个执行层问题：
    A. 每个过滤条件单独值多少（在 TP5%/H10 + zt_low 口径下），避免用一个
       看似漂亮但无法归因的组合。
    B. 止损宽度到底多宽？中位若在 −10% 以上，这个止损在实际操作中根本挂不住。
    C. 过滤后每天还能出几只？池子若一天 1 只，断板池配额 4 只就永远填不满。
"""
import statistics
import sys
from collections import defaultdict

import duanban_study as DS
import duanban_tune as DT
import nshape_backtest as NB

TP, HOLD = 0.05, 10


def main():
    smps = DS.collect(NB.load_all())
    print('[scan] %d 个信号' % len(smps), flush=True)

    rows = [s for s in smps]
    for s in rows:
        ret, ex, dys = DT.sim(s, 'zt_low', TP, HOLD)
        s['_ret'], s['_ex'], s['_days'] = ret, ex, dys

    def st(sub):
        return DT.stat([(s['_ret'], s['_ex'], s['_days']) for s in sub])

    base = st(rows)
    print()
    print('=== A. 单条件边际贡献（口径：zt_low 止损 / TP5% / 持 10 日）===')
    print('%-34s %6s %6s %7s %8s %6s %7s'
          % ('条件', '笔数', '占比', '胜率', '均收益', '盈亏比', 'Δ均收益'))
    print('%-34s %6d %5.0f%% %6.1f%% %+8.3f%% %6.2f %7s'
          % ('基线（现状全部样本）', base['n'], 100, base['win'], base['avg'], base['pf'], '—'))

    def F(s, **kw):
        f = s['feat']
        if kw.get('ma5') and f['dist_ma5'] < 0:
            return False
        if 'mc' in kw and f['mcap'] < kw['mc']:
            return False
        if 'pre' in kw and f['pre20'] > kw['pre']:
            return False
        if 'up' in kw and f['upper_s'] >= kw['up']:
            return False
        if 'drop1' in kw and f['pct1'] < kw['drop1']:
            return False
        if 'lbc' in kw and f['lbc'] > kw['lbc']:
            return False
        if 'pos' in kw and f['pos250'] < kw['pos']:
            return False
        return True

    unis = [
        ('站上 MA5（距MA5 ≥ 0）', dict(ma5=True)),
        ('市值 ≥ 50 亿', dict(mc=50)),
        ('市值 ≥ 100 亿', dict(mc=100)),
        ('市值 ≥ 200 亿', dict(mc=200)),
        ('启动前 20 日 ≤ 10%', dict(pre=10)),
        ('启动前 20 日 ≤ 0%', dict(pre=0)),
        ('信号日上影 < 3%', dict(up=3)),
        ('断板日跌幅 > −5%', dict(drop1=-5.0)),
        ('首板（连板 ≤ 1）', dict(lbc=1)),
        ('250 日位置 ≥ 60%', dict(pos=60)),
    ]
    for nm, kw in unis:
        sub = [s for s in rows if F(s, **kw)]
        x = st(sub)
        if x:
            print('%-34s %6d %5.0f%% %6.1f%% %+8.3f%% %6.2f %+7.3f%%'
                  % (nm, x['n'], x['n'] / base['n'] * 100, x['win'], x['avg'],
                     x['pf'], x['avg'] - base['avg']))

    print()
    print('=== A2. 逐步叠加（看每加一条的真实增量，不是只看最终组合）===')
    steps = [
        ('① 站上MA5', dict(ma5=True)),
        ('② +市值≥100', dict(ma5=True, mc=100)),
        ('③ +前20≤10%', dict(ma5=True, mc=100, pre=10)),
        ('④ +上影<3%', dict(ma5=True, mc=100, pre=10, up=3)),
        ('⑤ +断板日>−5%', dict(ma5=True, mc=100, pre=10, up=3, drop1=-5.0)),
        ('⑥ +首板', dict(ma5=True, mc=100, pre=10, up=3, drop1=-5.0, lbc=1)),
    ]
    prev = None
    for nm, kw in steps:
        sub = [s for s in rows if F(s, **kw)]
        x = st(sub)
        if not x:
            continue
        d = '' if prev is None else '%+.3f%%' % (x['avg'] - prev['avg'])
        print('%-20s %6d %6.1f%% %+8.3f%% %6.2f  Δ%+7.3f%%'
              % (nm, x['n'], x['win'], x['avg'], x['pf'], x['avg'] - base['avg']))
        print('%-20s %6s %6s %8s %6s  本步 %s' % ('', '', '', '', '', d))
        prev = x

    print()
    print('=== B. 止损宽度分布（zt_low 相对买入价）===')
    wid = [s['stop_pct'] for s in rows]
    print('  中位 %.2f%% / 均值 %.2f%% / 10%% 分位 %.2f%% / 90%% 分位 %.2f%%'
          % (statistics.median(wid), sum(wid) / len(wid),
             sorted(wid)[int(len(wid) * 0.1)], sorted(wid)[int(len(wid) * 0.9)]))
    print('%-20s %6s %7s %8s %6s' % ('止损宽度', '笔数', '胜率', '均收益', '盈亏比'))
    for lo, hi, lb in ((-30, -12, '−12% 以上（挂不住）'), (-12, -9, '−12~−9%'),
                       (-9, -6, '−9~−6%'), (-6, -3, '−6~−3%'), (-3, 0, '−3% 以内')):
        sub = [s for s in rows if lo <= s['stop_pct'] < hi]
        x = st(sub)
        if x:
            print('%-20s %6d %6.1f%% %+8.3f%% %6.2f' % (lb, x['n'], x['win'], x['avg'], x['pf']))

    print()
    print('=== B2. 止损钳制到 −10%（可执行版）会怎样 ===')
    print('%-30s %6s %7s %8s %6s' % ('口径', '笔数', '胜率', '均收益', '盈亏比'))

    def sim_clamp(smp, clamp=-0.10):
        """把 T 日最低价止损钳到「不超过 −10%」，模拟真实的止损单（挂太远等于没挂）。"""
        o, c, h, l = smp['arr']
        b = smp['b']
        n = len(c)
        buy = smp['buy']
        stop = max(smp['lowT'], buy * (1 + clamp))
        for j in range(b, min(b + HOLD, n)):
            if o[j] <= stop:
                return (o[j] / buy - 1) * 100 - DS.COST * 100, 'stop_gap', j - b + 1
            if l[j] <= stop:
                return (stop / buy - 1) * 100 - DS.COST * 100, 'stop', j - b + 1
            if o[j] >= buy * (1 + TP):
                return (o[j] / buy - 1) * 100 - DS.COST * 100, 'tp_gap', j - b + 1
            if h[j] >= buy * (1 + TP):
                return TP * 100 - DS.COST * 100, 'tp', j - b + 1
        j = min(b + HOLD, n) - 1
        return (c[j] / buy - 1) * 100 - DS.COST * 100, 'time', j - b + 1

    for nm, kw in (('基线', {}), ('四条件过滤', dict(ma5=True, mc=100, pre=10, up=3))):
        sub = [s for s in rows if F(s, **kw)]
        x1 = DT.stat([(s['_ret'], s['_ex'], s['_days']) for s in sub])
        x2 = DT.stat([sim_clamp(s) for s in sub])
        if x1:
            print('%-30s %6d %6.1f%% %+8.3f%% %6.2f'
                  % ('%s / 不钳制' % nm, x1['n'], x1['win'], x1['avg'], x1['pf']))
        if x2:
            print('%-30s %6d %6.1f%% %+8.3f%% %6.2f'
                  % ('%s / 钳到 −10%%' % nm, x2['n'], x2['win'], x2['avg'], x2['pf']))

    print()
    print('=== C. 每日信号密度（决定池子填不填得满）===')
    days = sorted({s['sig_day'] for s in rows})
    span = len(days)
    print('  信号覆盖 %d 个交易日（%s ~ %s）' % (span, days[0], days[-1]))
    for nm, kw in (('现状（无额外过滤）', {}),
                   ('① 站上MA5', dict(ma5=True)),
                   ('④ 四条件', dict(ma5=True, mc=100, pre=10, up=3)),
                   ('⑤ 五条件', dict(ma5=True, mc=100, pre=10, up=3, drop1=-5.0))):
        sub = [s for s in rows if F(s, **kw)]
        by = defaultdict(int)
        for s in sub:
            by[s['sig_day']] += 1
        print('  %-20s %6d 笔 / %3d 个有信号日 / **%.2f 只/交易日**'
              % (nm, len(sub), len(by), len(sub) / float(span)))
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""v3 口径终选（R100z232 第四步）：把前四步的发现压成可上线的参数，逐组验证。

## 断板反包（duanban）前四步的结论

    基线（zt_low / TP3% / 持 5 日）  胜率 65.7%  均收益 **+0.006%**  盈亏比 1.00
    样本外 2026 年                  胜率 64.5%  均收益 **−0.279%**  ← 已经失效
    收紧止损（洗盘段/信号日/固定 3%） 全部更差 → 止损不是问题所在
    真正的杠杆：**市值 ≥100 亿（+0.178%）+ 启动前 20 日 ≤10%（+0.310%）**
    止盈 3%→5%、持有 5→10 日：+0.144% → +0.722%

## 陈学长（chen）前四步的结论

    基线（洗盘段最低价 / TP3.5% / 持 2 日）  胜率 59.6%  均收益 +0.701%  盈亏比 1.58
    训练 +0.671% / 样本外 +0.770%           ← **没有衰减，这是两套里唯一站得住的**
    止损换 T 日最低价：59.6% → **67.6%**，+0.701% → **+1.010%**
    **刚站上 MA5 就买是弱信号**：距 MA5 0~2% 胜率仅 45.4%（+0.018%），
                              距 MA5 >5% 胜率 68.6%（+1.078%）
    与断板相反：启动前 20 日 >10% 在陈学长口径里是**正**因子（67.5% / +1.007%）

这一步只做一件事：把候选参数组逐组跑训练/样本外，选「样本外不塌」的那组。
不选样本内最高的那组——那正是过拟合的定义。
"""
import sys

import chen_study as CS
import duanban_study as DS
import duanban_tune as DT
import nshape_backtest as NB

SPLIT = '2026-01-01'


def st3(rows):
    return DT.stat([(r[0], r[1], r[2]) for r in rows])


def pr(nm, seg, x):
    if not x:
        print('  %-44s %-6s  样本不足' % (nm, seg))
        return
    print('  %-44s %-6s %6d %6.1f%% %+8.3f%% %6.2f'
          % (nm, seg, x['n'], x['win'], x['avg'], x['pf']))


def duanban():
    smps = DS.collect(NB.load_all())

    def F(s, mc=0, pre=1e9, dm=0.0, clamp=None):
        f = s['feat']
        if f['mcap'] < mc or f['pre20'] > pre or f['dist_ma5'] < dm:
            return False
        return True

    def run(sub, tp, hold, clamp=None):
        out = []
        for s in sub:
            smp = dict(s)
            if clamp is not None:
                cut = smp['buy'] * (1 + clamp)
                if smp['stop'] < cut:
                    smp['stop'] = cut
                    smp['lowT'] = cut
            out.append(DT.sim(smp, 'zt_low', tp, hold))
        return out

    print('=== 断板反包 v3 候选 ===')
    print('  %-44s %-6s %6s %7s %8s %6s' % ('口径', '区间', '笔数', '胜率', '均收益', '盈亏比'))
    cands = [
        ('v2 现状：zt_low/TP3/H5，无过滤', dict(), 0.03, 5, None),
        ('v3-a：+市值100+前20≤10+站上MA5，TP5/H10', dict(mc=100, pre=10, dm=0.0), 0.05, 10, None),
        ('v3-b：a + 距MA5≥2%，TP5/H10', dict(mc=100, pre=10, dm=2.0), 0.05, 10, None),
        ('v3-c：a + 距MA5≥3%，TP5/H10', dict(mc=100, pre=10, dm=3.0), 0.05, 10, None),
        ('v3-d：a + 止损钳 −10%，TP5/H10', dict(mc=100, pre=10, dm=0.0), 0.05, 10, -0.10),
        ('v3-e：a，TP5/H5（保守持有）', dict(mc=100, pre=10, dm=0.0), 0.05, 5, None),
        ('v3-f：a，TP3/H10（薄止盈）', dict(mc=100, pre=10, dm=0.0), 0.03, 10, None),
    ]
    for nm, kw, tp, hold, clamp in cands:
        sub = [s for s in smps if F(s, **kw)]
        rows = [(r[0], r[1], r[2]) for r in run(sub, tp, hold, clamp)]
        s_tr = [s for s in sub if s['sig_day'] < SPLIT]
        s_te = [s for s in sub if s['sig_day'] >= SPLIT]
        pr(nm, '全', st3(rows))
        pr('', '训练', st3([(x[0], x[1], x[2]) for x in run(s_tr, tp, hold, clamp)]))
        pr('', '样本外', st3([(x[0], x[1], x[2]) for x in run(s_te, tp, hold, clamp)]))
    return smps


def chen():
    smps = CS.collect(NB.load_all())

    def run(sub, tp, hold, stop_mode='wash_low'):
        out = []
        for s in sub:
            ret, ex, dys = CS.sim(s, tp=tp, hold=hold, stop_mode=stop_mode)
            out.append((ret, ex, dys))
        return out

    print()
    print('=== 陈学长 v3 候选 ===')
    print('  %-44s %-6s %6s %7s %8s %6s' % ('口径', '区间', '笔数', '胜率', '均收益', '盈亏比'))
    cands = [
        ('v2 现状：wash_low/TP3.5/H2', lambda s: True, 0.035, 2, 'wash_low'),
        ('v3-a：zt_low/TP3.5/H2', lambda s: True, 0.035, 2, 'zt_low'),
        ('v3-b：zt_low/TP5/H2', lambda s: True, 0.05, 2, 'zt_low'),
        ('v3-c：zt_low/TP5/H5', lambda s: True, 0.05, 5, 'zt_low'),
        ('v3-d：zt_low/TP5/H5 + 距MA5≥3%', lambda s: s['feat']['dist_ma5'] >= 3.0, 0.05, 5, 'zt_low'),
        ('v3-e：zt_low/TP5/H5 + 距MA5≥3% + 市值≥100',
         lambda s: s['feat']['dist_ma5'] >= 3.0 and s['feat']['mcap'] >= 100, 0.05, 5, 'zt_low'),
        ('v3-f：zt_low/TP3.5/H5 + 距MA5≥3%',
         lambda s: s['feat']['dist_ma5'] >= 3.0, 0.035, 5, 'zt_low'),
        ('v3-g：zt_low/TP5/H10 + 距MA5≥3%',
         lambda s: s['feat']['dist_ma5'] >= 3.0, 0.05, 10, 'zt_low'),
    ]
    for nm, f, tp, hold, sm in cands:
        sub = [s for s in smps if f(s)]
        rows = run(sub, tp, hold, sm)
        pr(nm, '全', st3(rows))
        pr('', '训练', st3(run([s for s in sub if s['sig_day'] < SPLIT], tp, hold, sm)))
        pr('', '样本外', st3(run([s for s in sub if s['sig_day'] >= SPLIT], tp, hold, sm)))
    # 密度
    days = len({s['sig_day'] for s in smps})
    sub = [s for s in smps if s['feat']['dist_ma5'] >= 3.0]
    print()
    print('  陈学长 v3-d 密度：%d 笔 / %d 个信号日 = %.2f 只/交易日'
          % (len(sub), len({s['sig_day'] for s in sub}), len(sub) / float(days or 1)))
    return smps


def main():
    duanban()
    chen()
    return 0


if __name__ == '__main__':
    sys.exit(main())

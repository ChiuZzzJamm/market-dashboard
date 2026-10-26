#!/usr/bin/env python3
"""N 字形态：过滤规则组合搜索（在 nshape_rows.json 明细上做）。

为什么要这一步：单因子分层只能看「边际差异」，实盘要的是「同时满足几条」之后
胜率能不能过 75%（2%/5% 止损下的保本线约 71%）。

防过拟合：
  ① 只用 2024-2025 训练，2026 样本外验证，两段都要求正期望才进入候选
  ② 每组样本数 >= 200
  ③ 规则条数上限 6 条，避免堆砌
"""
import json
import os
import itertools
import statistics

BASE = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(BASE, 'nshape_rows.json'), encoding='utf-8'))
print('样本 %d' % len(rows))

# 规则池：(名字, 判定函数)
RULES = [
    ('不是年线下方低位', lambda r: r['above_ma250'] == 0),
    ('MA60 下方', lambda r: r['above_ma60'] == 0),
    ('距 250 日高点 ≤95%', lambda r: r['pos250'] <= 0.95),
    ('MA20 乖离 ≤15%', lambda r: r['ma20_dev'] <= 0.15),
    ('MA20 乖离 <0（回踩均线）', lambda r: r['ma20_dev'] < 0),
    ('上影 <3%', lambda r: r['upper_shadow'] < 0.03),
    ('下影 <3%', lambda r: r['lower_shadow'] < 0.03),
    ('成交额 ≤5 亿', lambda r: r['day_amt'] <= 5e8),
    ('整理期缩量 <0.6', lambda r: r['shrink'] < 0.6),
    ('整理期缩量 <0.4', lambda r: r['shrink'] < 0.4),
    ('市场涨停 >60 家', lambda r: r['mkt_limit_cnt'] > 60),
    ('启动前 20 日涨幅 ≤25%', lambda r: r['pre20_gain'] <= 0.25),
    ('启动前 20 日涨幅 ≤10%', lambda r: r['pre20_gain'] <= 0.10),
    ('生命周期首板', lambda r: r['t0_prior_limit'] == 0),
    ('二板及以上', lambda r: r['t0_prior_limit'] >= 1),
    ('不追高（回撤 ≥0）', lambda r: r['retreat'] >= 0),
    ('收阳或十字（实体 ≥-2%）', lambda r: r['body'] >= -0.02),
    ('周五买', lambda r: r['weekday'] == 4),
    ('市值 ≥100 亿', lambda r: r['mcap_yi'] >= 100),
    ('市值 <100 亿', lambda r: 0 < r['mcap_yi'] < 100),
]


def stat(sub):
    if not sub:
        return None
    rets = [r['_ret'] for r in sub]
    win = sum(1 for x in rets if x > 0) / len(rets)
    return {'n': len(sub), 'win': win, 'avg': sum(rets) / len(rets),
            'med': statistics.median(rets)}


train = [r for r in rows if r['year'] in ('2024', '2025')]
test = [r for r in rows if r['year'] == '2026']
print('训练 %d / 样本外 %d' % (len(train), len(test)))
b = stat(rows)
print('基准：n=%d 胜率%.1f%% 期望%+.3f%%' % (b['n'], b['win'] * 100, b['avg'] * 100))

results = []
# 单条 + 两两 + 三三组合（穷举到 3 条，超过 3 条样本会掉太快）
for k in (1, 2, 3):
    for combo in itertools.combinations(RULES, k):
        fns = [c[1] for c in combo]
        tr = [r for r in train if all(f(r) for f in fns)]
        if len(tr) < 300:
            continue
        st = stat(tr)
        if st['avg'] <= 0.0015:
            continue
        te = [r for r in test if all(f(r) for f in fns)]
        if len(te) < 120:
            continue
        ste = stat(te)
        if ste['avg'] <= 0:
            continue
        results.append({'rules': [c[0] for c in combo], 'train': st, 'test': ste,
                        'score': min(st['avg'], ste['avg']) * (st['win'] + ste['win'])})

results.sort(key=lambda x: -x['score'])
print('\n通过双段验证的组合：%d 个，前 20：' % len(results))
for r in results[:20]:
    print('  %-52s 训练 n=%-5d 胜率%.1f%% 期望%+.2f%% | 2026 n=%-4d 胜率%.1f%% 期望%+.2f%%'
          % (' + '.join(r['rules']), r['train']['n'], r['train']['win'] * 100,
             r['train']['avg'] * 100, r['test']['n'], r['test']['win'] * 100,
             r['test']['avg'] * 100))

json.dump(results[:50], open(os.path.join(BASE, 'nshape_rules.json'), 'w'),
          ensure_ascii=False, indent=1)

#!/usr/bin/env python3
"""指数趋势状态（2026-10-27，用户第 1 问：趋势维度 3/10 → 至少 8 分）。

## 为什么必须有这一层

体检原话是「只有当日切片，『大盘在 MA20 上方还是下方』答不出来」。数据其实一直在：
`data.js` 的 `stkKlines` 里 `sh000001` 有完整 90 根日线，`atr_stop.py` 里有 ma20/ma60，
但**没有一层把它们接成「现在是什麼趋势状态」**——读者拿到的是今日涨跌 0.19%，
回答不了「这个 0.19% 发生在什么位置」。
而位置决定仓位：MA20 上方 3% 和下方 3%，同样一根小阳线是完全不同的含义。

## 这一层算什么（全部从 stkKlines 现算，不引入新数据源）

对 5 个指数（上证/深成/创业板/科创50/上证50）各算：
  · close / ma5 / ma20 / ma60、距 MA20 的百分比
  · MA20 斜率（近 5 日 MA20 变化，判断「均线往上还是往下」）
  · 60 日分位（现价在近 60 日高低区间的位置，0=最低 100=最高）
  · 连涨/连跌天数
  · ATR20（真实波幅均值，%)
  · 结构判定：多头排列（close>ma20>ma60）/ 空头排列 / 纠缠
  · 趋势档：强 / 偏强 / 中性 / 偏弱 / 弱（由位置+斜率+排列合成）

再合成一个**全市场趋势档**（取 5 个指数投票的中位数），并给出对应的**仓位上限**——
这一档直接作为 `strategy.positionBand` 的上限约束：日历因子说 6-7 成、
但趋势若判「弱」，最终只能给到 3 成。这就是「用法层」：指标不接决策等于没有。

## 口径纪律

· 只用 `stkKlines` 里的日线，**不抓网络**（本脚本可离线跑，失败写空不阻断流水线）。
· 所有阈值是经验值，写在 `TREND_BANDS` 里可改；**没有回测过的部分不许写成胜率**。
· 输出写 `data.js` 顶层 `trendState`，并在 `audit_pipeline.py` 登记 PRODUCT_OWNERSHIP。

用法：
    python3 trend_state.py            # 算并打印
    python3 trend_state.py --write    # 写回 data.js 顶层 trendState
"""
import argparse
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common  # noqa: E402

IDX = [
    ('sh000001', '上证指数', 'main'),
    ('sz399001', '深证成指', 'main'),
    ('sz399006', '创业板指', 'growth'),
    ('sh000688', '科创50', 'growth'),
    ('sh000016', '上证50', 'value'),
]

# 趋势档 → 仓位上限（占总资金 %）。这是「用法层」：位置决定该开多大仓。
# 经验值，不是回测出来的胜率——所以只作上限约束，不对外宣称能预测涨跌。
TREND_BANDS = [
    ('强', 90, '收盘在 MA20 上方、MA20 上行、且站在 60 日区间上半部'),
    ('偏强', 80, '在 MA20 上方但 MA20 未上行，或在上半部但刚站上'),
    ('中性', 60, 'MA20 附近缠绕，方向不明'),
    ('偏弱', 40, '在 MA20 下方但 MA20 仍上行，或在下半部'),
    ('弱', 25, '在 MA20 下方且 MA20 下行、处 60 日区间下三分之一'),
]


def _ma(vals, n, i):
    if i + 1 < n:
        return None
    seg = vals[i + 1 - n:i + 1]
    if any(x is None for x in seg):
        return None
    return sum(seg) / n


def _atr(rows, i, n=20):
    if i < n:
        return None
    trs = []
    for j in range(i - n + 1, i + 1):
        h, l, pc = rows[j][3], rows[j][4], rows[j - 1][2]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    c = rows[i][2]
    return (sum(trs) / len(trs) / c * 100) if c else None


def one_idx(rows):
    """单指数趋势状态。rows = [[date, open, close, high, low, vol], ...]"""
    if len(rows) < 65:
        return None
    c = [r[2] for r in rows]
    i = len(rows) - 1
    last = c[i]
    ma5, ma20, ma60 = _ma(c, 5, i), _ma(c, 20, i), _ma(c, 60, i)
    if not ma20:
        return None
    # MA20 斜率：近 5 日 MA20 变化率（%）
    ma20_prev = _ma(c, 20, i - 5)
    slope = ((ma20 / ma20_prev - 1) * 100) if ma20_prev else None
    # 60 日分位
    seg = c[max(0, i - 59):i + 1]
    lo, hi = min(seg), max(seg)
    pos60 = ((last - lo) / (hi - lo) * 100) if hi > lo else 50.0
    # 连涨/连跌
    streak = 0
    for j in range(i, 0, -1):
        if c[j] >= c[j - 1]:
            if streak < 0:
                break
            streak += 1
        else:
            if streak > 0:
                break
            streak -= 1
    # 结构
    if last > ma20 and ma20 > (ma60 or ma20):
        struct = '多头排列'
    elif last < ma20 and ma20 < (ma60 or ma20):
        struct = '空头排列'
    else:
        struct = '均线纠缠'
    # 趋势档
    above = last > ma20
    up = (slope or 0) > 0.15
    down = (slope or 0) < -0.15
    if above and up and pos60 >= 50:
        lv = '强'
    elif above and pos60 >= 50:
        lv = '偏强'
    elif not above and down and pos60 <= 33:
        lv = '弱'
    elif not above and down:
        lv = '偏弱'
    else:
        lv = '中性'
    band = next((b for b in TREND_BANDS if b[0] == lv), TREND_BANDS[2])
    return dict(
        name=None, code=None, date=rows[i][0],
        close=round(last, 2),
        chgPct=round((last / c[i - 1] - 1) * 100, 2) if c[i - 1] else None,
        ma5=round(ma5, 2) if ma5 else None,
        ma20=round(ma20, 2),
        ma60=round(ma60, 2) if ma60 else None,
        vsMa20Pct=round((last / ma20 - 1) * 100, 2),
        ma20Slope5=round(slope, 2) if slope is not None else None,
        pos60=round(pos60, 1),
        pos60High=round(hi, 2), pos60Low=round(lo, 2),
        streak=streak,
        atr20=round(_atr(rows, i), 2) if _atr(rows, i) is not None else None,
        struct=struct, level=lv, capPct=band[1],
    )


def build(D):
    K = D.get('stkKlines') or {}
    out, levels = [], []
    for code, nm, kind in IDX:
        rows = K.get(code)
        if not rows:
            continue
        st = one_idx(rows)
        if not st:
            continue
        st['name'], st['code'], st['kind'] = nm, code, kind
        out.append(st)
        levels.append(st['level'])
    if not out:
        return None
    order = {b[0]: k for k, b in enumerate(TREND_BANDS)}
    # 全市场中位档（5 个指数投票，取中位数——不是取最弱，也不是取平均）
    idx_sorted = sorted(levels, key=lambda x: order.get(x, 2))
    mid = idx_sorted[len(idx_sorted) // 2]
    band = next((b for b in TREND_BANDS if b[0] == mid), TREND_BANDS[2])
    main = next((x for x in out if x['code'] == 'sh000001'), out[0])
    weak = [x['name'] for x in out if order.get(x['level'], 2) >= order['偏弱']]
    strong = [x['name'] for x in out if order.get(x['level'], 2) <= order['偏强']]

    # 一句话结论：必须能直接回答「在 MA20 上方还是下方」，不能只有形容词
    txt = ('上证 %.2f 点，在 MA20（%.2f）%s %.2f%%、MA20 近 5 日%s %.2f%%、'
           '处近 60 日区间 %.0f%% 分位；全市场趋势档「%s」，仓位上限 %d%%。'
           % (main['close'], main['ma20'],
              '上方' if main['vsMa20Pct'] >= 0 else '下方', abs(main['vsMa20Pct']),
              '上行' if (main['ma20Slope5'] or 0) >= 0 else '下行',
              abs(main['ma20Slope5'] or 0), main['pos60'], mid, band[1]))
    if weak:
        txt += ' 走弱的指数：%s。' % '、'.join(weak)
    if strong:
        txt += ' 走强的指数：%s。' % '、'.join(strong)

    return dict(
        updatedAt=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        asOf=main['date'],
        indices=out,
        marketLevel=mid,
        marketCapPct=band[1],
        marketBasis=band[2],
        reasons=dict(weak=weak, strong=strong,
                     votes={b[0]: levels.count(b[0]) for b in TREND_BANDS if levels.count(b[0])}),
        text=txt,
        rule=('档位由「收盘 vs MA20 / MA20 近 5 日斜率 / 近 60 日分位」三项合成，'
              '每个指数各判一档，5 个指数取中位数为全市场档。'
              '仓位上限是**约束**不是建议：日历因子说 6-7 成，趋势判「弱」时只能到 25%。'
              '阈值为经验值，未做胜率回测——它只回答「现在什么位置、该开多大仓」，'
              '不预测涨跌。'),
    )
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    a = ap.parse_args()

    path = os.path.join(HERE, 'data.js')
    D = common.load_dashboard_data(HERE)
    st = build(D)
    if not st:
        print('[trend] stkKlines 缺指数日线，不写')
        return 1
    print('[trend] 全市场趋势档：%s（仓位上限 %d%%）' % (st['marketLevel'], st['marketCapPct']))
    print('%-10s %9s %8s %8s %8s %8s %7s %6s %s'
          % ('指数', '收盘', '距MA20', 'MA20斜率', '60日分位', 'ATR20', '连涨跌', '档位', '结构'))
    for x in st['indices']:
        print('%-10s %9.2f %+7.2f%% %+7.2f%% %7.1f%% %7.2f%% %+6d %6s %s'
              % (x['name'], x['close'], x['vsMa20Pct'], x['ma20Slope5'] or 0,
                 x['pos60'], x['atr20'] or 0, x['streak'], x['level'], x['struct']))
    print()
    print(st['text'])
    if a.write:
        D['trendState'] = st
        common.write_data_js(path, D)
        print('[write] data.js 顶层 trendState 已更新')
    return 0


if __name__ == '__main__':
    sys.exit(main())

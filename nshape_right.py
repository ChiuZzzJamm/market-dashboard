#!/usr/bin/env python3
"""N 字形态的「右侧」：突破确认日买入 + 突破成功率统计。

补齐前面的缺口：之前所有回测都是「整理期左侧买」（T0 后第 d 天），
没有回答「等它真的反包了再买」这件事值不值。

定义：
  锚点 T0：涨停/大阳 + 放量（同 nshape_backtest.find_setups）
  整理期：T0+1..T0+4，收盘 ≥ T0 实体中位、不破 T0 开盘价、不破 MA5 3%、量 ≤ T0
  突破 b：T0 之后首次「收盘 > T0 最高价」且当日涨幅 ≥ 5%
  可买性：b 日收盘涨停（涨幅 ≥ 涨停幅度-0.3%）→ 记「买不进」，单列不计入收益
"""
import json
import os
import sys
import statistics
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nshape_backtest import load_all, limit_pct, BASE
from nshape_bt import sim2, detail, line


def setups_right(st, max_wait=10):
    """返回 (b0, brk_stats)：b0 为突破日 [(idx, t0)]，brk_stats 为 (总锚点数, 突破数)。"""
    n = len(st.c)
    lp = limit_pct(st.code, st.name)
    out = []
    total = 0
    for t0 in range(260, n - 15):
        if not st.gap_ok(t0):
            continue
        if st.pct(t0) < lp:
            continue
        a0 = st.a[t0]
        base = st.a[t0 - 5:t0]
        if not base or a0 <= 0 or a0 < statistics.mean(base) * 1.5:
            continue
        mid = (st.o[t0] + st.c[t0]) / 2.0
        # 先验证整理期有效（最多 4 天）
        ok_days = 0
        for k in range(t0 + 1, min(t0 + 5, n)):
            if st.c[k] < mid or st.l[k] < st.o[t0] or st.a[k] > a0:
                break
            if st.ma5[k] and st.c[k] < st.ma5[k] * 0.97:
                break
            ok_days += 1
        if ok_days == 0:
            continue
        total += 1
        # 突破：整理期内或其后 10 日内首次收盘 > T0 最高价
        for k in range(t0 + 1, min(t0 + 1 + max_wait, n)):
            if st.c[k] > st.h[t0] and st.pct(k) >= 0.05:
                out.append((k, t0, ok_days))
                break
            if st.c[k] < st.o[t0] * 0.97:      # 破位，锚点作废
                break
    return out, total


def main():
    stocks = load_all()
    print('[load] %d 只' % len(stocks), flush=True)
    brks, total_anchor = [], 0
    for si, st in enumerate(stocks):
        o, tot = setups_right(st)
        total_anchor += tot
        for (k, t0, okd) in o:
            brks.append((si, k, t0, okd))
        if si % 500 == 0:
            print('  %d/%d 锚点%d 突破%d' % (si, len(stocks), total_anchor, len(brks)), flush=True)

    print('\n[样本] 有效 N 字锚点 %d 个，其中 10 日内完成反包 %d 个（%.1f%%）' %
          (total_anchor, len(brks), 100.0 * len(brks) / max(1, total_anchor)))

    out = {}
    # 可买性：突破日收盘是否涨停
    blocked = [b for b in brks if limit_pct(stocks[b[0]].code, stocks[b[0]].name) - 0.003
               <= stocks[b[0]].pct(b[1])]
    print('[可买性] 突破日收盘涨停（买不进）%d 个，占 %.1f%%；可买 %d 个' %
          (len(blocked), 100.0 * len(blocked) / max(1, len(brks)), len(brks) - len(blocked)))
    out['anchors'] = total_anchor
    out['brk_n'] = len(brks)
    out['blocked'] = len(blocked)

    print('\n===== 突破确认日收盘买（可买的样本）=====')
    for tp in (0.02, 0.025, 0.03, None):
        rows = []
        for si, k, t0, okd in brks:
            if limit_pct(stocks[si].code, stocks[si].name) - 0.003 <= stocks[si].pct(k):
                continue
            r = sim2(stocks[si], k, t0, k - t0, tp or 0.999, 0.05, 10, 'close')
            if r:
                rows.append(r)
        s = detail(rows)
        out['right_tp%s' % (tp or 'none')] = s
        tag = ('%d%%止盈' % round(tp * 100)) if tp else '不设止盈持10日'
        print('  %-14s %s' % (tag, line(s)))

    # 次日开盘买（不追尾盘）
    print('\n===== 突破确认日次日开盘买 =====')
    for tp in (0.02, 0.03):
        rows = []
        for si, k, t0, okd in brks:
            if limit_pct(stocks[si].code, stocks[si].name) - 0.003 <= stocks[si].pct(k):
                continue
            r = sim2(stocks[si], k, t0, k - t0, tp, 0.05, 10, 'open')
            if r:
                rows.append(r)
        s = detail(rows)
        out['right_open_tp%d' % round(tp * 100)] = s
        print('  %-14s %s' % ('%d%%止盈' % round(tp * 100), line(s)))

    # 按「整理几天后突破」分档
    print('\n===== 整理几天后反包（2%止盈）=====')
    for okd in (1, 2, 3, 4):
        rows = []
        for si, k, t0, d0 in brks:
            if d0 != okd:
                continue
            if limit_pct(stocks[si].code, stocks[si].name) - 0.003 <= stocks[si].pct(k):
                continue
            r = sim2(stocks[si], k, t0, k - t0, 0.02, 0.05, 10, 'close')
            if r:
                rows.append(r)
        s = detail(rows)
        out['okd_%d' % okd] = s
        print('  整理%d天 %s' % (okd, line(s)))

    # 按年
    print('\n===== 按年（2%止盈，收盘买）=====')
    byy = defaultdict(list)
    for si, k, t0, okd in brks:
        if limit_pct(stocks[si].code, stocks[si].name) - 0.003 <= stocks[si].pct(k):
            continue
        r = sim2(stocks[si], k, t0, k - t0, 0.02, 0.05, 10, 'close')
        if r:
            byy[stocks[si].d[k][:4]].append(r)
    for y in sorted(byy):
        print('  %s %s' % (y, line(detail(byy[y]))))
        out['year_%s' % y] = detail(byy[y])

    json.dump(out, open(os.path.join(BASE, 'nshape_right.json'), 'w'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()

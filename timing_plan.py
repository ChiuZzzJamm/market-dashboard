#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🎯 时间口挂单清单（2026-10-27 新建，用户第 1 问「能下单」+ 第 2 问「买 T+2/卖 T+4」）。

## 这个模块存在的唯一理由

`update_star.py` 的 `buyList` 回答的是「**此刻**现价在不在买区」，那是 9:45 看盘才成立的。
而回测（`duanban_timing.py`）证明的东西完全不同：

  · 策略单笔毛利 +0.58%（限价口径）；同一份回测的限价梯度（`timing_bt.json` → `final.limit`）
    是 **市价开盘 +26.7% ←→ 昨收−0.5% +33.6% ←→ 昨收−1% +36.8%**（年化，均含双边 0.16% 成本）；
  · 也就是说「买贵 0.5~1 个点就少吃 7 个点年化」——挂单这一层的价值全在这 7 个点上；
    （此处曾写「年化 +58.6% 掉到 −6.8%」，是把单笔 +0.580% 误读成 58.6%，量级错 100 倍，已改）

所以真正可执行的形态是：**昨天 16:00 就定好清单，开盘前把限价单挂进去**。
本模块就在 16:00 数据步之后跑，产出 `data.js` 的顶层 `timingPlan`：

    {date, tradeDate, orders:[{code,name,limit,floor,stop,tp,exitDate,...}], rule, ...}

9:45 只做三件事：① 用实时行情核对「开盘有没有破位」；② 报出实际可成交价；
③ 把清单原样展示。**不再筛股**——筛股在前一晚就做完了。

## 无前视保证（与 duanban_timing 严格同源）

用到的每一个字段，在「挂单时点」（T+2 开盘前）都已知：
    T 日：涨停（pct >= 9.7）、首板（lbc == 1）、T 日最低价（止损锚）
    T+1：收盘价（限价基准）、是否断板（pct < 9.7%）、放量比 V(T+1)/V(T)
全部取自已收盘的日 K，不读任何未来数据。

## 用法

    python3 timing_plan.py --dry      # 只打印，不写回
    python3 timing_plan.py            # 写 data.js 顶层 timingPlan
    python3 timing_plan.py --date 2026-10-26   # 指定 T+1 交易日（复盘用）
"""
import argparse
import json
import os
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import common                      # noqa: E402
import check_duanban as CD         # noqa: E402  涨停池（同花顺主源 + 东财兜底）
import duanban_timing as DT        # noqa: E402  口径常量唯一来源

# 口径常量全部从 duanban_timing 取，禁止在这里另写一套（改一处就两处漂移）
LIMIT_K = DT.LIMIT_K          # -0.5  限价 = T+1 收盘 × (1 + LIMIT_K/100)
FLOOR_K = DT.FLOOR_K          # 1.002 限价下限 = T 日最低价 × 1.002
TP = 0.05                     # 止盈 +5%
HOLD_MAX = 4                  # T+2 买 → T+4 卖（so = 4，bo = 2，持有 3 个交易日）
CAP_PER_DAY = 8               # 每晚最多挂 8 只
VOL_LO, VOL_HI = 1.1, 3.5     # T+1 放量比区间
MAX_CAND = 40                 # 候选池上限（送 AI 复核用，不改口径）

MIN_AMT = 2e8                 # 当日成交额 ≥ 2 亿（流动性硬门，与 full_scan 同口径）


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _prev_trade_days(n, end=None):
    """end 之前（不含 end）最近 n 个交易日，升序。"""
    from datetime import date, timedelta
    d = end or date.today()
    out = []
    while len(out) < n:
        d = d - timedelta(days=1)
        if common.is_trade_day(d):
            out.append(d)
    return sorted(out)


def load_klines(codes, base):
    """优先读 .kline_cache（16:00 数据步已灌满），缺失再走 check_duanban 现抓。

    R101 踩过的坑：`kline_cache.get_klines_bulk(codes, days=320, workers=8)` 的**第二个参数叫
    `days`，不是 `need`**。曾写成 `need=40` → TypeError 被下面的 `except` 吞成 `bulk={}`，
    然后退回对最多 120 只逐只 `fetch_kline_tencent` 现抓：一次跑 4 分钟以上、无任何报错，
    看上去只是「慢」。**吞异常必须留证据**——这里改成打印一行，下次再传错参数能当场看见。
    """
    import kline_cache as KC
    out = {}
    try:
        bulk = KC.get_klines_bulk(list(codes), days=40)
    except TypeError as e:
        print('[plan][WARN] kline_cache.get_klines_bulk 签名不符（%s）——退回逐只现抓，'
              '本次会明显偏慢，请核对参数名是否为 days=' % e)
        bulk = {}
    except Exception as e:                              # noqa: BLE001
        print('[plan][WARN] 批量取 K 线失败（%s: %s）——退回逐只现抓' % (type(e).__name__, e))
        bulk = {}
    for c in codes:
        rows = bulk.get(c) if isinstance(bulk, dict) else None
        if rows and len(rows) >= 30:
            # kline_cache 返回 [{day,open,close,high,low,volume}, ...]
            out[c] = [dict(d=r['day'], o=_f(r['open']), c=_f(r['close']),
                           h=_f(r['high']), l=_f(r['low']), v=_f(r['volume']))
                      for r in rows if r.get('day')]
    miss = [c for c in codes if c not in out]
    if miss:
        print('[plan] 缓存缺 %d/%d 只，逐只现抓（上限 120）' % (len(miss), len(codes)))
    for c in miss[:120]:                      # 兜底现抓（限流保护，最多 120 只）
        try:
            rows = CD.fetch_kline_tencent(c, 40)
        except Exception:
            rows = None
        if not rows or len(rows) < 30:
            continue
        out[c] = [dict(d=r['day'], o=_f(r['open']), c=_f(r['close']),
                       h=_f(r['high']), l=_f(r['low']), v=_f(r['volume']))
                  for r in rows if r.get('day')]
    return out


def build_plan(D, base=None, trade_date=None):
    """产出挂单清单。trade_date = T+1（已收盘那个交易日，YYYY-MM-DD）。"""
    base = base or BASE
    from datetime import date
    if trade_date:
        y, m, dd = (int(x) for x in trade_date.split('-'))
        end = date(y, m, dd)
        # 校验它确实是交易日
        if not common.is_trade_day(end):
            return None, '%s 不是交易日（节假日日历判定），拒绝按非交易日建单' % trade_date
        t1 = end
    else:
        # 默认：今天若是交易日、且已收盘，就取今天；否则取上一个交易日
        now = datetime.now()
        today = date(now.year, now.month, now.day)
        if common.is_trade_day(today) and now.hour >= 15:
            t1 = today
        else:
            t1 = _prev_trade_days(1)[-1]
    # T 日 = t1 的上一个交易日
    t0 = _prev_trade_days(1, t1)[-1]
    t0s = t0.strftime('%Y%m%d')

    # ---- ① T 日涨停池（首板口径）
    pool = CD.fetch_zt_pool(t0s)
    if pool is None:
        return None, ('T 日（%s）涨停池抓取失败（同花顺+东财双源均不可达）——'
                      '这是取数故障，不是「今天没有首板」；不写 timingPlan' % t0s)
    cands = []
    for it in pool:
        c = str(it.get('c') or '')
        if not c.startswith(('60', '00')):
            continue
        if int(it.get('lbc') or 0) != 1:      # 只要首板
            continue
        nm = str(it.get('n') or '')
        if 'ST' in nm.upper() or nm.startswith('*') or '退' in nm:
            continue
        cands.append(dict(code=c, name=nm, sector=it.get('hybk') or ''))
    if not cands:
        return None, ('T 日（%s）首板 0 只（涨停池 %d 只里没有主板首板）——'
                      '这是正常结果，不是抓取失败' % (t0s, len(pool)))
    codes = [x['code'] for x in cands]
    kls = load_klines(codes, base)
    if not kls:
        return None, 'K 线全部抓空，无法计算 T+1 断板/放量，不建单'

    # ---- ② 逐只算无前视过滤 + 挂单价
    orders, dropped = [], []
    for x in cands:
        c = x['code']
        rows = kls.get(c)
        if not rows or len(rows) < 10:
            dropped.append(dict(code=c, name=x['name'], why='K 线缺失（不是没机会，是没数据）'))
            continue
        # 定位 T 日 / T+1 下标（按日期字符串，不按下标假设，停牌也能对齐）
        iT = None
        for i in range(len(rows) - 1, -1, -1):
            if rows[i]['d'] == t0.strftime('%Y-%m-%d'):
                iT = i
                break
        if iT is None:
            dropped.append(dict(code=c, name=x['name'], why='K 线里没有 T 日（%s），停牌或数据缺口' % t0))
            continue
        if iT + 1 >= len(rows):
            dropped.append(dict(code=c, name=x['name'], why='K 线缺 T+1（%s）' % t1))
            continue
        r0, r1 = rows[iT], rows[iT + 1]
        if r1['d'] != t1.strftime('%Y-%m-%d'):
            dropped.append(dict(code=c, name=x['name'],
                                why='T+1 不是 %s（实际 %s），停牌' % (t1, r1['d'])))
            continue
        c0, c1 = _f(r0['c']), _f(r1['c'])
        l0, v0, v1 = _f(r0['l']), _f(r0['v']), _f(r1['v'])
        if not c0 or not c1 or not l0 or not v0 or not v1:
            dropped.append(dict(code=c, name=x['name'], why='T/T+1 价格或成交量缺失'))
            continue
        # 断板：T+1 没继续涨停
        p1 = (c1 / c0 - 1) * 100
        if p1 >= 9.7:
            dropped.append(dict(code=c, name=x['name'], why='T+1 继续涨停（%.1f%%），未断板' % p1))
            continue
        # 放量比
        r1v = v1 / v0 if v0 else None
        if r1v is None or not (VOL_LO <= r1v <= VOL_HI):
            dropped.append(dict(code=c, name=x['name'],
                                why='T+1 放量比 %.2f 不在 %.1f~%.1f' % (r1v or 0, VOL_LO, VOL_HI)))
            continue
        # 挂单价：cap = T+1 收盘 ×(1+k)，floor = T 日最低 × 1.002
        cap = round(c1 * (1 + LIMIT_K / 100.0), 2)
        floor = round(l0 * FLOOR_K, 2)
        if cap < floor:
            dropped.append(dict(code=c, name=x['name'],
                                why='限价 %.2f 已低于形态下沿 %.2f（T+1 跌幅过深），不挂' % (cap, floor)))
            continue
        if floor >= c1:
            dropped.append(dict(code=c, name=x['name'],
                                why='T 日最低 %.2f 已高于 T+1 收盘 %.2f，形态已破' % (floor, c1)))
            continue
        orders.append(dict(
            code=c, name=x['name'], sector=x['sector'],
            limit=cap, floor=floor, stop=round(l0, 2),
            tp=round(cap * (1 + TP), 2),
            t1Close=round(c1, 2), t1Pct=round(p1, 2), volRatio=round(r1v, 2),
            ztDate=t0.strftime('%Y-%m-%d'), ztLow=round(l0, 2),
            holdDays=HOLD_MAX - 2, exitNote='T+4 收盘到期平',
            sortKey=r1v,
        ))

    orders.sort(key=lambda o: -o['sortKey'])
    total_before_cap = len(orders)
    for o in orders:
        o.pop('sortKey', None)
    picked = orders[:CAP_PER_DAY]
    dropped_cap = orders[CAP_PER_DAY:]
    for o in dropped_cap:
        dropped.append(dict(code=o['code'], name=o['name'],
                            why='放量比排序第 %d 位，超出每日 %d 只上限'
                                % (orders.index(o) + 1, CAP_PER_DAY)))

    plan = {
        "date": datetime.now().strftime('%Y-%m-%d'),
        "generatedAt": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "tradeDate": t1.strftime('%Y-%m-%d'),          # T+1（清单基准日）
        "ztDate": t0.strftime('%Y-%m-%d'),             # T（涨停日）
        "buyDate": _next_trade_day_str(t1),            # T+2（下单日）
        "orders": picked,
        "summary": {
            "n": len(picked),
            "nCandidate": total_before_cap,
            "capPerDay": CAP_PER_DAY,
            "dropped": dropped[:30],
            "nDropped": len(dropped),
        },
        "params": {
            "limitK": LIMIT_K, "floorK": FLOOR_K, "tp": TP,
            "holdDays": HOLD_MAX - 2, "volRange": [VOL_LO, VOL_HI],
            "capPerDay": CAP_PER_DAY, "slots": DT.SLOTS,
        },
        # 回测出处：前端/推送直接把这段贴出来，禁止 AI 另算一套
        "bt": _bt_brief(base),
        "rule": (
            # R100z254：这里原来写「这是**挂单清单**」——markdown 强调符会被 esc 原样印在网页上。
            # update_star.py 里早有一条规矩「这段直接渲染成正文，不许带 ** 强调符」，
            # 但这条文案是后来加的、没照那条规矩办。audit G50 现在会拦这类泄漏。
            "这是挂单清单，不是看盘建议：请在 T+2 开盘前（9:25 集合竞价结束即挂）按 "
            "「限价」逐只挂单，成交价不会高于表内限价。开盘后不再筛股、不再追价——"
            "回测口径就是限价成交，晚一步的滑价会把全部毛利吃掉。"
            "盘中跌破「形态下沿」＝形态作废，先撤；触及「止盈」或持有到期无条件平。"
        ),
        # R101：风险文案也必须唯一来源。此前这段只写在 update_star.py 里，
        # 于是 16:00 定单 → 次日 9:45 核对之间（前端走 timingPlan 回退分支时）页面没有风险提示，
        # 8 只单挂在屏幕上却只有收益没有代价。文案随数据一起产出，两处都读同一份。
        "riskNote": (
            "止损距离偏宽是这套形态的固有代价：买价相对 T 日最低价中位溢价 +9.2%、43% 的单子超 +10%。"
            "回测里 17.9% 的笔触发止损、均亏 5.5%，靠 46.5% 的 +5% 止盈覆盖。"
            "所以单笔仓位必须小（每只 1/32 资金），不要因为「看着确定」就加重。"
        ),
        "source": "timing_plan.py（无前视：只用 T 日与 T+1 收盘数据，口径同 duanban_timing.py）",
    }
    return plan, None


def _next_trade_day_str(d):
    from datetime import timedelta
    x = d + timedelta(days=1)
    for _ in range(12):
        if common.is_trade_day(x):
            return x.strftime('%Y-%m-%d')
        x += timedelta(days=1)
    return ''


def _bt_brief(base):
    """把 timing_bt.json 的定稿口径摘要带出来（只读，不重算）。"""
    p = os.path.join(base, 'timing_bt.json')
    try:
        with open(p, encoding='utf-8') as fh:
            j = json.load(fh)
    except Exception:
        return None
    f = j.get('final') or {}
    if not f:
        return None
    return {
        "span": j.get('span'),
        "n": f.get('n'), "win": f.get('win'), "avg": f.get('avg'),
        "total": f.get('total'), "ann": f.get('ann'), "mdd": f.get('mdd'),
        "sharpe": f.get('sharpe'), "byYear": f.get('byYear'),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--date', help='T+1 交易日 YYYY-MM-DD（默认自动取最近已收盘交易日）')
    a = ap.parse_args()
    D = common.load_dashboard_data(BASE)
    plan, err = build_plan(D, BASE, a.date)
    if err:
        print('[timingPlan] %s' % err)
        return 1
    print('[timingPlan] T 涨停日 %s → T+1 基准日 %s → T+2 下单日 %s'
          % (plan['ztDate'], plan['tradeDate'], plan['buyDate']))
    print('[timingPlan] 候选 %d 只 → 挂单 %d 只（上限 %d）'
          % (plan['summary']['nCandidate'], plan['summary']['n'], CAP_PER_DAY))
    print('%-8s %-10s %8s %8s %8s %8s %8s %6s'
          % ('代码', '名称', '限价', '下沿', '止损', '止盈', 'T+1收', '放量'))
    for o in plan['orders']:
        print('%-8s %-10s %8.2f %8.2f %8.2f %8.2f %8.2f %6.2f'
              % (o['code'], o['name'][:8], o['limit'], o['floor'], o['stop'],
                 o['tp'], o['t1Close'], o['volRatio']))
    if plan['summary']['dropped']:
        print('\n未入选 %d 只（前 10）：' % plan['summary']['nDropped'])
        for d in plan['summary']['dropped'][:10]:
            print('  %s %s：%s' % (d['code'], d['name'], d['why']))
    b = plan.get('bt')
    if b:
        print('\n回测出处：%s ~ %s  n=%d 胜率%.1f%% 均%+.3f%% 年化%+.1f%% 回撤%.1f%% 夏普%.2f'
              % (b['span'][0], b['span'][1], b['n'], b['win'], b['avg'],
                 b['ann'] * 100, b['mdd'] * 100, b['sharpe']))
    if a.dry:
        print('\n[dry] 不写回 data.js')
        return 0
    # ★写盘走 common（硬约束 11）：_tmp + os.replace 原子替换
    D['timingPlan'] = plan
    common.write_data_js(BASE, D)
    print('\n[write] data.js 顶层 timingPlan 已更新')
    return 0


if __name__ == '__main__':
    sys.exit(main())

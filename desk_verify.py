#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""desk_verify.py —— 交易预案的次日对账（R100z186，2026-10-24 重写）

## 为什么是「条件单」口径而不是「开盘价区间」

R100z185 首版判的是「开盘价落在 entryLo..entryHi 就买」——**这个判据用户用不上**。
他 2026-10-24 的原话：

  「比如你开盘精选推荐一个竞价是水下的标的，然后开盘后涨到水上，我等你推荐后
    再水上 2% 点买入，收盘他涨到公司 5%，次日他就算是 -1% 开盘我也能盈利卖出，
    所以次日不是你需要考虑的，你需要考虑的是前一日选的标的，当日开盘是否适合买入。」

第二次纠正：「我前面只是举例竞价由弱转强，不是必须要竞价在水下。」

所以真正的买法是**盘中由弱转强才动手**，不是开盘价一锤定音：

    昨收 P  →  站上昨收 = 转强确认（第一档，不追）
            →  摸到 P×1.02 = 他实际付的价（第二档，条件单）
            →  P×1.05 = 止盈参考（质量指标，不是当天卖点）

09:45 只给「现在强不强 + 两条挂单价在哪」；**成交与否交给当天盘中**：
结算条件是 `开盘 < P×1.02 且 当日最高 ≥ P×1.02`——正好等于「盘中才转强、追进去」。
这样 09:45 竞价还水下的票，只要盘中真的转强，也不会漏账。

## 三档结算（因为出场口径决定生死）

2026-10-24 回测 91 笔实测（/tmp/fc/bt_exit.py）：同一个入场，只换出场，

| 出场 | 单笔净 | 胜率 |
|---|---|---|
| 当日收盘（浮盈，卖不掉） | −0.30% | 38.5% |
| **次日开盘卖（T+1 唯一能兑现）** | **−1.46%** | 31.9% |
| 次日收盘卖 | −0.64% | 42.9% |

所以台账**三档全记**，不挑对自己有利的那一行：

    retClose   当日收盘 vs 入场   —— 只评判「这天买得对不对」，不掺出场
    retNextOpen 次日开盘 vs 入场  —— 裸买唯一能兑现的口径
    hitTp      当日是否摸到 P×1.05 —— 质量指标（51% 的笔摸到，过一夜只剩 +0.24%）

## 输出

`desk_verify.json`（本地台账，不进 data.js）+ `duanban.star.deskCheck`（页面用一层）。

用法：
    run-python desk_verify.py --check --write-star   # 09:45：挂单提示 + 结算昨日
    run-python desk_verify.py --plan-day 2026-10-21  # 回放自检
    run-python desk_verify.py --report               # 累计战绩
    run-python desk_verify.py --self-test
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import common          # noqa: E402

LEDGER = os.path.join(ROOT, 'desk_verify.json')
QT = 'https://qt.gtimg.cn/q='

COST = 0.20            # 双边成本（佣金 0.025×2 + 印花 0.05 + 滑点），与回测同源
TRIG_MULT = 1.02       # 他的实际买入价：昨收 ×1.02
TP_MULT = 1.05         # 止盈参考：昨收 ×1.05

# 09:45 时点的强弱（只描述现在，不参与结算过滤——结算是全天的事）
STATE_ZH = {
    'cross': '已站上昨收',
    'strong': '已过 +2%',
    'weak': '未转强',
    'limit': '涨停买不到',
    'unknown': '待确认',
}


# ---------------------------------------------------------------- 行情
def fetch_quotes(codes):
    """腾讯 gtimg 批量快照 → {code: {name, open, prevClose, now, pct}}；失败 {}。"""
    out = {}
    codes = [c for c in codes if c]
    for i in range(0, len(codes), 60):
        batch = codes[i:i + 60]
        q = ','.join(('sh' if c.startswith('6') else 'sz') + c for c in batch)
        try:
            r = subprocess.run(['curl', '-s', '--max-time', '15', QT + q],
                               capture_output=True, timeout=25)
            txt = r.stdout.decode('gbk', errors='ignore')
        except Exception:
            continue
        for m in re.finditer(r'v_(?:sh|sz)(\d{6})="([^"]+)"', txt):
            f = m.group(2).split('~')
            if len(f) < 33:
                continue

            def num(i):
                try:
                    v = float(f[i])
                    return v if v > 0 else None
                except Exception:
                    return None

            out[m.group(1)] = {
                'name': f[1],
                'now': num(3), 'prevClose': num(4), 'open': num(5),
                'pct': num(32),
            }
    return out


def bars_for(D, day):
    """从 stkKlines 取指定日期的 K 线 → {code: {open,close,high,low}}。

    ★ norm_bars 的字段顺序是 (day, open, close, high, low, vol)——
      close 在下标 2、high 在 3、low 在 4，**不是**常见 OHLC。写错一位不会报错，
      只会安静地给出错误结论（首版就踩过：胜率算出 0.0%）。
    """
    import atr_stop as A
    out = {}
    for c, raw in (D.get('stkKlines') or {}).items():
        try:
            b = A.norm_bars(raw)
        except Exception:                                       # noqa: BLE001
            continue
        for k in range(len(b) - 1, -1, -1):
            if b[k][0] == day:
                out[str(c)] = {'date': b[k][0], 'open': b[k][1], 'close': b[k][2],
                               'high': b[k][3], 'low': b[k][4]}
                break
    return out


# ---------------------------------------------------------------- 挂单提示
def plan_order(pick, q):
    """单只 → 挂单提示。q 为 None 时给 unknown，不硬判。"""
    pc = (q or {}).get('prevClose')
    base = {'code': pick.get('code'), 'name': pick.get('name'),
            'q': pick.get('q'), 'srcs': pick.get('srcs') or []}
    if not pc:
        return dict(base, prevClose=None, anchor=None, trig=None, tp=None,
                    state='unknown', stateZh=STATE_ZH['unknown'],
                    open=None, openPct=None, now=None, nowPct=None,
                    note='行情缺失，无法给出挂单价（不凭猜写价位）')
    anchor = round(pc, 2)                       # 站上昨收 = 转强确认（第一档）
    trig = round(pc * TRIG_MULT, 2)             # 他实际付的价（第二档）
    tp = round(pc * TP_MULT, 2)
    op, now = q.get('open'), (q.get('now') or q.get('open'))
    opct = round((op / pc - 1) * 100, 2) if op else None
    npct = round((now / pc - 1) * 100, 2) if now else None
    base.update({'prevClose': round(pc, 2), 'anchor': anchor, 'trig': trig, 'tp': tp,
                 'open': round(op, 2) if op else None, 'openPct': opct,
                 'now': round(now, 2) if now else None, 'nowPct': npct})
    if npct is None:
        return dict(base, state='unknown', stateZh=STATE_ZH['unknown'],
                    note='没有现价，只有开盘价，按未转强处理')
    if npct >= 9.5:
        return dict(base, state='limit', stateZh=STATE_ZH['limit'],
                    note='接近涨停，买不到也不该追')
    if now >= trig:
        return dict(base, state='strong', stateZh=STATE_ZH['strong'],
                    note='已越过 +2%% 追价档：要么按现价追（成本更高），要么等回落到 %.2f' % trig)
    if now >= anchor:
        return dict(base, state='cross', stateZh=STATE_ZH['cross'],
                    note='刚站上昨收：第一档可买（不追），或挂 %.2f 等 +2%% 确认' % trig)
    return dict(base, state='weak', stateZh=STATE_ZH['weak'],
                note='还在水下/未站上昨收：挂 %.2f 等转强，别提前接' % trig)


def check(plan, quotes):
    return [plan_order(p, quotes.get(p.get('code'))) for p in plan]


# ---------------------------------------------------------------- 结算
def settle(prev_orders, bars, today_quotes, tag):
    """结算上一日全部挂单（三档口径）。

    成交判据 = `开盘 < P×1.02 且 当日最高 ≥ P×1.02`
      —— 开盘就在 +2% 上方的不算（那种高开不该追），
      —— 全天没摸到的也不算（条件单没触发）。
    与回测 /tmp/fc/bt_desk_water.py 完全同源。

    入场价一律取 trig（条件单价）。09:45 时若已 `strong`（现价已在 trig 上方），
    实际成本更高——用 `slip` 标出来，不假装买在 trig。
    """
    rows = []
    for o in prev_orders or []:
        code = o.get('code')
        trig, tp = o.get('trig'), o.get('tp')
        b = (bars or {}).get(str(code))
        row = {'code': code, 'name': o.get('name'), 'trig': trig, 'tp': tp,
               'prevClose': o.get('prevClose'), 'state45': o.get('state')}
        if not (trig and tp and b):
            row.update({'filled': False, 'why': '缺当日 K 线'})
            rows.append(row)
            continue
        filled = (b['open'] < trig) and (b['high'] >= trig)
        row.update({'filled': filled, 'dayOpen': b['open'], 'dayHigh': b['high'],
                    'dayClose': b['close']})
        if not filled:
            row['why'] = ('开盘已在 +2% 上方（不追）' if b['open'] >= trig
                          else '全天没摸到 +2%（条件单未触发）')
            rows.append(row)
            continue
        rc = round((b['close'] / trig - 1) * 100, 2)
        nq = (today_quotes or {}).get(str(code)) or {}
        nxt = nq.get('open')
        rn = round((nxt / trig - 1) * 100, 2) if nxt else None
        # 09:45 时现价已高于 trig 的，真实成本更高 → 记滑点（>0 表示比 trig 贵）
        slip = 0.0
        if o.get('state') == 'strong' and o.get('nowPct') is not None:
            slip = round(o['nowPct'] - (TRIG_MULT - 1) * 100, 2)
        row.update({'retClose': rc, 'retNetClose': round(rc - COST, 2),
                    'retNextOpen': rn,
                    'retNetNextOpen': round(rn - COST, 2) if rn is not None else None,
                    'hitTp': bool(b['high'] >= tp), 'slip': slip})
        rows.append(row)
    return summarize(rows, tag)


def summarize(rows, tag):
    """把明细汇总成页面与统计要的形状。只对成交的笔算收益。"""
    filled = [r for r in rows if r.get('filled')]
    n = len(filled)
    s = {'tag': tag, 'nOrders': len(rows), 'nFilled': n,
         'filledRate': round(n / len(rows) * 100, 1) if rows else None,
         'hitTp': None, 'hitTpRate': None,
         'winClose': None, 'meanClose': None,
         'winNextOpen': None, 'meanNextOpen': None, 'nNextOpen': 0,
         'rows': rows}
    if not n:
        return s
    s['hitTp'] = len([r for r in filled if r.get('hitTp')])
    s['hitTpRate'] = round(s['hitTp'] / n * 100, 1)
    s['winClose'] = round(len([r for r in filled if r['retNetClose'] > 0]) / n * 100, 1)
    s['meanClose'] = round(sum(r['retNetClose'] for r in filled) / n, 2)
    nxt = [r for r in filled if r.get('retNetNextOpen') is not None]
    s['nNextOpen'] = len(nxt)
    if nxt:
        s['winNextOpen'] = round(len([r for r in nxt if r['retNetNextOpen'] > 0]) / len(nxt) * 100, 1)
        s['meanNextOpen'] = round(sum(r['retNetNextOpen'] for r in nxt) / len(nxt), 2)
    return s


def cum_stats(days):
    """累计：把所有日的结算明细摊平重算（不累加日均，避免小样本日权重失真）。"""
    rows = [r for d in days.values() for r in ((d.get('settle') or {}).get('rows') or [])]
    filled = [r for r in rows if r.get('filled')]
    n = len(filled)
    out = {'days': len([d for d in days.values() if (d.get('settle') or {}).get('rows')]),
           'nOrders': len(rows), 'nFilled': n, 'hitTpRate': None,
           'winClose': None, 'meanClose': None, 'winNextOpen': None, 'meanNextOpen': None}
    if not n:
        return out
    out['hitTpRate'] = round(len([r for r in filled if r.get('hitTp')]) / n * 100, 1)
    out['winClose'] = round(len([r for r in filled if r['retNetClose'] > 0]) / n * 100, 1)
    out['meanClose'] = round(sum(r['retNetClose'] for r in filled) / n, 2)
    nxt = [r for r in filled if r.get('retNetNextOpen') is not None]
    if nxt:
        out['winNextOpen'] = round(len([r for r in nxt if r['retNetNextOpen'] > 0]) / len(nxt) * 100, 1)
        out['meanNextOpen'] = round(sum(r['retNetNextOpen'] for r in nxt) / len(nxt), 2)
    return out


def build_payload(td, valid, checked_at, settle_from, orders, settle_obj, cum):
    """页面用的一层 payload（抽成函数：端到端测试要能脱离 main 单独构造）。"""
    n_buyable = len([o for o in orders if o['state'] in ('cross', 'strong')])
    return {
        'planDate': str(td.get('tradeDate') or '')[:10],
        'validUntil': valid,
        'checkedAt': checked_at,
        'settleFrom': settle_from or '',
        'costModel': COST,
        'orders': orders,
        'nBuyable': n_buyable,
        'settle': {k: settle_obj.get(k) for k in
                   ('nOrders', 'nFilled', 'filledRate', 'hitTp', 'hitTpRate',
                    'winClose', 'meanClose', 'winNextOpen', 'meanNextOpen', 'nNextOpen')},
        'settleRows': settle_obj.get('rows') or [],
        'cum': cum,
        'summary': ('预案 %d 只，09:45 已站上昨收 %d 只（%s）；追价档 %s'
                    % (len(orders), n_buyable,
                       '、'.join('%s %s' % (o['code'], (o['name'] or '')[:6])
                                for o in orders if o['state'] in ('cross', 'strong')) or '无',
                       '、'.join('%.2f' % o['trig'] for o in orders if o['trig']) or '—')),
    }


def is_trading_day():
    """以 cal_factor.py 的 aShareClosed 为硬依据（与 09:45 同源，不自己判周末）。"""
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, 'cal_factor.py'), '--json'],
                           capture_output=True, timeout=30)
        d = json.loads(r.stdout.decode('utf-8', errors='ignore') or '{}')
        return not d.get('aShareClosed'), d.get('tags') or ''
    except Exception as e:                                       # noqa: BLE001
        return None, '日历脚本不可用：%s' % e


# ---------------------------------------------------------------- 台账
def load_ledger():
    if not os.path.exists(LEDGER):
        return {'days': {}, 'cum': {}}
    try:
        d = json.load(open(LEDGER, encoding='utf-8'))
        d.setdefault('days', {})
        return d
    except Exception:                                            # noqa: BLE001
        return {'days': {}, 'cum': {}}


def save_ledger(L):
    L['cum'] = cum_stats(L['days'])
    tmp = LEDGER + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(L, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LEDGER)


def prev_trading_day(days, today):
    ks = sorted(k for k in days if k < today)
    return ks[-1] if ks else None


# ---------------------------------------------------------------- 回放
def plan_from_history(D, plan_day, max_pick=8):
    """用 <= plan_day 的 K 线重算预案（自检/回放用，不读 tradeDesk）。

    与 signal_rank 完全同源：factors_of 打分、judge 否决、compute_one 出止损、
    exec_plan 出执行区间。不复制任何公式。
    """
    import atr_stop as A
    import signal_rank as R
    K = D.get('stkKlines') or {}
    idx = {}
    for c, raw in K.items():
        b = A.norm_bars(raw)
        if len(b) >= 70:
            idx[c] = b
    moms = []
    for c, b in idx.items():
        i = next((j for j, r in enumerate(b) if r[0] == plan_day), None)
        if i is None or i < 21:
            continue
        c0, c1 = b[i - 20][2], b[i][2]
        if c0 and c1 and c0 > 0:
            moms.append((c1 - c0) / c0 * 100)
    moms.sort()
    if len(moms) < 200:
        return []
    ctx = {'momSorted': moms, 'n': len(moms)}
    cand = []
    for c, b in idx.items():
        if not (str(c).startswith('60') or str(c).startswith('00')):
            continue
        i = next((j for j, r in enumerate(b) if r[0] == plan_day), None)
        if i is None or i < 65:
            continue
        seg = b[:i + 1]
        atr, _, _ = A.atr_wilder(seg)
        if atr is None:
            continue
        q, det = R.factors_of(seg, atr, ctx)
        close = seg[-1][2]
        if not close or close < 2.0:
            continue
        ok, _ = R.judge(det, det.get('atrPct'), None, close)
        if not ok:
            continue
        one = A.compute_one(c, '', seg)
        if not one:
            continue
        cand.append({'code': c, 'name': one.get('name') or '', 'q': q,
                     'stop': one['stop'], 'target': one['target'], 'srcs': []})
    cand.sort(key=lambda x: (-x['q'], x['code']))
    return cand[:max_pick]


def backtest_day(D, plan_day):
    """回放：plan_day 收盘出预案 → 次日开盘判定 → 再次日开盘结算 + 当日三档。"""
    import atr_stop as A
    picks = plan_from_history(D, plan_day)
    if not picks:
        print('[ERR] %s 无可用预案' % plan_day)
        return 1
    K = D.get('stkKlines') or {}
    days = sorted({r[0] for raw in K.values() for r in A.norm_bars(raw)})
    if plan_day not in days:
        print('[ERR] %s 不是交易日' % plan_day)
        return 1
    i = days.index(plan_day)
    if i + 2 >= len(days):
        print('[ERR] 需要 T+1 与 T+2 数据')
        return 1
    d1, d2 = days[i + 1], days[i + 2]

    b1 = bars_for(D, d1)
    quotes = {}
    for p in picks:
        b = b1.get(str(p['code']))
        if not b:
            continue
        try:
            pb = next((x for x in A.norm_bars(K.get(p['code']) or []) if x[0] == plan_day), None)
        except Exception:                                        # noqa: BLE001
            pb = None
        if not pb or not pb[2]:
            continue
        # 回放没有 09:45 快照，用开盘价当现价（只用开盘那一刻的信息，不算偷看）
        quotes[p['code']] = {'open': b['open'], 'prevClose': pb[2], 'now': b['open']}

    orders = check(picks, quotes)
    print('=== 回放 %s 收盘出预案 → %s 开盘（现价用开盘价代理）===' % (plan_day, d1))
    for o in orders:
        print('  %-8s %-6s q=%5.1f  昨收 %6.2f  站上 %6.2f  追价 %6.2f  止盈 %6.2f  %-8s %s'
              % (o['code'], (o.get('name') or '')[:6], o['q'],
                 o.get('prevClose') or 0, o.get('anchor') or 0, o.get('trig') or 0,
                 o.get('tp') or 0, o.get('stateZh') or '', o.get('note') or ''))

    q2 = {}
    b2 = bars_for(D, d2)
    for c, b in b2.items():
        q2[c] = {'open': b['open']}
    st = settle(orders, b1, q2, d1)
    print('\n=== %s 三档结算（成交判据：开盘 < 追价 且 盘中摸到追价）===' % d1)
    for r in st['rows']:
        if not r.get('filled'):
            print('  %-8s %-6s 未成交：%s' % (r['code'], (r.get('name') or '')[:6], r.get('why')))
            continue
        print('  %-8s %-6s 买 %6.2f → 收盘 %6.2f %+6.2f%% | 次日开 %s %s | 摸到止盈 %s'
              % (r['code'], (r.get('name') or '')[:6], r['trig'], r['dayClose'],
                 r['retNetClose'],
                 ('%6.2f' % (r.get('retNextOpen') or 0)) if r.get('retNextOpen') is not None else '  —  ',
                 ('%+6.2f%%' % r['retNetNextOpen']) if r.get('retNetNextOpen') is not None else '',
                 '是' if r.get('hitTp') else '否'))
    print('  合计：挂单 %d → 成交 %d（%.0f%%）｜当日收盘净 %+.2f%%（胜率 %.0f%%）'
          '｜次日开盘净 %+.2f%%｜摸到止盈 %.0f%%'
          % (st['nOrders'], st['nFilled'], st['filledRate'] or 0,
             st['meanClose'] or 0, st['winClose'] or 0,
             st['meanNextOpen'] or 0, st['hitTpRate'] or 0))
    return 0


# ---------------------------------------------------------------- 自检
def self_test():
    ok = True

    def chk(name, got):
        nonlocal ok
        print(('  OK   ' if got else '  FAIL ') + name)
        ok = ok and bool(got)

    pk = {'code': '000001', 'name': '测试', 'q': 90, 'srcs': []}
    q = lambda now, pc=10.0, op=None: {'open': op if op is not None else now,
                                       'prevClose': pc, 'now': now}
    o = plan_order(pk, q(10.3))
    chk('挂单价：站上 10.00 / 追价 10.20 / 止盈 10.50（实测 %s/%s/%s）'
        % (o['anchor'], o['trig'], o['tp']),
        (o['anchor'], o['trig'], o['tp']) == (10.0, 10.2, 10.5))
    chk('现价 10.30 → strong', plan_order(pk, q(10.3))['state'] == 'strong')
    chk('现价 10.10 → cross', plan_order(pk, q(10.1))['state'] == 'cross')
    chk('现价 10.00 → cross（边界算站上）', plan_order(pk, q(10.0))['state'] == 'cross')
    chk('现价 9.90 → weak', plan_order(pk, q(9.9))['state'] == 'weak')
    chk('现价 +9.6% → limit', plan_order(pk, q(10.96))['state'] == 'limit')
    chk('无行情 → unknown 不硬判', plan_order(pk, None)['state'] == 'unknown')
    chk('无昨收 → 不给价位', plan_order(pk, {'open': 10, 'now': 10, 'prevClose': None})['trig'] is None)

    # 结算：昨收 10 → 追价 10.2；次日开 10.0（<追价）+ 最高 10.4（≥追价）→ 成交
    od = [plan_order(pk, q(9.9))]
    bar = {'000001': {'open': 10.0, 'high': 10.4, 'close': 10.3, 'low': 9.8}}
    s = settle(od, bar, {'000001': {'open': 10.5}}, 'd1')
    r = s['rows'][0]
    chk('开盘低于追价 + 盘中摸到 → 成交', r['filled'])
    chk('当日收盘毛收益 +0.98（实测 %s）' % r.get('retClose'),
        abs(r.get('retClose', 0) - 0.98) < 0.011)
    chk('净收益 = 毛 − 0.20', abs(r['retNetClose'] - (r['retClose'] - COST)) < 0.011)
    chk('次日开盘毛 +2.94（实测 %s）' % r.get('retNextOpen'),
        abs(r.get('retNextOpen', 0) - 2.94) < 0.011)
    chk('摸到止盈（10.4 < 10.5 → 应为否）', r['hitTp'] is False)

    # 开盘就在追价上方 → 不追，不成交
    s2 = settle(od, {'000001': {'open': 10.3, 'high': 10.6, 'close': 10.5, 'low': 10.2}},
                {'000001': {'open': 10.5}}, 'd1')
    chk('开盘已在 +2% 上方 → 不算成交', s2['rows'][0]['filled'] is False)
    # 全天没摸到追价 → 不成交
    s3 = settle(od, {'000001': {'open': 9.9, 'high': 10.1, 'close': 10.0, 'low': 9.7}},
                {'000001': {'open': 10.0}}, 'd1')
    chk('全天没摸到 +2% → 不算成交', s3['rows'][0]['filled'] is False)
    # 摸到止盈
    s4 = settle(od, {'000001': {'open': 10.0, 'high': 10.6, 'close': 10.4, 'low': 9.9}},
                {'000001': {'open': 10.2}}, 'd1')
    chk('最高 10.6 ≥ 10.5 → 摸到止盈', s4['rows'][0]['hitTp'] is True)
    # 汇总自洽
    chk('汇总：1 笔成交、止盈率与明细一致',
        s4['nFilled'] == 1 and s4['hitTpRate'] == 100.0)
    chk('累计口径能吃下多日', cum_stats({'a': {'settle': s4}, 'b': {'settle': s}})['nFilled'] == 2)

    print('[SELF-TEST] ' + ('OK' if ok else 'FAIL'))
    return 0 if ok else 1


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='挂单提示今日 + 结算昨日')
    ap.add_argument('--plan-day', help='回放某日预案（自检，不读 tradeDesk）')
    ap.add_argument('--report', action='store_true', help='只看累计战绩')
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--write-star', action='store_true',
                    help='把对账结果写进 duanban.star.deskCheck（09:45 用）')
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    if a.report:
        L = load_ledger()
        c = cum_stats(L['days'])
        if a.json:
            print(json.dumps(c, ensure_ascii=False, indent=1))
        elif not c['nFilled']:
            print('累计 0 笔成交 —— 台账还没有可用样本（%d 天有结算记录）' % c['days'])
        else:
            print('累计 %d 天 / 挂单 %d → 成交 %d 笔' % (c['days'], c['nOrders'], c['nFilled']))
            print('  当日收盘：净 %+.2f%%  胜率 %.0f%%' % (c['meanClose'], c['winClose']))
            print('  次日开盘：净 %+.2f%%  胜率 %s' % (c['meanNextOpen'],
                                                 '%.0f%%' % c['winNextOpen']
                                                 if c['winNextOpen'] is not None else '—'))
            print('  摸到止盈：%.0f%%' % c['hitTpRate'])
            print('  （三档并看：当日收盘=判断对不对，次日开盘=裸买真能兑现的那档）')
        return 0

    D = common.load_dashboard_data(ROOT)
    if D is None:
        print('[ERR] data.js 解析失败', file=sys.stderr)
        return 1

    if a.plan_day:
        return backtest_day(D, a.plan_day)

    if not a.check:
        ap.print_help()
        return 2

    td = D.get('tradeDesk') or {}
    plan = td.get('picks') or []
    if not plan:
        print('[SKIP] tradeDesk.picks 为空，无可对账预案')
        return 0

    ok_day, tags = is_trading_day()
    if ok_day is None:
        print('[WARN] %s —— 无法确认今天是否交易日，不做判定' % tags)
        return 0
    if not ok_day:
        print('[SKIP] 今日 A 股休市（%s），不判定、不写台账' % tags)
        return 0

    today = datetime.date.today().isoformat()
    valid = str(td.get('validUntil') or '')[:10]
    if valid and today > valid:
        print('[WARN] 预案 validUntil=%s 已过期（今天 %s）——不判定，避免拿旧价位当今日指令'
              % (valid, today))
        return 0

    L = load_ledger()
    pd = prev_trading_day(L['days'], today)
    prev_orders = (L['days'].get(pd) or {}).get('orders') or []
    quotes = fetch_quotes(list(dict.fromkeys(
        [p.get('code') for p in plan] + [o.get('code') for o in prev_orders])))

    orders = check(plan, quotes)
    settle_obj = {'tag': '', 'nOrders': 0, 'nFilled': 0, 'rows': []}
    if pd:
        prev_bars = bars_for(D, pd)
        settle_obj = settle(prev_orders, prev_bars, quotes, pd)

    L['days'][today] = {
        'date': today,
        'checkedAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'planDay': td.get('tradeDate'),
        'orders': orders,
        'settle': settle_obj,
    }
    save_ledger(L)

    cum = cum_stats(L['days'])
    payload = build_payload(td, valid, L['days'][today]['checkedAt'], pd,
                            orders, settle_obj, cum)

    if a.write_star:
        db = D.get('duanban') or {}
        st = db.get('star') or {}
        if str(st.get('date') or '')[:10] != today:
            print('[WARN] duanban.star.date=%r 不是今天，不写对账（避免把昨日精选当今日）'
                  % st.get('date'))
        else:
            st['deskCheck'] = payload
            db['star'] = st
            D['duanban'] = db
            common.write_data_js(ROOT, D, '[desk_verify]')
            print('[OK] 已写 duanban.star.deskCheck')

    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0

    if pd:
        print('=== 昨日挂单结算（%s，成交判据：开盘 < 追价 且 盘中摸到追价）===' % pd)
        if not settle_obj['nOrders']:
            print('  （无挂单记录）')
        for r in settle_obj['rows']:
            if not r.get('filled'):
                print('  %-8s %-6s 未成交：%s' % (r['code'], (r.get('name') or '')[:6],
                                              r.get('why') or ''))
                continue
            print('  %-8s %-6s 买 %6.2f → 收盘 %6.2f %+6.2f%% ｜ 次日开 %s ｜ 摸到止盈 %s%s'
                  % (r['code'], (r.get('name') or '')[:6], r['trig'], r['dayClose'],
                     r['retNetClose'],
                     ('%+6.2f%%' % r['retNetNextOpen']) if r.get('retNetNextOpen') is not None else '  —  ',
                     '是' if r.get('hitTp') else '否',
                     ('（09:45 已过追价，滑点 +%.2f%%）' % r['slip']) if r.get('slip') else ''))
        if settle_obj['nFilled']:
            print('  小计 %d 笔：当日收盘净 %+.2f%%（胜率 %.0f%%）｜次日开盘净 %+.2f%%｜摸到止盈 %.0f%%'
                  % (settle_obj['nFilled'], settle_obj['meanClose'], settle_obj['winClose'],
                     settle_obj['meanNextOpen'] or 0, settle_obj['hitTpRate']))
    print('\n=== 今日挂单（%s 预案）===' % td.get('tradeDate'))
    for o in orders:
        print('  %-8s %-6s q=%5.1f  昨收 %6s  站上昨收 %6s  追价 %6s  止盈 %6s  %-10s %s'
              % (o['code'], (o.get('name') or '')[:6], o['q'],
                 o.get('prevClose') or '—', o.get('anchor') or '—',
                 o.get('trig') or '—', o.get('tp') or '—',
                 o.get('stateZh') or '', o.get('note') or ''))
    print('\n[OK] 已写 %s' % LEDGER)
    return 0


if __name__ == '__main__':
    sys.exit(main())

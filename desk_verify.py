#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""desk_verify.py —— 交易预案的次日对账（R100z185，2026-10-24）

## 为什么需要它

`signal_rank.py` 每天收盘后给出一张执行台：8 只票，每只带
`abandon / entryLo / entryHi / chase / stop / target`。这张表**从来没被验证过**——
生成完就放着，第二天没有人回头看「开盘给没给到价格」「昨天说能买的后来涨没涨」。
没有这一步，执行台就只是一张收盘排名表，不是交易预案。

用户的原话：「开盘精选跑的时候正好能验证交易预案里提供的标的，查验该标的当天的强弱，
验证你前一日的判断，并告知我今日哪几只标的可以买入（次日卖出）。」

## 三级判定（开盘后一刻钟，用真实开盘价）

| 开盘价位置 | 状态 | 含义 |
|---|---|---|
| ≥ 9.5% | `limit` | 一字板，买不到 |
| > chase | `chase` | 追高，不追（进去风险收益比已不对） |
| entryLo ~ entryHi | `buy` | **可建仓** |
| abandon ~ entryLo | `wait` | 未到买点，等回踩 |
| ≤ abandon | `drop` | 破位，昨日多头结构没成立 |

## 闭环怎么闭上

每次 `--check` 写一条进 `desk_verify.json`（含当日 `buy` 清单）。
**下一次** `--check` 会先结算上一次的清单：买入价 = 那天的开盘，
卖出价 = 今天的开盘（A 股 T+1 合法的最短持有），算真实收益与胜率。
这样「昨日判断准不准」每天都有答案，而不是等回测。

## 输出

`desk_verify.json`（不进 data.js，不碰任何线上字段）：
    {days: {日期: {date, checkedAt, verdicts:[…], buys:[…], settled:[…]}},
     stats: {n, win, mean, byVerdict}}

用法：
    run-python desk_verify.py --check            # 09:45 用：判定今日 + 结算昨日
    run-python desk_verify.py --plan-day 2026-10-21   # 回放：自建预案再判定（自检）
    run-python desk_verify.py --report           # 只看累计战绩
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

# 状态码 → 中文（页面与推送共用，禁写英文）
STATE_ZH = {
    'buy': '可买',
    'wait': '等回踩',
    'chase': '追高不追',
    'drop': '破位放弃',
    'limit': '一字板买不到',
}


# ---------------------------------------------------------------- 行情
def fetch_quotes(codes):
    """腾讯 gtimg 批量快照 → {code: {name, open, prevClose, now, pct}}；失败 {}。

    只要 open / prevClose 就能做全部判定；now 用于「已建仓后是否触及止损」。
    """
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


# ---------------------------------------------------------------- 判定
def judge_one(pick, q):
    """单只判定。返回 dict；q 为 None 时给 unknown。"""
    pl = pick.get('plan') or {}
    lo, hi = pl.get('entryLo'), pl.get('entryHi')
    ab, ch = pl.get('abandon'), pl.get('chase')
    base = {'code': pick.get('code'), 'name': pick.get('name'),
            'q': pick.get('q'), 'srcs': pick.get('srcs') or [],
            'entryLo': lo, 'entryHi': hi, 'abandon': ab, 'chase': ch,
            'stop': pick.get('stop'), 'target': pick.get('target')}
    if not q or not q.get('open') or not q.get('prevClose'):
        return dict(base, state='unknown', openPct=None,
                    note='行情缺失，无法判定（不要凭猜写结论）')
    op, pc = q['open'], q['prevClose']
    open_pct = round((op / pc - 1) * 100, 2)
    base['open'] = round(op, 2)
    base['openPct'] = open_pct
    if open_pct >= 9.5:
        return dict(base, state='limit', note='一字板，开盘买不到')
    if ch is not None and op > ch:
        return dict(base, state='chase', note='开在追高线上方，不追')
    if ab is not None and op <= ab:
        return dict(base, state='drop', note='开在放弃线下方，昨日多头结构没成立')
    if lo is not None and hi is not None and lo <= op <= hi:
        return dict(base, state='buy', note='落在建仓区间，可按计划买入')
    return dict(base, state='wait', note='低于建仓区间上沿但未破放弃线，等回踩')


def check(plan, quotes, today):
    """对整张预案判定。返回 (verdicts, buys)。"""
    verdicts, buys = [], []
    for p in plan:
        v = judge_one(p, quotes.get(p.get('code')))
        verdicts.append(v)
        if v['state'] == 'buy':
            buys.append({'code': v['code'], 'name': v['name'],
                         'entry': v['open'], 'stop': v['stop'],
                         'target': v['target'], 'openPct': v['openPct']})
    return verdicts, buys


# ---------------------------------------------------------------- 结算
def settle(prev, quotes, today):
    """用今日开盘价结算上一次的「可买」清单。返回结算明细。

    口径：买入价 = 那天的开盘（与判定同一时刻），卖出价 = 今天的开盘。
    A 股 T+1：当天买入次日才能卖，所以「今日开盘卖」是合法的最短持有。
    """
    out = []
    for b in prev.get('buys') or []:
        q = quotes.get(b.get('code'))
        if not q or not q.get('open'):
            continue
        ret = (q['open'] / b['entry'] - 1) * 100
        out.append({'code': b['code'], 'name': b['name'],
                    'entry': b['entry'], 'exit': q['open'],
                    'holdDays': 1, 'ret': round(ret, 2),
                    'retNet': round(ret - 0.2, 2),   # 双边成本约 0.20%
                    'win': ret - 0.2 > 0})
    return out


def stats(days):
    rows = [s for d in days.values() for s in (d.get('settled') or [])]
    if not rows:
        return {'n': 0, 'note': '尚无结算样本'}
    n = len(rows)
    rets = [r['retNet'] for r in rows]
    return {'n': n,
            'win': round(len([r for r in rows if r['win']]) / n * 100, 1),
            'mean': round(sum(rets) / n, 2),
            'best': round(max(rets), 2),
            'worst': round(min(rets), 2),
            'note': '净收益已扣 0.20% 双边成本'}


def is_trading_day():
    """以 cal_factor.py 的 aShareClosed 为硬依据（与 09:45 同源，不自己判周末）。

    周六跑 --check 会拿到上一个交易日的开盘价，判定出来的是「周五能不能买」，
    却写在今天的台账里——错位一天，后面所有结算全错。宁可不跑。
    """
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, 'cal_factor.py'), '--json'],
                           capture_output=True, timeout=30)
        d = json.loads(r.stdout.decode('utf-8', errors='ignore') or '{}')
        return not d.get('aShareClosed'), d.get('tags') or ''
    except Exception as e:                                   # noqa: BLE001
        return None, '日历脚本不可用：%s' % e


# ---------------------------------------------------------------- 台账
def load_ledger():
    if not os.path.exists(LEDGER):
        return {'days': {}, 'stats': {}}
    try:
        d = json.load(open(LEDGER, encoding='utf-8'))
        d.setdefault('days', {})
        return d
    except Exception:
        return {'days': {}, 'stats': {}}


def save_ledger(L):
    L['stats'] = stats(L['days'])
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
        plan = R.exec_plan(one['close'], one['stop'], one['target'], atr)
        if not plan:
            continue
        cand.append({'code': c, 'name': one.get('name') or '', 'q': q,
                     'stop': one['stop'], 'target': one['target'],
                     'plan': plan, 'srcs': []})
    cand.sort(key=lambda x: (-x['q'], x['code']))
    return cand[:max_pick]


def backtest_day(D, plan_day):
    """回放：plan_day 收盘出预案 → 次日开盘判定 → 再次日开盘结算。"""
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
    # 用 K 线伪造「开盘快照」
    quotes = {}
    for c, raw in K.items():
        b = A.norm_bars(raw)
        m = {r[0]: r for r in b}
        if d1 in m and m[d1][1]:
            j = [r[0] for r in b].index(d1)
            quotes[c] = {'open': m[d1][1], 'prevClose': b[j - 1][2] if j else None,
                         'now': m[d1][2], 'pct': None}
    verdicts, buys = check(picks, quotes, d1)
    print('=== 回放 %s 收盘出预案 → %s 开盘判定 ===' % (plan_day, d1))
    for v in verdicts:
        print('  %-8s %-6s q=%5.1f 开%+6.2f%%  %-6s %s'
              % (v['code'], (v.get('name') or '')[:6], v['q'],
                 v.get('openPct') or 0, STATE_ZH.get(v['state'], v['state']), v['note']))
    # 结算：d1 开盘买 → d2 开盘卖
    q2 = {}
    for c, raw in K.items():
        b = A.norm_bars(raw)
        m = {r[0]: r for r in b}
        if d2 in m:
            q2[c] = {'open': m[d2][1]}
    prev = {'buys': buys}
    st = settle(prev, q2, d2)
    print('\n=== %s 开盘卖出结算（T+1 最短持有）===' % d2)
    for s in st:
        print('  %-8s %-6s 买 %.2f → 卖 %.2f  %+.2f%%（净 %+.2f%%）%s'
              % (s['code'], (s['name'] or '')[:6], s['entry'], s['exit'],
                 s['ret'], s['retNet'], '赢' if s['win'] else '亏'))
    if st:
        n = len(st)
        print('  合计 %d 笔：胜率 %.1f%%，平均净 %+.2f%%'
              % (n, len([x for x in st if x['win']]) / n * 100,
                 sum(x['retNet'] for x in st) / n))
    if not buys:
        print('\n  今日无可买标的（全部追高/等回踩/破位）——这本身是一个有效结论')
    return 0


# ---------------------------------------------------------------- 自检
def self_test():
    ok = True

    def chk(name, got):
        nonlocal ok
        print(('  OK   ' if got else '  FAIL ') + name)
        ok = ok and bool(got)

    base = {'code': '000001', 'name': '测试', 'q': 90, 'stop': 9.0, 'target': 12.0,
            'plan': {'abandon': 9.5, 'entryLo': 10.0, 'entryHi': 10.6, 'chase': 11.2}}
    # prevClose 逐档调，否则「跳空 ≥9.5%」会先于 chase 命中，测不出追高分支
    q = lambda op, pc=10.0: {'open': op, 'prevClose': pc, 'now': op}
    chk('开在区间内 → buy', judge_one(base, q(10.3))['state'] == 'buy')
    chk('开在区间上沿 → buy', judge_one(base, q(10.6))['state'] == 'buy')
    chk('开在区间下沿 → buy', judge_one(base, q(10.0))['state'] == 'buy')
    chk('开在追高线上方 → chase', judge_one(base, q(11.3, 10.6))['state'] == 'chase')
    chk('开在放弃线下方 → drop', judge_one(base, q(9.4))['state'] == 'drop')
    chk('开在放弃线与下沿之间 → wait', judge_one(base, q(9.8))['state'] == 'wait')
    chk('一字板 → limit', judge_one(base, q(11.0))['state'] == 'limit')
    chk('行情缺失 → unknown 不硬判', judge_one(base, None)['state'] == 'unknown')

    # 结算：买 10，次日开 10.5 → +5%，净 +4.8%
    prev = {'buys': [{'code': '000001', 'name': '测试', 'entry': 10.0}]}
    st = settle(prev, {'000001': {'open': 10.5}}, '2026-10-27')
    chk('结算收益 = +5.0（实测 %s）' % (st[0]['ret'] if st else None),
        st and abs(st[0]['ret'] - 5.0) < 0.01)
    chk('结算净 = +4.8', st and abs(st[0]['retNet'] - 4.8) < 0.01)
    chk('亏损样本判负', not settle(prev, {'000001': {'open': 9.5}}, 'x')[0]['win'])

    print('[SELF-TEST] ' + ('OK' if ok else 'FAIL'))
    return 0 if ok else 1


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='判定今日 + 结算昨日')
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
        s = stats(L['days'])
        print(json.dumps(s, ensure_ascii=False, indent=1) if a.json else
              '累计 %d 笔｜胜率 %.1f%%｜平均净 %+.2f%%｜最好 %+.2f%%｜最差 %+.2f%%'
              % (s['n'], s['win'], s['mean'], s['best'], s['worst']))
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
        print('[WARN] 预案 validUntil=%s 已过期（今天 %s）——不做判定，避免拿旧价位当今日指令'
              % (valid, today))
        return 0

    codes = [p.get('code') for p in plan]
    L = load_ledger()
    pd = prev_trading_day(L['days'], today)
    prev_codes = [b.get('code') for b in (L['days'].get(pd, {}).get('buys') or [])] if pd else []
    quotes = fetch_quotes(list(dict.fromkeys(codes + prev_codes)))

    verdicts, buys = check(plan, quotes, today)
    settled = settle(L['days'].get(pd) or {}, quotes, today) if pd else []

    L['days'][today] = {
        'date': today,
        'checkedAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'planDay': td.get('tradeDate'),
        'verdicts': verdicts,
        'buys': buys,
        'settled': settled,
        'settledFrom': pd,
    }
    save_ledger(L)

    # 页面用的一层：只放「今天该怎么做」与「昨天判断准不准」，不带原始行情
    sn = len(settled)
    payload = {
        'planDate': str(td.get('tradeDate') or '')[:10],
        'validUntil': str(td.get('validUntil') or '')[:10],
        'checkedAt': L['days'][today]['checkedAt'],
        'settledFrom': pd or '',
        'settledN': sn,
        'settledWin': round(len([x for x in settled if x['win']]) / sn * 100, 1) if sn else None,
        'settledMeanNet': round(sum(x['retNet'] for x in settled) / sn, 2) if sn else None,
        'settledRows': settled,
        'verdicts': [{k: v.get(k) for k in
                      ('code', 'name', 'q', 'open', 'openPct', 'state', 'note')} for v in verdicts],
        'buys': buys,
        'summary': ('预案 %d 只，开盘可买 %d 只（%s）；其余 %s'
                    % (len(verdicts), len(buys),
                       '、'.join('%s %s' % (b['code'], (b['name'] or '')[:6]) for b in buys) or '无',
                       '追高/等回踩/破位')),
    }
    for v in payload['verdicts']:
        v['stateZh'] = STATE_ZH.get(v['state'], v['state'])

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

    print('=== 昨日判断结算（%s 的「可买」清单 → 今日开盘卖）===' % (pd or '—'))
    if not settled:
        print('  （无，首日或昨日无可买标的）')
    for s in settled:
        print('  %-8s %-6s 买 %.2f → 卖 %.2f  %+6.2f%%（净 %+6.2f%%）%s'
              % (s['code'], (s['name'] or '')[:6], s['entry'], s['exit'],
                 s['ret'], s['retNet'], '赢' if s['win'] else '亏'))
    if settled:
        n = len(settled)
        print('  小计 %d 笔：胜率 %.1f%%，平均净 %+.2f%%'
              % (n, len([x for x in settled if x['win']]) / n * 100,
                 sum(x['retNet'] for x in settled) / n))

    print('\n=== 今日预案判定（%s 预案，%s 开盘）===' % (td.get('tradeDate'), today))
    for v in verdicts:
        print('  %-8s %-6s q=%5.1f  开%+6.2f%%  %-8s %s'
              % (v['code'], (v.get('name') or '')[:6], v['q'],
                 v.get('openPct') or 0, STATE_ZH.get(v['state'], v['state']), v['note']))

    print('\n=== 今日可买（次日开盘卖出）===')
    if not buys:
        print('  无。全部追高/等回踩/破位 —— 今天不出手也是决策。')
    for b in buys:
        print('  %-8s %-6s 买入 %.2f  止损 %.2f  目标 %.2f'
              % (b['code'], (b['name'] or '')[:6], b['entry'],
                 b['stop'] or 0, b['target'] or 0))
    print('\n[OK] 已写 %s' % LEDGER)
    return 0


if __name__ == '__main__':
    sys.exit(main())

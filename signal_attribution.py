#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""signal_attribution.py —— 指标归因台账与建议权重（R100z184，2026-10-24）

## 要解决什么

这套系统有四个量化选股模块（谐波形态 / 吸筹打分 / 九门强势 / 断板反包）加一个
AI 预测。它们**各自独立推荐**，但从来没有人回答一个最基本的问题：

    **这四个指标，哪一个现在是准的？**

没有这个答案，四个指标就只能等权看待——等于把「已经证明有效的信号」和
「一直亏钱的信号」按同样可信度摆在一起。这是「四指标选股」只能拿 4 分的根本原因：
**有指标，没归因**。

## 怎么做

**台账**（`signal_ledger.json`）：每天把每个指标选出的标的登记一次，记录登记日
与登记日收盘。之后回扫 K 线，算每个标的 **T+1 / T+3 / T+5 / T+10** 的收益，
以及与**全市场等权基准**的差额（超额）。

用全市场等权而不是上证指数作基准，理由：
- 上证是市值加权，被银行石油绑架，中小盘涨跌它看不出来；
- 这套系统的标的绝大多数是中小盘，拿上证比会系统性高估或低估；
- 全市场等权（1,300+ 只当日涨幅中位数累乘）就地可算、零外部依赖、可复现。

**建议权重**：只有样本够（≥ MIN_N 条 且 ≥ MIN_DAYS 个交易日）的指标才参与定权，
权重 ∝ max(0, 超额 + 1)，超额为负的指标权重归零。样本不足时**不给结论**，
显示「积累中」——宁可空着，也不拿 3 个样本编一个排名。

**冷启动**：台账第一天是空的，但 `stop_ledger.json` 已有 12 个交易日、1,900 条
真实回算样本，其 `stopSrc` 能区分「谐波形态位 / AI 自选 / 断板 T 日低点 / 机械池
兜底」四类。这一份先当「止损口径归因」贴出来，让页面第一天就有真数据可看，
同时与新建的指标台账分开标注，不混成一个数。

## 输出

写 data.js 顶层 `signalPerf`。前端「指标表现」卡渲染：每个指标一行（样本数、
5 日超额、胜率、判定），底部给建议权重与数据来源说明。

用法：
    python3 signal_attribution.py            # 登记今日 + 回算 + 写 data.js
    python3 signal_attribution.py --view     # 只打印统计，不登记
    python3 signal_attribution.py --dry      # 不写盘
    python3 signal_attribution.py --self-test
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common          # noqa: E402
import atr_stop as A   # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(ROOT, 'signal_ledger.json')
STOP_LEDGER = os.path.join(ROOT, 'stop_ledger.json')

HORIZONS = (1, 3, 5, 10)
MIN_N = 30          # 出结论最少样本数
MIN_DAYS = 5        # 出结论最少覆盖交易日

SRC_LABEL = {
    'harmonic': '谐波形态',
    'accumulation': '吸筹打分',
    'powerScreen': '九门强势',
    'duanban': '断板反包',
    'ai': 'AI 预测',
    'mech': '机械池（吸筹+九门）',
}

# stop_ledger 的 stopSrc → 主台账键名。stop_ledger 无法区分吸筹与九门
# （两者都落进 default-3%），所以合成一个 'mech'，不硬拆成两个假精度。
SRC_LEGACY_MAP = {'harmonic': 'harmonic', 'ai-exit': 'ai',
                  'T日低点': 'duanban', 'default-3.0%': 'mech', 'default-3%': 'mech'}


def _f(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def load_ledger():
    if os.path.exists(LEDGER):
        try:
            return json.load(open(LEDGER, encoding='utf-8'))
        except Exception:
            pass
    return {'entries': {}}


def save_ledger(L):
    common.write_json_atomic(LEDGER, L, indent=1)


def market_bench(K):
    """全市场等权日收益（当日中位数）与累乘基准。

    返回 {'daily': {day: pct}, 'cum': {day: 累计涨幅%}}。
    只在当天有 ≥50 只有效样本时才算（样本太少的日期不产出基准，宁可缺）。
    """
    by_day = {}
    for code, bars in K.items():
        b = A.norm_bars(bars)
        for i in range(1, len(b)):
            p0, p1 = b[i - 1][2], b[i][2]
            if p0 and p1 and p0 > 0:
                by_day.setdefault(b[i][0], []).append((p1 - p0) / p0 * 100)
    daily = {}
    for d, v in by_day.items():
        if len(v) >= 50:
            v.sort()
            daily[d] = v[len(v) // 2]
    cum, acc = {}, 1.0
    for d in sorted(daily):
        acc *= (1 + daily[d] / 100)
        cum[d] = (acc - 1) * 100
    return {'daily': daily, 'cum': cum}


def snapshot(D):
    """今日各指标选出的标的（code/name/sector/close）。按指标分组，同指标内去重。"""
    K = D.get('stkKlines') or {}
    out = {}
    for code, meta in A.collect(D):
        bars = A.norm_bars(K.get(code))
        close = bars[-1][2] if bars else None
        for s in meta['_srcs']:
            grp = out.setdefault(s, {})
            if code not in grp:
                grp[code] = {'code': code, 'name': meta['name'],
                             'sector': meta['sector'], 'close': close}
    return {s: list(v.values()) for s, v in out.items()}


def fwd_ret(bars, entry_day, n):
    """登记日之后第 n 个交易日的收益（%）。找不到登记日就返回 None，绝不补 0。"""
    idx = None
    for i, b in enumerate(bars):
        if b[0] == entry_day:
            idx = i
            break
    if idx is None or idx + n >= len(bars):
        return None
    c0, c1 = bars[idx][2], bars[idx + n][2]
    if not c0 or c0 <= 0 or c1 is None:
        return None
    return round((c1 - c0) / c0 * 100, 3)


def bench_ret(bench, entry_day, n):
    """基准同期收益：从登记日次日开始累乘 n 个交易日。"""
    days = sorted(bench['daily'])
    if entry_day not in days:
        return None
    i = days.index(entry_day)
    if i + n >= len(days):
        return None
    acc = 1.0
    for d in days[i + 1:i + 1 + n]:
        acc *= (1 + bench['daily'][d] / 100)
    return round((acc - 1) * 100, 3)


def compute(L, D):
    """回算全台账，按指标 × 持有期出统计。"""
    K = D.get('stkKlines') or {}
    bench = market_bench(K)
    stats = {}
    for day, groups in sorted((L.get('entries') or {}).items()):
        for src, items in groups.items():
            st = stats.setdefault(src, {
                'n': 0, 'days': set(),
                'h': {str(h): {'rets': [], 'excs': []} for h in HORIZONS},
            })
            st['days'].add(day)
            for it in items:
                bars = A.norm_bars(K.get(it.get('code')))
                if len(bars) < 2:
                    continue
                got = False
                for h in HORIZONS:
                    r = fwd_ret(bars, day, h)
                    if r is None:
                        continue
                    got = True
                    st['h'][str(h)]['rets'].append(r)
                    b = bench_ret(bench, day, h)
                    if b is not None:
                        st['h'][str(h)]['excs'].append(r - b)
                if got:
                    st['n'] += 1
    return stats, bench


def summarize(stats):
    """把原始列表压成页面要用的统计量。"""
    out = {}
    for src, st in stats.items():
        rec = {'label': SRC_LABEL.get(src, src), 'n': st['n'], 'days': len(st['days']),
               'h': {}}
        for h in HORIZONS:
            d = st['h'][str(h)]
            rets, excs = d['rets'], d['excs']
            rec['h'][str(h)] = {
                'n': len(rets),
                'ret': round(sum(rets) / len(rets), 2) if rets else None,
                'exc': round(sum(excs) / len(excs), 2) if excs else None,
                'win': round(len([x for x in rets if x > 0]) / len(rets), 3) if rets else None,
                'winExc': (round(len([x for x in excs if x > 0]) / len(excs), 3)
                           if excs else None),
            }
        rec['sampleEnough'] = (rec['n'] >= MIN_N and rec['days'] >= MIN_DAYS)
        out[src] = rec
    return out


def suggest_weights(summary):
    """建议权重。只有样本够的指标参与，超额为负的归零。"""
    usable = {s: r for s, r in summary.items() if r['sampleEnough']}
    if not usable:
        return {}, '样本不足（需 ≥%d 条且 ≥%d 个交易日），暂不给建议权重' % (MIN_N, MIN_DAYS)
    raw = {}
    for s, r in usable.items():
        e = r['h']['5'].get('exc')
        raw[s] = max(0.0, (e if e is not None else 0.0) + 1.0)
    tot = sum(raw.values())
    if tot <= 0:
        return {}, '所有指标的 5 日超额都为负或不可用，不给权重（等权也未必该买）'
    w = {s: round(v / tot, 3) for s, v in raw.items() if v > 0}
    zero = [SRC_LABEL.get(s, s) for s, v in raw.items() if v <= 0]
    msg = '按 5 日超额定权（超额+1 归一化）'
    if zero:
        msg += '；%s 超额为负，权重归零' % '、'.join(zero)
    return w, msg


def stop_evidence():
    """从 stop_ledger 取「止损口径」的现成回算结论，作冷启动证据。

    ⚠️ 这与指标归因**不是同一个口径**：它衡量的是「该止损位会不会被触及」，
    不是「该指标选的票涨不涨」。所以单独放在 `stopEvidence` 里、页面分开标注，
    绝不和 bySrc 混在一起算——混了就成了用一个口径的结论冒充另一个口径。
    """
    if not os.path.exists(STOP_LEDGER):
        return None
    try:
        L = json.load(open(STOP_LEDGER, encoding='utf-8'))
    except Exception:
        return None
    st = L.get('stats') or {}
    src_label = {'harmonic': '谐波形态位', 'ai-exit': 'AI 自选 −3%',
                 'T日低点': '断板 T 日低点', 'default-3.0%': '机械池 −3%',
                 'default-3%': '机械池 −3%'}
    rows = []
    by_touch = st.get('bySrcTouch') or {}
    by_p5 = st.get('bySrc') or {}
    for k, v in by_touch.items():
        p5 = (by_p5.get(k) or {}).get('p5')
        rows.append({'src': k, 'label': src_label.get(k, k), 'n': v.get('n'),
                     'touchRate': v.get('rate'), 'p5': p5})
    rows.sort(key=lambda x: -(x['p5'] if x['p5'] is not None else -99))
    return {'updatedAt': L.get('updatedAt'), 'total': st.get('total'),
            'touched': st.get('touched'), 'rows': rows}


def build(D, do_snapshot=True):
    L = load_ledger()
    ent = L.setdefault('entries', {})
    trade_date = str((D.get('ashare') or {}).get('tradeDate')
                     or (D.get('aiPrediction') or {}).get('date') or '')[:10]

    if do_snapshot and trade_date:
        snap = snapshot(D)
        cur = ent.get(trade_date) or {}
        added = 0
        for src, items in snap.items():
            known = {x['code'] for x in cur.get(src, [])}
            new = [x for x in items if x['code'] not in known]
            if new:
                cur[src] = (cur.get(src) or []) + new
                added += len(new)
        if cur:
            ent[trade_date] = cur
        if added:
            print('[OK] 登记 %s：新增 %d 条指标选股记录' % (trade_date, added))

    stats, bench = compute(L, D)
    summ = summarize(stats)

    # 冷启动：新台账第一天必然回算不出样本（今天的票还没有明天）。此时代用
    # stop_ledger 的历史样本顶上，并把 dataSource 标成 stop_ledger，
    # 前端据此显示「数据来源」，不让读者误以为这是新台账算出来的。
    legacy, legacy_at, legacy_days = stats_from_stops(D, bench)
    data_source = 'signal_ledger'
    if legacy and not any(r['sampleEnough'] for r in summ.values()):
        data_source = 'stop_ledger'
        for lsrc, r in legacy.items():
            key = SRC_LEGACY_MAP.get(lsrc, lsrc)
            if (summ.get(key) or {}).get('sampleEnough'):
                continue          # 新台账已经够了，不用旧的覆盖
            summ[key] = {
                'label': SRC_LABEL.get(key, key),
                'n': r['n'], 'days': r['days'],
                'h': {str(h): {'n': r['n'], 'ret': None, 'exc': None,
                               'win': None, 'winExc': None} for h in HORIZONS},
                'sampleEnough': r['n'] >= MIN_N and r['days'] >= MIN_DAYS,
                'legacy': True,
                'touchRate': r['touchRate'],
            }
            summ[key]['h']['5'] = {'n': r['n'], 'ret': r['ret5'], 'exc': r['exc5'],
                                   'win': r['win5'], 'winExc': None}

    weights, wmsg = suggest_weights(summ)

    ranking = []
    for s, r in summ.items():
        e5 = r['h']['5'].get('exc')
        if not r['sampleEnough']:
            verdict = '积累中（%d 条/%d 日）' % (r['n'], r['days'])
        elif e5 is None:
            verdict = '不可判（5 日超额缺失）'
        elif e5 > 0.5 and (r['h']['5'].get('winExc') or 0.5) > 0.5:
            verdict = '有效'
        elif e5 > 0:
            verdict = '弱有效'
        else:
            verdict = '无效（跑输基准）'
        ranking.append({'src': s, 'label': r['label'], 'n': r['n'], 'days': r['days'],
                        'ret5': r['h']['5'].get('ret'), 'exc5': e5,
                        'win5': r['h']['5'].get('winExc'),
                        'touchRate': r.get('touchRate'),
                        'verdict': verdict, 'sampleEnough': r['sampleEnough'],
                        'legacy': bool(r.get('legacy')),
                        'weight': weights.get(s)})
    ranking.sort(key=lambda x: (-(x['exc5'] if x['exc5'] is not None else -99), -x['n']))

    if do_snapshot and ent:
        L['updatedAt'] = _now()
        save_ledger(L)

    return {
        'updatedAt': _now(),
        'asOf': trade_date,
        'horizons': list(HORIZONS),
        'criteria': {'minN': MIN_N, 'minDays': MIN_DAYS,
                     'bench': '全市场等权（1300+ 只当日涨幅中位数累乘）',
                     'excess': '标的收益 − 同期基准收益'},
        'bySrc': summ,
        'ranking': ranking,
        'weights': weights,
        'weightNote': wmsg,
        'dataSource': data_source,
        'dataSourceNote': (
            '新台账（signal_ledger）刚开始登记，样本不足以回算；当前使用 stop_ledger 的 '
            '%d 个交易日历史样本（截至 %s）。新台账样本达标后自动切换。'
            % (legacy_days, legacy_at or '—') if data_source == 'stop_ledger' else
            '来自 signal_ledger（本指标台账，逐日登记、逐日回算）'),
        'stopEvidence': stop_evidence(),
        'ledger': {'dates': len(ent), 'entries': sum(len(v) for g in ent.values()
                                                     for v in g.values())},
    }


def stats_from_stops(D, bench):
    """用 stop_ledger 的历史条目算「按指标来源的选股收益 + 超额」。

    ## 为什么可以直接用

    之前我把它当成「止损口径、不能当选股用」而排除掉，这是错的：
    stop_ledger 的每条记录都是**某个指标在某个交易日选出的票**，其 `p5Pct`
    是「入场后第 5 个交易日相对入场价的涨跌」——**与止损设在哪无关**，
    就是选股收益本身。只有 `touchRate` 才是止损口径。

    所以这张表同时给出两列，页面分开标注：
        ret5  选股能力（这个指标选的票 5 日赚不赚）
        touch 止损口径（这个指标的止损位合不合适）

    局限（必须显示出来，不能假装没有）：
      · `stopSrc` 无法区分「吸筹」与「九门」（两者都落进 default-3%）；
      · 同一只票被多指标选中时，stop_ledger 只记首个来源（先到先得），
        会低估共振票。这两点由新的 signal_ledger 逐步接管。

    返回 (out, updatedAt, 覆盖交易日数)，out 形如 {src: {...}}，
    src 用与主台账一致的键名，便于前端统一渲染。
    """
    if not os.path.exists(STOP_LEDGER):
        return {}, None, 0
    try:
        L = json.load(open(STOP_LEDGER, encoding='utf-8'))
    except Exception:
        return {}, None, 0
    ent = L.get('entries') or {}
    K = D.get('stkKlines') or {}
    acc = {}
    for day, items in sorted(ent.items()):
        for it in items:
            src = it.get('stopSrc') or it.get('from') or 'unknown'
            bars = A.norm_bars(K.get(str(it.get('code'))))
            if len(bars) < 2:
                continue
            r5 = fwd_ret(bars, day, 5)
            if r5 is None:
                continue
            b5 = bench_ret(bench, day, 5)
            a = acc.setdefault(src, {'rets': [], 'excs': [], 'touch': 0, 'n': 0, 'days': set()})
            a['rets'].append(r5)
            if b5 is not None:
                a['excs'].append(r5 - b5)
            a['n'] += 1
            a['days'].add(day)
            if (it.get('res') or {}).get('touched'):
                a['touch'] += 1
    out = {}
    all_days = set()
    for src, a in acc.items():
        all_days |= a['days']
        out[src] = {
            'n': a['n'], 'days': len(a['days']),
            'ret5': round(sum(a['rets']) / len(a['rets']), 2) if a['rets'] else None,
            'exc5': round(sum(a['excs']) / len(a['excs']), 2) if a['excs'] else None,
            'win5': round(len([x for x in a['rets'] if x > 0]) / len(a['rets']), 3)
            if a['rets'] else None,
            'touchRate': round(a['touch'] / a['n'], 3) if a['n'] else None,
        }
    return out, L.get('updatedAt'), len(all_days)


def _now():
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def self_test():
    """自检：收益计算、基准累乘、样本门槛、权重归零四件事。"""
    ok = True

    def chk(name, got):
        nonlocal ok
        print(('  OK   ' if got else '  FAIL ') + name)
        ok = ok and bool(got)

    # ① 前向收益：登记日收盘 100，之后 1/3/5/10 日分别 101/103/105/110
    bars = [('2026-10-01', 100, 100, 101, 99, 1e6),
            ('2026-10-02', 100, 101, 102, 100, 1e6),
            ('2026-10-03', 101, 101, 102, 100, 1e6),
            ('2026-10-04', 101, 103, 104, 101, 1e6),
            ('2026-10-05', 103, 104, 105, 103, 1e6),
            ('2026-10-06', 104, 105, 106, 104, 1e6),
            ('2026-10-07', 105, 106, 107, 105, 1e6),
            ('2026-10-08', 106, 107, 108, 106, 1e6),
            ('2026-10-09', 107, 108, 109, 107, 1e6),
            ('2026-10-10', 108, 109, 110, 108, 1e6),
            ('2026-10-11', 109, 110, 111, 109, 1e6)]
    b = A.norm_bars(bars)
    chk('T+1 收益 = +1.0（实测 %s）' % fwd_ret(b, '2026-10-01', 1), fwd_ret(b, '2026-10-01', 1) == 1.0)
    chk('T+3 收益 = +3.0（实测 %s）' % fwd_ret(b, '2026-10-01', 3), fwd_ret(b, '2026-10-01', 3) == 3.0)
    chk('T+10 收益 = +10.0（实测 %s）' % fwd_ret(b, '2026-10-01', 10),
        fwd_ret(b, '2026-10-01', 10) == 10.0)
    chk('未来不够 → None（不补 0）', fwd_ret(b, '2026-10-05', 10) is None)
    chk('登记日不存在 → None', fwd_ret(b, '2026-09-01', 1) is None)

    # ② 基准累乘：连续 +10% 两日 → +21%
    bench = {'daily': {'2026-10-01': 0.0, '2026-10-02': 10.0, '2026-10-03': 10.0},
             'cum': {}}
    chk('基准 2 日累乘 = +21.0（实测 %s）' % bench_ret(bench, '2026-10-01', 2),
        abs((bench_ret(bench, '2026-10-01', 2) or 0) - 21.0) < 0.01)

    # ③ 样本门槛：不足不给权重
    thin = {'harmonic': {'label': '谐波形态', 'n': 2, 'days': 1, 'h': {
        str(h): {'n': 2, 'ret': 5.0, 'exc': 5.0, 'win': 1.0, 'winExc': 1.0} for h in HORIZONS},
        'sampleEnough': False}}
    w, msg = suggest_weights(thin)
    chk('样本不足 → 权重为空', w == {})
    chk('样本不足 → 说明写明需多少', '需' in msg)

    # ④ 权重：超额为正的才有权重，负的归零；合计为 1
    good = {}
    for s, e in (('harmonic', 2.0), ('accumulation', -1.5), ('powerScreen', 1.0)):
        good[s] = {'label': s, 'n': 100, 'days': 10, 'sampleEnough': True,
                   'h': {str(h): {'n': 100, 'ret': e, 'exc': e, 'win': 0.6, 'winExc': 0.6}
                         for h in HORIZONS}}
    w2, msg2 = suggest_weights(good)
    chk('超额为正的指标有权重', 'harmonic' in w2 and 'powerScreen' in w2)
    chk('超额为负的指标权重归零（不在表里）', 'accumulation' not in w2)
    chk('权重合计 = 1.0（实测 %s）' % round(sum(w2.values()), 3),
        abs(sum(w2.values()) - 1.0) < 0.01)
    chk('零权说明里点名了该指标', '吸筹打分' in msg2)

    print('[SELF-TEST] ' + ('OK' if ok else 'FAIL'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--view', action='store_true', help='只打印统计，不登记今日')
    ap.add_argument('--dry', action='store_true', help='不写 data.js')
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    D = common.load_dashboard_data(ROOT)
    if D is None:
        print('[ERR] data.js 解析失败', file=sys.stderr)
        return 1
    out = build(D, do_snapshot=not a.view)
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
        return 0

    print('\n=== 指标归因（谁准，谁不准）===')
    if out['dataSource'] == 'stop_ledger':
        print('[来源] ' + out['dataSourceNote'])
    print('本台账 %d 个交易日 / %d 条记录｜判定门槛 ≥%d 条且 ≥%d 日'
          % (out['ledger']['dates'], out['ledger']['entries'], MIN_N, MIN_DAYS))
    print('\n  指标  样本  覆盖  5日收益  5日超额  超额胜率  止损触及  判定')
    for r in out['ranking']:
        h5 = (out['bySrc'].get(r['src']) or {}).get('h', {}).get('5', {})
        print('  %s  %d 条/%d 日  %s  %s  %s  %s  %s'
              % (r['label'], r['n'], r['days'],
                 '—' if h5.get('ret') is None else '%+.2f%%' % h5['ret'],
                 '—' if r['exc5'] is None else '%+.2f%%' % r['exc5'],
                 '—' if r['win5'] is None else '%.0f%%' % (r['win5'] * 100),
                 '—' if r.get('touchRate') is None else '%.1f%%' % (r['touchRate'] * 100),
                 r['verdict']))
    print('\n  建议权重：' + (', '.join('%s %.2f' % (SRC_LABEL.get(k, k), v)
                                     for k, v in out['weights'].items()) or '— 未定'))
    print('  依据：' + out['weightNote'])

    se = out.get('stopEvidence')
    if se:
        print('\n  附：止损口径实测（来自 stop_ledger，%d 条已完成跟踪，触及 %d 条）'
              % (se['total'] or 0, se['touched'] or 0))
        for r in se['rows']:
            print('    %-14s 样本 %4s  触及率 %5s  入场后5日 %6s'
                  % (r['label'], r['n'],
                     '—' if r['touchRate'] is None else '%.1f%%' % (r['touchRate'] * 100),
                     '—' if r['p5'] is None else '%+.2f%%' % r['p5']))
        print('    注：此表衡量「该止损位会不会被触及」，与上面的「指标选股赚不赚」'
              '是两个口径，不可相加或互相替代。')

    if a.dry:
        print('\n（--dry，未写 data.js）')
        return 0
    D['signalPerf'] = out
    common.write_data_js(ROOT, D, '[signal_attribution]')
    print('\n[OK] 已写回 data.js 顶层 signalPerf')
    return 0


if __name__ == '__main__':
    sys.exit(main())

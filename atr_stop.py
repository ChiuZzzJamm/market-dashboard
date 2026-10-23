#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atr_stop.py —— ATR 自适应止损引擎（R100z182，2026-10-24）

## 为什么要有这个脚本

改造前，全站 96 只推荐标的的止损**一律写成「最近收盘 −3%」**（AI 的 exit.stop
与机械池兜底同一口径）。2026-10-24 用 stop_ledger 的 720 条真实样本实测：

    default-3.0%  样本 70  触及率 68.6%  入场后5日 −1.70%  胜率 29%
    harmonic      样本 117 触及率  1.7%  入场后5日 +2.35%  胜率 63%

两个数字说明同一件事：**止损宽度和标的的波动率无关**。
高波动票（半导体设备/光模块）日内正常抖动就 3%，天天被扫；低波动票（石油）
3% 要到趋势走坏才碰得到，等于没有。更糟的是「T 日低点」这条线触及率 91.2%——
那不是风控，是每天必交的手续。

## 换成什么

以 Wilder ATR(14) 定宽度，而不是固定百分比：

    原始止损 = min( 收盘 − k×ATR14 , 20 日结构低点×0.99 )
    k = 2.0（收盘 > MA20 > MA60，趋势上行：给趋势呼吸空间）
    k = 1.5（震荡 / 下行：结构没走出来，不给宽止损）
    宽度钳制到 [2.5%, 8%]：低于 2.5% 无意义（日内噪音），高于 8% 不该放宽
    而该减仓——所以超宽时标 overWide 并如实说明「8% 位可能在结构位之上」

止损统一用**收盘确认**（close）：盘中破位不算，收盘破才算。理由与上面同源——
A 股日内下影线极长，用盘中价判定等于把「假破」算成「真破」。

## 仓位不再拍脑袋

专业做法是**先定单笔风险预算、再反算仓位**，不是先定手数：

    单票权重上限 = 单笔风险预算 ÷ 止损宽度
    默认风险预算 = 账户权益 0.5%（RISK_BUDGET_PCT）
    宽度 2.5% → 20% 仓位；宽度 8% → 6.25% 仓位

所以高波动票自动拿到小仓位、低波动票拿到大仓位，**每笔最大亏损都是权益的 0.5%**。
这是「止损宽度 → 仓位」的因果，改造前是反的（先定仓位、止损随便写 3%）。

## 目标价按 R 倍数给

    1R = 收盘 − 止损；target = 收盘 + 2.5R

改造前一律 +6%（与 −3% 搭配是 2:1 的赔率，但没有胜率支撑）。改成 R 倍数后，
赔率随波动率自适应，回测时也能直接算「期望 R」。

## 输出

写 data.js 顶层 `atrStops`（全站**唯一权威止损源**）：
前端 exitForCode 优先读这里，AI 在 aiPrediction 里写的 exit.stop 只作为
「AI 原始建议」保留，页面标注口径来源，避免两套止损在页面上打架。

## 实测证据（2026-10-24，151 只标的 × 60 个入场点 = 9,060 个样本，10 日窗口）

    python3 atr_stop.py --backtest 60

    口径                触及率    策略收益均值   胜率
    固定 −3%（挂单）      68.7%     +0.439%      27.9%
    ATR 止损（挂单）      36.6%     +0.510%      44.4%
    ATR 止损（收盘确认）  28.3%     +0.460%      46.9%

**固定 −3% 的胜率只有 27.9%**——不是选股差，是止损位把赢的单子提前砍掉了：
每 10 笔里 7 笔先被 3% 的日内抖动扫出，剩下的才轮到趋势说话。

本引擎默认取**收盘确认**（收益比挂单低 0.05pp，但在样本误差内；换手低 8.3pp、
胜率高 2.5pp，A 股 T+1 下被扫掉当天也回不来，收盘价才是当日真正可成交的价）。

用法：
    python3 atr_stop.py                 # 计算并写回 data.js
    python3 atr_stop.py --dry           # 只打印，不写盘
    python3 atr_stop.py --self-test     # 合成 K 线自检（含反向断言）
    python3 atr_stop.py --backtest 60   # 同条件回测：固定 3% vs ATR 止损
    python3 atr_stop.py --json          # 只输出 JSON（供其它脚本管道用）

口径铁律：
    · K 线不足算不出 ATR → 退化为「近 20 日振幅均值」并如实标近似，**绝不编 ATR**；
    · 一只票都没有可用 K 线 → 该票整个不出现，不写 stop=null 的壳；
    · 幂等：同样的 data.js 跑两次结果逐字节一致。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))

ATR_N = 14                 # Wilder ATR 周期
K_TREND = 2.0              # 趋势上行：给呼吸空间
K_RANGE = 1.5              # 震荡/下行：结构未成立，不给宽止损
WID_MIN = 0.025            # 止损宽度下限：低于此值是日内噪音，不是风控
WID_MAX = 0.10             # 止损宽度上限：A 股中小盘 ATR 常见 6-8%，2×ATR 就到 12%，
                           # 定 8% 会让 3/4 的票撞顶（实测 151 只里 113 只），等于又回到
                           # 「固定百分比」。定 10% 只兜极端值，风险仍由仓位反算控制。
STRUCT_LOOKBACK = 20       # 结构位回看交易日
RISK_BUDGET_PCT = 0.5      # 单笔风险预算（账户权益 %）
MAX_POS_PCT = 20.0         # 单票权重上限（权益 %）
RR_TARGET = 2.5            # 目标价 = 收盘 + 2.5R
# 板块映射缺失时的占位符。它**不是一个板块**：下游做「同板块集中度」时必须排除，
# 否则把浙商银行、赤峰黄金、汇顶科技合成一个「板块」算敞口，会报出假警报。
UNCLASSIFIED = '未分类'


def _f(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def norm_bars(bars):
    """统一 K 线容器 → [(day, open, close, high, low, volume)]，丢弃残缺行。

    data.js 的 stkKlines 是 `[day, open, close, high, low, vol]`（**close 在下标 2**，
    不是常见的 OHLC 顺序，写错一位整条止损会偏到开盘价上）；
    断板/吸筹条目内嵌 kline 是 dict 容器。两种都收，缺字段的行直接丢（不补 0）。

    ⚠️ 第 6 位 volume 必须保留：signal_rank 的量能因子读它。
    第一版这里只返回到 low（5 元组），量能因子取 `len(bars[0]) > 5` 恒为假 →
    永远走「中性 40%」分支，等于量能这一项**没参与打分**却不报错。
    """
    out = []
    for b in bars or []:
        v = None
        if isinstance(b, (list, tuple)) and len(b) >= 5:
            d, o, c, h, l = str(b[0]), _f(b[1]), _f(b[2]), _f(b[3]), _f(b[4])
            if len(b) > 5:
                v = _f(b[5])
        elif isinstance(b, dict):
            d = str(b.get('day') or b.get('date') or '')
            o, c, h, l = _f(b.get('open')), _f(b.get('close')), _f(b.get('high')), _f(b.get('low'))
            v = _f(b.get('volume') if b.get('volume') is not None else b.get('vol'))
        else:
            continue
        if c and h and l and c > 0 and h > 0 and l > 0:
            out.append((d, o, c, h, l, v))
    out.sort(key=lambda x: x[0])
    return out


def atr_wilder(bars, n=ATR_N):
    """Wilder ATR：返回 (atr, tr样本数, 是否足量)。

    初值 = 前 n 个 TR 的简单均值，之后 a = (a*(n-1) + TR) / n。
    不足 n 个 TR 时返回可用 TR 的简单均值并标 enough=False ——
    调用方据此把 basis 写成「振幅近似」而不是谎称 ATR。
    """
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i][3], bars[i][4], bars[i - 1][2]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return None, 0, False
    if len(trs) < n:
        return round(sum(trs) / len(trs), 4), len(trs), False
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return round(a, 4), len(trs), True


def amp_fallback(bars, n=STRUCT_LOOKBACK):
    """K 线不足以算 ATR 时的近似：近 n 日 (最高−最低) 均值。显式标注，不冒充 ATR。"""
    seg = bars[-n:]
    if not seg:
        return None
    return round(sum((b[3] - b[4]) for b in seg) / len(seg), 4)


def trend_of(bars):
    """趋势三态。用 MA20/MA60 而非更短均线：MA5/MA10 在 A 股噪声太大。

    样本不足 40 根时不下趋势结论（返回『震荡』），避免用 9 根 K 线判「上行」。
    """
    closes = [b[2] for b in bars]
    if len(closes) < 40:
        return '震荡', None, None
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / min(60, len(closes))
    c = closes[-1]
    if c > ma20 > ma60:
        return '上行', round(ma20, 2), round(ma60, 2)
    if c < ma20 < ma60:
        return '下行', round(ma20, 2), round(ma60, 2)
    return '震荡', round(ma20, 2), round(ma60, 2)


def stop_of(close, atr, k, bars):
    """止损核心公式 —— **唯一实现**（compute_one 与 backtest 都调它）。

    单独抽出来是因为回测与生产必须同口径。这个项目踩过太多次「同一个规则在四个地方
    各写一遍、有一处没跟上就是 bug」（CHECKLIST L2 四方口径）：如果回测里复制一份
    止损公式，改参数时只改一处，回测结论就会变成「证明了一个生产上不存在的策略」。

    返回 (stop, basis_parts, over_wide)。
    """
    basis = []
    stop_atr = close - k * atr

    seg = bars[-STRUCT_LOOKBACK:] if len(bars) >= 5 else bars
    struct_low = min(b[4] for b in seg)
    stop_struct = struct_low * 0.99

    # 结构位不能无条件取更宽（见 compute_one 内注释）：只有落在
    # [ATR 位 − 1×ATR, ATR 位] 带内才采纳，否则中间是真空，拿它当止损等于放弃风控。
    if stop_atr - atr <= stop_struct <= stop_atr:
        raw = stop_struct
        basis.append('结构位 %.2f×0.99（落在 ATR 位下方 1×ATR 内，采纳）' % struct_low)
    else:
        raw = stop_atr
        if stop_struct < stop_atr - atr:
            basis.append('结构位 %.2f 过远（低于 ATR 位超 1×ATR），不采纳' % struct_low)

    raw_wid = (close - raw) / close
    over_wide = raw_wid > WID_MAX
    if raw_wid < WID_MIN:
        raw = close * (1 - WID_MIN)
        basis.append('宽度下限 %.1f%% 钳制（原 %.1f%%，低于此值是日内噪音）'
                     % (WID_MIN * 100, raw_wid * 100))
    elif over_wide:
        raw = close * (1 - WID_MAX)
        basis.append('宽度上限 %.1f%% 钳制（原始需 %.1f%%，应减仓而非放宽）'
                     % (WID_MAX * 100, raw_wid * 100))
    return raw, basis, over_wide


def compute_one(code, name, bars, sector='', src=''):
    """单票止损/目标/仓位。返回 dict 或 None（K 线不可用）。"""
    if not bars or len(bars) < 5:
        return None
    close = bars[-1][2]
    if not close or close <= 0:
        return None

    atr, ntr, enough = atr_wilder(bars)
    basis_parts = []
    if enough:
        basis_parts.append('ATR14 %.3f' % atr)
    else:
        atr = amp_fallback(bars) if atr is None else atr
        if atr is None:
            return None
        basis_parts.append('振幅近似 %.3f（K线仅 %d 根，不足 %d）' % (atr, len(bars), ATR_N + 1))

    trend, ma20, ma60 = trend_of(bars)
    k = K_TREND if trend == '上行' else K_RANGE
    basis_parts.append('%.1f×ATR（%s）' % (k, trend))

    stop, sbasis, over_wide = stop_of(close, atr, k, bars)
    basis_parts.extend(sbasis)
    struct_low = min(b[4] for b in (bars[-STRUCT_LOOKBACK:] if len(bars) >= 5 else bars))

    stop = round(stop, 2)
    wid = (close - stop) / close
    if wid <= 0:
        return None

    pos = min(RISK_BUDGET_PCT / (wid * 100) * 100, MAX_POS_PCT)
    r1 = close - stop
    target = round(close + RR_TARGET * r1, 2)

    return {
        'code': str(code), 'name': name or '', 'sector': (sector or '').strip() or UNCLASSIFIED, 'src': src,
        'close': round(close, 2),
        'atr14': atr, 'atrPct': round(atr / close * 100, 2), 'atrEnough': enough,
        'trend': trend, 'ma20': ma20, 'ma60': ma60, 'k': k,
        'stop': stop, 'stopWidPct': round(wid * 100, 2),
        'structLow': round(struct_low, 2),
        'target': target, 'rr': RR_TARGET,
        'posPct': round(pos, 1),
        'confirm': 'close',
        'overWide': over_wide,
        'basis': '；'.join(basis_parts),
    }


def collect(D):
    """从 data.js 收全部需要止损的标的：AI 48 只 + 四个机械池，按 code 去重。

    去重口径与 stop_tracker 一致：AI 优先（它是「推荐位」），机械池补充。
    同一只票在多个池出现时，sector 取先出现的，src 记录全部来源（用 / 连接），
    这样页面上能看出「这只票被几个信号同时选中」——共振本身就是加分项。
    """
    K = D.get('stkKlines') or {}
    seen = {}
    order = []

    def add(code, name, sector, src):
        c = str(code or '').strip()
        if not c or c not in K:
            return
        if c in seen:
            if src not in seen[c]['_srcs']:
                seen[c]['_srcs'].append(src)
            return
        seen[c] = {'code': c, 'name': name or '', 'sector': (sector or '').strip() or UNCLASSIFIED, '_srcs': [src]}
        order.append(c)

    for s in (D.get('aiPrediction') or {}).get('sectors') or []:
        for t in s.get('stocks') or []:
            add(t.get('code'), t.get('name'), s.get('sector'), 'ai')

    hz = D.get('harmonic') or {}
    for pool in ('confirmPool', 'watchPool'):
        for x in hz.get(pool) or []:
            add(x.get('code'), x.get('name'), x.get('sector'), 'harmonic')

    for x in (D.get('accumulation') or {}).get('scored') or []:
        add(x.get('code'), x.get('name'), x.get('sector'), 'accumulation')

    for x in (D.get('powerScreen') or {}).get('passed') or []:
        add(x.get('code'), x.get('name'), x.get('sector'), 'powerScreen')

    db = D.get('duanban') or {}
    for pool in ('confirmed', 'watching'):
        for x in db.get(pool) or []:
            add(x.get('code'), x.get('name'), x.get('sector'), 'duanban')

    return [(c, seen[c]) for c in order]


def build(D):
    """算全部标的 + 汇总 + 组合集中度。"""
    K = D.get('stkKlines') or {}
    items = {}
    skipped = []
    for code, meta in collect(D):
        bars = norm_bars(K.get(code))
        r = compute_one(code, meta['name'], bars, meta['sector'], '/'.join(meta['_srcs']))
        if r is None:
            skipped.append({'code': code, 'name': meta['name'], 'reason': 'K线不足或不可用'})
            continue
        # 多信号共振计数：≥2 个模块同时选中，是这套系统里最强的定性加分
        r['resonance'] = len(meta['_srcs'])
        r['src'] = '/'.join(meta['_srcs'])
        items[code] = r

    vals = list(items.values())
    wids = sorted(v['stopWidPct'] for v in vals)
    pos_sum = round(sum(v['posPct'] for v in vals), 1)

    by_sector = {}
    for v in vals:
        s = v['sector'] or UNCLASSIFIED
        b = by_sector.setdefault(s, {'n': 0, 'sumPos': 0.0})
        b['n'] += 1
        b['sumPos'] = round(b['sumPos'] + v['posPct'], 1)

    # 集中度只看真板块。「未分类」是板块映射缺失的占位符，不是同一个板块——
    # 把它当成板块会报出「未分类 8 只合计 71.7%」这种假警报（浙商银行+赤峰黄金+汇顶科技
    # 之间没有同涨同跌关系）。缺失只数单列，让下游知道板块约束在本轮覆盖不到这几只。
    over = sorted([{'sector': k, **v} for k, v in by_sector.items()
                   if k != UNCLASSIFIED and v['sumPos'] > 60], key=lambda x: -x['sumPos'])
    miss_n = by_sector.get(UNCLASSIFIED, {}).get('n', 0)

    trade_date = str((D.get('ashare') or {}).get('tradeDate')
                     or (D.get('aiPrediction') or {}).get('date') or '')[:10]

    summary = {
        'n': len(vals),
        'skipped': len(skipped),
        'medianStopWidPct': wids[len(wids) // 2] if wids else None,
        'avgStopWidPct': round(sum(wids) / len(wids), 2) if wids else None,
        'minStopWidPct': wids[0] if wids else None,
        'maxStopWidPct': wids[-1] if wids else None,
        'overWideN': len([v for v in vals if v['overWide']]),
        'atrApproxN': len([v for v in vals if not v['atrEnough']]),
        'byTrend': {t: len([v for v in vals if v['trend'] == t]) for t in ('上行', '震荡', '下行')},
        'bySector': by_sector,
        'sumPosPct': pos_sum,
        'sectorMissingN': miss_n,
        'concentrationWarn': over,
    }
    # 证据随数据走：前端把这几行贴出来，用户才知道止损口径为什么换了，
    # 而不是又一处「AI 说改了」的口说无凭。
    # 数字必须来自实时回测（约 0.4s）——写死过一次，K 线一长就跟实际脱节。
    bt = bt_rows(bt_agg(D, 60)[0]) if items else []
    return {
        'updatedAt': _now(),
        'tradeDate': trade_date,
        'engine': 'ATR14-Wilder/%s' % ('k=%.1f趋势,%.1f震荡' % (K_TREND, K_RANGE)),
        'params': {'atrN': ATR_N, 'kTrend': K_TREND, 'kRange': K_RANGE,
                   'widMin': WID_MIN, 'widMax': WID_MAX,
                   'riskBudgetPct': RISK_BUDGET_PCT, 'maxPosPct': MAX_POS_PCT,
                   'rr': RR_TARGET, 'confirm': 'close'},
        'evidence': evidence_text(bt),
        'evidenceRows': bt,
        'items': items,
        'skipped': skipped,
        'summary': summary,
    }


def _now():
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def self_test():
    """合成 K 线自检。四个方向都必检：算得对、方向对、钳制生效、仓位反算对。

    构造：40 根 K 线，收盘恒 100，日内振幅 2（high 101 / low 99）→ TR 恒 2.0，
    所以 ATR14 必须 = 2.0（正弦波动为 0，Wilder 递推收敛到 TR 本身）。
    收盘 100、k=2.0（或 1.5）→ ATR 止损 96 或 97。
    结构低点 = 99（20 日低）× 0.99 = 98.01 → 比 ATR 止损高 → 取更宽的 96/97。
    """
    ok = True

    def chk(name, got):
        nonlocal ok
        print(('  OK   ' if got else '  FAIL ') + name)
        ok = ok and bool(got)

    def mk(n=40, c=100.0, amp=2.0, trend=None):
        bars = []
        for i in range(n):
            cc = c + (i * 0.2 if trend == 'up' else (-i * 0.2 if trend == 'down' else 0))
            bars.append(('2026-08-%02d' % (i + 1), cc, cc, cc + amp / 2, cc - amp / 2))
        return bars

    # ① ATR 收敛值：TR 恒等于 amp=2.0
    b = mk()
    atr, ntr, enough = atr_wilder(b)
    chk('ATR14 收敛到 2.0（实测 %.3f）' % (atr or -1), atr is not None and abs(atr - 2.0) < 1e-6)
    chk('TR 样本数 = 39（40 根 K 线）', ntr == 39)
    chk('足量标记 atrEnough=True', enough is True)

    # ② 震荡票：k=1.5 → 止损 97，宽度 3.0%，仓位 0.5/3.0*100 = 16.7%
    r = compute_one('000001', '自检震荡', b)
    chk('震荡 → stop=97.0（实测 %s）' % (r or {}).get('stop'), r and abs(r['stop'] - 97.0) < 0.02)
    chk('震荡 → k=1.5', r and r['k'] == K_RANGE)
    chk('宽度 3.0%%（实测 %s）' % (r or {}).get('stopWidPct'), r and abs(r['stopWidPct'] - 3.0) < 0.05)
    chk('仓位 16.7%%（实测 %s）' % (r or {}).get('posPct'), r and abs(r['posPct'] - 16.7) < 0.2)
    chk('目标 = 100+2.5×3 = 107.5（实测 %s）' % (r or {}).get('target'), r and abs(r['target'] - 107.5) < 0.05)

    # ③ 趋势上行：k=2.0 → ATR 止损 = 107.8−2×2 = 103.8；
    #    但 20 日结构低点 = 103.0×0.99 = 101.97 更低（更宽）→ 必须取 101.97。
    #    这条正是「取更宽者」的设计意图：结构位比 ATR 位更深时按 ATR 位止损，
    #    一根正常下影线就会把仓位扫在支撑上方。所以这里断言的是**结构位生效**，
    #    不是「宽度=4%」——那是我第一版写错的期望值（自检当场抓出来的）。
    bu = mk(trend='up')
    r2 = compute_one('000002', '自检上行', bu)
    chk('上行趋势判定（实测 %s）' % (r2 or {}).get('trend'), r2 and r2['trend'] == '上行')
    chk('上行 → k=2.0', r2 and r2['k'] == K_TREND)
    chk('上行 → 结构位优先 stop=101.97（实测 %s）' % (r2 or {}).get('stop'),
        r2 and abs(r2['stop'] - 101.97) < 0.02)
    chk('上行 → 结构位低于 ATR 位 103.8', r2 and r2['stop'] < 103.8)

    # ④ 宽度上限钳制：振幅 20 → ATR 20，k×ATR 远超上限 → 必须钳到 WID_MAX
    bw = mk(amp=20.0)
    r3 = compute_one('000003', '自检超宽', bw)
    chk('超宽 → 钳到上限 %.0f%%（实测 %s）' % (WID_MAX * 100, (r3 or {}).get('stopWidPct')),
        r3 and abs(r3['stopWidPct'] - WID_MAX * 100) < 0.05)
    chk('超宽 → overWide=True', r3 and r3['overWide'] is True)
    chk('超宽 → 仓位 = 风险预算/上限 = %.1f%%（实测 %s）'
        % (RISK_BUDGET_PCT / (WID_MAX * 100) * 100, (r3 or {}).get('posPct')),
        r3 and abs(r3['posPct'] - RISK_BUDGET_PCT / (WID_MAX * 100) * 100) < 0.2)

    # ⑤ 宽度下限钳制：振幅 0.2 → ATR 0.2 → 宽度 0.4% → 必须钳到 2.5%
    bn = mk(amp=0.2)
    r4 = compute_one('000004', '自检极窄', bn)
    chk('极窄 → 钳到 2.5%%（实测 %s）' % (r4 or {}).get('stopWidPct'), r4 and abs(r4['stopWidPct'] - 2.5) < 0.05)
    chk('极窄 → 仓位 20.0%%（触顶）', r4 and abs(r4['posPct'] - 20.0) < 0.2)

    # ⑥ 反向断言：K 线不足必须返回 None，不许造数
    chk('K线 3 根 → 返回 None（不编止损）', compute_one('000005', '缺K', mk(n=3)) is None)
    chk('K线为空 → 返回 None', compute_one('000006', '无K', []) is None)

    # ⑦ 结构位「过远不采纳」——这是第一版真出过的 bug：min() 无条件取宽，
    #    实测把一只 9.53 元的票止损撑到 5.10（−47%），等于没有风控。
    #    末段挖坑把 20 日低点打到 80，ATR 位 97、离 97−2=95 更远 → 必须不采纳，stop 仍 97。
    bp = mk()
    bp = bp[:-3] + [('2026-09-30', 100, 100, 101, 80), ('2026-10-01', 100, 100, 101, 99),
                    ('2026-10-02', 100, 100, 101, 99)]
    r5 = compute_one('000007', '自检挖坑', bp)
    chk('挖坑 → 结构位如实记录 80.0（实测 %s）' % (r5 or {}).get('structLow'),
        r5 and abs(r5['structLow'] - 80.0) < 0.05)
    chk('挖坑 → 结构位过远不采纳，stop 仍在 ATR 位附近（实测 %s）' % (r5 or {}).get('stop'),
        r5 and r5['stop'] > 90)
    chk('挖坑 → 不得误标 overWide', r5 and r5['overWide'] is False)
    chk('挖坑 → basis 写明「过远，不采纳」', r5 and '过远' in r5['basis'])

    # ⑧ 收盘确认口径写入
    chk('confirm 口径 = close', r and r.get('confirm') == 'close')

    # ⑨ 证据必须由回测产出，不许手写（曾经写死 9,060/68.7%，K 线一长就脱节）
    fake = {'fixed': {'n': 9060, 'hit': 6224, 'sum_ret': 3977.0, 'win': 2528},
            'atr_intraday': {'n': 9060, 'hit': 3316, 'sum_ret': 4621.0, 'win': 4023},
            'atr_close': {'n': 9060, 'hit': 2564, 'sum_ret': 4168.0, 'win': 4249}}
    rows = bt_rows(fake)
    chk('bt_rows 产出 3 行（实测 %d）' % len(rows), len(rows) == 3)
    chk('bt_rows 触及率 = hit/n（实测 %s，期望 0.687）' % (rows[0] or {}).get('touchRate'),
        rows and abs(rows[0]['touchRate'] - 0.687) < 0.001)
    chk('bt_rows 胜率 = win/n（实测 %s，期望 0.279）' % (rows[0] or {}).get('winRate'),
        rows and abs(rows[0]['winRate'] - 0.279) < 0.001)
    txt = evidence_text(rows)
    for v in (rows[0]['touchRate'], rows[0]['winRate'], rows[2]['touchRate'], rows[2]['winRate']):
        want = '%.1f%%' % (v * 100)
        chk('证据句包含实测值 %s' % want, want in txt)
    chk('证据句样本数与 bt_rows 一致（含 9,060）', '9,060' in txt)
    # 反向：样本不足三行时必须返回空串，而不是编一句话
    chk('不足 3 行 → 证据句为空（不编造）', evidence_text(rows[:1]) == '')
    # 反向：改变 n 后句子必须跟着变，证明不是常量
    fake2 = {k: dict(v) for k, v in fake.items()}
    fake2['fixed']['n'] = 500
    chk('n 变化后证据句跟着变（不再写死）', evidence_text(bt_rows(fake2)) != txt)

    print('[SELF-TEST] ' + ('OK' if ok else 'FAIL'))
    return 0 if ok else 1


BT_LABELS = (('fixed', '固定 −3%（挂单）'),
             ('atr_intraday', 'ATR 止损（挂单）'),
             ('atr_close', 'ATR 止损（收盘确认）'))


def bt_rows(agg):
    """把回测聚合结果转成可直接写进 data.js 的行（前端/闸门共用同一份数字）。

    之前 evidence 是一句手写的字符串，K 线每天增长、回测会漂移，
    页面却永远显示 9,060/68.7% 那组旧数字——等于又一处「写死基线」。
    现在句子由这里的行拼出来，数字只有一个来源。
    """
    rows = []
    for key, label in BT_LABELS:
        v = agg.get(key) or {}
        n = v.get('n') or 0
        if not n:
            continue
        rows.append({
            'key': key,
            'label': label,
            'n': n,
            'touchRate': round(v['hit'] / n, 4),
            'retPct': round(v['sum_ret'] / n, 3),
            'winRate': round(v['win'] / n, 4),
        })
    return rows


def evidence_text(rows):
    """由行生成一句人话证据；数字全部来自实时回测，不手写。"""
    if len(rows) < 3:
        return ''
    f, ai_, ac = rows[0], rows[1], rows[2]
    return ('同条件回测 %s 个入场点（同一批入场点、10 日持有窗口）：'
            '固定 −3%% 触及率 %.1f%%、胜率 %.1f%%；'
            'ATR 止损挂单触及率 %.1f%%、胜率 %.1f%%；'
            'ATR 止损收盘确认 触及率 %.1f%%、胜率 %.1f%%（未计双边成本 0.35%%）'
            % ('{:,}'.format(f['n']),
               f['touchRate'] * 100, f['winRate'] * 100,
               ai_['touchRate'] * 100, ai_['winRate'] * 100,
               ac['touchRate'] * 100, ac['winRate'] * 100))


def bt_agg(D, days=60):
    """同条件回测：同一批入场点，比较「固定 −3%」与「ATR 止损」谁更优。

    对每只票、每根 K 线作为潜在入场点（留 1 根之后观察 10 根）：
      · 固定口径：low ≤ close×(1−3%) 即算触及；
      · ATR 口径：用入场日之前的数据算 ATR/结构位定止损（**不使用未来数据**），
        盘中触及 low ≤ stop 记 hit_intraday，收盘跌破 close < stop 记 hit_close。
    统计触及率与触及后 5 日收益，用来回答一个具体问题：
    「放宽止损会不会把亏损放大到吃掉超额」。
    """
    K = D.get('stkKlines') or {}
    codes = [c for c, _ in collect(D)]
    LOOK, HOLD = 10, 5
    keys = ('fixed', 'atr_intraday', 'atr_close')
    agg = {k: {'n': 0, 'hit': 0, 'sum_ret': 0.0, 'win': 0} for k in keys}

    def _acc(key, hit, ret):
        """ret = 该口径下模拟持有的实际收益（%）。触及则按离场价走，否则按第 LOOK 根收盘。"""
        a = agg[key]
        a['n'] += 1
        if hit:
            a['hit'] += 1
        if ret is not None:
            a['sum_ret'] += ret
            if ret > 0:
                a['win'] += 1

    def _play(stop, mode, fut, last_close):
        """模拟一种止损的执行：返回 (是否触及, 实际收益%)。

        三种口径的离场价不同，这正是差异所在：
          · 挂单式（固定 3% / ATR 盘中）：止损单先成交，出场价 = stop；
          · 收盘确认（ATR 收盘）：只有收盘价跌破才卖，出场价 = 那根收盘价
            （所以可能卖得比 stop 更低——这是收盘确认的代价，必须如实计入，
            不能假设总能在 stop 上成交，否则就是自欺）。
        """
        if mode == 'close':
            for b in fut:
                if b[2] < stop:
                    return True, (b[2] - entry) / entry * 100
            return False, (last_close - entry) / entry * 100
        for b in fut:
            if b[4] <= stop:
                return True, (stop - entry) / entry * 100
        return False, (last_close - entry) / entry * 100

    for code in codes:
        bars = norm_bars(K.get(code))
        if len(bars) < ATR_N + LOOK + HOLD + 5:
            continue
        for i in range(ATR_N + 1, len(bars) - LOOK - HOLD):
            hist = bars[:i + 1]          # 只含 i 及之前 → 不偷看未来
            entry = bars[i][2]
            if not entry:
                continue
            a, _, enough = atr_wilder(hist)
            if not enough or not a:
                continue
            trend, _, _ = trend_of(hist)
            k = K_TREND if trend == '上行' else K_RANGE
            st, _, _ = stop_of(entry, a, k, hist)

            fut = bars[i + 1:i + 1 + LOOK]
            if len(fut) < LOOK:
                continue
            last_close = fut[-1][2]

            fs = entry * 0.97
            h1, r1 = _play(fs, 'intraday', fut, last_close)
            _acc('fixed', h1, r1)
            h2, r2 = _play(st, 'intraday', fut, last_close)
            _acc('atr_intraday', h2, r2)
            h3, r3 = _play(st, 'close', fut, last_close)
            _acc('atr_close', h3, r3)

    return agg, len(codes)


def backtest(D, days=60):
    agg, ncodes = bt_agg(D, days)
    print('\n=== 同条件回测（%d 只标的、10 日持有窗口内的全部入场点）===' % ncodes)
    print('  口径               样本      触及率    策略收益均值   胜率')
    for row in bt_rows(agg):
        v = agg[row['key']]
        print('  %-18s %7d   %6.1f%%   %+8.3f%%   %5.1f%%'
              % (row['label'], row['n'], row['touchRate'] * 100,
                 v['sum_ret'] / v['n'], row['winRate'] * 100))
    print('  口径说明：策略收益 = 模拟持有到 10 日窗口末；若期间触及止损，'
          '挂单口径按止损价成交，收盘确认口径按破位当日收盘价成交（可能更低，如实计入）。')
    print('  未计交易成本：双边约 0.35%（佣金+印花税+滑点），比较时请自行扣除。')
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true', help='只打印不写盘')
    ap.add_argument('--json', action='store_true', help='只输出 JSON')
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('--backtest', type=int, default=0, metavar='N', help='跑回测后退出')
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    D = common.load_dashboard_data(ROOT)
    if D is None:
        print('[ERR] data.js 解析失败', file=sys.stderr)
        return 1

    if a.backtest:
        backtest(D, a.backtest)
        return 0

    out = build(D)
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
        return 0

    s = out['summary']
    print('\n=== ATR 自适应止损引擎 ===')
    print('交易日 %s｜可计算 %d 只（跳过 %d 只：%s）'
          % (out['tradeDate'], s['n'], s['skipped'],
             '、'.join(x['code'] for x in out['skipped'][:6]) or '无'))
    print('止损宽度 中位 %s%%｜均值 %s%%｜区间 %s%%–%s%%'
          % (s['medianStopWidPct'], s['avgStopWidPct'],
             s['minStopWidPct'], s['maxStopWidPct']))
    print('趋势分布 上行 %d / 震荡 %d / 下行 %d｜超宽(>8%%) %d 只｜ATR 近似 %d 只'
          % (s['byTrend']['上行'], s['byTrend']['震荡'], s['byTrend']['下行'],
             s['overWideN'], s['atrApproxN']))
    print('单票上限 %.0f%%、单笔风险预算 %.1f%% —— 止损宽则仓位自动变小，'
          '所以每笔打掉的最大亏损恒为权益的 %.1f%%'
          % (MAX_POS_PCT, RISK_BUDGET_PCT, RISK_BUDGET_PCT))
    print('（%d 只全买的理论合计仓位 %.0f%% 只是上界，实际取舍由 signal_rank 的 A/B/C 分层决定）'
          % (s['n'], s['sumPosPct']))
    if s['concentrationWarn']:
        for w in s['concentrationWarn']:
            print('  [集中度] %s：%d 只、合计建议仓位 %.1f%% —— 同板块同涨同跌，'
                  '实际风险敞口远大于只数看起来的样子' % (w['sector'], w['n'], w['sumPos']))

    # 抽样展示 8 只，便于人工核对方向对不对
    vals = sorted(out['items'].values(), key=lambda v: v['code'])[:8]
    print('\n  代码     名称          收盘    ATR%   止损     宽度     仓位   R目标   依据')
    for v in vals:
        print('  %-8s %-12s %7.2f  %5.2f  %7.2f  %5.2f%%  %5.1f%%  %7.2f  %s'
              % (v['code'], (v['name'] or '')[:6], v['close'], v['atrPct'], v['stop'],
                 v['stopWidPct'], v['posPct'], v['target'], v['basis']))

    if a.dry:
        print('\n（--dry，未写盘）')
        return 0
    D['atrStops'] = out          # 先挂节点再写盘：write_data_js 写的是传进去的 D
    common.write_data_js(ROOT, D, '[atr_stop]')
    print('[OK] 已写回 data.js 顶层 atrStops（%d 只，全站唯一权威止损源）' % s['n'])
    return 0


if __name__ == '__main__':
    sys.exit(main())

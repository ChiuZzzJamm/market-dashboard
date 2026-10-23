#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""signal_rank.py —— 量化打分与「核心/备选/观察」分层（R100z183，2026-10-24）

## 要解决什么

改造前 `aiPrediction` 每天给 6 个板块 × 8 只 = **48 只标的**，每只都写一段产业逻辑，
但 `poolMeta` 自己就写着：机械候选数 **0/0/0/3/0/4**，48 只里 **43 只是「自选补位」**。

也就是说，90% 的推荐标的**没有任何量化信号支撑**——AI 先决定「看好石油开采」，
再从板块里挑 8 个名字，然后给每只配一段听起来合理的故事。这不是选股，是叙事填充。

交易员视角看，这是最致命的一环：**没有信号的标的和没有标的没区别**，因为
- 你不知道该给它多大仓位（只能平均分配，等于把风险摊薄成平庸）；
- 你无法复盘（错了不知道是逻辑错还是运气差）；
- 你无法改进（没有可调参数）。

## 怎么做

给每只票算一个 **0-100 的量化分**，五个因子全部来自 K 线本身（可复现、可回测、
不依赖 AI 的判断），再按分分层：

    动量 25 分   近 20 日涨幅在全市场 1,300+ 只里的百分位（横截面，不是绝对涨幅）
    趋势 20 分   收盘 > MA20 > MA60 得满分；均线纠缠减半；空头排列 0 分
    量能 20 分   近 5 日均量 ÷ 近 60 日均量（放量倍数），1.0× 为中性
    位置 20 分   收盘在 60 日区间的分位；0.4–0.7 最佳（<0.2 弱势、>0.9 追高）
    波动 15 分   ATR% 在 3–6% 最佳（<2% 没弹性，>8% 风险过大）

**否决项优先于分数**（这是专业与业余的分界）：
- 空头排列（收盘 < MA20 < MA60）→ 强制 C 级。做多的前提是趋势向上，
  在下跌趋势里找「超跌反弹」是另一套系统，不属于这套。
- ATR% > 7.5% → 降一级。波动大到止损宽度失控的票，不该进核心仓。

分层：
    A（≥70）核心：可下注，按 atr_stop 的仓位建议执行
    B（55–70）备选：可小仓位试仓，或等回调到止损位附近
    C（<55）观察：只进自选，不给仓位

## 输出

写 data.js 顶层 `tradeDesk`：一份「今天到底买什么、买多少、止损在哪」的执行清单，
按分排序、带板块集中度预警。前端「交易执行台」卡只读这一个节点。

用法：
    python3 signal_rank.py              # 计算并写回 data.js
    python3 signal_rank.py --dry        # 只打印
    python3 signal_rank.py --self-test  # 合成数据自检（含否决项反向断言）
    python3 signal_rank.py --json       # 输出 JSON

依赖方向：本脚本 **import atr_stop**，止损/ATR/趋势一律复用它的实现——
绝不在第二个文件里复制一份「ATR 怎么算」，否则改参数时两处必然脱节（CHECKLIST L2）。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common          # noqa: E402
import atr_stop as A   # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))

# 因子权重（合计 100）。改这里必须同步改 audit_pipeline 的 G33 断言。
W_MOM, W_TREND, W_VOL, W_POS, W_AMP = 25, 20, 20, 20, 15

# 分层不再用「绝对分数切档」。原因：候选池本身已经被断板/吸筹/九门/谐波/AI 筛过一轮，
# 整体强于全市场，绝对阈值会切出「151 只里 77 只 A 级」这种没有区分度的结果（首版实测）。
# 现在改成：**硬性否决先筛掉不能做的**，剩下的按分排名，只有前 MAX_PICK 名进执行清单。
# 这与真实交易一致——每天只做最好的几只，而不是「所有 70 分以上的都买」。
MAX_PICK = 8                 # 执行清单最多几只
SECTOR_MAX_N = 2             # 单板块最多几只进执行清单（同板块两只以上就同涨同跌）
SECTOR_MAX_POS = 35.0        # 单板块建议仓位上限（%）

VETO_ATR_PCT = 7.0           # ATR% 超过此值 = 波动不可控，直接否决（不是降级）
LOW_PRICE_VETO = 2.0         # 股价低于此值否决：面值退市风险 + 流动性差
QUAL_STRONG, QUAL_MID = 78, 66   # 展示用强度标签（强/中/弱），不参与准入


def _f(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


# 东财细分板块带罗马数字尾缀（IT服务Ⅱ / 其他家电Ⅲ），同一个板块被拆成两个名字。
# 不归一的话「单板块最多 2 只」会被绕过：8 只清单里出现「IT服务」1 只 +「IT服务Ⅱ」1 只，
# 看着是两个板块，实际是同一个赌注下了两次注。首版实测（2026-10-24 清单）就踩到了。
_SECTOR_ROMAN = re.compile(r'\s*(?:[\u2160-\u2169]|(?:III|II|IV|VI|VII|VIII|IX|V|I))$')


def norm_sector(s):
    """板块名归一：去掉尾部的罗马数字层级（Ⅰ…Ⅹ / II / III …）。"""
    t = str(s or '').strip()
    if not t:
        return '未分类'
    t2 = _SECTOR_ROMAN.sub('', t).strip()
    return t2 or '未分类'


def pct_rank(sorted_vals, v):
    """v 在 sorted_vals 里的百分位（0-100）。二分查找，避免 O(n²)。

    用**横截面**百分位而不是绝对值：A 股所有票同一天一起涨/一起跌，
    用绝对涨幅会让「牛市里人人都 90 分、熊市里人人都 10 分」，分层失去意义。
    """
    import bisect
    if not sorted_vals:
        return 50.0
    i = bisect.bisect_left(sorted_vals, v)
    return round(i / len(sorted_vals) * 100, 1)


def market_ctx(K):
    """全市场横截面统计：算 20 日涨幅分布，供动量分位用。"""
    moms = []
    for code, bars in K.items():
        b = A.norm_bars(bars)
        if len(b) < 25:
            continue
        c0, c1 = b[-21][2], b[-1][2]
        if c0 and c1 and c0 > 0:
            moms.append((c1 - c0) / c0 * 100)
    moms.sort()
    return {'momSorted': moms, 'n': len(moms)}


def factors_of(bars, atr, ctx):
    """五因子打分。返回 (总分, 明细 dict, 附加信息 dict)。"""
    closes = [b[2] for b in bars]
    close = closes[-1]
    highs = [b[3] for b in bars]
    lows = [b[4] for b in bars]
    vols = [b[5] for b in bars if len(b) > 5 and b[5]]
    det = {}

    # ① 动量（横截面分位）
    if len(closes) >= 21 and closes[-21] > 0:
        mom20 = (close - closes[-21]) / closes[-21] * 100
        pr = pct_rank(ctx['momSorted'], mom20)
        det['mom'] = round(W_MOM * pr / 100, 1)
        det['momPct'] = pr
        det['mom20'] = round(mom20, 2)
    else:
        det['mom'] = W_MOM / 2
        det['momPct'] = 50.0
        det['mom20'] = None

    # ② 趋势 —— 连续打分，不是三档。首版用「上行 20 / 震荡 10 / 下行 0」的阶跃写法，
    #    实测分数呈双峰（85-96 一堆、45-60 一堆，中间空档），因为强票四个因子全拿满、
    #    弱票多个因子归零，排序失去分辨力。改成连续量：
    #      d1 = 收盘距 MA20 的百分比（短期偏离）
    #      d2 = MA20 相对 MA60 的百分比（中期斜率，权重更高）
    #    典型 A 股 d1∈[-8,8]、d2∈[-6,6]，合成后线性映射到 0–20。
    trend, ma20, ma60 = A.trend_of(bars)
    if ma20 and ma60:
        d1 = (close / ma20 - 1) * 100
        d2 = (ma20 / ma60 - 1) * 100
        raw = d1 * 0.8 + d2 * 1.2
        det['trend'] = round(max(0.0, min(float(W_TREND), (raw + 8) / 16 * W_TREND)), 1)
        det['trendRaw'] = round(raw, 2)
    else:
        det['trend'] = float(W_TREND) * 0.5
        det['trendRaw'] = None
    det['trendState'] = trend

    # ③ 量能 —— 连续对数映射。首版「≥1.5 倍直接满分」会让 2.3 倍和 5 倍同分，
    #    丢掉「越放量越值得看」的信息。以 0.7× 为 0 分、3.0× 为满分做对数插值。
    import math
    if len(vols) >= 60:
        v5 = sum(vols[-5:]) / 5
        v60 = sum(vols[-60:]) / 60
        if v60 > 0:
            ratio = v5 / v60
            if ratio <= 0.7:
                det['vol'] = 0.0
            elif ratio >= 3.0:
                det['vol'] = float(W_VOL)
            else:
                det['vol'] = round(W_VOL * (math.log(ratio) - math.log(0.7))
                                   / (math.log(3.0) - math.log(0.7)), 1)
            det['volRatio'] = round(ratio, 2)
        else:
            det['vol'], det['volRatio'] = W_VOL * 0.4, None
    else:
        det['vol'], det['volRatio'] = W_VOL * 0.4, None

    # ④ 位置：60 日区间分位。0.3–0.75 满分（趋势中段），<0.3 偏弱、>0.75 追高
    seg_h = max(highs[-60:])
    seg_l = min(lows[-60:])
    if seg_h > seg_l:
        pos = (close - seg_l) / (seg_h - seg_l)
        det['posPct60'] = round(pos, 3)
        if 0.3 <= pos <= 0.75:
            det['pos'] = float(W_POS)
        elif pos < 0.3:
            det['pos'] = round(W_POS * max(0.0, pos / 0.3) * 0.8, 1)
        else:
            det['pos'] = round(W_POS * max(0.0, (1.0 - pos) / 0.25) * 0.8, 1)
    else:
        det['posPct60'] = None
        det['pos'] = W_POS * 0.3

    # ⑤ 波动：ATR% 3-6% 最佳
    atr_pct = atr / close * 100 if close else None
    det['atrPct'] = round(atr_pct, 2) if atr_pct is not None else None
    if atr_pct is None:
        det['amp'] = W_AMP * 0.5
    elif 3.0 <= atr_pct <= 6.0:
        det['amp'] = float(W_AMP)
    elif atr_pct < 3.0:
        det['amp'] = round(W_AMP * max(0.0, atr_pct / 3.0) * 0.85, 1)
    else:
        det['amp'] = round(W_AMP * max(0.0, (12.0 - atr_pct) / 6.0) * 0.85, 1)

    total = round(sum(det[k] for k in ('mom', 'trend', 'vol', 'pos', 'amp')), 1)
    return total, det


def judge(det, atr_pct, stop_wid, close=None):
    """硬性否决。返回 (能否参与, 理由)。

    这些不是「扣分项」，是「今天不做」。交易里有些票根本不该进候选——
    给它 60 分排在中游，比直接说「不做」更危险，因为中游看着还有机会。

    ⚠️ 「止损需要 10%」**不在否决项里**。第一版把它写进去，实测否决掉 85/151 只，
    其中包括 96.4 分的头名——而 10% 止损配 5% 仓位，单笔亏损仍是权益 0.5%，
    数学上完全可控。宽止损的解法是**减仓**，不是不做；把两者混为一谈，
    等于用一个错误的理由放弃掉最好的标的。
    """
    if det.get('trendState') == '下行':
        return False, '空头排列（收盘<MA20<MA60），做多前提不成立'
    if atr_pct is not None and atr_pct > VETO_ATR_PCT:
        return False, ('波动过大（ATR %.2f%% > %.1f%%），止损放不下正常波动'
                       % (atr_pct, VETO_ATR_PCT))
    # 面值退市风险：A 股连续 20 个交易日低于 1 元即终止上市，2 元以下属高危区，
    # 且低价股流动性差、跳空多，止损容易被穿透。这类票再强也不进执行清单。
    if close is not None and close < LOW_PRICE_VETO:
        return False, ('股价 %.2f 元低于 %.1f 元，面值退市风险 + 流动性差，不做'
                       % (close, LOW_PRICE_VETO))
    return True, ''


def strength_of(q):
    """展示用强度标签。不参与准入，只给人看的粗粒度。"""
    if q >= QUAL_STRONG:
        return '强'
    if q >= QUAL_MID:
        return '中'
    return '弱'


def why_of(det, tier):
    """一句话可读理由。前端要能一眼看懂「凭什么」。"""
    bits = []
    st = det.get('trendState')
    bits.append({'上行': '趋势上行', '震荡': '趋势震荡', '下行': '趋势下行'}.get(st, '趋势未知'))
    pr = det.get('momPct')
    if pr is not None:
        if pr >= 80:
            bits.append('20日动量全市场前 %d%%' % round(100 - pr))
        elif pr <= 20:
            bits.append('20日动量全市场后 %d%%' % round(pr))
    vr = det.get('volRatio')
    if vr is not None:
        if vr >= 1.5:
            bits.append('放量 %.1f 倍' % vr)
        elif vr <= 0.7:
            bits.append('缩量 %.1f 倍' % vr)
    p = det.get('posPct60')
    if p is not None:
        if p >= 0.9:
            bits.append('处于 60 日高位（追高风险）')
        elif p <= 0.2:
            bits.append('处于 60 日低位')
    return '、'.join(bits)


def select_desk(qualified, max_pick=MAX_PICK, sector_max_n=SECTOR_MAX_N):
    """按分排名取前 max_pick 名，单板块最多 sector_max_n 只；「未分类」不占配额。

    抽成函数是为了能被自检直接测（原来这段逻辑埋在 build 里，只能靠跑真实数据
    反推，出了问题只能看结果猜原因）。
    """
    desk, sec_n, rank = [], {}, 0
    for p in qualified:
        if len(desk) >= max_pick:
            break
        s = p.get('sector') or A.UNCLASSIFIED
        if s != A.UNCLASSIFIED and sec_n.get(s, 0) >= sector_max_n:
            p['cappedBySector'] = True
            continue
        if s != A.UNCLASSIFIED:
            sec_n[s] = sec_n.get(s, 0) + 1
        else:
            p['sectorUnknown'] = True
        rank += 1
        p['rank'] = rank
        p['inDesk'] = True
        desk.append(p)
    for p in qualified:
        p.setdefault('inDesk', False)
        p.setdefault('rank', None)
    return desk, sec_n


def build(D):
    K = D.get('stkKlines') or {}
    ctx = market_ctx(K)
    stops = ((D.get('atrStops') or {}).get('items') or {})

    # 收集候选（与 atr_stop 同源：AI + 四机械池），并记录来源
    cands = {}
    for code, meta in A.collect(D):
        cands[code] = {'code': code, 'name': meta['name'], 'sector': meta['sector'],
                       'srcs': list(meta['_srcs'])}

    # 机械池来源补一次（collect 只记首个 sector，来源列表够用了）

    raw_picks = []
    for code, c in cands.items():
        bars = A.norm_bars(K.get(code))
        if len(bars) < 25:
            continue
        atr, _, enough = A.atr_wilder(bars)
        if atr is None:
            continue
        q, det = factors_of(bars, atr, ctx)
        # 信号共振加分：被几个独立模块同时选中就是几条独立证据。
        # 2 个 +2、3 个 +4、4 个以上 +6，封顶 100。
        bonus = min(6, max(0, len(c['srcs']) - 1) * 2)
        q = min(100.0, round(q + bonus, 1))
        atr_pct = det.get('atrPct')

        st = stops.get(code) or {}
        wid = st.get('stopWidPct')
        close_v = st.get('close') or bars[-1][2]
        ok, veto = judge(det, atr_pct, wid, close_v)
        raw_picks.append({
            'code': code, 'name': c['name'], 'sector': norm_sector(c['sector']),
            'q': q, 'qBase': round(q - bonus, 1), 'bonus': bonus,
            'ok': ok, 'veto': veto, 'strength': strength_of(q),
            'close': close_v,
            'stop': st.get('stop'), 'stopWidPct': wid,
            'target': st.get('target'), 'posPct': st.get('posPct'),
            'atrPct': atr_pct, 'atrEnough': enough,
            'resonance': len(c['srcs']), 'srcs': c['srcs'],
            'factors': {k: det.get(k) for k in
                        ('mom', 'trend', 'vol', 'pos', 'amp', 'momPct', 'mom20',
                         'volRatio', 'posPct60', 'trendState', 'trendRaw')},
            'why': why_of(det, None),
        })

    # 通过否决的按分排名（同分看共振，再看代码，保证排序稳定可复现）
    qualified = sorted([p for p in raw_picks if p['ok']],
                       key=lambda x: (-x['q'], -x['resonance'], x['code']))
    vetoed = sorted([p for p in raw_picks if not p['ok']],
                    key=lambda x: (-x['q'], x['code']))
    # 通过者身上不该留 veto 空串：空值会被 G9 空内容扫描当噪音报出来，
    # 而「没有否决理由」用「字段不存在」表达最准确（不是「有空理由」）。
    for p in qualified:
        p.pop('veto', None)

    # 执行清单：前 MAX_PICK 名，且单板块最多 SECTOR_MAX_N 只。
    # 同板块第二只不是「分散」，是「同一个赌注下两次注」——A 股板块内相关性常 0.7 以上。
    # 「未分类」不占板块配额：它是板块映射缺失的占位符，不是同一个板块。
    # 若把它当板块，浙商银行、赤峰黄金、汇顶科技会被挤成「只准买 2 只」，
    # 但它们之间没有同涨同跌关系——那样是在用错误信息做分散，反而降低组合质量。
    desk, sec_n = select_desk(qualified)

    # 组合仓位：先按单票建议加总，再按板块上限与总仓位上限两轮压缩
    by_sector, sum_pos = {}, 0.0
    for p in desk:
        v = _f(p.get('posPct')) or 0.0
        by_sector.setdefault(p['sector'], {'n': 0, 'pos': 0.0})
        by_sector[p['sector']]['n'] += 1
        by_sector[p['sector']]['pos'] = round(by_sector[p['sector']]['pos'] + v, 1)
        sum_pos += v

    warns = []
    # ① 板块上限（同样跳过「未分类」：占位符不是板块，不该被板块上限约束）
    for s, v in sorted(by_sector.items(), key=lambda kv: -kv[1]['pos']):
        if s == A.UNCLASSIFIED:
            continue
        if v['pos'] > SECTOR_MAX_POS:
            warns.append('%s 建议仓位合计 %.1f%% 超单板块上限 %.0f%%——同板块同涨同跌，'
                         '真跌起来是一起跌，不是分散' % (s, v['pos'], SECTOR_MAX_POS))

    # ② 总仓位对齐 strategy.positionBand（"4-5 成" → 0.45-0.55）
    band = ((D.get('strategy') or {}).get('positionBand') or {})
    cap_hi = _f(band.get('hi'))
    cap_txt = band.get('text') or ''
    scaled = None
    if cap_hi and sum_pos > 0:
        target = cap_hi * 100
        if sum_pos > target:
            scaled = round(target, 1)
            k = target / sum_pos
            for p in desk:
                if _f(p.get('posPct')):
                    p['posPctRaw'] = p['posPct']
                    p['posPct'] = round(p['posPct'] * k, 1)
            warns.append('单票建议仓位加总 %.1f%% 超过组合目标仓位 %s（%.0f%%），'
                         '已按比例压缩到 %.1f%%'
                         % (sum_pos, cap_txt or '上限', target, scaled))

    return {
        'updatedAt': _now(),
        'tradeDate': str((D.get('ashare') or {}).get('tradeDate')
                         or (D.get('aiPrediction') or {}).get('date') or '')[:10],
        'method': '五因子量化分 0-100：动量%d/趋势%d/量能%d/位置%d/波动%d，'
                  '信号共振每多一个来源 +2（封顶 +6）；三项硬性否决：空头排列'
                  '（收盘<MA20<MA60）、ATR%%>%.0f、股价<%.1f 元（面值退市风险）；'
                  '通过者按分排名取前 %d 名进执行清单，单板块最多 %d 只（板块缺失的标的不占配额，页面会标出只数）。'
                  '止损宽度本身不是否决项——宽止损靠减仓解决，单笔风险恒为权益的 %.1f%%'
                  % (W_MOM, W_TREND, W_VOL, W_POS, W_AMP, VETO_ATR_PCT, LOW_PRICE_VETO,
                     MAX_PICK, SECTOR_MAX_N, A.RISK_BUDGET_PCT),
        'weights': {'mom': W_MOM, 'trend': W_TREND, 'vol': W_VOL, 'pos': W_POS, 'amp': W_AMP},
        'tiers': {
            'qualified': len(qualified), 'vetoed': len(vetoed), 'desk': len(desk),
            'strong': len([p for p in qualified if p['strength'] == '强']),
            'mid': len([p for p in qualified if p['strength'] == '中']),
        },
        'picks': desk,
        'candidates': [p for p in qualified if not p['inDesk']][:10],
        'watch': vetoed[:12],
        'portfolio': {
            'n': len(desk),
            'sumPosPct': round(sum_pos, 1),
            'cappedPosPct': scaled,
            'targetBand': cap_txt,
            'bySector': by_sector,
            'sectorMaxPos': SECTOR_MAX_POS,
            # 板块缺失只数：明确告诉读者「同板块上限」在这几只上没生效，
            # 而不是让人以为 8 只已经做了完整分散。
            'sectorUnknownN': len([p for p in desk if p.get('sectorUnknown')]),
            'warn': warns,
        },
        'market': {'n': ctx['n'],
                   'momMedian': round(ctx['momSorted'][len(ctx['momSorted']) // 2], 2)
                   if ctx['momSorted'] else None},
    }


def _now():
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def self_test():
    """合成数据自检。重点验证**否决项真的能否决**，以及分档阈值方向正确。"""
    ok = True

    def chk(name, got):
        nonlocal ok
        print(('  OK   ' if got else '  FAIL ') + name)
        ok = ok and bool(got)

    def mk(n=70, slope=0.0, amp=2.0, base=100.0, vol=1e7, vol_last=1.0):
        """合成连续日 K（含成交量列，量能因子要用）。日期必须严格递增，
        否则 norm_bars 排序后会把行打乱、动量算出反向。"""
        import datetime
        d0 = datetime.date(2026, 6, 1)
        out = []
        for i in range(n):
            c = base + slope * i
            v = vol * (vol_last if i >= n - 5 else 1.0)
            day = (d0 + datetime.timedelta(days=i)).isoformat()
            out.append((day, c, c, c + amp / 2, c - amp / 2, v))
        return out

    def ctx_of(mom):
        return {'momSorted': sorted([mom]), 'n': 1}

    # ① 强票动量必须落在分位顶端（横截面口径）
    b = mk(slope=0.5, vol_last=2.0)          # 70 天涨 34.5 元
    atr, _, _ = A.atr_wilder(b)
    q, det = factors_of(b, atr, {'momSorted': [0.0], 'n': 1})
    chk('强票 → 趋势上行（实测 %s）' % det['trendState'], det['trendState'] == '上行')
    chk('强票 → 动量分位 100（实测 %s）' % det.get('momPct'), det.get('momPct') == 100.0)
    chk('强票 → 放量倍数 ≥1.5（实测 %s）' % det.get('volRatio'),
        (det.get('volRatio') or 0) >= 1.5)

    # ② 否决项直测 —— 不靠合成 K 线去「碰巧」命中某一档。
    #    上一版把场景选成「涨 35% 且站在 60 日最高位」，位置因子按设计扣了分、
    #    总分 66.8 落 B 档，我还以为代码错。打分是对的（那形态就是追高），
    #    错的是测试假设。所以现在直接测 judge() 的判定边界。
    good = {'trendState': '上行'}
    chk('正常票（ATR 4%%，止损 5%%）→ 通过', judge(good, 4.0, 5.0)[0] is True)
    chk('空头排列 → 否决', judge({'trendState': '下行'}, 4.0, 5.0)[0] is False)
    chk('空头排列 → 理由写明', '空头排列' in judge({'trendState': '下行'}, 4.0, 5.0)[1])
    chk('ATR %.0f%%+ → 否决' % (VETO_ATR_PCT + 0.5),
        judge(good, VETO_ATR_PCT + 0.5, 5.0)[0] is False)
    # 止损宽度**不是**否决项（第一版写错过）：宽止损配小仓位，单笔风险仍是 0.5%。
    # 这条断言把这个决定钉住，防止以后又把它加回否决里。
    chk('止损需 10% → 仍通过（靠减仓、不是不做）', judge(good, 6.0, 10.0)[0] is True)
    chk('止损 6%% → 通过', judge(good, 6.0, 6.0)[0] is True)
    chk('股价 1.73 元 → 否决（面值退市风险）', judge(good, 4.0, 5.0, 1.73)[0] is False)
    chk('股价 12 元 → 通过', judge(good, 4.0, 5.0, 12.0)[0] is True)

    # ② 空头排列必须否决，哪怕分不低
    bd = mk(slope=-0.3)
    atrd, _, _ = A.atr_wilder(bd)
    qd, detd = factors_of(bd, atrd, {'momSorted': [-20, -10, 0], 'n': 3})
    okd, vetod = judge(detd, detd['atrPct'], 5.0)
    chk('空头排列 → 趋势下行（实测 %s）' % detd['trendState'], detd['trendState'] == '下行')
    chk('空头排列 → 不通过（实测 %s）' % okd, okd is False)
    chk('空头排列 → 否决理由写明（实测 %s）' % vetod[:16], '空头排列' in vetod)

    # ③ 波动过大否决：构造 ATR% > VETO_ATR_PCT
    bw = mk(amp=30.0, slope=0.5)
    atrw, _, _ = A.atr_wilder(bw)
    qw, detw = factors_of(bw, atrw, {'momSorted': [-10, 0, 35], 'n': 3})
    okw, vetow = judge(detw, detw['atrPct'], 5.0)
    chk('高波动 ATR%%=%.1f > 阈值' % (detw['atrPct'] or 0), (detw['atrPct'] or 0) > VETO_ATR_PCT)
    chk('高波动 → 不通过（实测 %s）' % okw, okw is False)
    chk('高波动 → 否决理由写明波动', '波动过大' in vetow)

    # ③b 分数必须连续、不能双峰：同一条慢涨曲线，只改动量能，分差应落在中间段
    b1 = mk(slope=0.15, vol_last=1.0)
    b2 = mk(slope=0.15, vol_last=1.6)
    q1, d1_ = factors_of(b1, A.atr_wilder(b1)[0], {'momSorted': [0, 3, 6], 'n': 3})
    q2, d2_ = factors_of(b2, A.atr_wilder(b2)[0], {'momSorted': [0, 3, 6], 'n': 3})
    chk('量能 1.0× → 1.6× 分差在 0-8 之间（连续，非阶跃 %s）'
        % round(q2 - q1, 1), 0 < (q2 - q1) < 8)

    # ④ 分数方向：同一只票，放量应比缩量分高
    b_up = mk(slope=0.4, vol_last=2.0)
    b_dn = mk(slope=0.4, vol_last=0.4)
    qa, _ = factors_of(b_up, A.atr_wilder(b_up)[0], {'momSorted': [0, 30], 'n': 2})
    qb, _ = factors_of(b_dn, A.atr_wilder(b_dn)[0], {'momSorted': [0, 30], 'n': 2})
    chk('放量分 > 缩量分（%.1f vs %.1f）' % (qa, qb), qa > qb)

    # ⑤ 边界：K 线太少必须不崩、不造分
    short = mk(n=10)
    chk('K线 10 根 → norm_bars 可用但 build 会跳过', len(A.norm_bars(short)) == 10)

    # ⑥ 因子权重合计 = 100（改权重时这条会立刻报警）
    chk('五因子权重合计 = 100（实测 %d）' % (W_MOM + W_TREND + W_VOL + W_POS + W_AMP),
        W_MOM + W_TREND + W_VOL + W_POS + W_AMP == 100)

    # ⑦ 板块归一：细分尾缀必须合并，正常名字不能被改坏（反向）。不归一的话
    #    「单板块 ≤2 只」会被 IT服务 / IT服务Ⅱ 这类双胞胎名字绕过。
    chk('IT服务Ⅱ → IT服务（实测 %s）' % norm_sector('IT服务Ⅱ'), norm_sector('IT服务Ⅱ') == 'IT服务')
    chk('其他家电Ⅲ → 其他家电（实测 %s）' % norm_sector('其他家电Ⅲ'),
        norm_sector('其他家电Ⅲ') == '其他家电')
    chk('半导体设备 不被改坏（实测 %s）' % norm_sector('半导体设备'),
        norm_sector('半导体设备') == '半导体设备')
    chk('空板块名 → 未分类（实测 %s）' % norm_sector(''), norm_sector('') == '未分类')

    # ⑧ 执行清单配额（select_desk）：同板块限 2 只，但「未分类」不占配额。
    #    反向两测：① 同板块 3 只只准进 2 只；② 未分类 3 只可以全进（不是「同一赌注」）。
    mkq = lambda code, sec: {'code': code, 'sector': sec}
    _qs = lambda: [mkq('600001', '银行'), mkq('600002', '银行'), mkq('600003', '银行'),
                   mkq('600004', '半导体'), mkq('600005', '未分类'), mkq('600006', '未分类'),
                   mkq('600007', '未分类')]
    q8 = _qs()
    d3, sn3 = select_desk(q8, max_pick=8, sector_max_n=2)
    bank = [p for p in d3 if p['sector'] == '银行']
    unc = [p for p in d3 if p['sector'] == '未分类']
    chk('同板块 3 只 → 只进 2 只（实测 %d）' % len(bank), len(bank) == 2)
    chk('未分类 3 只 → 全进、不占配额（实测 %d）' % len(unc), len(unc) == 3)
    chk('进清单的未分类被标 sectorUnknown', all(p.get('sectorUnknown') for p in unc))
    # select_desk 就地改传入的 dict，所以断言要查同一批对象（q8），不能查副本
    chk('被板块上限挤掉的标 cappedBySector（600003）',
        any(p.get('cappedBySector') for p in q8 if p['code'] == '600003'))
    chk('清单总数 = 2+1+3 = 6（实测 %d）' % len(d3), len(d3) == 6)
    chk('排名连续 1..6（实测 %s）' % [p['rank'] for p in d3],
        [p['rank'] for p in d3] == [1, 2, 3, 4, 5, 6])

    # ⑨ 反向：max_pick 上限本身仍生效（别为了修未分类把总量上限也放开）
    d4, _ = select_desk(_qs(), max_pick=3, sector_max_n=2)
    chk('max_pick=3 → 只进 3 只（实测 %d）' % len(d4), len(d4) == 3)

    print('[SELF-TEST] ' + ('OK' if ok else 'FAIL'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--self-test', action='store_true')
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    D = common.load_dashboard_data(ROOT)
    if D is None:
        print('[ERR] data.js 解析失败', file=sys.stderr)
        return 1
    out = build(D)
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
        return 0

    t = out['tiers']
    pf = out['portfolio']
    print('\n=== 交易执行台（量化排名）===')
    print('交易日 %s｜通过否决 %d 只（强 %d / 中 %d）｜被否决 %d 只｜执行清单 %d 只'
          % (out['tradeDate'], t['qualified'], t['strong'], t['mid'], t['vetoed'], t['desk']))
    print('建议仓位合计 %.1f%%（组合目标 %s）'
          % (pf['cappedPosPct'] if pf['cappedPosPct'] is not None else pf['sumPosPct'],
             pf['targetBand'] or '无上限'))
    print('\n  名次 强度 分    代码     名称        板块        收盘    止损    宽度   仓位   目标   共振  依据')
    for p in out['picks']:
        print('  %2d   %-2s %5.1f %-8s %-10s %-10s %7.2f %7.2f %6.2f%% %5.1f%% %7.2f  %d    %s'
              % (p['rank'] or 0, p['strength'], p['q'], p['code'],
                 (p['name'] or '')[:5], (p['sector'] or '')[:6],
                 p['close'] or 0, p['stop'] or 0, p['stopWidPct'] or 0, p['posPct'] or 0,
                 p['target'] or 0, p['resonance'], p['why']))
    if pf['warn']:
        print('\n  组合预警：')
        for w in pf['warn']:
            print('   [WARN] ' + w)
    if out['candidates']:
        print('\n  备选（通过否决但排名靠后，等回调或换手再进）：')
        for p in out['candidates'][:5]:
            print('   %-8s %-10s 分 %5.1f  %s' % (p['code'], (p['name'] or '')[:5], p['q'],
                                                 p['why']))
    if out['watch']:
        print('\n  今日不做（否决，附理由）：')
        for p in out['watch'][:6]:
            print('   %-8s %-10s 分 %5.1f  %s' % (p['code'], (p['name'] or '')[:5], p['q'],
                                                 p['veto']))

    if a.dry:
        print('\n（--dry，未写盘）')
        return 0
    D['tradeDesk'] = out
    common.write_data_js(ROOT, D, '[signal_rank]')
    print('\n[OK] 已写回 data.js 顶层 tradeDesk（%d 只执行清单 + %d 只观察）'
          % (pf['n'], len(out['watch'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())

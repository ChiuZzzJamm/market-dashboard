#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主力隐晦拿货六特征打分（2026-09-26 新增 · 方向③）。

方法论与数据契约：fin-strategy-engine skill `references/accumulation-patterns.md`。
六特征 F1-F6 均基于日级 K 线 + 成交量确定性计算；凡涉「大单/小单/竞价」一律降级为
量能代理，story 由自动化 LLM 撰写，脚本只产出特征命中与总分。

- 扫描域：与 harmonic_detect.py 一致（collect_pool_codes + 断板池），主板 60/00。
- 数据：优先 data.js 内嵌 stkKlines（离线），其次 kline_cache 网络兜底。
- 分池：强（≥70）/ 观察（50-69）；仅保留 ≥50 的标的，上限 20 只，按分降序。
- 输出：D.accumulation（契约见 references §3）。失败保留旧值（R91n）。
- 用法：python3 accumulation_score.py [--dry-run] [--self-test]
  --self-test：合成强吸筹序列离线自检，验证 grade=强，不读写 data.js。
"""
import sys, json, os, time, datetime

import kline_cache as K

BASE = os.path.dirname(os.path.abspath(__file__))
MIN_SCORE = 50
HARD_LIQ = 2e8  # 近 20 日日均成交额 ≥ 2 亿（流动性硬门禁）


def _ma(vals, n):
    if len(vals) < n or n <= 0:
        return None
    s = vals[-n:]
    return sum(s) / len(s)


def _vol_ma5(bars, i):
    """bars[:i+1] 的 5 日均量（不含 i 当日）。"""
    seg = [b['volume'] for b in bars[max(0, i - 5):i]]
    return sum(seg) / len(seg) if seg else 0.0


def _is_limit_up(bar, prev_close):
    return (bar['close'] / prev_close - 1) >= 0.095


def _is_up(bar):
    return bar['close'] >= bar['open']


def _upper_shadow(bar):
    return bar['high'] - max(bar['open'], bar['close'])


def _body(bar):
    return abs(bar['close'] - bar['open'])


def score_one(bars, name=''):
    """返回 dict: {score, features:{f1..f6}, hitCount, grade, note}。"""
    n = len(bars)
    feats = {'f1': 0, 'f2': 0, 'f3': 0, 'f4': 0, 'f5': 0, 'f6': 0}
    notes = []

    if n < 60:
        return {'score': 0, 'features': feats, 'hitCount': 0, 'grade': '观察',
                'note': '数据不足60根'}

    closes = [b['close'] for b in bars]
    vols = [b['volume'] for b in bars]
    turns = [(bars[i]['close'] / bars[i - 1]['close'] - 1) for i in range(1, n)]

    # ---- F6 前置：流动性 + 热度 ----
    avg_amt20 = _ma([bars[i]['volume'] * bars[i]['close'] for i in range(n - 20, n)], 20) or 0
    liq_ok = avg_amt20 >= HARD_LIQ
    hi250 = max(closes[-250:]) if n >= 250 else max(closes)
    lo250 = min(closes[-250:]) if n >= 250 else min(closes)
    drawdown = (hi250 - closes[-1]) / hi250 if hi250 else 0
    has_zt = any(_is_limit_up(bars[i], bars[i - 1]['close'])
                 for i in range(max(1, n - 250), n))
    heat = (1 if has_zt else 0) + (1 if 0.25 <= drawdown <= 0.70 else 0)
    if not liq_ok:
        # 流动性一票否决：不进入打分
        return {'score': 0, 'features': feats, 'hitCount': 0, 'grade': '观察',
                'note': '流动性不足（日均成交额<2亿），不进入吸筹打分'}
    feats['f6'] = 10 if heat == 2 else (6 if heat == 1 else 0)
    notes.append(f"F6=流动性达标;热度{'双满足' if heat==2 else ('单满足' if heat==1 else '无')}(涨停史={has_zt},回撤={drawdown:.0%})")

    # ---- F1 试盘冲高自然回落 ----
    f1_hit = False
    for i in range(max(1, n - 40), n - 4):
        vma = _vol_ma5(bars, i)
        if vma <= 0:
            continue
        us = _upper_shadow(bars[i])
        bd = _body(bars[i])
        if us >= 2 * bd and bars[i]['volume'] >= 1.8 * vma:
            # 次日起 3 日内缩量回落
            nxt = bars[i + 1:i + 4]
            if nxt and all(b['volume'] < vma for b in nxt) and nxt[-1]['close'] < bars[i]['close']:
                lo250_now = min(closes[-250:]) if n >= 250 else min(closes)
                hi250_now = max(closes[-250:]) if n >= 250 else max(closes)
                in_low_zone = bars[-1]['close'] <= lo250_now + 0.4 * (hi250_now - lo250_now)
                if in_low_zone:
                    f1_hit = True
                    break
    if f1_hit:
        feats['f1'] = 20
        notes.append("F1=试盘冲高缩量回落(低位区)")

    # ---- F2 缩量下杀后止跌拉回 ----
    f2_hit = False
    for i in range(max(1, n - 120), n - 8):
        # 找 ≥3 日连跌且量递减
        if i + 3 >= n:
            break
        seg = bars[i:i + 3]
        if all(seg[j]['close'] < seg[j - 1]['close'] for j in range(1, 3)):
            vma = _vol_ma5(bars, i)
            if seg[-1]['volume'] <= 0.7 * vma and seg[0]['volume'] > seg[-1]['volume']:
                drop = seg[0]['close'] - seg[-1]['close']
                rec = bars[i + 3:i + 8]
                if rec and (rec[-1]['close'] - seg[-1]['close']) >= 0.5 * drop:
                    f2_hit = True
                    break
    if f2_hit:
        feats['f2'] = 15
        notes.append("F2=缩量下杀后止跌拉回≥50%")

    # ---- F3 二次试盘 + 横盘收窄 ----
    f3_hit = False
    if f1_hit:
        # 计数 F1 式试盘出现 ≥2 次
        cnt = 0
        for i in range(max(1, n - 120), n - 4):
            vma = _vol_ma5(bars, i)
            if vma <= 0:
                continue
            if _upper_shadow(bars[i]) >= 2 * _body(bars[i]) and bars[i]['volume'] >= 1.8 * vma:
                cnt += 1
        amp10 = (max(b['high'] for b in bars[-10:]) - min(b['low'] for b in bars[-10:])) / bars[-1]['close']
        amp20 = (max(b['high'] for b in bars[-20:]) - min(b['low'] for b in bars[-20:])) / bars[-1]['close']
        flat = amp10 < 0.6 * amp20
        center = sum(b['close'] for b in bars[-10:]) / 10
        within = all(abs(b['close'] - center) / center <= 0.06 for b in bars[-10:])
        if cnt >= 2 and flat and within:
            f3_hit = True
    if f3_hit:
        feats['f3'] = 20
        notes.append("F3=二次试盘+横盘收窄±6%")

    # ---- F4 再度缩量下杀不破前低 ----
    f4_hit = False
    for i in range(max(2, n - 120), n - 6):
        # 前高量
        pre_hi_v = max(vols[max(0, i - 20):i])
        seg = bars[i:i + 6]
        pull_v = max(b['volume'] for b in seg)
        pull_lo = min(b['low'] for b in seg)
        pre_lo = min(b['low'] for b in bars[max(0, i - 20):i])
        stable = all(abs(b['close'] - seg[0]['close']) / seg[0]['close'] <= 0.03 for b in seg)
        if pull_v < 0.6 * pre_hi_v and pull_lo > pre_lo and stable:
            f4_hit = True
            break
    if f4_hit:
        feats['f4'] = 15
        notes.append("F4=缩量下杀未破前低+横盘±3%")

    # ---- F5 底部缩量板 ----
    f5_hit = False
    for i in range(max(1, n - 60), n):
        prev_c = bars[i - 1]['close']
        gap = bars[i]['open'] / prev_c - 1
        if _is_limit_up(bars[i], prev_c) and gap >= 0.05 \
                and bars[i]['volume'] <= 0.8 * bars[i - 1]['volume'] \
                and bars[i]['close'] == bars[i]['high']:
            f5_hit = True
            break
    if f5_hit:
        feats['f5'] = 20
        notes.append("F5=底部缩量开盘板(次日盯竞价)")

    score = sum(feats.values())
    hit = sum(1 for v in feats.values() if v > 0)
    grade = '强' if score >= 70 else ('观察' if score >= MIN_SCORE else '不入池')
    return {'score': score, 'features': feats, 'hitCount': hit, 'grade': grade,
            'note': ';'.join(notes) if notes else '无特征命中'}


# ---------------- 自检 ----------------
def _ap(bars, o, c, h, l, v):
    bars.append({'day': '', 'open': o, 'close': c, 'high': h, 'low': l, 'volume': v})


def _mk_acc_bars():
    """合成一只强吸筹序列（价格全程处于低位区，满足 F1 低位约束）：
    F1/F3 双试盘 → F2 缩量下杀后收复 → F4 缩量下杀未破前低 → F5 缩量开盘板。"""
    Vb = 300_000_000  # 基础成交量（放大后日均额≥2亿）
    bars = []
    # 阶段1：高位 100 回落至 ~55，构造 250 日低位区（hi250≈100, lo250≈55）
    price = 100.0
    for _ in range(150):
        price *= 0.997
        _ap(bars, price, price, price * 1.01, price * 0.99, Vb)
    base = 58.0
    # 阶段2a：试盘A（长上影 + 2.2×量）→ 3 日缩量回落（F1 下杀段）
    _ap(bars, base, base * 0.99, base * 1.12, base * 0.98, int(2.2 * Vb))
    d = base * 0.99
    for _ in range(3):
        d *= 0.99
        _ap(bars, d, d, d * 1.005, d * 0.995, int(0.6 * Vb))
    # 阶段2b：收复 ≥50%（F2 恢复段）
    for _ in range(3):
        d *= 1.015
        _ap(bars, d, d, d * 1.01, d * 0.99, int(0.7 * Vb))
    # 阶段2c：试盘B（第二试盘，供 F3 计数）→ 回落
    _ap(bars, d, d * 0.99, d * 1.12, d * 0.98, int(2.2 * Vb))
    d2 = d * 0.99
    for _ in range(3):
        d2 *= 0.99
        _ap(bars, d2, d2, d2 * 1.005, d2 * 0.995, int(0.6 * Vb))
    for _ in range(2):
        d2 *= 1.01
        _ap(bars, d2, d2, d2 * 1.01, d2 * 0.99, int(0.7 * Vb))
    # 阶段3：F4 缩量下杀未破前低 + 横盘 ±3%
    fl = d2 * 0.97
    for _ in range(8):
        _ap(bars, fl, fl, fl * 1.004, fl * 0.986, int(0.4 * Vb))
    # 阶段4：F5 底部缩量开盘板（涨停 + 开盘≥5% + 量≤0.8×前日）
    prev = fl
    op = prev * 1.05
    cl = prev * 1.098  # 涨停
    _ap(bars, op, cl, cl, prev * 0.94, int(0.5 * 0.4 * Vb))
    # 阶段5：收尾 10 日横盘（保证 F3 横盘收窄 + 满足 F1 低位区）
    for _ in range(10):
        _ap(bars, cl, cl, cl * 1.004, cl * 0.986, int(0.5 * Vb))
    return bars


def self_test():
    bars = _mk_acc_bars()
    r = score_one(bars, 'SELFTEST')
    print(f"[SELF-TEST] accumulation score={r['score']} grade={r['grade']} hit={r['hitCount']}")
    print(f"[SELF-TEST] features={r['features']}")
    ok = r['score'] >= 70 and r['grade'] == '强' and r['hitCount'] >= 5
    print(f"[SELF-TEST] {'OK' if ok else 'FAIL'} (期望 强吸筹 / 命中≥5特征)")
    return ok


# ---------------- 主流程 ----------------
def main():
    args = sys.argv[1:]
    if '--self-test' in args:
        sys.exit(0 if self_test() else 1)

    import common
    dry = '--dry-run' in args

    D = common.load_dashboard_data(BASE)
    codes = K.collect_pool_codes(D)
    # 扩展：断板池 code 一并纳入扫描
    for pool in ('confirmed', 'watching'):
        for e in (D.get('duanban') or {}).get(pool) or []:
            c = str(e.get('code') or '')
            if c.startswith(('60', '00')) and c not in codes:
                codes.append(c)
    codes = sorted(set(codes))
    print(f"[ACC] 扫描域 {len(codes)} 只（优先内嵌 stkKlines）")

    scored = []
    for c in codes:
        bars = K.get_bars(c, D)
        if not bars or len(bars) < 60:
            continue
        nm = (D.get('stkKlineNames') or {}).get(c) or ''
        r = score_one(bars, nm)
        if r['score'] < MIN_SCORE:
            continue
        scored.append({'code': c, 'name': nm, 'score': r['score'],
                       'grade': r['grade'], 'features': r['features'],
                       'hitCount': r['hitCount'], 'note': r['note'],
                       'story': '', 'deduce': None})
    scored.sort(key=lambda x: -x['score'])
    scored = scored[:20]
    today = datetime.date.today().strftime('%Y-%m-%d')

    new_field = {
        'updatedAt': f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}（吸筹打分 {today} 收盘 数据已自动更新）",
        'tradeDate': today,
        'scored': scored,
        'summary': '',
    }

    old = D.get('accumulation')
    if not scored and old:
        print('[ACC] 本轮无 ≥50 分标的，保留既有池（R91n 不清场）')
        old['note'] = (old.get('note') or '') + f"｜{today} 本轮无新命中"
        new_field = old
    elif not scored and not old:
        print('[ACC] 无命中且无旧值，写空结构')
    D['accumulation'] = new_field

    if dry:
        print(f"[ACC][DRY] 命中 {len(scored)} 只（强/观察），未写回")
        print(json.dumps([(i['code'], i['name'], i['score'], i['grade'])
                          for i in scored[:10]], ensure_ascii=False))
        return

    out = 'window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False,
                                                  separators=(',', ':')) + ';\n'
    with open(os.path.join(BASE, 'data.js'), 'w', encoding='utf-8') as f:
        f.write(out)
    print(f"[ACC] 写回 data.js：命中 {len(scored)} 只（强 {sum(1 for s in scored if s['grade']=='强')} / 观察 {sum(1 for s in scored if s['grade']=='观察')}）")


if __name__ == '__main__':
    main()

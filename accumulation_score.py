#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主力隐晦拿货六特征打分（2026-09-26 新增 · 方向③）。

方法论与数据契约：fin-strategy-engine skill `references/accumulation-patterns.md`。
六特征 F1-F6 均基于日级 K 线 + 成交量确定性计算；凡涉「大单/小单/竞价」一律降级为
量能代理，story 由自动化 LLM 撰写，脚本只产出特征命中与总分。

- 扫描域：与 harmonic_detect.py 一致（collect_pool_codes + 断板池），主板 60/00。
- 数据：优先 data.js 内嵌 stkKlines（离线），其次 kline_cache 网络兜底。
- 分池：强（≥70）/ 观察（50-69）；仅保留 ≥50 的标的，上限 20 只。
- 排序（R100z21）：双键 -score → -hitCount → code，先比权重深度再比特征广度。
- 输出：scored 全量（强在前）+ strong / watch 两个子池，供前端分组渲染成「强池 / 观察池」。
- 输出：D.accumulation（契约见 references §3）。失败保留旧值（R91n）。
- 用法：python3 accumulation_score.py [--dry-run] [--self-test]
  --self-test：合成强吸筹序列离线自检，验证 grade=强，不读写 data.js。
"""

import sys, json, os, datetime

import kline_cache as K

BASE = os.path.dirname(os.path.abspath(__file__))
MIN_SCORE = 50
HARD_LIQ = 2e8  # 近 20 日日均成交额 ≥ 2 亿（流动性硬门禁）
# R100z22：信号新鲜度硬纪律（专业口径——历史 K 线模型的分数会随走势走旧，
# 同样的 76 分，3 日前有涨停和 40 日前有涨停完全不是一回事）。
STRONG_MAX_LAG = 10   # 强池必须有 ≥10 个交易日内的试盘/涨停信号，否则再高分也只算「观察」
POOL_MAX_LAG = 25     # 信号滞后 ≥25 个交易日＝结构早已走旧，直接不入池


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


def _upper_shadow(bar):
    return bar['high'] - max(bar['open'], bar['close'])


def _body(bar):
    return abs(bar['close'] - bar['open'])


def score_one(bars, name=''):
    """返回 dict: {score, features:{f1..f6}, hitCount, grade, note}。"""
    n = len(bars)
    feats = {'f1': 0, 'f2': 0, 'f3': 0, 'f4': 0, 'f5': 0, 'f6': 0}
    notes = []
    sig_idx = -1  # R100z22：最近一次信号（试盘/涨停板）所在 K 线序号，用于算「信号滞后几日」

    if n < 60:
        return {'score': 0, 'features': feats, 'hitCount': 0, 'grade': '观察',
                'sigIdx': -1, 'note': '数据不足60根'}

    closes = [b['close'] for b in bars]
    vols = [b['volume'] for b in bars]

    # ---- F6 前置：流动性 + 热度 ----
    avg_amt20 = _ma([bars[i]['volume'] * bars[i]['close'] for i in range(n - 20, n)], 20) or 0
    liq_ok = avg_amt20 >= HARD_LIQ
    hi250 = max(closes[-250:]) if n >= 250 else max(closes)
    drawdown = (hi250 - closes[-1]) / hi250 if hi250 else 0
    has_zt = any(_is_limit_up(bars[i], bars[i - 1]['close'])
                 for i in range(max(1, n - 250), n))
    heat = (1 if has_zt else 0) + (1 if 0.25 <= drawdown <= 0.70 else 0)
    if not liq_ok:
        # 流动性一票否决：不进入打分（R100z25：lagDays 必须回，否则前端读 undefined 会渲染出脏标签）
        return {'score': 0, 'features': feats, 'hitCount': 0, 'grade': '观察',
                'sigIdx': -1, 'lagDays': None, 'note': '流动性不足（日均成交额<2亿），不进入吸筹打分'}
    feats['f6'] = 10 if heat == 2 else (6 if heat == 1 else 0)
    # R100z22：note 文案改为投资人人话（原「F6=流动性达标;热度双满足(涨停史=True,回撤=28%)」机器串，
    # 且每张卡重复同一段括号后缀、读不出在说什么）。按实际命中项拼装中文短句，去掉 F 编号与布尔字面量。
    amt_yi = avg_amt20 / 1e8
    dd_pct = round(drawdown * 100)
    if heat == 2:
        head = '日均 %.1f 亿·热度双达标（近 250 日有涨停、回撤 %d%%）' % (amt_yi, dd_pct)
    elif heat == 1:
        head = '日均 %.1f 亿·热度单达标（近 250 日%s、回撤 %d%%）' % (
            amt_yi, '有过涨停' if has_zt else '回撤够大但无涨停', dd_pct)
    else:
        head = '日均 %.1f 亿·无涨停且回撤不足 25%%（热度不计分）' % amt_yi
    notes.append(head)

    # ---- F1 试盘冲高自然回落 ----
    # R100z21：低位区判定改用「试盘当日」所处窗口位置（前 120 日区间下 45% 分位），
    # 原写法拿 bars[-1]['close']（当前收盘）比 250 日区间 → 一旦股价已反弹出低位区，
    # F1 永久不命中（实测池内 100% 为 0），连带 F3（原硬依赖 F1）也永久 0 分。
    f1_hit = False
    probe_idx = []
    for i in range(max(1, n - 40), n - 4):
        vma = _vol_ma5(bars, i)
        if vma <= 0:
            continue
        us = _upper_shadow(bars[i])
        bd = _body(bars[i])
        # R100z21：阈值 1.8→1.5、3 日逐日量改为均值口径。实测 285 只扫描域内
        # 「上影≥2×实体 + 单日量≥1.8×5日均量」几乎全灭（F1 命中率 0%），
        # 属模型阈值脱离当下缩量节奏，放松到 1.5× + 3 日均量回落仍属「放完就歇」的试盘语义。
        if us >= 2 * bd and bars[i]['volume'] >= 1.5 * vma:
            probe_idx.append(i)  # 试盘日留档，供 F3 独立计数
            # 次日起 3 日内缩量回落（3 日平均量 < 0.85×5 日均量，且收盘回到试盘价下方）
            nxt = bars[i + 1:i + 4]
            if nxt and sum(b['volume'] for b in nxt) / len(nxt) < 0.85 * vma \
                    and nxt[-1]['close'] < bars[i]['close']:
                w_lo = min(b['low'] for b in bars[max(0, i - 120):i + 1])
                w_hi = max(b['high'] for b in bars[max(0, i - 120):i + 1])
                if w_hi > w_lo and bars[i]['close'] <= w_lo + 0.45 * (w_hi - w_lo):
                    f1_hit = True
                    sig_idx = max(sig_idx, i)  # R100z22：信号新鲜度用（最近一次试盘）
                    break
    if f1_hit:
        feats['f1'] = 20
        notes.append('近 2 个月出现过放量长上影试盘线，冲高后 3 日缩量回落、'
                     '收盘仍留在上冲价下方')

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
                    sig_idx = max(sig_idx, i + 3)  # R100z22：止跌完成日也作为信号日
                    break
    if f2_hit:
        feats['f2'] = 15
        notes.append('3日缩量连跌后已收复过半跌幅')

    # ---- F3 二次试盘 + 横盘收窄 ----
    # R100z21：原 F3 硬依赖 f1_hit（F1 常年=false → F3 常年=false，特征只剩 f2/f4/f5/f6 四项二值）。
    # 改为独立计数最近 120 日的试盘次数，≥2 次即成立，与 F1 是否命中解耦。
    f3_hit = False
    probe_cnt = len(probe_idx)
    if probe_cnt < 2:
        probe_cnt = 0
        for i in range(max(1, n - 120), n - 4):
            vma = _vol_ma5(bars, i)
            if vma <= 0:
                continue
            if _upper_shadow(bars[i]) >= 2 * _body(bars[i]) and bars[i]['volume'] >= 1.5 * vma:
                probe_cnt += 1
    if probe_cnt >= 2:
        amp10 = (max(b['high'] for b in bars[-10:]) - min(b['low'] for b in bars[-10:])) / bars[-1]['close']
        amp20 = (max(b['high'] for b in bars[-20:]) - min(b['low'] for b in bars[-20:])) / bars[-1]['close']
        flat = amp10 < 0.6 * amp20
        center = sum(b['close'] for b in bars[-10:]) / 10
        within = all(abs(b['close'] - center) / center <= 0.06 for b in bars[-10:])
        if flat and within:
            f3_hit = True
            sig_idx = max(sig_idx, n - 2)  # R100z22：横盘收窄是「最近 10 日」的当下结构，算最新信号
    if f3_hit:
        feats['f3'] = 20
        notes.append('近 6 个月同类试盘共 %d 次·近 10 日振幅已收窄到 20 日的 6 成以内·'
                     '收盘贴着中枢 ±6%%' % probe_cnt)

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
            sig_idx = max(sig_idx, i + 5)  # R100z22：缩量横盘段末日
            break
    if f4_hit:
        feats['f4'] = 15
        notes.append('再下杀未破前低·6日内横盘±3%')

    # ---- F5 底部缩量板（R100z21：首次板 / 接力板 tier 分层）----
    # 首次板：本轮第一次涨停，上方无套牢、筹码干净，是「新资金进场」的启动位 → 高分。
    # 接力板：20 日内已有涨停，属老资金接力/连板中段，缩量要防对倒出货 → 低分。
    f5_hit = False
    f5_tier = ''
    for i in range(max(1, n - 60), n):
        prev_c = bars[i - 1]['close']
        gap = bars[i]['open'] / prev_c - 1
        if _is_limit_up(bars[i], prev_c) and gap >= 0.05 \
                and bars[i]['volume'] <= 0.8 * bars[i - 1]['volume'] \
                and bars[i]['close'] == bars[i]['high']:
            pre_zt = any(_is_limit_up(bars[k], bars[k - 1]['close'])
                         for k in range(max(1, i - 20), i))
            f5_tier = '首次板' if not pre_zt else '接力板'
            f5_hit = True
            sig_idx = max(sig_idx, i)  # R100z22：涨停日是最靠后的强信号
            break
    if f5_hit:
        feats['f5'] = 22 if f5_tier == '首次板' else 16
        if f5_tier == '首次板':
            notes.append('底部缩量跳空涨停（量仅前一日 8 成以下）·命中首次板分支（22 分）：'
                         '本轮第一根涨停、上方筹码干净无套牢')
        else:
            notes.append('底部缩量跳空涨停（量仅前一日 8 成以下）·命中接力板分支（16 分）：'
                         '前 20 日已有涨停，属老资金接力，防对倒出货')
    else:
        f5_tier = '-'

    score = sum(feats.values())
    hit = sum(1 for v in feats.values() if v > 0)
    # R100z22：信号滞后 = 最近一次信号（试盘/涨停）距今几个交易日；历史 K 线模型下，
    # 同样 76 分，「3 日前有涨停」和「40 日前有涨停」完全不是一回事，据此加新鲜度硬纪律：
    # 滞后 ≥25 日直接不入池；≥70 分但滞后 >10 日的降级为「观察」（结构还在，但已不是当下可执行的票）。
    lag = (n - 1 - sig_idx) if sig_idx >= 0 else None
    if lag is not None and lag >= POOL_MAX_LAG:
        grade = '不入池'
    elif score >= 70 and lag is not None and lag <= STRONG_MAX_LAG:
        grade = '强'
    else:
        grade = '观察' if score >= MIN_SCORE else '不入池'
    return {'score': score, 'features': feats, 'hitCount': hit, 'grade': grade,
            'f5Tier': f5_tier, 'sigIdx': sig_idx, 'lagDays': lag,
            'note': '；'.join(notes) if notes else '无特征命中'}


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
    names = K.collect_pool_names(D)  # R100g：池内 code→name（stkKlineNames 缺名兜底）
    sec_map = K.collect_sectors(D)   # R100q：code→行业（弹窗板块徽章全覆盖，新浪兜底）
    # R100m：fullScan 候选名并入（全市场标的简称）
    fs = D.get('fullScan') or {}
    if fs.get('date') == datetime.date.today().strftime('%Y-%m-%d') and isinstance(fs.get('names'), dict):
        for c, nm in fs['names'].items():
            names.setdefault(str(c), nm)
    codes = K.collect_pool_codes(D)
    # 扩展：断板池 code 一并纳入扫描
    for pool in ('confirmed', 'watching'):
        for e in (D.get('duanban') or {}).get(pool) or []:
            c = str(e.get('code') or '')
            if c.startswith(('60', '00')) and c not in codes:
                codes.append(c)
    # R100m：全量扫候选并入扫描域（fullScan.date==今日 才生效；缺失/过期自动退回池内域，不清场）
    if fs.get('date') == datetime.date.today().strftime('%Y-%m-%d') and fs.get('candidates'):
        fs_codes = [str(c) for c in fs['candidates'] if str(c).startswith(('60', '00'))]
        codes = sorted(set(codes) | set(fs_codes))
        print(f"[ACC] 全量扫候选并入：+{len(fs_codes)} 只（扫描域 {len(codes)} 只）")
    else:
        print('[ACC] fullScan 缺失/非今日，退回池内扫描域')
    codes = sorted(set(codes))
    # R100z25：口径说明写准——内嵌 stkKlines 只有 90 根（不够 F3/F6 的 120/250 日窗口），
    # 打分一律走网络全量 320 根，前端弹窗才读内嵌那 90 根做 K 线图，两者各取所需。
    print(f"[ACC] 扫描域 {len(codes)} 只（K 线取网络全量 320 根）")

    scored = []
    sample_day = ''
    for c in codes:
        bars = K.get_bars(c)
        if not bars or len(bars) < 60:
            continue
        # R100z21：tradeDate 取样本最后一根 K 线的交易日（非跑批当天，避免非交易日写脏）
        if not sample_day and bars[-1].get('day'):
            sample_day = str(bars[-1]['day'])[:10]
        nm = names.get(c) or (D.get('stkKlineNames') or {}).get(c) or ''
        r = score_one(bars, nm)
        if r['score'] < MIN_SCORE or r['grade'] == '不入池':
            continue
        # R100z22：信号滞后直接取 score_one 的返回值（最近一次试盘/涨停距今几个交易日）；
        # 排序时作为次序键（滞后小者靠前），前端据此挂灰标， grade 判定也已用同一口径。
        lag_days = r.get('lagDays')
        scored.append({'code': c, 'name': nm, 'sector': sec_map.get(c, ''), 'score': r['score'],
                       'grade': r['grade'], 'features': r['features'],
                       'hitCount': r['hitCount'], 'f5Tier': r.get('f5Tier') or '-',
                       # R100z22：sigIdx 为最近一次信号（试盘/涨停）的 K 线序号，算滞后要用
                       'sigIdx': int(r.get('sigIdx') or -1),
                       'lagDays': lag_days,
                       'note': r['note'],
                       'story': '', 'deduce': None})
    # R100z21：双键排序——score（权重深度）主序，hitCount（特征广度）次序，code 兜底保证确定性
    # R100z22：滞后天数作为次序键（越新鲜越靠前），缺失（无试盘/涨停信号）排最后
    scored.sort(key=lambda x: (-x['score'], -x['hitCount'],
                               x['lagDays'] if x['lagDays'] is not None else 999, x['code']))
    scored = scored[:20]
    today = datetime.date.today().strftime('%Y-%m-%d')
    trade_date = sample_day or today

    # R100z21：显式分池（强池 / 观察池），供前端分组渲染；scored 仍为全量强在前
    strong = [s for s in scored if s['grade'] == '强']
    watch = [s for s in scored if s['grade'] == '观察']
    n_first = sum(1 for s in scored if s.get('f5Tier') == '首次板')
    lags = [s['lagDays'] for s in scored if s['lagDays'] is not None]
    if lags:
        lag_txt = (f"信号新鲜度：最近一次试盘/涨停平均滞后 {sum(lags) / len(lags):.1f} 个交易日"
                   f"（最长 {max(lags)} 日）｜规则：滞后 ≤{STRONG_MAX_LAG} 日才进强池，"
                   f">={STRONG_MAX_LAG} 日降观察，≥{POOL_MAX_LAG} 日直接不入池。")
    else:
        lag_txt = (f"信号新鲜度：池内均无线索类强信号（试盘/涨停），全部按滞后规则降级处理"
                   f"（≤{STRONG_MAX_LAG} 日才进强池，≥{POOL_MAX_LAG} 日不入池）。")
    summary = (f"六特征打分：强 {len(strong)} 只 / 观察 {len(watch)} 只（共 {len(scored)} 只）。"
               f"强池=结构完整、且信号在 {STRONG_MAX_LAG} 个交易日内、可直接跟（盯量能与次日竞价）；"
               f"观察池=缺一两项或信号走旧的候选，只排队。"
               f"F5 分层：首次板 {n_first} 只（底部启动，筹码干净，高分）、"
               f"接力板 {len(scored) - n_first} 只（老资金接力，防对倒）。" + lag_txt)

    new_field = {
        'updatedAt': f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}（吸筹打分 {trade_date} 收盘 数据已自动更新）",
        'tradeDate': trade_date,
        'scored': scored,
        'strong': strong,
        'watch': watch,
        'summary': summary,
    }

    old = D.get('accumulation')
    # R100z44：入池首日标记——新入池标的 entryDate=本数据交易日，老标的沿用首次入池日
    # （从旧 data.js 按 code 继承；离池后再进视为新入池）。前端据此首日黄色高亮+「新」徽章。
    _old_ed = {str(i.get('code') or ''): i.get('entryDate')
               for i in (old or {}).get('scored') or [] if isinstance(i, dict)}
    for i in new_field.get('scored') or []:
        i['entryDate'] = _old_ed.get(str(i.get('code') or '')) or trade_date

    # ---------------- R100z56：失效归档池（用户 2026-10-04 要求，参考断板反包/谐波） ----------------
    # 定义：上一轮 scored 内、本轮跌出（不再达标）的标的 → 归因失效归档，自失效日起留 5 个交易日。
    # 归因只认三条硬证据，取不到就写「本轮未达 50 分线（形态走坏）」，绝不编原因：
    #   ① 破位止损：最新收盘 < 入池日收盘 ×0.975（吸筹纪律＝跌破最近收盘 −2.5% 无条件离场）
    #   ② 信号走旧：上一轮 lagDays ≥ POOL_MAX_LAG（25 日），本轮直接不入池
    #   ③ 评分掉出 MIN_SCORE（50 分线）
    def _acc_fail_reason(code, oi):
        bars = K.get_bars(code) or []
        if bars and len(bars) >= 2:
            entry_close = None
            ed = str(oi.get('entryDate') or '').strip()
            if ed:
                for b in bars:
                    if str(b.get('day') or '').strip()[:10] == ed:
                        entry_close = b.get('close')
                        break
            if entry_close:
                if bars[-1].get('close', 0) < entry_close * 0.975:
                    return f"破位止损（{ed} 入池收盘 {entry_close} → 最新 {bars[-1]['close']}，跌破 −2.5%）"
        lag = oi.get('lagDays')
        if lag is not None and lag >= POOL_MAX_LAG:
            return f"信号走旧（滞后 {lag} 日 ≥{POOL_MAX_LAG} 日线，本轮不入池）"
        return f"本轮评分低于 {MIN_SCORE} 分线（上一轮 {oi.get('score')} 分），形态走坏"

    _old_scored = (old or {}).get('scored') or []
    _new_codes = {str(i.get('code')) for i in scored}
    new_fails = []
    for _oi in _old_scored:
        if not isinstance(_oi, dict):
            continue
        _c = str(_oi.get('code') or '')
        if not _c or _c in _new_codes:
            continue
        new_fails.append({'code': _c,
                          'name': _oi.get('name') or '',
                          'sector': _oi.get('sector') or '',
                          'score': _oi.get('score'),
                          'grade': _oi.get('grade') or '',
                          'lagDays': _oi.get('lagDays'),
                          'reason': _acc_fail_reason(_c, _oi),
                          'entryDate': trade_date})
    if not scored and old:
        print('[ACC] 本轮无 ≥50 分标的，保留既有池（R91n 不清场）')
        old['note'] = (old.get('note') or '') + f"｜{today} 本轮无新命中"
        old['failPool'] = common.roll_fail_pool(old.get('failPool') or [], [], trade_date)
        new_field = old
    elif not scored and not old:
        print('[ACC] 无命中且无旧值，写空结构')
    else:
        _existed = len(new_fails)
        new_field['failPool'] = common.roll_fail_pool(
            old.get('failPool') or [], new_fails, trade_date)
        if _existed:
            print(f"[ACC] 失效池新增 {_existed} 只（5 交易日滚动窗口后共 "
                  f"{len(new_field['failPool'])} 只）")
    D['accumulation'] = new_field

    if dry:
        print(f"[ACC][DRY] 命中 {len(scored)} 只（强/观察），未写回")
        print(json.dumps([(i['code'], i['name'], i['score'], i['grade'], i['hitCount'],
                           i['f5Tier'], i['features']) for i in scored], ensure_ascii=False))
        return

    out = 'window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False,
                                                  separators=(',', ':')) + ';\n'
    with open(os.path.join(BASE, 'data.js'), 'w', encoding='utf-8') as f:
        f.write(out)
    print(f"[ACC] 写回 data.js：命中 {len(scored)} 只（强 {len(strong)} / 观察 {len(watch)}）｜tradeDate={trade_date}")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""谐波形态检测（2026-09-26 新增 · 方向①=⑤ 合并项）。

方法论与数据契约：fin-strategy-engine skill `references/harmonic-patterns.md`。
七形态看涨几何与比率以用户标准图（skill assets/IMG_9819~9825）为准：
  AB=CD / Bat / Gartley / Butterfly / Crab / Cypher / Shark。
统一目标位口径：T1 = D + 0.382×|CD|，T2 = D + 0.618×|CD|（与标准图拟合误差 ≤2%）。

- 扫描域：data.js 标的池（collect_pool_codes，主板 60/00）。
- 摆动点：ZigZag 阈值 5%（日级）。
- 分池：确认池（D 已触 PRZ 且现企稳阳线）/ 观察池（D 途中，距 PRZ ≤8%）。
- 输出：D.harmonic（契约见 references §6）。失败保留旧值（R91n）；全源失败且无旧值则字段置空结构。
- 用法：python3 harmonic_detect.py [--dry-run] [--self-test] [--days 320]
  --self-test：合成 7 形态序列离线自检，不读写 data.js。
"""
import sys, json, os, time, datetime

import kline_cache as K
# R100z57 交易日纪律：交易日口径一律走 common.today_trade_date（周末/长假跑不得写假日期），
# 常见写法 date.today() 会印出「周六收盘」并把全池误挂「新」徽章。
# R100z64：此处原有一行 `import common`，但本文件从不在顶层用它（真正调用点都在函数内
# import common，见 _trading_days_between / main），属死 import，已删；删后 pyflakes 仍干净。

BASE = os.path.dirname(os.path.abspath(__file__))

PATTERNS = {
    # name: dict(B=XA回撤目标区间, C=AB回撤区间, D判定函数, band=各比率容差)
    'AB=CD':      {'b': (0.382, 0.618), 'c': (0.382, 0.886)},
    'Bat':        {'b': (0.45, 0.55),   'c': (0.382, 0.886)},
    'Gartley':    {'b': (0.55, 0.68),   'c': (0.382, 0.886)},
    'Butterfly':  {'b': (0.72, 0.85),   'c': (0.30, 0.55)},
    'Crab':       {'b': (0.55, 0.68),   'c': (0.30, 0.55)},
    'Cypher':     {'b': (0.382, 0.618), 'c': None},
    'Shark':      {'b': (0.80, 0.95),   'c': None},
}
TOL = 0.06          # B 腿容差
TOL_C = 0.12        # C 腿容差
TOL_D = 0.10        # D 腿容差


def atr14(bars):
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]['high'], bars[i]['low'], bars[i-1]['close']
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return None
    m = trs[-14:]
    return sum(m) / len(m)


MIN_GAP = 3  # 相邻枢轴最小间隔（bar），防单根长阳/长阴引发的假震荡


def _day_at(bars, idx):
    """枢轴 idx → 交易日（YYYY-MM-DD），用于前端 K 线图标注 X/A/B/C/D 落点。"""
    try:
        if isinstance(idx, int) and 0 <= idx < len(bars):
            return bars[idx].get('day')
    except Exception:
        pass
    return None


def _append_pivot(piv, idx, price, typ):
    """强制 H/L 交替：同型枢轴出现时保留更极值者。"""
    if piv and piv[-1][2] == typ:
        if (typ == 'H' and price >= piv[-1][1]) or (typ == 'L' and price <= piv[-1][1]):
            piv[-1] = (idx, price, typ)
        return
    piv.append((idx, price, typ))


def zigzag(bars, pct=0.05):
    """阈值摆动点：返回 [(idx, price, 'H'|'L')]。"""
    if len(bars) < 10:
        return []
    piv = []
    direction = 0
    ext_i, ext_p = 0, bars[0]['high']
    low_i, low_p = 0, bars[0]['low']
    for i in range(1, len(bars)):
        h, l = bars[i]['high'], bars[i]['low']
        if direction >= 0:
            if h > ext_p:
                ext_p, ext_i = h, i
            if l <= ext_p * (1 - pct):
                if not piv or (i - piv[-1][0] >= MIN_GAP
                               and abs(ext_p - piv[-1][1]) / piv[-1][1] >= pct):
                    _append_pivot(piv, ext_i, ext_p, 'H')
                direction, low_i, low_p = -1, i, l
                continue
        if direction <= 0:
            if l < low_p:
                low_p, low_i = l, i
            if h >= low_p * (1 + pct):
                if not piv or (i - piv[-1][0] >= MIN_GAP
                               and abs(low_p - piv[-1][1]) / piv[-1][1] >= pct):
                    _append_pivot(piv, low_i, low_p, 'L')
                direction, ext_i, ext_p = 1, i, h
    tail = piv[-1][2] if piv else 'H'
    if tail == 'H':
        li = min(range(piv[-1][0] + 1, len(bars)), key=lambda k: bars[k]['low'], default=None)
        if li is not None and bars[li]['low'] < piv[-1][1] * (1 - pct) \
                and li - piv[-1][0] >= MIN_GAP:
            _append_pivot(piv, li, bars[li]['low'], 'L')
    else:
        hi = max(range(piv[-1][0] + 1, len(bars)), key=lambda k: bars[k]['high'], default=None)
        if hi is not None and bars[hi]['high'] > piv[-1][1] * (1 + pct) \
                and hi - piv[-1][0] >= MIN_GAP:
            _append_pivot(piv, hi, bars[hi]['high'], 'H')
    return piv


def _in(v, lo, hi, tol):
    return lo - tol <= v <= hi + tol


def match_patterns(X, A, B, C, D):
    """对五点结构匹配七形态，返回 (pattern, ratios, prz, detail) 或 None。
    几何以标准图为准：X=高、A=低、B=高；C 为 B 后的枢轴（低或延伸高）；D 为 C 后极值。
    """
    xa = X - A
    if xa <= 0:
        return None
    rB = (B - A) / xa
    best = None
    cands = []
    for name, cfg in PATTERNS.items():
        r = {}
        if name in ('AB=CD', 'Bat', 'Gartley', 'Butterfly', 'Crab'):
            # C 为 B 后低点（正常枢轴）
            if C >= B:
                continue
            rC = (B - C) / max(B - A, 1e-9)
            if not _in(rC, cfg['c'][0], cfg['c'][1], TOL_C):
                continue
            if not _in(rB, cfg['b'][0], cfg['b'][1], TOL):
                continue
            if name == 'AB=CD':
                d_proj = C + 1.0 * (B - A)
                r['CD'] = (D - C) / max(B - A, 1e-9)
                ok = D > C and abs(r['CD'] - 1.0) <= TOL_D
                retrace_chk = (D - A) / xa
                ok = ok and 0.55 <= retrace_chk <= 0.85
            elif name == 'Bat':
                d_proj = A + 0.886 * xa
                ok = D > C and abs((D - A) / xa - 0.886) <= TOL_D
            elif name == 'Gartley':
                d_proj = A + 0.786 * xa
                ok = D > C and abs((D - A) / xa - 0.786) <= TOL_D
            elif name == 'Butterfly':
                d_proj = A - 0.270 * xa
                ok = D < A and abs((A - D) / xa - 0.270) <= TOL_D
            else:  # Crab
                d_proj = A - 0.618 * xa
                ok = D < A and abs((A - D) / xa - 0.618) <= TOL_D
            if not ok:
                continue
            r['AB'], r['BC'] = round(rB, 3), round(rC, 3)
            r['CD'] = round(abs(D - C) / max(abs(B - A), 1e-9), 3)
            cands.append((name, d_proj, r))
        elif name == 'Cypher':
            # C 为 B 后延伸高（须高于 X），D 回撤 0.786 XC
            if C <= X:
                continue
            rC = (C - B) / max(B - A, 1e-9)
            if not _in(rC, 1.13, 1.414, TOL_C + 0.05):
                continue
            if not _in(rB, cfg['b'][0], cfg['b'][1], TOL):
                continue
            d_proj = C - 0.786 * (C - X)
            if D >= C or abs((C - D) / max(C - X, 1e-9) - 0.786) > TOL_D:
                continue
            r = {'AB': round(rB, 3), 'BC': round(rC, 3), 'CD': 0.786}
            cands.append((name, d_proj, r))
        else:  # Shark
            # B≈0.886 XA 回撤；C 跌破 A（BC 腿 ≈1.13 XA）；D=C+1.618 XA 延伸（X 上方）
            if C >= A:
                continue
            rC = (B - C) / xa
            if not _in(rC, 1.0, 1.30, TOL_C):
                continue
            if not _in(rB, cfg['b'][0], cfg['b'][1], TOL):
                continue
            d_proj = C + 1.618 * xa
            if D <= C or abs((D - C) / xa - 1.618) > TOL_D + 0.05:
                continue
            r = {'AB': round(rB, 3), 'BC': round(rC, 3), 'CD': 1.618}
            cands.append((name, d_proj, r))
    if not cands:
        return None
    # 取 D 偏差最小者
    def dev(c):
        name, dp, r = c
        if name in ('AB=CD',):
            return abs(r['CD'] - 1.0)
        if name in ('Bat',):
            return abs((D - A) / xa - 0.886)
        if name in ('Gartley',):
            return abs((D - A) / xa - 0.786)
        if name in ('Butterfly',):
            return abs((A - D) / xa - 0.270)
        if name in ('Crab',):
            return abs((A - D) / xa - 0.618)
        if name in ('Cypher',):
            return abs((C - D) / max(C - X, 1e-9) - 0.786)
        return abs((D - C) / xa - 1.618)
    best = min(cands, key=dev)
    name, d_proj, r = best
    return name, d_proj, r


def _run_high(bars, frm):
    seg = bars[frm:]
    if not seg:
        return None, None
    k = max(range(len(seg)), key=lambda t: seg[t]['high'])
    return seg[k]['high'], frm + k


def _run_low(bars, frm):
    seg = bars[frm:]
    if not seg:
        return None, None
    k = min(range(len(seg)), key=lambda t: seg[t]['low'])
    return seg[k]['low'], frm + k


def scan_bars(bars, pct=0.05):
    """对单只 K 线做形态扫描，返回候选 dict 或 None。

    枢轴解释优先级（防误判）：
    1. X(H),A(L),B(H),C(L),D(第五枢轴) —— 完整五枢轴，全七形态；
    2. X,A,B(H),C(L) + D=运行极值（D 未确认） —— 回撤族观察态；
    3. X,A,B(H) 且其后运行低点跌破 A：C 为途中点（名义 0.5AB）—— Butterfly/Crab 延伸族；
    4. X,A,B(H) 且 B 高于 X：Cypher（B 名义派生），D=C−0.786XC。
    """
    if not bars or len(bars) < 40:
        return None
    piv = zigzag(bars, pct)
    if len(piv) < 3:
        return None
    last_close = bars[-1]['close']
    out = []
    for i in range(len(piv) - 2):
        p1, p2, p3 = piv[i], piv[i+1], piv[i+2]
        if not (p1[2] == 'H' and p2[2] == 'L' and p3[2] == 'H'):
            continue
        X, A, B = p1[1], p2[1], p3[1]
        xi, ai, bi = p1[0], p2[0], p3[0]
        if X - A <= 0:
            continue
        tried = []

        # 解释 1/2：存在 C 枢轴（L）——元组尾附各枢轴 bar idx，供前端 K 线定位 X/A/B/C/D
        if i + 3 < len(piv) and piv[i+3][2] == 'L':
            C = piv[i+3][1]; ci = piv[i+3][0]
            if i + 4 < len(piv):
                D, di = piv[i+4][1], piv[i+4][0]
                tried.append((X, A, B, C, D, xi, ai, bi, ci, di))
                # Shark 解释：C=p4，D=p5（若与解释 1 的 D 不同）
                if piv[i+4][2] == 'H':
                    tried.append((X, A, B, C, piv[i+4][1], xi, ai, bi, ci, piv[i+4][0]))
            else:
                D, di = _run_high(bars, piv[i+3][0])
                if D:
                    tried.append((X, A, B, C, D, xi, ai, bi, ci, di))
        # 解释 3：延伸族（B 后低点跌破 A，C 为途中点，无真实 C 枢轴 → 名义价 0.5AB）
        run_lo, run_lo_i = _run_low(bars, p3[0])
        if run_lo is not None and run_lo < A:
            C_nom = B - 0.5 * (B - A)
            # R100z4q：名义 C 补真实日期——B 后第一根 low 触及 C_nom 的 bar
            # （run_lo < A < C_nom，穿越 bar 必然存在），前端 K 线连线才有 C 顶点
            ci_nom = None
            for j in range(bi + 1, run_lo_i + 1):
                if bars[j]['low'] <= C_nom:
                    ci_nom = j
                    break
            tried.append((X, A, B, C_nom, run_lo, xi, ai, bi, ci_nom, run_lo_i))
        # 解释 4：Cypher（p3 高于 X，B 名义派生）
        if B > X and p3[0] + 1 < len(bars):
            Cq = B; cqi = bi
            Dq, dqi = _run_low(bars, p3[0])
            if Dq:
                k = 1.272
                Bq = (Cq + k * A) / (1 + k)
                rBq = (Bq - A) / (X - A)
                if 0.382 - TOL <= rBq <= 0.618 + TOL:
                    tried.append(('CYPHER', X, A, Bq, Cq, Dq, xi, ai, bi, cqi, dqi))
        for t in tried:
            if len(t) == 11:
                _, X2, A2, B2, C2, D2, xi2, ai2, bi2, ci2, di2 = t
            else:
                X2, A2, B2, C2, D2, xi2, ai2, bi2, ci2, di2 = t
            m = match_patterns(X2, A2, B2, C2, D2)
            if not m:
                continue
            name, d_proj, ratios = m
            a = atr14(bars) or max(0.01, last_close * 0.02)
            prz_lo, prz_hi = d_proj - 0.5 * a, d_proj + 0.5 * a
            if name == 'Shark':
                stop = B2 - a
            elif name in ('Butterfly', 'Crab', 'Cypher'):
                stop = min(D2, d_proj) - a
            else:
                stop = A2 - a
            cd = abs(D2 - C2)
            t1, t2 = D2 + 0.382 * cd, D2 + 0.618 * cd
            touched = bars[-1]['low'] <= prz_hi
            bull = bars[-1]['close'] >= bars[-1]['open']
            stage = '确认' if (touched and bull and last_close >= prz_lo * 0.995) else '观察'
            dist = round((prz_hi - last_close) / last_close * 100, 2)
            # 有效性闸门（2026-09-28）：价格已涨过 PRZ 上沿 3% 以上（D 结构走完失效，
            # 如 601869 几月前 Crab D=71.98 而现价 402），或距 PRZ 仍差 8% 以上（D 远未
            # 到位），一律作废——防陈旧形态混入观察池导致 PRZ 与现价严重脱节
            if dist > 8 or dist < -3:
                continue
            # R100z4q：D 点时效闸门——D 距今超 30 个交易日的形态一律作废。
            # 此前只看价格距离（600593 D=2026-01、002005 D=2025-07 现价恰在 PRZ 附近徘徊
            # 照样过闸），但「D段确认·企稳」的反转窗口早已关闭，陈旧图形不应留在日度池
            if not isinstance(di, int) or (len(bars) - 1 - di) > 30:
                continue
            out.append({
                'pattern': name,
                'points': {'X': round(X2, 2), 'A': round(A2, 2), 'B': round(B2, 2),
                           'C': round(C2, 2), 'D': round(D2, 2)},
                'pointDays': {'X': _day_at(bars, xi2), 'A': _day_at(bars, ai2),
                              'B': _day_at(bars, bi2), 'C': _day_at(bars, ci2),
                              'D': _day_at(bars, di2)},
                'ratios': ratios, 'prz': [round(prz_lo, 2), round(prz_hi, 2)],
                'stop': round(stop, 2), 'target1': round(t1, 2), 'target2': round(t2, 2),
                # R100z52：T1/T2 按谐波定义从 **D 点** 起算（不动定义），但现价贴 PRZ 上沿时
                # 会出现 T1 ≤ 现价（目标已达成/「倒挂」）。以前只把数字塞进 data.js，前端不渲染，
                # 看不出倒挂；现在显式给「是否达成 + 相对现价百分比」，前端据此打提示。
                't1Reached': bool(last_close >= t1), 't2Reached': bool(last_close >= t2),
                't1GapPct': round((t1 - last_close) / last_close * 100, 2),
                't2GapPct': round((t2 - last_close) / last_close * 100, 2),
                'stage': stage, 'distToPrzPct': dist,
                'dead': last_close < stop,
            })
    if not out:
        return None
    out.sort(key=lambda c: abs(c['points']['D'] - (c['prz'][0] + c['prz'][1]) / 2))
    return out[0]


# ---------------- 自检（合成 7 形态） ----------------

def _mk_bars(points):
    """把五点价格序列铺成合成 K 线（线性插值 + 微噪声）。"""
    import random
    random.seed(42)
    bars = []
    path = []
    for a, b in zip(points[:-1], points[1:]):
        steps = 20
        for s in range(steps):
            t = s / steps
            path.append(a + (b - a) * t)
    path.append(points[-1])
    base_day = datetime.date(2026, 1, 1)
    for i, p in enumerate(path):
        noise = random.uniform(-0.15, 0.15)
        o = path[i-1] if i else p
        c = p + noise * 0.3
        hi = max(o, c, p) + abs(noise)
        lo = min(o, c, p) - abs(noise)
        bars.append({'day': str(base_day + datetime.timedelta(days=i)), 'open': o,
                     'close': c, 'high': hi, 'low': lo, 'volume': 1000 + i})
    return bars


def self_test():
    cases = {
        'AB=CD':     [100, 70, 85.0, 75.73, 90.73],
        'Bat':       [100, 70, 85.0, 71.77, 96.58],
        'Gartley':   [100, 70, 88.54, 72.1, 93.58],
        'Butterfly': [100, 70, 93.58, 84.57, 61.90],
        'Crab':      [100, 70, 88.54, 81.46, 51.46],
        'Cypher':    [100, 70, 88.54, 112.12, 102.59],
        'Shark':     [100, 70, 96.58, 62.66, 111.22],
    }
    ok = 0
    for name, pts in cases.items():
        bars = _mk_bars(pts)
        r = scan_bars(bars)
        got = r['pattern'] if r else None
        status = 'OK' if got == name else 'FAIL'
        if got == name:
            ok += 1
        print(f"[SELF-TEST] {name:<10} -> {got or 'None':<10} {status}"
              + (f" prz={r['prz']} stage={r['stage']}" if r else ''))
    print(f"[SELF-TEST] {ok}/7 通过")
    return ok == 7


# ---------------- 失败池（R100i）：破位 / 形态失效归档，5 交易日后自动清理 ----------------

STATE_FILE = os.path.join(BASE, '.harmonic_state.json')


def _trading_days_between(d0, d1):
    """真实交易日数量（含端点，节假日感知），用于「自失效日起保留 5 个交易日」清理闸门。
    R100z44：委托 common.trading_days_between——旧实现只数周一~周五，国庆/春节长假
    会被数成交易日，节后首日运行时失效池被提前清空（09-30 归档到 10-08 被数成 8>5 误删）。"""
    import common
    return common.trading_days_between(d0, d1)


def _load_state():
    try:
        with open(STATE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(st):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(st, f, ensure_ascii=False)
    except Exception:
        pass


def _clean_fail(fp, today):
    """失效池 5 个交易日滚动窗口清洗（R100z44：自失效/归档日起保留 5 个真实交易日，
    超期剔除；节假日感知，国庆/春节不误数）。正常轮次与抓取异常日都走这道闸门。"""
    if not fp:
        return []
    out = []
    for f in fp:
        ed = f.get('entryDate')
        try:
            if ed and _trading_days_between(ed, today) > 5:
                continue
        except Exception:
            pass
        out.append(f)
    return out


def _compute_fail_pool(prev_pools, today_codes, cur_info, today, bars_map=None):
    """prev_pools 中已不在今日池的标的 → 归因失败入池。

    R100z4x：归档时快照「失效前」完整几何（failHz），前端失效池弹窗据此重放
    XABCD 连线与 PRZ 参数（ points/pointDays 由 cur_pools 全量留存后才有，
    历史条目由 backfill_failhz.py 一次性回补，缺失时前端自然降级为纯K线）。
    R100z5a：误归档防护——滑出池但重扫同形态几何仍有效（运行间检测噪声，如
    宝新能源 D 点仅 2 个交易日却被告超龄），直接跳过不归档。"""
    fails = []
    for code, info in prev_pools.items():
        if code in today_codes:
            continue
        cur = cur_info.get(code)
        if cur is None:
            # R100z5a：重扫复核——几何仍有效（同形态、未破位、过价格闸门）属检测噪声，不归档
            _b = (bars_map or {}).get(code)
            _rr = scan_bars(_b) if _b and len(_b) >= 40 else None
            if (_rr and not _rr.get('dead') and _rr.get('points') and _rr.get('prz')
                    and _rr.get('pattern') == info.get('pattern')):
                continue
            d_day = ((info.get('pointDays') or {}).get('D') or '')[:10]
            d_age = None
            try:
                d_age = (datetime.date.today() - datetime.datetime.strptime(d_day, '%Y-%m-%d').date()).days
            except Exception:
                d_age = None
            if d_age is not None and d_age * 5 / 7 > 30:
                reason = 'D 点距今超 30 个交易日，形态超龄归档（价格未破止损、仍在反转区附近，保守起见不再跟踪）'
            else:
                reason = '形态失效（最新 K 线使 XABCD 摆动结构不再成立，不再符合谐波几何）'
        elif cur.get('dead'):
            reason = '跌破止损位（' + str(info.get('stop')) + '），形态破位失效'
        else:
            dist = cur.get('distToPrzPct')
            reason = '价格远离反转区（距 PRZ ' + (str(dist) + '%' if dist is not None else '') + '，D 结构走完失效）'
        f = {'code': code, 'name': info.get('name', ''), 'pattern': info.get('pattern', ''),
             'sector': info.get('sector', ''),
             'reason': reason, 'entryDate': today}
        # R100z4x：failHz 快照——只在几何字段齐全时写入（宁缺勿滥，前端按 prz 存在性守卫）
        if info.get('points') and info.get('pointDays') and info.get('prz'):
            f['failHz'] = {k: info.get(k) for k in (
                'stage', 'points', 'pointDays', 'ratios', 'prz', 'stop', 'target1', 'target2')}
            # R100z5a：归档轮次即失效轮次——failDay=失效日（当日收盘 K 线），asOf=前一日
            _b = (bars_map or {}).get(code)
            if _b and len(_b) >= 2:
                f['failHz']['failDay'] = _b[-1]['day']
                f['failHz']['asOf'] = _b[-2]['day']
        fails.append(f)
    return fails


def _collect_sectors(D):
    """R100q：改走 kline_cache.collect_sectors 共享映射（断板池/精选/谐波/连板/板块TOP/
    AI预测 站内字段优先 + 新浪行业全量兜底），弹窗板块徽章全覆盖。保留壳兼容旧调用。"""
    return K.collect_sectors(D)


# ---------------- 主流程 ----------------

def main():
    args = sys.argv[1:]
    if '--self-test' in args:
        sys.exit(0 if self_test() else 1)

    import common
    dry = '--dry-run' in args
    days = 320
    if '--days' in args:
        days = int(args[args.index('--days') + 1])

    D = common.load_dashboard_data(BASE)
    names = K.collect_pool_names(D)  # R100g：池内 code→name（stkKlineNames 缺名兜底）
    # R100m：fullScan 候选名并入（全市场标的简称）
    fs = D.get('fullScan') or {}
    if fs.get('date') == datetime.date.today().strftime('%Y-%m-%d') and isinstance(fs.get('names'), dict):
        for c, nm in fs['names'].items():
            names.setdefault(str(c), nm)
    sec_map = _collect_sectors(D)    # R100l：池内 code→sector（断板池真实行业优先）
    codes = K.collect_pool_codes(D)
    # R100m：全量扫候选并入扫描域（fullScan.date==今日 才生效；缺失/过期自动退回池内域，不清场）
    if fs.get('date') == datetime.date.today().strftime('%Y-%m-%d') and fs.get('candidates'):
        fs_codes = [str(c) for c in fs['candidates'] if str(c).startswith(('60', '00'))]
        codes = sorted(set(codes) | set(fs_codes))
        print(f"[HARMONIC] 全量扫候选并入：+{len(fs_codes)} 只（扫描域 {len(codes)} 只）")
    else:
        print('[HARMONIC] fullScan 缺失/非今日，退回池内扫描域')
    print(f"[HARMONIC] 标的池 {len(codes)} 只，抓取 {days} 日 K 线（预算 {K.DEADLINE_S:.0f}s）")
    t0 = time.time()
    kl = K.get_klines_bulk(codes, days=days, workers=8)
    print(f"[HARMONIC] 抓取完成 {sum(1 for v in kl.values() if v)}/{len(codes)}，"
          f"耗时 {time.time()-t0:.0f}s")

    # 吸筹共振表（accumulation_score.py 先跑则带 accResonance）
    acc = {}
    accd = D.get('accumulation') or {}
    for it in accd.get('scored', []) or []:
        if isinstance(it, dict) and it.get('code'):
            acc[it['code']] = it.get('hitCount', 0)

    # R100z4q：旧池 AI 叙事保留表——盘中/中途重跑本脚本会把 story/deduce 清空，
    # 直到下一次自动化 AI 补写；同 code 同 pattern 的条目直接继承旧叙事
    _old_map = {}
    _oldh = D.get('harmonic') or {}
    for _k in ('confirmPool', 'watchPool'):
        for _it in (_oldh.get(_k) or []):
            if isinstance(_it, dict) and _it.get('code'):
                _old_map[str(_it['code']) + '|' + str(_it.get('pattern') or '')] = (
                    _it.get('story') or '', _it.get('deduce'))

    confirm, watch = [], []
    cur_info = {}
    for c in codes:
        bars = kl.get(c)
        if not bars:
            cur_info[c] = None
            continue
        r = scan_bars(bars)
        cur_info[c] = r
        if not r or r.get('dead'):
            continue
        # 取简称：优先 stkKlineNames / duanban
        nm = names.get(c) or (D.get('stkKlineNames') or {}).get(c) or ''
        _sd = _old_map.get(str(c) + '|' + str(r['pattern'])) or ('', None)
        item = {
            'code': c, 'name': nm, 'sector': sec_map.get(c, ''),
            'pattern': r['pattern'],
            'stage': r['stage'], 'points': r['points'], 'pointDays': r.get('pointDays', {}),
            'ratios': r['ratios'],
            'prz': r['prz'], 'stop': r['stop'], 'target1': r['target1'],
            'target2': r['target2'], 'distToPrzPct': r['distToPrzPct'],
            'accResonance': int(acc.get(c, 0)),
            'lastClose': round(bars[-1]['close'], 2), 'lastDay': bars[-1]['day'],
            'story': _sd[0], 'deduce': _sd[1],
        }
        (confirm if r['stage'] == '确认' else watch).append(item)
    confirm.sort(key=lambda x: x['distToPrzPct'])
    watch.sort(key=lambda x: x['distToPrzPct'])

    # ⚠️ R100z57：不许拿 date.today() 当交易日——见 power_screener.py 同处注释。
    # 原先这里写的是 date.today()，10-03（周六）手工跑就把「周六收盘」写进了
    # updatedAt/tradeDate，26 只池内标的的 entryDate 也跟着落到周六（整屏误挂「新」）。
    today = common.today_trade_date()
    # R100h：tags 必须与截断后的池一致（此前用全量 confirm+watch，池截断 12/20 后
    # 被截掉的标的"量化卡有谐波徽标、弹窗无图像无提示"——findHarmonic 在池内找不到）
    pool_c, pool_w = confirm[:12], watch[:20]
    tags = {}
    for it in pool_c + pool_w:
        tags[it['code']] = it['pattern']

    old = D.get('harmonic')
    n_ok = sum(1 for v in kl.values() if v)
    fetch_ok = len(codes) > 0 and n_ok >= len(codes) * 0.8

    # R100i：失败池——原在池内、今日滑出的标的归因失败归档。
    # R100z44（用户 2026-10-04 拍板，推翻 R100z5a「只保留最新一轮归档」）：
    # 失效标的自失效日起保留 5 个交易日后才剔除——旧归档（5 交易日窗口内）继续携带，
    # 与断板侧 check_duanban.py 的 carried+new_fails 口径完全一致；
    # 抓取异常日沿用旧池兜底（R91m 防清场）。
    # 仅在抓取正常（fetch_ok）且确有数据/旧池时计算新失败，避免抓取故障时误判全池失效。
    today_codes = set([it['code'] for it in confirm] + [it['code'] for it in watch])
    prev_state = _load_state()
    prev_pools = prev_state.get('pools', {})
    # R100z44：入池首日标记——新入池标的 entryDate=今日，老标的沿用首次入池日（state 逐日携带）。
    # 前端据此给首日新入池卡片整体黄色高亮+「新」徽章，次日自动恢复（用户 2026-10-04 要求）。
    # ⚠️ R100z57：旧写法 `(prev_pools[code].entryDate) or today` 会在上一轮没留下 entryDate 时
    # 一律写今天 → 整屏刷「新」。改三分支（与 accumulation_score / power_screener 同口径）：
    # ① 旧池有 entryDate → 沿用；② 旧池没见过 → 写 today（确实是首次入池）；
    # ③ 旧池见过但无 entryDate（老数据、生成代码早于 R100z44）→ 不写，前端不挂徽章。
    for it in confirm + watch:
        _pv = prev_pools.get(it['code']) or {}
        if it['code'] in prev_pools and _pv.get('entryDate'):
            it['entryDate'] = _pv['entryDate']
        elif it['code'] not in prev_pools:
            it['entryDate'] = today
    carried = _clean_fail(prev_state.get('failPool', []), today)
    new_fails = []
    if fetch_ok and (confirm or watch or prev_pools):
        new_fails = _compute_fail_pool(prev_pools, today_codes, cur_info, today, kl)
    if fetch_ok:
        # R100z44：窗口内旧归档 + 今日新失效（同 code 以新条目为准）
        carried_codes = set(str(f.get('code')) for f in carried)
        failPool = carried + [f for f in new_fails if str(f.get('code')) not in carried_codes]
    else:
        failPool = carried
    # R100z4x：failHz 跨运行保留——把既有 data.js harmonic.failPool 的 failHz 按 code 合并回
    # carried/new 条目（backfill_failhz.py 一次性回补的失效前几何不会被下次抓取覆盖冲掉）；
    # 新失败项若已有快照（来自 prev_pools 几何）则不覆盖。
    if old and old.get('failPool'):
        _hz_by_code = {}
        for _f in old['failPool']:
            if _f.get('code') and isinstance(_f.get('failHz'), dict) and _f['failHz'].get('prz'):
                _hz_by_code[str(_f['code'])] = _f['failHz']
        for _f in failPool:
            if str(_f.get('code')) in _hz_by_code and not (
                    isinstance(_f.get('failHz'), dict) and _f['failHz'].get('prz')):
                _f['failHz'] = _hz_by_code[str(_f['code'])]
    # 保存状态供下次比对（抓取失败时保留 prev_pools，保证后续仍能识别滑出）
    # R100z4x：cur_pools 全量留存几何字段——标的滑出池时 _compute_fail_pool 才能快照失效前形态
    cur_pools = {}
    for it in confirm + watch:
        cur_pools[it['code']] = {'name': it['name'], 'pattern': it['pattern'],
                                 'sector': it.get('sector', ''),
                                 'stop': it.get('stop'), 'prz': it.get('prz'),
                                 'stage': it.get('stage'), 'points': it.get('points'),
                                 'pointDays': it.get('pointDays'), 'ratios': it.get('ratios'),
                                 'target1': it.get('target1'), 'target2': it.get('target2'),
                                 'entryDate': it.get('entryDate') or today}  # R100z44
    _save_state({'pools': cur_pools if fetch_ok else prev_pools, 'failPool': failPool})

    new_field = {
        'updatedAt': f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}（谐波形态 {today} 收盘 数据已自动更新）",
        'tradeDate': today,
        'confirmPool': pool_c,
        'watchPool': pool_w,
        'summary': '',
        'tags': tags,
        'failPool': failPool,
    }

    if not confirm and not watch and old and not fetch_ok:
        # 数据故障（大面积抓取失败）：保留旧池（R91n 不清场），失败池仅做 5 交易日清理
        print(f'[HARMONIC] 抓取异常 {n_ok}/{len(codes)}，保留既有池（R91n）')
        old['note'] = (old.get('note') or '') + f"｜{today} 抓取异常保留旧池"
        old['failPool'] = _clean_fail(old.get('failPool', []), today)
        new_field = old
    elif not confirm and not watch and old and fetch_ok:
        # 抓取正常但有效性闸门后无检出：旧池形态已失效（如价格涨离 PRZ），写空不误导
        print(f'[HARMONIC] 抓取正常 {n_ok}/{len(codes)} 但无有效形态，写空结构')
    if not confirm and not watch and not old:
        print('[HARMONIC] 无检出且无旧值，写空结构')
    D['harmonic'] = new_field

    if dry:
        print(f"[HARMONIC][DRY] 确认池 {len(confirm)} / 观察池 {len(watch)}，未写回")
        print(json.dumps({'confirm': [i['code'] + i['pattern'] for i in confirm[:8]],
                          'watch': [i['code'] + i['pattern'] for i in watch[:8]]},
                         ensure_ascii=False))
        return

    out = 'window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False,
                                                  separators=(',', ':')) + ';\n'
    with open(os.path.join(BASE, 'data.js'), 'w', encoding='utf-8') as f:
        f.write(out)
    print(f"[HARMONIC] 写回 data.js：确认池 {len(confirm)} / 观察池 {len(watch)}")


if __name__ == '__main__':
    main()

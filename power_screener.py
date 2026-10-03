#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量化强势筛选（2026-09-26 新增 · 方向②，分时条件已取消 → 纯日线收盘筛选）。

方法论：fin-strategy-engine skill 选股扩展（数据清单方向②）。全部为日级确定性阈值，
不依赖盘中分时（用户 9/26 拍板：取消盘中自动化）。

- 九道硬阈值（G1-G9）：G1 涨幅≥3% / G2 放量≥1.5×5日均量 / G3 价处20日区间上1/3 /
  G4 站上MA20 / G5 非ST主板 / G6 近5日无长上影出货 / G7 当日振幅≤15% /
  G8 波动率适中(ATR14/close∈[2%,8%]) / G9 RSI14∈[40,80]。
- 硬门禁：G1/G2/G4/G5/G7 必过；质量门：G3/G6/G8/G9 过≥3 道。
- score = 通过阈值数（0-9）；passed = 硬门禁全过 且 质量门≥3。
- 扫描域：collect_pool_codes ∪ 断板池 ∪ stkKlines 内 60/00 标的；优先内嵌 stkKlines。
- 交叉徽标：命中 harmonic.tags / accumulation.scored 则打标（前端用）。
- 输出：D.powerScreen。失败保留旧值（R91n）。
- 用法：python3 power_screener.py [--dry-run] [--self-test]
"""
import sys, json, os, datetime

import kline_cache as K

BASE = os.path.dirname(os.path.abspath(__file__))

# 硬门禁（必过）
HARD = ['g1', 'g2', 'g4', 'g5', 'g7']
# 质量门（过≥3）
SOFT = ['g3', 'g6', 'g8', 'g9']
SOFT_NEED = 3


def _ma(vals, n):
    if len(vals) < n:
        return None
    s = vals[-n:]
    return sum(s) / len(s)


def _rsi14(closes):
    if len(closes) < 15:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-14:]) / 14
    al = sum(losses[-14:]) / 14
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def screen_one(bars, name='', idx=None):
    """返回 dict: {passed, score, gates:{g1..g9:bool}, pct, volRatio, rsi, reason}。

    idx：以 bars[idx] 作为「最新一根」判定（历史逐日重放口径用，
    默认 None = bars[-1]，与实盘口径一致）。所有相对尾部的窗口（20 日区间、
    近 5 日、ATR14、RSI14）都随 idx 平移；idx 之外的数据一律不得泄漏。
    注：原依赖的外部回测脚本 signal_stats.py 已于 R100z31 下线（不计入
    data.js.signalStats），重放口径现由本函数自身支持。
    """
    n = len(bars)
    gates = {f'g{i}': False for i in range(1, 10)}
    if n < 25 or (idx is not None and idx < 24):
        return {'passed': False, 'score': 0, 'gates': gates, 'pct': 0,
                'volRatio': 0, 'rsi': 0, 'reason': '数据不足25根'}

    end = n - 1 if idx is None else idx
    last = bars[end]
    prev = bars[end - 1]
    closes = [b['close'] for b in bars[:end + 1]]
    pct = last['close'] / prev['close'] - 1
    # G1 涨幅≥3%
    gates['g1'] = pct >= 0.03
    # G2 放量
    vma5 = _ma([b['volume'] for b in bars], 5)
    vol_ratio = (last['volume'] / vma5) if vma5 else 0
    gates['g2'] = vol_ratio >= 1.5
    # G3 价处20日区间上1/3
    hi20 = max(b['high'] for b in bars[end - 19:end + 1])
    lo20 = min(b['low'] for b in bars[end - 19:end + 1])
    gates['g3'] = (hi20 - lo20) > 0 and last['close'] >= lo20 + 0.66 * (hi20 - lo20)
    # G4 站上MA20
    ma20 = _ma(closes, 20)
    gates['g4'] = ma20 is not None and last['close'] > ma20
    # G5 非ST主板（code 已在扫描域限定 60/00；此处只排 ST）
    gates['g5'] = 'ST' not in (name or '') and 'st' not in (name or '')
    # G6 近5日无长上影出货
    cnt_bad = 0
    for b in bars[end - 4:end + 1]:
        us = b['high'] - max(b['open'], b['close'])
        body = abs(b['close'] - b['open'])
        if body > 0 and us >= 2.5 * body and b['close'] < b['open']:
            cnt_bad += 1
    gates['g6'] = cnt_bad <= 1
    # G7 当日振幅≤15%
    amp = (last['high'] - last['low']) / last['close']
    gates['g7'] = amp <= 0.15
    # G8 波动率适中
    trs = []
    for i in range(1, end + 1):
        h, l, pc = bars[i]['high'], bars[i]['low'], bars[i - 1]['close']
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[-14:]) / 14 if len(trs) >= 14 else 0
    vrate = atr / last['close'] if last['close'] else 0
    gates['g8'] = 0.02 <= vrate <= 0.08
    # G9 RSI14
    rsi = _rsi14(closes)
    gates['g9'] = (rsi is not None) and (40 <= rsi <= 80)

    hard_ok = all(gates[g] for g in HARD)
    soft_ok = sum(1 for g in SOFT if gates[g]) >= SOFT_NEED
    score = sum(1 for g in gates.values() if g)
    passed = hard_ok and soft_ok

    reason = ';'.join(k.upper() for k, v in gates.items() if v)
    return {'passed': passed, 'score': score, 'gates': gates, 'pct': round(pct * 100, 2),
            'volRatio': round(vol_ratio, 2), 'rsi': round(rsi, 1) if rsi else None,
            'reason': reason or '无'}


# ---------------- 自检 ----------------
def _mk_power_bars():
    """合成一只强势股：温和上行（含涨跌互现使 RSI 落在 40-80）+ 放量长阳收盘（过全部 9 门）。"""
    bars = []
    p = 50.0
    for i in range(40):
        chg = 0.012 if i % 2 == 0 else -0.009  # 净上行 + 含回调 → RSI 适中
        o = p
        c = p * (1 + chg)
        hi = max(o, c) * 1.015
        lo = min(o, c) * 0.985
        bars.append({'day': '', 'open': o, 'close': c, 'high': hi, 'low': lo, 'volume': 1_000_000})
        p = c
    # 末根长阳（涨幅≥3% + 放量）
    prev_c = bars[-1]['close']
    nl = {'day': '', 'open': prev_c, 'close': prev_c * 1.035,
          'high': prev_c * 1.046, 'low': prev_c * 0.999, 'volume': 4_800_000}
    bars.append(nl)
    return bars


def self_test():
    bars = _mk_power_bars()
    r = screen_one(bars, '测试股')
    print(f"[SELF-TEST] power passed={r['passed']} score={r['score']} "
          f"pct={r['pct']}% volRatio={r['volRatio']} rsi={r['rsi']}")
    print(f"[SELF-TEST] gates={r['gates']}")
    ok = r['passed'] and r['score'] == 9
    print(f"[SELF-TEST] {'OK' if ok else 'FAIL'} (期望 全过 9 门)")
    return ok


# ---------------- 主流程 ----------------
def main():
    args = sys.argv[1:]
    if '--self-test' in args:
        sys.exit(0 if self_test() else 1)
    # R100z27：本脚本没用 argparse，`--help` 会直接跑一遍 651 只全扫描（慢且易被误当成语法检查）。
    # 显式早退，与其它脚本的 --help 行为对齐（避免「查一下用法」触发一次生产扫描）。
    if '--help' in args or '-h' in args:
        print('用法：python3 power_screener.py [--dry-run] [--self-test] [--write]')
        print('  --dry-run   只打印扫描结果，不写回 data.js（默认即 dry）')
        print('  --self-test 合成一只强势股自检 9 道门是否都能判过')
        print('  --write     写回 data.js 的 D.powerScreen')
        return

    import common
    # 注意：默认**写回**（16:00 流水线直接调本脚本无参数即落盘），只有 --dry-run 不写。
    dry = '--dry-run' in args

    D = common.load_dashboard_data(BASE)
    names = K.collect_pool_names(D)  # R100g：池内 code→name（stkKlineNames 缺名兜底）
    sec_map = K.collect_sectors(D)   # R100q：code→行业（弹窗板块徽章全覆盖，新浪兜底）
    codes = K.collect_pool_codes(D)
    # 扩展扫描域：断板池 code + stkKlines 内 60/00 标的
    for pool in ('confirmed', 'watching'):
        for e in (D.get('duanban') or {}).get(pool) or []:
            c = str(e.get('code') or '')
            if c.startswith(('60', '00')):
                codes.append(c)
    sk = D.get('stkKlines') or {}
    for c in sk:
        if c.startswith(('60', '00')):
            codes.append(c)
    codes = sorted(set(codes))
    print(f"[POWER] 扫描域 {len(codes)} 只（优先内嵌 stkKlines）")

    # 交叉徽标
    htags = set((D.get('harmonic') or {}).get('tags', {}).keys())
    ascodes = set(str(it.get('code') or '') for it in (D.get('accumulation') or {}).get('scored', []) or [])

    passed = []
    for c in codes:
        bars = K.get_bars(c, D)
        if not bars or len(bars) < 25:
            continue
        nm = names.get(c) or (D.get('stkKlineNames') or {}).get(c) or ''
        r = screen_one(bars, nm)
        if not r['passed']:
            continue
        item = {'code': c, 'name': nm, 'sector': sec_map.get(c, ''), 'score': r['score'], 'pct': r['pct'],
                'volRatio': r['volRatio'], 'rsi': r['rsi'], 'lastClose': round(bars[-1]['close'], 2),
                'reasons': r['reason'], 'story': '', 'deduce': None}
        if c in htags:
            item['harmonic'] = (D['harmonic']['tags'] or {}).get(c)
        if c in ascodes:
            item['acc'] = True
        passed.append(item)
    passed.sort(key=lambda x: (-x['score'], -x['pct']))
    today = datetime.date.today().strftime('%Y-%m-%d')

    new_field = {
        'updatedAt': f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}（量化筛选 {today} 收盘 数据已自动更新）",
        'tradeDate': today,
        'passed': passed[:30],
        'summary': '',
    }
    old = D.get('powerScreen')
    # R100z44：入池首日标记——新入池标的 entryDate=本数据交易日，老标的沿用首次入池日
    # （从旧 data.js 按 code 继承；离池后再进视为新入池）。前端据此首日黄色高亮+「新」徽章。
    _old_ed = {str(i.get('code') or ''): i.get('entryDate')
               for i in (old or {}).get('passed') or [] if isinstance(i, dict)}
    for i in new_field.get('passed') or []:
        i['entryDate'] = _old_ed.get(str(i.get('code') or '')) or today
    if not passed and old:
        print('[POWER] 本轮无达标标的，保留既有池（R91n 不清场）')
        old['note'] = (old.get('note') or '') + f"｜{today} 本轮无新命中"
        new_field = old
    elif not passed and not old:
        print('[POWER] 无达标且无旧值，写空结构')
    D['powerScreen'] = new_field

    if dry:
        print(f"[POWER][DRY] 达标 {len(passed)} 只，未写回")
        print(json.dumps([(i['code'], i['name'], i['score'], i['pct']) for i in passed[:10]],
                         ensure_ascii=False))
        return

    out = 'window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False,
                                                  separators=(',', ':')) + ';\n'
    with open(os.path.join(BASE, 'data.js'), 'w', encoding='utf-8') as f:
        f.write(out)
    print(f"[POWER] 写回 data.js：达标 {len(passed)} 只")


if __name__ == '__main__':
    main()

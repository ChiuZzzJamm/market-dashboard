#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R100z52：止损「只算不跟踪」的补丁 —— 四模块都在算止损位，但从来没人回过头看
「这个止损到底有没有被触及、触及之后是继续跌还是反手被洗出去」。

算出来不执行的止损是纸面纪律：本脚本把每天各模块给出的止损位登记进台账
（stop_ledger.json），之后每个交易日回扫 K 线，统计两件事：

  1. **止损失命中率** = 入场（= 登记日收盘）之后是否有一天「最低价 ≤ 止损位」；
  2. **触及后 5 日表现** = 被触及的止损，是止损有效（继续下跌）还是被洗盘（反弹）。

第 2 项比第 1 项更有价值：止损失命中率高不等于止损纪律好，如果每次触及之后
五天内都又涨回去，说明止损位设太紧、在被反复洗盘，反而该放宽或改用收盘价确认。

止损来源（优先级从高到低，先到先得，冲突不覆盖）：
  - harmonic.confirmPool / watchPool 的几何 stop（形态位，最可信）；
  - aiPrediction.sectors[].stocks[].exit.stop（08:30 任务给自选股写的止损，
    口径为「最近收盘 −3%」）；
  - 机械池 duanban.confirmed/watching、powerScreen.passed、accumulation.scored
    **本身没有止损字段** → 按 DEFAULT_STOP_PCT（最近收盘 −3%，与 08:30 对自选股的
    口径一致）补齐，并在台账 stopSrc 标成 default-3% 以便回看时区分「真形态位」与「兜底值」。

用法：
  python3 stop_tracker.py              # 登记当日 + 回算已完成跟踪 + 写台账
  python3 stop_tracker.py --view       # 只打印统计，不登记
  python3 stop_tracker.py --write-data # 额外把统计写进 data.js 顶层 stopTrack（前端展示用）
  python3 stop_tracker.py --days 10    # 回算最近 N 个交易日（默认全部）

口径铁律：
  - 台账只存脚本算得出的数，**绝不补 0 冒充未触及**；取不到 K 线就如实标 noKline。
  - 幂等：同一交易日重复跑不会重复登记；--recalc 可整体重算（台账存了 stop 与入场价）。
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kline_cache as K  # noqa: E402  (只取网络数，不走 K 线缓存，避免污染 320 根缓存)

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(ROOT, 'data.js')
LEDGER = os.path.join(ROOT, 'stop_ledger.json')

DEFAULT_STOP_PCT = 0.03   # 机械池兜底止损：最近收盘 −3%（与 08:30 对自选股口径一致）
LOOKAHEAD = 5             # 「触及后 5 日表现」的窗口（交易日）
SRC_PRIORITY = ('harmonic', 'ai-exit', 'default')

NODE_BIN = ('/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node'
            if os.path.exists('/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node')
            else 'node')

NODE_SRC = r"""
const fs = require('fs');
const p = process.env.DATA_PATH || 'data.js';
let s = fs.readFileSync(p, 'utf8');
const m = s.match(/window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!m) { console.error('data.js parse failed'); process.exit(1); }
const d = eval('(' + m[1] + ')');
process.stdout.write(JSON.stringify({
  date: (d.aiPrediction || {}).date || null,
  harmonic: { confirmPool: (d.harmonic || {}).confirmPool || [], watchPool: (d.harmonic || {}).watchPool || [] },
  aiStocks: ((d.aiPrediction || {}).sectors || []).map(function (s) {
    return { sector: s.sector, stocks: (s.stocks || []).map(function (t) {
      return { code: t.code, name: t.name, lastClose: t.lastClose, exit: t.exit || null }; }) };
  }),
  duanban: { confirmed: (d.duanban || {}).confirmed || [], watching: (d.duanban || {}).watching || [] },
  power: (d.powerScreen || {}).passed || [],
  accum: (d.accumulation || {}).scored || []
}));
"""


def load_data_js(path=DATA_JS):
    import subprocess
    pr = subprocess.run([NODE_BIN, '-e', NODE_SRC],
                        env=dict(os.environ, DATA_PATH=path),
                        capture_output=True, text=True, cwd=ROOT)
    if pr.returncode != 0:
        print('[ERR] 读取 data.js 失败：' + (pr.stderr or '').strip()[:200], file=sys.stderr)
        return None
    try:
        return json.loads(pr.stdout)
    except Exception as e:
        print('[ERR] 解析失败：' + str(e), file=sys.stderr)
        return None


def _f(x):
    return float(x) if isinstance(x, (int, float)) else None


def collect_entries(D):
    """按优先级从四个模块抽 (code, name, sector, stop, stopSrc)。"""
    out = {}
    hz = (D.get('harmonic') or {})
    for pool in ('confirmPool', 'watchPool'):
        for x in hz.get(pool) or []:
            st = _f(x.get('stop'))
            if st and x.get('code'):
                out[str(x['code'])] = {'code': str(x['code']), 'name': x.get('name') or '',
                                       'sector': x.get('sector') or '',
                                       'stop': st, 'stopSrc': 'harmonic',
                                       'lastClose': _f(x.get('lastClose'))}
    for s in D.get('aiStocks') or []:
        for t in s.get('stocks') or []:
            ex = t.get('exit') or {}
            st = _f(ex.get('stop'))
            c = str(t.get('code') or '')
            if not st or not c or c in out:
                continue
            out[c] = {'code': c, 'name': t.get('name') or '', 'sector': s.get('sector') or '',
                      'stop': st, 'stopSrc': 'ai-exit', 'lastClose': _f(t.get('lastClose'))}
    # 机械池：一律兜底 −3%，并标注来源，避免把「兜底值」当成「形态位」去评价
    plain = []
    for x in (D.get('duanban') or {}).get('confirmed') or []:
        plain.append((x, 'duanban'))
    for x in (D.get('duanban') or {}).get('watching') or []:
        plain.append((x, 'duanban'))
    for x in D.get('power') or []:
        plain.append((x, 'powerScreen'))
    for x in D.get('accum') or []:
        plain.append((x, 'accumulation'))
    for x, src in plain:
        c = str(x.get('code') or '')
        lc = _f(x.get('lastClose'))
        if not c or c in out or lc is None:
            continue
        out[c] = {'code': c, 'name': x.get('name') or '', 'sector': x.get('sector') or '',
                  'stop': round(lc * (1 - DEFAULT_STOP_PCT), 2),
                  'stopSrc': 'default-3%', 'lastClose': lc, 'from': src}
    vals = list(out.values())
    vals.sort(key=lambda v: (SRC_PRIORITY.index(v['stopSrc'])
                             if v['stopSrc'] in SRC_PRIORITY else 99, v['code']))
    return vals


def _fetch(code):
    bars = K.fetch_kline_tencent(code, days=40, retries=1)
    if bars:
        return bars
    return K.fetch_kline_sina(code, days=40, retries=1)


def _pct(a, b):
    """(b−a)/a 百分比；b 缺失返回 None（绝不补 0）。"""
    if a is None or b is None or a == 0:
        return None
    return round((b - a) / a * 100, 2)


def track_one(entry, bars, ref_day=None):
    """对单条台账回算触及情况。返回新字段（不改动原条目）。

    ref_day 是**登记日**（台账日期，YYYYMMDD）。定位入场 K 线**优先按日期**——
    第一版按 `close == lastClose` 匹配，实测 41 条只匹配上 7 条（复权口径、
    lastClose 取整、停牌股都对不上），等于 80% 的样本直接丢掉没统计。
    日期定位不到才退回收盘价近似匹配。
    """
    stop = entry.get('stop')
    lc = entry.get('lastClose')
    if not bars or not stop or not lc:
        return {'noKline': not bars, 'touched': None}
    # 起点：入场（登记）日那根之后的 K 线；登记日当天没抓到就整条跳过
    idx = None
    if ref_day and len(ref_day) == 8:
        rd = f'{ref_day[:4]}-{ref_day[4:6]}-{ref_day[6:]}'
        for i, b in enumerate(bars):
            if b.get('day') == rd:
                idx = i
                break
    if idx is None:
        for i, b in enumerate(bars):
            if _f(b.get('close')) == lc:
                idx = i
                break
    if idx is None:
        # 用「收盘价最接近」的那根当入场日（腾讯/新浪 复权口径可能与 data.js 略有差）
        best, bd = None, None
        for i, b in enumerate(bars):
            d = abs((_f(b.get('close')) or 0) - lc)
            if bd is None or d < bd:
                best, bd = i, d
        if best is not None and bd is not None and bd <= max(lc * 0.005, 0.01):
            idx = best
    if idx is None:
        return {'noKline': True, 'touched': None}
    after = bars[idx + 1:]
    if not after:
        return {'noKline': False, 'touched': None, 'pending': True}
    hi = None
    for j, b in enumerate(after):
        if _f(b.get('low')) is not None and _f(b.get('low')) <= stop:
            hi = j
            break
    def _close_n(j):
        """after 里第 j 根（j 从 0 起）的收盘；不够就 None（绝不补 0）。"""
        if 0 <= j < len(after):
            return _f(after[j].get('close'))
        return None

    # 「锚点日之后第 N 根」= after[anchor + N]；入场日本身算 after[-1]，
    # 所以「入场后第 5 个交易日」= after[LOOKAHEAD-1]（写成 after[LOOKAHEAD] 会多算一根）。

    res = {'noKline': False, 'pending': False, 'touched': hi is not None}
    if hi is not None:
        t = after[hi]
        res['hitDay'] = t.get('day')
        res['hitLow'] = _f(t.get('low'))
        res['daysToHit'] = hi + 1
        c5 = _close_n(hi + LOOKAHEAD)          # 触及日之后第 5 根
        res['after5Pct'] = _pct(lc, c5)
        res['rebound5Pct'] = _pct(_f(t.get('low')), c5)
        res['maxDD5Pct'] = _pct(lc, min((_f(b.get('low')) or lc) for b in after[hi:hi + LOOKAHEAD + 1]))
    # 入场后第 5 个交易日的表现（不管有没有触及都记一份，看这批票的短期均值）
    res['p5Pct'] = _pct(lc, _close_n(LOOKAHEAD - 1))
    return res


def summarize(entries):
    """按台账entries算统计。entries 为已完成回算的条目列表。"""
    done = [e for e in entries if not e.get('noKline') and not e.get('pending')]
    touched = [e for e in done if e.get('touched')]
    p5 = [e['p5Pct'] for e in done if e.get('p5Pct') is not None]
    a5 = [e['after5Pct'] for e in touched if e.get('after5Pct') is not None]
    r5 = [e['rebound5Pct'] for e in touched if e.get('rebound5Pct') is not None]
    return {
        'total': len(done),
        'touched': len(touched),
        'touchRate': round(len(touched) / len(done), 3) if done else None,
        'avgP5Pct': round(sum(p5) / len(p5), 2) if p5 else None,
        'avgAfter5Pct': round(sum(a5) / len(a5), 2) if a5 else None,
        'avgRebound5Pct': round(sum(r5) / len(r5), 2) if r5 else None,
        'hitThenUp': len([x for x in a5 if x > 0]) if a5 else None,
        'hitThenUpRate': (round(len([x for x in a5 if x > 0]) / len(a5), 3)
                          if a5 else None),
        'bySrc': _by_src(done),
        'bySrcTouch': _by_src_touch(done),
    }


def _by_src(done):
    g = {}
    for e in done:
        g.setdefault(e.get('stopSrc') or 'unknown', []).append(e)
    return {k: {'n': len(v), 'p5': round(sum([x['p5Pct'] for x in v if x.get('p5Pct') is not None]) /
                                         len([x for x in v if x.get('p5Pct') is not None]), 2)
                if any(x.get('p5Pct') is not None for x in v) else None}
                for k, v in g.items()}


def _by_src_touch(done):
    g = {}
    for e in done:
        g.setdefault(e.get('stopSrc') or 'unknown', []).append(e)
    out = {}
    for k, v in g.items():
        t = [x for x in v if x.get('touched')]
        out[k] = {'n': len(v), 'touched': len(t),
                  'rate': round(len(t) / len(v), 3) if v else None}
    return out


def self_test():
    """合成 K 线自检：入场 100、止损 97（−3%）；第 2 根最低 98.5（未触及），
    第 3 根最低 94（**触及**）；之后第 8 根收盘 104。
    期望：touched=True / hitDay=2026-10-09 / daysToHit=3
          after5Pct=(104−100)/100=+4.0   p5Pct=(第8根? 见下)=+2.0
          maxDD5Pct=(94−100)/100=−6.0
    """
    def b(d, o, c, h, l):
        return {'day': d, 'open': o, 'close': c, 'high': h, 'low': l, 'volume': 1.0}
    bars = [
        b('2026-09-30', 100, 100, 101, 99),   # 入场日（ref_day，不计入 after）
        b('2026-10-08', 100, 99, 100, 98.5),  # 入场后第 1 个交易日（+1）未触及（98.5 > 97）
        b('2026-10-09', 97, 96, 97, 94),      # +2 触及（low 94 < 97）
        b('2026-10-12', 95, 96, 96.5, 94.5),  # +3
        b('2026-10-13', 96, 98, 98.5, 95),    # +4
        b('2026-10-14', 98, 99, 99.5, 97),    # +5 收盘 99  ← p5Pct 取这根
        b('2026-10-15', 99, 101, 101.5, 99),  # +6
        b('2026-10-16', 101, 104, 105, 100),  # +7 收盘 104  ← 触及后再 5 根，after5Pct 取这根
    ]
    ent = {'code': '000001', 'name': '自检', 'lastClose': 100.0, 'stop': 97.0, 'stopSrc': 'harmonic'}
    r = track_one(ent, bars, '2026-09-30')
    # 注意三者口径差异：`after` 不含入场日，所以
    #   daysToHit = hi+1 = 「入场后第几个交易日」→ 本例发生在 +2（2026-10-09）
    #   p5Pct      = after[5] 收盘（= 入场后第 6 个根，2026-10-14 = 99）→ −1.0%
    #   after5Pct  = 触及日之后再 5 根（2026-10-16 = 104）→ +4.0%
    checks = [
        ('touched=True', r.get('touched') is True),
        ('hitDay=2026-10-09', r.get('hitDay') == '2026-10-09'),
        ('daysToHit=2（入场后第 2 个交易日）', r.get('daysToHit') == 2),
        ('after5Pct=+4.0（触及后再 5 根 104）', r.get('after5Pct') == 4.0),
        ('p5Pct=-1.0（入场后第 5 根 99）', r.get('p5Pct') == -1.0),
        ('maxDD5Pct=-6.0（触及日窗口最低 94）', r.get('maxDD5Pct') == -6.0),
        ('rebound5Pct 非空', r.get('rebound5Pct') is not None),
    ]
    ok = True
    for name, got in checks:
        print(('  OK   ' if got else '  FAIL ') + name)
        ok = ok and got
    # 反例：最低价仍在止损之上 → 不得误判触及（97.2 > 97）
    bars2 = [dict(bars[0]), {'day': '2026-10-08', 'open': 100, 'close': 99, 'high': 100, 'low': 97.5, 'volume': 1},
             {'day': '2026-10-09', 'open': 100, 'close': 101, 'high': 102, 'low': 97.2, 'volume': 1}]
    r2 = track_one(ent, bars2, '2026-09-30')
    print(('  OK   ' if r2.get('touched') is False else '  FAIL ') + '止损之上不误判触及')
    ok = ok and r2.get('touched') is False
    print('[SELF-TEST] ' + ('OK' if ok else 'FAIL'))
    return 0 if ok else 1


def load_ledger():
    if os.path.exists(LEDGER):
        try:
            return json.load(open(LEDGER, encoding='utf-8'))
        except Exception:
            pass
    return {}


def save_ledger(L):
    json.dump(L, open(LEDGER, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--view', action='store_true', help='只打印统计，不登记当日')
    ap.add_argument('--write-data', action='store_true',
                    help='把统计写进 data.js 顶层 stopTrack 键（前端展示用，会改 data.js）')
    ap.add_argument('--days', type=int, default=0, help='只回算最近 N 个交易日，0=全部')
    ap.add_argument('--recalc', action='store_true',
                    help='丢弃已有回算结果整体重跑（口径变更后用，幂等）')
    ap.add_argument('--self-test', action='store_true', help='跑合成 K 线自检后退出')
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    L = load_ledger()
    ledger = L.setdefault('entries', {})
    if a.recalc:
        for _dt, items in ledger.items():
            for e in items:
                e.pop('res', None)
        print('[OK] 已丢弃全部回算结果，准备按当前口径重跑')
    D = load_data_js()
    if D is None:
        return 1

    if not a.view:
        today = D.get('date')
        if not today:
            print('[SKIP] data.js 无 aiPrediction.date，无法确定登记日', file=sys.stderr)
            return 0
        items = collect_entries(D)
        if not items:
            print('[info] 今日无任何带止损位的标的，不登记（台账保持原样）')
        else:
            cur = ledger.get(today) or []
            known = {e['code'] for e in cur}
            new = [x for x in items if x['code'] not in known]
            if not cur:
                cur = items
                print(f'[OK] 登记 {today}：{len(cur)} 条止损位')
            else:
                cur = cur + new
                print(f'[OK] 登记 {today}：已有 {len(cur) - len(new)} 条，新增 {len(new)} 条'
                      f'（同码不覆盖，以首登记口径为准）')
            for e in cur:
                e['lastClose'] = _f(e.get('lastClose'))
            ledger[today] = cur

    # ---- 回算：对所有已登记的日期扫 K 线 ----
    dates = sorted(ledger.keys())
    if a.days:
        dates = dates[-a.days:]
    if not dates:
        print('[info] 台账为空，无可执行跟踪')
        return 0

    todo = {}
    for dt in dates:
        for e in ledger[dt]:
            if e.get('res') is None:
                todo.setdefault(e['code'], e)

    if todo:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(_fetch, c): c for c in todo}
            bars_map = {}
            for f in as_completed(futs):
                bars_map[futs[f]] = f.result()
        for c, e in todo.items():
            e['_bars'] = bars_map.get(c)

    for dt in dates:
        changed = False
        for e in ledger[dt]:
            if e.get('res', {}).get('_done'):
                continue
            res = {k: v for k, v in track_one(e, e.pop('_bars', None), dt).items()}
            res['_done'] = True
            e['res'] = res
            changed = True
        if changed and not a.view:
            print(f'[OK] 回算 {dt} 的触及情况')

    if not a.view:
        save_ledger(L)

    all_done = [e for dt in sorted(ledger.keys()) for e in ledger[dt]
                if e.get('res', {}).get('noKline') is False and not e.get('res', {}).get('pending')]
    all_pending = [e for dt in sorted(ledger.keys()) for e in ledger[dt]
                   if e.get('res', {}).get('pending')]
    all_nok = [e for dt in sorted(ledger.keys()) for e in ledger[dt] if e.get('res', {}).get('noKline')]
    st = summarize(all_done)
    st['pending'] = len(all_pending)
    st['noKline'] = len(all_nok)
    if not a.view or a.write_data:
        L['stats'] = st
        L['updatedAt'] = _now()
        save_ledger(L)
    if a.write_data:
        write_stop_track_to_datajs(st)
    _print(st, ledger)
    return 0


def _now():
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def write_stop_track_to_datajs(st):
    """把统计写进 data.js 的 `stopTrack` 键。

    ⚠️ 不能像第一版那样在文件末尾追加 `window.STOP_TRACK = {...};` 语句：
    全仓库解析 data.js 的正则形如 `/DASHBOARD_DATA\\s*=\\s*(\\{[\\s\\S]*\\});?\\s*$/`，
    语句一旦在末尾，捕获会一路吞到文件尾，把 Stop 语句一起当 JSON 解析 →
    实测 audit 从 PASS=24 掉到 PASS=20 + 4 个 WARN（validate_stocks 全 scope 解析失败）。
    所以这里必须**插进对象内部**、且保持文件仍以 `}` 收尾，并写成**扁平对象**
    （嵌套的 `}` 会让「找 value 结尾」的索引切错）。
    """
    payload = {
        'updatedAt': _now(),
        'total': st.get('total'), 'touched': st.get('touched'),
        'touchRate': st.get('touchRate'),
        'avgP5Pct': st.get('avgP5Pct'), 'avgAfter5Pct': st.get('avgAfter5Pct'),
        'avgRebound5Pct': st.get('avgRebound5Pct'), 'hitThenUpRate': st.get('hitThenUpRate'),
    }
    obj = json.dumps(payload, ensure_ascii=False)
    s = open(DATA_JS, encoding='utf-8').read()
    i = s.rindex('}')                       # 文件末尾那个 } 就是 DASHBOARD_DATA 的收尾
    head, tail = s[:i], s[i:]
    m = head.rfind('"stopTrack"')
    if m >= 0:
        b = head.index(':', m) + 1
        e = head.index('}', b) + 1          # 载荷是扁平对象，第一个 } 即结尾
        head = head[:m] + '"stopTrack": ' + obj + head[e:]
    else:
        head = head + ', "stopTrack": ' + obj
    open(DATA_JS, 'w', encoding='utf-8').write(head + tail)
    print('[OK] 统计已写入 data.js 的 DASHBOARD_DATA.stopTrack（扁平对象，文件仍以 } 收尾）')


def _print(st, ledger):
    print('\n=== 止损跟踪台账（止损失命中率 + 触及后 5 日表现）===')
    rate = ('—' if st.get('touchRate') is None else f'{st["touchRate"] * 100:.0f}%')
    print(f"  样本（已完成跟踪）：{st.get('total')} 条"
          f"｜止损失命中 {st.get('touched')} 条 → 命中率 {rate}")
    print(f"  入场后 5 日平均：{st.get('avgP5Pct') if st.get('avgP5Pct') is not None else '—'}%"
          f"｜触及后 5 日平均（相对入场价）：{st.get('avgAfter5Pct') if st.get('avgAfter5Pct') is not None else '—'}%"
          f"｜触及后 5 日相对最低点反弹：{st.get('avgRebound5Pct') if st.get('avgRebound5Pct') is not None else '—'}%")
    if st.get('hitThenUpRate') is not None:
        print(f"  触及后 5 日又涨回去的比例：{st['hitThenUpRate'] * 100:.0f}%"
              f"（越高说明止损位设越容易被洗盘，该考虑放宽或改收盘价确认）")
    if st.get('bySrcTouch'):
        print('  分组（src: 样本/触及/触及率）：')
        for k, v in st['bySrcTouch'].items():
            print(f"    {k}: {v['n']} / {v['touched']} / "
                  f"{(v['rate'] * 100 if v['rate'] is not None else '—')}%")
    for dt in sorted(ledger.keys()):
        n = len(ledger[dt])
        t = len([e for e in ledger[dt] if e.get('res', {}).get('touched')])
        p = len([e for e in ledger[dt] if e.get('res', {}).get('pending')])
        print(f"  [{dt}] 登记 {n} 条，止损失命中 {t} 条，待跟踪 {p} 条")
    print(f"  待跟踪 {st.get('pending')} 条 = 入场日之后还没出下一根 K 线，"
          f"下一个交易日跑一次即可判定；K 线缺失 {st.get('noKline')} 条（如实不计入统计）")


if __name__ == '__main__':
    sys.exit(main())

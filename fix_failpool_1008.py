#!/usr/bin/env python3
"""R100z92 失效池修复：补回 2026-10-08 被异常清空的 吸筹/九门 failPool。

背景（证据链，均可复现）：
- git 11e524b（10-08 09:53，16:00 链路前最后一次落盘）：accumulation.scored = 节前(09-30) 20 只
- git 866bf30（10-08 17:52，链路后）：scored 换成当日 20 只，failPool=[]（键存在=走了正常分支）
- 按 accumulation_score.py:444-458 / power_screener.py:252-265 的同款判据，
  节前池掉出的标的**必须**入失效池；实际为 0 → 链路中段被旧快照覆盖丢失。
- 本脚本按两个生成脚本的归因口径离线重建失效条目并补回（幂等：已在池内的 code 跳过）。

归因口径（与生成脚本逐字一致）：
- 吸筹（accumulation_score.py _acc_fail_reason）：
  ① 破位止损：最新收盘 < 入池日(2026-09-30)收盘 ×0.975
  ② 信号走旧：上一轮 lagDays ≥ 25（POOL_MAX_LAG）
  ③ 兜底：本轮评分低于 50 分线（MIN_SCORE）
- 九门（power_screener.py）：「本轮未过九门阈值（上一轮 X/9 门，阈值不再满足）」
- entryDate=2026-10-08（失效日=发现日，与生成脚本口径一致）

用法：python3 fix_failpool_1008.py [--dry]
"""
import json
import re
import subprocess
import sys

ROOT = '/Users/loccco/WorkBuddy/market dashboard'
PREV_REF = '11e524b'          # 10-08 09:53 = 16:00 链路前最后落盘（节前池）
FAIL_DATE = '2026-10-08'      # 失效日=发现日
POOL_MAX_LAG = 25             # accumulation_score.py POOL_MAX_LAG
MIN_SCORE = 50                # accumulation_score.py MIN_SCORE
BREAK_RATIO = 0.975           # 吸筹纪律 −2.5%


def load_dash(text):
    m = re.search(r'window\.DASHBOARD_DATA = (\{.*\});?\s*$', text, re.S)
    return json.loads(m.group(1))


def kline_rows(cur, code):
    """内嵌 K 线 → [(day, close)] 升序；键格式自动适配（600000/sh600000/sz000050）。"""
    kl = cur.get('stkKlines') or {}
    for key in (code, 'sh' + code, 'sz' + code):
        raw = kl.get(key)
        if not raw:
            continue
        rows = raw if isinstance(raw, list) else raw.get('rows') or raw.get('data') or []
        out = []
        for b in rows:
            if isinstance(b, dict) and b.get('day'):
                out.append((str(b['day'])[:10], float(b.get('close') or 0)))
            elif isinstance(b, (list, tuple)) and len(b) >= 3:
                try:
                    out.append((str(b[0])[:10], float(b[2])))
                except (TypeError, ValueError):
                    continue
        if out:
            return out
    return []


def acc_reason(code, oi, rows):
    """与 accumulation_score.py:_acc_fail_reason 同款三证据归因。"""
    entry_close = None
    for day, close in rows:
        if day == '2026-09-30':          # 入池日=节前交易日（节前池 entryDate 缺失，用池 tradeDate）
            entry_close = close
            break
    last_close = rows[-1][1] if rows else None
    if entry_close and last_close and last_close < entry_close * BREAK_RATIO:
        return ('破位止损（2026-09-30 入池收盘 %s → 最新 %s，跌破 −2.5%%）'
                % (('%g' % entry_close), ('%g' % last_close)))
    lag = oi.get('lagDays')
    if lag is not None and int(lag) >= POOL_MAX_LAG:
        return '信号走旧（滞后 %d 日 ≥%d 日线，本轮不入池）' % (int(lag), POOL_MAX_LAG)
    return '本轮评分低于 %d 分线（上一轮 %s 分），形态走坏' % (MIN_SCORE, oi.get('score'))


def main():
    dry = '--dry' in sys.argv
    prev_text = subprocess.run(['git', '-C', ROOT, 'show', PREV_REF + ':data.js'],
                               capture_output=True, check=True).stdout.decode('utf-8')
    prev = load_dash(prev_text)
    with open(ROOT + '/data.js', encoding='utf-8') as f:
        cur = load_dash(f.read())

    changed = []

    # ---------- 吸筹 failPool ----------
    old_scored = {str(i.get('code')): i for i in (prev.get('accumulation') or {}).get('scored') or []
                  if isinstance(i, dict)}
    new_acc_codes = {str(i.get('code')) for i in (cur.get('accumulation') or {}).get('scored') or []
                     if isinstance(i, dict)}
    acc = cur.setdefault('accumulation', {})
    pool = {str(f.get('code')): f for f in acc.get('failPool') or []}
    added_acc = 0
    for code, oi in old_scored.items():
        if code in new_acc_codes or code in pool:
            continue
        rows = kline_rows(cur, code)
        pool[code] = {'code': code,
                      'name': oi.get('name') or '',
                      'sector': oi.get('sector') or '',
                      'score': oi.get('score'),
                      'grade': oi.get('grade') or '',
                      'lagDays': oi.get('lagDays'),
                      'reason': acc_reason(code, oi, rows),
                      'entryDate': FAIL_DATE}
        added_acc += 1
    acc['failPool'] = list(pool.values())
    changed.append('吸筹失效池 +%d（共 %d）' % (added_acc, len(acc['failPool'])))

    # ---------- 九门 failPool ----------
    old_pw = {str(i.get('code')): i for i in (prev.get('powerScreen') or {}).get('passed') or []
              if isinstance(i, dict)}
    new_pw_codes = {str(i.get('code')) for i in (cur.get('powerScreen') or {}).get('passed') or []
                    if isinstance(i, dict)}
    pw = cur.setdefault('powerScreen', {})
    pool_pw = {str(f.get('code')): f for f in pw.get('failPool') or []}
    added_pw = 0
    for code, oi in old_pw.items():
        if code in new_pw_codes or code in pool_pw:
            continue
        pool_pw[code] = {'code': code,
                         'name': oi.get('name') or '',
                         'sector': oi.get('sector') or '',
                         'score': oi.get('score'),
                         'reason': '本轮未过九门阈值（上一轮 %s/9 门，阈值不再满足）' % oi.get('score'),
                         'entryDate': FAIL_DATE}
        added_pw += 1
    pw['failPool'] = list(pool_pw.values())
    changed.append('九门失效池 +%d（共 %d）' % (added_pw, len(pw['failPool'])))

    print('；'.join(changed))
    if dry or (added_acc == 0 and added_pw == 0):
        print('[DRY/无改动] 不写盘')
        return

    out = 'window.DASHBOARD_DATA = ' + json.dumps(cur, ensure_ascii=False,
                                                  separators=(',', ':')) + ';\n'
    with open(ROOT + '/data.js', 'w', encoding='utf-8') as f:
        f.write(out)
    print('已写回 data.js')


if __name__ == '__main__':
    main()

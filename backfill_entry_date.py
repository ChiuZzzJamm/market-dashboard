#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回填四模块池内标的的 entryDate（R100z56，一次性工具，可重复执行）。

背景（用户 2026-10-04 反馈「断板/九门/谐波/吸筹都没看到新入池的『新』徽标」）：
R100z44 就已给四模块写了「入池首日标记」——生成侧为条目写 entryDate、前端 poolFresh()
比对 entryDate 与本模块数据交易日，相等才挂「新」徽章。但代码落地时间晚于最后一次生成，
导致线上 data.js 里 accumulation.scored / powerScreen.passed / harmonic.confirm|watchPool /
duanban.confirmed|watching 的条目**一个 entryDate 都没有** → poolFresh() 恒 false
→ 徽章只在断板 failPool（那里由 check_duanban.py 写了 entryDate）出现过。

危害不止「看不见」：若直接放任到 10-08 首次生成，四模块会走 `or today` 兜底，
把存量标的全部误判成「新入池」，当天整屏黄卡刷「新」，徽章当场失去意义。

本脚本按「该模块数据自己的交易日」回填缺失的 entryDate：
  accumulation.scored      → accumulation.tradeDate
  powerScreen.passed       → powerScreen.tradeDate
  harmonic.confirm/watch   → harmonic.tradeDate
  duanban.confirmed/watch  → duanban.generatedAt（当天 10 位）
  .harmonic_state.json     → 谐波生成侧继承用的 prev_pools（同源同值，否则继承链又空一环）
**已存在 entryDate 的一律不动**（离池重进、脚本侧已修过的不覆盖）。

回填后：今天页面不会误挂「新」；10-08 首次生成时存量标的继承旧日期、只剩真新进的挂徽章。

用法：python3 backfill_entry_date.py [--dry-run]
"""
import json
import os
import sys

import common

BASE = os.path.dirname(os.path.abspath(__file__))

# (模块路径 getter, 条目取自哪个键, 日期取自哪个键) —— 见文件头说明
TARGETS = [
    ('accumulation', 'scored', 'tradeDate'),
    ('powerScreen', 'passed', 'tradeDate'),
    ('harmonic', 'confirmPool', 'tradeDate'),
    ('harmonic', 'watchPool', 'tradeDate'),
    ('duanban', 'confirmed', 'generatedAt'),
    ('duanban', 'watching', 'generatedAt'),
]

STATE_FILE = os.path.join(BASE, '.harmonic_state.json')


def main():
    dry = '--dry-run' in sys.argv
    D = common.load_dashboard_data(BASE)

    filled = 0
    for mod, key, date_key in TARGETS:
        m = D.get(mod) or {}
        date_v = str(m.get(date_key) or '')
        if not date_v:
            print(f"[SKIP] {mod}.{key}: 缺 {date_key}，不给假日期")
            continue
        day = date_v[:10]
        for it in (m.get(key) or []):
            if not isinstance(it, dict):
                continue
            if it.get('entryDate'):
                continue
            it['entryDate'] = day
            filled += 1
        print(f"[{'DRY' if dry else 'FIX'}] {mod}.{key} → entryDate={day}")

    # 谐波生成侧的继承链（.harmonic_state.json.pools）同源回填
    st = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding='utf-8') as f:
                st = json.load(f)
        except Exception as e:
            print(f"[WARN] 读 .harmonic_state.json 失败：{e}")
    hz_day = str((D.get('harmonic') or {}).get('tradeDate') or '').strip()
    st_n = 0
    if hz_day:
        for code, v in (st.get('pools') or {}).items():
            if isinstance(v, dict) and not v.get('entryDate'):
                v['entryDate'] = hz_day[:10]
                st_n += 1
    if st_n:
        print(f"[{'DRY' if dry else 'FIX'}] .harmonic_state.json.pools → entryDate={hz_day[:10]}（{st_n} 只）")

    print(f"[{'DRY' if dry else 'FIX'}] 合计回填池内条目 {filled} 处 + 状态 {st_n} 处")
    if dry or not filled:
        return

    out = 'window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';\n'
    with open(os.path.join(BASE, 'data.js'), 'w', encoding='utf-8') as f:
        f.write(out)
    if st_n:
        try:
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(st, f, ensure_ascii=False)
        except Exception as e:
            print(f"[WARN] 写 .harmonic_state.json 失败：{e}")
    print(f"[OK] data.js 已写回（+{filled} 处 entryDate）")


if __name__ == '__main__':
    main()

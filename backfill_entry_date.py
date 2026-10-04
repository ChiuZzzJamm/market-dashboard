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
**已存在且是合法交易日的 entryDate 一律不动**（离池重进、脚本侧已修过的不覆盖）。

⚠️ R100z57 修订（首版写坏过一次，已回滚重写）：首版直接把模块的生成日/模块 tradeDate 抄进
entryDate，于是 10-01（国庆假期）生成的 duanban 74 条、10-03（周六）生成的 harmonic 26 条
全部拿到一个**非交易日** entryDate；而前端判据是 entryDate === modDate（harmonic 的 modDate
正是那个 tradeDate）→ 上线当天整屏刷「新」徽章。
现在改为**统一归一到最近交易日**（common.last_trade_day），并且：
  1) 模块 tradeDate 不是交易日 → 纠正为最近交易日（harmonic/九门标题不再印「周六收盘」）；
  2) updatedAt 文案里嵌的假日期同步纠正（「谐波形态 2026-10-03 收盘」→ 2026-09-30）；
  3) 池内 entryDate 若落在非交易日 → 一并纠正；合法的不动。
归一化后：今天不挂「新」；10-08 首次生成时存量标的继承旧日期、只剩真新进的挂徽章。

用法：python3 backfill_entry_date.py [--dry-run]
"""
import json
import os
import re
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

    def _norm(s):
        """归一到最近交易日；空/异常值返回 ''（宁可填不进，也不编假日期）。"""
        s = str(s or '')[:10].strip()
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', s):
            return ''
        try:
            return common.last_trade_day(s)
        except Exception:
            return ''

    filled = 0
    for mod, key, date_key in TARGETS:
        m = D.get(mod) or {}
        date_v = str(m.get(date_key) or '')
        if not date_v:
            print(f"[SKIP] {mod}.{key}: 缺 {date_key}，不给假日期")
            continue
        raw = date_v[:10]
        # ⚠️ generatedAt 是「生成时刻」时间戳不是数据日，前端 duanban 的 modD 正是取它的日期
        #    （index.html: modD = duanban.generatedAt[:10]）。把 generatedAt 归一成数据日会让
        #    modD === entryDate → 断板池 74 条集体挂「新」。故 duanban 只取它的日期作 entryDate 来源，
        #    generatedAt 本身与 updatedAt 一律不动；只有真·数据字段 tradeDate 才允许被纠正。
        is_generated_at = (date_key == 'generatedAt')
        day = _norm(raw)
        if day and day != raw and not is_generated_at:
            print(f"[{'DRY' if dry else 'FIX'}] {mod}.{date_key}: {raw}（非交易日）→ {day}")
            if not dry:
                m[date_key] = day
                if isinstance(m.get('updatedAt'), str) and raw in m['updatedAt']:
                    m['updatedAt'] = m['updatedAt'].replace(raw, day)
        if not day:
            print(f"[SKIP] {mod}.{key}: 日期 {raw} 无法归一，不给假日期")
            continue
        for it in (m.get(key) or []):
            if not isinstance(it, dict):
                continue
            cur = str(it.get('entryDate') or '')
            # 只覆盖「缺失」或「明显是假交易日」两种；合法日期不动
            if cur and cur == day:
                continue
            if cur and _norm(cur) == cur:
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
    hz_day = _norm((D.get('harmonic') or {}).get('tradeDate'))
    st_n = 0
    if hz_day:
        for code, v in (st.get('pools') or {}).items():
            if not isinstance(v, dict):
                continue
            cur = str(v.get('entryDate') or '')
            if cur and cur == hz_day:
                continue
            if cur and _norm(cur) == cur:
                continue  # 已是合法交易日，别覆盖
            v['entryDate'] = hz_day
            st_n += 1
    if st_n:
        print(f"[{'DRY' if dry else 'FIX'}] .harmonic_state.json.pools → entryDate={hz_day[:10]}（{st_n} 只）")

    print(f"[{'DRY' if dry else 'FIX'}] 合计回填池内条目 {filled} 处 + 状态 {st_n} 处")
    if dry:
        return

    # ---------- 阶段二（R100z57）：清掉 data.js 池内回填的 entryDate ----------
    # 为什么清：data.js 的 entryDate 是**展示用**的「今日新入池」判据（前端 poolFresh 要求
    # entryDate === 模块交易日）。上面把整池 entryDate 填成模块日，等于宣布「全池今天入池」，
    # 上线当天整屏刷「新」，徽章当场贬值。
    # 真实记忆不该放在展示数据里：生成侧要么从 state 文件继承（harmonic/duanban），
    # 要么认「上一轮池成员」（accumulation/power_screener 的 R100z57 三分支）。
    # 清掉后：今天一律不挂「新」；10-08 首次生成时只有真·新进的会被打上当日日期。
    cleared = 0
    if '--clear' in sys.argv:
        for mod, key, _dk in TARGETS:
            m = D.get(mod) or {}
            for it in (m.get(key) or []):
                if isinstance(it, dict) and it.pop('entryDate', None):
                    cleared += 1
        print(f"[CLEAR] data.js 池内 entryDate 已清除 {cleared} 处")
    else:
        print("[CLEAR] 跳过（未加 --clear）")

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

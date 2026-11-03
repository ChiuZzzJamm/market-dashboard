#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 signal_ledger.json 里「汇总源」的条目按当日池归属拆成子源（一次性迁移，幂等）。

## 为什么需要这一步

signal_attribution.py 从 R100z297 起把三个汇总源拆成子池登记：

    duanban → duanban_confirmed / duanban_watching
    chen    → chen_strict / chen_loose
    buy     → buy_duanban / buy_chen / buy_tech / buy_other

但**改版前的历史条目只存了汇总键**、每条没有池归属，所以新子行会从 0 开始——
用户会看到「断板反包 196 条」下面挂两个「0 条」的子行，比不拆更差。

历史条目的 `code` 是有的，而每天的 data.js 在 git 里也都有，所以可以回溯：
按「该登记日 23:59 前最后一次 data.js 提交」重建当天各池的成员集合，
再把每条历史记录归到它当时所属的池。

## 口径与已知偏差（写进 note，不隐瞒）

- 池成员取自「该日最后一次 data.js 提交」（多为当晚 19:46 的 deploy 提交）。
  duanban / chenPool 由 16:00 数据步生成、19:30 叙事步只写 aiPrediction.verification 与
  ashare 叙述字段，**不改池**；star.buyList 是 9:45 产物、19:30 也不动。所以成员应当一致。
- 万一某只票在两次快照之间进出了池，会按「当日终版」归属——这是近似，不是逐时刻精确。
- 归不到任何池的条目**留在原汇总键**（不删），并打印数量当安全网：宁可少拆，不可错拆。

用法：
    python3 migrate_ledger_pools.py --dry     # 只看会怎么拆，不写盘
    python3 migrate_ledger_pools.py --write   # 落盘（先备份到 signal_ledger.json.bak-<ts>）
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, 'signal_ledger.json')

# 汇总键 → 子键的候选（按顺序匹配，先命中先用）
SPLIT = {
    'duanban': (('duanban_confirmed', ), ('duanban_watching', )),
    'chen': (('chen_strict', ), ('chen_loose', )),
    'buy': (('buy_duanban', ), ('buy_chen', ), ('buy_tech', ), ('buy_other', )),
}


def git_datajs(day):
    """取该日 23:59 前最后一次 data.js 提交的内容（无提交返回 None）。"""
    r = subprocess.run(
        ['git', 'log', '--before=%s 23:59' % day, '-1', '--format=%H', '--', 'data.js'],
        capture_output=True, text=True, cwd=HERE)
    h = (r.stdout or '').strip()
    if not h:
        return None
    r2 = subprocess.run(['git', 'show', '%s:data.js' % h],
                        capture_output=True, text=True, cwd=HERE)
    if r2.returncode != 0 or not r2.stdout:
        return None
    s = r2.stdout
    i = s.find('window.DASHBOARD_DATA')
    if i < 0:
        return None
    j = s.find('=', i)
    body = s[j + 1:].strip().rstrip(';').strip()
    try:
        return json.loads(body)
    except Exception:
        return None


def duanban_map(D):
    """当日 code → duanban_confirmed / duanban_watching。"""
    m = {}
    if not D:
        return m
    db = D.get('duanban') or {}
    for x in db.get('confirmed') or []:
        c = str(x.get('code') or '').strip()
        if c:
            m[c] = 'duanban_confirmed'
    for x in db.get('watching') or []:
        c = str(x.get('code') or '').strip()
        # confirmed 优先：同一只票不该同时在两个池；真同时出现时按确认池算
        if c and c not in m:
            m[c] = 'duanban_watching'
    return m


def chen_map(D):
    """当日 code → chen_strict / chen_loose。

    ⚠️ 必须与 duanban_map 分开：陈学长池里的票**大量同时在断板池里**
    （「两个都选中」就是交集），共用一个合并表时后来者不覆盖，
    导致 chen 里一多半条目归不到 chen_* → 干跑时 11 条里 8 条「无池归属」才发现。
    """
    m = {}
    if not D:
        return m
    cp = D.get('chenPool') or {}
    for x in cp.get('strict') or []:
        c = str(x.get('code') or '').strip()
        if c:
            m[c] = 'chen_strict'
    for x in cp.get('loose') or []:
        c = str(x.get('code') or '').strip()
        if c and c not in m:
            m[c] = 'chen_loose'
    return m


def buy_map(D):
    """当日 code → buy_* 子键 的映射。

    ⚠️ 必须与 pool_map 分开：buyList 里的票**同时也在断板池/陈学长池里**，
    用同一个映射表会把它们归成 duanban_confirmed / chen_loose（不同族），
    拆 buy 时一条也匹配不上。首版就这么写的，干跑时 3 天「全部无法归属」才发现。
    """
    m = {}
    if not D:
        return m
    for x in ((D.get('duanban') or {}).get('star') or {}).get('buyList') or []:
        c = str(x.get('code') or '').strip()
        if not c or str(x.get('src') or '') == 'bp':
            continue
        zh = str(x.get('srcZh') or '')
        m[c] = ('buy_chen' if '陈学长' in zh else
                'buy_duanban' if '断板' in zh else
                'buy_tech' if ('技术池' in zh or '板块' in zh) else 'buy_other')
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true', help='落盘（默认只打印）')
    ap.add_argument('--dry', action='store_true', help='只打印（同默认）')
    a = ap.parse_args()

    L = json.load(open(LEDGER, encoding='utf-8'))
    ent = L.get('entries') or {}
    dates = sorted(ent.keys())
    print('[migrate] 台账 %d 个日期：%s' % (len(dates), dates))

    cache = {}
    total_moved, total_kept = 0, 0
    for d in dates:
        groups = ent.get(d) or {}
        keys = [k for k in groups if k in SPLIT]
        if not keys:
            print('  %s: 无汇总键，跳过' % d)
            continue
        if d not in cache:
            D = git_datajs(d)
            cache[d] = {
                'duanban': duanban_map(D),
                'chen': chen_map(D),
                'buy': buy_map(D),
            }
            print('  %s: data.js %s，映射 断板 %d / 陈学长 %d / 清单 %d'
                  % (d, '已取到' if D else '✗ 取不到',
                     len(cache[d]['duanban']), len(cache[d]['chen']), len(cache[d]['buy'])))
        cm = cache[d]
        for k in keys:
            # 三张表各自独立：同一只票可以同时属于断板池和陈学长池（「两个都选中」），
            # 共用一张表就会互相占位（见 chen_map 的注释）。
            pm = cm.get(k) or {}
            items = groups.get(k) or []
            keep, moved = [], {}
            for it in items:
                c = str(it.get('code') or '').strip()
                tgt = pm.get(c)
                # 只允许拆到同一族的子键（duanban→duanban_*，chen→chen_*，buy→buy_*）
                if tgt and tgt.split('_')[0] == k.split('_')[0] and tgt != k:
                    moved.setdefault(tgt, []).append(it)
                else:
                    keep.append(it)
            if not moved:
                total_kept += len(keep)
                print('    %-10s %3d 条：全部无法归属，保留原键' % (k, len(keep)))
                continue
            groups[k] = keep
            for tgt, arr in moved.items():
                # 幂等：已有同日同键则去重合并（按 code）
                exist = {str(x.get('code')): x for x in (groups.get(tgt) or [])}
                for x in arr:
                    exist.setdefault(str(x.get('code')), x)
                groups[tgt] = list(exist.values())
                total_moved += len(arr)
            total_kept += len(keep)
            print('    %-10s %3d 条 → %s%s'
                  % (k, len(items) - len(keep),
                     ', '.join('%s %d' % (t, len(v)) for t, v in sorted(moved.items())),
                     ('；留原键 %d 条（无池归属）' % len(keep)) if keep else ''))
            if not keep:
                groups.pop(k, None)
        ent[d] = groups

    print('\n[migrate] 合计：迁移 %d 条，留在原键 %d 条' % (total_moved, total_kept))

    if not a.write:
        print('[migrate] 未写盘（加 --write 落盘）')
        return 0

    bak = LEDGER + '.bak-' + time.strftime('%Y%m%d-%H%M%S')
    shutil.copy2(LEDGER, bak)
    print('[migrate] 已备份 → %s' % os.path.basename(bak))
    L['entries'] = ent
    tmp = LEDGER + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(L, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, LEDGER)
    print('[migrate] 已写入 %s' % os.path.basename(LEDGER))
    return 0


if __name__ == '__main__':
    sys.exit(main())

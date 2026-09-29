#!/usr/bin/env python3
"""R100z4y 一次性补丁：为 harmonic 三池 sector 缺失的条目补板块名（新浪个股页单票兜底），
并合并进顶层 sectorMap。只改 sector / sectorMap，其余字段一律原样保留（R91m）。"""

import sys, os
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import common
import kline_cache as K


def main():
    D = common.load_dashboard_data(BASE)
    hz = D.get('harmonic') or {}
    sm = D.get('sectorMap') if isinstance(D.get('sectorMap'), dict) else {}
    targets = []
    for pool in ('confirmPool', 'watchPool', 'failPool'):
        for e in hz.get(pool) or []:
            if isinstance(e, dict) and e.get('code') and not e.get('sector'):
                targets.append((pool, e))
    codes = sorted({str(e['code']) for _, e in targets})
    print(f"[SECTOR] 待补 {len(targets)} 条 / {len(codes)} 只：{codes}")
    if not codes:
        return
    got = 0
    for c in codes:
        s = K.fetch_sector_single(c)
        if s:
            got += 1
            if sm is not None:
                sm.setdefault(c, s)
            print(f"[SECTOR] {c} -> {s}")
        else:
            print(f"[SECTOR] {c} 抓取失败，保留空值")
    for pool, e in targets:
        s = sm.get(str(e['code'])) or ''
        if s:
            e['sector'] = s
    print(f"[SECTOR] 成功 {got}/{len(codes)}")
    if got == 0:
        print('[SECTOR] 无一成功，不写回 data.js')
        return
    import json
    out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ";\n"
    with open(os.path.join(BASE, 'data.js'), 'w', encoding='utf-8') as f:
        f.write(out)
    import subprocess
    node = common.find_node()
    p = subprocess.run([node, '--check', 'data.js'], cwd=BASE, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit('node --check 失败：' + p.stderr)
    print('[SECTOR] data.js 写回完成，node --check 通过')


if __name__ == '__main__':
    main()

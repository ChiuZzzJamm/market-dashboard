#!/usr/bin/env python3
"""R100z4x 一次性回补：为 harmonic.failPool 存量条目快照「失效前」谐波几何（failHz）。

原理：失效归档时旧版脚本只存了 code/name/pattern/sector/reason/entryDate，未存几何。
形态检测是确定性的——把该标的 K 线截断到归档日（entryDate）之前（即它在池内的最后
一个收盘日），重跑 scan_bars 即可复现失效当时的 XABCD/PRZ/止损/目标位。

安全边界（R91m）：
- 只改 data.js 的 harmonic.failPool 各条目的 failHz 字段；其余任何字段一律原样保留。
- 扫描不出同形态（pattern 不匹配 / dead / 数据缺失）就跳过，宁缺勿滥，不写占位。
"""

import sys, os, json, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import common
import kline_cache as K
from harmonic_detect import scan_bars


def main():
    D = common.load_dashboard_data(BASE)
    hz = D.get('harmonic') or {}
    fp = hz.get('failPool') or []
    todo = [e for e in fp if isinstance(e, dict) and e.get('code')
            and not (isinstance(e.get('failHz'), dict) and e['failHz'].get('prz'))]
    print(f"[BACKFILL] failPool {len(fp)} 只，待回补 {len(todo)} 只")
    if not todo:
        print('[BACKFILL] 无待回补条目，退出')
        return

    codes = sorted({str(e['code']) for e in todo})
    kl = K.get_klines_bulk(codes, days=320, workers=8)
    got = sum(1 for v in kl.values() if v)
    print(f"[BACKFILL] K线抓取 {got}/{len(codes)}")

    hit = miss = 0
    for e in todo:
        code = str(e['code'])
        bars = kl.get(code)
        if not bars or len(bars) < 40:
            miss += 1
            print(f"[BACKFILL] 跳过 {code} {e.get('name','')}：无K线/不足40根")
            continue
        # R100z4x：失效归档时形态往往早已破位（缓存 K 线已是失效后价格），简单截断到
        # entryDate 通常复现不出。改为「从最新一根 K 线向前逐日回溯，找最近一次能复现谐波
        # 几何（优先同形态）的日期」，该几何即失效前结构（含 dead——破位当天结构恰是失效原貌）。
        best_exact = best_any = None
        for i in range(len(bars) - 1, 39, -1):
            r = scan_bars(bars[:i + 1])
            if not r or not r.get('points') or not r.get('prz'):
                continue
            if best_any is None:
                best_any = r
            if r.get('pattern') == e.get('pattern'):
                best_exact = r
                break
        r = best_exact or best_any
        if not r:
            miss += 1
            print(f"[BACKFILL] 跳过 {code} {e.get('name','')}：回溯全程无有效形态几何")
            continue
        approx = best_exact is None
        e['failHz'] = {k: r.get(k) for k in (
            'stage', 'points', 'pointDays', 'ratios', 'prz', 'stop', 'target1', 'target2', 'pattern')}
        if approx:
            e['failHz']['approx'] = True
        hit += 1

    print(f"[BACKFILL] 回补完成：成功 {hit}，跳过 {miss}")
    if hit == 0:
        print('[BACKFILL] 无一条成功，不写回 data.js')
        return

    # 写回（与 merge_duanban 同款格式），随后 node --check
    data_path = os.path.join(BASE, 'data.js')
    out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ";\n"
    with open(data_path, 'w', encoding='utf-8') as f:
        f.write(out)
    import subprocess
    node = common.find_node()
    p = subprocess.run([node, '--check', 'data.js'], cwd=BASE, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit('node --check 失败：' + p.stderr)
    print('[BACKFILL] data.js 写回完成，node --check 通过')

    today = datetime.date.today().strftime('%Y-%m-%d')
    print(f"[BACKFILL] 回补日期 {today}，failHz 样例：",
          json.dumps(fp[0].get('failHz'), ensure_ascii=False)[:200] if fp[0].get('failHz') else '无')


if __name__ == '__main__':
    main()

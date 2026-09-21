#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 data.js 中所有标的清单重排为统一顺序：龙头 → 概念 → 小盘人气 → 断板反包。
只读本地 data.js，原地重排 stocks 顺序后写回；不改数量、不改内容、不淘汰任何标的。
"""
import json
import os
import re
import subprocess

os.chdir(os.path.dirname(os.path.abspath(__file__)))


def load_data():
    node = subprocess.run(
        ['/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node', '-e',
         "const fs=require('fs');let s=fs.readFileSync('data.js','utf8');"
         "let m=s.match(/window\\s*\\.\\s*DASHBOARD_DATA\\s*=\\s*(\\{[\\s\\S]*\\});?\\s*$/);"
         "process.stdout.write(JSON.stringify(eval('('+m[1]+')')))"],
        capture_output=True, text=True)
    return json.loads(node.stdout)


def cat_of(note):
    n = (note or '').strip()
    if n.startswith('断板反包'):
        return 'D'
    if n.startswith('板块龙头') or n.startswith('龙头'):
        return 'L'
    if n.startswith('相关概念') or n.startswith('概念'):
        return 'C'
    if n.startswith('小盘'):
        return 'X'
    return '?'


# 目标顺序：龙头 L → 概念 C → 小盘 X → 断板 D
ORDER = {'L': 0, 'C': 1, 'X': 2, 'D': 3}


def reorder(stocks):
    """返回重排后的 stocks（稳定排序：同类内保持原相对顺序）。"""
    if not isinstance(stocks, list):
        return stocks
    return sorted(stocks, key=lambda s: ORDER.get(cat_of((s or {}).get('note', '')), 9))


def main():
    D = load_data()
    touched = 0

    for mk in ('ashare', 'us'):
        sec = D.get(mk) or {}
        for key in ('bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews'):
            for nw in (sec.get(key) or []):
                if not isinstance(nw, dict):
                    continue
                for imp in (nw.get('impacts') or []):
                    if isinstance(imp, dict) and isinstance(imp.get('stocks'), list):
                        new = reorder(imp['stocks'])
                        if new != imp['stocks']:
                            imp['stocks'] = new
                            touched += 1
        for key in ('bullish', 'bearish'):
            for g in (sec.get(key) or []):
                if isinstance(g, dict) and isinstance(g.get('stocks'), list):
                    new = reorder(g['stocks'])
                    if new != g['stocks']:
                        g['stocks'] = new
                        touched += 1

    ai = D.get('aiPrediction') or {}
    for s in (ai.get('sectors') or []):
        if isinstance(s, dict) and isinstance(s.get('stocks'), list):
            new = reorder(s['stocks'])
            if new != s['stocks']:
                s['stocks'] = new
                touched += 1

    with open('data.js', 'w', encoding='utf-8') as f:
        f.write('window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, indent=2) + ';\n')

    print(f"重排完成：共调整 {touched} 条清单为 龙头→概念→小盘人气→断板反包 顺序")


if __name__ == '__main__':
    main()

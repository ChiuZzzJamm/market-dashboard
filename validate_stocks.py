#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标的数量与配比硬校验（R82，零依赖，读本地 data.js）。

校验规则（与用户分层口径逐字对应）：
1) 五节要闻（bullNews/bearNews/macroNews/intlNews/bankViews）每条 impacts 的每个板块：
   恰好 8 只，且四类配比必须符合 N 分层表（N = 该板块 stocks 中「断板反包」数量）：
       N>=5: 龙头1 概念1 小盘1 断板5
       N=4 : 龙头2 概念1 小盘1 断板4
       N=3 : 龙头2 概念2 小盘1 断板3
       N=2 : 龙头2 概念2 小盘2 断板2
       N=1 : 龙头2 概念2 小盘3 断板1
       N=0 : 龙头2 概念2 小盘4 断板0
2) aiPrediction.sectors 每个板块：同样恰好 8 只 + 上述配比。
3) bullish/bearish 每个主题：恰好 4 只（不分层）。
4) 所有标的 code 必须为 60/00 开头沪深主板（禁 688/689、300/301/302、4/8/92 开头）。
5) macroNews/intlNews/bankViews 每条要闻卡必须含 direction 字段，取值仅限 看涨/看跌/中性
   （bullNews/bearNews 落在利好/利空列、无 direction，不校验；缺失方向则页面涨跌徽标空白）。

类别按 note 前缀判定：断板反包→断板；板块龙头/龙头→龙头；相关概念/概念→概念；
小盘→小盘；其余前缀视为无法识别（报违规）。

顺序硬约束（R83）：每只清单内的标的必须按「龙头 → 概念 → 小盘人气 → 断板反包」
的先后顺序排列（同类内保持原相对序），不得穿插交错，否则判违规。

用法: python3 validate_stocks.py [--json]
退出码: 0 = 全部合规(输出 ALL OK)；1 = 有违规。
"""
import json
import re
import subprocess
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

BAD_PREFIX = ('688', '689', '300', '301', '302', '4', '8', '92')

VALID_DIR = ('看涨', '看跌', '中性')

# 仅这三节要闻需要 direction（页面涨跌徽标来源）；bullNews/bearish 落在利好/利空列。
DIR_KEYS = ('macroNews', 'intlNews', 'bankViews')

TIERS = {
    5: (1, 1, 1), 6: (1, 1, 1),  # N>=5
    4: (2, 1, 1),
    3: (2, 2, 1),
    2: (2, 2, 2),
    1: (2, 2, 3),
    0: (2, 2, 4),
}


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


def board_bad(code):
    c = str(code or '')
    return (not re.fullmatch(r'\d{6}', c)) or c.startswith(BAD_PREFIX)


def check_tiered(stocks, where, violations):
    """恰好 8 只 + N 分层配比 + 主板过滤 + 顺序硬约束（龙头→概念→小盘→断板）。"""
    if not isinstance(stocks, list) or not stocks:
        violations.append(f"{where}: stocks 缺失或为空")
        return
    for s in stocks:
        if isinstance(s, dict) and board_bad(s.get('code')):
            violations.append(f"{where}: 含非沪深主板标的 {s.get('code')} {s.get('name','')}")
    from collections import Counter
    cnt = Counter()
    seq = []
    for s in stocks:
        c = cat_of((s or {}).get('note', ''))
        cnt[c] += 1
        seq.append(c)
    n = len(stocks)
    if n != 8:
        violations.append(
            f"{where}: 共 {n} 只（应为 8）| 配比 L{cnt['L']} C{cnt['C']} X{cnt['X']} D{cnt['D']}"
            + ("| 含无法识别类别note" + dict(cnt)['?'] if cnt['?'] else ''))
        return
    d = cnt['D']
    if d > 5:
        violations.append(f"{where}: 断板反包 {d} 只 > 5，超出分层表上限")
        return
    if cnt['?']:
        violations.append(
            f"{where}: {cnt['?']} 只标的 note 前缀无法识别四类身份（须以 断板反包/板块龙头/相关概念/小盘 开头）")
        return
    need = TIERS[d]
    if (cnt['L'], cnt['C'], cnt['X']) != need:
        violations.append(
            f"{where}: N={d} 应为 龙头{need[0]} 概念{need[1]} 小盘{need[2]} 断板{d}，"
            f"实际 龙头{cnt['L']} 概念{cnt['C']} 小盘{cnt['X']} 断板{cnt['D']}")
        return
    # 顺序硬约束：必须严格 龙头(全部) → 概念(全部) → 小盘(全部) → 断板(全部)
    ORDER = {'L': 0, 'C': 1, 'X': 2, 'D': 3}
    if seq != sorted(seq, key=lambda c: ORDER[c]):
        violations.append(
            f"{where}: 顺序不合规（应为 龙头→概念→小盘人气→断板反包），实际序列 {' '.join(seq)}")


def check4(stocks, where, violations):
    """bullish/bearish 每主题恰好 4 只 + 主板过滤（note 为自由文本，不要求四类前缀/顺序）。"""
    if not isinstance(stocks, list) or not stocks:
        violations.append(f"{where}: stocks 缺失或为空")
        return
    for s in stocks:
        if isinstance(s, dict) and board_bad(s.get('code')):
            violations.append(f"{where}: 含非沪深主板标的 {s.get('code')} {s.get('name','')}")
    if len(stocks) != 4:
        violations.append(f"{where}: 共 {len(stocks)} 只（应为 4）")


def check_direction(nw, where, violations):
    """macroNews/intlNews/bankViews 每条必须带 direction（看涨/看跌/中性），否则页面涨跌徽标空白。"""
    if not isinstance(nw, dict):
        return
    d = nw.get('direction')
    if d not in VALID_DIR:
        violations.append(
            f"{where}: 缺少/非法 direction（应为 看涨/看跌/中性 之一，实际 {d!r}）")


def reorder_inplace(D):
    """无破坏性地把所有标的清单重排为 龙头→概念→小盘人气→断板反包（同类内保序）。
    仅调顺序，不改数量与内容；幂等。"""
    ORDER = {'L': 0, 'C': 1, 'X': 2, 'D': 3}

    def r(stocks):
        if not isinstance(stocks, list):
            return stocks
        return sorted(stocks, key=lambda s: ORDER.get(cat_of((s or {}).get('note', '')), 9))

    for mk in ('ashare', 'us'):
        sec = D.get(mk) or {}
        for key in ('bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews'):
            for nw in (sec.get(key) or []):
                if isinstance(nw, dict):
                    for imp in (nw.get('impacts') or []):
                        if isinstance(imp, dict) and isinstance(imp.get('stocks'), list):
                            imp['stocks'] = r(imp['stocks'])
        for key in ('bullish', 'bearish'):
            for g in (sec.get(key) or []):
                if isinstance(g, dict) and isinstance(g.get('stocks'), list):
                    g['stocks'] = r(g['stocks'])
    ai = D.get('aiPrediction') or {}
    for s in (ai.get('sectors') or []):
        if isinstance(s, dict) and isinstance(s.get('stocks'), list):
            s['stocks'] = r(s['stocks'])


def main():
    as_json = '--json' in sys.argv
    D = load_data()

    # 先无破坏性地按统一顺序重排（龙头→概念→小盘人气→断板反包），
    # 再校验数量/配比/主板；顺序问题在此被自动纠正，不会触发 FAIL。
    if '--no-fix-order' not in sys.argv:
        reorder_inplace(D)
        with open('data.js', 'w', encoding='utf-8') as f:
            f.write('window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, indent=2) + ';\n')

    violations = []

    for mk in ('ashare', 'us'):
        sec = D.get(mk) or {}
        for key in ('bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews'):
            for i, nw in enumerate(sec.get(key) or []):
                if not isinstance(nw, dict):
                    continue
                if key in DIR_KEYS:
                    check_direction(nw, f"{mk}.{key}[{i}]({(nw.get('sector') or '')[:14]})", violations)
                for j, imp in enumerate(nw.get('impacts') or []):
                    if isinstance(imp, dict):
                        check_tiered(imp.get('stocks'),
                                     f"{mk}.{key}[{i}].impacts[{j}]({(imp.get('theme') or '')[:14]})",
                                     violations)
        for key in ('bullish', 'bearish'):
            for i, g in enumerate(sec.get(key) or []):
                if isinstance(g, dict):
                    check4(g.get('stocks'), f"{mk}.{key}[{i}]({(g.get('theme') or '')[:14]})", violations)

    ai = D.get('aiPrediction') or {}
    for i, s in enumerate(ai.get('sectors') or []):
        if isinstance(s, dict):
            check_tiered(s.get('stocks'), f"aiPrediction.{(s.get('sector') or '')[:16]}", violations)

    if as_json:
        print(json.dumps({'ok': not violations, 'violations': violations},
                         ensure_ascii=False, indent=2))
    if violations:
        print(f"[FAIL] 共 {len(violations)} 处违规：")
        for v in violations:
            print("  -", v)
        sys.exit(1)
    print("ALL OK: 全部 impacts/aiPrediction 恰好 8 只且配比符合 N 分层表、"
          "顺序为龙头→概念→小盘人气→断板反包，bullish/bearish 恰好 4 只，全部标的为沪深主板；"
          "macroNews/intlNews/bankViews 均含合法 direction（看涨/看跌/中性）。")


if __name__ == '__main__':
    main()

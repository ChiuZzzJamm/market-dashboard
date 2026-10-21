#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
risk_gate.py —— 把「个股硬风险黑名单 red」落到四个推荐池（R100z168）

背景：stock_risk_blacklist.py 每天都在算 red 名单，但只有 update_star.py 会过滤。
      check_duanban / power_screener / accumulation_score 压根不读 → 10-09 就公告减持的
      603330 天洋新材，到 10-21 还挂在「断板确认池」和「量化筛选」里当推荐标的。

清理范围（只动「推荐位」，不动研究/跟踪位）：
    duanban.confirmed / watching / star.picks
    powerScreen.passed
    accumulation.scored / strong / watch
刻意不动：
    *.failPool（失败池本来就该列出来）、fullScan.candidates（全量扫描研究用）、
    stkKlines（K 线数据）、stopTrack（持仓跟踪，剔掉等于丢跟踪）、
    五节要闻 impacts —— **不剔**。原因见下。

  为什么 impacts 不自动剔（R100z168 实测踩坑）：
    `validate_stocks` 要求每个 impacts.stocks **恰好 8 只**且配比 L2/C2/X1/D3。
    剔掉「化学制药」里的 ST人福 后剩 7 只 → 立刻 `[FAIL] 共 7 只（应为 8）`，
    自动化反而把部署拦死；而机械池同板块只有 4 只断板候选，补不出「概念」位。
    所以 impacts 归 **AI 换股**（prompt 已写明「受益标的不得含 ST/退市股」），
    G29 只负责在非利空段发现 ST 时告警；利空段（bearNews/bearish）放 ST 股属合理分析。

剔除口径（与 validate_stocks.check_risk_blacklist 的 asof 语义一致）：
    · ST/退市型 red        → 无日期例外，一律剔（ST 不该出现在任何推荐位）
    · 公告型 red           → 公告日 <= 交易日 ⇒ 剔；全部公告日 > 交易日 ⇒ 保留
      （盘后新披露的，当天盘中确实无法预知，指标本身有效；次日 09:45 会被剔）

用法：
    python3 risk_gate.py            # 只报告
    python3 risk_gate.py --write    # 清理并写回 data.js（_tmp + os.replace）
    python3 risk_gate.py --check    # 闸门用：有残留则 exit 1
"""
import argparse
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(ROOT, 'data.js')
BL_JSON = os.path.join(ROOT, 'risk_blacklist.json')

TARGETS = [
    (('duanban', 'confirmed'), '断板确认池'),
    (('duanban', 'watching'), '断板观察池'),
    (('duanban', 'star', 'picks'), '开盘精选'),
    (('powerScreen', 'passed'), '量化筛选'),
    (('accumulation', 'scored'), '吸筹打分'),
    (('accumulation', 'strong'), '吸筹强化'),
    (('accumulation', 'watch'), '吸筹观察'),
]

DATE_RE = re.compile(r'20\d{2}-\d{2}-\d{2}')


def load_data():
    raw = open(DATA_JS, encoding='utf-8').read()
    return json.loads(raw[raw.index('{'):raw.rindex('}') + 1])


def load_red():
    if not os.path.exists(BL_JSON):
        return {}, 'missing'
    try:
        bl = json.load(open(BL_JSON, encoding='utf-8'))
    except Exception as e:
        return {}, 'bad:%s' % e
    out = {}
    for code, v in ((bl.get('blacklist') or {})).items():
        if (v or {}).get('severity') == 'red':
            out[str(code)] = v
    return out, 'ok'


def trade_day(D):
    t = str(((D.get('ashare') or {}).get('tradeDate') or ''))[:10]
    if DATE_RE.fullmatch(t):
        return t
    return datetime.date.today().isoformat()


def judge(code, v, today):
    """返回 (是否剔除, 原因描述)。"""
    reasons = [str(x) for x in (v.get('reasons') or [])]
    txt = '；'.join(reasons)
    if any('ST/退市' in r for r in reasons):
        return True, 'ST/退市型（%s）' % txt
    dates = DATE_RE.findall(txt)
    if not dates:
        return True, '公告型 red 但无日期可判，按已发生处理（%s）' % txt
    late = [d for d in dates if d > today]
    if len(late) == len(dates):
        return False, '盘后新披露（%s > 交易日 %s），本日盘中不可知，保留' % (max(dates), today)
    hit = [d for d in dates if d <= today]
    return True, '公告型（%s，交易日 %s 前已发生）' % (max(hit), today)


def get_list(D, path):
    node = D
    for p in path:
        if not isinstance(node, dict):
            return None
        node = node.get(p)
    return node if isinstance(node, list) else None


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--write', action='store_true', help='清理并写回 data.js')
    g.add_argument('--check', action='store_true', help='有残留则 exit 1（闸门用）')
    args = ap.parse_args()

    D = load_data()
    red, st = load_red()
    if st != 'ok':
        print('⚠ risk_blacklist.json %s → 无法判定，不改任何池' % st)
        return 0 if st == 'missing' else 1
    if not red:
        print('✅ 黑名单无 red 标的，无需清理（幂等）')
        return 0

    today = trade_day(D)
    verdict = {c: judge(c, v, today) for c, v in red.items()}
    print('交易日 %s｜red 标的 %d 只' % (today, len(red)))
    for c, (drop, why) in sorted(verdict.items()):
        print('  %s %-8s %s  %s' % (c, red[c].get('name', ''), '剔除' if drop else '保留', why))

    total = 0
    for path, label in TARGETS:
        lst = get_list(D, path)
        if lst is None:
            print('  · %s：节点不存在，跳过' % label)
            continue
        keep, dropped = [], []
        for it in lst:
            code = str((it or {}).get('code') or '')
            drop, why = verdict.get(code, (False, ''))
            if drop:
                dropped.append((code, (it or {}).get('name') or red[code].get('name', '')))
            else:
                keep.append(it)
        if dropped:
            total += len(dropped)
            print('  ✂ %s（%d→%d）：%s' % (label, len(lst), len(keep),
                                          '、'.join('%s %s' % x for x in dropped)))
            if args.write:
                lst[:] = keep
        else:
            print('  · %s：无 red 标的（%d 只）' % (label, len(lst)))

    if args.check:
        if total:
            print('❌ 仍有 %d 个 red 标的留在推荐位 → risk_gate.py --write 未跑或被绕过' % total)
            return 1
        print('✅ 推荐位无 red 标的')
        return 0

    if not total:
        print('✅ 推荐位干净，无改动（幂等）')
        return 0
    if not args.write:
        print('（未加 --write，仅报告）')
        return 0

    body = json.dumps(D, ensure_ascii=False, separators=(',', ':'))
    tmp = DATA_JS + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('window.DASHBOARD_DATA = ' + body + ';\n')
    os.replace(tmp, DATA_JS)
    print('✅ 已写回 data.js，共剔除 %d 条' % total)
    return 0


if __name__ == '__main__':
    sys.exit(main())

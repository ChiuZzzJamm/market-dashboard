#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_narrative.py · 叙事深度确定性预检（R100z136，audit_pipeline 之前的自检工具）

背景（2026-10-14 / 10-15 两次 16:00 任务各跑 2 小时+ 的根因之一）：
叙事写完后直接跑 audit_pipeline，G16 只报前 15 条短字段、G17/G20 只报前 2-3 条，
AI 拿不到完整清单，只能「猜哪里短 → 整文件重写 → 再跑 audit」拉锯 4-5 轮
（10-15 有约 25 分钟浪费在「给 deduce 加共享后缀凑字数 → 触发 G20 → 回退重写」的循环）。

本脚本把 G16 / G17 / G20 三项检查的**完整清单**在落笔后、audit 前打印出来：
  - G16 每个短字段：路径 + 当前字数 + 地板 + 缺口（--detail 附当前文本摘录）
  - G17 每个复读句：句子 + 出现在哪些字段（≥14 字被 ≥3 字段共用）
  - G20 每个跨板块模板窗口：窗口 + 涉及哪些板块字段（≥12 字 × ≥3 板块）
与 audit_pipeline 共用同一份采集函数（collect_g16_checks / collect_g17_owners /
collect_g20_dups），阈值永远一致，不存在两套口径。
预检全绿 = audit 的 G16/G17/G20 必然全绿，不必再用 audit 试错。

用法:
    python3 check_narrative.py              # 全量预检
    python3 check_narrative.py --detail     # 附每个短字段的当前文本摘录
    python3 check_narrative.py --only us    # 只查某前缀（ashare/us/aiPrediction/openOutlook）

退出码: 0 = 全部达标（可直跑 audit_pipeline）  1 = 有清单待处理  2 = data.js 解析失败
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import audit_pipeline as ap  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description="叙事深度预检：G16 短字段 / G17 复读 / G20 跨板块模板（与 audit_pipeline 同口径）")
    parser.add_argument("--detail", action="store_true",
                        help="附每个短字段的当前文本摘录（前 160 字）")
    parser.add_argument("--only", default=None,
                        help="只查指定前缀的字段：ashare / us / aiPrediction / openOutlook"
                             "（deduce 全站条目始终受检）")
    a = parser.parse_args()

    try:
        D = ap.load_data_json()
    except Exception as e:                                   # noqa: BLE001
        print(f"[FAIL] data.js 解析失败：{e}")
        return 2

    only = a.only
    if only and not only.endswith("."):
        only = only + "."

    def wanted(label):
        # --only 收窄到某市场/顶层字段；deduce 是全站内嵌推演，任何任务都可能写，始终受检
        return (not only) or label.startswith(only) or label.startswith("deduce")

    # ---------------- G16 叙事字数地板 ----------------
    checks = ap.collect_g16_checks(D)
    lookup = {lb: t for lb, t, _ in checks}
    shorts = [(lb, len(t), fl) for lb, t, fl in checks if len(t) < fl and wanted(lb)]
    print("=" * 78)
    print(" check_narrative · 叙事深度预检（与 audit_pipeline G16/G17/G20 同口径）")
    print("=" * 78)
    print(f"\n[G16] 叙事字数地板：{len(checks)} 处受检，{len(shorts)} 处过短")
    for lb, n, fl in shorts:
        print(f"  SHORT {lb}  当前 {n} 字 / 需 {fl}（缺口 {fl - n}）")
        if a.detail:
            cur = lookup.get(lb, "")
            print(f"        当前文本：{cur[:160]}{'…' if len(cur) > 160 else ''}")

    # ---------------- G17 跨字段模板复读 ----------------
    owners = ap.collect_g17_owners(D)
    dups = [(s, sorted(o)) for s, o in owners.items()
            if len(o) >= 3 and (not only or any(wanted(x) for x in o))]
    dups.sort(key=lambda x: -len(x[1]))
    print(f"\n[G17] 跨字段模板复读（≥14 字句子被 ≥3 个字段共用）：{len(dups)} 句")
    for s, labels in dups:
        tail = " …" if len(s) > 60 else ""
        more = " …" if len(labels) > 8 else ""
        print(f"  DUP 「{s[:60]}{tail}」 ×{len(labels)} 处：{', '.join(labels[:8])}{more}")

    # ---------------- G20 板块 deduce 跨板块模板 ----------------
    g20 = ap.collect_g20_dups(D)
    if only:
        mk_only = only.rstrip(".")
        g20 = [x for x in g20 if x[0] == mk_only]
    print(f"\n[G20] 板块 deduce 跨板块共享模板（≥12 字窗口 × ≥3 板块）：{len(g20)} 处")
    for mk, w, labels in g20[:20]:
        more = " …" if len(labels) > 8 else ""
        print(f"  TPL [{mk}] 「{w}」 ×{len(labels)} 板块：{', '.join(labels[:8])}{more}")
    if len(g20) > 20:
        print(f"  ……另有 {len(g20) - 20} 处，从略")

    # ---------------- 汇总 ----------------
    total = len(shorts) + len(dups) + len(g20)
    print("\n" + "-" * 78)
    if total:
        print(f" 合计 {total} 项待处理。按清单精确补写——严禁为凑字数添加共享后缀/模板句")
        print(" （那正是 G17/G20 的打击对象）；改完重跑本脚本，全绿（退出码 0）再跑")
        print(" audit_pipeline.py，届时 G16/G17/G20 必然全绿。")
        return 1
    print(" 全部达标 → 可直接跑 audit_pipeline.py（G16/G17/G20 此时必然全绿）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

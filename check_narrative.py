#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_narrative.py · 叙事深度与字数的确定性预检（R100z136，audit_pipeline 之前的自检工具）

背景（2026-10-14 / 10-15 两次 16:00 任务各跑 2 小时+ 的根因之一）：
叙事写完后直接跑 audit_pipeline，G16 只报前 15 条短字段、G17/G20 只报前 2-3 条，
AI 拿不到完整清单，只能「猜哪里短 → 整文件重写 → 再跑 audit」拉锯 4-5 轮
（10-15 有约 25 分钟浪费在「给 deduce 加共享后缀凑字数 → 触发 G20 → 回退重写」的循环）。

本脚本把 G16 / G17 / G20 三项检查的**完整清单**在落笔后、audit 前打印出来：
  - G16 每个短字段：路径 + 当前字数 + 地板 + 缺口（--detail 附当前文本摘录）
  - G16 每个超长字段：路径 + 当前字数 + 上限 + 超出量（R100z160 新增）
  - G17 每个复读句：句子 + 出现在哪些字段（≥14 字被 ≥3 字段共用，分句符含 `；`）
  - G20 每个跨板块模板窗口：窗口 + 涉及哪些板块字段（≥12 字 × ≥3 板块）
与 audit_pipeline 共用同一份采集函数（collect_g16_checks / collect_g17_owners /
collect_g20_dups），阈值永远一致，不存在两套口径。
预检全绿 = audit 的 G16/G17/G20 必然全绿，不必再用 audit 试错。

★字数纪律（R100z160，2026-10-21 实报「凭感觉估字数，压缩迭代 3 轮 276→219→180」）：
  这里的「字数」= `len(str)`，**数字、标点、书名号、英文字母全部计入**。凭汉字个数直觉估
  普遍低估 40-60 字，所以**不要估，直接看这张表**。上限来自 7 条自动化 prompt 里写明的
  区间（未写区间的字段只报地板，绝不瞎设上限）；超上限标 OVER，但**不拦部署**（G16 只有
  地板，OVER 属 prompt 纪律问题而非数据错误），改到位即可，别为了压字数再改坏内容。

用法:
    python3 check_narrative.py              # 默认：短字段 + 超长字段清单
    python3 check_narrative.py --table      # 追加全字段字数实测表（当前/地板/上限/状态）
    python3 check_narrative.py --detail     # 附每个短字段的当前文本摘录
    python3 check_narrative.py --only us    # 只查某前缀（ashare/us/aiPrediction/openOutlook）

退出码: 0 = 地板全达标（可直跑 audit_pipeline）  1 = 有短字段/复读/模板待处理  2 = data.js 解析失败
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import audit_pipeline as ap  # noqa: E402


def ceil_of(label):
    """字段字数上限（R100z160）。只登记 prompt 里**写明区间**的字段，其余返回 None。

    来源逐条可查（prompt 原文 → 区间）：
      08:30 / 周日22:00  aiPrediction.sectors[].logic   200-260
      19:30              ashare.themePicks[].logic      240-420
      19:30              ashare.lianban[].story         120-260
      16:00 / 07:00 / 周日22:00  duanban.*[].story      100-180
      07:00 / 周日21:00  us.sectorsUp/Down[].reason.detail  220-400
      07:00 / 周日21:00  us 五节要闻[].summary           220-400
      19:30              ashare.outlook                 300-500
      07:00              us.outlook                     600-900（周日21:00 写 300-500，口径不一
                                                        → 取并集上限，只挡明显超长，不误报）
      08:30 / 周日22:00  openOutlook                    400-600
      deduce             main 60-120 / counter 40-80 / cands.text 40-80 / eliminate 15-40
    未登记（prompt 未写区间，只受 G16 地板管）：ashare 板块 detail、ashare 五节 summary、
    us.summary、accumulation note。硬设上限会逼 AI 无端删内容，宁可不设。
    """
    if label.startswith("aiPrediction.sectors[") and label.endswith(".logic"):
        return 260
    if label.startswith("ashare.themePicks[") and label.endswith(".logic"):
        return 420
    if label.startswith("ashare.lianban[") and label.endswith(".story"):
        return 260
    if label.startswith("duanban.") and label.endswith(".story"):
        return 180
    if label.startswith("us.") and label.endswith(".reason.detail"):
        return 400
    if label.startswith("us.") and label.endswith(".summary") and "[" in label:
        return 400
    if label == "us.outlook":
        return 900
    if label == "ashare.outlook":
        return 500
    if label in ("openOutlook", "aiPrediction.openOutlook"):
        return 650   # 08:30 写 content 450-650 / 周日22:00 写 400-600 → 取并集上限
    if label == "duanban.star.pushText":
        return 700   # 9:45 prompt 写死 300-700（页面🌟模块与微信推送共用）
    if label == "duanban.star.sentiment":
        return 200   # 9:45 prompt 写死 120-200
    if label.startswith("deduce#"):
        if label.endswith(".main"):
            return 120
        if label.endswith(".counter"):
            return 80
        if label.endswith(".eliminate"):
            return 40
        if label.endswith(".text") or ".cands[" in label:
            return 80
    return None


def main():
    parser = argparse.ArgumentParser(
        description="叙事深度预检：G16 短字段/超长字段 / G17 复读 / G20 跨板块模板（与 audit_pipeline 同口径）")
    parser.add_argument("--detail", action="store_true",
                        help="附每个短字段的当前文本摘录（前 160 字）")
    parser.add_argument("--table", action="store_true",
                        help="追加全字段字数实测表（当前字数 / 地板 / 上限 / 状态）")
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

    # ---------------- G16 叙事字数地板 + 上限 ----------------
    checks = ap.collect_g16_checks(D)
    lookup = {lb: t for lb, t, _ in checks}
    rows = [(lb, len(t), fl, ceil_of(lb)) for lb, t, fl in checks if wanted(lb)]
    shorts = [r for r in rows if r[1] < r[2]]
    overs = [r for r in rows if r[3] and r[1] > r[3]]
    overs.sort(key=lambda r: -(r[1] - r[3]))     # 超出最多的先看
    print("=" * 88)
    print(" check_narrative · 叙事深度预检（与 audit_pipeline G16/G17/G20 同口径）")
    print(" 字数 = len(str)，数字/标点/书名号全部计入 —— 凭汉字直觉普遍低估 40-60 字，别看感觉看这里")
    print("=" * 88)
    print(f"\n[G16] 叙事字数：{len(rows)} 处受检，{len(shorts)} 处过短，{len(overs)} 处超上限")
    for lb, n, fl, _c in shorts:
        print(f"  SHORT {lb}  当前 {n} 字 / 需 {fl}（缺口 {fl - n}）")
        if a.detail:
            cur = lookup.get(lb, "")
            print(f"        当前文本：{cur[:160]}{'…' if len(cur) > 160 else ''}")
    for lb, n, fl, c in overs[:12]:
        print(f"  OVER  {lb}  当前 {n} 字 / 上限 {c}（超出 {n - c}）")
    if len(overs) > 12:
        print(f"  ……另有 {len(overs) - 12} 处超上限（超出量较小），加 --table 看全量")

    if a.table:
        print("\n  ---- 全字段字数实测表（当前 / 地板 / 上限 / 状态）----")
        for lb, n, fl, c in sorted(rows):
            if n < fl:
                st = "SHORT"
            elif c and n > c:
                st = "OVER "
            else:
                st = "ok   "
            print(f"  {lb:<46} {n:>5} {fl:>6} {(c if c else '—'):>6}  {st}")

    # ---------------- G17 跨字段模板复读 ----------------
    owners = ap.collect_g17_owners(D)
    dups = [(s, sorted(o)) for s, o in owners.items()
            if len(o) >= 3 and (not only or any(wanted(x) for x in o))]
    dups.sort(key=lambda x: -len(x[1]))
    print(f"\n[G17] 跨字段模板复读（≥14 字句子被 ≥3 个字段共用，分句符 。！？；）：{len(dups)} 句")
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
    print("\n" + "-" * 88)
    if overs:
        print(f" 另有 {len(overs)} 处超上限（OVER）：不拦部署，但超出 prompt 区间会挤占上下文、"
              f"且容易被后续压缩打乱——按上表上限一次改到位，别再反复试凑。")
    if total:
        print(f" 合计 {total} 项待处理。按清单精确补写——严禁为凑字数添加共享后缀/模板句")
        print(" （那正是 G17/G20 的打击对象）；改完重跑本脚本，全绿（退出码 0）再跑")
        print(" audit_pipeline.py，届时 G16/G17/G20 必然全绿。")
        return 1
    print(" 地板全部达标 → 可直接跑 audit_pipeline.py（G16/G17/G20 此时必然全绿）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_dd_prefix.py —— 清洗 data.js 里 deduce 文本自带的「主推/反证」前缀。

背景（R100z173/z174）：渲染层统一在 deduce.main / deduce.counter 前面补标签
（「主推：」「反证：」），但 AI 生成时经常自己再写一层，实测三种形态：
  - 早期形态 `反证：…`              （R99o 已拦）
  - 实测 19 条 `反证是…`（无冒号）    （R100z173）
  - 实测 1 条  `反证在资金：…`        （R100z174，页面显示「反证：反证在资金：…」）
本项目正确做法是「数据层不带标签、渲染层加标签」，所以数据里残留的一律剥掉。

⚠️ 前缀正则**直接复用 common._DD_PREFIX_RE**（单一事实源）。index.html 的 _ddClean()
是同一口径的 JS 副本，改这里必须同改那边（audit_pipeline G31 拿同一组样例跑三方比对）。

用法：
  python3 fix_dd_prefix.py            # 只扫描打印（默认 dry-run，不动文件）
  python3 fix_dd_prefix.py --write    # 落盘（_tmp + os.replace 原子写）
  python3 fix_dd_prefix.py --scan     # 只做合规复查，有任何命中即 exit 1（给闸门调用）

设计取舍：走**文本级精确替换**而不是 json 反序列化再 dumps——
后者会把整份 data.js 重新序列化（25 万字符级 churn，见 common.load_dashboard_data
的注释），文本替换只动命中的那几十字节，diff 干净可审。
"""
import json
import os
import re
import sys

from common import _DD_PREFIX_RE

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data.js")

# 只匹配**字段值开头**（"counter":"…" / "main":"…"）的标签，避免误伤正文里的引用。
# 去掉行首锚 ^，把 common 的同一套分支内联到引号之后，保证三方口径完全一致。
PAT = re.compile(
    r'("(?:counter|main)"\s*:\s*")' + _DD_PREFIX_RE.pattern.replace("^", "", 1)
)


def load_text():
    with open(DATA, encoding="utf-8") as f:
        return f.read()


def parse(text):
    m = re.search(r"window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$", text)
    if not m:
        raise SystemExit("data.js 里找不到 DASHBOARD_DATA，放弃")
    return json.loads(m.group(1))


def main():
    write = "--write" in sys.argv
    scan = "--scan" in sys.argv
    text = load_text()
    hits = list(PAT.finditer(text))

    if scan:
        if hits:
            print("FAIL data.js 有 %d 处 deduce 自前缀（应为 0，渲染层会重复）" % len(hits))
            for m in hits[:5]:
                print("  台账=", text[max(0, m.start() - 24):m.end() + 34].replace("\n", " "))
            return 1
        print("PASS data.js 的 deduce.main/counter 均无自前缀")
        return 0

    if not hits:
        print("无需清洗：0 处自前缀")
        return 0

    print("命中 %d 处：" % len(hits))
    for m in hits:
        seg = text[m.end():m.end() + 34]
        print("  - %s" % seg.replace("\n", " "))

    if not write:
        print("\n[dry-run] 未改动文件；确认无误后加 --write 落盘")
        return 0

    new = PAT.sub(r"\1", text)
    # 落盘前校验：JSON 仍可解析、且条数不变（防替换把结构弄坏）
    before = parse(text)
    after = parse(new)

    def count_stats(d):
        n = {"counter": 0, "main": 0, "deduce": 0}

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("counter", "main") and isinstance(v, str):
                        n[k] += 1
                    elif k == "deduce" and isinstance(v, dict):
                        n["deduce"] += 1
                    walk(v)
            elif isinstance(o, list):
                for x in o:
                    walk(x)

        walk(d)
        return n

    s0, s1 = count_stats(before), count_stats(after)
    if s0 != s1:
        raise SystemExit("统计不一致，放弃写入：%s vs %s" % (s0, s1))
    if len(new) >= len(text):
        raise SystemExit("替换后长度没变小，说明正则没真正命中，放弃写入")

    tmp = DATA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, DATA)

    # 复验落盘（Edit 报成功 ≠ 落盘，这里也一样）
    left = len(PAT.findall(load_text()))
    print("\n已写入：%d 处前缀被剥，字节 %d → %d，复查残留 %d 处" %
          (len(hits), len(text.encode()), len(new.encode()), left))
    print("counter=%d main=%d deduce=%d（替换前后一致）" % (s1["counter"], s1["main"], s1["deduce"]))
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())

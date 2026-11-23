#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多空题材（ashare.bullish / ashare.bearish）渲染闸门。

为什么需要：这两个字段 2026-10-08 起「数据保留、独立卡删除」，结果谁也不显示——
AI 每天生成 4+4 条、页面一个字看不到，直到 2026-11-23 用户问「活字段没人看」。
现在挂进「题材掘金」卡内部渲染（不新增模块）。本闸门把 renderBullBear 抠出来真跑一次：
条数必须与数据一致、不许出现 undefined / null / [object Object]。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_pend_table as CP                                    # noqa: E402

NEED = ("esc", "escAttr", "stkTechMeta", "stkGoldCls", "renderBullBear")
JS = r"""
const fs = require('fs');
global.window = {}; global.document = {};
eval(fs.readFileSync(__DATA__, 'utf8'));
const D = window.DASHBOARD_DATA || {};
const a = D.ashare || {};
const h = renderBullBear(a);
const out = {ok: true, errs: [], info: {}};
function bad(m) { out.ok = false; out.errs.push(m); }
const up = (a.bullish || []).length, dn = (a.bearish || []).length;
out.info = {up: up, dn: dn, len: h.length};
if (!up && !dn) { out.info.empty = true; }
else {
  const items = (h.match(/<div class="bb-item">/g) || []).length;
  if (items !== up + dn) bad('渲染 ' + items + ' 条 ≠ 数据 ' + (up + dn) + ' 条');
  if (h.indexOf('bb-up') < 0 && up) bad('利好题材有数据但没渲染左列');
  if (h.indexOf('bb-down') < 0 && dn) bad('利空题材有数据但没渲染右列');
  ['undefined', 'null', '[object Object]'].forEach(function (t) {
    if (h.indexOf(t) >= 0) bad('渲染结果里出现 ' + t);
  });
  if ((h.match(/stock-chip/g) || []).length < (up + dn)) bad('标的 chip 少于题材条数（stocks 没渲染）');
}
console.log(JSON.stringify(out));
"""


def main():
    src = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
    parts, missing = [], []
    for fn in NEED:
        b = CP.extract_func(src, fn)
        (parts if b else missing).append(b if b else fn)
    if missing:
        print("[ERR] 抠不到函数：%s（改名了就同步改本检查器）" % ", ".join(missing))
        return 2
    if not subprocess.run(["which", "node"], capture_output=True).stdout:
        print("[ERR] 没有 node")
        return 2
    run = os.path.join(tempfile.gettempdir(), "mdashboard_bb_run.js")
    with open(run, "w", encoding="utf-8") as f:
        f.write("\n".join(parts) + "\n" + JS.replace("__DATA__", repr(os.path.join(HERE, "data.js"))))
    p = subprocess.run(["node", run], capture_output=True, text=True, timeout=180)
    try:
        res = json.loads(p.stdout or "{}")
    except ValueError:
        print("[FAIL] 渲染器没吐 JSON：%s" % (p.stderr or "")[:200])
        return 1
    if not res.get("ok"):
        print("[FAIL] 多空题材渲染不对：" + "；".join(res.get("errs", [])[:5]))
        return 1
    i = res.get("info") or {}
    if i.get("empty"):
        print("[OK] 多空题材今日无数据（空是常态，不生成卡片）")
    else:
        print("[OK] 多空题材渲染通过：利好 %d / 利空 %d 条，%d 字节" % (i["up"], i["dn"], i["len"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

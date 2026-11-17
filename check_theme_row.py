#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材行（连板梯队里的「题材」段）渲染闸门：把 lbThemeSecHtml 抠出来在 node 里真跑一次。

为什么需要（R100z356，2026-11-17）：
    自动成组的题材是新的一类条目（`auto=True` / `needConfirm=True` / `cands=[]`）。它出问题的方式
    全是「页面看着正常」：徽章没渲染（读者以为这是正式题材）、候选行空着（读者以为这题材没票）、
    组名取到 undefined（页面出现空白）。数据侧 G63 已经断言了不变量，但**没有任何一条闸门
    真的执行过这个函数**——这正是 R100z336 的教训（谐波折线在 setTimeout 里抛异常，
    K 线照常画、所有静态检查全 PASS）。

判据（rc=1 → FAIL）：
    ① 每个题材都渲染出一行 `.pre-th`；
    ② auto 的必须带 `<span class="pre-th-a">待确认</span>`，且 title 里有 why（不是空 tooltip）；
    ③ auto 的候选行必须写明「只列今日连板成员」，不许出现「池内同题材可补涨」；
    ④ 每行的连板成员数 == boardStocks 数（少一个就是有票被静默丢掉）；
    ⑤ 产出里不许出现 undefined / NaN / **（Markdown 星号漏进 HTML）。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from check_pend_table import extract_func  # noqa: E402  复用同一套大括号/正则字面量提取

INDEX = os.path.join(HERE, "index.html")
NEED_FUNCS = ("esc", "escAttr", "lbThemeSecHtml")

NODE = r"""
const fs = require('fs');
global.window = {}; global.document = {};
window.DASHBOARD_DATA = {};
eval(fs.readFileSync(__DATA__, 'utf8'));
function stkOpenIn() {}
const D = window.DASHBOARD_DATA || {};
const th = ((D.duanban || {}).themes) || {};
const h = lbThemeSecHtml(D.duanban || {});
const out = {ok: true, errs: [], info: {}};
function bad(m) { out.ok = false; out.errs.push(m); }

const rows = (h.match(/<div class="pre-th">/g) || []).length;
out.info.themes = Object.keys(th).length;
out.info.rows = rows;
if (rows !== Object.keys(th).length) {
  bad('渲染出 ' + rows + ' 行 ≠ 题材 ' + Object.keys(th).length + ' 个（有题材被丢掉）');
}
let autoN = 0, badge = 0, whyOk = 0, miss = 0, wrongLab = 0, undef = 0;
Object.keys(th).forEach(function (k) {
  const t = th[k] || {};
  const inLb = (t.boardStocks || []).length;
  // 每行以 `<div class="pre-th"><div class="pre-th-l1"><b>组名</b>` 开头，所以按「切块 + 含组名」取，
  // 不能要求 indexOf === 0（第一版这么写，于是每行都取到空串、四条断言全部误报）。
  const seg = h.split('<div class="pre-th">').filter(function (s) { return s.indexOf('<b>' + k + '</b>') >= 0; })[0] || '';
  const got = (seg.match(/class="pre-th-s"/g) || []).length;
  if (got !== inLb) { miss++; }
  if (t.auto) {
    autoN++;
    if (seg.indexOf('class="pre-th-a"') >= 0) badge++;
    if (/class="pre-th-a" title="[^"]{8,}"/.test(seg)) whyOk++;
    if (seg.indexOf('待确认题材') < 0) wrongLab++;
    if (seg.indexOf('池内同题材可补涨') >= 0) wrongLab++;
  }
});
out.info.auto = autoN;
if (miss) bad(miss + ' 个题材的连板成员数与 boardStocks 不符');
if (badge !== autoN) bad('待确认徽章 ' + badge + ' 个 ≠ auto 题材 ' + autoN + ' 个');
if (whyOk !== autoN) bad('待确认 tooltip 缺说明的 ' + (autoN - whyOk) + ' 个（读者不知道为什么待确认）');
if (wrongLab) bad(wrongLab + ' 个 auto 题材的候选行文案不对（应写「只列今日连板成员」，且不许出现「池内同题材可补涨」）');
['undefined', 'NaN', '>null<', '**'].forEach(function (t) {
  if (h.indexOf(t) >= 0) { undef++; bad('产出里出现字面量 ' + JSON.stringify(t)); }
});
process.stdout.write(JSON.stringify(out));
"""


def main():
    argv = sys.argv[1:]
    if "--help" in argv:
        print(__doc__)
        return 0
    if not os.path.exists(INDEX):
        print("[ERR] 找不到 index.html")
        return 2
    src = open(INDEX, encoding="utf-8").read()
    parts, missing = [], []
    for fn in NEED_FUNCS:
        body = extract_func(src, fn)
        (parts if body else missing).append(body if body else fn)
    if missing:
        print("[ERR] index.html 里抠不到函数：%s（改名了就同步改本检查器）" % ", ".join(missing))
        return 2
    node = subprocess.run(["which", "node"], capture_output=True, text=True).stdout.strip()
    if not node:
        print("[ERR] 没有 node，无法真跑渲染函数")
        return 2
    tdir = tempfile.gettempdir()
    fn_path = os.path.join(tdir, "mdashboard_theme_funcs.js")
    run_path = os.path.join(tdir, "mdashboard_theme_run.js")
    with open(fn_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    # 函数体必须与模板**拼在同一个文件里**：写到两个文件会让模板里的 lbThemeSecHtml
    # 变成 ReferenceError（本检查器第一版就是这么挂的，rc=1 但一条 errs 都没有）。
    with open(run_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts) + "\n" +
                NODE.replace("__DATA__", repr(os.path.join(HERE, "data.js"))))
    p = subprocess.run([node, run_path], capture_output=True, text=True, timeout=180)
    try:
        res = json.loads(p.stdout or "{}")
    except ValueError:
        print("[FAIL] 题材行渲染器没吐出 JSON（%s）" % (p.stderr or "")[:200])
        return 1
    if not res.get("ok"):
        print("[FAIL] 题材行渲染不对：")
        for e in res.get("errs", [])[:6]:
            print("  - " + e)
        return 1
    i = res.get("info") or {}
    print("[OK] 题材行 %s 行渲染通过（auto %s 个待确认徽章齐全、成员数与数据一致）"
          % (i.get("rows"), i.get("auto")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

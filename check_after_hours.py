#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘后·隔夜公告（ashare.afterHours）：渲染真跑 + 数据自洽（R100z357）。

为什么需要：
    这一节是「19:30 部署后到次日 07:00」之间唯一的 A 股出口，出问题的方式全是「看着正常」：
    条目渲染成空白、时间槽丢了（读者分不清昨晚还是今早）、抓不到时显示「暂无公告」
    （等于告诉读者「你的票昨晚没事」，而真相是源不可达）。所以两条都上机器闸：
      a) 数据层（audit G76）：n/items/slot/day/主板/covered 自洽；
      b) 渲染层（本脚本）：把 afterHoursHtml 抠出来在 node 里真调一次，断言 4 个 span、
         时间槽、degraded 的告警文案、无 undefined/NaN。

rc=2 表示检查器本身没跑成（node 缺 / 函数改名），此时**不代表干净**。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from check_pend_table import extract_func  # noqa: E402

INDEX = os.path.join(HERE, "index.html")
NEED_FUNCS = ("esc", "escAttr", "afterHoursHtml")

NODE = r"""
const fs = require('fs');
global.window = {}; global.document = {};
window.DASHBOARD_DATA = {};
eval(fs.readFileSync(__DATA__, 'utf8'));
function stkOpenIn() {}
const D = window.DASHBOARD_DATA || {};
const ah = (D.ashare || {}).afterHours;
const out = {ok: true, errs: [], info: {}};
function bad(m) { out.ok = false; out.errs.push(m); }

const h = afterHoursHtml(ah);
out.info.n = ah ? (ah.n || 0) : -1;
out.info.len = h.length;

if (!ah) {
  if (h !== '') { bad('没有 afterHours 时不该渲染任何东西（拿到 ' + h.length + ' 字符）'); }
} else if ((ah.items || []).length) {
  const rows = (h.match(/<div class="ah-item">/g) || []).length;
  if (rows !== (ah.items || []).length) {
    bad('渲染 ' + rows + ' 行 ≠ items ' + (ah.items || []).length + ' 条');
  }
  // 每行 4 个 span：时间槽 / 名称 / 标题（名称带 onclick 进 K 线）
  const seg = h.split('<div class="ah-item">').slice(1);
  seg.forEach(function (s, i) {
    if ((s.match(/<span /g) || []).length < 3) bad('第 ' + (i + 1) + ' 行 span 少于 3 个（时间/名称/标题缺一）');
    if (s.indexOf('stkOpenIn(') < 0) bad('第 ' + (i + 1) + ' 行的名称不可点（应接 stkOpenIn）');
  });
  if (h.indexOf('盘后') < 0 && h.indexOf('早间') < 0) bad('时间槽没渲染（读者分不清昨晚还是今早）');
  if (h.indexOf('时间只到日') < 0) bad('缺少「时间只到日」的口径说明');
  if (h.indexOf('池内 · 昨夜公告') < 0) bad('标题不对（应写「池内 · 昨夜公告」——它是个股公告，不是全球要闻）');
  if (h.indexOf('全球要闻') >= 0) bad('文案里出现「全球要闻」——这块是个股公告，挂在要闻页就是错位');
  if (h.indexOf('截至') < 0) bad('缺少「截至 HH:MM」的时效标注');
} else if (ah.degraded) {
  if (h.indexOf('未取到公告') < 0) bad('degraded 时必须写「未取到公告」，不能留空或写「暂无公告」');
  if (h.indexOf('暂无公告') >= 0) bad('degraded 时禁止写「暂无公告」（那是把抓取失败说成没有新闻）');
}

['undefined', 'NaN', '>null<'].forEach(function (t) {
  if (h.indexOf(t) >= 0) bad('产出里出现字面量 ' + JSON.stringify(t) + '（拼接漏值）');
});

// 反向：抓到了却在页面上说「暂无公告」也是错
if (ah && (ah.items || []).length && h.indexOf('暂无公告') >= 0) {
  bad('有 ' + ah.items.length + ' 条公告却写着「暂无公告」');
}
process.stdout.write(JSON.stringify(out));
"""


def main():
    argv = sys.argv[1:]
    if "--help" in argv:
        print(__doc__)
        return 0
    # --data 让埋雷/排障可以指向一份改造过的副本（默认就是项目 data.js）
    data_path = os.path.join(HERE, "data.js")
    for i, a in enumerate(argv):
        if a.startswith("--data="):
            data_path = a.split("=", 1)[1]
        elif a == "--data" and i + 1 < len(argv):
            data_path = argv[i + 1]
    if not os.path.exists(INDEX):
        print("[ERR] 找不到 index.html")
        return 2
    src = open(INDEX, encoding="utf-8").read()
    parts, missing = [], []
    for fn in NEED_FUNCS:
        b = extract_func(src, fn)
        (parts.append(b) if b else missing.append(fn))
    if missing:
        print("[ERR] index.html 里抠不到函数：%s（改名了就同步改本检查器）" % ", ".join(missing))
        return 2
    node = subprocess.run(["which", "node"], capture_output=True, text=True).stdout.strip()
    if not node:
        print("[ERR] 没有 node，无法真跑渲染函数")
        return 2
    tdir = tempfile.gettempdir()
    run_path = os.path.join(tdir, "mdashboard_afterhours_run.js")
    with open(run_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts) + "\n" +
                NODE.replace("__DATA__", repr(data_path)))
    p = subprocess.run([node, run_path], capture_output=True, text=True, timeout=120)
    try:
        res = json.loads(p.stdout or "{}")
    except ValueError:
        print("[FAIL] afterHoursHtml 没吐出 JSON：%s" % (p.stderr or "")[:200])
        return 1
    if not res.get("ok"):
        print("[FAIL] 盘后·隔夜渲染不对：")
        for e in res.get("errs", [])[:6]:
            print("  - " + e)
        return 1
    i = res.get("info") or {}
    print("[OK] 盘后·隔夜 %s 条渲染通过（时间槽/可点名称/时效标注齐全）" % i.get("n"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

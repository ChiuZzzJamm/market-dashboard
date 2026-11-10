#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_hz_overlay.py — 谐波 X→A→B→C→D 折线覆盖层闸门（R100z336，2026-11-10）。

**为什么需要它**（用户 23:29 截图「k线上的谐波线怎么没了」）：
弹窗 K 线里的红线是 `_dbk_hzGraphic()` 用 ECharts `graphic` 像素层画的，
里面给字母标注拼字体串时引用了 `FONT` —— 而 `FONT` 只是 `barOption()` 里的局部变量，
全局并不存在。于是 `_dbk_hzGraphic` 在 `setOption` 之前就抛 `ReferenceError: FONT is not defined`：
  · K 线本身由前一步 `setOption(_dbk_option(...))` 画好了 → 蜡烛、均线、PRZ/止损/T1T2 全在；
  · 折线整条消失（只是覆盖层，看起来像「形态没了」，不像报错）；
  · 错误发生在 `setTimeout` 里，外层 try/catch 接不到，控制台没有痕迹；
  · `node --check`（语法合法）、`check_inline_js`（语法闸门）、`audit_pipeline` 全部 PASS。

一次「把写死的 sans-serif 改成全站字体栈」的清理，就让一个纯前端可视功能静默下线，
而没有任何闸门拦得住 —— 因为所有闸门看的都是**数据**和**字符串有没有出现**，
没有一条真的**把绘制函数跑一遍**。本闸就是补这一层：把函数从 index.html 抠出来，
用最小 stub 在 node 里执行一次，断言它确实产出了折线 + 5 个圆点 + 5 个字母。

判据（FAIL 级，确定性、离线、无浏览器、无网络）：
  1. `_dbk_hzGraphic` 必须产出恰好一次 graphic 设置；
  2. 子元素必须是 4 条 line + 5 个 circle + 5 个 text（X/A/B/C/D 各一）；
  3. 字母顺序必须是 X,A,B,C,D（顺序错了折线会连成乱线，视觉上像「形态变形」）；
  4. 每个字母的 style.font 必须是非空字符串且带 px 与字体名（这次就是这条抓的）；
  5. 坐标必须是有限数字（convertToPixel 拿不到坐标系时会返回 NaN → 画在画布外）。

用法：
    python3 check_hz_overlay.py            # 校验 index.html
    python3 check_hz_overlay.py --quiet    # 只在出错时打印（audit_pipeline 用）

退出码：0=通过；1=折线绘制有问题（FAIL）；2=环境问题（找不到 node / 函数被改名）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(HERE, "index.html")

# 参与本次执行的最小函数集合（都是从 index.html 顶层抠出来的独立函数）
NEED = ["cssVar", "cssFont", "_dbk_hzPixels", "_dbk_hzGraphic"]


def find_node():
    """与 check_inline_js.py / deploy.sh 同一取法（三处不一致会导致闸门静默跳过）。"""
    p = shutil.which("node")
    if p:
        return p
    base = os.path.expanduser("~/.workbuddy/binaries/node/versions")
    if os.path.isdir(base):
        for d in sorted(os.listdir(base), reverse=True):
            cand = os.path.join(base, d, "bin", "node")
            if os.path.isfile(cand):
                return cand
    return None


def extract_fn(src, name):
    """抠出顶层函数源码：`^function name(` 到列 0 的 `}`。

    这些函数都是脚本顶层、缩进为 0 的写法，按「行首 }」截断比大括号计数可靠得多
    （计数要跳过字符串/注释/正则字面量，历史上正因如此误判过）。
    """
    m = re.search(r"(?m)^function\s+" + re.escape(name) + r"\s*\(([\s\S]*?)\n\}\n", src)
    if not m:
        return None
    return "function " + name + "(" + m.group(1) + "\n}\n"


HARNESS = r"""
/* ---- 最小 stub：只提供 cssFont/cssVar 需要的 --font，以及 chart 的 4 个方法 ---- */
global.window = global;
global.document = { documentElement: {} };
global.getComputedStyle = function () {
  return { getPropertyValue: function (n) {
    return n === '--font' ? '"Microsoft YaHei", "微软雅黑", "PingFang SC", sans-serif' : '';
  } };
};

%(fns)s

var calls = [];
var chart = {
  isDisposed: function () { return false; },
  // 与 ECharts convertToPixel 同签名：日期下标 → x 像素，价格 → y 像素（y 轴反向）
  convertToPixel: function (finder, v) { return [12 + v[0] * 10, 400 - (v[1] - 10) * 20]; },
  setOption: function (o) { calls.push(o); }
};

var kl = [], i;
for (i = 0; i < 40; i++) kl.push(['D' + i, 16.0, 16.2, 17.0, 15.8, 1000]);
var hz = {
  points:    { X: 17.26, A: 13.30, B: 15.47, C: 14.33, D: 16.58 },
  pointDays: { X: 'D5',   A: 'D12',  B: 'D20',  C: 'D28',  D: 'D35' }
};

var err = null;
try { _dbk_hzGraphic(chart, kl, hz); } catch (e) { err = String(e && e.message || e); }

var out = { err: err, sets: calls.length };
if (!err && calls.length) {
  var g = calls[0].graphic || [];
  var ch = (g[0] && g[0].children) || [];
  var by = function (t) { return ch.filter(function (x) { return x.type === t; }); };
  var texts = by('text'), lines = by('line'), circles = by('circle');
  out.groups = g.length;
  out.lines = lines.length;
  out.circles = circles.length;
  out.texts = texts.length;
  out.letters = texts.map(function (t) { return String((t.style || {}).text || ''); }).join('');
  out.fonts = texts.map(function (t) { return String((t.style || {}).font || ''); });
  out.badCoords = lines.length + circles.length > 0 && ch.some(function (x) {
    var s = x.shape || {}, v = [s.x1, s.y1, s.x2, s.y2, s.cx, s.cy].filter(function (z) {
      return z !== undefined;
    });
    return v.some(function (z) { return typeof z !== 'number' || !isFinite(z); });
  });
}
console.log(JSON.stringify(out));
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--file", default=HTML)
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print("❌ 找不到 %s" % args.file)
        return 2
    src = open(args.file, encoding="utf-8").read()

    fns, missing = [], []
    for name in NEED:
        code = extract_fn(src, name)
        if code is None:
            missing.append(name)
        else:
            fns.append(code)
    if missing:
        print("⚠️ index.html 里抠不到这些函数：%s —— 谐波折线覆盖层可能已改名或删除，"
              "闸门无法验证（不是通过）。" % "、".join(missing))
        return 2

    node = find_node()
    if not node:
        print("❌ 找不到 node —— 谐波折线覆盖层无法验证，拒绝「当作通过」放行。")
        return 2

    # 固定路径 + 覆盖写 + 永不删除（沙箱把 unlink 接管成「移到废纸篓」，mkstemp 会攒垃圾）
    tmp = os.path.join(tempfile.gettempdir(), "mdashboard_hz_overlay_check.js")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(HARNESS % {"fns": "\n".join(fns)})
    except OSError as e:
        print("❌ 探针脚本写不进 %s：%s" % (tmp, e))
        return 2

    try:
        p = subprocess.run([node, tmp], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        print("❌ 谐波折线探针超时（30s）")
        return 1
    if p.returncode != 0:
        print("❌ 探针执行失败：%s" % ((p.stderr or "").strip().splitlines() or ["rc=%s" % p.returncode])[-1])
        return 1
    try:
        d = json.loads((p.stdout or "").strip().splitlines()[-1])
    except Exception:                                        # noqa: BLE001
        print("❌ 探针输出无法解析：%s" % (p.stdout or "")[:200])
        return 1

    bad = []
    if d.get("err"):
        bad.append("绘制函数抛错（折线会整条消失，而 K 线本身照常）: %s" % d["err"])
    elif d.get("sets") != 1:
        bad.append("没有产出 graphic 设置（sets=%s）——覆盖层为空" % d.get("sets"))
    else:
        if d.get("lines") != 4:
            bad.append("折线段应为 4 条（X-A-B-C-D），实际 %s" % d.get("lines"))
        if d.get("circles") != 5:
            bad.append("圆点应为 5 个，实际 %s" % d.get("circles"))
        if d.get("texts") != 5:
            bad.append("字母标注应为 5 个，实际 %s" % d.get("texts"))
        if d.get("letters") != "XABCD":
            bad.append("字母顺序应为 XABCD，实际 %r（顺序错会让折线连成乱线）" % d.get("letters"))
        for f in (d.get("fonts") or []):
            if not re.match(r"^\s*\d+\s+[\d.]+px\s+\S", f):
                bad.append("字母字体串不合格 %r（canvas 不认 CSS 变量，必须是真实字体栈）" % f)
        # 全站唯一字体栈：字母必须与 --font 同源，不许各写一套
        if (d.get("fonts") or [""])[0] and "Microsoft YaHei" not in d["fonts"][0]:
            bad.append("字母字体没走 --font（实际 %r）" % d["fonts"][0])
        if d.get("badCoords"):
            bad.append("有坐标不是有限数字（convertToPixel 没拿到坐标系时会画到画布外）")

    if bad:
        print("❌ 谐波 X→A→B→C→D 折线覆盖层有问题：")
        for x in bad:
            print("   · " + x)
        return 1
    if not args.quiet:
        print("✅ 谐波折线覆盖层可绘制：4 段线 + 5 个圆点 + 字母 %s，字体取自 --font"
              % d.get("letters"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

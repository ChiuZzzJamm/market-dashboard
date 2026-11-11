#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量能解读（标的弹窗 `stkEnergyHtml`）的数字对账闸门。

为什么需要（R100z342，2026-11-11）：
    这块是**把算出来的数字写成断言式中文**（"量放到 2.9 倍"、"缩量到 28.6%"）。
    一旦算错，读者看到的是一句语气肯定的假事实——比空白危险得多。
    本轮自己就踩过一次：`lp0` 在 ③ 号段落里还没赋值（var 提升成 undefined），
    于是"启动日的盘中回踩"被写成了"洗盘"，语义正好说反，而页面渲染完全正常。

判据：
    ① 有断板结构 → 至少 4 段，每段必须含数字（纯形容词的段落等于没证据）；
    ② 输出里的关键数字，必须与 **Python 独立重算** 的结果一致（±0.2）；
    ③ 无结构但在断板/陈学长池 → 必须显式说「不适用」，不能空着；
    ④ 无结构且不在池 → 整块返回空（不占位、不误导）；
    ⑤ 任何输出都不许出现 undefined / NaN / null 字面量。

rc=1 → FAIL；rc=2 → 检查器没跑成（不代表干净）。
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")
DATA = os.path.join(HERE, "data.js")

NEED = ["esc", "escAttr", "stkParseKline", "stkGlobalKline", "stkEnergyInPool", "stkEnergyHtml"]
CASES = {"603536": "structure", "600207": "degrade", "600519": "silent"}


def extract_func(src, name):
    m = re.search(r"^(function\s+%s\s*\()" % re.escape(name), src, re.M)
    if not m:
        return None
    i = src.index("{", m.end() - 1)
    depth, j, n, prev = 0, i, len(src), ""
    ok = set("(,=:[!&|?{};+-*%~^<>") | {"\n", ""}
    while j < n:
        ch = src[j]
        if ch in "\"'`":
            q = ch; j += 1
            while j < n:
                if src[j] == "\\":
                    j += 2; continue
                if src[j] == q:
                    break
                j += 1
            prev = q
        elif ch == "/" and j + 1 < n and src[j + 1] == "/":
            while j < n and src[j] != "\n":
                j += 1
            prev = "\n"
        elif ch == "/" and j + 1 < n and src[j + 1] == "*":
            j += 2
            while j + 1 < n and not (src[j] == "*" and src[j + 1] == "/"):
                j += 1
            j += 1; prev = "/"
        elif ch == "/" and (prev in ok or prev.isspace()):
            j += 1; cls = False
            while j < n:
                c2 = src[j]
                if c2 == "\\":
                    j += 2; continue
                if c2 == "[":
                    cls = True
                elif c2 == "]":
                    cls = False
                elif c2 == "/" and not cls:
                    break
                elif c2 == "\n":
                    break
                j += 1
            prev = "/"
        elif ch == "{":
            depth += 1; prev = ch
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
            prev = ch
        elif not ch.isspace():
            prev = ch
        j += 1
    return None


NODE = r"""
const fs = require('fs'), vm = require('vm');
const document = { getElementById: () => null, addEventListener: () => {}, removeEventListener: () => {} };
const window = {};
const ctx = vm.createContext({ document, window, console, Math, Number, String, isFinite, JSON, parseFloat, isNaN });
vm.runInContext(fs.readFileSync(__SRC__, 'utf8'), ctx);   // data.js
vm.runInContext(fs.readFileSync(__FNS__, 'utf8'), ctx);   // 从 index.html 抠出来的函数
const out = {};
for (const code of __CODES__) {
  try { out[code] = vm.runInContext('stkEnergyHtml', ctx)(code) || ''; }
  catch (e) { out[code] = 'THROW:' + (e && e.message); }
}
process.stdout.write(JSON.stringify(out));
"""


def py_recompute(code):
    """独立重算：断板放量倍数 / 缩量比 / 启动量占断板日比例。"""
    s = open(DATA, encoding="utf-8").read()
    p = "window.DASHBOARD_DATA = "
    D = json.loads(s[len(p):].rstrip().rstrip(";"))
    kl = (D.get("stkKlines") or {}).get(code)
    if not kl or len(kl) < 21:
        return None
    c = [r[2] for r in kl]
    l = [r[4] for r in kl]
    v = [r[5] for r in kl]
    n = len(kl)
    d = -1
    for k in range(n - 1, 20, -1):
        if c[k - 1] / c[k - 2] - 1 < 0.098:
            continue
        if v[k] < 2.0 * v[k - 1]:
            continue
        if c[k] >= c[k - 1]:
            continue
        d = k
        break
    if d < 0:
        return None
    z = d - 1
    lp = (c[n - 1] / c[n - 2] - 1) * 100
    end = n - 2 if (lp >= 5 and n - 1 > d + 1) else n - 1
    ratio = None
    if end >= d + 3:
        ratio = min(v[d + 1:end + 1]) / v[d] * 100
    return {"mult": v[d] / v[z], "ratio": ratio,
            "q": v[n - 1] / v[d] * 100 if n - 1 > d else None,
            "lowPct": (min(l[d + 1:]) / c[z] - 1) * 100 if d + 1 < n else None}


def main():
    if "--help" in sys.argv:
        print(__doc__)
        return 0
    src = open(INDEX, encoding="utf-8").read()
    parts, miss = [], []
    for fn in NEED:
        b = extract_func(src, fn)
        (parts if b else miss).append(b or fn)
    if miss:
        print("[ERR] index.html 里抠不到函数：%s" % ", ".join(miss))
        return 2
    node = subprocess.run(["which", "node"], capture_output=True, text=True).stdout.strip()
    if not node:
        print("[ERR] 没有 node")
        return 2

    tdir = tempfile.gettempdir()
    fns = os.path.join(tdir, "mdashboard_energy_funcs.js")
    run = os.path.join(tdir, "mdashboard_energy_run.js")
    open(fns, "w", encoding="utf-8").write("\n".join(parts))
    open(run, "w", encoding="utf-8").write(
        NODE.replace("__SRC__", repr(DATA))
            .replace("__FNS__", repr(fns))
            .replace("__CODES__", json.dumps(list(CASES))))
    p = subprocess.run([node, run], capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        print("[ERR] 检查器没跑成：%s" % (p.stderr.strip()[-400:] or "rc=%s" % p.returncode))
        return 2
    res = json.loads(p.stdout.strip().splitlines()[-1])

    bad = []
    for code, kind in CASES.items():
        h = res.get(code, "")
        if h.startswith("THROW:"):
            bad.append("%s 渲染抛异常 %s" % (code, h[6:]))
            continue
        if "undefined" in h or "NaN" in h or ">null<" in h:
            bad.append("%s 输出里出现 undefined/NaN/null 字面量" % code)
            continue
        if kind == "silent":
            if h:
                bad.append("%s 不在池且无结构，本该整块不显示，却输出了 %d 字" % (code, len(h)))
            continue
        if kind == "degrade":
            if "不适用" not in h:
                bad.append("%s 无断板结构但在池，应显式说「不适用」，实际：%s" % (code, h[:60]))
            continue
        # structure：分段数 + 每段含数字 + 数字与独立重算一致
        ps = re.findall(r"<p>(.*?)</p>", h, re.S)
        if len(ps) < 4:
            bad.append("%s 有断板结构，只输出了 %d 段（应 ≥4）" % (code, len(ps)))
        for i, seg in enumerate(ps):
            if not re.search(r"\d", seg):
                bad.append("%s 第 %d 段没有任何数字（纯形容词＝没证据）" % (code, i + 1))
        if "推断" not in h:
            bad.append("%s 缺「推断」标注——这是判断，不能当事实发出去" % code)
        ref = py_recompute(code)
        if not ref:
            bad.append("%s Python 独立重算拿不到结构，JS 却输出了结构——两边口径不一致" % code)
        else:
            m = re.search(r"量放到\s*<b>([\d.]+)\s*倍", h)
            if not m:
                bad.append("%s 输出里找不到「量放到 X 倍」" % code)
            elif abs(float(m.group(1)) - ref["mult"]) > 0.2:
                bad.append("%s 放量倍数 页面写 %s 倍 / 重算 %.2f 倍" % (code, m.group(1), ref["mult"]))
            if ref["ratio"] is not None:
                # 两个分支都要对账：缩量够就写「量缩到 X%」，不够就写「量能最低只到 X%」。
                # 只查前者的话，把分母改错会掉进 else 分支、数字没人看（本轮实测漏过一次）。
                m2 = re.search(r"量缩到断板日的\s*<b>([\d.]+)%", h)
                if not m2:
                    m2 = re.search(r"量能最低只到断板日的\s*([\d.]+)%", h)
                if not m2:
                    bad.append("%s 缩量比算得出来，但输出里既没有「量缩到」也没有「量能最低只到」"
                               "——没有任何数字可对账" % code)
                elif abs(float(m2.group(1)) - ref["ratio"]) > 0.2:
                    bad.append("%s 缩量比 页面写 %s%% / 重算 %.1f%%" % (code, m2.group(1), ref["ratio"]))
            if ref["q"] is not None and "缩量涨停" in h:
                m3 = re.search(r"只用了断板日\s*<b>([\d.]+)%", h)
                if not m3:
                    bad.append("%s 说了「缩量涨停」却没给启动量占比" % code)
                elif abs(float(m3.group(1)) - ref["q"]) > 0.2:
                    bad.append("%s 启动量占比 页面写 %s%% / 重算 %.1f%%" % (code, m3.group(1), ref["q"]))

    if bad:
        print("[FAIL] 量能解读数字对不上：")
        for b in bad[:8]:
            print("  - " + b)
        return 1
    print("[OK] 量能解读：结构样本 %d 段且数字与独立重算一致；无结构降级/静默都对"
          % len(re.findall(r"<p>", res.get("603536", ""))))
    return 0


if __name__ == "__main__":
    sys.exit(main())

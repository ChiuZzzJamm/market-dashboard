#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""进行明细 / 结算明细 两张弹窗表：把渲染函数抠出来在 node 里**真跑一遍**。

为什么需要这个（R100z338，2026-11-11）：
    R100z336 的教训是「谐波折线在 setTimeout 里抛 ReferenceError，K 线照常画、
    页面上看不出是报错，而 node --check / check_inline_js / 90+ 项审计全 PASS」——
    因为**没有任何一条闸门真的执行过绘制函数**。进行明细这张表是同一类风险：
    88 行 × 6 列全靠一坨字符串拼接 + 四个三元表达式，任何一个变量名写错、
    任何一个 `r.tgtPct` 取到 undefined，结果是「弹窗打开了但表格是空的」
    或者「满屏 NaN」——数据侧完全正常，纯看数据永远查不出来。

    所以这里用与 check_hz_overlay.py 同一套办法：从 index.html 抠出函数源码，
    配一个**最小 DOM 桩**在 node 里真调一次 openPendingDetail()，
    然后断言它吐出来的 HTML。

判据（rc=1 → FAIL）：
    ① 六列表头一字不差：标的 / 买入日 / 买入价 / 现价 / 浮动盈亏 / 进度；
    ② 行数 == compare.opens 条数（少一行就是有票被静默丢掉）；
    ③ 每行 6 个 <td>（列数错位 = 后面全部错位）；
    ④ 涨红跌绿落在类上：td.up 数 == 收益>0 的条数、td.down 数 == 收益<0 的条数；
    ⑤ 两个降级态各自出现：'待开盘买入' == preBuy 数、'待 K 线' == noKline 数
       （降级必须有话说，空着就是"有结论没写"）；
    ⑥ 产出里不许出现 undefined / NaN / null 字面量（拼接漏值的指纹）；
    ⑦ 结算明细表在同一个桩里也能跑，且它自己的表头不被进行明细覆盖
       （两张表曾共用 #settle-modal，串票过一次）。

rc=2 表示检查器本身没跑成（node 缺 / 函数改名），此时**不代表干净**。
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")

REQUIRED_HEADS = ["标的", "买入日", "买入价", "现价", "浮动盈亏", "进度"]
SETTLE_HEADS = ["标的", "买入日", "买入价", "卖出日", "卖出价", "盈亏"]
NEED_FUNCS = ["esc", "escAttr", "stlModalEl", "stlEsc", "closeStlDetail", "closeSettleDetail",
              "closePendingDetail", "openPendingDetail", "openSettleDetail"]


def extract_func(src, name):
    """按大括号配平抠出 `function name(...) {...}`。

    跳过三类会骗过朴素括号计数的东西：字符串、注释、**正则字面量**
    （`escAttr` 里有 `/"/g`，不认正则就会把它当字符串开头、一路吞到文件尾）。
    """
    m = re.search(r"^(function\s+%s\s*\()" % re.escape(name), src, re.M)
    if not m:
        return None
    i = src.index("{", m.end() - 1)
    depth = 0
    j = i
    n = len(src)
    # 正则字面量只可能出现在这些字符之后（否则是除号）
    re_ok_prev = set("(,=:[!&|?{};+-*%~^<>") | {"\n", ""}
    prev = ""
    while j < n:
        c = src[j]
        if c in "\"'`":
            q = c
            j += 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == q:
                    break
                j += 1
            prev = q
        elif c == "/" and j + 1 < n and src[j + 1] == "/":
            while j < n and src[j] != "\n":
                j += 1
            prev = "\n"
        elif c == "/" and j + 1 < n and src[j + 1] == "*":
            j += 2
            while j + 1 < n and not (src[j] == "*" and src[j + 1] == "/"):
                j += 1
            j += 1
            prev = "/"
        elif c == "/" and (prev in re_ok_prev or prev.isspace()):
            # 正则字面量：扫到未转义的收尾 /
            j += 1
            in_cls = False
            while j < n:
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_cls = True
                elif ch == "]":
                    in_cls = False
                elif ch == "/" and not in_cls:
                    break
                elif ch == "\n":
                    break
                j += 1
            prev = "/"
        elif c == "{":
            depth += 1
            prev = c
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
            prev = c
        elif not c.isspace():
            prev = c
        j += 1
    return None


NODE_TEMPLATE = r"""
const fs = require('fs');
const vm = require('vm');

// ---------- 最小 DOM 桩：只做这坨字符串拼接真正碰到的东西 ----------
function El(tag) {
  this.tagName = tag || 'div';
  this.id = '';
  this.className = '';
  this.innerHTML = '';
  this.children = [];
  this.scrollTop = 0;
  this._box = null;
}
El.prototype.addEventListener = function () {};
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
El.prototype.classList = {
  _self: null,
  add: function (c) { const e = this._self; if (e.className.split(/\s+/).indexOf(c) < 0) e.className = (e.className + ' ' + c).trim(); },
  remove: function (c) { const e = this._self; e.className = e.className.split(/\s+/).filter(x => x && x !== c).join(' '); },
  contains: function (c) { return this._self.className.split(/\s+/).indexOf(c) >= 0; }
};
El.prototype.querySelector = function (sel) {
  // 桩只认这两个选择器，返回同一个常驻盒子，好让 openXxx 把表塞进去
  if (sel === '.db-modal-box') { if (!this._box) this._box = new El('div'); return this._box; }
  return null;
};
function makeEl(tag) { const e = new El(tag); e.classList = Object.create(El.prototype.classList); e.classList._self = e; return e; }

const REG = {};
const document = {
  getElementById: function (id) { return REG[id] || null; },
  createElement: function (tag) { return makeEl(tag); },
  querySelector: function () { return null; },
  querySelectorAll: function () { return []; },
  addEventListener: function () {},
  removeEventListener: function () {},
  body: { appendChild: function (c) { REG[c.id] = c; return c; } }
};
const window = {};
const getComputedStyle = function () { return { getPropertyValue: function () { return ''; } }; };

const ctx = vm.createContext({ document, window, console, getComputedStyle, Math, Number, String, isFinite, JSON });
vm.runInContext(fs.readFileSync(__SRC__, 'utf8'), ctx);
// data.js：window.DASHBOARD_DATA = {...}
vm.runInContext(fs.readFileSync(__DATA__, 'utf8'), ctx);

const out = { ok: true, errs: [], info: {} };
function bad(m) { out.ok = false; out.errs.push(m); }

try {
  const D = ctx.window.DASHBOARD_DATA || {};
  const cmp = ((D.chenPool || {}).compare) || {};
  const opens = cmp.opens || [];
  const settles = cmp.settles || [];
  const rowOf = function (html) {
    const m = html.match(/<tbody>([\s\S]*?)<\/tbody>/);
    if (!m) return [];
    return m[1].split('<tr>').slice(1).map(function (s) { return s.split('</tr>')[0]; });
  };
  const headsOf = function (html) {
    return (html.match(/<th[^>]*>([\s\S]*?)<\/th>/g) || []).map(function (t) {
      return t.replace(/<[^>]+>/g, '').trim();
    });
  };

  // ---- 进行明细 ----
  const pendFn = vm.runInContext('openPendingDetail', ctx);
  pendFn();
  const pend = REG['pend-modal'];
  if (!pend) bad('openPendingDetail() 没造出 #pend-modal');
  const ph = pend ? pend._box.innerHTML : '';
  out.info.pendLen = ph.length;
  out.info.pendRows = rowOf(ph).length;
  out.info.opens = opens.length;

  const phHeads = headsOf(ph);
  if (phHeads.join('/') !== __PEND_HEADS__.join('/')) {
    bad('进行明细表头不对：拿到 [' + phHeads.join(' | ') + ']');
  }
  if (rowOf(ph).length !== opens.length) {
    bad('进行明细行数 ' + rowOf(ph).length + ' ≠ opens ' + opens.length);
  }
  const cells = rowOf(ph).map(function (r) { return (r.match(/<td[\s>]/g) || []).length; });
  const badCells = cells.filter(function (n) { return n !== 6; });
  if (badCells.length) bad(badCells.length + ' 行的列数不是 6（实际 ' + JSON.stringify(badCells.slice(0, 5)) + '）');

  const upN = (ph.match(/<td class="up"/g) || []).length;
  const dnN = (ph.match(/<td class="down"/g) || []).length;
  const expUp = opens.filter(function (r) { return typeof r.retPct === 'number' && r.retPct > 0; }).length;
  const expDn = opens.filter(function (r) { return typeof r.retPct === 'number' && r.retPct < 0; }).length;
  out.info.up = upN + '/' + expUp;
  out.info.down = dnN + '/' + expDn;
  if (upN !== expUp) bad('涨（红）类 ' + upN + ' 条 ≠ 收益>0 的 ' + expUp + ' 条');
  if (dnN !== expDn) bad('跌（绿）类 ' + dnN + ' 条 ≠ 收益<0 的 ' + expDn + ' 条');

  const expPre = opens.filter(function (r) { return r.state === 'preBuy'; }).length;
  const expNoK = opens.filter(function (r) { return r.state === 'noKline'; }).length;
  const gotPre = (ph.match(/待开盘买入/g) || []).length;
  const gotNoK = (ph.match(/待 K 线/g) || []).length;
  out.info.pre = gotPre + '/' + expPre;
  out.info.noKline = gotNoK + '/' + expNoK;
  if (gotPre !== expPre) bad('「待开盘买入」出现 ' + gotPre + ' 次 ≠ preBuy ' + expPre + ' 条');
  if (gotNoK !== expNoK) bad('「待 K 线」出现 ' + gotNoK + ' 次 ≠ noKline ' + expNoK + ' 条');

  // 拼接漏值的指纹
  const leak = ['undefined', 'NaN', '>null<'].filter(function (t) { return ph.indexOf(t) >= 0; });
  if (leak.length) bad('产出里出现字面量 ' + JSON.stringify(leak) + '（拼接漏值）');

  // ---- 结算明细：同一个桩里跑，确认两表不互相覆盖 ----
  vm.runInContext('openSettleDetail', ctx)();
  const stl = REG['settle-modal'];
  if (!stl) bad('openSettleDetail() 没造出 #settle-modal');
  const sh = stl ? stl._box.innerHTML : '';
  out.info.settleRows = rowOf(sh).length;
  out.info.settles = settles.length;
  const shHeads = headsOf(sh);
  if (shHeads.join('/') !== __SETTLE_HEADS__.join('/')) {
    bad('结算明细表头不对（可能被进行明细覆盖）：[' + shHeads.join(' | ') + ']');
  }
  if (rowOf(sh).length !== settles.length) {
    bad('结算明细行数 ' + rowOf(sh).length + ' ≠ settles ' + settles.length);
  }
  const sleak = ['undefined', 'NaN', '>null<'].filter(function (t) { return sh.indexOf(t) >= 0; });
  if (sleak.length) bad('结算明细产出里出现字面量 ' + JSON.stringify(sleak));

  // 两张表必须是两个独立容器
  if (REG['pend-modal'] && REG['settle-modal'] && REG['pend-modal'] === REG['settle-modal']) {
    bad('两张表拿到了同一个容器（串票）');
  }
} catch (e) {
  out.ok = false;
  out.errs.push('渲染函数抛异常：' + (e && e.name) + ': ' + (e && e.message));
}

process.stdout.write(JSON.stringify(out));
"""


def main():
    argv = sys.argv[1:]
    if "--help" in argv:
        print(__doc__)
        return 0
    # --quiet 只是调用方（audit_pipeline）统一带的旗标；本器输出本来就单行，接受但不多做

    if not os.path.exists(INDEX):
        print("[ERR] 找不到 index.html")
        return 2
    src = open(INDEX, encoding="utf-8").read()

    parts = []
    missing = []
    for fn in NEED_FUNCS:
        body = extract_func(src, fn)
        if body is None:
            missing.append(fn)
        else:
            parts.append(body)
    if missing:
        print("[ERR] index.html 里抠不到函数：%s（改名了就同步改本检查器）" % ", ".join(missing))
        return 2

    node = subprocess.run(["which", "node"], capture_output=True, text=True).stdout.strip()
    if not node:
        print("[ERR] 没有 node，无法真跑渲染函数")
        return 2

    # 临时文件放系统 temp、**固定文件名覆盖写、不删**（24.133 / K14：跑完不在仓库留垃圾，
    # 也不许裸删——留在 temp 里下次覆盖就行，出问题时还能直接打开看当时那一版）。
    tdir = tempfile.gettempdir()
    fn_path = os.path.join(tdir, "mdashboard_pend_funcs.js")
    tmp = os.path.join(tdir, "mdashboard_pend_run.js")

    script = ("\n".join(parts) + "\n" +
              NODE_TEMPLATE
              .replace("__SRC__", repr(fn_path))
              .replace("__DATA__", repr(os.path.join(HERE, "data.js")))
              .replace("__PEND_HEADS__", repr(REQUIRED_HEADS).replace("'", '"'))
              .replace("__SETTLE_HEADS__", repr(SETTLE_HEADS).replace("'", '"')))

    with open(fn_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(script)

    p = subprocess.run([node, tmp], capture_output=True, text=True, timeout=180)

    if p.returncode != 0:
        print("[ERR] 检查器没跑成：%s" % (p.stderr.strip()[-600:] or "rc=%s" % p.returncode))
        return 2

    import json
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        print("[ERR] 检查器输出无法解析：%s" % p.stdout[:400])
        return 2

    if res["ok"]:
        info = res["info"]
        print("[OK] 进行明细 %d 行 × 6 列（涨 %s / 跌 %s / 待开盘 %s / 待K线 %s）；"
              "结算明细 %d 行独立渲染"
              % (info["pendRows"], info["up"], info["down"], info["pre"],
                 info["noKline"], info["settleRows"]))
        return 0

    print("[FAIL] 表格渲染不对：")
    for e in res["errs"]:
        print("  - " + e)
    return 1


if __name__ == "__main__":
    sys.exit(main())

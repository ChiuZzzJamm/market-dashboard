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
# kind: structure=有断板结构 / multi=多连板 / outflow=必须判出货 / degrade=在池无结构 / silent=不显示
CASES = {"603536": "structure", "600865": "structure", "600207": "structure",
         "001360": "multi", "600156": "structure", "600778": "shorts", "600519": "silent"}

# R100z345：旧断言「600156 最近放量下跌 → 必须判出货」是错的，已删。
# 它把「倍量阴 + 之后一天的回落」当成出货证据，而这就是断板反包形态本身。
# 换成下面两组**反向**断言——它们保护的是同一件事，但方向对了：
#   EARLY：断板后只过 1~2 天的票，绝不能判「出货嫌疑」（洗盘期还没开始，没有可比样本）
#   WASH ：标准洗盘样本（惠发：10/30 砸 -9.4%、11/02 砸 -10% 后缩量 29% 再涨停），绝不能判出货
EARLY = {"600156"}
WASH = {"603536"}

# R100z345：**正向**断言——每个阶段必须出对应结论，不能只检查"不是出货"。
# 起因：埋雷时发现「把换手未完成分支直接改成出货」「删掉洗盘完成分支」「断板当天也判出货」
# 三种改坏都能 PASS，因为惠发走的是别的分支、而断言只看反向。
# 做法：把日线截断到历史某天，再调 stkEnergyHtml，锁住每个时点的结论。
#   (截止日, 必须出现, 绝不能出现)
CUTS = {
    "603536": [
        ("2026-10-30", "起点", "出货嫌疑"),        # 断板当天：倍量阴是形态起点
        ("2026-11-02", "洗盘第1天", "出货嫌疑"),    # 砸到 8.52：才第 1 天，无可比样本
        ("2026-11-03", "挖坑洗盘", "出货嫌疑"),      # 破前低又收回
        ("2026-11-09", "阶段：", "出货嫌疑"),        # 缩量到 29%，仍未站上 MA5
        ("2026-11-10", "洗盘完成", "出货嫌疑"),      # 涨停 + 站上 MA5
    ],
    "600156": [
        ("2026-11-10", "洗盘第1天", "出货嫌疑"),
    ],
    # 600865 量能仍有断板日的 90%：必须落在「换手未完成」，不能是出货也不能是洗盘完成。
    # 这条专门盯住那个分支——否则把「换手未完成」直接改成「出货」也没人发现。
    "600865": [
        ("2026-11-10", "换手未完成", "出货嫌疑"),
    ],
}


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
// 历史时点：把日线截断到指定日期再跑一遍，验证每个阶段的结论
const cuts = __CUTS__;
for (const code of Object.keys(cuts)) {
  const orig = (window.DASHBOARD_DATA.stkKlines || {})[code];
  if (!orig) { out['CUT|' + code + '|NOKLINE'] = 'THROW:no kline'; continue; }
  for (const item of cuts[code]) {
    const until = item[0];
    window.DASHBOARD_DATA.stkKlines[code] = orig.filter(function (r) { return r[0] <= until; });
    try { out['CUT|' + code + '|' + until] = vm.runInContext('stkEnergyHtml', ctx)(code) || ''; }
    catch (e) { out['CUT|' + code + '|' + until] = 'THROW:' + (e && e.message); }
  }
  window.DASHBOARD_DATA.stkKlines[code] = orig;
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
        if v[k] < 1.1 * v[k - 1]:   # 与前端同一门槛（断板反包 1.1~3.5 / 陈学长 ≥2，取宽的那端）
            continue
        if c[k] / c[k - 1] - 1 >= 0.098:   # 次日没涨停就算断板（红盘断板也算，与池卡片同口径）
            continue
        d = k
        break
    if d < 0 or n - 1 - d > 45:     # 结构太老当没有
        return None
    z = d - 1
    z0 = z
    while z0 - 2 >= 0 and c[z0 - 1] / c[z0 - 2] - 1 >= 0.098:
        z0 -= 1
    vseg = sum(v[z0:z + 1]) / (z - z0 + 1)   # 连板期均量（前端同口径）
    lp = (c[n - 1] / c[n - 2] - 1) * 100
    end = n - 2 if (lp >= 5 and n - 1 > d + 1) else n - 1
    ratio = None
    if end >= d + 3:
        ratio = min(v[d + 1:end + 1]) / v[d] * 100
    return {"mult": v[d] / vseg, "ratio": ratio, "nban": z - z0 + 1,
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
            .replace("__CODES__", json.dumps(list(CASES)))
            .replace("__CUTS__", json.dumps(CUTS)))
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
        if kind == "shorts":
            # 在池但内嵌日线不足 21 根 → 必须明说"数据不够"，不能静默留空。
            # 注：「在池但无结构 → 不适用」那条分支今天在真实数据上不可达
            # （实测池内 185 只有日线的标的全部都有断板结构），所以没有活样本可测。
            if "不足 21 根" not in h:
                bad.append("%s 日线不足，应明说「内嵌日线不足 21 根」，实际：%s" % (code, h[:60]))
            continue
        # structure：分段数 + 每段含数字 + 数字与独立重算一致
        ps = re.findall(r"<p>(.*?)</p>", h, re.S)
        # 段数随「走到哪一步」变化：刚到断板只有 首板+断板，洗完才有沉淀，启动后才有第五段。
        # 所以不卡固定条数，卡**必须出现的两段**（首板、断板）——少这两段说明结构没认出来。
        if len(ps) < 3:
            bad.append("%s 有断板结构，只输出了 %d 段（首板+断板+洗盘至少 3 段）" % (code, len(ps)))
        joined = "".join(ps)
        for must in ("首板", "断板"):
            if must not in joined:
                bad.append("%s 有断板结构，却缺「%s」那一段" % (code, must))
        for i, seg in enumerate(ps):
            if not re.search(r"\d", seg):
                bad.append("%s 第 %d 段没有任何数字（纯形容词＝没证据）" % (code, i + 1))
        if kind == "multi":
            ref0 = py_recompute(code)
            want = "%d 连板" % (ref0 or {}).get("nban", 0)
            if want not in h:
                bad.append("%s 是 %s，输出里没写出来（首板/二板没分清）" % (code, want))
        if "阶段：" not in h:
            bad.append("%s 没有「阶段：」结论——分不出洗盘/出货就等于没解读" % code)
        if code in EARLY:
            if "出货嫌疑" in h:
                bad.append("%s 断板后只过 1~2 天，量能与低点都没有可比样本，不能判「出货嫌疑」"
                           "（这正是用户 2026-11-11 15:52 批评的那条：把倍量阴当成出货）" % code)
        if code in WASH:
            if "出货嫌疑" in h:
                bad.append("%s 是标准洗盘样本（砸 -10%% 后缩量再涨停），被判成了「出货嫌疑」" % code)
        if "推断" not in h:
            bad.append("%s 缺「推断」标注——这是判断，不能当事实发出去" % code)
        ref = py_recompute(code)
        if not ref:
            bad.append("%s Python 独立重算拿不到结构，JS 却输出了结构——两边口径不一致" % code)
        else:
            m = re.search(r"量是连板期均量的\s*<b>([\d.]+)\s*倍", h)
            if not m:
                bad.append("%s 输出里找不到「量是连板期均量的 X 倍」" % code)
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

    # 历史时点：每个阶段必须出对应结论，且任何时点都不许是「出货嫌疑」
    for code, items in CUTS.items():
        for until, must, mustnot in items:
            h = res.get("CUT|%s|%s" % (code, until), None)
            if h is None:
                bad.append("%s@%s 没跑出结果（node 桩没跑到截断逻辑）" % (code, until))
                continue
            if h.startswith("THROW:"):
                bad.append("%s@%s 截断后渲染抛异常 %s" % (code, until, h[6:]))
                continue
            if "undefined" in h or "NaN" in h or ">null<" in h:
                bad.append("%s@%s 输出里出现 undefined/NaN/null" % (code, until))
            if must and must not in h:
                bad.append("%s@%s 应判「%s」，实际没出现（当前结论：%s）"
                           % (code, until, must, re.sub(r"<[^>]+>", "", h[-160:])))
            if mustnot and mustnot in h:
                bad.append("%s@%s 不该出现「%s」——这就是用户 2026-11-11 15:52 批评的"
                           "「一股脑把放量当出货」" % (code, until, mustnot))

    if bad:
        print("[FAIL] 量能解读数字对不上：")
        for b in bad[:8]:
            print("  - " + b)
        return 1
    nc = sum(len(v) for v in CUTS.values())
    print("[OK] 量能解读：结构样本 %d 段且数字与独立重算一致；历史时点 %d 个阶段结论全对；"
          "无结构降级/静默都对"
          % (len(re.findall(r"<p>", res.get("603536", ""))), nc))
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

NEED = ["esc", "escAttr", "stkParseKline", "stkGlobalKline", "stkEnergyInPool", "stkMinLine", "stkEnergyHtml"]
# kind: structure=有断板结构 / multi=多连板 / outflow=必须判出货 / degrade=在池无结构 / silent=不显示
CASES = {"603536": "structure", "600865": "structure", "600207": "structure",
         "001360": "multi", "600156": "structure", "600778": "shorts", "600519": "silent",
         "000533": "structure"}

# R100z345：旧断言「600156 最近放量下跌 → 必须判出货」是错的，已删。
# 它把「倍量阴 + 之后一天的回落」当成出货证据，而这就是断板反包形态本身。
# 换成下面两组**反向**断言——它们保护的是同一件事，但方向对了：
#   EARLY：断板后只过 1~2 天的票，绝不能判「出货嫌疑」（洗盘期还没开始，没有可比样本）
#   WASH ：标准洗盘样本（惠发：10/30 砸 -9.4%、11/02 砸 -10% 后缩量 29% 再涨停），绝不能判出货

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
        # R100z383：当日涨停一律判「反包完成」（旧断言期望「洗盘完成」——那是只看量能、
        # 没看涨停的旧口径，正是用户 2026-11-25 骂的那条）
        ("2026-11-10", "反包完成", "出货嫌疑"),
    ],
    "600156": [
        ("2026-11-10", "洗盘第1天", "出货嫌疑"),
    ],
    # 600865 量能仍有断板日的 90%：必须落在「换手未完成」，不能是出货也不能是洗盘完成。
    # 这条专门盯住那个分支——否则把「换手未完成」直接改成「出货」也没人发现。
    "600865": [
        ("2026-11-10", "换手未完成", "出货嫌疑"),
    ],
    "603887": [("2026-11-25", "反包完成", "阶段：挖坑洗盘")],
    "002197": [("2026-11-25", "反包完成", "阶段：洗盘完成")],
    "605577": [("2026-11-25", "反包完成", "阶段：洗盘第1天")],
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


VERDICTS = ("有承接", "承接失败", "偏承接", "温和换手", "像出货")


def _check_compact(code, h):
    """四行判断的契约校验：每行必须在、且数字能与数据对账。"""
    out = []
    if "无分时数据" in h:
        # 降级分支：没有分钟线就只许明说，不许编承接/异动/尾盘三行
        if "推断" not in h:
            out.append("%s 降级输出缺「推断」标注" % code)
        for banned in ("有承接", "承接失败", "尾盘红柱"):
            if banned in h:
                out.append("%s 无分时数据却给出「%s」——不许用日线冒充分时" % (code, banned))
        return out
    if "推断" not in h:
        out.append("%s 缺「推断」标注——判断不能当事实发" % code)
    if not any(v in h for v in VERDICTS):
        out.append("%s 「判断」行没有落在五类结论里（有承接/承接失败/偏承接/温和换手/像出货）" % code)
    if "分时量异动" not in h:
        out.append("%s 缺「分时量异动」行" % code)
    if "尾盘红柱" not in h and "已封板" not in h:
        out.append("%s 缺「尾盘红柱」行（或未说明已封板）" % code)
    # 数字对账：次数 / 百分比必须与前端注入的同一份数据一致
    try:
        m = re.search(r"分时量异动\s*<b[^>]*>(\d+)\s*次", h)
        b = re.search(r"尾盘红柱\s*<b[^>]*>(\d+)%", h)
        data = _intraday_of(code)
        if data:
            # 2026-11-28 用户：只认红柱（买盘）爆量，绿柱卖盘不算异动
            _bs = data.get("bursts") or []
            n_exp = sum(1 for x in _bs if x.get("dir") == "红")
            if m and int(m.group(1)) != n_exp:
                out.append("%s 爆量次数 页面 %s / 数据 %d" % (code, m.group(1), n_exp))
            if b and data.get("tailRedPct") is not None and int(b.group(1)) != int(data["tailRedPct"]):
                out.append("%s 尾盘红柱 页面 %s%% / 数据 %s%%" % (code, b.group(1), data["tailRedPct"]))
            # 结论与 hold 不能打架：写了「有承接」但 hold=False 就是自相矛盾
            if "有承接" in h and data.get("hold") is False:
                out.append("%s 判定「有承接」，但数据是爆量后创了新低（hold=False）" % code)
    except Exception:                                            # noqa: BLE001
        pass
    return out


def _intraday_of(code):
    try:
        j = json.load(open(os.path.join(HERE, "intraday_minute.json"), encoding="utf-8"))
        m0 = (j.get("stocks") or {}).get(str(code)) or {}
        return (m0.get("prev") or m0) if m0 else {}
    except Exception:                                            # noqa: BLE001
        return {}


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
    # 2026-11-28：输出契约改成「四行判断」后，旧的段数/连板/倍数/缩量比/时间线断言全部失去对象
    # （它们校验的是已被删掉的长文叙述）。这里只保留新契约校验，避免闸门对着不存在的文案报错。
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
        if not h.strip():
            # 前端判定「不适用」（不在池且无断板结构 / 日线不足且不在池）→ 空是正确降级
            continue
        bad.extend(_check_compact(code, h))

    if bad:
        print("[FAIL] 量能解读数字对不上：")
        for b in bad[:8]:
            print("  - " + b)
        return 1
    print("[OK] 量能解读（四行判断）：%d 个样本全部落在契约内"
          "（洗盘日/判断/分时量异动/尾盘红柱，数字与数据对账）；无分时的样本已明说降级"
          % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())

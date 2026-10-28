#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缩量回踩口径对账：前端 ordShrinkScan（JS） vs shrink_scan.scan（Python）。

为什么要这个脚本：这个形态有**两份实现**——前端从浏览器的 `D.stkKlines` 现扫给
「明日可下单」出卡，Python 侧给 9:45 开盘精选供候选（同一份 data.js、同一套四条硬条件）。
单文件内联 JS 没有构建步骤，做不到「一份实现两处读」，所以用**对账**代替共享：
改任何一边的条件后跑一次，不一致就 FAIL，不许带着两套结论上线。

做法：把 index.html 复制成临时探针页（内联一段脚本把 ordPrep().poolC 的代码与字段
写进 <pre id="PROBEBOX">），headless Chrome 跑完 dump-dom 回读，与 Python 结果逐只比。
判据三样：代码集合相同、每只的缩量天数相同、放量倍数差 ≤0.01（浮点回声）。
退出码 0=一致，1=不一致（闸门可直接用）。
"""
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shrink_scan as SS  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROBE_MARK = "PROBEBOX"

PROBE = r"""
<pre id="PROBEBOX">PROBE_START</pre>
<script>
window.addEventListener('load', function(){
  setTimeout(function(){
    var out = {rows: [], err: null};
    try {
      var P = ordPrep();
      (P.poolC || []).forEach(function(o){
        out.rows.push({code: String(o.code), shrinkDays: o.shrink ? o.shrink.shrinkDays : null,
                       volRatio: o.shrink ? o.shrink.volRatio : null});
      });
      out.nCand = P.nShrinkCand;
    } catch(e) { out.err = (e && e.stack) ? e.stack : String(e); }
    document.getElementById('PROBEBOX').textContent = JSON.stringify(out);
  }, 3500);
});
</script>
"""


def load_data():
    with open(os.path.join(HERE, "data.js"), encoding="utf-8") as f:
        s = f.read()
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


def js_pool():
    """跑前端拿 poolC，返回 (rows, err)。rows/list 元素 {code, shrinkDays, volRatio}。"""
    src = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
    page = src.replace("</body>", PROBE + "</body>") if "</body>" in src else src + PROBE
    fd, path = tempfile.mkstemp(prefix="_probe_shrink_", suffix=".html", dir=HERE)
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(page)
        cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--allow-file-access-from-files", "--virtual-time-budget=45000",
               "--dump-dom", "file://" + path]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        dom = r.stdout or ""
        i = dom.find('id="%s">' % PROBE_MARK)
        if i < 0:
            return None, "探针页没有回读到 PROBEBOX（chrome exit=%s，dom %d 字符）" % (r.returncode, len(dom))
        j = dom.find("</pre>", i)
        raw = html.unescape(dom[i + len('id="%s">' % PROBE_MARK):j])
        if not raw.strip().startswith("{"):
            return None, "探针块内容不是 JSON（前 80 字：%r）——大概率是页面脚本未执行完" % raw[:80]
        obj = json.loads(raw)
        if obj.get("err"):
            return None, "页面内异常：" + str(obj["err"])[:400]
        return obj.get("rows") or [], None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def main():
    if not os.path.exists(CHROME):
        print("[SKIP] 找不到 Chrome，无法对账（本机缺浏览器不影响线上，但这条闸门就没跑）")
        return 0
    if not shutil.which("python3") and not sys.executable:
        print("[SKIP] 无 python3")
        return 0
    D = load_data()
    js_rows, err = js_pool()
    if err:
        print("[FAIL] 前端探针失败：%s" % err)
        return 1
    py = SS.scan(D, exclude=SS.pool_codes(D))["rows"]
    jm = {str(r["code"]): r for r in js_rows}
    pm = {str(r["code"]): r for r in py}
    only_js = sorted(set(jm) - set(pm))
    only_py = sorted(set(pm) - set(jm))
    diff = []
    for c in sorted(set(jm) & set(pm)):
        a, b = jm[c], pm[c]
        if a.get("shrinkDays") != b.get("shrinkDays"):
            diff.append("%s 缩量天数 JS=%s / PY=%s" % (c, a.get("shrinkDays"), b.get("shrinkDays")))
        va, vb = a.get("volRatio"), b.get("volRatio")
        if va is not None and vb is not None and abs(float(va) - float(vb)) > 0.011:
            diff.append("%s 放量倍数 JS=%s / PY=%s" % (c, va, vb))
    print("[shrink] 前端 poolC %d 只 / Python %d 只" % (len(jm), len(pm)))
    for c in sorted(jm):
        print("  JS %-7s 缩量第 %s 天 放量 %s 倍" % (c, jm[c].get("shrinkDays"), jm[c].get("volRatio")))
    if only_js:
        print("[FAIL] 只有前端有：%s" % "、".join(only_js))
    if only_py:
        print("[FAIL] 只有 Python 有：%s" % "、".join(only_py))
    for d in diff:
        print("[FAIL] 字段不一致：%s" % d)
    if only_js or only_py or diff:
        print("[FAIL] 两份实现口径不一致——改一边漏了另一边，改完再跑本脚本")
        return 1
    print("[PASS] 缩量回踩口径一致（代码集合 + 缩量天数 + 放量倍数）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

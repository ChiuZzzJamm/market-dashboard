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
import pathlib
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


def clean_legacy_probes():
    """清掉老版本留在项目根目录的探针页（R100z284）。

    老版本用 `tempfile.mkstemp(dir=HERE)` 把 722KB 的 index.html 副本写在**项目根目录**，
    靠 finally 里的 os.remove 收尾。本机 shell 里没问题，但在自动化沙箱里删除会被拦
    （os.remove 抛 OSError → 被 `except OSError: pass` 吞掉），残留文件随后被沙箱的
    收尾清理挪进废纸篓 —— 用户 2026-10-30 截图：一天 13 个 `_probe_shrink_*.html`、
    每个 723KB，全在废纸篓里躺着。

    探针页现在写系统临时目录（见 js_pool），这里只是把历史残渣顺手扫掉。
    删不掉不报错：外面已经有 .gitignore 兜底，不该为清理失败让对账闸门变红。
    """
    n = 0
    try:
        for fn in os.listdir(HERE):
            if fn.startswith("_probe_shrink_") and fn.endswith(".html"):
                try:
                    os.remove(os.path.join(HERE, fn))
                    n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n


def js_pool():
    """跑前端拿 poolC，返回 (rows, err)。rows/list 元素 {code, shrinkDays, volRatio}。"""
    src = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
    page = src.replace("</body>", PROBE + "</body>") if "</body>" in src else src + PROBE
    # R100z284：探针页不再落在项目目录，也**不再删除**。
    #
    # 两个坑叠在一起才逼出这个写法：
    #   ① 老版本把 722KB 的 index.html 副本写在项目根（`tempfile.mkstemp(dir=HERE)`），
    #      一天跑十几次，项目根随时躺着半打大文件；
    #   ② 更狠的是**删不掉**：自动化跑在沙箱里，`os.remove` 被安全删除机制接管成
    #      「移到废纸篓」（会话记录里能直接看到 `safeDeleteEvents: {operation:"trash"}`）。
    #      finally 里删一次 = 废纸篓多一个 722KB。用户 2026-10-30 截图：一天 13 个。
    #
    # 所以：固定路径 + 每次覆盖写 + 永不删除。系统临时目录由 macOS 自己轮转清理，
    # 用户视野里再也看不到它。并发安全靠「内容幂等」——两个进程写出的是同一份
    # index.html + 同一段 PROBE，谁后写都是同一个字节串。
    path = os.path.join(tempfile.gettempdir(), "mdashboard_probe_shrink.html")
    # index.html 里 `<script src="data.js?v=...">` 是相对路径，挪到临时目录就找不到数据，
    # 插一条 <base> 让相对 URL 仍按项目根解析（只影响相对 URL，不影响 location.hash 逻辑）。
    if "<base " not in page:
        page = page.replace("<head>", '<head>\n<base href="%s/">' % pathlib.Path(HERE).as_uri(), 1)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(page)
    except OSError as e:
        return None, "探针页写不进临时目录 %s：%s" % (path, e)
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


def main():
    if not os.path.exists(CHROME):
        print("[SKIP] 找不到 Chrome，无法对账（本机缺浏览器不影响线上，但这条闸门就没跑）")
        return 0
    if not shutil.which("python3") and not sys.executable:
        print("[SKIP] 无 python3")
        return 0
    n = clean_legacy_probes()
    if n:
        print("[clean] 清掉项目根目录的历史探针页 %d 个（R100z284：它们曾被挪进废纸篓）" % n)
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

#!/usr/bin/env python3
"""名字解析卫生检查：调用了不存在的名字 / 定义了没人调的名字。

为什么需要这一层（本机没有 pyflakes/flake8，也不该为它装依赖）：
  24.x 记过一条硬教训——「改工具模块函数名 = 改所有引用点（会 NameError 炸全链）」。
  这类错 py_compile / node --check 都拦不住（语法完全合法），也不是现有任何闸门的判据：
  脚本能启动，只是跑到那一行才炸；前端更糟——点进某个子页才炸，而闸门只测首页。
  2026-10-28 删掉 ④⑤ 两档后手工扫出 6 个前端死函数，说明这个方向平时没人看。

判据（宁可漏报，不许误报——闸门一旦爱叫就没人看它）：
  1. .py  调用了本文件从未绑定的名字 → FAIL。用 ast 判定，误报为 0（已实测 81 个文件）。
  2. .py  模块级函数全仓零引用       → WARN。
  3. 前端 `function X(` 全文零引用   → WARN。
     引用数必须在**整份 index.html** 里数：这些函数大量通过 onclick="X(1)" 这类
     HTML 属性被调用，只数 <script> 块内会把 9 个活函数误判成死函数（第一版就是这么错的）。
  4. 前端「调用了从未定义的函数」**故意不查**：没有真 JS 词法器就分不清
     `calc(...)`（在 style 字符串里）和 `foo(...)`（在代码里），实测原始源码误报 15 个、
     剥字符串又被 `/'/g` 这种正则字面量带偏。与其放一条爱叫的闸门，不如只做反向那半边：
     函数被改名而调用点没改时，旧名字会因「零引用定义」被抓到，新名字会因「无定义」在
     浏览器里报错——前端本来就有点开才炸的验收环节，这一层补的是「没人发现留了个死函数」。

用法：
  run-python check_undefined_calls.py            # 扫 *.py + index.html
  run-python check_undefined_calls.py --quiet    # 只出汇总（audit_pipeline 用）
退出码：有「调用了不存在的名字」→ 1；只有死代码 → 0。
"""
import ast
import builtins
import glob
import re
import sys

BUILTIN = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}


# ---------------------------------------------------------------- Python 侧
def py_bound(tree):
    """整模块所有被绑定过的名字（不区分作用域，故意放宽以免误报）。"""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
            if not isinstance(n, ast.ClassDef):
                a = n.args
                for x in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
                    out.add(x.arg)
                if a.vararg:
                    out.add(a.vararg.arg)
                if a.kwarg:
                    out.add(a.kwarg.arg)
        elif isinstance(n, ast.Lambda):
            a = n.args
            for x in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
                out.add(x.arg)
            if a.vararg:
                out.add(a.vararg.arg)
            if a.kwarg:
                out.add(a.kwarg.arg)
        elif isinstance(n, ast.Import):
            for al in n.names:
                out.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for al in n.names:
                out.add(al.asname or al.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.update(n.names)
        elif isinstance(n, ast.comprehension):
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    out.add(t.id)
    return out


def scan_py(path, src, allpy):
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [("FAIL", path, e.lineno or 0, "语法错误：%s" % e)]
    have = py_bound(tree) | BUILTIN
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id not in have:
            out.append(("FAIL", path, n.lineno, "调用了从未绑定的名字 %s" % n.func.id))
    for m in re.finditer(r"(?m)^def\s+([A-Za-z_]\w*)\s*\(", src):
        name = m.group(1)
        if name.startswith("__"):
            continue
        if len(re.findall(r"(?<![\w])" + re.escape(name) + r"(?![\w])", allpy)) <= 1:
            out.append(("WARN", path, src[:m.start()].count("\n") + 1,
                        "死函数 %s（全仓零引用）" % name))
    return out


# ---------------------------------------------------------------- 前端侧
def scan_html(path, src):
    """只查「定义在 script 块内、整份 HTML 里零引用」的函数（见文件头第 3、4 条）。"""
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", src)
    if not blocks:
        return []
    out = []
    for blk in blocks:
        for m in re.finditer(r"(?m)^\s*function\s+([A-Za-z_$][\w$]*)\s*\(", blk):
            name = m.group(1)
            if len(re.findall(r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])", src)) <= 1:
                out.append(("WARN", path, 0, "死函数 %s（全文零引用）" % name))
    return out


def main():
    quiet = "--quiet" in sys.argv
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not files:
        files = sorted(glob.glob("*.py")) + (["index.html"] if glob.glob("index.html") else [])
    allpy = "\n".join(open(x, encoding="utf-8", errors="replace").read()
                      for x in glob.glob("*.py"))
    items = []
    for f in files:
        src = open(f, encoding="utf-8", errors="replace").read()
        if f.endswith(".py"):
            items += scan_py(f, src, allpy)
        elif f.endswith((".html", ".htm")):
            items += scan_html(f, src)
    fails = [x for x in items if x[0] == "FAIL"]
    warns = [x for x in items if x[0] == "WARN"]
    if not quiet:
        for lv, path, ln, msg in items:
            print("[%s] %-26s L%-5d %s" % (lv, path, ln, msg))
    print("SUMMARY undef=%d dead=%d（扫 %d 个文件）" % (len(fails), len(warns), len(files)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

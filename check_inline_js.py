#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_inline_js.py — index.html 内联 JS 语法闸门（R100z205，2026-10-25）。

**为什么需要它**：`deploy.sh` 一直只校验 `node --check data.js`，从来不查 index.html
里那 30 万字符的内联 `<script>`。而页面白屏只有两种坏法：
  ① data.js 语法坏（已拦）；② index.html 的内联 JS 语法坏（**从来没拦过**）。
第二种更隐蔽：改一行渲染代码少写一个括号、删函数时多留一个 `}`，data.js 依旧完美，
audit_pipeline 75 项全绿（它查的是数据契约和字符串，不做 JS 解析），
check_code 的 K1 只 `py_compile` 和 `bash -n`、`node --check data.js`，
于是**闸门全绿 → 直接推上线 → 用户打开是白屏**，要等下一个自动化或人肉发现。

2026-10-25 那次删死函数（`newsTag`/`shortDate`）我是手工抽出来验的，验完才敢 deploy——
这种「靠人记得验」的环节不能留，必须变成闸门。

用法：
    python3 check_inline_js.py            # 校验 index.html，有错 exit 1
    python3 check_inline_js.py --quiet    # 只在出错时打印

退出码：0=全部通过（或页面里没有内联脚本）；1=有语法错；2=环境问题（找不到 node）。
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(HERE, 'index.html')

# 匹配「没有 src 属性的 <script>」：`<script src=...>` 是外链，语法由浏览器按 URL 取，
# 不归这里管。`(?![^>]*\bsrc=)` 保证不误抓外链标签。
BLOCK_RE = re.compile(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', re.S)


def find_node():
    """定位 node：优先 PATH，其次托管安装目录（自动化环境 PATH 常缺）。

    与 deploy.sh 的取法保持一致 —— 两处取法不一致的话，deploy 能找到 node
    而这里找不到，闸门会静默跳过（比没有闸门更糟：以为查过了）。
    """
    p = shutil.which('node')
    if p:
        return p
    base = os.path.expanduser('~/.workbuddy/binaries/node/versions')
    if os.path.isdir(base):
        for d in sorted(os.listdir(base), reverse=True):
            cand = os.path.join(base, d, 'bin', 'node')
            if os.path.isfile(cand):
                return cand
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--file', default=HTML)
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print(f'❌ 找不到 {args.file}')
        return 2
    src = open(args.file, encoding='utf-8').read()
    blocks = BLOCK_RE.findall(src)
    # 每个块在源文件中的起始行号（报错时能直接跳过去改，不用自己数）
    starts = [src[:m.start(1)].count('\n') + 1 for m in BLOCK_RE.finditer(src)]

    node = find_node()
    if not node:
        print('❌ 找不到 node —— 内联 JS 语法无法校验，拒绝「当作通过」放行。')
        return 2

    bad = 0
    checked = 0
    for i, (body, line) in enumerate(zip(blocks, starts)):
        if not body.strip():
            continue          # 空块（占位/注释）不送检
        checked += 1
        # R100z285：原来 `mkstemp` + `finally: os.unlink` —— 自动化沙箱把 unlink 接管成
        # 「移到废纸篓」（不报错、不真删），于是**每跑一次校验就多一个废纸篓垃圾**，
        # 和 722KB 探针页同源。改固定路径 + 覆盖写 + 永不删除：
        # 块是 for 循环串行处理的，同一时刻只有一个块在用这个文件。
        tmp = os.path.join(tempfile.gettempdir(), 'mdashboard_inline_js_check.js')
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(body)
        except OSError as e:
            print(f'❌ 内联 JS 临时文件写不进 {tmp}：{e} —— 无法校验，拒绝放行。')
            return 2
        r = subprocess.run([node, '--check', tmp], capture_output=True, text=True)
        if r.returncode != 0:
            bad += 1
            # node 的行号是「块内行号」，换算回 index.html 的行号才可点
            msg = [x for x in (r.stderr or '').strip().splitlines() if x.strip()]
            head = msg[0] if msg else '语法错误'
            m = re.search(r':(\d+)$', head)
            if m:
                head = (f'{os.path.basename(args.file)} 第 '
                        f'{line + int(m.group(1)) - 1} 行')
            print(f'❌ 内联 script 块 #{i + 1}（起于第 {line} 行）语法错 → {head}')
            for ln in msg[1:6]:
                print('     ' + ln.strip())

    if bad:
        print(f'❌ index.html 内联 JS 语法未通过（{bad}/{checked} 块有错）—— '
              f'推上线就是白屏，已阻断。')
        return 1
    if not args.quiet:
        print(f'✅ index.html 内联 JS 语法通过（{checked} 块 / '
              f'{sum(len(b) for b in blocks):,} 字符）')
    return 0


if __name__ == '__main__':
    sys.exit(main())

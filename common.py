#!/usr/bin/env python3
"""看板脚本共用工具：定位 node、把 data.js 解析为 JSON。
供 push_notify.py 与 update_us_from_quotes.py 复用，避免重复实现。
"""
import os, shutil, glob, json, subprocess, time


def http_get(url, timeout=15, retries=3, decode='utf-8'):
    """用 curl 抓取（urllib 会被部分源拒连）；失败返回 None。decode='gb2312' 用于腾讯行情。"""
    cmd = ['curl', '-s', '--max-time', str(timeout),
           '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)']
    for i in range(1, retries + 1):
        try:
            p = subprocess.run(cmd + [url], capture_output=True, timeout=timeout + 5)
        except Exception:
            p = None
        out = p.stdout if (p and p.returncode == 0) else b''
        if out.strip():
            return out.decode(decode, errors='replace')
        if i < retries:
            time.sleep(2)
    return None


def find_node():
    """优先 PATH，其次 workbuddy 管理的多版本目录，最后兜底系统路径。"""
    p = shutil.which('node')
    if p:
        return p
    cands = sorted(glob.glob('/Users/loccco/.workbuddy/binaries/node/versions/*/bin/node'))
    if cands:
        return cands[-1]
    for c in ['/usr/local/bin/node', '/usr/bin/node']:
        if os.path.exists(c):
            return c
    raise RuntimeError('node not found in PATH or known locations')


def load_dashboard_data(base):
    """用 node 把 data.js 解析为 Python dict。失败即抛异常。"""
    node = find_node()
    node_src = r'''
const fs = require('fs');
let s = fs.readFileSync('data.js','utf8');
let m = s.match(/window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!m) { console.error('data.js parse failed'); process.exit(1); }
process.stdout.write(JSON.stringify(eval('(' + m[1] + ')')));
'''
    p = subprocess.run([node, '-e', node_src], capture_output=True, text=True, cwd=base)
    if p.returncode != 0:
        raise RuntimeError('data.js parse failed: ' + p.stderr)
    return json.loads(p.stdout)

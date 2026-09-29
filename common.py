#!/usr/bin/env python3
"""看板脚本共用工具：定位 node、把 data.js 解析为 JSON。
供 push_notify.py 与 update_us_from_quotes.py 复用，避免重复实现。
"""
import os, shutil, glob, json, re, random, subprocess, time


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


# ---------- R100z6: 同花顺抓取统一收口 ----------
# 原 update_ashare_sectors.py（curl_ths_url/curl_ths/parse_ths_industries/fetch_ths_industries/curl_ths_json）
# 与 check_duanban.py（curl_ths_text/curl_ths_json/parse_ths_industries/fetch_ths_industries）双副本合一，
# 避免再出现「修了 A 副本漏了 B 副本」的口径漂移（R100z5d 90 行业分页即此教训）。
THS_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
THS_URL = "https://q.10jqka.com.cn/thshy/"
THS_REFERER = "https://q.10jqka.com.cn/"
THS_DATA_REFERER = "https://data.10jqka.com.cn/"
THS_DATACENTER_REFERER = "https://data.10jqka.com.cn/datacenterph/limitup/limtupInfo.html"


def ths_get(url, timeout=20):
    """同花顺页面抓取：curl + UA + Referer + Accept-Language。返回 bytes（失败 b""）。"""
    try:
        r = subprocess.run(["curl", "-s", "--max-time", str(timeout),
                            "-H", "User-Agent: " + THS_UA,
                            "-H", "Referer: " + THS_REFERER,
                            "-H", "Accept-Language: zh-CN,zh;q=0.9",
                            url], capture_output=True, timeout=timeout + 10)
    except Exception:
        return b""
    return r.stdout or b""


def ths_json(url, timeout=15, referer=THS_DATA_REFERER):
    """同花顺 JSON 接口：curl + Referer，失败返回 None。utf-8 失败回退 gbk 解码。"""
    try:
        r = subprocess.run(["curl", "-s", "--max-time", str(timeout),
                            "-H", "User-Agent: " + THS_UA,
                            "-H", "Referer: " + referer, url],
                           capture_output=True, timeout=timeout + 10)
    except Exception:
        return None
    out = r.stdout or b""
    if isinstance(out, (bytes, bytearray)):
        try:
            out = out.decode("utf-8")
        except UnicodeDecodeError:
            out = out.decode("gbk", errors="replace")
    if not isinstance(out, str) or not out.strip():
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def parse_ths_industries(html):
    """解析同花顺行业一览表。返回 [{code,name,pct,netInflow,up,down,lead,leadPct}]；失败/不足返回 []。
    列顺序（相对行业名所在列 ni）：ni+1 涨跌幅 / ni+4 净流入(亿) / ni+5 上涨家数 / ni+6 下跌家数
        / ni+8 领涨股 / ni+10 领涨股涨跌幅。涨跌幅做 [-15,15] 合理性校验防列偏移错位。
    （duanban 只用 code/name/pct 子集，兼容。）"""
    if isinstance(html, (bytes, bytearray)):
        # 同花顺行业页为 GBK 编码，utf-8 直接解会乱码；先试 utf-8 失败回退 gbk
        try:
            text = html.decode("utf-8")
        except UnicodeDecodeError:
            text = html.decode("gbk", errors="replace")
    else:
        text = str(html)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S)
    out = []
    for row in rows:
        m = re.search(r'thshy/detail/code/(\d+)[^>]*>([^<]+)</a>', row)
        if not m:
            continue
        code, name = m.group(1), m.group(2).strip()
        if not name:
            continue
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        tds = [re.sub(r"<[^>]+>", "", t).strip() for t in tds]
        ni = None
        for i, t in enumerate(tds):
            if t == name:
                ni = i
                break
        if ni is None or ni + 10 >= len(tds):
            continue
        def num(s):
            s = s.replace("%", "").replace("亿", "").replace("万", "").replace(",", "")
            try:
                return float(s)
            except Exception:
                return None
        pct = num(tds[ni + 1])
        if pct is None or pct < -15 or pct > 15:
            continue
        def i2(s):
            v = num(s)
            return int(v) if v is not None else None
        out.append({
            "code": code, "name": name, "pct": round(pct, 2),
            "netInflow": num(tds[ni + 4]),
            "up": i2(tds[ni + 5]),
            "down": i2(tds[ni + 6]),
            "lead": (tds[ni + 8] if ni + 8 < len(tds) else None),
            "leadPct": num(tds[ni + 10]),
        })
    return out


def fetch_ths_industries():
    """同花顺行业一览表分页抓全（第1页50 + 第2页40 = 90 行业；R98h/R100z5d 合一）。
    第2页起用非 ajax 整页 URL（/thshy/index/page/N/；ajax/1/ 会触发反爬跳转）。
    按 code 去重合并；第 1 页 <10 个视为异常原样返回（调用方自行走备源/兜底）。"""
    seen, out = set(), []
    for x in parse_ths_industries(ths_get(THS_URL)):
        if x["code"] not in seen:
            seen.add(x["code"])
            out.append(x)
    if len(out) < 10:
        return out
    for page in range(2, 4):  # 最多抓到第3页防死循环
        time.sleep(1.0 + random.random())
        rows = parse_ths_industries(ths_get(f"https://q.10jqka.com.cn/thshy/index/page/{page}/"))
        fresh = [x for x in rows if x["code"] not in seen]
        if not fresh:
            break
        for x in fresh:
            seen.add(x["code"])
            out.append(x)
        if len(rows) < 10:  # 不足一页说明已到尾页
            break
    return out

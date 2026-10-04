#!/usr/bin/env python3
"""个股当日涨跌幅批量查询（R100z11，供 verification.stockCheck 取数）。

用途：16:00 生成 AI 预测命中验证时，取「每个预测板块的 8 只标的当日真实涨跌幅」，
算出平均涨幅与方向一致率——把验证颗粒度从「板块方向」下移到标的层面
（方向说对了但 8 只全选错，账户照样亏）。

用法：
    python3 stock_quotes.py sh600000,sz000001,sz002230
    python3 stock_quotes.py --codes-file codes.txt
    python3 stock_quotes.py sh600000,sz000001 --json          # 只输出 JSON

输出（JSON）：{"ok":true,"quotes":{"sh600000":{"name":"…","price":12.3,"pct":1.87}},"failed":[]}
取不到价的标的记入 failed，**绝不返回 0 冒充**（0 会污染一致率）。

数据源：腾讯 qt.gtimg.cn（主）+ 新浪 hq.sinajs.cn（兜底）。
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')


def fetch_tencent(codes):
    """批量取（一次最多 60 只）。返回 {code: {'name','price','pct'}}。"""
    out = {}
    if not codes:
        return out
    for i in range(0, len(codes), 60):
        chunk = codes[i:i + 60]
        url = 'https://qt.gtimg.cn/q=' + ','.join(chunk)
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': 'https://gu.qq.com/'})
        raw = urllib.request.urlopen(req, timeout=12).read().decode('gbk', 'ignore')
        for line in raw.split(';'):
            line = line.strip()
            if '~' not in line:
                continue
            body = line.split('=', 1)[-1].strip().strip('"')
            f = body.split('~')
            if len(f) < 33:
                continue
            code = f[2]
            try:
                price = float(f[3])
                prev = float(f[4])
            except ValueError:
                continue
            if prev <= 0 or price <= 0:
                continue
            out[code] = {'name': f[1], 'price': round(price, 2),
                         'pct': round((price - prev) / prev * 100, 2)}
    return out


def fetch_sina(codes):
    out = {}
    if not codes:
        return out
    url = 'https://hq.sinajs.cn/list=' + ','.join(codes)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': 'https://finance.sina.com.cn'})
    raw = urllib.request.urlopen(req, timeout=12).read().decode('gbk', 'ignore')
    for line in raw.strip().split('\n'):
        if '="' not in line:
            continue
        left, right = line.split('="', 1)
        code = left.split('_')[-1]
        f = right.rstrip('";').split(',')
        if len(f) < 4:
            continue
        try:
            price, prev = float(f[3]), float(f[2])
        except ValueError:
            continue
        if prev <= 0 or price <= 0:
            continue
        out[code] = {'name': f[0], 'price': round(price, 2),
                     'pct': round((price - prev) / prev * 100, 2)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('codes', nargs='?', default='')
    ap.add_argument('--codes-file')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    codes = []
    if a.codes:
        codes = [c.strip() for c in a.codes.split(',') if c.strip()]
    if a.codes_file:
        codes += [l.strip() for l in open(a.codes_file, encoding='utf-8') if l.strip()]
    # 规范化：裸 6 位补市场前缀（6 开头→sh，其余→sz）
    norm = []
    for c in codes:
        c = c.strip()
        if len(c) == 6 and c.isdigit():
            c = ('sh' if c.startswith('6') else 'sz') + c
        norm.append(c)
    codes = norm

    quotes, failed = {}, []
    try:
        quotes = fetch_tencent(codes)
    except Exception as e:
        print('[WARN] 腾讯源失败：' + str(e)[:120], file=sys.stderr)
    for c in codes:
        if c not in quotes:
            failed.append(c)
    if failed:
        try:
            quotes_sina = fetch_sina(failed)
            for c, v in quotes_sina.items():
                quotes[c] = v
            still = [c for c in failed if c not in quotes]
            if still:
                print('[WARN] 新浪兜底仍失败：' + ','.join(still)[:120], file=sys.stderr)
        except Exception as e:
            print('[WARN] 新浪兜底失败：' + str(e)[:120], file=sys.stderr)
    failed = [c for c in codes if c not in quotes]

    res = {'ok': bool(quotes), 'quotes': quotes, 'failed': failed}
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        for c in codes:
            q = quotes.get(c)
            print(c, ('%s %.2f %+.2f%%' % (q['name'], q['price'], q['pct'])) if q else '取价失败')
    return 0


if __name__ == '__main__':
    sys.exit(main())

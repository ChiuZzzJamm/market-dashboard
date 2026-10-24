#!/usr/bin/env python3
"""个股当日涨跌幅批量查询（R100z11，供 verification.stockCheck 取数）。

用途：17:00 叙事步生成 AI 预测命中验证时，取「每个预测板块的 8 只标的当日真实涨跌幅」，
算出平均涨幅与方向一致率——把验证颗粒度从「板块方向」下移到标的层面
（方向说对了但 8 只全选错，账户照样亏）。

用法：
    python3 stock_quotes.py sh600000,sz000001,sz002230
    python3 stock_quotes.py --codes-file codes.txt
    python3 stock_quotes.py sh600000,sz000001 --json          # 只输出 JSON

输出（JSON）：{"ok":true,"quotes":{"sh600000":{"name":"…","price":12.3,"pct":1.87,...}},"failed":[]}
取不到价的标的记入 failed，**绝不返回 0 冒充**（0 会污染一致率）。

字段（R100z186 补齐，此前只解析 f[3]/f[4] 两个，其余 86 个字段白拿）：

    name/price/pct        名称/现价/涨跌幅（原有）
    open/prevClose        今开/昨收 —— 开盘跳空 = (open-prevClose)/prevClose，lead_lag 的唯一依据
    high/low              最高/最低
    volRatio              量比（f[49]）—— 「此刻的量能是否异常」唯一可得口径
    avgPrice              均价（f[51]）—— 现价在均价上/下 = 当日买方是否占优
    turnover              换手率%（f[38]）
    amplitude             振幅%（f[43]）

为什么必须补：原来只有 price/pct，导致「现涨+3%」这种数字无法判断是
**高开+3% 一路走弱**还是**低开被买到+3%**——两者是完全相反的信号。
R100z186 回测（132 个交易日 / 8.9 万样本）：追「高开且领涨」的 t=−2.8、
胜率 42%；买「强板块里还没涨的」t=+3.4、胜率 68%。没有 open 字段就分不开这两类。

数据源：腾讯 qt.gtimg.cn（主）+ 新浪 hq.sinajs.cn（兜底）。
新浪源无量比/均价字段，**缺失一律 None，绝不拿别的数顶替**。
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')


def _num(f, i):
    """安全取字段：空串/非法 → None（绝不补 0）。"""
    try:
        v = f[i]
    except IndexError:
        return None
    if v is None or v == '' or v == '-':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_tencent(codes):
    """批量取（一次最多 60 只）。返回 {code: {...}}。字段见模块 docstring。"""
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
            # ★键名必须与调用方传进来的形式一致（带 sh/sz 前缀）。
            # 原来直接拿 f[2]（纯 6 位）当键，而 codes 是 'sh600000' 形式 →
            # `c not in quotes` 恒真 → 每只都被判失败 → **永久走新浪兜底**，
            # 腾讯主力源等于从未生效（新浪无量比/均价/换手/振幅，于是这四项永远是空）。
            # R100z186 实测发现并修正：优先用响应里的 v_sh600000 符号名。
            _m = re.search(r'v_(sh|sz)(\d{6})=', line)
            code = (_m.group(1) + _m.group(2)) if _m else f[2]
            try:
                price = float(f[3])
                prev = float(f[4])
            except ValueError:
                continue
            if prev <= 0 or price <= 0:
                continue
            op = _num(f, 5)
            out[code] = {
                'name': f[1], 'price': round(price, 2),
                'pct': round((price - prev) / prev * 100, 2),
                'prevClose': round(prev, 2),
                # 今开缺失/为 0（停牌/未开盘）时留 None，别拿 price 冒充
                'open': round(op, 2) if op else None,
                'high': round(_num(f, 33), 2) if _num(f, 33) else None,
                'low': round(_num(f, 34), 2) if _num(f, 34) else None,
                'volRatio': round(_num(f, 49), 2) if _num(f, 49) is not None else None,
                'avgPrice': round(_num(f, 51), 2) if _num(f, 51) else None,
                'turnover': _num(f, 38),
                'amplitude': _num(f, 43),
                'amount': _num(f, 37),
            }
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
        if len(f) < 6:
            continue
        try:
            price, prev = float(f[3]), float(f[2])
        except ValueError:
            continue
        if prev <= 0 or price <= 0:
            continue
        out[code] = {
            'name': f[0], 'price': round(price, 2), 'prevClose': round(prev, 2),
            'pct': round((price - prev) / prev * 100, 2),
            'open': round(float(f[1]), 2) if f[1] and float(f[1]) > 0 else None,
            'high': round(float(f[4]), 2) if f[4] and float(f[4]) > 0 else None,
            'low': round(float(f[5]), 2) if f[5] and float(f[5]) > 0 else None,
            # 新浪无「量比/均价」——缺失即 None，不拿别的口径顶替
            'volRatio': None, 'avgPrice': None,
            'turnover': None, 'amplitude': None, 'amount': None,
        }
    return out


def open_gap(q):
    """开盘跳空（%）—— lead_lag 与所有「高开还是低开」判断的统一出口。

    open 缺失（停牌/竞价无成交）时返回 None：**这与「零跳空」是两件事**，
    混成 0 会把「不知道」当成「不涨不跌」，进而把无关标的选进强板块落后股。
    """
    if not isinstance(q, dict):
        return None
    op, pc = q.get('open'), q.get('prevClose')
    if not op or not pc or pc <= 0:
        return None
    return round((op - pc) / pc * 100, 2)


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
            if not q:
                print(c, '取价失败')
                continue
            g = open_gap(q)
            print('%s %s %.2f %+.2f%% 开盘%s 量比%s 均价%s 换手%s 振幅%s' % (
                c, q['name'], q['price'], q['pct'],
                ('%+.2f%%' % g) if g is not None else '—',
                ('%.2f' % q['volRatio']) if q.get('volRatio') is not None else '—',
                ('%.2f' % q['avgPrice']) if q.get('avgPrice') is not None else '—',
                ('%.2f%%' % q['turnover']) if q.get('turnover') is not None else '—',
                ('%.2f%%' % q['amplitude']) if q.get('amplitude') is not None else '—'))
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_names.py —— data.js 个股简称与行情源对齐（R100z168）

问题：AI 写要闻/精选时凭记忆填 name，会出现
      ① 已更名（韦尔股份→豪威集团、银泰黄金→山金国际、郑煤机→中创智领…）
      ② 已戴帽（人福医药→ST人福、荣华实业→*ST荣华）——最危险，用户看不出风险
      ③ 我方截断（华润新能 vs 华润新能源）
      黑名单自己取的是实时行情名，两边不一致 → 同一只票两套名字。

做法：拉行情源真实简称 → 清洗 → 只覆盖「清洗后仍不同」的条目。
      清洗规则（吸收源侧噪声，避免误改）：
        · 去全部空白（腾讯对 4 字名插空格：粤 传 媒 / 五 粮 液 / 万  科Ａ）
        · 全角字母数字转半角（Ａ→A）
        · 带 XD/XR/DR 除权前缀的一律跳过（源名被截断成 "XD工商银"，不可靠）
        · 81/88 开头的板块指数代码跳过（非个股）
      写入走 _tmp + os.replace，幂等：名字已对齐时直接 exit 0 不落盘。

用法：
    python3 sync_names.py            # 只报告差异
    python3 sync_names.py --write    # 校正并写回 data.js
"""
import argparse
import json
import os
import re
import sys
import unicodedata

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

DATA_JS = os.path.join(ROOT, 'data.js')
CODE_RE = re.compile(r'^\d{6}$')
DIV_PREFIX = ('XD', 'XR', 'DR')


def clean(name):
    """把行情源简称洗成可比形式；不可比时返回 None。"""
    s = (name or '').strip()
    if not s:
        return None
    s = re.sub(r'\s+', '', s)                       # 去空白
    s = unicodedata.normalize('NFKC', s)            # Ａ→A，全角数字→半角
    if s.startswith(DIV_PREFIX):                    # 除权前缀：源名已截断，放弃
        return None
    return s


def load_data():
    raw = open(DATA_JS, encoding='utf-8').read()
    i, j = raw.index('{'), raw.rindex('}')
    return raw, json.loads(raw[i:j + 1]), i


def harvest(D):
    """收集 data.js 里全部 (code, name) 出现点：[(code, name, 路径)]。"""
    out = []

    def walk(o, path):
        if isinstance(o, dict):
            c, n = o.get('code'), o.get('name')
            if isinstance(c, str) and CODE_RE.match(c) and c[:2] not in ('81', '88') \
                    and isinstance(n, str) and n.strip():
                out.append((c, n.strip(), path))
            for k, v in o.items():
                walk(v, '%s.%s' % (path, k))
        elif isinstance(o, list):
            for idx, v in enumerate(o):
                walk(v, '%s[%d]' % (path, idx))

    walk(D, 'D')
    for c, n in (D.get('stkKlineNames') or {}).items():
        if isinstance(n, str) and n.strip() and CODE_RE.match(str(c)) \
                and not str(c).startswith(('81', '88')):
            out.append((str(c), n.strip(), 'stkKlineNames.%s' % c))
    # stockNews 是 {code: {name, news[]}} 结构 —— dict 里没有 code 字段，
    # 上面的 walk 收不到（R100z168 线上实测漏了 601717/001248/601567 三条）。
    for c, v in (D.get('stockNews') or {}).items():
        if CODE_RE.match(str(c)) and isinstance(v, dict):
            n = v.get('name')
            if isinstance(n, str) and n.strip():
                out.append((str(c), n.strip(), 'stockNews.%s' % c))
    return out


def apply(D, fixes):
    """就地把 name 换成新名；返回改动条数。"""
    n = 0
    # 旧名 → 新名（只用于「name 字段没有 code」的孤立条目，如 harmonic.failPool[39]）
    old2new = {}
    for _c, old, _p in harvest(D):
        if _c in fixes:
            old2new[old] = fixes[_c]

    def walk(o):
        nonlocal n
        if isinstance(o, dict):
            c, nm = o.get('code'), o.get('name')
            if isinstance(c, str) and c in fixes and isinstance(nm, str) \
                    and nm.strip() != fixes[c]:
                o['name'] = fixes[c]
                n += 1
            elif c is None and isinstance(nm, str) and nm.strip() in old2new \
                    and nm.strip() != old2new[nm.strip()]:
                o['name'] = old2new[nm.strip()]      # 精确整值匹配，不碰正文
                n += 1
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(D)
    kn = D.get('stkKlineNames') or {}
    for c, new in fixes.items():
        if c in kn and isinstance(kn[c], str) and kn[c].strip() != new:
            kn[c] = new
            n += 1
    sn = D.get('stockNews') or {}
    for c, new in fixes.items():
        if isinstance(sn.get(c), dict) and isinstance(sn[c].get('name'), str) \
                and sn[c]['name'].strip() != new:
            sn[c]['name'] = new
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true', help='写回 data.js')
    ap.add_argument('--check', action='store_true',
                    help='闸门用：有差异 exit 1；源不可达 exit 2（不可判定）')
    args = ap.parse_args()

    raw, D, _ = load_data()
    items = harvest(D)
    codes = sorted({c for c, _, _ in items})
    print('data.js 内个股 code：%d 个 / name 出现点：%d 处' % (len(codes), len(items)))

    try:
        import common
        real = common.fetch_names(codes)
    except Exception as e:                                    # 源不可达：显式降级
        print('⚠ 行情名源不可达（%s）→ 本次跳过，不改任何名字' % e)
        return 2 if args.check else 0

    print('行情源取到：%d / %d' % (len(real), len(codes)))
    if not real:
        print('⚠ 一只都没取到 → 视为源故障，不改任何名字')
        return 2 if args.check else 0

    fixes, skipped = {}, 0
    for c, old, _ in items:
        src = real.get(c)
        if not src:
            continue
        new = clean(src)
        if new is None:
            skipped += 1
            continue
        if new != unicodedata.normalize('NFKC', old.replace(' ', '')):
            fixes[c] = new

    if skipped:
        print('跳过（除权前缀/源名不可靠）：%d 处' % skipped)
    if not fixes:
        print('✅ 全部对齐，无需改动（幂等）')
        return 0

    print('=== 需校正 %d 只 ===' % len(fixes))
    for c, new in sorted(fixes.items()):
        olds = sorted({o for cc, o, _ in items if cc == c})
        print('  %s  %s → %s' % (c, ' / '.join(olds), new))

    if args.check:
        print('❌ 有 %d 只个股简称与行情源不一致 → sync_names.py --write 未跑' % len(fixes))
        return 1
    if not args.write:
        print('（未加 --write，仅报告）')
        return 0

    n = apply(D, fixes)
    body = json.dumps(D, ensure_ascii=False, separators=(',', ':'))
    tmp = DATA_JS + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('window.DASHBOARD_DATA = ' + body + ';\n')
    os.replace(tmp, DATA_JS)
    print('✅ 已写回 data.js：%d 处 name 更新' % n)
    return 0


if __name__ == '__main__':
    sys.exit(main())

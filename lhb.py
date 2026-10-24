#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lhb.py · 龙虎榜席位结构（R100z195）

**为什么加**（用户 2026-10-24：「主力资金模块你目前只有大数，你来决定是不是可以增加龙虎榜？」）
现有 `ashare.fundIn/fundOut` 只回答「哪个行业净流入多少钱」——
是**行业级金额**，不回答「这笔钱是谁的钱、是主动定价还是被动申购」。
龙虎榜是 A 股唯一公开到**席位级别**的成交分布：机构专用 / 北向 / 游资营业部 / 拉萨系（散户+量化），
四类资金的次日行为完全不同（机构建仓可持有，游资一日游要接力，拉萨系是接盘方）。
这正是「大数」缺的那一层。**加，但只加事实，不加预测。**

**口径与限制（必须原样写进页面 note，不许在叙事里说成别的意思）**
  · 覆盖面：龙虎榜只含**触发披露阈值**的个股（涨跌幅偏离 7%、换手、振幅、三日累计 20% 等），
    101 只 / 全市场 5000+ 只 → 它是「异动的样本」，不是全市场资金流。
  · 时间：交易所收盘后披露，17:00 抓取经常只有一部分 → `status=partial` 显式打标；
    一只没有就回退上一交易日并标 `prev`。**绝不把「没抓到」写成「没有」**。
  · 席位归类按名称：机构专用=基金/保险等专用席位；*股通专用=北向；拉萨系=东方财富证券拉萨营业部
    （业内共识是散户+量化混合，不是「主力」）；其余营业部统称游资席位。
    **不做「某游资一定来抬轿」这种推断**，只报净额。
  · 每只票的 `histWin` 是东财统计的**该股历史上榜后上涨比例**，是历史频率、不是本次胜率，
    故只作参考字段，不进排序、不进结论句。

**成本控制**：汇总接口一次拿全（含机构家数/净买额/成交额/上榜原因）；
席位明细则每只票 2 次请求（买+卖），只对**主板净买前 5 + 净卖前 3** 抓（8 只 = 16 请求）。
全量抓会变成 202 请求、17:00 跑不完 —— 这是刻意的取舍，不是漏做。

用法:
    python3 lhb.py                 # 人读表格
    python3 lhb.py --json          # 结构化（给 prompt / 调试）
    python3 lhb.py --write         # 写进 data.js 的 ashare.lhb
    python3 lhb.py --selftest      # 离线自检（不联网）
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import common

BASE = os.path.dirname(os.path.abspath(__file__))

API = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
REFERER = 'https://data.eastmoney.com/'

# 榜单里展示几只
TOP_N = 5          # 主板净买前 N（带席位明细）
SELL_N = 3         # 净卖前 N
SEAT_N = 3         # 每只票展示几个买方席位

MAIN_BOARD = ('沪主板', '深主板')

# 完整性门槛：正常交易日 60~130 只。低于这个数 = 交易所还没披露完
COMPLETE_MIN = 40

NOTE = ('龙虎榜只覆盖触发披露阈值的个股（涨跌幅偏离 7%、换手、振幅、三日累计 20% 等），'
        '不是全市场资金流；同一只票同日可能上多张榜，本模块按代码去重、只取单日口径（三日榜不可与单日相加）。'
        '席位按名称归类：机构专用（基金保险等）、股通专用（北向）、'
        '拉萨系（东方财富证券拉萨营业部，散户与量化混合）、其余营业部统称游资。'
        '「同一席位同时进多只」只表示同名营业部同时净买，同一营业部可有多个客户，不等于同一资金。'
        '净买/净卖榜只列 60/00 主板（本看板交易范围），统计口径（上榜家数、净买合计）是全市场。'
        '本模块是当日成交的分布事实，不构成次日方向判断。')


# ---------------------------------------------------------------- 抓取
def _get(report, flt, size=200, sort='', order=''):
    q = {'reportName': report, 'columns': 'ALL', 'source': 'WEB', 'client': 'WEB',
         'pageNumber': '1', 'pageSize': str(size), 'filter': flt}
    if sort:
        q['sortColumns'] = sort
        q['sortTypes'] = order or '-1'
    url = API + '?' + urllib.parse.urlencode(q)
    raw = common.http_get(url, timeout=20, retries=2)
    if not raw:
        return []
    try:
        d = json.loads(raw)
    except Exception:
        return []
    return ((d.get('result') or {}).get('data')) or []


def fetch_summary(date):
    """当日龙虎榜汇总。返回原始行列表（东财字段名）。"""
    return _get('RPT_DAILYBILLBOARD_DETAILSNEW',
                "(TRADE_DATE='%s')" % date, size=200,
                sort='BILLBOARD_NET_AMT', order='-1')


def fetch_seats(code, date, side='BUY'):
    """某只票的营业部明细。side=BUY/SELL。"""
    rn = 'RPT_BILLBOARD_DAILYDETAILSBUY' if side == 'BUY' else 'RPT_BILLBOARD_DAILYDETAILSSELL'
    return _get(rn, "(TRADE_DATE='%s')(SECURITY_CODE=\"%s\")" % (date, code), size=10)


# ---------------------------------------------------------------- 归类
def classify(name, code=''):
    """席位名 → inst / north / lhasa / broker / other。顺序不能换：拉萨营业部也含「证券」。"""
    n = str(name or '')
    if str(code) == '0' or '机构专用' in n:
        return 'inst'
    if '股通专用' in n:
        return 'north'
    if '拉萨' in n:
        return 'lhasa'
    if '营业部' in n or '证券' in n:
        return 'broker'
    return 'other'


def board_of(code):
    c = str(code or '')
    if c.startswith(('600', '601', '603', '605')):
        return '沪主板'
    if c.startswith(('000', '001', '002', '003')):
        return '深主板'
    if c.startswith(('300', '301', '302')):
        return '创业板'
    if c.startswith(('688', '689')):
        return '科创板'
    if c.startswith(('4', '8', '92')):
        return '北交所'
    return '其他'


def reason_short(s):
    """上榜原因 → 短标签（页面一行放得下）。原句太长，101 只都挂全句会撑爆卡片。"""
    t = str(s or '')
    # 「连续三个交易日…涨幅偏离值累计 20%」同时含「涨幅偏离」，必须先匹配三日口径，
    # 否则三日榜会被误标成单日 7%（自检 27 条里第 27 条就是这么挂的）
    for kw, tag in (('连续三个交易日', '三日累计20%'),
                    ('严重异常', '严重异常波动'),
                    ('无价格涨跌幅限制', '无涨跌幅限制'),
                    ('跌幅偏离', '跌幅偏离7%'), ('涨幅偏离', '涨幅偏离7%'),
                    ('换手率', '高换手'), ('振幅', '高振幅')):
        if kw in t:
            return tag
    return t[:12] or '—'


def parse_explain(s):
    """东财 EXPLAIN：'1家机构买入，成功率41.90%' → (机构家数, 历史上榜后上涨比例)。"""
    t = str(s or '')
    m = re.search(r'(\d+)\s*家机构', t)
    n = int(m.group(1)) if m else 0
    w = re.search(r'成功率\s*([\d.]+)\s*%', t)
    return n, (float(w.group(1)) if w else None)


def _yi(v):
    try:
        return round(float(v or 0) / 1e8, 2)
    except Exception:
        return 0.0


def row_item(x):
    code = str(x.get('SECURITY_CODE') or '')
    net = _yi(x.get('BILLBOARD_NET_AMT'))
    amt = _yi(x.get('ACCUM_AMOUNT'))
    inst_n, _ = parse_explain(x.get('EXPLAIN'))
    try:
        pct = round(float(x.get('CHANGE_RATE') or 0), 2)
    except Exception:
        pct = 0.0
    return {
        'code': code,
        'name': x.get('SECURITY_NAME_ABBR') or '',
        'board': board_of(code),
        'pct': pct,
        'netAmt': net,
        'ratio': round(net / amt * 100, 1) if amt else None,
        'reason': reason_short(x.get('EXPLANATION')),
        'instN': inst_n,
        'raw': x,
    }


def seat_block(rows, side='BUY'):
    """一组席位行 → 性质汇总 + 净额前 N。

    ⚠️ 买卖两侧排序方向相反：买方看净额降序（谁买得最多），
    卖方看净额**升序**（谁卖得最狠）。2026-10-24 截图实测踩过：
    统一降序时「卖方首位」取到的是卖得最少的那个（盛视科技显示 -0.05 亿），
    而它当日净卖 1.48 亿 —— 等于把最重要的一条反向显示了。
    """
    agg = {'inst': 0.0, 'north': 0.0, 'lhasa': 0.0, 'broker': 0.0, 'other': 0.0}
    top = []
    for r in rows:
        kind = classify(r.get('OPERATEDEPT_NAME'), r.get('OPERATEDEPT_CODE'))
        net = _yi(r.get('NET'))
        agg[kind] = round(agg[kind] + net, 2)
        nm = r.get('OPERATEDEPT_NAME') or ''
        top.append({'n': nm, 's': short_seat(nm), 'v': net, 'k': kind})
    top.sort(key=lambda z: z['v'], reverse=(side == 'BUY'))
    return agg, top


def short_seat(name):
    """营业部全名 → 卡片放得下的短名（保留「券商简称 + 分支地名」）。

    全名如「国泰海通证券股份有限公司三亚迎宾路证券营业部」有 22 字，
    一张卡放 3 个会溢出；而只截前 8 字会让三条全变成「国泰海通证券股份」——
    同一券商的不同分支恰恰是判断「是不是同一个游资」的关键，所以砍中间留两端。
    """
    t = str(name or '').strip()
    if not t:
        return '—'
    if '机构专用' in t or '股通专用' in t:
        return t
    for junk in ('股份有限公司', '有限责任公司', '有限公司', '证券营业部', '营业部', '证券'):
        t = t.replace(junk, '')
    return t[:14] or name[:14]


# ---------------------------------------------------------------- 组装
def _round2(v):
    return round(float(v or 0), 2)


# 同一代码同日可上多张榜（单日涨幅偏离 / 三日累计 20% / 换手 / 振幅），
# 每张榜的 BILLBOARD_NET_AMT 口径不同（三日榜是 3 日累计），**不能相加**。
# 所以按代码去重：优先保留「单日」榜那条，三日榜只作为标签补在 reasons 里。
SINGLE_DAY_TAGS = ('涨幅偏离7%', '跌幅偏离7%', '高换手', '高振幅', '无涨跌幅限制')


def _dedupe(items):
    seen, out = {}, []
    for it in items:
        c = it['code']
        if c not in seen:
            seen[c] = it
            it['reasons'] = [it['reason']]
            out.append(it)
            continue
        prev = seen[c]
        prev['reasons'].append(it['reason'])
        # 三日榜 vs 单日榜：单日口径更可比，用它替换；数值不同不合并、不加总
        if prev['reason'] not in SINGLE_DAY_TAGS and it['reason'] in SINGLE_DAY_TAGS:
            keep = it
            keep['reasons'] = prev['reasons']
            i = out.index(prev)
            out[i] = keep
            seen[c] = keep
    for it in out:
        it['reasons'] = sorted(set(it['reasons']))
        it['multi'] = len(it['reasons']) > 1
    return out


def build(D=None, date=None, fetch_seat=True):
    """返回 (lhb_dict, warn_list)。永不抛异常：抓不到就降级打标。"""
    warn = []
    today = date or common.today_trade_date()
    rows = fetch_summary(today)
    status = 'complete'
    used = today
    if not rows:
        # 17:00 常遇「还没披露」：回退最近 3 个交易日找有数据的一天
        d = today
        for _ in range(3):
            d = common.last_trade_day(_prev_day(d))
            rows = fetch_summary(d)
            if rows:
                used, status = d, 'prev'
                warn.append('当日（%s）龙虎榜为空，回退到 %s' % (today, d))
                break
        if not rows:
            warn.append('连续 4 个交易日都抓不到龙虎榜（接口异常或休市）')
            return None, warn
    elif len(rows) < COMPLETE_MIN:
        status = 'partial'
        warn.append('当日只抓到 %d 只（少于 %d），交易所披露未完成' % (len(rows), COMPLETE_MIN))

    raw_items = [row_item(x) for x in rows]
    items = _dedupe(raw_items)
    up = sum(1 for i in items if i['pct'] > 0)
    down = sum(1 for i in items if i['pct'] < 0)
    net_total = _round2(sum(i['netAmt'] for i in items))
    inst_items = [i for i in items if i['instN'] > 0]
    inst_pool_net = _round2(sum(i['netAmt'] for i in inst_items))

    # 本看板交易范围是 60/00 主板（用户硬约束），创业板/科创板/北交所即使上榜也不列；
    # 上面的上榜家数/净买合计仍是**全市场**口径，故句子必须写明「全市场」。
    mains = [i for i in items if i['board'] in MAIN_BOARD]
    top = sorted([i for i in mains if i['netAmt'] > 0], key=lambda z: -z['netAmt'])[:TOP_N]
    sell = sorted([i for i in mains if i['netAmt'] < 0], key=lambda z: z['netAmt'])[:SELL_N]
    if not top:
        warn.append('主板当日无净买入个股')
    if not sell:
        warn.append('主板当日无净卖出个股')

    if fetch_seat and (top or sell):
        want = [(i, 'BUY') for i in top] + [(i, 'SELL') for i in sell]

        def _one(pair):
            it, side = pair
            return it, seat_block(fetch_seats(it['code'], used, side), side)

        with ThreadPoolExecutor(max_workers=6) as ex:
            for it, (agg, seat_top) in ex.map(_one, want):
                if not seat_top:
                    continue
                it['seats'] = agg
                it['seatTop'] = seat_top[:SEAT_N]

    # 席位重复：同一个营业部席位当日同时净买多只票 —— 这是「一个资金同时扫多票」，
    # 比单只票的净买额更有信息量（用户 2026-10-24 说的「大数」缺的正是这一层）。
    # ⚠️ 只报事实：不写「同一游资」——同一营业部可以有多个客户，这个断言数据不支持。
    rep = {}
    for it in top + sell:
        for s in it.get('seatTop') or []:
            # 「机构专用」「股通专用」是类别不是某个席位，出现在多只票是常态，
            # 当成「同一资金扫票」就是误读 —— 只统计具体营业部与拉萨系。
            if s['v'] <= 0 or s['k'] not in ('broker', 'lhasa'):
                continue
            k = s['n']
            if k not in rep:
                rep[k] = {'n': k, 's': s.get('s') or k, 'k': s['k'], 'names': [], 'net': 0.0}
            if it['name'] not in rep[k]['names']:
                rep[k]['names'].append(it['name'])
            rep[k]['net'] = round(rep[k]['net'] + s['v'], 2)
    seat_repeat = sorted([v for v in rep.values() if len(v['names']) >= 2],
                         key=lambda z: -z['net'])[:5]

    # 汇总句：每句都带范围限定，不许出现「全市场净买」这种口径外推
    parts = []
    parts.append('全市场上榜 %d 只（涨 %d 跌 %d），榜单净买合计 %+.2f 亿；机构专用席位出现在 %d 只里，'
                 '这 %d 只合计净买 %+.2f 亿。'
                 % (len(items), up, down, net_total, len(inst_items), len(inst_items), inst_pool_net))
    if top:
        t0 = top[0]
        seg = '主板净买第一 %s（%s）%+.2f 亿，占当日成交额 %s%%' % (
            t0['name'], t0['code'], t0['netAmt'],
            t0['ratio'] if t0['ratio'] is not None else '—')
        if t0.get('seatTop'):
            seg += '，买方首位 %s %+.2f 亿' % (t0['seatTop'][0]['s'], t0['seatTop'][0]['v'])
        parts.append(seg + '。')
    if sell:
        s0 = sell[0]
        seg = '主板净卖第一 %s（%s）%+.2f 亿' % (s0['name'], s0['code'], s0['netAmt'])
        if s0.get('seatTop'):
            seg += '，卖方首位 %s %+.2f 亿' % (s0['seatTop'][0]['s'], s0['seatTop'][0]['v'])
        parts.append(seg + '。')
    if seat_repeat:
        r0 = seat_repeat[0]
        parts.append('同一营业部席位同时进多只净买榜：%s 出现在 %d 只（%s）里，合计 %+.2f 亿。'
                     % (r0['s'], len(r0['names']), '、'.join(r0['names'][:4]), r0['net']))

    out = {
        'date': used,
        'status': status,
        'source': 'eastmoney',
        'total': len(items),
        'mainN': len(mains),
        'upN': up,
        'downN': down,
        'netBuyTotal': net_total,
        'instN': len(inst_items),
        'instPoolNet': inst_pool_net,
        'top': [_slim(i) for i in top],
        'sell': [_slim(i) for i in sell],
        'seatRepeat': [{'n': v['n'], 's': v['s'], 'k': v['k'], 'names': v['names'],
                        'net': v['net']} for v in seat_repeat],
        'text': ''.join(parts),
        'note': NOTE,
    }
    return out, warn


def _slim(i):
    """去掉 raw，控制 data.js 体积。席位同时留全名 n 与短名 s（前端用小名防溢出）。"""
    o = {k: v for k, v in i.items() if k != 'raw'}
    if o.get('seatTop'):
        o['seatTop'] = [{'n': s['n'], 's': s.get('s') or s['n'], 'v': s['v'], 'k': s['k']}
                        for s in o['seatTop']]
    return o


def _prev_day(s):
    import datetime as _dt
    d = _dt.datetime.strptime(str(s)[:10], '%Y-%m-%d').date()
    return (d - _dt.timedelta(days=1)).strftime('%Y-%m-%d')


def write_back(D, lhb):
    a = D.get('ashare')
    if not isinstance(a, dict):
        return 0
    a['lhb'] = lhb
    return 1


# ---------------------------------------------------------------- 自检
def selftest():
    ok, bad = 0, []

    def chk(name, cond, extra=''):
        nonlocal ok
        if cond:
            ok += 1
        else:
            bad.append('%s %s' % (name, extra))

    # 席位归类：拉萨必须排在营业部之前，否则拉萨系全被算成游资
    chk('机构专用', classify('机构专用', '0') == 'inst')
    chk('机构专用按代码', classify('某机构', '0') == 'inst')
    chk('深股通', classify('深股通专用') == 'north')
    chk('沪股通', classify('沪股通专用') == 'north')
    chk('拉萨系优先于营业部', classify('东方财富证券股份有限公司拉萨金融城南环路证券营业部') == 'lhasa',
        classify('东方财富证券股份有限公司拉萨金融城南环路证券营业部'))
    chk('普通营业部=游资', classify('国泰海通证券股份有限公司成都天府大道证券营业部') == 'broker')
    chk('其他', classify('某某分公司') == 'other')

    # 板块
    chk('002 属深主板', board_of('002354') == '深主板')
    chk('600 属沪主板', board_of('600028') == '沪主板')
    chk('300 属创业板', board_of('300750') == '创业板')
    chk('688 属科创板', board_of('688981') == '科创板')
    chk('830 属北交所', board_of('830799') == '北交所')

    # 上榜原因短标签
    chk('涨幅偏离', reason_short('日涨幅偏离值达到7%的前5只证券') == '涨幅偏离7%')
    chk('跌幅偏离', reason_short('日跌幅偏离值达到7%的前5只证券') == '跌幅偏离7%')
    chk('三日累计', reason_short('连续三个交易日内，涨幅偏离值累计达到20%的证券') == '三日累计20%')

    # EXPLAIN 解析
    chk('1家机构', parse_explain('1家机构买入，成功率41.90%') == (1, 41.9),
        str(parse_explain('1家机构买入，成功率41.90%')))
    chk('普通席位', parse_explain('普通席位买入，成功率46.53%') == (0, 46.53))
    chk('空串', parse_explain('') == (0, None))

    # 席位聚合：净额按性质分桶
    agg, top = seat_block([
        {'OPERATEDEPT_NAME': '机构专用', 'OPERATEDEPT_CODE': '0', 'NET': 100000000},
        {'OPERATEDEPT_NAME': '深股通专用', 'OPERATEDEPT_CODE': '1', 'NET': -20000000},
        {'OPERATEDEPT_NAME': '东方财富证券股份有限公司拉萨金融城南环路证券营业部',
         'OPERATEDEPT_CODE': '2', 'NET': -30000000},
        {'OPERATEDEPT_NAME': '国泰海通证券股份有限公司成都天府大道证券营业部',
         'OPERATEDEPT_CODE': '3', 'NET': 50000000},
    ])
    chk('机构净额 1 亿', agg['inst'] == 1.0, str(agg))
    chk('北向 -0.2 亿', agg['north'] == -0.2, str(agg))
    chk('拉萨 -0.3 亿', agg['lhasa'] == -0.3, str(agg))
    chk('游资 0.5 亿', agg['broker'] == 0.5, str(agg))
    chk('席位按净额降序', [t['v'] for t in top] == [1.0, 0.5, -0.2, -0.3], str(top))
    # 卖方必须反向：卖得最狠的排第一（2026-10-24 截图实测把 -0.05 亿当「卖方首位」）
    _, stop = seat_block([
        {'OPERATEDEPT_NAME': 'A营业部', 'NET': 1000000},
        {'OPERATEDEPT_NAME': 'B营业部', 'NET': -300000000},
        {'OPERATEDEPT_NAME': 'C营业部', 'NET': -5000000},
    ], 'SELL')
    chk('卖方首位=净卖最大', stop[0]['n'] == 'B营业部' and stop[0]['v'] == -3.0, str(stop))
    chk('_yi 换算', _yi(489098145.24) == 4.89, str(_yi(489098145.24)))

    # 去重：同日多榜的同一只票只算一次，且优先单日口径（三日榜净额不可与单日相加）
    dup = _dedupe([
        row_item({'SECURITY_CODE': '003021', 'SECURITY_NAME_ABBR': '兆威机电', 'CHANGE_RATE': 10,
                  'BILLBOARD_NET_AMT': 234196429.47, 'ACCUM_AMOUNT': 2977799708,
                  'EXPLANATION': '连续三个交易日内，涨幅偏离值累计达到20%的证券',
                  'EXPLAIN': '1家机构买入，成功率39.81%'}),
        row_item({'SECURITY_CODE': '003021', 'SECURITY_NAME_ABBR': '兆威机电', 'CHANGE_RATE': 10,
                  'BILLBOARD_NET_AMT': 214000000, 'ACCUM_AMOUNT': 2350000000,
                  'EXPLANATION': '日涨幅偏离值达到7%的前5只证券',
                  'EXPLAIN': '1家机构买入，成功率39.81%'}),
    ])
    chk('同码去重只留一条', len(dup) == 1, str(len(dup)))
    chk('保留单日口径', dup[0]['netAmt'] == 2.14 and dup[0]['reason'] == '涨幅偏离7%', str(dup[0]))
    chk('多榜标记', dup[0]['multi'] is True and len(dup[0]['reasons']) == 2, str(dup[0].get('reasons')))

    # 席位短名：同一券商不同分支必须还能区分（只截前 8 字会全撞成「国泰海通证券股份」）
    s1 = short_seat('国泰海通证券股份有限公司成都天府大道证券营业部')
    s2 = short_seat('国泰海通证券股份有限公司三亚迎宾路证券营业部')
    chk('短名可区分分支', s1 != s2 and len(s1) <= 14, '%s / %s' % (s1, s2))
    chk('短名保留地名', '成都' in s1 and '三亚' in s2, '%s / %s' % (s1, s2))
    chk('机构专用不改名', short_seat('机构专用') == '机构专用')

    # 组装：造 3 行假数据，确认统计口径
    fake = [
        {'SECURITY_CODE': '002354', 'SECURITY_NAME_ABBR': '天娱数科', 'CHANGE_RATE': 10,
         'BILLBOARD_NET_AMT': 489098145.24, 'ACCUM_AMOUNT': 2310005102,
         'EXPLANATION': '日涨幅偏离值达到7%的前5只证券', 'EXPLAIN': '1家机构买入，成功率41.90%'},
        {'SECURITY_CODE': '600028', 'SECURITY_NAME_ABBR': '中国石化', 'CHANGE_RATE': -6,
         'BILLBOARD_NET_AMT': -120000000, 'ACCUM_AMOUNT': 800000000,
         'EXPLANATION': '日跌幅偏离值达到7%的前5只证券', 'EXPLAIN': '普通席位买入，成功率46.53%'},
        {'SECURITY_CODE': '300750', 'SECURITY_NAME_ABBR': '宁德时代', 'CHANGE_RATE': 3,
         'BILLBOARD_NET_AMT': 80000000, 'ACCUM_AMOUNT': 5000000000,
         'EXPLANATION': '日换手率达到20%的前5只证券', 'EXPLAIN': '2家机构买入，成功率55.00%'},
    ]
    items = [row_item(x) for x in fake]
    mains = [i for i in items if i['board'] in MAIN_BOARD]
    chk('主板剔除创业板', [i['name'] for i in mains] == ['天娱数科', '中国石化'], str([i['name'] for i in mains]))
    chk('涨跌家数', sum(1 for i in items if i['pct'] > 0) == 2)
    chk('机构池净买', [i['name'] for i in items if i['instN'] > 0] == ['天娱数科', '宁德时代'])

    print('lhb 自检：%d/%d 通过' % (ok, ok + len(bad)))
    for b in bad:
        print('  ✗', b)
    return 0 if not bad else 1


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--date', default='', help='指定交易日 YYYY-MM-DD（默认今天对应的交易日）')
    ap.add_argument('--no-seat', action='store_true', help='跳过席位明细（只出汇总，调试用）')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())

    lhb, warn = build(date=(a.date or None), fetch_seat=not a.no_seat)
    for w in warn:
        print('[lhb][WARN] ' + w)
    if not lhb:
        print('[lhb] 无数据，不写盘（保留上一份，绝不写空）')
        sys.exit(2)

    if a.json:
        print(json.dumps({'ok': True, 'lhb': lhb, 'warnings': warn}, ensure_ascii=False))
        return

    if a.write:
        D = common.load_dashboard_data(BASE)
        n = write_back(D, lhb)
        if not n:
            print('[lhb] data.js 里没有 ashare 节点，不写')
            sys.exit(2)
        common.write_data_js(BASE, D, label='lhb')
        D2 = common.load_dashboard_data(BASE)
        got = (D2.get('ashare') or {}).get('lhb') or {}
        print('[lhb] 落盘复验：%s %s 条 top / %s 条 sell / status=%s'
              % (got.get('date'), len(got.get('top') or []), len(got.get('sell') or []),
                 got.get('status')))
        if not got:
            print('[lhb][FAIL] 写完复验读不到 ashare.lhb')
            sys.exit(2)
        return

    print('龙虎榜 %s（%s）全市场 %d 只上榜（主板 %d），净买合计 %+.2f 亿，机构现身 %d 只'
          % (lhb['date'], lhb['status'], lhb['total'], lhb['mainN'],
             lhb['netBuyTotal'], lhb['instN']))
    print(lhb['text'])
    print('\n净买前 %d（主板）：' % TOP_N)
    for i in lhb['top']:
        print('  %s %-6s %+6.2f亿 占成交%s%% %-10s %s'
              % (i['code'], i['name'], i['netAmt'], i['ratio'],
                 '/'.join(i.get('reasons') or [i['reason']]),
                 ('席位 ' + '/'.join('%s%+.2f' % (s.get('s') or s['n'], s['v'])
                                     for s in i.get('seatTop') or []))
                 if i.get('seatTop') else ''))
    if lhb['sell']:
        print('\n净卖前 %d：' % SELL_N)
        for i in lhb['sell']:
            print('  %s %-6s %+6.2f亿 %s' % (i['code'], i['name'], i['netAmt'], i['board']))
    if lhb.get('seatRepeat'):
        print('\n同一席位进多只（买方向）：')
        for r in lhb['seatRepeat']:
            print('  %-16s %d 只 合计%+.2f亿  %s'
                  % (r['s'], len(r['names']), r['net'], '、'.join(r['names'][:5])))


if __name__ == '__main__':
    main()

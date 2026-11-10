#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按当前 `chen_pool.settle_one` 口径重算 `chen_ledger.json` 全部条目（R100z299）。

## 为什么需要它

`settle_ledger()` 只结算 `status != settled` 的条目——已结算的永不再算。所以一旦
结算规则本身有 bug（本轮就是：止盈从**买入当天**开始判，等于 T+0 卖出），历史样本会
永久保留错误结果，页面上的 92.9% 也不会自己变回真值。

这个脚本把**所有**条目按当前规则重算一遍，并打 `settleVer` 标记，让「这批数是用哪版规则
算的」可查。重算不是「重新判分」——它修的是一个算术错误（那笔卖单本来不存在），
不是调参数。

## 数据源

优先用 `data.js` 的 `stkKlines`（离线、可复现、与页面同源）；缺票/根数不足时才回落到
`check_duanban.get_kline` 联网抓。

用法：
    python3 resettle_ledger.py --dry    # 只看修前修后（不写盘）
    python3 resettle_ledger.py --write  # 落盘
"""
import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chen_pool as CP                                          # noqa: E402
import check_duanban as CD                                      # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(ROOT, 'data.js')
LEDGER = os.path.join(ROOT, 'chen_ledger.json')
VER = 'R100z299'


def load_datajs_k():
    src = open(DATA_JS, encoding='utf-8').read()
    i = src.find('window.DASHBOARD_DATA')
    j = src.find('=', i)
    D = json.loads(src[j + 1:].strip().rstrip(';').strip())
    return D


def to_kl(bars):
    """[日期, 开, 收, 高, 低, 量] → settle_one 要的 dict 列表。"""
    out = []
    for b in bars or []:
        if isinstance(b, dict):          # check_duanban.get_kline 已经是 dict 形态
            if b.get('day'):
                out.append(b)
            continue
        if not b or len(b) < 5:
            continue
        out.append({'day': b[0], 'open': b[1], 'close': b[2],
                    'high': b[3], 'low': b[4]})
    return out


def by_pool(ents):
    g = {}
    for e in ents:
        g.setdefault(e.get('pool') or '?', []).append(e)
    return g


def fmt(entries):
    """用真函数 pool_stats 出「n / 胜率 / 均值」，不自己另算一套。"""
    s = CP.pool_stats(entries)
    return '%2d 笔 %5s%% %6s%%' % (s['n'], s['winRate'], s['avgRet'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    if not (a.write or a.dry):
        ap.error('要么 --dry 要么 --write')

    D = load_datajs_k()
    K = D.get('stkKlines') or {}
    led = json.load(open(LEDGER, encoding='utf-8'))
    ents = led.get('entries') or []
    before = copy.deepcopy(ents)          # 修前快照，用来出对照表

    miss, changed, back_to_open = [], [], []
    klc = {}          # R100z337：进行明细要用同一份 K 线（别让它自己再抓一次，两处不同源）
    for e in ents:
        code = str(e.get('code'))
        kl = to_kl(K.get(code))
        if len(kl) < 5:
            kl = to_kl(CD.get_kline(code, CD.sina_symbol(code)) or [])
        if len(kl) < 5:
            miss.append(code)
            continue
        klc[code] = kl
        old = dict(e.get('result') or {})
        new = CP.settle_one(e, kl)
        if new is None:
            if old.get('status') == 'settled':
                back_to_open.append((e.get('pool'), code, e.get('name'), old))
            e['result'] = None
        else:
            if old.get('status') == 'settled' and \
               (old.get('result'), old.get('exitDay'), old.get('retPct')) != \
               (new.get('result'), new.get('exitDay'), new.get('retPct')):
                changed.append((e.get('pool'), code, e.get('name'), old, new))
            e['result'] = new
        e['settleVer'] = VER

    print('条目 %d ｜ 取不到 K 线 %d ｜ 改回未结算 %d ｜ 结果变动 %d'
          % (len(ents), len(miss), len(back_to_open), len(changed)))

    gb, gn = by_pool(before), by_pool(ents)
    print('\n%-16s %-22s %s' % ('池', '修前', '修后'))
    for k in sorted(set(gb) | set(gn)):
        print('%-16s %-22s %s' % (k, fmt(gb.get(k) or []), fmt(gn.get(k) or [])))

    if changed:
        print('\n变动样例（前 12 条）：')
        for k, code, name, old, new in changed[:12]:
            print('  %-14s %s %-6s %s %s(%s%%) → %s %s(%s%%)'
                  % (k, code, name or '', old.get('result'), old.get('exitDay'),
                     old.get('retPct'), new.get('result'), new.get('exitDay'),
                     new.get('retPct')))

    if a.write:
        led['entries'] = ents
        led['resettleAt'] = VER
        tmp = LEDGER + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(led, f, ensure_ascii=False, indent=1)
        os.replace(tmp, LEDGER)
        print('\n[OK] 已写入 %s' % LEDGER)

        # 页面上的胜率表读的是 data.js 的 chenPool.compare / track，不重算就只有台账变了、
        # 页面还是旧数。这里用 chen_pool 自己的 build_compare / build_track，
        # 不另写一套口径（否则改规则时必然漏一处）。
        import common
        cp = D.get('chenPool') or {}
        chen_codes = [e.get('code') for e in (cp.get('strict') or []) + (cp.get('loose') or [])]
        db_codes = [e.get('code') for e in (D.get('duanban') or {}).get('confirmed') or []]
        # R100z332：必须传 done。原来这里不传，等于把「今日已兑现」的剔除清空——
        # 用户看到的就是「今天涨停的票又回到明日买入名单」。
        cmp = CP.build_compare(chen_codes, db_codes, led, done=CP.today_done(D), kl_cache=klc)
        cp['compare'] = cmp
        cp['track'] = CP.build_track(led, 0, cmp)
        D['chenPool'] = cp
        common.write_data_js(DATA_JS, D, label='[resettle]')
        print('[OK] 已刷新 data.js 的 chenPool.compare / track')
    else:
        print('\n[dry] 未写盘')


if __name__ == '__main__':
    main()

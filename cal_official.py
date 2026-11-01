# -*- coding: utf-8 -*-
"""深交所官方交易日历客户端（G13 第三方交叉校专用，R100z288）。

为什么需要它：common.HOLIDAY_RANGES 与 cal_factor.HOLIDAYS 是两份手工副本，
G13 的 ① 只校验「两份表彼此一致」——两份都错时它照样绿。
2026-02-23 就是这样：春节写成 02-15~02-21，官方实际休市到 02-23，
两处同错、G13 全绿，08:30 的交易日守卫会在那天当交易日跑全链。

本模块提供唯一的第三方源：深交所官方逐月交易日历端点
    https://www.szse.cn/api/report/exchange/onepersistenthour/monthList?month=YYYY-MM

设计原则（改之前先读，三条都踩过）：

  1. **绝不因为断网 / 源变更而 FAIL。** 抓不到就返回空，由调用方降级成 WARN。
     闸门的第一职责是拦「确定的错」，不是拦「不知道」——否则一次 WiFi 抖动就能拦下部署。
  2. **按月缓存**（`.trade_cal/YYYY-MM.json`，带 fetchedAt），TTL 30 天。
     过期只尝试刷新一次，刷新失败沿用旧缓存（stale but usable），并在 meta 里标记 stale。
  3. **未来月份视为「无数据」**：官方对尚未生成日历的月份返回 `data: []`，
     不能当成「该月 0 个交易日」去和手工表比对（2027 全年现在就是空的）。
     判据是 `len(rows) == 0` → 该月跳过，不是 `交易日数 == 0`。

用法：
    python3 cal_official.py --check 2026-01 2026-12   # 人工核查某区间
    python3 cal_official.py --refresh 2026              # 强制刷新某年缓存
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, '.trade_cal')
API = 'https://www.szse.cn/api/report/exchange/onepersistenthour/monthList?month=%s'
TTL_DAYS = 30
TIMEOUT = 8

_UA = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/126.0 Safari/537.36',
    'Referer': 'https://www.szse.cn/',
}


def _month_path(y, m):
    return os.path.join(CACHE_DIR, '%04d-%02d.json' % (y, m))


def _read_cache(y, m):
    p = _month_path(y, m)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        if not isinstance(d.get('days'), dict):
            return None
        return d
    except Exception:                                        # noqa: BLE001
        return None                                          # 缓存坏了当作没有，下一轮重拉


def _write_cache(y, m, days):
    """原子写：先写 .tmp 再 os.replace（与 common.write_data_js 同一手法）。
    缓存目录若不存在则建；建不出来只影响性能，不影响正确性，直接放弃。"""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        p = _month_path(y, m)
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'month': '%04d-%02d' % (y, m),
                       'fetchedAt': int(time.time()),
                       'src': 'szse.monthList',
                       'days': days}, f, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, p)
    except Exception:                                        # noqa: BLE001
        pass


def fetch_month(y, m, timeout=TIMEOUT):
    """拉官方某月：{日期: 是否交易日}。

    返回三态，调用方必须区分：
      dict 非空 → 正常拿到数据
      {}        → 官方明确无该月数据（未来月份，`data: []`）
      None      → **网络/解析失败**（断网、超时、源改版）——未知，不能当「无数据」
    只在这里区分这两者，上层才敢做「连续失败就熔断」的决定。"""
    url = API % ('%04d-%02d' % (y, m))
    try:
        req = urllib.request.Request(url, headers=_UA)
        raw = urllib.request.urlopen(req, timeout=timeout).read()
        d = json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
    rows = d.get('data') or []
    if not rows:
        return {}                                            # 未来月份：官方尚未生成
    out = {}
    for r in rows:
        q, b = r.get('jyrq'), str(r.get('jybz'))
        if q:
            out[q] = (b == '1')
    return out


def get_month(y, m, ttl_days=TTL_DAYS, allow_fetch=True, timeout=TIMEOUT):
    """取某月日历。返回 (days, meta)；meta = {cached, stale, fetched}。

    days 为空表示「没有可用官方数据」（无缓存且拉不到，或官方无该月），
    调用方必须把它当作「未知」而不是「全月休市」。
    """
    c = _read_cache(y, m)
    if c:
        age = (time.time() - int(c.get('fetchedAt') or 0)) / 86400.0
        if age <= ttl_days or not allow_fetch:
            return c['days'], {'cached': True, 'stale': age > ttl_days, 'fetched': False}
    if not allow_fetch:
        return (c['days'] if c else {}), {'cached': bool(c), 'stale': True, 'fetched': False}
    fresh = fetch_month(y, m, timeout=timeout)
    if fresh:
        _write_cache(y, m, fresh)
        return fresh, {'cached': False, 'stale': False, 'fetched': True}
    if fresh is None:                                        # 网络失败（未知）
        return (c['days'] if c else {}), {'cached': bool(c), 'stale': True,
                                          'fetched': False, 'netfail': True}
    return {}, {'cached': False, 'stale': False, 'fetched': True}      # 官方无该月数据


def get_months(pairs, ttl_days=TTL_DAYS, allow_fetch=True, timeout=TIMEOUT):
    """批量取月。pairs = [(y, m), ...]；返回 (days, meta)。

    days: {日期: 是否交易日}（只含官方有数据的月份）
    meta: {months_ok, months_empty, fetched, stale, netfail, empty}

    熔断：连续 2 个月网络失败就停止后续请求，剩下的标记为 netfail。
    审计闸门里最坏情况是 7 个月 × 8s 超时 = 56s，会让人以为 pipeline 卡死；
    断网时正确做法是立刻降级成 WARN，而不是一个月一个月地等超时。
    """
    days, meta = {}, {'months_ok': 0, 'months_empty': 0, 'fetched': 0, 'stale': 0,
                      'netfail': 0, 'empty': []}
    fails = 0
    for y, m in sorted(set(pairs)):
        if fails >= 2:
            meta['netfail'] += 1
            meta['months_empty'] += 1                        # 熔断月份也要计入「不可用」，否则
            meta['empty'].append('%04d-%02d' % (y, m))       # 汇报会出现「失败 12 次、涉及 2 个月」
            continue
        d, mm = get_month(y, m, ttl_days=ttl_days, allow_fetch=allow_fetch, timeout=timeout)
        if d:
            days.update(d)
            meta['months_ok'] += 1
        else:
            meta['months_empty'] += 1
            meta['empty'].append('%04d-%02d' % (y, m))
        if mm.get('netfail'):
            meta['netfail'] += 1
            fails += 1
        elif mm.get('fetched'):
            fails = 0
        meta['fetched'] += 1 if mm.get('fetched') else 0
        meta['stale'] += 1 if mm.get('stale') else 0
    return days, meta


def months_between(a, b):
    """[a, b] 闭区间覆盖的 (年, 月) 列表。"""
    out = []
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def months_of_ranges(ranges):
    """假期区间列表 → 涉及的 (年, 月) 集合。

    ranges 允许是 (start, end) 二元组，也允许是 cal_factor.holiday_ranges() 产出的
    (名称, start, end) 三元组——只取最后两个元素，两种形态都吃得下
    （这里咬过一次：只按二元组解包，接进 G13 直接 "too many values to unpack"）。"""
    out = set()
    for item in ranges:
        a, b = item[-2], item[-1]
        for ym in months_between(a, b):
            out.add(ym)
    return out


def diff_days(ours, official):
    """ours / official 都是 {日期字符串: bool}；只比对两侧都有的日期。
    返回 [(日期, 我方, 官方)]，按日期升序。"""
    out = []
    for k in sorted(set(ours) & set(official)):
        if bool(ours[k]) != bool(official[k]):
            out.append((k, bool(ours[k]), bool(official[k])))
    return out


def _cli():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', nargs=2, metavar=('YYYY-MM', 'YYYY-MM'))
    ap.add_argument('--refresh', metavar='YYYY')
    ap.add_argument('--offline', action='store_true', help='只用缓存，不联网')
    a = ap.parse_args()
    if a.refresh:
        y = int(a.refresh)
        for m in range(1, 13):
            d, meta = get_month(y, m, ttl_days=0)
            print('%04d-%02d  %3d 天%s' % (y, m, len(d), '  (官方无数据)' if not d else ''))
        return
    if not a.check:
        ap.print_help()
        return
    a1 = date(*(int(x) for x in a.check[0].split('-')), 1)
    y2, m2 = (int(x) for x in a.check[1].split('-'))
    a2 = date(y2, m2, 1)
    days, meta = get_months(months_between(a1, a2), allow_fetch=not a.offline)
    print('覆盖 %d 月（无数据 %d 月 %s），联网刷新 %d 月，stale %d 月'
          % (meta['months_ok'], meta['months_empty'], meta['empty'], meta['fetched'], meta['stale']))
    for k in sorted(days):
        print('  %s  %s' % (k, '开市' if days[k] else '休市'))


if __name__ == '__main__':
    _cli()

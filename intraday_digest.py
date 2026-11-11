#!/usr/bin/env python3
"""盘中分时摘要：给「断板反包 / 陈学长」标的补一份 5 分钟级的承接证据。

## 为什么需要它（R100z345，2026-11-11 用户当面纠正）

用户原话：「什么算出货什么算洗盘要看盘中分时，如果向下砸没有量能承接那可能是出货，
但向下砸是有承接是有托单的那可能就是洗盘……你作为一个专业交易员需要多方面去判断，
不能一股脑只看量」。

起因是我把「单日放量下跌」直接判成出货，结果惠发食品 10/30（砸 −9.4%）、11/02（砸 −10%）
这两天的**标准洗盘**全被写成「这是出货」。而断板反包的形态定义本来就是
「涨停次日倍量阴 + 量能逐日递减」——按旧口径等于自己否掉自己的形态。

日线 OHLC 无法表达「下砸有没有承接」：一根长下影可能是全天阴跌尾盘拉一下，
也可能是开盘砸完立刻被买回，两者收出来的 K 线可以一模一样。所以必须看分时。

## 算哪些量（对应交易员真正会盯的三件事）

每个交易日输出一条 7 元组，全部由 5 分钟 bar 现算：

| 字段 | 含义 | 怎么读 |
|---|---|---|
| `day` | MM-DD | 与 stkKlines 的日期后 5 位对齐 |
| `vwapBelow` | 收在当日累计 VWAP 下方的 bar 占比(%) | ≤20 全天压在均价上方=强；≥85 一路在均价下方=弱 |
| `devVwap` | 收盘相对全天 VWAP 偏离(%) | 正=收在均价上方 |
| `lowMin` | 当日最低点出现的分钟数（09:30 起算） | **开盘砸=恐慌一次出清；尾盘创新低=越砸越低** |
| `lowLift` | 日内局部低点是否逐个抬高(1/0) | 抬高=每次砸都有人接 |
| `rebound` | 最低点后 6 根内的最大回升(%) | 砸完拉得回来=有承接 |
| `upDown` | 回升段均量 ÷ 下砸段均量 | >1 反弹有量；<0.4 反弹无量（无人接） |

`lowMin` 是本轮实测出来最有效的一条：惠发 10/30 最低点在 14:15（尾盘，全天 48 根全在
VWAP 下方）＝恐慌砸盘；11/02 最低点在 09:40（开盘，之后 45 根里只有 3 根在 VWAP 下方、
收盘 +6.19%）＝砸完立刻被接走。两者日线都是「放量大阴线」，分时完全不同。

## 防清场（R91m 铁律）

只新增/更新 `stkIntraday` 一个顶层字段。某只票抓取失败 → **保留旧值**，绝不写空覆盖；
全部失败时直接退出不写盘。已不在池的旧键保留（历史解读还要用），只按 `--prune` 显式清理。

## 体积

只存 20 个数字 × 最多 21 个交易日 × 断板后 ≤20 日的票（实测约 60 只）≈ 8KB，
不存原始 5 分钟线。超过 20 日的票不再更新但保留已有值。

## 用法

  python3 intraday_digest.py                    # 干跑，只打印统计
  python3 intraday_digest.py --write            # 写回 data.js（持 pipeline_lock）
  python3 intraday_digest.py --write --prune    # 顺便清掉已不在池且断板超 30 日的旧键
  python3 intraday_digest.py --help

挂载：16:00 A股自动化 `embed_klines.py` 之后（日线先进 data.js，再按它算断板日）。
"""
import json
import os
import sys
import time
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

CACHE_DIR = os.path.join(BASE, '.intraday_cache')
# R100z351（用户 2026-11-11 18:06 采纳「窗口放到 20 天」）：10 → 20。
# 原来断板后超过 10 日的票不再抓，页面上大部分标的点开只有日线描述、没有分时，
# 读者会以为「这块对这只票没有」。20 日的代价是体积约 8KB、请求约 50~60 次（实测仍 3 秒内）。
KEEP_DAYS = 20          # 断板后多少天内继续更新分时
PRUNE_DAYS = 30         # --prune 时，断板超过这么多天且不在池的键才删
WORKERS = 6
DEADLINE_S = 420        # 整体预算；超时未抓到的按缺失处理（保留旧值）
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')


def _help_guard():
    """K17：`--help` / `-h` 只打用法，绝不执行主流程。

    本脚本会联网抓 70+ 只票的分时并写 data.js，手写 sys.argv 解析时
    `--help` 会被当普通参数忽略、直奔默认流程（探参数变成改数据）。
    """
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__ or '(本脚本无说明)')
        raise SystemExit(0)


def secid(code):
    c = str(code)
    return ('1.' if c.startswith(('6', '5', '9')) else '0.') + c


def cache_path(code):
    return os.path.join(CACHE_DIR, '%s.json' % code)


def fetch_5m(code, beg, end):
    """东财 5 分钟线。成功返回 [[day, open, close, high, low, vol, amt], ...]，失败 None。

    一次请求就能拿到整个区间（实测 9 个交易日 432 根），不必按天循环。
    """
    import urllib.request
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get'
           '?secid=%s&klt=5&fqt=1&beg=%s&end=%s'
           '&fields1=f1,f2&fields2=f51,f52,f53,f54,f55,f56,f57' % (secid(code), beg, end))
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode('utf-8', 'ignore'))
    except Exception:
        return None
    kl = ((d or {}).get('data') or {}).get('klines') or []
    out = []
    for k in kl:
        p = k.split(',')
        if len(p) < 7:
            continue
        try:
            out.append([p[0], float(p[1]), float(p[2]), float(p[3]),
                        float(p[4]), float(p[5]), float(p[6])])
        except ValueError:
            continue
    return out or None


def read_cache(code):
    try:
        with open(cache_path(code), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def write_cache(code, payload):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = cache_path(code) + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, cache_path(code))
    except OSError:
        pass


def minutes_of(hhmm):
    """'13:05' → 自 09:30 起的交易分钟数（午休不计）。"""
    try:
        h, m = int(hhmm[:2]), int(hhmm[3:5])
    except (ValueError, IndexError):
        return None
    cur = h * 60 + m
    am_open, am_close = 9 * 60 + 30, 11 * 60 + 30
    pm_open = 13 * 60
    if cur <= am_close:
        return max(0, cur - am_open)
    return (am_close - am_open) + max(0, cur - pm_open)


def day_digest(bars, prev_close=None):
    """把一天的 5 分钟 bar 压成 18 个数字（R100z346，用户 2026-11-11 16:49 要求加深）。

    用户原话：「你量能关系写的太浅了……你要告诉我的是上午和下午有没有站分时线，是一直站在
    分时线上还是想站分时线站不上去，盘中下跌有没有承接，是无量承接靠大盘情绪还是有量承接里面
    主力在吸筹？以及收盘是站分时线上还是分时线下，全天都在分时线上吗？盘中有没有爆量拉升的，
    如果有爆量拉升之后有没有回落下来还是拉升以后回落一点点之后横住的？」

    对应到可从 5 分钟线算出来的量（下标见下），全部是**当日累计口径**（分时图那条黄线本来就是累计的）：

    | # | 字段 | 回答哪个问题 |
    |---|---|---|
    | 1 | 全天收在均价线下方的根数% | 全天都在线上吗 |
    | 2 | 上午（≤11:30）% | 上午站没站住 |
    | 3 | 下午（≥13:00）% | 下午站没站住 |
    | 4 | 收盘偏离均价% | 收盘在线上还是线下 |
    | 5 | 收盘处在当日振幅位置% | 尾盘强还是弱 |
    | 6 | 最低点出现的分钟 | 开盘砸 vs 尾盘砸 |
    | 7 | **最大回撤段跌幅%** | 盘中这一跌有多深 |
    | 8 | 回撤段起点分钟 | 从几点开始跌 |
    | 9 | 回撤段终点分钟 | 跌到几点见底 |
    | 10 | **下跌段平均下影质量 0~1** | 跌的时候每根有没有被买回来 |
    | 11 | 回撤后回升% | 砸完弹回来多少 |
    | 12 | 回升段量÷下跌段量 | **有量还是无量** |
    | 13 | 回升后 3 根里站住均价线的根数 0~3 | 弹回来站不站得住 |
    | 14 | **承接类型 0无/1无量/2有量** | 用户要的那一句定性 |
    | 15 | 爆量根的时刻（-1 为无） | 有没有爆量拉升 |
    | 16 | 爆量根单根涨幅% | 拉了多少 |
    | 17 | 爆量根量÷当日均量 | 放量倍数 |
    | 18 | 爆量后回吐占涨幅的% | 回落还是横住（<40 算横住） |

    ⚠️ 「下跌段」用**最大回撤**（先找 peak、再找其后的 trough）来找，不是「全天最高点往后找最低点」——
    后者在 11-02 会找到尾盘的小回调，把「砸 8.7% 后回升 14.4%」错判成「无承接」（实测踩过）。
    """
    n = len(bars)
    if n < 8:
        return None
    cum_amt = cum_vol = 0.0
    vwaps = []
    below = 0
    for b in bars:
        cum_amt += b[6]
        cum_vol += b[5]
        v = (cum_amt / (cum_vol * 100)) if cum_vol > 0 else b[2]
        vwaps.append(v)
        if b[2] < v:
            below += 1

    def below_pct(idx):
        if not idx:
            return None
        return int(round(sum(1 for i in idx if bars[i][2] < vwaps[i]) / float(len(idx)) * 100))

    am_idx = [i for i in range(n) if bars[i][0][11:16] <= '11:30']
    pm_idx = [i for i in range(n) if bars[i][0][11:16] >= '13:00']

    # ① 最大回撤段（peak → 其后 trough）
    # ⛔ 创新高的那一根必须 `continue`，不能同时当回撤终点：安彩高科 11-10 的 13:15 是
    #    「从 4.98 拉到 5.28」的一根阳线，旧写法把它自己的 (high-low) 算成「急杀 5.7%」，
    #    方向正好相反（实际 +5.62%）。修完才是 13:15→14:45 真正跌 5.11%。
    peak = 0
    hi_i, lo_i, drop = 0, 0, 0.0
    for i in range(n):
        if bars[i][3] > bars[peak][3]:
            peak = i
            continue
        d = (bars[peak][3] - bars[i][4]) / bars[peak][3] * 100 if bars[peak][3] else 0
        if d > drop:
            hi_i, lo_i, drop = peak, i, d

    # ② 下跌段内平均收盘位置（下影质量：每根收在自身振幅的哪一档）
    pos = []
    for j in range(hi_i, lo_i + 1):
        rng = max(1e-9, bars[j][3] - bars[j][4])
        pos.append((bars[j][2] - bars[j][4]) / rng)
    dn_cpos = (sum(pos) / len(pos)) if pos else 1.0

    # ③ 回升：最低点之后 6 根内的最大反弹 + 量能对比 + 是否站住均价线
    dn_vol = sum(bars[j][5] for j in range(hi_i, lo_i + 1)) or 1e-9
    up_end = min(n, lo_i + 7)
    up_vol = sum(bars[j][5] for j in range(lo_i + 1, up_end))
    rebound = 0.0
    if lo_i + 1 < n and bars[lo_i][4]:
        rebound = (max(bars[j][3] for j in range(lo_i, up_end)) / bars[lo_i][4] - 1) * 100
    ratio = up_vol / dn_vol
    stand = sum(1 for j in range(lo_i + 1, min(n, lo_i + 4)) if bars[j][2] >= vwaps[j])

    # ④ 承接定性：先看弹不弹得回来、再分有量/无量
    if stand >= 2 and rebound >= 1.0:
        absorb = 2 if (ratio >= 0.8 or dn_cpos >= 0.55) else 1
    elif rebound >= 0.5:
        absorb = 1
    else:
        absorb = 0

    # ⑤ 爆量拉升：单根 ≥3× 当日均量且收涨，取量最大的那根
    avgv = sum(b[5] for b in bars) / n
    boom = None
    for i in range(1, n - 1):
        if bars[i][5] < 3 * avgv:
            continue
        if (bars[i][2] / bars[i - 1][2] - 1) * 100 < 0.5:
            continue
        if boom is None or bars[i][5] > bars[boom][5]:
            boom = i
    boom_min, boom_pct, boom_mult, boom_give = -1, 0.0, 0.0, -1
    if boom is not None:
        boom_min = minutes_of(bars[boom][0][11:16])
        boom_pct = round((bars[boom][2] / bars[boom - 1][2] - 1) * 100, 2)
        boom_mult = round(bars[boom][5] / avgv, 2) if avgv else 0
        base = bars[boom - 1][2]
        peak_after = max(bars[j][3] for j in range(boom, n))
        span = peak_after - base
        boom_give = int(round((peak_after - bars[-1][2]) / span * 100)) if span > 1e-9 else 0

    dhi = max(b[3] for b in bars)
    dlo = min(b[4] for b in bars)
    cpos = int(round((bars[-1][2] - dlo) / max(1e-9, dhi - dlo) * 100))

    def mm(hhmm):
        m = minutes_of(hhmm)
        return -1 if m is None else int(m)

    # 收在**昨收**下方的根数占比。用户看分时图上「红绿」就是拿昨收当基准的，
    # 而「站均价线」是另一回事——安彩 11-10 全天 94% 的时间在昨收之下（绿），
    # 但只有 6% 在均价线下方（因为均价本身也在昨收之下）。两个基准必须同时给，
    # 否则用户会觉得「明明全绿，怎么说站在线上」。
    below_prev = -1
    if prev_close:
        below_prev = int(round(sum(1 for b in bars if b[2] < prev_close) / float(n) * 100))

    return [
        bars[-1][0][5:10],                       # 0  MM-DD
        int(round(below / n * 100)),             # 1  全天均价线下方%
        below_pct(am_idx) if am_idx else -1,     # 2  上午%
        below_pct(pm_idx) if pm_idx else -1,     # 3  下午%
        round((bars[-1][2] / vwaps[-1] - 1) * 100, 1) if vwaps[-1] else 0.0,   # 4 收盘偏离%
        cpos,                                    # 5  收盘振幅位置%
        mm(bars[min(range(n), key=lambda i: bars[i][4])][0][11:16]),           # 6 最低点分钟
        round(drop, 1),                          # 7  最大回撤跌幅%
        mm(bars[hi_i][0][11:16]),                # 8  回撤起点分钟
        mm(bars[lo_i][0][11:16]),                # 9  回撤终点分钟
        round(dn_cpos, 2),                       # 10 下跌段下影质量
        round(rebound, 1),                       # 11 回撤后回升%
        round(ratio, 2),                         # 12 回升量/下跌量
        stand,                                   # 13 回升后站住根数 0~3
        absorb,                                  # 14 承接类型 0/1/2
        boom_min,                                # 15 爆量时刻（-1 无）
        boom_pct,                                # 16 爆量涨幅%
        boom_mult,                               # 17 爆量倍数
        boom_give,                               # 18 爆量后回吐%
        below_prev,                              # 19 收在昨收下方%（-1=取不到昨收）
    ]


def group_by_day(bars):
    out = {}
    for b in bars:
        out.setdefault(b[0][:10], []).append(b)
    for k in out:
        out[k].sort(key=lambda x: x[0])
    return out


def find_break_day(kl):
    """与前端 stkEnergyHtml 完全同口径地找断板日，返回下标；没有返回 -1。

    断板 = 前一日涨停(≥9.8%) + 当日放量(≥1.1×) + 当日没涨停。
    注意不能要求"收阴"：华升股份 11-09 是红盘断板（+4.10%），收阴条件会把它跳过、
    一路回溯到三个月前，解读里就会出现「08-06 首板…08-10–11-10 沉淀」的荒谬跨度。
    """
    c = [r[2] for r in kl]
    v = [r[5] for r in kl]
    n = len(kl)
    for k in range(n - 2, 20, -1):
        if c[k-1] / c[k-2] - 1 < 0.098:
            continue
        if v[k] < 1.1 * v[k-1]:
            continue
        if c[k] / c[k-1] - 1 >= 0.098:
            continue
        return k
    return -1


def pool_codes(D):
    """需要分时证据的代码：陈学长严格/放宽/观察 + 断板反包确认/观察。

    R100z351（用户 2026-11-11 18:06 采纳「窗口放到 20 天」时一起改）：
    原来只取**确认池**，观察池一个都不抓。可实际点开弹窗最多的恰恰是观察池——
    「形态进行中、只差最后一根确认」的票，读者最想看的就是它的承接。
    结果页面上大部分标的只有日线描述、没有分时，看起来像「这块对这只票没有」。
    观察池加上后实测约 120 只 / 序列化约 36KB（data.js 是 8MB 量级，可接受）。"""
    out = set()
    cp = D.get('chenPool') or {}
    db = D.get('duanban') or {}
    for key in ('strict', 'loose', 'watching'):
        for e in (cp.get(key) or []):
            if isinstance(e, dict) and e.get('code'):
                out.add(str(e['code']))
    for key in ('confirmed', 'watching'):
        for e in (db.get(key) or []):
            if isinstance(e, dict) and e.get('code'):
                out.add(str(e['code']))
    return out


def main():
    argv = sys.argv[1:]
    do_write = '--write' in argv
    do_prune = '--prune' in argv
    quiet = '--quiet' in argv

    import common as CD

    D = CD.load_dashboard_data(BASE)
    if not D:
        print('[ERR] 读不到 data.js')
        return 2
    K = D.get('stkKlines') or {}
    old = D.get('stkIntraday') or {}
    inpool = pool_codes(D)
    if not inpool:
        print('[WARN] 当前没有确认池标的，跳过')
        return 0

    # 1) 挑出要更新的票：断板后 ≤KEEP_DAYS 日
    tasks = []
    skipped_old = 0
    for code in sorted(inpool):
        kl = K.get(code) or []
        if len(kl) < 26:
            continue
        d = find_break_day(kl)
        if d < 0:
            continue
        passed = len(kl) - 1 - d
        if passed > KEEP_DAYS:
            skipped_old += 1
            continue
        tasks.append((code, kl[d][0].replace('-', ''), kl[-1][0].replace('-', ''), d))

    if not quiet:
        print('确认池 %d 只｜断板后 ≤%d 日待更新 %d 只｜断板过久跳过 %d 只｜已有旧数据 %d 只'
              % (len(inpool), KEEP_DAYS, len(tasks), skipped_old, len(old)))

    # 2) 并发抓取
    t0 = time.time()
    fetched, failed = {}, []
    if tasks:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(fetch_5m, code, beg, end): code for code, beg, end, _ in tasks}
            for fu in as_completed(futs, timeout=DEADLINE_S + 30):
                code = futs[fu]
                try:
                    r = fu.result()
                except Exception:
                    r = None
                if r:
                    fetched[code] = r
                else:
                    failed.append(code)

    # 3) 算摘要
    fresh = {}
    for code, beg, end, d in tasks:
        bars = fetched.get(code)
        if not bars:
            continue
        byday = group_by_day(bars)
        # 日线里建「当日 → 昨日收盘」，供「收在昨收下方%」用（用户看红绿就是这个基准）
        _kl = K.get(code) or []
        prev_of = {}
        for _i in range(1, len(_kl)):
            prev_of[str(_kl[_i][0])] = _kl[_i - 1][2]
        rows = []
        for day in sorted(byday):
            dig = day_digest(byday[day], prev_of.get(day))
            if dig:
                rows.append(dig)
        if rows:
            fresh[code] = rows
            write_cache(code, {'at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                               'rows': rows})

    # 4) 合并（防清场：抓不到就保留旧值）
    out = dict(old)
    for code, rows in fresh.items():
        out[code] = rows
    for code in failed:
        if code not in out:
            continue        # 本来就没有，不新增空值
        # 保留旧值；只在有缓存时用缓存刷新
        c = read_cache(code)
        if c and c.get('rows'):
            out[code] = c['rows']

    pruned = 0
    if do_prune:
        for code in list(out):
            kl = K.get(code) or []
            if not kl:
                continue
            d = find_break_day(kl)
            if d >= 0 and (len(kl) - 1 - d) > PRUNE_DAYS and code not in inpool:
                out.pop(code, None)
                pruned += 1

    if not quiet:
        print('抓到 %d 只｜失败 %d 只｜prune %d 只｜最终条目 %d 只｜耗时 %.0fs'
              % (len(fresh), len(failed), pruned, len(out), time.time() - t0))
        if failed:
            print('  失败：%s' % ('、'.join(failed[:8]) + ('…' if len(failed) > 8 else '')))
        size = len(json.dumps(out, ensure_ascii=False, separators=(',', ':')))
        print('  stkIntraday 序列化体积 %.1f KB' % (size / 1024.0))

    if not do_write:
        if not quiet:
            print('（干跑，未写盘；加 --write 落盘）')
        return 0 if fetched or not tasks else 1

    if not fresh and old:
        print('[WARN] 本轮一只都没抓到，保留旧值不写盘（防清场）')
        return 1

    # 5) 持锁写盘
    import subprocess
    lock_task = 'intraday-digest'
    rc = subprocess.run([sys.executable, os.path.join(BASE, 'pipeline_lock.py'),
                         'acquire', '--task', lock_task, '--wait', '300'],
                        cwd=BASE, check=False).returncode
    # 拿不到锁就不要写：pipeline_lock 的 3 = HELD（别人正持有）。
    # 原来这里没看返回码，锁被 16:00/19:30 占着时照样写盘，等于绕过锁。
    # 宁可本轮跳过（明天还有一次），也不能和主线抢写。
    if rc != 0:
        print('[LOCK][SKIP] 拿不到 data 锁（rc=%d），本轮不写盘，保留旧值' % rc)
        return 1
    try:
        D2 = CD.load_dashboard_data(BASE) or D
        D2['stkIntraday'] = out
        CD.write_data_js(os.path.join(BASE, 'data.js'), D2, label='stkIntraday')
        print('[OK] stkIntraday 已写入 %d 只（%d 只本轮更新）' % (len(out), len(fresh)))
    finally:
        subprocess.run([sys.executable, os.path.join(BASE, 'pipeline_lock.py'),
                        'release', '--task', lock_task], cwd=BASE, check=False)
    return 0


if __name__ == '__main__':
    _help_guard()
    sys.exit(main())

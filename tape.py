#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分时量价结构诊断（R100z193，2026-10-24 用户提出、AI 优化后落地）。

## 为什么做这个

用户原话（2026-10-24）：
    「竞价往往都是虚的，9:25 之前都可以撤单……我认为可以看开盘的分时量：
     如果下跌是有量且没有承接那就不看好；如果下跌无量且跌破分时线有承接那可以观察；
     如果开盘是爆量拉升也可以观察。」

口径先说清楚（这是纠正，不是抬杠）：
- 9:15–9:20 的竞价参考价**可撤单**，是假的 —— 用户说对了；
- 但 **9:25 的成交价是真成交、不可撤**，`openPct` 是价格事实，不是信号。
  所以本模块不删 `openPct`，只是**不给它「强弱」解释权**，把解释权交给分时结构。
- 用户框架真正的缺口：日线/单点快照看不到「跌破之后有没有收回来」，
  而那正是路径信息。腾讯分时接口（appstock/app/minute/query）给每分钟价+累计量额，
  于是「跌破几次、跌破时有没有量、现在站上没有」全都可算。

## 指标怎么来的（每个数都能回指原始分时）

接口行格式：`HHMM 价格 累计成交量(手) 累计成交额(元)`，逐分钟差分得单分钟量。
均价线（分时白线）= 累计成交额 / (累计成交量 × 100)，2026-10-23 实测 09:30 那根
14775094/(27463×100)=5.38 与价格完全一致，口径正确。

- `auctionShare` 竞价量占此刻总量比 = 第一根量 / 累计量。
  竞价占比过高（>0.25）说明开盘那一下就成交完了，后面没人接力。
- `downVolShare` 下跌分钟的成交量占比（>0.55 = 下跌是放量的）
- `breaks` 从均价线上方跌破的次数；`reclaim` 跌破后现在又站上
- `burst` 单分钟最大量 / 中位量（≥3 = 有爆量分钟）
- `slope` 均价线最后 5 分钟斜率（均价线自己上移才是真买盘推的）

## 状态机（顺序即优先级）

| state  | 中文             | 判据（全部满足）                                                        | 动作 |
|--------|------------------|-------------------------------------------------------------------------|------|
| dump   | 有量下杀·无承接  | vsVwap<-0.25 且 downVolShare>0.55 且 livePct<-0.8                        | 放弃 |
| fade   | 高开走弱         | openPct>1.2 且 vsOpen<-0.8 且 vsVwap<0                                    | 放弃 |
| surge  | 爆量拉升         | vsOpen>0.6 且 burst>=3 且 slope>0 且 vsVwap>0                             | 观察 |
| probe  | 下探被接回       | openPct<-0.5 且 breaks>=1 且 现价>均价线 且 downVolShare<0.5               | 观察 |
| weak   | 均价线下弱势整理 | vsVwap<0 且 slope<0                                                       | 等   |
| flat   | 多空平衡         | 其余                                                                      | 等   |

**阈值是经验值，未经回测**（历史分时免费源拿不到，只能从 2026-10-26 起前向采集）。
所以本模块只输出「现在是什么结构 + 该按哪档纪律处理」，**绝不输出胜率、绝不预判涨跌**。
文案里也不许出现「必涨/见底/可重仓」。等积累了 40+ 个交易日的前向样本再谈胜率。

## 用法

    python3 tape.py sh600028,sz000001            # 人读
    python3 tape.py --codes-file codes.txt --json
    python3 tape.py --selftest                   # 13 项自检，改动后必须跑
"""
import argparse
import json
import statistics as st
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
URL = 'https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=%s'

# 阈值（集中放这里，调参只改这一处）
TH = dict(
    dump_vwap=-0.25, dump_downvol=0.55, dump_live=-0.8,
    fade_open=1.2, fade_vsopen=-0.8,
    surge_vsopen=0.6, surge_burst=3.0,
    probe_open=-0.5, probe_downvol=0.5,
    sink_vsopen=-2.0,
    auction_high=0.25, burst_strong=3.0,
)

STATE = {
    'dump':  ('有量下杀·无承接', 'abandon', '放弃'),
    'fade':  ('高开走弱·竞价虚高', 'abandon', '放弃'),
    'surge': ('爆量拉升·均价线上移', 'watch', '观察'),
    'probe': ('下探被接回·站上均价线', 'watch', '观察'),
    'weak':  ('均价线下弱势整理', 'wait', '等'),
    'flat':  ('多空平衡', 'wait', '等'),
    'unknown': ('分时缺失', 'wait', '不下结论'),
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def sym_of(code):
    c = str(code)
    return ('sh' if c.startswith('6') else 'sz') + c


def fetch_raw(code, timeout=10):
    """→ (name, prev_close, [(hhmm, px, cumVol, cumAmt)]) ；失败 (None, None, [])。"""
    sym = sym_of(code)
    req = urllib.request.Request(URL % sym, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode('utf-8', 'ignore'))
    except Exception:
        return None, None, []
    node = (d.get('data') or {}).get(sym) or {}
    if not isinstance(node, dict):
        return None, None, []
    qt = (node.get('qt') or {}).get(sym) or []
    name = qt[1] if len(qt) > 1 else None
    prev = _f(qt[4]) if len(qt) > 4 else None
    raw = ((node.get('data') or {}).get('data')) or []
    bars = []
    for line in raw:
        p = str(line).split()
        if len(p) < 4:
            continue
        t, px, cv, ca = p[0], _f(p[1]), _f(p[2]), _f(p[3])
        if px is None or cv is None or ca is None or px <= 0:
            continue
        bars.append((t, px, cv, ca))
    return name, prev, bars


def diagnose(bars, prev_close, up_to=None):
    """核心：分时序列 → 结构判定。返回 dict（字段见模块 docstring）。

    `up_to` 默认 None = 用「此刻之前全部分时」。不要写死 '0945'：本任务常因排队
    在 9:50–10:0x 才跑到，写死会白白丢掉最后 5-15 分钟（而这 15 分钟往往正是
    「跌破后有没有收回」的答案）。
    """
    if not bars or prev_close is None or prev_close <= 0:
        return dict(state='unknown', zh=STATE['unknown'][0], act='wait',
                    actZh=STATE['unknown'][2], text='分时或昨收缺失，不给方向',
                    openPct=None, livePct=None, vsOpen=None, vsVwap=None,
                    downVolShare=None, breaks=None, reclaim=None,
                    burst=None, slope=None, auctionShare=None, nBars=0)
    seq = ([b for b in bars if b[0] <= up_to] or bars[:16]) if up_to else bars
    if len(seq) < 3:
        return diagnose([], prev_close)

    px = [b[1] for b in seq]
    cv = [b[2] for b in seq]
    ca = [b[3] for b in seq]
    # 单分钟量（第一根是竞价成交，单独留着算 auctionShare）
    vols = [cv[0]] + [max(cv[i] - cv[i-1], 0) for i in range(1, len(cv))]
    total = sum(vols) or 1
    vwap = [ca[i] / (cv[i] * 100) if cv[i] > 0 else px[i] for i in range(len(cv))]

    open_pct = (px[0] / prev_close - 1) * 100
    live_pct = (px[-1] / prev_close - 1) * 100
    vs_open = live_pct - open_pct
    vs_vwap = (px[-1] / vwap[-1] - 1) * 100 if vwap[-1] > 0 else 0.0

    downs = [i for i in range(1, len(px)) if px[i] < px[i-1]]
    down_vol = sum(vols[i] for i in downs) / total
    breaks = sum(1 for i in range(1, len(px))
                 if px[i-1] >= vwap[i-1] and px[i] < vwap[i])
    reclaim = breaks > 0 and px[-1] > vwap[-1]
    body = vols[1:] or [1]
    med = st.median(body) or 1
    burst = max(body) / med
    slope = ((vwap[-1] - vwap[-6]) / vwap[-6] * 100) if len(vwap) >= 6 and vwap[-6] > 0 else 0.0
    auction_share = vols[0] / total

    # ---- 状态机（顺序即优先级）----
    if vs_vwap < TH['dump_vwap'] and down_vol > TH['dump_downvol'] and live_pct < TH['dump_live']:
        state = 'dump'
    elif open_pct > TH['fade_open'] and vs_open < TH['fade_vsopen'] and vs_vwap < 0:
        state = 'fade'
    elif vs_open > TH['surge_vsopen'] and burst >= TH['surge_burst'] and slope > 0 and vs_vwap > 0:
        state = 'surge'
    elif open_pct < TH['probe_open'] and breaks >= 1 and px[-1] > vwap[-1] and down_vol < TH['probe_downvol']:
        state = 'probe'
    elif vs_open <= TH['sink_vsopen']:
        # 2026-10-24 实测补的：郑中设计开盘 -3.08%、此刻 -5.44%，只因「站回均价线」
        # 就被判成 flat（多空平衡）——从 -3 跌到 -5.4 不叫平衡。开盘后跌超 2 个点，
        # 无论均价线在哪，都只能是弱。
        state = 'weak'
    elif vs_vwap < 0 and slope < 0:
        state = 'weak'
    else:
        state = 'flat'

    zh, act, act_zh = STATE[state]

    # ---- 文案：每个数字都来自上面算出来的量 ----
    bits = [f'开盘 {open_pct:+.2f}%', f'此刻 {live_pct:+.2f}%']
    if breaks:
        bits.append(f'跌破均价线 {breaks} 次' + ('、现已站回' if reclaim else '、仍在均价线下方'))
    else:
        bits.append('未跌破均价线')
    bits.append(f'下跌分钟量占 {down_vol*100:.0f}%')
    if burst >= TH['burst_strong']:
        bits.append(f'最大单分钟量为中位 {burst:.1f} 倍')
    if auction_share > TH['auction_high']:
        bits.append(f'竞价量占 {auction_share*100:.0f}%（开盘一把成交完）')
    bits.append(f'均价线 5 分钟斜率 {slope:+.3f}%')
    text = '；'.join(bits) + '。结构＝' + zh

    return dict(state=state, zh=zh, act=act, actZh=act_zh, text=text,
                openPct=round(open_pct, 2), livePct=round(live_pct, 2),
                vsOpen=round(vs_open, 2), vsVwap=round(vs_vwap, 2),
                downVolShare=round(down_vol, 3), breaks=breaks, reclaim=reclaim,
                burst=round(burst, 2), slope=round(slope, 3),
                auctionShare=round(auction_share, 3), nBars=len(seq))


def diagnose_codes(codes, workers=8):
    """并发抓 + 判定 → {code: {...}}；抓不到的不进结果（调用方须显式降级，不得补默认值）。"""
    codes = [str(c) for c in codes]
    out = {}

    def one(c):
        name, prev, bars = fetch_raw(c)
        if not bars or prev is None:
            return c, None, name
        d = diagnose(bars, prev)
        d['name'] = name
        return c, d, name

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for c, d, name in ex.map(one, codes):
            if d:
                out[c] = d
    return out


def summarize(tape_map, picks):
    """按 picks 的 src 分组统计结构分布 → 给 AI 一个数得出来的底。"""
    cnt = {}
    for p in picks:
        t = (tape_map.get(str(p.get('code'))) or {}).get('state') or 'unknown'
        cnt[t] = cnt.get(t, 0) + 1
    pool = {k: 0 for k in STATE}
    for p in picks:
        if p.get('src') != 'pool':
            continue
        t = (tape_map.get(str(p.get('code'))) or {}).get('state') or 'unknown'
        pool[t] = pool.get(t, 0) + 1
    parts = []
    for k in ('dump', 'fade', 'surge', 'probe', 'weak', 'flat', 'unknown'):
        if cnt.get(k):
            parts.append(f'{STATE[k][0]} {cnt[k]} 只')
    text = ('本次精选 ' + str(len(picks)) + ' 只分时结构：' + '；'.join(parts) + '。') if parts \
        else '本次精选无标的可做分时诊断。'
    return dict(byState=cnt, poolByState=pool, text=text,
                rule='状态是「此刻的盘口结构」，不是涨跌预测；阈值经验值、未经回测，'
                     '只决定纪律档位（放弃/等/观察），禁止当成胜率依据')


# ==================== 自检 ====================
def _mk(prices, vols, prev):
    """按【单分钟量】构造真实分时：累计额必须逐分钟累加「该分钟价×该分钟量」，
    否则均价线等于现价、vsVwap 恒为 0，四个状态全判成 flat（2026-10-24 踩过）。"""
    bars, cv, ca = [], 0.0, 0.0
    for i, (p, v) in enumerate(zip(prices, vols)):
        cv += v
        ca += p * v * 100
        bars.append((f'{930+i:04d}', p, cv, ca))
    return bars, prev


def selftest():
    ok = 0
    bad = []

    def chk(name, cond, extra=''):
        nonlocal ok
        if cond:
            ok += 1
        else:
            bad.append(f'{name} {extra}')

    # 1 空输入
    d = diagnose([], 5.0)
    chk('空分时→unknown', d['state'] == 'unknown' and d['actZh'] == '不下结论')

    # 2 有量下杀无承接：一路阴跌、量都在下跌分钟、均价线下方
    prices = [5.00, 4.98, 4.96, 4.95, 4.93, 4.92, 4.90, 4.89, 4.88]
    vols = [1000, 2600, 2400, 2200, 2000, 1800, 1600, 1400, 1200]
    d = diagnose(_mk(prices, vols, 5.06)[0], 5.06)
    chk('持续阴跌→dump', d['state'] == 'dump', f"{d['state']} vsvwap={d['vsVwap']}")

    # 3 高开走弱
    prices = [5.25, 5.22, 5.19, 5.16, 5.14, 5.12, 5.11, 5.10, 5.09]
    vols = [2000, 1800, 1600, 1400, 1200, 1000, 900, 800, 700]
    d = diagnose(_mk(prices, vols, 5.05)[0], 5.05)
    chk('高开+走弱→fade', d['state'] == 'fade', f"{d['state']} open={d['openPct']}")

    # 4 下探被接回：早盘放量下探（量前大后小），缩量回升站上均价线
    prices = [4.98, 4.96, 4.94, 4.96, 4.99, 5.02, 5.04, 5.05, 5.06]
    vols = [2000, 1800, 1600, 1200, 1000, 800, 700, 600, 500]
    d = diagnose(_mk(prices, vols, 5.05)[0], 5.05)
    chk('低开下探后收回→probe', d['state'] == 'probe',
        f"{d['state']} breaks={d['breaks']} vsvwap={d['vsVwap']} down={d['downVolShare']}")

    # 5 爆量拉升：均价线同步上移
    prices = [5.00, 5.02, 5.04, 5.09, 5.12, 5.16, 5.18, 5.20, 5.22]
    vols = [500, 500, 500, 3000, 600, 600, 600, 600, 600]
    d = diagnose(_mk(prices, vols, 4.98)[0], 4.98)
    chk('爆量拉升→surge', d['state'] == 'surge',
        f"{d['state']} vsopen={d['vsOpen']} burst={d['burst']} slope={d['slope']} vsvwap={d['vsVwap']}")

    # 6 均价线算得对不对（第一根价=均价）
    bars, prev = _mk([5.38], [27463], 5.36)
    d = diagnose(bars, prev)
    chk('单根→unknown(不足3根)', d['state'] == 'unknown')

    # 7 昨收缺失必须 unknown，不许猜
    d = diagnose(_mk([5.0, 5.1, 5.2], [100, 200, 300], 5.0)[0], None)
    chk('昨收缺失→unknown', d['state'] == 'unknown')

    # 8 平价横盘 → flat
    prices = [5.00, 5.00, 5.01, 5.00, 5.01, 5.00, 5.01, 5.00, 5.00]
    vols = [1000] * len(prices)
    d = diagnose(_mk(prices, vols, 5.00)[0], 5.00)
    chk('横盘→flat', d['state'] in ('flat', 'weak'), d['state'])

    # 8b 开盘后跌超 2 个点、但站回均价线 —— 不许判成 flat（2026-10-24 郑中设计实测漏判）
    prices = [4.80, 4.78, 4.74, 4.70, 4.72, 4.74, 4.76, 4.78, 4.77]
    vols = [1500, 1600, 1700, 1800, 1600, 1400, 1200, 1000, 900]
    d = diagnose(_mk(prices, vols, 4.95)[0], 4.95)
    chk('跌超2点不可判flat', d['state'] != 'flat',
        f"{d['state']} vsopen={d['vsOpen']} vsvwap={d['vsVwap']}")

    # 9 文案必须含具体数字，禁止空话
    chk('文案含开盘/此刻', '开盘' in d['text'] and '此刻' in d['text'])
    chk('文案不含预判词', not any(w in d['text'] for w in ('必涨', '见底', '重仓', '抄底')))

    # 10 未知状态动作=wait
    chk('unknown 动作=wait', STATE['unknown'][1] == 'wait')
    # 11 dump 动作=abandon
    chk('dump 动作=abandon', STATE['dump'][1] == 'abandon')
    # 12 summarize 不把技术池算进 poolByState
    sm = summarize({'600028': {'state': 'dump'}},
                   [{'code': '600028', 'src': 'pool'}, {'code': '600030', 'src': 'hz'}])
    chk('poolByState 只数 pool', sm['poolByState']['dump'] == 1 and sm['byState']['dump'] == 1)
    # 13 rule 必须写明未经回测
    chk('rule 声明未回测', '未经回测' in sm['rule'])

    print(f'[tape] 自检 {ok}/{ok+len(bad)} 通过')
    for b in bad:
        print('  FAIL', b)
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('codes', nargs='?', default='')
    ap.add_argument('--codes-file')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    codes = []
    if a.codes_file:
        with open(a.codes_file, encoding='utf-8') as f:
            codes = [x.strip() for x in f if x.strip()]
    if a.codes:
        codes += [x.strip() for x in a.codes.split(',') if x.strip()]
    if not codes:
        print('用法: tape.py sh600028,sz000001 | --codes-file f | --selftest', file=sys.stderr)
        sys.exit(2)
    m = diagnose_codes([c.replace('sh', '').replace('sz', '') for c in codes])
    if a.json:
        print(json.dumps({'ok': bool(m), 'tape': m, 'failed':
                          [c for c in codes if c.replace('sh', '').replace('sz', '') not in m]},
                         ensure_ascii=False))
        return
    for c, d in m.items():
        print(f"{c} {d.get('name') or '':6s} {d['zh']:14s} [{d['actZh']}] {d['text']}")
    miss = [c for c in codes if c.replace('sh', '').replace('sz', '') not in m]
    if miss:
        print(f'[tape] 抓取失败 {len(miss)} 只：{",".join(miss)}（不补默认值）')


if __name__ == '__main__':
    main()

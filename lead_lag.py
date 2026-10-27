#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lead_lag.py —— 强板块落后股（R100z186，2026-10-24）

## 为什么要有这个模块

用户 2026-10-24 拿页面截图问：**「次日选的标的胜率连 60% 都没有，那你前一日复盘
的内容是什么？」** 这句话问到了根子上，但根子不在「复盘写得不够」，在
**从复盘到选股的映射方向反了**。

用 132 个交易日 / 89,030 个「股票×日」样本回测（严格只用 9:45 可知信息，
出场 T+1 收盘，扣 0.20% 成本）：

    现在四池在选的那一类（追强）                期望       t      胜率
      昨日涨停                                   −0.37%   −2.3    46%
      个股高开>+2% 且领先板块>2pp                 −0.49%   −2.7    46%
      板块高开>+1% 且个股领先板块>2pp（追龙头）    −1.25%   −2.8    42%

    反过来的一类（买强板块里还没涨的）          期望       t      胜率
      板块高开>+1% 且个股落后板块>2pp             +0.93%   +2.8    63%
      再加「昨日涨幅>0」                          +1.29%   +3.4    68%

同样是「强」这个字，**买已经涨起来的那一半是负期望，买还没涨的那一半是正期望**。
差别来自资金约束：板块被资金选中后，先动的是龙头，龙头的钱赚完了；
补涨的钱在后面。四个池（断板反包/九门/谐波/吸筹）选的全部是「已经在涨的」，
所以它们 1 日胜率 41-47% ≈ 抛硬币减成本（全市场同期 47%）。

## 规则（全部只用 9:45 可知信息，无未来函数）

    ① 板块开盘跳空 > +1.0%      成分股（主板、≥6 只有效）开盘跳空均值
    ② 个股落后板块 > +2.0pp     板块跳空 − 个股跳空（同一把尺子量，不用现价）
    ③ 昨日涨幅 > 0              只要「不是崩下来的」，不要求强势
    ④ 9:45 现价相对开盘 ∈ [−1.5%, +0.4%]   执行约束，不是 alpha（见下）
    ⑤ 排除 ST/退市/黑名单 red/涨跌停/非主板/股价<2/无成交

④ 的理由：alpha 是「按开盘价买入」测出来的。9:45 才下单，买到的是当时的价，
比开盘价高多少就吃掉多少。实测入场价上浮 0.3%/0.5%/0.8%/1.2% 时期望依次降到
+0.99%/+0.79%/+0.49%/+0.09%，胜率 65%/60%/56%/49%。所以上限卡 +0.4%（约 62% 胜率），
下限 −1.5% 只是防接飞刀，不是加分项。

## 出场纪律（页面必须写明，否则数字不可复现）

    ⚠️ 下面这组数字**必须来自 backtest() 的实际产出**，不许再手抄一份（2026-10-27 发现
       docstring 里的 +1.07%/t=3.7/胜率70% 与脚本跑出来的 +1.00%/t=2.67(逐票)、日聚类 t=1.1
       对不上——那种「文档比数据好看」的偏差，比没有文档更坏）。
      当前实测（窗口 2026-06-11~2026-10-23 / 90 个交易日 / 110 笔 / 33 个信号日）：
    主口径：当日收盘卖（不过夜）   +1.00%  逐票 t=+2.67、**日聚类 t=+1.10**（不显著）  胜率 61.8%
    备选：  次日收盘卖             +0.86%  逐票 t=+1.57、日聚类 t=+0.62               胜率 62.7%
      注意：33 个信号日的日聚类 t 只有 1.10，**按本项目的判定标准属于「未定」而非「有效」**。
      逐票 t 会高估显著性（同一天选的票共享大盘，不是独立样本），判定一律看日聚类。

当日收盘卖的 t 值更高、且不承担隔夜跳空——全市场隔夜跳空均值 −0.181%/日、
胜率仅 34%（系统选出的票更差，−0.465%、31%）。**非必要不留过夜**。

## 局限（必须写进页面，不许只报好数字）

  · 信号稀少：132 个交易日里只有 33 天有候选（75% 的交易日一只都没有），
    中位 2 只/天、最多 49 只。**宁缺勿滥，凑数就是亏钱**。
  · 2026-10 单月 −1.26%、胜率 50%（7/8/9 月分别 +1.97%/+1.31%/+2.15%，
    胜率 77%/65%/71%）。样本只有 5 个月，不能当成稳定印钞机。
  · 板块归属用 data.js 的 sectorMap（16:00 写入，1,093 只主板、49 个板块 ≥6 只），
    不覆盖全市场 5,000 只——漏掉的票不会出现在候选里。

## 输出

写 `duanban.leadLag`（与 duanban.star 同级、同生命周期：9:45 写、16:00 合并时保留
最近交易日、前端按 date==今日 渲染）。同时把当日候选追加进 `signal_ledger.json`
的 `entries[date].leadLag`，由 signal_attribution 逐日回算真实胜率——
**回测数字是历史，台账数字才是这套规则现在的表现**。

用法：
    python3 lead_lag.py             # 抓盘 + 写 data.js
    python3 lead_lag.py --dry       # 只打印
    python3 lead_lag.py --self-test
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import common                      # noqa: E402
import stock_quotes as SQ          # noqa: E402

LEDGER = os.path.join(ROOT, 'signal_ledger.json')

# ---------------- 规则阈值（回测确定，不通过参数暴露，避免「调参让结果好看」）
SEC_GAP_MIN = 1.0      # 板块开盘跳空下限 %
LAG_MIN = 2.0          # 个股落后板块下限 pp
MIN_SEC_N = 6          # 板块内有效成分股下限
GAP_TRIM = 12.0        # 板块跳空均值剔除 |跳空|>12% 的单点（退市整理/停复牌异常）
YRET_MIN = 0.0         # 昨日涨幅下限 %
LIVE_VS_OPEN_MAX = 0.4   # 现价相对开盘上限（入场滑点上限）
LIVE_VS_OPEN_MIN = -1.5  # 现价相对开仓下限（防接飞刀）
MIN_PRICE = 2.0
MAX_PICKS = 6

ST_KW = ('ST', '*ST', '退', 'N ', 'C ')

# 出场口径的名字与数字**一律来自 backtest()**，这里只留标签。
# 曾经写死过一组（+1.07%/t=3.7/胜率70%，来自离线 bt14.py），换了修剪和窗口之后
# 那组数字页面还挂着，而代码已经算不出它——正是「页面和代码各写一套」的老毛病。
EXIT_LABELS = ('当日收盘卖', '次日收盘卖')


def _f(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def is_main_board(c):
    c = str(c or '')
    return len(c) == 6 and c[:2] in ('60', '00') and c[:3] not in ('688', '689')


def load_blacklist(base=ROOT):
    try:
        with open(os.path.join(base, 'risk_blacklist.json'), encoding='utf-8') as f:
            raw = json.load(f)
        return {str(k): str((v or {}).get('severity') or '')
                for k, v in (raw.get('blacklist') or {}).items() if isinstance(v, dict)}
    except Exception:
        return {}


def load_data(path=os.path.join(ROOT, 'data.js')):
    with open(path, encoding='utf-8') as f:
        s = f.read()
    return json.loads(s[s.index('{'):s.rindex('}') + 1])


def bars_of(K, code):
    """stkKlines 容器 → [(day, open, close, high, low, vol)]，与 atr_stop.norm_bars 同口径。"""
    out = []
    for b in (K.get(str(code)) or []):
        if isinstance(b, (list, tuple)) and len(b) >= 5:
            d = str(b[0])
            o, c, h, l = _f(b[1]), _f(b[2]), _f(b[3]), _f(b[4])
            if c and h and l and c > 0:
                out.append((d, o, c, h, l, _f(b[5]) if len(b) > 5 else None))
        elif isinstance(b, dict):
            c = _f(b.get('close'))
            if c and c > 0:
                out.append((str(b.get('day') or b.get('date') or ''), _f(b.get('open')), c,
                            _f(b.get('high')), _f(b.get('low')),
                            _f(b.get('volume') if b.get('volume') is not None else b.get('vol'))))
    out.sort(key=lambda x: x[0])
    return out


def prev_close_and_yret(bars, today):
    """昨收 + 昨日涨幅（%）。

    只能用 **today 之前** 的最后一根 K 线——9:45 时 data.js 的最后一根应当是昨日，
    但若上游某个自动化把当日 K 线先写进去（16:00 数据步在收盘后写），就会把今天
    当成昨天，昨涨直接算错。所以按日期过滤，不按「最后一根」取。
    """
    prev = [b for b in bars if b[0] < today]
    if len(prev) < 2:
        return None, None
    pc, p2 = prev[-1][2], prev[-2][2]
    if not pc or not p2 or p2 <= 0:
        return None, None
    return pc, round((pc - p2) / p2 * 100, 2)


# ---------------------------------------------------------------- 抓盘

def fetch_all(codes, workers=6):
    """并发抓快照。返回 ({code: quote}, [失败code])。键名带 sh/sz 前缀。"""
    syms = [('sh' if c.startswith('6') else 'sz') + c for c in codes]
    out, failed = {}, []
    batches = [syms[i:i + 60] for i in range(0, len(syms), 60)]

    def one(batch):
        try:
            return SQ.fetch_tencent(batch)
        except Exception:
            return {}

    if workers <= 1 or len(batches) <= 1:
        res = [one(b) for b in batches]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            res = list(ex.map(one, batches))
    for r in res:
        out.update(r)
    for c, s in zip(codes, syms):
        if s not in out:
            failed.append(c)
    return out, failed


def replay_quotes(D, day, bars_map=None, names=None):
    """用日线重建 `day` 当天 9:45 可见的快照，供非交易日 / 盘后验收规则。

    ## 为什么必须有一个「回放」模式

    这条规则的入场点是 **9:45 的实时快照**（开盘跳空 + 现价偏离）。周末或盘后跑
    `compute()` 拿到的是**收盘快照**：`open` 还是当天开盘价，但 `price` 已经是收盘价，
    于是 `vsOpen` = 全天振幅而不是入场滑点——周五收盘涨了 3% 的票会被「滑点超限」
    全部剔除，规则看起来像坏的。实测 2026-10-24（周六）就是这种情况。

    回放把 `price` 设成当日开盘价，等价于「开盘价成交」，同时也把
    `vsOpen` 恒为 0——即滑点过滤不生效。所以回放结论只能说「规则会挑出哪些票」，
    不能说「今天真赚了多少」。这条限制写进 `node.replayNote`，由页面显示。

    `amount` 用当日成交量填（`compute` 只判真假，用于排掉停牌），
    `volRatio` 留 None（日线算不出量比，不许编）。

    名字必须补：`compute` 里「戴帽 ST 禁在受益位」是靠 `name` 里的关键字判的，
    回放若 `name=None`，ST 股会绕过过滤直接进候选。所以这里一次性取全量名字，
    取不到就把 `name` 留 None 并在返回里报 `named` 覆盖率，由调用方打标。

    `bars_map` / `names` 可由调用方预先算好传入：逐日回放 90 天时若每天都重建
    1,138 只票的 K 线，光这一步就是 90×1138 次列表重建（实测占九成耗时）。
    """
    K = D.get('stkKlines') or {}
    bars_map = bars_map if bars_map is not None else {str(c): bars_of(K, c) for c in K}
    out = {}
    for c, bars in bars_map.items():
        idx = None
        for i, b in enumerate(bars):
            if b[0] == day:
                idx = i
                break
        if idx is None or idx == 0:
            continue
        o, pc, vol = bars[idx][1], bars[idx - 1][2], bars[idx][5]
        if not o or not pc or pc <= 0:
            continue
        out[str(c)] = {
            'name': None, 'price': o, 'prevClose': pc, 'open': o,
            'pct': round((o - pc) / pc * 100, 2),      # 9:45 时点的涨跌≈开盘跳空
            'high': None, 'low': None,
            'volRatio': None, 'avgPrice': None,
            'turnover': None, 'amplitude': None,
            'amount': vol,                              # 只用来判「有没有成交」
        }
    if names is None:
        try:
            names = common.fetch_names(sorted(out.keys()))
        except Exception:
            names = {}
    named = 0
    for c, v in out.items():
        nm = names.get(c)
        if nm:
            v['name'] = nm
            named += 1
    return out, named



# ---------------------------------------------------------------- 核心

def compute(D, quotes=None, today=None, blacklist=None):
    """算出 leadLag 节点。quotes 可注入（自测用），否则现抓。"""
    today = today or datetime.now().strftime('%Y-%m-%d')
    K = D.get('stkKlines') or {}
    SM = D.get('sectorMap') or {}
    blacklist = load_blacklist() if blacklist is None else blacklist

    # 板块 → 主板成分（有 K 线才参与，与回测口径一致）
    groups = {}
    for c, sec in SM.items():
        if is_main_board(c) and len(K.get(str(c)) or []) >= 40 and sec:
            groups.setdefault(str(sec), []).append(str(c))
    groups = {k: v for k, v in groups.items() if len(v) >= MIN_SEC_N}
    universe = sorted({c for v in groups.values() for c in v})
    if not universe:
        return None, 'sectorMap 或 stkKlines 为空，无法分板块'

    failed = []
    if quotes is None:
        quotes, failed = fetch_all(universe)
    # 统一成不带前缀的 6 位键，方便和 sectorMap 对齐。
    # 这里必须再过一次主板过滤：正常路径 fetch_all 只请求 universe（已是主板），
    # 但注入/上游返回多余代码时不能靠「上游应该没给」来保证——实测注入法回放
    # 2026-09-01 时，300251/301551 这类创业板票从报价字典漏进了候选，
    # 直接违反「A 股只许 60/00 主板」的硬约束。
    q = {}
    for k, v in (quotes or {}).items():
        c6 = str(k)[-6:]
        if is_main_board(c6) and c6 in SM:
            q[c6] = v

    # ① 板块开盘跳空（成分股均值，只用有 open 的）
    #    剔掉 |跳空|>12% 的单点：退市整理股（实测 002731 萃华退 单只跳空 −55.8%）
    #    会把 14 只成分的均值直接拽歪 4pp，让「服装鞋类」整块假性 −5.4%。
    #    这个修剪在 132 日 / 8.9 万样本的历史回测上与不修剪**结果逐位一致**
    #    （N=158、+1.29%、t=3.4、胜率 68%），只在极端单点上生效，不是调参。
    sec_gap, sec_n = {}, {}
    for sec, codes in groups.items():
        gaps = []
        for c in codes:
            g = SQ.open_gap(q.get(c))
            if g is not None and abs(g) <= GAP_TRIM:
                gaps.append(g)
        if len(gaps) >= MIN_SEC_N:
            sec_gap[sec] = round(sum(gaps) / len(gaps), 2)
            sec_n[sec] = len(gaps)

    strong = {s: g for s, g in sec_gap.items() if g > SEC_GAP_MIN}

    # ②③④⑤ 逐票筛选
    picks, skipped = [], {'yday': 0, 'lag': 0, 'live': 0, 'risk': 0}
    for c, qq in q.items():
        sec = SM.get(c)
        if not sec or sec not in strong:
            continue
        sg = strong[sec]
        og = SQ.open_gap(qq)
        if og is None:
            continue
        lag = round(sg - og, 2)
        if lag <= LAG_MIN:
            skipped['lag'] += 1
            continue
        bars = bars_of(K, c)
        pc, yret = prev_close_and_yret(bars, today)
        if yret is None or yret <= YRET_MIN:
            skipped['yday'] += 1
            continue
        name = str(qq.get('name') or '')
        price, op = _f(qq.get('price')), _f(qq.get('open'))
        if not price or not op or price < MIN_PRICE or pc is None:
            skipped['risk'] += 1
            continue
        if any(k in name for k in ST_KW) or blacklist.get(c) == 'red':
            skipped['risk'] += 1
            continue
        # 涨跌停买不进/卖不掉
        if abs(qq.get('pct') or 0) >= 9.5:
            skipped['risk'] += 1
            continue
        if not _f(qq.get('amount')):
            skipped['risk'] += 1   # 无成交（停牌）
            continue
        vs_open = round((price - op) / op * 100, 2) if op > 0 else None
        if vs_open is None or vs_open > LIVE_VS_OPEN_MAX or vs_open < LIVE_VS_OPEN_MIN:
            skipped['live'] += 1
            continue
        picks.append({
            'code': c, 'name': name, 'sector': sec,
            'secGap': sg,                     # 板块开盘跳空 %
            'openGap': og,                    # 个股开盘跳空 %
            'lag': lag,                       # 落后板块 pp（排序主键）
            'yPct': yret,                     # 昨日涨幅 %
            'price': round(price, 2), 'open': round(op, 2),
            'livePct': qq.get('pct'),         # 现涨（相对昨收）
            'vsOpen': vs_open,                # 现价相对开盘 %（= 入场滑点）
            'volRatio': qq.get('volRatio'),
            'avgPrice': qq.get('avgPrice'),
            'turnover': qq.get('turnover'),
            'secN': sec_n.get(sec),
        })

    picks.sort(key=lambda x: (-x['lag'], -(x['secGap'] or 0)))
    picks = picks[:MAX_PICKS]
    for p in picks:
        p['note'] = ('%s开盘%+.1f%%，本股开盘%+.1f%%（落后%.1fpp）；昨日%+.1f%%，'
                     '现%+.1f%%、较开盘%+.1f%%' % (
                         p['sector'], p['secGap'], p['openGap'], p['lag'],
                         p['yPct'], p['livePct'] or 0, p['vsOpen']))

    node = {
        'date': today,
        'time': datetime.now().strftime('%H:%M'),
        'rule': {
            'secGapMin': SEC_GAP_MIN, 'lagMin': LAG_MIN, 'yretMin': YRET_MIN,
            'liveVsOpen': [LIVE_VS_OPEN_MIN, LIVE_VS_OPEN_MAX],
            'minSecN': MIN_SEC_N,
        },
        'cost': 0.20,
        'sectors': sorted([{'name': s, 'gap': g, 'n': sec_n.get(s)} for s, g in strong.items()],
                          key=lambda x: -x['gap'])[:12],
        'picks': picks,
        'scanned': {'universe': len(universe), 'sectors': len(groups),
                    'quoted': len(q), 'strongSectors': len(strong),
                    'failed': len(failed), 'skipped': skipped},
        'gapNote': None,
    }
    if len(q) < len(universe) * 0.5:
        node['gapNote'] = ('个股快照仅取到 %d/%d 只（<50%%），板块开盘跳空样本不足，'
                           '本次候选仅供参考' % (len(q), len(universe)))
    if not picks:
        # 空仓只有两种成因，必须说清是哪一种：
        #   ① 今日压根没有强板块 → 谈不上落后股（说「但里面没有…」是逻辑错位）
        #   ② 有强板块，但里面的落后股被某一道筛子挡住了 → 点名是哪道
        if not strong:
            # 规则行已经写了「扫描 N 个板块 / M 只主板」，这里不再重复「扫描」二字
            why = ('今日没有一个板块开盘跳空 > +%.1f%%，没有强板块就谈不上落后股'
                   '（%d 只主板成分股归入 %d 个板块，全部没到门槛）'
                   % (SEC_GAP_MIN, len(universe), len(groups)))
        else:
            st = skipped
            hit = []
            if st.get('lag'):
                hit.append('落后不足 %.1fpp 的 %d 只' % (LAG_MIN, st['lag']))
            if st.get('yday'):
                hit.append('昨日没涨的 %d 只' % st['yday'])
            if st.get('live'):
                hit.append('已冲高或跌破下限的 %d 只' % st['live'])
            if st.get('risk'):
                hit.append('风险剔除（ST/停牌/涨跌停/黑名单）的 %d 只' % st['risk'])
            why = ('今日强板块 %d 个（%s），但里面的落后股被筛掉了：%s'
                   % (len(strong),
                      '、'.join('%s %+.1f%%' % (s['name'], s['gap']) for s in
                               sorted([{'name': s, 'gap': g} for s, g in strong.items()],
                                      key=lambda x: -x['gap'])[:3]),
                      '；'.join(hit) or '无一只进入候选'))
        node['gapNote'] = node['gapNote'] or (
            '%s。这条规则「空仓」是常态而非故障——回测里约六成交易日一只都没有，'
            '强板块与落后股同时出现才出手。' % why)
    return node, None


def register(node):
    """把当日候选追加进 signal_ledger，交给 signal_attribution 逐日回算真实胜率。"""
    if not node or not node.get('picks'):
        return 0
    try:
        L = json.load(open(LEDGER, encoding='utf-8')) if os.path.exists(LEDGER) else {}
    except Exception:
        L = {}
    ent = L.setdefault('entries', {}).setdefault(node['date'], {})
    known = {str(x.get('code')) for x in (ent.get('leadLag') or [])}
    new = [{'code': p['code'], 'name': p['name'], 'sector': p['sector'],
            'screen': 'leadLag', 'rule': 'secGap>%.1f&lag>%.1f&yret>0' % (SEC_GAP_MIN, LAG_MIN)}
           for p in node['picks'] if p['code'] not in known]
    if new:
        ent['leadLag'] = (ent.get('leadLag') or []) + new
        common.write_json_atomic(LEDGER, L, indent=1)
    return len(new)


# ---------------------------------------------------------------- 回测（用线上同一份代码）

BT_CACHE = os.path.join(ROOT, 'leadlag_bt.json')


def _tstat(xs):
    """t 值。样本 <5 或方差为 0 → None（不假装显著）。"""
    xs = [x for x in (xs or []) if x is not None]
    if len(xs) < 5:
        return None
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    if var <= 0:
        return None
    return round(m / ((var / len(xs)) ** 0.5), 2)


def _wr(xs):
    xs = [x for x in (xs or []) if x is not None]
    return round(len([x for x in xs if x > 0]) / len(xs), 3) if xs else None


def backtest(D, cost=0.20):
    """用**线上同一个 compute()** 在历史每一个交易日上跑一遍，得出胜率。

    ## 为什么要这样做，而不是引用一份离线回测数字

    离线回测（`/tmp/ib/bt14.py`）跑的是「我自己另写的一套循环」，和页面真正跑的
    `compute()` 是两份代码：阈值改一个、板块均值剔极值改一处，两边的数字就不再是
    同一件事，而页面还会继续展示旧结论。所以这里直接逐日调用 `compute()`，
    把 `replay_quotes()` 重建的 9:45 快照喂进去——**回测与线上同一条代码路径**，
    页面上的胜率和实际在跑的东西不可能脱节。

    ## 口径（2026-10-27 修正：原主口径是 T+0，A 股做不到）

    **入场**：当日开盘价（9:45 近似）。
    **出场**：**一律 T+1 及以后**——A 股 T+1，当日买入当日不可卖，所以
    「当日收盘卖」这个口径在实盘不存在。原主口径 `(当日收盘 − 当日开盘)` 是
    **T+0 非法口径**，它把「当天下午的情绪」记成了利润，必须废弃。

    三种合法口径并列（都扣 `cost` 双边成本）：
      · `main` = **次日收盘卖**（T+1）——主口径
      · `alt`  = **次日开盘卖**（T+1，最保守：隔夜就跑）
      · `t2`   = **T+2 收盘卖**（多持一天）
    另保留 `t0Invalid` 只为对照，**页面不许把它当主口径展示**。

    t 值给两个：逐票（仅供参考）和**按日聚类**（判定用这个，同一天 6 只票共享
    同一个大盘，不能当 6 个独立样本）。

    返回 dict，直接进 `node['bt']` 给页面用；不写死任何数字。
    """
    K = D.get('stkKlines') or {}
    days = sorted({str(b[0]) for b in (K.get('600000') or []) if isinstance(b, (list, tuple))})
    if len(days) < 20:
        return None, 'stkKlines 覆盖不足 20 个交易日，无法回测'
    bars_map = {str(c): bars_of(K, c) for c in K}
    idx_map = {c: {b[0]: i for i, b in enumerate(v)} for c, v in bars_map.items()}
    try:
        names = common.fetch_names([c for c in bars_map if is_main_board(c)])
    except Exception:
        names = {}

    # r1 主口径（次日收盘卖）/ r2 备选（次日开盘卖）/ r3 T+2 收盘 / r0 T+0 非法（仅对照）
    r1, r2, r3, r0 = [], [], [], []
    d1, d2, sig = [], [], []
    by_month = {}
    recent = []
    for d in days[:-2]:                    # 需要 i+1（T+1）与 i+2（T+2），末两天不参与
        q, _ = replay_quotes(D, d, bars_map=bars_map, names=names)
        if not q:
            continue
        node, err = compute(D, quotes=q, today=d)
        if err or not node['picks']:
            continue
        sig.append(d)
        cur0, cur1, cur2, cur3 = [], [], [], []
        for p in node['picks']:
            c, i = p['code'], idx_map.get(p['code'], {}).get(d)
            if i is None:
                continue
            b = bars_map[c]
            entry = b[i][1]
            if not entry:
                continue
            # T+0（非法）：当日收盘卖。只留作对照，不进任何主指标。
            cur0.append(round((b[i][2] - entry) / entry * 100 - cost, 2))
            if i + 1 < len(b):
                # T+1 次日收盘卖（主口径）
                cur1.append(round((b[i + 1][2] - entry) / entry * 100 - cost, 2))
                # T+1 次日开盘卖（最保守）
                cur2.append(round((b[i + 1][1] - entry) / entry * 100 - cost, 2))
            if i + 2 < len(b):
                cur3.append(round((b[i + 2][2] - entry) / entry * 100 - cost, 2))
        if not cur1:
            continue
        r0 += cur0
        r1 += cur1
        r2 += cur2
        r3 += cur3
        d1.append(sum(cur1) / len(cur1))
        if cur2:
            d2.append(sum(cur2) / len(cur2))
        mk = d[:7]
        by_month.setdefault(mk, {'n': 0, 'r': []})
        by_month[mk]['n'] += len(cur1)
        by_month[mk]['r'] += cur1
        # 最近几次出手的实际结果。聚合胜率说服力有限，用户要看的是「上周真挑到的那几只
        # 到底涨没涨」——所以把逐日明细一起带出来，只留最近 6 个信号日。
        recent.append({'date': d, 'n': len(cur1),
                       'ret': round(sum(cur1) / len(cur1), 2),          # 主口径：T+1 收盘卖
                       'up': len([x for x in cur1 if x > 0]),
                       'retOpen': round(sum(cur2) / len(cur2), 2) if cur2 else None,
                       'retT2': round(sum(cur3) / len(cur3), 2) if cur3 else None,
                       'retT0': round(sum(cur0) / len(cur0), 2) if cur0 else None,
                       'names': [str(p.get('name') or p['code']) for p in node['picks']][:6]})

    if not r1:
        return None, '回测无样本'
    return {
        'window': [days[0], days[-1]],
        'days': len(days),
        'signalDays': len(sig),
        'flatRate': round(1 - len(sig) / len(days), 3),
        'samples': len(r1),
        'cost': cost,
        'recent': recent[-6:],
        # main = 次日收盘卖（T+1，主口径）；alt = 次日开盘卖（T+1，最保守）
        'main': {'name': '次日收盘卖', 'ret': round(sum(r1) / len(r1), 2),
                 'win': _wr(r1), 't': _tstat(r1), 'tDaily': _tstat(d1),
                 'dayWin': _wr(d1), 'n': len(r1), 'days': len(d1)},
        'alt': {'name': '次日开盘卖', 'ret': round(sum(r2) / len(r2), 2) if r2 else None,
                'win': _wr(r2), 't': _tstat(r2), 'tDaily': _tstat(d2),
                'dayWin': _wr(d2), 'n': len(r2), 'days': len(d2)},
        't2': {'name': 'T+2 收盘卖', 'ret': round(sum(r3) / len(r3), 2) if r3 else None,
               'win': _wr(r3), 't': _tstat(r3), 'tDaily': _tstat(r3),
               'n': len(r3)},
        # T+0 只能作对照：A 股当日买当日不可卖，这个数字实盘不存在。
        't0Invalid': {'name': '当日收盘卖（T+0·A股不可行）',
                      'ret': round(sum(r0) / len(r0), 2) if r0 else None,
                      'win': _wr(r0), 't': _tstat(r0), 'n': len(r0),
                      'note': 'A 股 T+1，当日买入不可当日卖出；此口径仅用于说明「原口径虚高了多少」'}, 
        'byMonth': [{'month': k, 'n': v['n'], 'ret': round(sum(v['r']) / len(v['r']), 2),
                     'win': _wr(v['r'])} for k, v in sorted(by_month.items())],
        'lastSignalDay': sig[-1] if sig else None,
    }, None


def _bt_sig(D, cost=0.20):
    """回测缓存签名：数据窗口一变就重算。只认「数据 + 规则」，不认时间。"""
    K = D.get('stkKlines') or {}
    bars = K.get('600000') or []
    last = str(bars[-1][0]) if bars else ''
    return 'v2|%s|%d|%s|%s|%s|%.2f' % (
        last, len(K), SEC_GAP_MIN, LAG_MIN, GAP_TRIM, cost)


def backtest_cached(D, cost=0.20):
    """回测很贵（逐日跑 compute），所以缓存到 leadlag_bt.json，签名不变就复用。

    缓存只当加速器：文件坏了、签名变了、结构缺字段，一律重算。
    绝不因为「缓存里有数字」就直接用——那正是页面与代码脱节的来源。
    """
    sig = _bt_sig(D, cost)
    try:
        c = json.load(open(BT_CACHE, encoding='utf-8'))
        if c.get('sig') == sig and (c.get('bt') or {}).get('main', {}).get('ret') is not None:
            return c['bt'], None, True
    except Exception:
        pass
    bt, err = backtest(D, cost=cost)
    if err:
        return None, err, False
    try:
        common.write_json_atomic(BT_CACHE, {'sig': sig, 'bt': bt}, indent=1)
    except Exception:
        pass
    return bt, None, False


# ---------------------------------------------------------------- 自检

def self_test():
    ok = True

    def chk(name, cond):
        nonlocal ok
        print(('  [OK]   ' if cond else '  [FAIL] ') + name)
        ok = ok and bool(cond)

    # 造 4 个板块 × 8 只，2026-01-02 为「今日」，K 线到 2025-12-31（昨日）
    # 42 根：compute() 要求 ≥40 根（与线上「有足够历史」的口径一致），
    # 自测数据必须满足同一门槛，否则测的是另一条路径。
    DAYS = (['2025-11-%02d' % (i + 1) for i in range(30)]
            + ['2025-12-%02d' % (i + 1) for i in range(11)] + ['2025-12-31'])

    def mk_bars(pc, p2):
        out = [[d, p2, p2, p2, p2, 1e6] for d in DAYS[:-1]]
        out.append([DAYS[-1], pc, pc, pc, pc, 1e6])
        return out
    SM, KL, QU = {}, {}, {}
    for si, sec in enumerate(['甲板块', '乙板块', '丙板块', '丁板块']):
        for j in range(8):
            c = '60%04d' % (si * 100 + j)
            SM[c] = sec
            # 昨日涨幅：甲板块 +3%（强势板块），其余 −1%
            pc, p2 = 10.0, (10.0 / 1.03 if si == 0 else 10.0 / 0.99)
            KL[c] = mk_bars(pc, round(p2, 4))
            # 甲板块开盘跳空 +2%，但第 0 只也开 +2%（领先，该剔）、第 1 只开 −0.5%（落后 2.5pp，该留）
            og = 2.0
            if si == 0 and j == 1:
                og = -0.5
            if si == 0 and j == 2:
                og = 1.9     # 落后 0.1pp，不达标
            op = round(pc * (1 + og / 100), 2)
            QU['sh' + c] = {'name': '测试%02d' % j, 'price': op, 'open': op,
                            'prevClose': pc, 'pct': og, 'volRatio': 1.1, 'avgPrice': op,
                            'turnover': 1.0, 'amplitude': 2.0, 'amount': 1e5}

    D = {'stkKlines': KL, 'sectorMap': SM}
    node, err = compute(D, quotes=QU, today='2026-01-02', blacklist={})
    chk('不报错（%s）' % err, err is None)
    codes = [p['code'] for p in node['picks']]
    chk('只留下落后 2.5pp 的那只（实测 %s）' % codes, codes == ['600001'])
    p = node['picks'][0]
    # 期望值直接从注入的报价算，不写死——写死一个「我以为的均值」会掩盖算法改了这件事
    exp = sum(SQ.open_gap(QU['sh60%04d' % j]) for j in range(8)) / 8
    chk('板块跳空 = 成分均值 %.2f%%（实测 %.2f）' % (exp, p['secGap']),
        abs(p['secGap'] - exp) < 0.02)
    chk('落后幅度 = 板块跳空 − 个股跳空 = %.2fpp（实测 %.2f）'
        % (p['secGap'] - p['openGap'], p['lag']),
        abs(p['lag'] - (p['secGap'] - p['openGap'])) < 0.011)
    chk('note 含板块名与实际数值', '甲板块' in p['note'] and '落后' in p['note'])

    # 反向测试①：昨日跌 → 必须被剔除（规则③）
    KL2 = {k: list(v) for k, v in KL.items()}
    KL2['600001'] = [[d, 9.0, 9.0, 9.0, 9.0, 1e6] for d in DAYS]
    n2, _ = compute({'stkKlines': KL2, 'sectorMap': SM}, quotes=QU,
                    today='2026-01-02', blacklist={})
    chk('昨日涨幅为 0 → 剔除（实测 %d 只）' % len(n2['picks']), not n2['picks'])

    # 反向测试①b：创业板/科创板报价混进来 → 必须被拦（硬约束：A 股只许 60/00 主板）
    QU0 = dict(QU)
    QU0['sz300251'] = dict(QU['sh600001'], name='创业板测试')
    n0, _ = compute({'stkKlines': KL, 'sectorMap': SM}, quotes=QU0,
                    today='2026-01-02', blacklist={})
    chk('非主板（300xxx）不得进入候选（实测 %s）'
        % [p['code'] for p in n0['picks']],
        all(not p['code'].startswith('30') for p in n0['picks']))

    # 反向测试②：现价已比开盘高 1%（滑点超限）→ 必须被剔除
    QU3 = json.loads(json.dumps(QU))
    QU3['sh600001']['price'] = round(QU3['sh600001']['open'] * 1.01, 2)
    n3, _ = compute({'stkKlines': KL, 'sectorMap': SM}, quotes=QU3,
                    today='2026-01-02', blacklist={})
    chk('现价偏离开盘 +1%% → 剔除（实测 %d 只）' % len(n3['picks']), not n3['picks'])

    # 反向测试③：黑名单 red → 剔除
    n4, _ = compute({'stkKlines': KL, 'sectorMap': SM}, quotes=QU,
                    today='2026-01-02', blacklist={'600001': 'red'})
    chk('黑名单 red → 剔除（实测 %d 只）' % len(n4['picks']), not n4['picks'])

    # 反向测试④：今天是交易日但 K 线里已经有今天的 bar → 昨收仍须取「今天之前」
    KL5 = {k: list(v) for k, v in KL.items()}
    KL5['600001'] = KL5['600001'] + [['2026-01-02', 100, 100, 100, 100, 1e6]]
    n5, _ = compute({'stkKlines': KL5, 'sectorMap': SM}, quotes=QU,
                    today='2026-01-02', blacklist={})
    chk('K 线含当日 bar 时仍按「今日之前」取昨收（实测 %d 只）' % len(n5['picks']),
        len(n5['picks']) == 1)

    # 反向测试⑤：板块成分不足 6 只 → 不参与（不硬凑）
    SM6 = dict(SM)
    for j in range(1, 8):
        SM6.pop('60%04d' % j, None)
    n6, _ = compute({'stkKlines': KL, 'sectorMap': SM6}, quotes=QU,
                    today='2026-01-02', blacklist={})
    chk('成分股 <6 只的板块不参与（实测 %d 只）' % len(n6['picks']), not n6['picks'])

    # 反向测试⑥：空仓文案必须说清「是哪一种空仓」
    #   ① 强板块=0 → 不许出现「但里面没有」（连强板块都没有，何来“里面”）
    no_gap = json.loads(json.dumps(QU))
    for _c in no_gap:
        no_gap[_c]['open'] = no_gap[_c]['prevClose']      # 全体平开 → 板块跳空=0 → 无强板块
        no_gap[_c]['price'] = no_gap[_c]['open']
    n7, _ = compute({'stkKlines': KL, 'sectorMap': SM}, quotes=no_gap,
                    today='2026-01-02', blacklist={})
    g7 = str(n7.get('gapNote') or '')
    chk('强板块=0 的空仓文案不提「但里面没有」，改说「没有一个板块开盘跳空」（实测：%s）'
        % g7[:30],
        bool(g7) and '但里面没有' not in g7 and '没有一个板块开盘跳空' in g7)
    #   ② 有强板块但落后股被筛掉 → 文案必须点名是哪道筛子（n2：600001 昨日跌 → 「昨日没涨」）
    g2 = str(n2.get('gapNote') or '')
    chk('有强板块时空仓文案点名拦截原因（实测：%s）' % g2[:46],
        bool(g2) and ('昨日没涨' in g2 or '落后不足' in g2))

    print('[SELF-TEST] ' + ('OK' if ok else 'FAIL'))
    return 0 if ok else 1


def print_report(node):
    print('=== 强板块落后股（%s %s）===' % (node['date'], node['time']))
    s = node['scanned']
    print('  扫描：板块 %d 个 / 个股 %d 只 / 抓到 %d（失败 %d）→ 强板块 %d 个'
          % (s['sectors'], s['universe'], s['quoted'], s['failed'], s['strongSectors']))
    print('  强板块：' + ('、'.join('%s%+.1f%%' % (x['name'], x['gap']) for x in node['sectors'][:6]) or '—'))
    if node['picks']:
        print('  候选 %d 只（按落后幅度降序）：' % len(node['picks']))
        for p in node['picks']:
            print('    %s %-6s %s' % (p['code'], p['name'], p['note']))
    else:
        print('  候选 0 只 —— ' + str(node.get('gapNote')))
    if node.get('gapNote') and node['picks']:
        print('  [WARN] ' + node['gapNote'])
    bt = node.get('bt') or {}
    if bt.get('main'):
        m, al = bt['main'], bt.get('alt') or {}
        print('  回测（同一份代码逐日跑 %s~%s，共 %d 个交易日）：'
              % (bt['window'][0], bt['window'][1], bt['days']))
        print('    有信号 %d 天（%.0f%% 交易日空仓）／样本 %d 条／扣 %.2f%% 成本'
              % (bt['signalDays'], bt['flatRate'] * 100, bt['samples'], bt['cost']))
        print('    %s：%+.2f%%，胜率 %.0f%%，日聚类 t=%s，日胜率 %s'
              % (m['name'], m['ret'], (m['win'] or 0) * 100, m['tDaily'],
                 ('%.0f%%' % (m['dayWin'] * 100)) if m.get('dayWin') is not None else '—'))
        if al:
            print('    %s：%+.2f%%，胜率 %.0f%%，日聚类 t=%s，日胜率 %s'
                  % (al['name'], al['ret'], (al['win'] or 0) * 100, al['tDaily'],
                     ('%.0f%%' % (al['dayWin'] * 100)) if al.get('dayWin') is not None else '—'))
    else:
        print('  回测：未运行（%s）' % (node.get('btNote') or '—'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true', help='只打印，不写 data.js')
    ap.add_argument('--replay', metavar='YYYY-MM-DD',
                    help='用日线重建该日 9:45 快照（非交易日验收规则用，不登记台账）')
    ap.add_argument('--self-test', action='store_true')
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    D = load_data()
    if a.replay:
        q, named = replay_quotes(D, a.replay)
        if not q:
            print('[FAIL] %s 没有 K 线（非交易日或超出数据范围）' % a.replay, file=sys.stderr)
            return 1
        node, err = compute(D, quotes=q, today=a.replay)
        mode = 'replay'
        if node and q and named < len(q) * 0.8:
            node['gapNote'] = ('名字只取到 %d/%d 只（<80%%），「ST 不进候选」这一条可能没生效，'
                               '本次回放结论仅作规则验收' % (named, len(q)))
    else:
        node, err = compute(D)
        mode = 'live'
    if err:
        print('[FAIL] ' + err, file=sys.stderr)
        return 1

    node['mode'] = mode
    if mode == 'replay':
        node['replayNote'] = (
            '回放：用 %s 日线开盘价重建 9:45 快照，等价于「开盘价成交」，'
            '因此入场滑点过滤不生效、也没有量比。它只能回答「这套规则会挑出哪些票」，'
            '不能回答「当天真赚了多少」；真实胜率由台账逐日累计。' % a.replay)
        node['time'] = '回放'

    bt, bterr, hit = backtest_cached(D)
    if bt:
        node['bt'] = bt
        # 出场口径按「日聚类 t」择优——不是按均值、也不是按胜率：均值差 0.2pp 说明不了
        # 什么，日聚类 t 才反映「换一天还算不算数」。两个口径都进页面，不藏。
        m, al = bt['main'], bt['alt']
        better = al if (al.get('tDaily') or -99) > (m.get('tDaily') or -99) else m
        worse = m if better is al else al
        node['exit'] = {'main': better, 'alt': worse}
        node['span'] = {'days': bt['days'], 'samples': bt['samples'],
                        'signalDays': bt['signalDays']}
        print('[BT] %s' % ('命中缓存，未重算' if hit else '已按当前数据重算'))
    else:
        node['exit'] = None
        node['span'] = None
        node['btNote'] = bterr or '未知原因'
        print('[WARN] 回测未完成：%s' % node['btNote'], file=sys.stderr)

    print_report(node)
    if a.dry:
        print('\n[DRY] 未写盘')
        return 0
    if mode == 'live':
        n = register(node)
        print('[OK] 台账登记 %d 条（signal_ledger.entries.%s.leadLag）' % (n, node['date']))
    D.setdefault('duanban', {})['leadLag'] = node
    common.write_data_js(ROOT, D, '[leadLag]')
    return 0


if __name__ == '__main__':
    sys.exit(main())

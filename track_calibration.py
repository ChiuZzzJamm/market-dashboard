#!/usr/bin/env python3
"""置信度后验校准台账（R100z11）——给「高确信」标签一个可核对的命中率账本。

背景（2026-09-30 实测）：当日 6 板块验证 hit 2 / miss 3 / 部分兑现 1（胜率 33%），
而 miss 的两条「光通信·光模块」「半导体设备材料」恰好都标了「高确信」走强。
标签与命中率背离时，系统没有任何机制去惩罚这个标签——本脚本就是把账本做出来。

用法：
    python3 track_calibration.py                 # 并入本轮 data.js 预测结果 + 打印分层命中率与门槛
    python3 track_calibration.py --view          # 只查看台账与统计，不并入
    python3 track_calibration.py --json          # 机器可读输出（自动化读取门槛用）
    python3 track_calibration.py --backfill 25   # 从 git 历史 data.js 回填样本（幂等）

数据（★文件名不带点：GitHub Pages 不发布 dotfile，前端要靠 fetch('calibration_log.json') 画命中横条）：
    台账 calibration_log.json     { log: { "<预测日期>": [ {date,sector,sectorKey,predicted,actualPct,
                                              excessPct,excessPctIdx,conf,hit,hitAbs,hitStock,
                                              avgPct,excessAvg,benchPct,benchSecPct,benchUsed,pos} ] },
                                    open: { "<预测日期>": {tendencyHit,bandHit,volHit,score,pos} } }
    门槛 calibration_state.json   { generatedAt, window, stat:{层:{n,hit,rate,ci,sampleEnough}}, gates:{...} }
    净值 portfolio_curve.json     { series:[{date,pf,bench,dd}], stat:{totRet,benchRet,maxDd,...} }

命中口径（R100z15 缺口①——2026-10-01 定稿；R100z158 基准改双轨）：
    ★权威基准 benchSecPct = 同花顺一级行业（data.js 的 ashare.allBoards）当日涨跌幅**中位数**。
      为什么换掉指数基准（2026-10-20 实测）：被判定对象是**行业板块**，拿综合指数当尺子是量纲错配。
      实测差异：10-20 行业中位 +0.94% vs 四指数中位 −0.14%（差 1.08pp）；10-15 −2.94% vs −1.59%；
      10-08 −0.70% vs −2.07%。普涨日行业天然跑赢综合指数 → 每个板块都「轻松跑赢」，超额系统性虚高；
      普跌日反过来虚低。同源比较才谈得上 alpha。
    benchPct = 上证 / 深成 / 创业板 / 中证1000 四指数涨跌幅中位数，**降级为大盘参照**，
      仅在 benchSecPct 缺失时顶替，并在条目里以 benchUsed 标注实际用的是哪把尺子。
    超额 excessPct = actualPct − (benchSecPct or benchPct)；excessPctIdx 保留旧指数口径作对照。
    基准全部缺失时超额留空、**不得补 0**。
    「hit」= 超额口径：预测走强/偏强 → 超额 > 0 命中；预测承压 → 超额 < 0 命中；
             |超额| < 0.2% 记 0.5（部分兑现）；方向不匹配记 0。
    「hitAbs」= 旧的方向口径（实际涨幅 ±），原样留在台账里做**对照**。
    为什么要两套：9-30 全市场 2824 家下跌时，某板块 −1.5% 其实是跑赢中位数的负 alpha，
    旧口径会把它判成「预测失败」。跑本脚本时会打印 hit 与 hitAbs 的差值——
    **差值越大说明旧口径越在自欺**，这个数字本身就是产品价值。

组合层（R100z15 缺口④）：每个预测日入一条快照（该日 8 只标的等权 avgPct），
    累乘成组合净值 pf 与基准净值 bench，同时给出最大回撤——命中 60% 但亏钱、命中 50% 但赚钱，
    光看命中率分不出来。
"""
import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import date

import stop_tracker  # R100z59：止损台账接线（见 main() 内「R100z59 止损台账」段）

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
from common import write_json_atomic  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(ROOT, 'data.js')
# ★R100z13：一律不带点的文件名——GitHub Pages 不发布 dotfile，前端要 fetch('calibration_log.json')
LOG_FILE = os.path.join(ROOT, 'calibration_log.json')
STATE_FILE = os.path.join(ROOT, 'calibration_state.json')
BENCH_FILE = os.path.join(ROOT, 'market_bench.json')
CURVE_FILE = os.path.join(ROOT, 'portfolio_curve.json')

# 门槛阈值（命中率 < 阈值即触发降档；见 references/predict-calibration.md）
HARD_GATE = 0.25    # 低于此值：当日禁止使用「高确信」
SOFT_GATE = 0.40    # 低于此值：当日最多 1 条「高确信」
MIN_SAMPLE = 5      # 通用层：样本不足时不降档（避免小样本噪声误杀）
MIN_SAMPLE_HIGH = 8 # R100z13：「高确信」层专用硬门槛——高确信是最高承诺，
                    # 只有 ≥8 条样本才允许据此降档（3~5 条就下「禁用高确信」会天天跳、且容易过冲）
WINDOW = 20         # 统计窗口（交易日）

NODE_SRC = r"""
const fs = require('fs');
const p = process.env.DATA_PATH;
let s = fs.readFileSync(p, 'utf8');
let m = s.match(/window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!m) { console.error('data.js parse failed'); process.exit(1); }
let d; try { d = eval('(' + m[1] + ')'); } catch (e) { console.error('eval failed', e.message); process.exit(1); }
const ap = d.aiPrediction || {};
const oo = d.openOutlook || {};
process.stdout.write(JSON.stringify({
  date: ap.date || null,
  confBySector: (ap.sectors || []).map((x) => ({ sector: x.sector, conf: x.confidence || null })),
  verification: ap.verification || null,
  // R100z15 缺口①/④：标的层平均涨幅（8 只等权）→ 组合净值曲线；stockCheck.fund 保留
  stockCheck: (ap.verification && ap.verification.stockCheck) || null,
  // R100z13：建议仓位（8:30/16:00 数据步写下的可回测数字）+ 今日开盘前瞻结构化假设（供前瞻兑现打分）
  // （R100z51 键名修正）：建议仓位实际写在 **openOutlook.pos**（score_openoutlook.py:42 读的也是这个），
  // data.js 顶层从来没有 `posAdvice` 键 → 原写法恒为 null，台账每条 pos / 组合曲线的仓位归因一直是空的。
  pos: (oo && oo.pos) || null,
  openPos: oo.pos || null,
  openVerification: oo.verification || null
}));
"""


def load_prediction(path=DATA_JS):
    import subprocess
    node = ('/Users/loccco/.workbuddy/binaries/run-node'
            if os.path.exists('/Users/loccco/.workbuddy/binaries/run-node') else 'node')
    env = dict(os.environ, DATA_PATH=path)
    pr = subprocess.run([node, '-e', NODE_SRC], env=env, capture_output=True, text=True)
    if pr.returncode != 0:
        print('[ERR] 读取 data.js 失败：' + (pr.stderr or '').strip()[:200])
        return None
    try:
        return json.loads(pr.stdout)
    except Exception as e:  # noop
        print('[ERR] 解析失败：' + str(e))
        return None


# ---------------- R100z52：方向判定词表（替掉「只认『承压』两个字」的旧口径） ----------------
# 旧实现 `up = str(predicted).find('承压') == -1`：AI 写「偏弱 / 回撤 / 震荡 / 下行 /
# 降温」这些同样明确看空的词时 up=True，一律算成看多 → 命中率被系统性抬高。
# 实测口径：verification.details[].predicted 是极短文本（如「走强」/「承压」/「震荡偏弱」），
# 词表按「最长/最特殊优先」匹配，命中多词时判中性，宁可漏判也不硬凑方向。
# ---------------- R100z157：置信度归一（BUG-1 修复） ----------------
# 台账 conf 只允许 高/中/低 三态。2026-10-13 有 2 条被写成全称「中等确信」（R100z152h
# 「全站统一中等确信」改造漏了台账侧），落进 conf_rank 的 unknown(9) → 被当成最差置信档，
# 污染 build_curve「当日取最高置信档」的组合口径。这里做**写入端归一 + 读取端容错**双保险：
# 历史条目已坏，靠 --recalc 回填；未来写入在 build_items 就归一，前端/闸门永远看不到全称。
CONF_ENUM = ('高', '中', '低')


def norm_conf(v):
    """把任意写法的置信度归一为 高/中/低/unknown（唯一出口，勿在别处再写映射）。"""
    s = str(v if v is not None else '').strip().replace('\u3000', ' ')
    if not s:
        return 'unknown'
    if s in CONF_ENUM:
        return s
    low = s.lower()
    if '高' in s or low in ('high', 'h'):
        return '高'
    if '中' in s or low in ('medium', 'mid', 'm', 'normal'):
        return '中'
    if '低' in s or low in ('low', 'l'):
        return '低'
    return 'unknown'


DIR_FLAT = ('震荡', '横盘', '胶着', '犹豫', '整理', '分歧', '观望', '僵持', '拉锯', '中性')
# ★R100z158（2026-10-20 实测修）：原词表缺「看涨 / 看跌」这组最常用的方向词。
# 10-19 六条预测里 4 条写「看涨」→ classify_dir 全部返回 flat → hit=None 被排除出统计，
# 而那 4 条中有 3 条方向是错的（计算机设备 −0.30 / 油气 −0.10 / 创新药 −1.02）。
# 结果是「失败样本被系统性剔除、命中率被抬高」——比少判更坏的是**有偏**。
# 教训：方向词表必须覆盖 AI 实际用词，且新增词要能用 --selftest 回归。
DIR_UP = ('走强', '上行', '上涨', '偏强', '看多', '看涨', '偏多', '做多', '走高', '提振',
          '回暖', '反弹', '反攻', '突破', '扩张', '接力', '活跃', '走俏', '领涨', '企稳回升')
DIR_DOWN = ('承压', '偏弱', '下行', '下跌', '回落', '回调', '回撤', '走弱', '降温',
            '退潮', '减仓', '抛压', '避险', '收窄', '领跌',
            '看跌', '看空', '偏空', '做空', '杀跌', '补跌', '走低')


def classify_dir(predicted):
    """把 AI 的板块方向文本判定为 'up' / 'down' / 'flat'。

    flat（震荡/观望/多空各半）**不计入命中统计**（返回 None），
    因为「没方向」被算成「看多」正是上一版把命中率抬高的主因。
    """
    s = str(predicted or '')
    for k in DIR_FLAT:
        if k in s:
            return 'flat'
    hu = any(k in s for k in DIR_UP)
    hd = any(k in s for k in DIR_DOWN)
    if hu and hd:
        return 'flat'          # 又看多又看空 = 没方向
    if hu:
        return 'up'
    if hd:
        return 'down'
    return 'flat'


# ---------------- R100z158：板块名归一（回测归集的必要前提） ----------------
# 实测 48 条台账里出现 **41 个不同的板块名**——「光通信·光模块 / 算力硬件·光模块 /
# 通信设备·光模块 / 光模块 / 光模块·CPO」在业务上是同一件事，却被记成 5 个板块，
# 于是任何一个板块都攒不到 ≥3 条样本，分层命中率永远建立在噪声上。
# 归一口径（保守，只去装饰不合并语义）：
#   ① 去空格与间隔号；
#   ② 取「·」前的主词（AI 习惯「主词·细分」，主词才是板块归属）；
#   ③ 去掉「板块/行业/概念/指数/链/方向」这类纯后缀词。
# 不做同义词合并（「电力」≠「核电」），宁可少归集也不要造出假样本；
# 归集后的 key 只用于「按板块看历史战绩」，不作为对外展示名。
SEC_SUFFIX = ('板块', '行业', '概念', '指数', '方向', '产业链', '条线')


def sector_key(name):
    """把板块名压成可归集的 key；空值返回 ''。

    顺序很重要：必须**先按「·」取主词再清洗**。反过来先删间隔号会把
    「半导体设备·国产替代」粘成「半导体设备国产替代」，等于没归集。
    """
    s = str(name or '').strip()
    if not s:
        return ''
    s = re.split(r'[·・]', s)[0]
    s = re.sub(r'[\s\u3000\-—/]+', '', s)
    if not s:
        return ''
    for suf in SEC_SUFFIX:
        if len(s) > len(suf) + 1 and s.endswith(suf):
            s = s[: -len(suf)]
    return s


def self_test():
    """方向词表回归（--selftest 调用）。新增词必须先过这里，避免又漏一组常用词。"""
    cases = [
        ('走强', 'up'), ('偏强', 'up'), ('看涨', 'up'), ('偏多', 'up'), ('做多', 'up'),
        ('企稳回升', 'up'), ('领涨', 'up'),
        ('承压', 'down'), ('看跌', 'down'), ('看空', 'down'), ('偏空', 'down'),
        ('杀跌', 'down'), ('补跌', 'down'), ('走低', 'down'), ('领跌', 'down'),
        ('震荡', 'flat'), ('中性', 'flat'), ('震荡偏弱', 'flat'),
        ('看多看空各半', 'flat'), ('', 'flat'), (None, 'flat'),
    ]
    bad = [(t, w, classify_dir(t)) for t, w in cases if classify_dir(t) != w]
    if bad:
        print('[FAIL] 方向词表回归失败：')
        for t, w, g in bad:
            print('   %-14s 期望 %-4s 实得 %s' % (repr(t), w, g))
        return 1
    keys = [('半导体设备·国产替代', '半导体设备'), ('半导体·存储芯片', '半导体'),
            ('光模块·CPO', '光模块'), ('核电板块', '核电'), (' 电力 · 核电电网 ', '电力'),
            ('', ''), (None, '')]
    kbad = [(a, b, sector_key(a)) for a, b in keys if sector_key(a) != b]
    if kbad:
        print('[FAIL] 板块归一回失败：')
        for a, b, g in kbad:
            print('   %-20s 期望 %-12s 实得 %s' % (a, b, g))
        return 1
    print('[OK] 自检通过：方向词表 %d 例 / 板块归一 %d 例' % (len(cases), len(keys)))
    return 0


def score(predicted, actual_pct, excess_pct=None):
    """R100z15：命中判定改**超额口径**。

    excess_pct 为 None（基准缺失）时退回旧的绝对方向口径，并把结果同时标成 hitAbs，
    台账里两条都留着，宁可多存一个字段也不要伪造结论。
    返回 1 / 0.5 / 0；方向未明确（flat）时返回 None（不计入命中统计）。
    """
    up = classify_dir(predicted) == 'up'
    if classify_dir(predicted) == 'flat':
        return None
    if excess_pct is not None:
        if abs(excess_pct) < 0.2:
            return 0.5
        return (1 if up and excess_pct > 0 else (1 if (not up) and excess_pct < 0 else 0))
    if actual_pct is None:
        return None
    if abs(actual_pct) < 0.2:
        return 0.5
    return (1 if up and actual_pct > 0 else (1 if (not up) and actual_pct < 0 else 0))


def score_abs(predicted, actual_pct):
    """旧的「只判方向」口径，保留进台账作对照（R100z15 缺口①的证据）。

    R100z52：这里同样换用 classify_dir —— 对照的价值是「超额 vs 绝对」两种统计口径，
    不是「有 bug vs 无 bug」。方向判定本身的 bug 已在 score() 里修掉。
    """
    if actual_pct is None:
        return None
    if classify_dir(predicted) == 'flat':
        return None
    up = classify_dir(predicted) == 'up'
    if abs(actual_pct) < 0.2:
        return 0.5
    return (1 if up and actual_pct > 0 else (1 if (not up) and actual_pct < 0 else 0))


def score_stock(predicted, excess_avg):
    """R100z158：**选股 alpha** 口径——与 score() 的板块方向口径平行，别混用。

    为什么必须拆开（2026-10-20 实证）：10-19「创新药·CXO」预测看涨，板块指数 −0.83%
    （方向错，hit=0），但选出的 8 只等权 +2.40%、超额 +2.22%（选股是对的）。合成一个
    verdict 时只能判「未命中」，等于把「选股有效」全抹掉；反过来也有「板块涨了但选的票
    跑输」——那才是真该扣分的情形。两个指标一起看才知道该改「看板块」还是「选票」：
        hit      = 板块指数超额 → 检验「判方向」的能力
        hitStock = 8 只等权超额 → 检验「选标的」的能力
    口径与 score() 严格同构（|超额|<0.2 记 0.5），只是输入换成 excessAvg。
    """
    if excess_avg is None:
        return None
    d = classify_dir(predicted)
    if d == 'flat':
        return None
    up = d == 'up'
    if abs(excess_avg) < 0.2:
        return 0.5
    return (1 if up and excess_avg > 0 else (1 if (not up) and excess_avg < 0 else 0))


def load_bench(on_date=None):
    """读 market_bench.py 产出的市场基准。

    ★口径铁律（2026-10-01 踩到）：market_bench.json 里的实时值只有**当天**一个。
      把 10-01 的基准去算 09-30 的超额，等于拿昨天的尺子量前天的身高——历史超额会全是假的
      （第一版就犯了这个错，跑出来「旧口径 42% vs 超额 58%」这个数根本不可信）。
      所以：实时值的 date 必须与待入账的预测日一致，否则不得直接挪用。

    R100z158：新增 history 层（按日期键存的历史基准，由 market_bench.py --backfill-bench
      从 git 历史 data.js 的 allBoards 回填）。查序：当日实时值 → 按日期查 history → None。
      仍然**绝不**把某一天的基准套到另一天，也绝不补 0；查不到就让超额留空。
    """
    if not os.path.exists(BENCH_FILE):
        return None
    try:
        b = json.load(open(BENCH_FILE, encoding='utf-8'))
    except Exception:
        return None
    _has = (b.get('benchPct') is not None) or (b.get('benchSectorPct') is not None)
    if _has and (on_date is None or b.get('date') == on_date):
        if not on_date:
            return b
        # R100z158c：实时块的字段可能**局部缺失**（典型：当日行业基准算不出来 →
        # benchSectorPct=None / sectorN=None），而同一天的 history 条目恰好有该字段。
        # 旧逻辑「只要实时块有一个基准字段就整个返回」，会让半残的实时块把更全的 history
        # 条目整个遮掉 —— 实测 2026-10-20：实时块 benchSectorPct=None、history 有 0.94，
        # 结果当天入账被静默降级成指数口径（excessPct 记成 2.93 而非 1.98，hit 也随之算错）。
        # 现按**字段**回落补齐（同日同义，绝不跨日期套用），实时值永远优先。
        h = ((b.get('history') or {}).get(on_date)) or {}
        if isinstance(h, dict):
            merged = dict(b)
            for _k in ('benchPct', 'benchSectorPct'):
                if merged.get(_k) is None and h.get(_k) is not None:
                    merged[_k] = h[_k]
            if merged.get('sectorN') is None and h.get('sectorN') is not None:
                merged['sectorN'] = h['sectorN']
            return merged
        return b
    if on_date:
        h = (b.get('history') or {}).get(on_date)
        if isinstance(h, dict):
            hp, hs = h.get('benchPct'), h.get('benchSectorPct')
            if hp is not None or hs is not None:
                return {'date': on_date, 'benchPct': hp, 'benchSectorPct': hs,
                        'sectorN': h.get('sectorN'), 'names': h.get('names') or [],
                        'benchSrc': h.get('src') or 'history'}
    return None


def fill_bench_from_history(log):
    """R100z158c：把历史条目的基准按日期从 market_bench.json[history] 补齐（幂等）。

    ★背景（2026-10-20 实测 bug）：这段「按日期补齐」原先只写在 `--recalc` 分支里，而
      08:30 / 19:30 / 周日22:00 三条自动化调的都是**不带 --recalc** 的普通模式 —— 于是
      每跑一次普通模式就把历史条目的 benchSecPct 抹成 null、excessPct 悄悄退回指数口径：
      实测 sectorCmpDays 8→7、权威超额 −2.27%→−2.24%、10-20 那天的行业中位凭空丢失。
      等于 R100z158 刚建立的「同源行业基准」会被第一次自动化运行自己拆掉。

    非 --recalc 路径**不重算 hit**（那是 recalc 的职责），只补「尺子」本身 + 归一 conf；
    幂等可反复跑（已填过的不再动），且仍然**绝不**把某天的基准套到另一天、绝不补 0。
    返回 (补齐条数, conf 归一条数)。
    """
    hist = (load_bench() or {}).get('history') or {}
    filled = conf_fixed = 0
    for _dt, items in (log.get('log') or {}).items():
        h = hist.get(_dt) or {}
        for it in items:
            if it.get('benchSecPct') is None and h.get('benchSectorPct') is not None:
                it['benchSecPct'] = round(float(h['benchSectorPct']), 3)
                filled += 1
            if it.get('benchPct') is None and h.get('benchPct') is not None:
                it['benchPct'] = round(float(h['benchPct']), 3)
                filled += 1
            c0 = it.get('conf')
            c1 = norm_conf(c0)
            if c0 != c1:
                it['conf'] = c1
                conf_fixed += 1
    return filled, conf_fixed


def avg_of(sc_list, sector, bench):
    """取该板块 stockCheck 里 avgPct（8 只等权），算超额。取不到返回 (None, None)。"""
    if not sc_list:
        return None, None
    for sc in sc_list:
        if (sc.get('sector') or '').strip() == sector:
            a = sc.get('avgPct')
            a = float(a) if isinstance(a, (int, float)) else None
            e = round(a - bench, 3) if (a is not None and bench is not None) else None
            return a, e
    return None, None


def load_log():
    # R100z273：读坏不许静默变空账本——下游 write_json_atomic(LOG_FILE, log) 是整份覆盖写，
    # 读成空就等于把校准样本历史冲掉（校准曲线会突然「样本变少」而不报错）。
    lg = common.load_json_quarantine(LOG_FILE, None, tag='[track_calibration] 校准台账')
    return lg if isinstance(lg, dict) else {'log': {}, 'open': {}}


def save_log(log):
    """R100z13：供 score_openoutlook.py 直接 import 本模块后落盘（避免重复实现）。"""
    log.setdefault('log', {})
    log.setdefault('open', {})
    write_json_atomic(LOG_FILE, log, label='[track_calibration.save_log]')


def build_items(date, dets, conf_map, bench, sc_list, bench_main=None, bench_sec=None):
    """把 verification.details 落成台账条目（R100z15：同时落超额口径与旧方向口径）。
    bench_main：主板等权代理基准（上证+深成当日涨幅均值），与 benchPct（四指数中位数）并列存，
    供 build_curve 产第二条更贴近主板口径的基准线（R100z117 回测方法论补强）。
    bench_sec：R100z158 权威基准（全行业涨跌幅中位数）。给了就用它算超额，benchPct 仅留作对照。"""
    eff = bench_sec if bench_sec is not None else bench
    used = 'sector' if bench_sec is not None else ('index' if bench is not None else '')
    items = []
    for d in dets:
        nm = d.get('sector')
        act = d.get('actualPct')
        act = float(act) if isinstance(act, (int, float)) else None
        ex = round(act - eff, 3) if (act is not None and eff is not None) else None
        ex_idx = round(act - bench, 3) if (act is not None and bench is not None) else None
        avg, ex_avg = avg_of(sc_list, nm, eff)
        items.append({'date': date, 'sector': nm,
                      'sectorKey': sector_key(nm),
                      'predicted': d.get('predicted'),
                      'dirClass': classify_dir(d.get('predicted')),
                      'actualPct': act,
                      'benchPct': bench, 'benchSecPct': bench_sec, 'benchUsed': used,
                      'excessPct': ex, 'excessPctIdx': ex_idx,
                      'benchMainPct': bench_main,
                      'result': d.get('result'),
                      'conf': norm_conf(conf_map.get(nm)),
                      'avgPct': avg, 'excessAvg': ex_avg,
                      'hit': score(d.get('predicted'), act, ex),
                      'hitAbs': score_abs(d.get('predicted'), act),
                      'hitStock': score_stock(d.get('predicted'), ex_avg),
                      'pos': None})
    return items


def build_curve(log):
    """R100z15 缺口④：组合等权净值 vs 基准净值 + 回撤（命中率再高，净值不涨就是白忙）。
    R100z117：新增第二条基准线 benchMain（上证+深成等权，主板口径代理）+ 方法论假设与样本量标注。"""
    series, pf, bn, bn_main, peak = [], 1.0, 1.0, 1.0, 1.0
    bn_sec = 1.0
    pf_cmp, bn_cmp, cmp_days = 1.0, 1.0, 0   # R100z158：指数基准齐全日的可比净值
    pf_sec, bn_sec_cmp, sec_days = 1.0, 1.0, 0   # R100z158：行业基准齐全日（组合的正确尺子）
    for dt in sorted(log.get('log', {}).keys()):
        items = log['log'][dt] or []
        # R100z97 组合口径（用户拍板）：只取「置信度=高」的板块（每板块 8 只），板块间再等权
        # ——当日无「高」则降档取当日最高置信档（中→低），保证曲线连续；不再用全部 48 只
        # （旧实现①只取第一板块 avgPct，②临时修成 6 板块全等权，均非用户想要的口径）
        conf_rank = {'高': 0, '中': 1, '低': 2, 'unknown': 9}
        # R100z157：读取端也归一（历史台账里有「中等确信」这类全称，不归一就会落 unknown）
        best_rank = min((conf_rank.get(norm_conf(x.get('conf')), 9) for x in items), default=9)
        pool = [x for x in items if conf_rank.get(norm_conf(x.get('conf')), 9) == best_rank] or items
        avgs = [float(x['avgPct']) for x in pool
                if isinstance(x.get('avgPct'), (int, float))]
        if avgs:
            avg = sum(avgs) / len(avgs)
        else:
            acts = [float(x['actualPct']) for x in pool
                    if isinstance(x.get('actualPct'), (int, float))]
            avg = sum(acts) / len(acts) if acts else None
        if avg is None:
            continue
        bs = [float(x['benchPct']) for x in items
              if isinstance(x.get('benchPct'), (int, float))]
        b = sum(bs) / len(bs) if bs else None
        bm = [float(x['benchMainPct']) for x in items
              if isinstance(x.get('benchMainPct'), (int, float))]
        bm_v = sum(bm) / len(bm) if bm else None
        bsec = [float(x['benchSecPct']) for x in items
                if isinstance(x.get('benchSecPct'), (int, float))]
        bsec_v = sum(bsec) / len(bsec) if bsec else None
        pf *= (1 + avg / 100.0)
        if b is not None:
            bn *= (1 + b / 100.0)
        if bm_v is not None:
            bn_main *= (1 + bm_v / 100.0)
        if bsec_v is not None:
            bn_sec *= (1 + bsec_v / 100.0)
        # R100z158：可比超额——只在**基准齐全**的日子同时累乘组合与基准。
        # 起因：09-30 没有 benchPct（market_bench.json 当日缺失），旧实现让组合照跌 −1.96%
        # 而基准原地不动，等于把「随大盘下跌」记成 −1.96% 的纯超额，直接污染 excessPct。
        if b is not None:
            pf_cmp *= (1 + avg / 100.0)
            bn_cmp *= (1 + b / 100.0)
            cmp_days += 1
        # ★组合是一篮子行业板块，正确的尺子是**同源行业基准**而不是综合指数（量纲错配会让
        # 普涨日超额虚高）。这里单独累乘一对，只在行业基准齐全的日子计入。
        if bsec_v is not None:
            pf_sec *= (1 + avg / 100.0)
            bn_sec_cmp *= (1 + bsec_v / 100.0)
            sec_days += 1
        peak = max(peak, pf)
        dd = round((pf - peak) / peak * 100.0, 2)
        series.append({'date': dt, 'avgPct': round(avg, 3), 'pf': round(pf, 4),
                       'bench': round(bn, 4), 'benchMain': round(bn_main, 4),
                       'benchSec': round(bn_sec, 4) if bsec_v is not None else None,
                       'dd': dd,
                       'nSec': len(pool), 'nStocks': len(pool) * 8,
                       'benchMissing': b is None, 'benchSecMissing': bsec_v is None,
                       'poolConf': norm_conf(pool[0].get('conf')) if pool else ''})
    if not series:
        return None
    max_dd = min(series, key=lambda x: x['dd'])
    tot = (series[-1]['pf'] - 1) * 100
    bn_tot = (series[-1]['bench'] - 1) * 100
    bm_tot = (series[-1]['benchMain'] - 1) * 100 if series[-1].get('benchMain') else None
    # R100z158：分置信档净值——「高确信」到底有没有用，靠这条线自证，不靠争论。
    # 每个档位只在**当日存在该档板块**时累乘（没预测就不动，不拿别的档充数）。
    by_conf = {}
    for _k in CONF_ENUM:
        _pf, _days, _peak = 1.0, 0, 1.0
        for it_dt in sorted(log.get('log', {}).keys()):
            _items = [x for x in (log['log'][it_dt] or [])
                      if norm_conf(x.get('conf')) == _k
                      and isinstance(x.get('avgPct'), (int, float))]
            if not _items:
                continue
            _a = sum(float(x['avgPct']) for x in _items) / len(_items)
            _pf *= (1 + _a / 100.0)
            _days += 1
            _peak = max(_peak, _pf)
        if _days:
            by_conf[_k] = {'days': _days, 'totRetPct': round((_pf - 1) * 100, 2),
                           'maxDdPct': round((_pf - _peak) / _peak * 100, 2)}
    # R100z117：方法论假设（文档化，未回灌净值——回灌只会让曲线更差，且各源口径不一）
    assumptions = {
        'feePerSide': 0.025, 'stampDutySell': 0.05, 'slippagePerSide': 0.1,
        'turnover': 'T+1 次日兑现，单标的年化换手高；净值为毛收益，未扣费/税/滑点',
        'sampleWindow': len(series),
        'sampleEnough': len(series) >= 15,
        'benchmarkNote': '★权威基准=同花顺一级行业涨跌幅中位数（benchSec，与预测对象同源）；'
                         'bench=上证/深成/创业板/中证1000 四指数涨跌幅中位数（大盘参照）；'
                         'benchMain=上证+深成等权（主板口径代理，低 beta）。三者均为对照线，'
                         '不代表「买市场」的可交易组合。'}
    cmp_tot = (pf_cmp - 1) * 100
    cmp_bench = (bn_cmp - 1) * 100
    sec_tot = (pf_sec - 1) * 100
    sec_bench_tot = (bn_sec_cmp - 1) * 100
    stat = {'days': len(series), 'totRetPct': round(tot, 2), 'benchRetPct': round(bn_tot, 2),
            'excessPct': round(tot - bn_tot, 2),
            # 指数基准可比层（保留对照）
            'comparableDays': cmp_days,
            'excessComparablePct': round(cmp_tot - cmp_bench, 2) if cmp_days else None,
            'totRetComparablePct': round(cmp_tot, 2) if cmp_days else None,
            'benchRetComparablePct': round(cmp_bench, 2) if cmp_days else None,
            # ★R100z158 权威层：同源行业基准（组合就是一篮子行业板块，这才是同一个尺子）
            'sectorCmpDays': sec_days,
            'excessSectorPct': round(sec_tot - sec_bench_tot, 2) if sec_days else None,
            'totRetSectorCmpPct': round(sec_tot, 2) if sec_days else None,
            'benchSectorCmpRetPct': round(sec_bench_tot, 2) if sec_days else None,
            'benchSectorRetPct': round((bn_sec - 1) * 100, 2) if sec_days else None,
            'benchMainRetPct': round(bm_tot, 2) if bm_tot is not None else None,
            'benchMainExcessPct': round(tot - bm_tot, 2) if bm_tot is not None else None,
            'byConf': by_conf,
            'maxDdPct': max_dd['dd'], 'maxDdDate': max_dd['date'],
            'assumptions': assumptions,
            'note': ('组合=各预测日「置信度=高」板块（无高则取当日最高置信档）的预测标的等权平均涨跌幅累乘；'
                     '基准=同日市场基准累乘；回撤按组合净值峰谷算。'
                     '★权威超额=excessSectorPct（同源行业基准，只统计行业基准齐全的 %d 天）——'
                     '组合本身是一篮子行业板块，拿综合指数当尺子是量纲错配，普涨日会虚高。'
                     'excessComparablePct（指数基准 %d 天）与 excessPct 均留作对照。'
                     % (sec_days, cmp_days))}
    return {'ok': True, 'series': series, 'stat': stat}


def min_sample_for(key):
    """「高确信」层样本门槛更高：最高承诺不能拿 3 条样本下结论（R100z13）。"""
    return MIN_SAMPLE_HIGH if key == '高' else MIN_SAMPLE


def ci_of(p, n):
    """命中率 ±1σ 区间（二项分布正态近似）；n 太小则区间失去意义，直接返回 full=False。"""
    if not n:
        return None
    s = (p * (1 - p) / n) ** 0.5
    lo, hi = max(0.0, p - s), min(1.0, p + s)
    return {'lo': round(lo, 3), 'hi': round(hi, 3), 'sigma': round(s, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--view', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--backfill', type=int, default=0,
                    help='从 git 历史 data.js 回填最近 N 个版本的预测结果，快速攒够分层样本（默认不回填）')
    ap.add_argument('--recalc', action='store_true',
                    help='用当前口径重算台账全量 hit / hitAbs（R100z52 方向词表修版专用，幂等）')
    ap.add_argument('--selftest', action='store_true',
                    help='R100z158：方向词表与板块归一的回归自检（改词表后必跑）')
    a = ap.parse_args()

    if a.selftest:
        return self_test()

    log = load_log()
    _b = load_bench() or {}
    bench = _b.get('benchPct')
    # R100z117：主板等权代理基准（上证+深成当日涨幅均值），与 benchPct 并列
    _names = _b.get('names') or []
    _maint = [n['pct'] for n in _names
              if n.get('code') in ('sh000001', 'sz399001') and n.get('pct') is not None]
    bench_main = (sum(_maint) / len(_maint)) if _maint else None

    # R100z158c：历史基准补齐上移为**公共前置** —— 凡会写盘的普通模式都必须先补齐。
    # 08:30 / 19:30 / 周日22:00 三条自动化调的都是**不带 --recalc** 的普通模式，此前只有
    # --recalc 分支做补齐 → 普通模式每跑一次就把历史条目的 benchSecPct 抹成 null、
    # 权威超额悄悄退回指数口径（实测 sectorCmpDays 8→7、−2.27%→−2.24%）。
    # --view / --json 保持纯只读，不做任何写盘。
    _filled = _conffix = 0
    if not a.view and not a.json:
        _filled, _conffix = fill_bench_from_history(log)

    # --recalc：R100z52 方向词表修版后，历史上用「只认『承压』」坏口径算出的命中率
    # 全在台账里，不重算就永远带着偏高值往下滚。台账本身存了 predicted / actualPct /
    # excessPct，重算是纯函数回放、不依赖外部数据，幂等可反复跑。
    if a.recalc:
        fixed = 0
        conf_fixed = 0
        bench_filled = 0
        # R100z158c：基准补齐已上移为公共前置；这里再调一次是幂等保险（唯一会漏的场景是
        # `--recalc --json` 组合——那时上面的公共前置被跳过）。
        _f2, _c2 = fill_bench_from_history(log)
        bench_filled += _f2
        conf_fixed += _c2
        for _dt, items in log.get('log', {}).items():
            for it in items:
                act = it.get('actualPct')
                act = float(act) if isinstance(act, (int, float)) else None
                # R100z158：超额一律从「实际值 − 基准」重算，而不是读存下来的 excessPct
                # （旧流程里 excessAvg 与 excessPct 可能用两把不同的尺子算出来，读旧值会把错口径固化）。
                # 尺子优先级：benchSecPct（行业中位）→ benchPct（指数中位）→ 无（留空，绝不补 0）。
                _bs = it.get('benchSecPct')
                _bs = float(_bs) if isinstance(_bs, (int, float)) else None
                _bi = it.get('benchPct')
                _bi = float(_bi) if isinstance(_bi, (int, float)) else None
                eff = _bs if _bs is not None else _bi
                it['benchUsed'] = 'sector' if _bs is not None else ('index' if _bi is not None else '')
                it['excessPct'] = round(act - eff, 3) if (act is not None and eff is not None) else None
                it['excessPctIdx'] = round(act - _bi, 3) if (act is not None and _bi is not None) else None
                ex = it['excessPct']
                exa = it.get('avgPct')
                exa = float(exa) if isinstance(exa, (int, float)) else None
                if exa is not None:
                    it['excessAvg'] = round(exa - eff, 3) if eff is not None else None
                else:   # 无 8 只等权均值时保留旧值，别把能算的 hitStock 一起抹掉
                    _e0 = it.get('excessAvg')
                    it['excessAvg'] = float(_e0) if isinstance(_e0, (int, float)) else None
                exa = it['excessAvg']
                before = (it.get('hit'), it.get('hitAbs'), it.get('hitStock'))
                it['hit'] = score(it.get('predicted'), act, ex)
                it['hitAbs'] = score_abs(it.get('predicted'), act)
                it['hitStock'] = score_stock(it.get('predicted'), exa)
                it['dirClass'] = classify_dir(it.get('predicted'))
                # R100z158：历史条目补 sectorKey（幂等，只补不改名）
                if not it.get('sectorKey'):
                    it['sectorKey'] = sector_key(it.get('sector'))
                if (it.get('hit'), it.get('hitAbs'), it.get('hitStock')) != before:
                    fixed += 1
        save_log(log)
        print(f"[OK] --recalc 完成：{fixed} 条命中分被修正，{bench_filled} 条基准被补齐（行业中位），"
              f"{conf_fixed} 条 conf 归一为 高/中/低；"
              f"台账共 {sum(len(v) for v in log['log'].values())} 条已按新词表+同源行业基准重算")
        return 0

    # --backfill：从 git 历史提交里的 data.js 提取 aiPrediction.verification，快速攒样本。
    # 历史快照中的 actualPct/result 是当时写下的真实收盘结果，conf 随 sectors 一并保留，口径可信；
    # 同一日期若已入账则跳过（幂等）。
    if a.backfill and not a.view:
        import subprocess
        try:
            hashes = subprocess.run(['git', '-C', ROOT, 'log', '--format=%H', '-n', str(a.backfill)],
                                    capture_output=True, text=True).stdout.split()
        except Exception as e:  # noop
            hashes = []
            print('[WARN] git 历史读取失败：' + str(e))
        added = 0
        for h in hashes:
            try:
                blob = subprocess.run(['git', '-C', ROOT, 'show', f'{h}:data.js'],
                                      capture_output=True, text=True)
                if blob.returncode != 0 or not blob.stdout:
                    continue
                # R100z285：原来 tmp 落在**项目根**（`.calibration_tmp.js`），每个 git hash
                # 复制一份整份 data.js，用完 os.remove —— 沙箱把 remove 接管成「移到废纸篓」，
                # backfill N 次就往废纸篓塞 N 个几百 KB 的副本。
                # 改固定路径（系统临时目录）+ 覆盖写 + 永不删除：循环内同名复用，
                # 每一轮 git show 的 blob 覆盖上一份，盘上恒定 1 个文件。
                tmp = os.path.join(tempfile.gettempdir(), 'mdashboard_calibration_tmp.js')
                open(tmp, 'w', encoding='utf-8').write(blob.stdout)
                pd = load_prediction(tmp)
                if not pd or not pd.get('date') or not (pd.get('verification') or {}).get('details'):
                    continue
                if pd['date'] in log['log']:
                    continue
                conf_map = {c['sector']: c['conf'] for c in (pd.get('confBySector') or [])}
                # ★R100z158 修 bug：此前这里直接把「今天」的 bench 套给每个历史日期，
                # 等于拿今天的尺子量一个月前的身高——回填出来的超额全是假的。
                # 现在按日期取基准（实时值只在同日命中，否则走 market_bench.json 的 history 层）。
                _bb = load_bench(pd['date']) or {}
                _bi = _bb.get('benchPct')
                _bsec = _bb.get('benchSectorPct')
                _mt = [n['pct'] for n in (_bb.get('names') or [])
                       if n.get('code') in ('sh000001', 'sz399001') and n.get('pct') is not None]
                _bm = (sum(_mt) / len(_mt)) if _mt else None
                log['log'][pd['date']] = build_items(
                    pd['date'], pd['verification']['details'], conf_map, _bi,
                    pd.get('stockCheck') or [], _bm, _bsec)
                # R100z13：开盘前瞻兑现打分一并入账（19:30 叙事步已打分的才并入，没打分的跳过）
                ov = pd.get('openVerification')
                if ov:
                    log.setdefault('open', {})[pd['date']] = ov
                added += 1
            except Exception:
                continue
        if added:
            write_json_atomic(LOG_FILE, log, label='[track_calibration.main]')
            if not a.json:
                print(f"[OK] 回填 {added} 个历史交易日入账")
    if not a.view:
        pred = load_prediction()
        if not pred:
            sys.exit(1)
        conf_map = {c['sector']: c['conf'] for c in (pred.get('confBySector') or [])}
        dets = (pred.get('verification') or {}).get('details') or []
        if pred.get('date') and dets:
            _bb = load_bench(pred['date']) or {}
            bench = _bb.get('benchPct')
            bench_sec = _bb.get('benchSectorPct')
            _nm = _bb.get('names') or []
            _mt = [n['pct'] for n in _nm
                   if n.get('code') in ('sh000001', 'sz399001') and n.get('pct') is not None]
            bench_main = (sum(_mt) / len(_mt)) if _mt else None
            items = build_items(pred['date'], dets, conf_map, bench,
                                pred.get('stockCheck') or [], bench_main, bench_sec)
            for it in items:
                it['pos'] = pred.get('pos')
            log['log'][pred['date']] = items
            save_log(log)
            if not a.json:
                _bs = ('行业中位 %+.2f%%（%s 个行业）' % (bench_sec, _bb.get('sectorN') or '?')
                       if bench_sec is not None else
                       ('四指数中位 %+.2f%%（行业基准缺失，降级）' % bench if bench is not None
                        else '基准缺失，本轮只落旧方向口径'))
                print(f"[OK] 已并入台账：{pred['date']}（{len(items)} 条，基准={_bs}）")

        # R100z158c：即便本轮没有新的预测日入账（pred 缺 date/dets），公共前置补齐的
        # 历史基准也必须落盘——否则补了等于没补，下一次跑又被抹掉。
        if _filled or _conffix:
            save_log(log)
            print(f"[OK] 历史基准补齐落盘：{_filled} 条基准（行业中位）+ {_conffix} 条 conf 归一")

    # ---------------- R100z59 止损台账：接上「只算不跟踪」的补丁 ----------------
    # 7 项硬约束里的止损纪律，此前只有 stop_tracker.py 在算、却从没被调用过（死脚本），
    # stop_ledger.json 永远是空的——等于止损位算出来没人回扫，纸面纪律。
    # 这里只在**真实跑**（无 --view、且非 --json 读门槛模式）时接一次：
    #   登记当日各模块止损位（同码不覆盖，幂等）+ 回扫所有已登记条的触及情况。
    # --json 是 08:30 / 周末任务读门槛用的**只读**模式，不接：避免多一轮 K 线网络抓取、
    # 也避免任何写盘把「只读校验」变成副作用。stop_tracker.main() 内部会 argparse 读
    # sys.argv，调用前必须把 argv 换成它自己的（否则会拿走本脚本的参数直接 SystemExit）。
    if (not a.view) and (not a.json) and pred and pred.get('date'):
        _argv = sys.argv
        try:
            _buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(_buf), contextlib.redirect_stderr(_buf):
                    stop_tracker.main()
            except Exception as _e:
                # R100z72：止损台账是校准主流程的旁路副作用，即便它异常也绝不应中断校准。
                print(f"[WARN] 止损台账登记异常（不影响校准主流程）：{_e}", file=sys.stderr)
            if not a.json:
                for _ln in _buf.getvalue().splitlines():
                    if _ln.startswith(('[OK]', '[info]', '[SKIP]')):
                        print('  ' + _ln)
        finally:
            sys.argv = _argv

    # 统计：按 conf 层聚合，取最近 WINDOW 个预测日
    dates = sorted(log['log'].keys())[-WINDOW:]
    buckets = {'高': [], '中': [], '低': [], 'unknown': []}
    for dt in dates:
        for it in log['log'][dt]:
            buckets.setdefault(norm_conf(it.get('conf')), []).append(it)

    stat = {}
    for k, v in buckets.items():
        if not v:
            continue
        got = sum(x['hit'] for x in v if x['hit'] is not None)
        n = len([x for x in v if x['hit'] is not None])
        rate = round(got / n, 3) if n else None
        need = min_sample_for(k)
        stat[k] = {'n': n, 'hit': round(got, 1), 'rate': rate,
                   'minSample': need, 'sampleEnough': n >= need,
                   'ci': ci_of(rate, n) if rate is not None else None}

    def gate_for(key):
        s = stat.get(key)
        need = min_sample_for(key)
        if not s or not s.get('rate'):
            return {'maxHighConf': 2, 'maxConf': '高确信',
                    'reason': f'「{key}」层样本不足（{s["n"] if s else 0}/{need}），暂不降档'}
        ci = s.get('ci') or {}
        band = f'±1σ [{ci.get("lo", 0):.0%}, {ci.get("hi", 0):.0%}]'
        if not s['sampleEnough']:
            # R100z13：样本没到该层门槛，即使命中率看着低也不降档——小样本噪声会天天跳门槛
            return {'maxHighConf': 2, 'maxConf': '高确信',
                    'reason': f'「{key}」层命中率 {s["rate"]:.0%}（{band}）但样本 {s["n"]}<{need}，'
                              '未达降档门槛，暂不降档（继续攒样本后再评）'}
        if s['rate'] < HARD_GATE:
            return {'maxHighConf': 0, 'maxConf': '中等确信',
                    'reason': f'「{key}」层近 {len(dates)} 日命中率 {s["rate"]:.0%} {band} < {HARD_GATE:.0%}（样本≥{need}），当日禁用该层'}
        if s['rate'] < SOFT_GATE:
            return {'maxHighConf': 1, 'maxConf': '中等确信',
                    'reason': f'「{key}」层近 {len(dates)} 日命中率 {s["rate"]:.0%} {band} < {SOFT_GATE:.0%}（样本≥{need}），当日最多 1 条该层'}
        return {'maxHighConf': 2, 'maxConf': '高确信',
                'reason': f'「{key}」层近 {len(dates)} 日命中率 {s["rate"]:.0%} {band}（≥{SOFT_GATE:.0%}，样本≥{need}），可正常使用'}

    gates = {k: gate_for(k) for k in ('高', '中', '低')}
    state = {'generatedAt': date.today().isoformat(), 'window': dates, 'stat': stat, 'gates': gates}
    write_json_atomic(STATE_FILE, state, label='[track_calibration.calib]')

    if a.json:
        print(json.dumps(state, ensure_ascii=False))
        return 0
    print('=== 置信度后验校准台账（近 %d 个预测日：%s）===' % (len(dates), '、'.join(dates[-6:])))
    for k in ('高', '中', '低'):
        s = stat.get(k)
        if not s:
            print(f'  {k}确信：无样本')
            continue
        ci = s.get('ci') or {}
        print(f"  {k}确信：{s['n']} 条 / 命中分 {s['hit']} / 命中率 {s['rate']:.0%}"
              f"（±1σ {ci.get('lo', 0):.0%}~{ci.get('hi', 0):.0%}）"
              f"{'' if s['sampleEnough'] else '（样本 %d<%d，暂不降档）' % (s['n'], s['minSample'])}")
        print('      门槛：' + gates[k]['reason'])
    op = log.get('open') or {}
    if op:
        print(f"  开盘前瞻兑现：{len(op)} 个交易日已打分（{'、'.join(sorted(op)[-5:])}）")

    # 缺口①的证据：旧方向口径 vs 新超额口径，差多大就说明「只判方向」自欺了多少
    allx = [x for dt in dates for x in log['log'][dt]]
    hs = [x['hit'] for x in allx if x.get('hit') is not None]
    ha = [x['hitAbs'] for x in allx if x.get('hitAbs') is not None]
    if hs and ha:
        rw, ra = sum(hs) / len(hs), sum(ha) / len(ha)
        print('  命中口径对照：超额口径 %.0f%%（%d 条） vs 旧方向口径 %.0f%%（%d 条），差 %+.0f 个百分点'
              % (rw * 100, len(hs), ra * 100, len(ha), (rw - ra) * 100))
        if ra - rw >= 0.15:
            print('      ⚠️ 旧口径明显虚高：全市场普跌时「某板块跌幅 lesser 仍算命中」，'
                  '这就是为什么必须扣掉基准再判（R100z15）')
    # R100z158：方向 vs 选股拆分解读——两者差距说明该改「看板块」还是「选票」
    ss = [x['hitStock'] for x in allx if x.get('hitStock') is not None]
    if ss and hs:
        rs = sum(ss) / len(ss)
        print('  方向 vs 选股：判方向 %.0f%%（%d 条） / 选标的 %.0f%%（%d 条）'
              % (rw * 100, len(hs), rs * 100, len(ss)))
        if rs - rw >= 0.10:
            print('      → 选股明显强于判方向：alpha 主要来自「选票」而非「看板块」，'
                  '优化重心应放在板块方向（择时）而非标的筛选')
        elif rw - rs >= 0.10:
            print('      → 判方向强于选股：板块看对了但票没选好，优化重心应放在标的筛选与权重')

    # 缺口④：组合净值曲线（高置信板块标的等权 vs 基准，R100z97 口径见 build_curve）
    curve = build_curve(log)
    if curve:
        write_json_atomic(CURVE_FILE, curve, label='[track_calibration.curve]')
        s = curve['stat']
        print('  组合净值：%d 个交易日，累计 %+.2f%%（指数基准 %+.2f%%）'
              % (s['days'], s['totRetPct'], s['benchRetPct']))
        if s.get('excessSectorPct') is not None:
            print('      ★权威超额（同源行业基准）%+.2f%%：组合 %+.2f%% vs 行业中位 %+.2f%%，'
                  '只算行业基准齐全的 %d 天'
                  % (s['excessSectorPct'], s['totRetSectorCmpPct'],
                     s['benchSectorCmpRetPct'], s['sectorCmpDays']))
        if s.get('excessComparablePct') is not None:
            print('      指数基准超额 %+.2f%%（组合 %+.2f%% vs 指数中位 %+.2f%%，%d 天）——'
                  '供对照，组合是一篮子行业，这条线天然虚高'
                  % (s['excessComparablePct'], s['totRetComparablePct'],
                     s['benchRetComparablePct'], s['comparableDays']))
        print('      主板代理基准 %s（超额 %s）｜全样本超额 %+.2f%%（含缺基准日，仅对照）'
              % (('%+.2f%%' % s['benchMainRetPct'] if s.get('benchMainRetPct') is not None else '—'),
                 ('%+.2f%%' % s['benchMainExcessPct'] if s.get('benchMainExcessPct') is not None else '—'),
                 s['excessPct']))
        print('      最大回撤 %.2f%%（%s）' % (s['maxDdPct'], s['maxDdDate']))
        byc = s.get('byConf') or {}
        if byc:
            print('      分置信档净值（自证置信度有没有用）：' +
                  '｜'.join('%s %d 天 %+.2f%%（回撤 %.2f%%）'
                            % (k, v['days'], v['totRetPct'], v['maxDdPct'])
                            for k, v in byc.items()))
        if not s['assumptions']['sampleEnough']:
            print('      ⚠️ 样本仅 %d 个交易日（<15），结论统计显著性不足，仅供观察、勿据此下重注'
                  % s['days'])
        print('      假设：单边佣金 %.3f%% / 卖出印花税 %.3f%% / 滑点 %.3f%%（净值未扣，毛收益）；'
              '权威基准=同花顺一级行业中位、指数基准=四指数中位、主板代理=上证+深成等权' %
              (s['assumptions']['feePerSide'], s['assumptions']['stampDutySell'],
               s['assumptions']['slippagePerSide']))
        print('      已写入 ' + os.path.basename(CURVE_FILE) + '（前端画净值与回撤）')

    print('[OK] 门槛已写入 ' + os.path.basename(STATE_FILE))
    return 0


if __name__ == '__main__':
    sys.exit(main())

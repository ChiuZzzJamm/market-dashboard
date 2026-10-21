#!/usr/bin/env python3
"""机械选股池（R100z15 缺口②）——把「断板池 / 谐波池 / 吸筹池 / 强势池」接回决策链。

为什么需要它（2026-10-01 体检实测的最根本问题）：
    项目自己造了四个选股引擎（check_duanban / harmonic_detect / accumulation_score / power_screener），
    旧 16:00 单步任务那个交易日里却只有 6/48 只（12.5%）来自断板池，剩下 42 只是 AI 看 note 手写的
    「板块龙头 / CPO概念 / 换手活跃」。而刚上的个股硬风险黑名单拦掉的 4 只（恒邦股份、豫光金铅、
    华钰矿业、立昂微），恰好全都是这类主观 pick——**黑名单拦得住坏票，却拦不住「好票是 AI 瞎挑的」**。

本脚本定下的规矩：
    每个预测板块的 8 只标的，必须由机械池产出；AI 只保留**解释权**，交出**选择权**。
    配额（写死，AI 不得改）：
        断板确认池最多 4 只（按 probability 降序）
        谐波确认池最多 2 只（确认池优先于观察池；谐波自带形态失效位，直接当止损）
        吸筹/强势池最多 2 只（accumulation 分数 / powerScreen 分数 降序）
        以上凑不满 8 只才允许「全局补位」，且补位≤2 只并强制标 [补位]
    板块匹配：精确同名 → 显式别名表 → 字符 Jaccard≥0.34 → 子串包含，逐级放宽。

产物（★文件名不带点：GitHub Pages 不发布 dotfile）：
    stock_pool.json { ok, date, candidateDate, sectorsSource, sectors,
                      bySector:{板块:[8 只]}, coverage, meta, fail }
    date          = 建键所对的预测日（沿用 aiPrediction.date）
    candidateDate = 候选数据源的交易日（data.js ashare.tradeDate）——
                    R100z163 新增，validate_stocks 的时序闸靠它判断「池是不是拿
                    比预测更新的收盘数据重算的」，用 date 判断会把 08:30 的合法重键
                    误判成「预测无从照抄」而整轮降级。
    sectorsSource = 'aiPrediction'（默认）| 'cli'（--sectors 显式重键）

校验器：validate_stocks.py 的 check_stock_pool —— **只约束「④ 断板反包」这一类槽位**：
    池内有断板达标候选时，断板槽位必须取自池内（一只都不用 → FAIL；用得少 → WARN 问取舍）。
    **①②③ 板块龙头 / 相关概念股 / 小盘人气股 本来就由 AI 按四类配比表自选，不属于违规**
    （R100z160c，2026-10-21 用户指出：池子给不满 8 只时就用这三类补满，这是设计）。
    历史口径「8 只整组照抄 / 池外 ≥2 只即 FAIL」已作废——它与「每板块恰好 8 只」互斥。

用法：
    python3 build_stock_pool.py                 # 干跑，打印每个板块的机械池与覆盖率
    python3 build_stock_pool.py --write         # 写 stock_pool.json
    python3 build_stock_pool.py --json          # 机器可读
    python3 build_stock_pool.py --write --sectors "医疗保健,消费电子,…"
                                                # R100z163：按指定板块名重键（只重键、不换股）

为什么需要 --sectors（R100z163，2026-10-21 用户拍板）：
    池是在前一交易日 17:00 按**当时** aiPrediction.sectors 的板块名建的键。次日 08:30
    盘前重新定板块时改几个名字，整批键就对不上——当天实测 6 个板块里 4 个「无同名板块」
    （医疗保健 / 消费电子 / 计算机设备 / 航空运输），check_stock_pool 对它们整轮失明，
    「④ 断板反包必须取自机械池」这条约束静默降级成「汇报点名即可」。
    正确的修法不是放宽匹配（会让『消费电子』被『消费·食品加工』误牵），而是让池按**当日**
    板块名重键：候选仍由四个引擎机械产出，AI 只是报了个板块名，选择权仍在机械侧。
"""
import argparse
import json
import os
import re
import sys
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'stock_pool.json')

NODE_SRC = r"""
const fs = require('fs');
const p = process.env.DATA_PATH;
let s = fs.readFileSync(p, 'utf8');
let m = s.match(/window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!m) { console.error('data.js parse failed'); process.exit(1); }
let d; try { d = eval('(' + m[1] + ')'); } catch (e) { console.error('eval failed', e.message); process.exit(1); }
const cut = (x) => String(x == null ? '' : x).replace(/\D/g, '').slice(-6);
const ap = d.aiPrediction || {};
process.stdout.write(JSON.stringify({
  date: ap.date || (d.ashare && d.ashare.tradeDate) || null,
  candidateDate: (d.ashare && d.ashare.tradeDate) || null,
  sectors: (ap.sectors || []).map((x) => ({ sector: x.sector, direction: x.direction })),
  duanban: {
    confirmed: (d.duanban && d.duanban.confirmed) || [],
    watching: (d.duanban && d.duanban.watching) || []
  },
  harmonic: d.harmonic || {},
  accumulation: d.accumulation || {},
  powerScreen: d.powerScreen || {}
}));
"""

# 显式板块别名表：预测板块名（AI 爱用的口语名）→ 机械池里的板块 token
# ★踩坑（2026-10-01）：只写「通信」这种短 token 会误伤（"通信" 匹配到 "通信服务" 之外的空 sector）。
#   别名必须写**完整板块词**，并且要求候选 sector 命中别名之一，避免字符级 Jaccard 乱牵。
SECTOR_ALIAS = {
    '光通信·光模块': ['通信服务', '通信设备', '光学光电子', '通信'],
    '光模块': ['通信服务', '通信设备', '光学光电子'],
    'CPO': ['通信服务', '光学光电子', '电子器件'],
    '半导体设备材料': ['半导体', '电子器件', '元件', '自动化设备'],
    '黄金·贵金属': ['贵金属', '有色金属', '有色·工业金属', '贵金属·工业金属', '小金属', '黄金'],
    '贵金属': ['贵金属', '小金属', '有色金属'],
    '有色·工业金属': ['有色·工业金属', '有色金属', '工业金属', '小金属'],
    '核电·数据中心供电': ['核电·数据中心供电', '电力行业', '电力'],
    '光伏逆变器': ['光伏逆变器', '光伏设备'],
    '储能': ['电池', '电力设备'],
    '创新药': ['生物制药', '医疗服务', '化学制药'],
    '军工·无人机': ['军工电子', '航天装备', '航空装备'],
    '固态电池': ['电池', '化学制品'],
}

# 匹配阈值（写死：宁缺毋滥，绝不让字符级相似度把能源股牵进光模块）
STRONG = 0.50    # Jaccard ≥0.5：强同类，可直接进候选
WEAK = 0.34      # Jaccard ≥0.34 或子串包含：弱同类，仅允许进「补位候选」
FILL_CAP = 2      # 单个板块补位上限（全局补位超 2 只说明机械池没货，宁可少给也不能凑数）

NOISE_WORDS = ('行业', '设备', '制造', '材料', '装置', '器件', '服务', '产业', 'Ⅱ')

# code → 最新收盘价索引（R100z163）。由 main() 在跑 pick_for 之前从四个池里扫一遍填好。
# 为什么需要：accumulation.scored 的字段只有 {code,name,sector,score,grade,features,…}，
# **完全不带价格**（无 lastClose、无 kline），单靠它 mk() 算出来的 stop/target 全是 None
# → 机械池交出去的就是「止损 — / 目标 —」的空纪律位，AI 照抄必被 exit 三件套判 FAIL。
# 同一只票通常也在 powerScreen / harmonic / 断板里出现并带价，故用别的池兜底，零网络。
PX = {}


def norm(s):
    return str(s or '').strip()


def tok(s):
    """板块名 → 字符集合（去通用后缀噪声），用于 Jaccard 相似度"""
    t = norm(s)
    for w in NOISE_WORDS:
        t = t.replace(w, '')
    return set(t)


def sim(a, b):
    ta, tb = tok(a), tok(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def contains(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    return (na in nb) or (nb in na)


def load():
    import subprocess
    node = ('/Users/loccco/.workbuddy/binaries/run-node'
            if os.path.exists('/Users/loccco/.workbuddy/binaries/run-node') else 'node')
    pr = subprocess.run([node, '-e', NODE_SRC], env=dict(os.environ, DATA_PATH=os.path.join(ROOT, 'data.js')),
                        capture_output=True, text=True)
    if pr.returncode != 0:
        print('[ERR] 读 data.js 失败：' + (pr.stderr or '').strip()[:200])
        return None
    return json.loads(pr.stdout)


def r2(x):
    try:
        return round(float(x), 2)
    except Exception:
        return None


def build_exit(item, stoploss, target):
    """统一的退出纪律字段（R100z15 缺口③）：stop / target / falsify 三件缺一不可。"""
    f = falsify_of(item)
    return {'stop': stoploss, 'target': target, 'falsify': f,
            'stopBasis': stoploss_basis(item)}


def stoploss_basis(item):
    src = item.get('src')
    if src == '谐波':
        return '形态失效位（谐波 D 点投影下方）'
    if src == '断板反包':
        # R100z61b：止损锚 = T 日（涨停日）最低价（历史事实、不漂移）。
        # 注意与形态淘汰线分离——形态破位由 check_duanban 判、照常移入失效池，
        # 这里只管「价格止损位」，两者是两条线，别混成一个。
        return 'T 日（涨停日）最低价（固定锚；形态破位移失效池，与止损位分离）'
    return '入场日收盘 −3%（机械候选，锚入场价不锚现价）'


def falsify_of(item):
    src = item.get('src')
    if src == '断板反包':
        return '放量跌破断板日最低价，或反包日冲高回落收阴——形态失败，无条件离场'
    if src == '谐波':
        return '跌破形态失效位（止损），或价格未进 PRZ 即先行破位——结构无效'
    if src == '吸筹':
        return '第 4-6 日仍不收复 MA5 且量能未放大——吸筹失败，视为假突破'
    if src == '强势':
        return '次日收阴且跌破前一日最低价——情绪退潮，不追高'
    return '跌破买入当日最低点——机械候选无形态位，一律按止损纪律执行'


def pick_for(sector, pools, quota_duan=4, quota_harm=2, quota_acc=2, quota_fill=8):
    """给一个预测板块挑 8 只。返回 (list, coverage_note)"""
    duan = pools.get('duanban') or []
    harm = pools.get('harmonic') or []
    acc = pools.get('accumulation') or []
    pow_ = pools.get('power') or []
    aliases = SECTOR_ALIAS.get(sector, [])

    def match(pool):
        """严格匹配：精确同名 > 别名命中 > 强同类 > 弱同类(仅补位用)。
        sector 为空的候选一律丢弃（无量可售，靠字符相似度硬扯会把能源股塞进光模块）。"""
        out, orphan = [], []
        for it in pool:
            nm = norm(it.get('sector'))
            if not nm:
                orphan.append('%s%s' % (norm(it.get('name')), code6(it)))
                continue
            exact = (norm(sector) == nm)
            alias = any((a == nm) or (nm in a) or (a in nm) for a in aliases)
            s = sim(sector, nm)
            c = contains(sector, nm)
            if exact:
                out.append((3.0, it))
            elif alias:
                out.append((2.5, it))
            elif s >= STRONG:
                out.append((2.0, it))
            elif s >= WEAK or c:
                out.append((1.0, it))     # 弱同类：只进补位池，不进主候选
        out.sort(key=lambda x: (-x[0], -(x[1].get('probability') or x[1].get('score') or 0)))
        seen, strong, weak, res = set(), [], [], []
        for sc, it in out:
            code = code6(it)
            if code in seen:
                continue
            seen.add(code)
            res.append(it)
            (strong if sc >= 2.0 else weak).append(it)
        return strong, weak, orphan

    (m_d, w_d, o_d) = match(duan)
    (m_h, w_h, o_h) = match(harm)
    (m_a, w_a, o_a) = match(acc)
    (m_p, w_p, o_p) = match(pow_)
    weak_all = w_d + w_h + w_a + w_p
    orphans = o_d + o_h + o_a + o_p
    got, notes = [], []

    def dup(it):
        """跨池去重（R100z163）。原实现只在每个 match() 内部去重，跨池没有：
        2026-10-21 实测同一只票会以「谐波 + 强势」「吸筹 + 强势」两种身份
        **同时进同一个板块的 8 只**（金域医学 603882、润建股份 002929），
        8 只里有重复代码 → 下游去重后不足 8 只 / 配比点数对不上。
        优先级即调用顺序：断板 > 谐波 > 吸筹 > 强势 > 补位。"""
        return any(g['code'] == code6(it) for g in got)

    unpriced = []

    def take(it, src, **kw):
        """统一入选闸：跨池去重 + **必须能定价**。
        定价不到就整条丢弃 —— 交一只「止损 — / 目标 —」的票出去，下游 exit 三件套
        必然判 FAIL，还不如不给（宁可少给也不给空纪律，同 FILL_CAP 的取舍原则）。"""
        px = last_close_of(it) or PX.get(code6(it))
        if px is None:
            unpriced.append('%s%s' % (norm(it.get('name')), code6(it)))
            return False
        got.append(mk(it, src, px, **kw))
        return True

    # 1) 断板确认池（最多 4）
    # R100z61b：止损锚 = T 日（涨停日）最低价（绝对价、固定），不再用 last ×0.975。
    # 形态淘汰（破 T 日低点×0.98 出池）仍由 check_duanban 独立判定，两条线分离。
    for it in m_d[:quota_duan]:
        if len(got) >= quota_fill:
            break
        if dup(it):
            continue
        take(it, '断板反包', stop_pct=-2.5, tgt=6.0, stop_price=zt_low(it))
    # 2) 谐波池（最多 2，确认池优先）
    for it in m_h[:quota_harm]:
        if len(got) >= quota_fill:
            break
        if dup(it):
            continue
        take(it, '谐波', stop_pct=0.0, use_harm_stop=True, tgt=r2(it.get('target1')))
    # 3) 吸筹 / 强势（最多 2）
    for it in m_a[:1]:
        if len(got) >= quota_fill:
            break
        if dup(it):
            continue
        take(it, '吸筹', stop_pct=-3.0, tgt=8.0)
    for it in m_p[:1]:
        if len(got) >= quota_fill:
            break
        if dup(it):
            continue
        take(it, '强势', stop_pct=-3.0, tgt=8.0)
    # 4) 补位：只从「弱同类」里挑，且单板块上限 FILL_CAP=2；同类都没有就宁可少给
    if len(got) < quota_fill:
        fill_n = 0
        for it in weak_all:
            if fill_n >= FILL_CAP or len(got) >= quota_fill:
                break
            if dup(it):
                continue
            if take(it, '补位', stop_pct=-3.0, tgt=8.0, filled=True):
                fill_n += 1
        if fill_n:
            notes.append('机械强同类只有 %d 只，补位 %d 只（≤%d）；若仍不足 8 只为同类库存不够，'
                         '不得跨行业硬凑' % (len(got) - fill_n, fill_n, FILL_CAP))
        if len(got) < quota_fill and orphans:
            notes.append('另有 %d 个候选因 sector 字段为空被丢弃（%s）'
                         % (len(orphans), '、'.join(orphans[:4])))
    if unpriced:
        notes.append('%d 只候选四池里都取不到价格、无法给出止损/目标，已丢弃（%s）'
                     % (len(unpriced), '、'.join(unpriced[:4])))

    for g in got:
        g['quota'] = '断板 %d / 谐波 %d / 吸筹强势 %d / 补位 %d' % (
            sum(1 for x in got if x['src'] == '断板反包'),
            sum(1 for x in got if x['src'] == '谐波'),
            sum(1 for x in got if x['src'] in ('吸筹', '强势')),
            sum(1 for x in got if x['src'] == '补位'))
    # 少于 3 只 = 这个板块机器根本选不出来：正确的反应是 17:00 叙事步换板块，不是硬凑 8 只
    if len(got) < 3:
        notes.append('机械候选仅 %d 只（<3）——该板块「机器选不出来」，'
                     '17:00 叙事步应换一个候选充足的板块，绝不允许拿别的板块股票来凑数' % len(got))
    return got, notes


def code6(it):
    return str(it.get('code') or '').replace('.SH', '').replace('.SZ', '').replace('.BJ', '').zfill(6)


def last_close_of(it):
    """断板池条目常无 lastClose 字段（R100z61 注释已确认），需从 kline 末根取收盘价；
    kline 两种容器都见过（list[day,open,close,high,low,vol] 与 dict），两种都要兼容，
    否则 dict 格式下 `(kline or [None])[-1][2]` 会抛 KeyError 把整只板块的选股直接打崩。"""
    lc = r2(it.get('lastClose'))
    if lc is not None:
        return lc
    for b in reversed(it.get('kline') or []):
        if isinstance(b, (list, tuple)) and len(b) > 2:
            return r2(b[2])
        if isinstance(b, dict):
            return r2(b.get('close'))
    return None


def zt_low(it):
    """断板反包：T 日（涨停日）最低价，作为**绝对价止损锚**（R100z61b）。

    涨停日已过去，其最低价是历史事实、不随每日重算变化，天然是固定锚 —— 正好治
    「止损 = 最新收盘 × 固定百分比」那个病（最新收盘每天变 → 止损线永远贴着现价
    下方 N 个百分点同步下移 → 价格跌多少线就降多少 → 理论上永不停损）。
    data.js 里 kline 两种容器都见过（list[day,open,close,high,low,vol] 与 dict），
    这里全部兼容。取不到返回 None，交给调用方回落百分比口径。
    """
    zt = it.get('ztDate')
    if not zt:
        return None
    for b in (it.get('kline') or []):
        if isinstance(b, (list, tuple)):
            if len(b) > 4 and b[0] == zt:
                return r2(b[4])
        elif isinstance(b, dict) and b.get('day') == zt:
            return r2(b.get('low'))
    return None


def mk(it, src, last, stop_pct=0.0, tgt=6.0, use_harm_stop=False, filled=False, stop_price=None):
    """tgt 传的是**百分数**（6 表示 +6%），stop_pct 同理（-2.5 表示 −2.5%）。

    ★踩坑（2026-10-01）：第一版把 6 当「0.06 倍」相乘，目标价算成 0.29 元这种荒谬值，
    页面挂上去会被当成笑话。现统一为价格 = last × (1 + pct/100)。

    ★R100z61b（2026-10-05 用户拍板）：止损锚换成**固定锚**，不再锚「最新收盘」。
    旧实现一律 `stop = last × (1+pct/100)`，而 last = lastClose 每天重算 → 止损线
    永远贴着现价下方 N 个百分点同步下移（trailing 反向），价格跌多少线就降多少，
    等于**理论上永远等不到触发**；只有走 use_harm_stop 的谐波是固定锚。
    新优先级：① 谐波形态位 ② 绝对价锚 stop_price（断板反包 = T 日最低价）
              ③ 条目已写入的 entryClose（上一轮生成时锁定的入场日收盘）
              ④ 兜底 last（仅首日，之后会被 ③ 接管）。
    """
    code = code6(it)
    name = norm(it.get('name'))
    # R100z163：吸筹池不带价 → 用 PX 索引兜底，否则 stop/target 全是 None（空纪律位）
    last = r2(last)
    if last is None:
        last = PX.get(code)
    # 止损锚：形态位 > 绝对价锚 > 已锁定的入场日收盘 > 兜底最新收盘
    hz_stop = r2(it.get('stop'))
    if use_harm_stop and hz_stop and last and 0.8 * last <= hz_stop <= 1.2 * last:
        stop = hz_stop
    elif stop_price and last and 0.8 * last <= stop_price <= 1.2 * last:
        stop = r2(stop_price)                          # 断板反包：T 日（涨停日）最低价
    else:
        _ec = r2(it.get('entryClose'))
        if _ec and last and 0.7 * last <= _ec <= 1.3 * last:
            stop = r2(_ec * (1 + stop_pct / 100.0))    # 入场日收盘锚（生成时锁定，固定）
        else:
            stop = r2(last * (1 + stop_pct / 100.0)) if last else None   # 兜底（会漂移，仅首日）
    # entryClose：机械池锁一次就不再变（谐波走形态位，不占这个字段）
    _ec = r2(it.get('entryClose'))
    entry_close = _ec if _ec else (r2(last) if (last and not use_harm_stop) else None)
    # 目标：谐波优先用形态 T1，但同样要过量级校验
    hz_tgt = r2(it.get('target1'))
    if use_harm_stop and hz_tgt and last and 1.2 * last <= hz_tgt <= 2.0 * last:
        target = hz_tgt
    else:
        target = r2(last * (1 + tgt / 100.0)) if last else None
    item = {'code': code, 'name': name, 'sector': norm(it.get('sector')),
            'src': src + ('·补位' if filled else ''),
            'note': (('[%s] ' % src) if filled else '') + norm(it.get('probNote') or it.get('note') or '')[:60],
            'lastClose': last, 'entryClose': entry_close,
            'exit': build_exit({'src': src}, stop, target)}
    return item


def parse_sectors_arg(raw):
    """--sectors 解析：全角/半角逗号、顿号、分号、竖线都认；去空、去重、保序。

    2026-10-21 实测：AI 手打板块名时四种分隔符都出现过，只认半角逗号会整串被当成
    一个超长板块名（→ 该板块 0 只候选，静默失明）。宁可多认几种，也不要静默失败。
    """
    out = []
    for x in re.split(r'[,，、;；|｜/／]+', str(raw or '')):
        x = norm(x)
        if x and x not in out:
            out.append(x)
    return out


def prev_keys():
    """读现有 stock_pool.json 的键，用于重键前后 diff（失败返回空集，不影响主流程）。"""
    try:
        with open(OUT, encoding='utf-8') as f:
            return list((json.load(f).get('bySector') or {}).keys())
    except Exception:                                        # noqa: BLE001
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--sectors', default=None,
                    help='逗号分隔的板块名：按这些名字重键（只换键不换股）；缺省读 aiPrediction.sectors')
    a = ap.parse_args()

    d = load()
    if not d:
        return 1

    du = d.get('duanban') or {}
    hm = d.get('harmonic') or {}
    ac = d.get('accumulation') or {}
    ps = d.get('powerScreen') or {}

    def flat(pool):
        out = []
        for it in (pool or []):
            code = str(it.get('code') or '').replace('.SH', '').replace('.SZ', '').zfill(6)
            if not code:
                continue
            item = dict(it)
            item['code'] = code
            out.append(item)
        return out

    pools = {
        'duanban': flat(du.get('confirmed')) + flat(du.get('watching')),
        'harmonic': flat(hm.get('confirmPool')) + flat(hm.get('watchPool')),
        'accumulation': flat(ac.get('scored')),
        'power': flat(ps.get('passed')),
    }

    PX.clear()
    for _arr in pools.values():
        for _it in _arr:
            _c, _p = code6(_it), last_close_of(_it)
            if _c and _p and not PX.get(_c):
                PX[_c] = _p

    if a.sectors:
        names = parse_sectors_arg(a.sectors)
        if not names:
            print('[ERR] --sectors 没解析出任何板块名（原值：%r）' % a.sectors)
            return 2
        sectors_source = 'cli'
    else:
        names = [norm(s.get('sector')) for s in (d.get('sectors') or [])
                 if norm((s or {}).get('sector'))]
        sectors_source = 'aiPrediction'

    old_keys = prev_keys() if a.sectors else []

    by_sector, coverage, fails, allnotes = {}, [], [], []
    for nm in names:
        lst, notes = pick_for(nm, pools)
        by_sector[nm] = lst
        got_src = set(x['src'] for x in lst)
        cov = {'sector': nm, 'picked': len(lst), 'src': sorted(got_src),
               'codes': [x['code'] for x in lst]}
        if not lst:
            cov['warn'] = '机械候选为空（板块名未匹配到任何候选池，需补 SECTOR_ALIAS 或候选不足）'
            fails.append(nm)
        coverage.append(cov)
        allnotes += notes

    res = {'ok': True, 'date': d.get('date'),
           'candidateDate': d.get('candidateDate'),
           'sectorsSource': sectors_source,
           'sectors': names,
           'quotaRule': '断板反包≤4 + 谐波≤2 + 吸筹/强势≤2 + 补位≤2',
           'bySector': by_sector, 'coverage': coverage, 'note': '；'.join(sorted(set(allnotes))),
           'pools': {k: len(v) for k, v in pools.items()},
           'fail': fails,
           'generatedAt': date.today().isoformat()}

    if a.write:
        # R100z165（L10 写盘原子化）：先 .tmp 再 os.replace，避免半截 stock_pool.json
        _tmp = OUT + '.tmp'
        with open(_tmp, 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        os.replace(_tmp, OUT)

    if a.json:
        print(json.dumps({k: res[k] for k in ('ok', 'date', 'candidateDate', 'sectorsSource',
                                              'bySector', 'coverage', 'pools', 'fail')},
                         ensure_ascii=False))
        return 0

    print('=== 机械选股池（R100z15）===')
    print('建键依据：%s%s ｜ 预测日 %s ｜ 候选数据日 %s' %
          ('--sectors 显式指定' if sectors_source == 'cli' else 'aiPrediction.sectors',
           '' if sectors_source == 'cli' else '（默认）',
           res['date'] or '—', res['candidateDate'] or '—'))
    if sectors_source == 'cli':
        kept = [k for k in names if k in old_keys]
        added = [k for k in names if k not in old_keys]
        dropped = [k for k in old_keys if k not in names]
        print('重键 diff：沿用 %d / 新增 %d / 丢弃旧键 %d' % (len(kept), len(added), len(dropped)))
        if dropped:
            print('   丢弃（已不再预测的旧键）：' + '、'.join(dropped))
    print('候选池规模：断板 %d / 谐波 %d / 吸筹 %d / 强势 %d' %
          (len(pools['duanban']), len(pools['harmonic']),
           len(pools['accumulation']), len(pools['power'])))
    for c in coverage:
        print('\n【%s】机械候选 %d 只' % (c['sector'], c['picked']))
        for it in by_sector.get(c['sector'], []):
            ex = it['exit']
            print('   %s %s [%-6s] 止损%8s 目标%8s' %
                  (it['code'], it['name'], it['src'].split('·')[0],
                   ex['stop'] if ex['stop'] is not None else '—',
                   ex['target'] if ex['target'] is not None else '—'))
        if c.get('warn'):
            print('   ⚠️ ' + c['warn'])
    if res['note']:
        print('\nnote：' + res['note'])
    # 覆盖：机械池总候选 / 应选
    tot_pick = sum(c['picked'] for c in coverage)
    print('\n合计机械产出 %d 只（预测板块 %d 个）' % (tot_pick, len(coverage)))
    if a.write:
        print('已写入 ' + os.path.basename(OUT))
    return 0


if __name__ == '__main__':
    sys.exit(main())

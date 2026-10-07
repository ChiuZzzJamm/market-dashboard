#!/usr/bin/env python3
"""机械选股池（R100z15 缺口②）——把「断板池 / 谐波池 / 吸筹池 / 强势池」接回决策链。

为什么需要它（2026-10-01 体检实测的最根本问题）：
    项目自己造了四个选股引擎（check_duanban / harmonic_detect / accumulation_score / power_screener），
    16:00 那个交易日里却只有 6/48 只（12.5%）来自断板池，剩下 42 只是 AI 看 note 手写的
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
    stock_pool.json { ok, date, bySector:{板块:[8 只]}, coverage, meta, fail }

校验器：validate_stocks.py 的 check_stock_pool —— 板块**有机械候选时 AI 必须照抄**，
    照抄 ≠ 全等（候选不足 8 只时允许少于 8），但出现机械池以外的 code 一律 FAIL。

用法：
    python3 build_stock_pool.py                 # 干跑，打印每个板块的机械池与覆盖率
    python3 build_stock_pool.py --write         # 写 stock_pool.json
    python3 build_stock_pool.py --json          # 机器可读
"""
import argparse
import json
import os
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

    # 1) 断板确认池（最多 4）
    for it in m_d[:quota_duan]:
        if len(got) >= quota_fill:
            break
        last = last_close_of(it)
        # R100z61b：止损锚改 T 日（涨停日）最低价（绝对价、固定），不再用 last ×0.975。
        # 形态淘汰（破 T 日低点×0.98 出池）仍由 check_duanban 独立判定，两条线分离。
        got.append(mk(it, '断板反包', last, stop_pct=-2.5, tgt=6.0, stop_price=zt_low(it)))
    # 2) 谐波池（最多 2，确认池优先）
    for it in m_h[:quota_harm]:
        if len(got) >= quota_fill:
            break
        got.append(mk(it, '谐波', r2(it.get('lastClose')), stop_pct=0.0, use_harm_stop=True,
                      tgt=r2(it.get('target1'))))
    # 3) 吸筹 / 强势（最多 2）
    for it in m_a[:1]:
        if len(got) >= quota_fill:
            break
        got.append(mk(it, '吸筹', r2(it.get('lastClose')), stop_pct=-3.0, tgt=8.0))
    for it in m_p[:1]:
        if len(got) >= quota_fill:
            break
        got.append(mk(it, '强势', r2(it.get('lastClose')), stop_pct=-3.0, tgt=8.0))
    # 4) 补位：只从「弱同类」里挑，且单板块上限 FILL_CAP=2；同类都没有就宁可少给
    if len(got) < quota_fill:
        fill_n = 0
        for it in weak_all:
            if fill_n >= FILL_CAP or len(got) >= quota_fill:
                break
            code = code6(it)
            if any(g['code'] == code for g in got):
                continue
            got.append(mk(it, '补位', r2(it.get('lastClose')), stop_pct=-3.0, tgt=8.0, filled=True))
            fill_n += 1
        if fill_n:
            notes.append('机械强同类只有 %d 只，补位 %d 只（≤%d）；若仍不足 8 只为同类库存不够，'
                         '不得跨行业硬凑' % (len(got) - fill_n, fill_n, FILL_CAP))
        if len(got) < quota_fill and orphans:
            notes.append('另有 %d 个候选因 sector 字段为空被丢弃（%s）'
                         % (len(orphans), '、'.join(orphans[:4])))

    for g in got:
        g['quota'] = '断板 %d / 谐波 %d / 吸筹强势 %d / 补位 %d' % (
            sum(1 for x in got if x['src'] == '断板反包'),
            sum(1 for x in got if x['src'] == '谐波'),
            sum(1 for x in got if x['src'] in ('吸筹', '强势')),
            sum(1 for x in got if x['src'] == '补位'))
    # 少于 3 只 = 这个板块机器根本选不出来：正确的反应是 16:00 换板块，不是硬凑 8 只
    if len(got) < 3:
        notes.append('机械候选仅 %d 只（<3）——该板块「机器选不出来」，'
                     '16:00 应换一个候选充足的板块，绝不允许拿别的板块股票来凑数' % len(got))
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--json', action='store_true')
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

    by_sector, coverage, fails, allnotes = {}, [], [], []
    for s in (d.get('sectors') or []):
        nm = norm(s.get('sector'))
        if not nm:
            continue
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
           'quotaRule': '断板反包≤4 + 谐波≤2 + 吸筹/强势≤2 + 补位≤2',
           'bySector': by_sector, 'coverage': coverage, 'note': '；'.join(sorted(set(allnotes))),
           'pools': {k: len(v) for k, v in pools.items()},
           'fail': fails,
           'generatedAt': date.today().isoformat()}

    if a.write:
        with open(OUT, 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)

    if a.json:
        print(json.dumps({k: res[k] for k in ('ok', 'date', 'bySector', 'coverage', 'pools', 'fail')},
                         ensure_ascii=False))
        return 0

    print('=== 机械选股池（R100z15）===')
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

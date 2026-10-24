#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标的数量与配比硬校验（R82，零依赖，读本地 data.js）。

校验规则（与用户分层口径逐字对应）：
1) 五节要闻（bullNews/bearNews/macroNews/intlNews/bankViews）每条 impacts 的每个板块：
   恰好 8 只，且四类配比必须符合 N 分层表（N = 该板块 stocks 中「断板反包」数量）：
       N>=5: 龙头1 概念1 小盘1 断板5
       N=4 : 龙头2 概念1 小盘1 断板4
       N=3 : 龙头2 概念2 小盘1 断板3
       N=2 : 龙头2 概念2 小盘2 断板2
       N=1 : 龙头2 概念2 小盘3 断板1
       N=0 : 龙头2 概念2 小盘4 断板0
2) aiPrediction.sectors 每个板块：同样恰好 8 只 + 上述配比。
3) bullish/bearish 每个主题：恰好 4 只（不分层）。
4) 所有标的 code 必须为 60/00 开头沪深主板（禁 688/689、300/301/302、4/8/92 开头）。
5) macroNews/intlNews/bankViews 每条要闻卡应含 direction 字段（看涨/看跌/中性）；缺失或非法值
   在写回 data.js 时自动补为 中性（前端渲染为中性灰，降级表现与旧版一致），并输出 WARNING 日志，
   但不阻断部署（避免单一装饰字段卡死整轮更新）。bullNews/bearNews 无 direction，不处理。
6) 五节要闻 impacts 分组数 1~4（R90b B方案：theme 必须与新闻传导链相关、宁少勿凑，允许只挂
   1~2 组；仅 >4 超上限输出 WARNING 不阻断部署）。另含要闻质量软闸门（R90b）：theme 与
   title/summary 无关联、组内标的重复凑数（重复>2）、条目内标的复用>3 次、同 sector 跨节
   重复出现——均 WARNING 不阻断，由自动化 prompt 与 AI 自检负责修正。
7) duanban 断板反包模块（R87，软闸门）：确认池/观察池全部标的须 60/00 开头沪深主板
   （违规自动剔除 + WARNING）、probability 缺失/非法自动补 50 + WARNING、缺 bullRefs/
   kline 输出 WARNING。模块整体缺失不检查（16:00 数据步职责）。

类别按 note 前缀判定：断板反包→断板；板块龙头/龙头→龙头；相关概念/概念→概念；
小盘→小盘；其余前缀视为无法识别（报违规）。

顺序硬约束（R83）：每只清单内的标的必须按「龙头 → 概念 → 小盘人气 → 断板反包」
的先后顺序排列（同类内保持原相对序），不得穿插交错，否则判违规。

用法: python3 validate_stocks.py [--json]
退出码: 0 = 全部合规(输出 ALL OK)；1 = 有违规。
"""
import json
import os
import re
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# R100z53：新闻来源白名单从「只写在看板自动化 prompt 正文里」下沉成机器闸门，
# 判定口径单一来源 news_sources.py（prompt 只指路，不再各写一份免得口径漂移）。
import news_sources

# R100z175：deduce 前缀判定**复用 common 的单一事实源**，不许在本文件再写一份。
# 事故：本文件一度自带 `(主推|反证)\s*([:：]|是)`，只认冒号与「是」→ 实际出现的
# 「反证在资金：」与裸拼「反证证券跌1.21%」都被**静默放行**（第五份副本没跟上）。
# 判据必须与清洗器逐字一致：清洗器会剥的形态，校验器就必须拦，否则脏数据合法通过。
from common import _DD_PREFIX_RE

BAD_PREFIX = ('688', '689', '300', '301', '302', '4', '8', '92')

VALID_DIR = ('看涨', '看跌', '中性')

# 仅这三节要闻需要 direction（页面涨跌徽标来源）；bullNews/bearish 落在利好/利空列。
DIR_KEYS = ('macroNews', 'intlNews', 'bankViews')

TIERS = {
    5: (1, 1, 1), 6: (1, 1, 1),  # N>=5
    4: (2, 1, 1),
    3: (2, 2, 1),
    2: (2, 2, 2),
    1: (2, 2, 3),
    0: (2, 2, 4),
}


def load_data():
    """R100z158c：改走 common.load_dashboard_data —— 与 R100z156 同口径。

    旧实现固定走 node eval 通道，JS 的 `JSON.stringify` 会把 31304.0 归一成 31304；
    而本脚本在**非 --no-fix** 模式下会在 main 尾部把整份 D 重排写回 data.js（reorder 落盘），
    于是「读-改-写」一次就把 12 万个成交量/整数价格从 `31304.0` 变成 `31304`，产生约
    25 万字符的**纯格式 diff**（2026-10-20 22:32 手动跑一次实测 249,408 字符），把当天真改动
    全淹掉、也让 git blame 失真。自动化链路都带 `--no-fix` 所以一直没暴露。

    现在：优先 Python json.loads 直解（保留 int/float 字面量，加载→写回可字节级不变）；
    仅当文本非纯 JSON（遗留 JS 字面量 / NaN / Infinity）才回退 node 通道，且 node 路径
    由 common.find_node() 解析（不再硬编码 `run-node` 单一路径）。
    """
    import common
    return common.load_dashboard_data(os.path.dirname(os.path.abspath(__file__)))


def cat_of(note):
    n = (note or '').strip()
    if n.startswith('断板反包'):
        return 'D'
    if n.startswith('板块龙头') or n.startswith('龙头'):
        return 'L'
    if n.startswith('相关概念') or n.startswith('概念'):
        return 'C'
    if n.startswith('小盘'):
        return 'X'
    return '?'


def board_bad(code):
    c = str(code or '')
    return (not re.fullmatch(r'\d{6}', c)) or c.startswith(BAD_PREFIX)


def check_tiered(stocks, where, violations):
    """恰好 8 只 + N 分层配比 + 主板过滤 + 顺序硬约束（龙头→概念→小盘→断板）。"""
    if not isinstance(stocks, list) or not stocks:
        violations.append(f"{where}: stocks 缺失或为空")
        return
    for s in stocks:
        if isinstance(s, dict) and board_bad(s.get('code')):
            violations.append(f"{where}: 含非沪深主板标的 {s.get('code')} {s.get('name','')}")
    from collections import Counter
    cnt = Counter()
    seq = []
    for s in stocks:
        c = cat_of((s or {}).get('note', ''))
        cnt[c] += 1
        seq.append(c)
    n = len(stocks)
    if n != 8:
        violations.append(
            f"{where}: 共 {n} 只（应为 8）| 配比 L{cnt['L']} C{cnt['C']} X{cnt['X']} D{cnt['D']}"
            + ("| 含无法识别类别note " + str(dict(cnt)['?']) if cnt['?'] else ''))
        return
    # 组内 code 必须互不相同（防「8 个位置只有 6~7 只不同标的」的隐藏重复 bug）
    codes = [str((s or {}).get('code')) for s in stocks]
    if len(set(codes)) != 8:
        dup = [c for c, k in Counter(codes).items() if k > 1]
        violations.append(f"{where}: 组内标的 code 存在重复 {dup}（{len(set(codes))} 只互异，应为 8 只互不相同）")
        return
    d = cnt['D']
    if d > 5:
        violations.append(f"{where}: 断板反包 {d} 只 > 5，超出分层表上限")
        return
    if cnt['?']:
        violations.append(
            f"{where}: {cnt['?']} 只标的 note 前缀无法识别四类身份（须以 断板反包/板块龙头/相关概念/小盘 开头）")
        return
    need = TIERS[d]
    if (cnt['L'], cnt['C'], cnt['X']) != need:
        violations.append(
            f"{where}: N={d} 应为 龙头{need[0]} 概念{need[1]} 小盘{need[2]} 断板{d}，"
            f"实际 龙头{cnt['L']} 概念{cnt['C']} 小盘{cnt['X']} 断板{cnt['D']}")
        return
    # 顺序硬约束：必须严格 龙头(全部) → 概念(全部) → 小盘(全部) → 断板(全部)
    ORDER = {'L': 0, 'C': 1, 'X': 2, 'D': 3}
    if seq != sorted(seq, key=lambda c: ORDER[c]):
        violations.append(
            f"{where}: 顺序不合规（应为 龙头→概念→小盘人气→断板反包），实际序列 {' '.join(seq)}")


def check4(stocks, where, violations):
    """bullish/bearish 每主题恰好 4 只 + 主板过滤（note 为自由文本，不要求四类前缀/顺序）。"""
    if not isinstance(stocks, list) or not stocks:
        violations.append(f"{where}: stocks 缺失或为空")
        return
    for s in stocks:
        if isinstance(s, dict) and board_bad(s.get('code')):
            violations.append(f"{where}: 含非沪深主板标的 {s.get('code')} {s.get('name','')}")
    if len(stocks) != 4:
        violations.append(f"{where}: 共 {len(stocks)} 只（应为 4）")


def fix_direction_inplace(D):
    """macroNews/intlNews/bankViews 每条应带 direction（看涨/看跌/中性）；缺失/非法自动补为 中性。
    返回 WARNING 信息列表（非致命，不阻断部署；direction 在写回 data.js 时一并修正）。"""
    warns = []
    for mk in ('ashare', 'us'):
        sec = D.get(mk) or {}
        for key in DIR_KEYS:
            for i, nw in enumerate(sec.get(key) or []):
                if not isinstance(nw, dict):
                    continue
                d = nw.get('direction')
                if d not in VALID_DIR:
                    nw['direction'] = '中性'
                    warns.append(
                        f"{mk}.{key}[{i}]({(nw.get('sector') or '')[:14]}): direction 缺失/非法（{d!r}）→ 自动补为 中性")
    return warns


def check_impact_count(D):
    """五节要闻 impacts 分组数上限检查（R90b B方案：分组数 1~4、宁少勿凑——theme 必须与
    新闻传导链相关，允许只挂 1~2 组；仅对 >4 超上限输出 WARNING，不阻断部署）。"""
    warns = []
    for mk in ('ashare', 'us'):
        sec = D.get(mk) or {}
        for key in ('bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews'):
            for i, nw in enumerate(sec.get(key) or []):
                if not isinstance(nw, dict):
                    continue
                n_imp = len(nw.get('impacts') or [])
                if n_imp > 4:
                    warns.append(
                        f"{mk}.{key}[{i}]({(nw.get('sector') or '')[:14]}): impacts 分组数 {n_imp}"
                        f"（上限 4 组，应精简为实际受影响板块）")
    return warns


def _bigrams(s):
    s = re.sub(r'\s+', '', str(s or ''))
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}


def check_news_quality(D):
    """R90b B/C 方案软闸门（全部 WARN，不阻断部署）：
    a) theme 相关性：impact theme 与该条 title+summary 无任何二字片段交集 → 可能凑板块；
    b) 组内重复凑数：同一 impact 组内同 code 出现 >1 次，超额（重复只数）>2 → WARN；
    c) 条目级复用：同一条要闻内同一标的跨组出现 >3 次 → WARN；
    d) 跨节同主题重复：同一卡内同一 sector 出现在 ≥2 节 → WARN（美债既看涨又中性类问题）。
    e) 主角板块（R91e）：第一组 theme 与 title/summary 无二字交集 → WARN（主角板块缺席、只挂外围板块类问题）。"""
    warns = []
    for mk in ('ashare', 'us'):
        sec = D.get(mk) or {}
        sector_secs = {}
        for key in ('bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews'):
            for i, nw in enumerate(sec.get(key) or []):
                if not isinstance(nw, dict):
                    continue
                label = f"{mk}.{key}[{i}]({(nw.get('sector') or '')[:14]})"
                text = str(nw.get('title') or '') + str(nw.get('summary') or '')
                text_bg = _bigrams(text)
                entry_cnt = {}
                for j, imp in enumerate(nw.get('impacts') or []):
                    if not isinstance(imp, dict):
                        continue
                    th = str(imp.get('theme') or '')
                    if th and not (_bigrams(th) & text_bg):
                        if j == 0:
                            warns.append(f"{label}.impacts[0](theme={th[:12]}): 第一组非主角板块且与新闻无传导关联（R91e：第一组必须是消息主角板块本身，如黄金新闻第一组应为「贵金属」而非「饰品」）")
                        else:
                            warns.append(f"{label}.impacts[{j}](theme={th[:12]}): theme 未在 title/summary 传导链出现，疑似凑板块（B口径：宁少勿凑）")
                    stocks = imp.get('stocks') or []
                    from collections import Counter
                    cc = Counter(str(s.get('code')) for s in stocks if isinstance(s, dict))
                    dup_extra = sum(v - 1 for v in cc.values() if v > 1)
                    if dup_extra > 2:
                        dnames = {str((s or {}).get('code')): (s or {}).get('name') for s in stocks if isinstance(s, dict)}
                        dups = {dnames.get(k, k): v for k, v in cc.items() if v > 1}
                        warns.append(f"{label}.impacts[{j}]({th[:12]}): 组内标的重复凑数 {dups}（重复 {dup_extra} 只 >2；小板块允许少量复用但须换 note 角度）")
                    for s in stocks:
                        if isinstance(s, dict):
                            entry_cnt[str(s.get('code'))] = entry_cnt.get(str(s.get('code')), 0) + 1
                over = {k: v for k, v in entry_cnt.items() if v > 3}
                if over:
                    warns.append(f"{label}: 同一标的在条目内跨组复用超限 {over}（>3 次，请轮换其他标的）")
                sector_secs.setdefault(str(nw.get('sector') or ''), set()).add(key)
        for sv, keys in sector_secs.items():
            if sv and len(keys) >= 2:
                warns.append(f"{mk}: 同主题「{sv[:16]}」跨节出现在 {sorted(keys)}（C口径：同一事件只保留一条，方向冲突以最新事件为准）")
    return warns


def check_freshness(D):
    """ashare.updatedAt 与 tradeDate 日期不一致 → WARN（防止行情数据新鲜但时间戳停留在旧日）。"""
    a = D.get('ashare') or {}
    td = str(a.get('tradeDate') or '')
    ua = str(a.get('updatedAt') or '')
    m = re.match(r'(\d{4}-\d{2}-\d{2})', ua)
    if td and m and m.group(1) != td:
        return [f"ashare.updatedAt 日期({m.group(1)})与 tradeDate({td}) 不一致——行情数据可能已更新但时间戳未刷新，请核对"]
    return []


def check_horizon(D):
    """R91：macroNews/intlNews/bankViews 每个 impact 须带 horizon（长线/短线）→ 缺失/非法 WARN。"""
    warns = []
    for mk in ('ashare', 'us'):
        sec = D.get(mk) or {}
        for key in ('macroNews', 'intlNews', 'bankViews'):
            for i, nw in enumerate(sec.get(key) or []):
                if not isinstance(nw, dict):
                    continue
                for j, imp in enumerate(nw.get('impacts') or []):
                    if not isinstance(imp, dict):
                        continue
                    hz = imp.get('horizon')
                    if hz not in ('长线', '短线'):
                        warns.append(
                            f"{mk}.{key}[{i}].impacts[{j}]({imp.get('theme', '')}): "
                            f"horizon 缺失或非法（{hz!r}），须为「长线」或「短线」")
    return warns


# ── R100z152g（2026-10-19）：叙事型 deduce 白名单 + scope 分派 ─────────────────────
# 实发事故：ashare.fundNote_deduce.conf='中确信'（应为「中等确信」）溜过原闸门并上线——
# 原 check_deduce 只扫 aiPrediction.sectors，17:00 run 照它的报错修掉了 sectors 内 5 处同错、
# 唯独漏掉闸门外的这 1 处（git 实证：「中确信」6→1、「中等确信」61→66）。
# 分派原则 =「写它的任务跑哪个 scope」，与 check_logic_depth(ai) / check_reason_depth(us) 对齐：
#   ai 组 = 决策链（aiPrediction + A股利好/利空卡）
#   us 组 = 叙事段落（A股/美股 板块理由、五节要闻、题材掘金 + ashare.fundNote_deduce）
# 不入白名单：量化池（harmonic/accumulation/powerScreen）的 deduce 恒为 None（纯指标页
#   by-design 占位，2026-10-19 实测 99 处全 None）、us.bullish/bearish 当前不带 deduce。
# 列表节点用「有则校验」语义（deduce 缺失/非对象 = 未启用，跳过）；仅 aiPrediction.sectors
#   保持「缺失即 FAIL」旧语义（三候挑选股是前端推演段硬依赖）。
_NARRATIVE_DEDUCE_AI = (
    ('aiPrediction', 'sectors', True),
    ('ashare', 'bullish', False),
    ('ashare', 'bearish', False),
)
_NARRATIVE_DEDUCE_US = (
    ('ashare', 'sectorsUp', False), ('ashare', 'sectorsDown', False),
    ('ashare', 'themePicks', False),
    ('ashare', 'bullNews', False), ('ashare', 'bearNews', False),
    ('ashare', 'macroNews', False), ('ashare', 'intlNews', False),
    ('ashare', 'bankViews', False),
    ('us', 'sectorsUp', False), ('us', 'sectorsDown', False),
    ('us', 'bullNews', False), ('us', 'bearNews', False),
    ('us', 'macroNews', False), ('us', 'intlNews', False),
    ('us', 'bankViews', False),
)


def _dd_validate(d, tag, hards, warns):
    """单条 deduce 结构校验（R100z152g：自 check_deduce 抽出，供白名单各节点复用）。
    结构缺失/枚举非法 → hard（阻断部署）；文本过短 → warn。d 须为 dict（调用方保证）。"""
    cands = d.get('cands')
    if not isinstance(cands, list) or len(cands) != 2:
        cnt = 0 if cands is None else (len(cands) if isinstance(cands, list) else f"非列表({type(cands).__name__})")
        hards.append(f"{tag}: deduce.cands 必须恰好 2 个候选（当前 {cnt} 个）")
    for k, c in enumerate(cands if isinstance(cands, list) else []):
        if not isinstance(c, dict) or not str(c.get('text') or '').strip():
            hards.append(f"{tag}: deduce.cands[{k}] 缺 text 推演内容")
    conf = d.get('conf')
    if conf not in ('高确信', '中等确信', '推测', '直觉'):
        hards.append(f"{tag}: deduce.conf 非法（{conf!r}），须取 高确信/中等确信/推测/直觉 之一（禁方向词）")
    counter = d.get('counter')
    if not isinstance(counter, str) or not counter.strip():
        hards.append(f"{tag}: deduce.counter 反证缺失或为空（R99p：不分确信度必列）")
    for key in ('main', 'counter'):
        v = d.get(key)
        if isinstance(v, str):
            # R100z173/R100z175：前缀判定改用 common._DD_PREFIX_RE（单一事实源），
            # 覆盖 冒号 / 「是」 / 「在<标记词>：」 / 裸拼 四种真实形态，
            # 谓语白名单（成立/不成立/已被证伪…）不误伤。禁止在本文件另写正则。
            _pm = _DD_PREFIX_RE.match(v)
            if _pm:
                _lbl = '主推' if _pm.group(0).strip().startswith('主推') else '反证'
                hards.append(f"{tag}: deduce.{key} 自带「{_pm.group(0).strip()[:12]}」前缀"
                             f"（R100z175：渲染层会补「{_lbl}：」→ 页面重复成"
                             f"「{_lbl}：{_lbl}…」）")
            if len(v.strip()) < 6:
                warns.append(f"{tag}: deduce.{key} 过短（{len(v.strip())} 字），推演信息量不足")


def check_deduce(D, scope='all'):
    """R99m/R99o/R99p：aiPrediction 每板块必须内嵌 deduce 三候选推演（前端推演段依赖）。
    结构性缺失（deduce 非对象 / cands 数≠2 / conf 非枚举 / counter 缺失或为空 / 自带「主推：」「反证：」前缀）
    → 硬违规（FAIL，阻断部署）；cands 文本过短等质量项 → WARN。

    R100z152g：白名单扩到全部叙事型 deduce 节点，按 scope 过滤（ai 组 / us 组，见上方注释）。"""
    hards, warns = [], []
    targets = []
    if scope in ('all', 'ai'):
        targets += _NARRATIVE_DEDUCE_AI
    if scope in ('all', 'us'):
        targets += _NARRATIVE_DEDUCE_US
    for mkt, key, required in targets:
        arr = (D.get(mkt) or {}).get(key) or []
        if not isinstance(arr, list):
            warns.append(f"{mkt}.{key} 非列表（{type(arr).__name__}），deduce 校验跳过")
            continue
        for i, item in enumerate(arr):
            if not isinstance(item, dict):
                continue
            nm = str(item.get('name') or item.get('sector') or item.get('title') or '')[:14]
            tag = f"{mkt}.{key}[{i}]({nm})"
            d = item.get('deduce')
            if not isinstance(d, dict):
                if required:
                    hards.append(f"{tag}: 缺 deduce 三候选推演对象（R99m 内嵌硬约束，前端推演段将空白）")
                continue
            _dd_validate(d, tag, hards, warns)
        if required and not arr:
            warns.append(f"{mkt}.{key} 为空，deduce 校验跳过")
    if scope in ('all', 'us'):
        d = (D.get('ashare') or {}).get('fundNote_deduce')
        if isinstance(d, dict):
            _dd_validate(d, 'ashare.fundNote_deduce', hards, warns)
    return hards, warns


def check_exp(D):
    """R100z11：aiPrediction.sectors[].exp —— 4c 的 price-in / 四象限落位结构化成字段（前端徽章 + 脚本可校验的前提）。
    带 exp 就必须字段完整；exp.layer=='E' 时该板块 confidence 不得为「高」
    （预期层上限「中等确信」，对应 08:30 禁令⑰）。E 层字段不齐 → 硬违规 FAIL。"""
    hards, warns = [], []
    for i, s in enumerate((D.get('aiPrediction') or {}).get('sectors') or []):
        if not isinstance(s, dict):
            continue
        tag = f"aiPrediction.sectors[{i}]({str(s.get('sector'))[:14]})"
        exp = s.get('exp')
        if not isinstance(exp, dict) or not exp:
            continue  # 未声明预期层 → 不在机械校验范围（散文口径仍有效）
        if exp.get('layer') not in ('F', 'E', 'O'):
            hards.append(f"{tag}: exp.layer 非法（{exp.get('layer')!r}），须为 F/E/O 之一")
        if exp.get('layer') == 'E':
            if str(s.get('confidence')) == '高':
                hards.append(f"{tag}: 预期层(E) 标了「高确信」，上限只能是「中等确信」（R100z9 禁令⑰）")
            pi = exp.get('priceIn')
            if isinstance(pi, bool) or not isinstance(pi, (int, float)) or not -100 <= pi <= 200:
                hards.append(f"{tag}: exp.priceIn 须为 -100~200 的数值（当前 {pi!r}）")
            if not str(exp.get('priceInBasis') or '').strip():
                hards.append(f"{tag}: exp.priceInBasis 为空（须写「窗口期板块累计 +X%、龙头 N 连板、扩散 M 只」可核查依据）")
            if str(exp.get('verdict') or '') not in ('加确认', '维持', '兑现降级'):
                hards.append(f"{tag}: exp.verdict 非法（{exp.get('verdict')!r}），须为 加确认/维持/兑现降级 三选一")
            if '×' not in str(exp.get('quad') or ''):
                warns.append(f"{tag}: exp.quad 未写成「公布值×price-in」四象限格式（当前 {exp.get('quad')!r}）")
    return hards, warns


def check_stockcheck(D):
    """R100z11：verification.stockCheck —— 验证颗粒度下移到标的层面（方向对但 8 只全选错必须被检出）。
    hitN 须 0~8 整数、alignRate 须 = hitN/8、avgPct 须数值，details 条数对齐 sectors → WARN。"""
    ap = D.get('aiPrediction') or {}
    # R100z12 闸门口径修正：verification 由 17:00 叙事步填写，08:30（盘前）与周日（date=下周一）
    # 按设计就是 null，此时「stockCheck 缺失」属正常，绝不能报成每日必现的假 WARN
    #（否则 08:30 的 5.5 校验会出现一个永远修不好的 WARN，逼 AI 反复绕）。
    if not isinstance(ap.get('verification'), dict):
        return []
    v = ap['verification']
    sc = v.get('stockCheck')
    if sc is None:
        # 当日既已写了 verification 却没有 stockCheck → 才真正属于漏项
        return ["顶层 aiPrediction.verification 已写但 stockCheck 缺失（R100z11：标的层面验证未落地，"
                "「方向对但选股错」无法被检出）"]
    if not isinstance(sc, list):
        return ["verification.stockCheck 不是数组"]
    warns = []
    n_sec = len(ap.get('sectors') or [])
    if n_sec and len(sc) != n_sec:
        warns.append(f"verification.stockCheck({len(sc)} 条) 与 sectors({n_sec} 个) 不一一对应")
    for i, x in enumerate(sc):
        if not isinstance(x, dict):
            warns.append(f"stockCheck[{i}] 不是对象")
            continue
        nm = str(x.get('sector') or '')[:14]
        n = x.get('hitN')
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 8:
            warns.append(f"stockCheck[{i}]({nm}): hitN 非法（{n!r}），须 0~8 的整数")
            continue
        ar = x.get('alignRate')
        if not isinstance(ar, (int, float)):
            warns.append(f"stockCheck[{i}]({nm}): alignRate 缺失或非法（须 = hitN/8 = {n / 8.0:.3f}）")
        elif abs(ar - n / 8.0) > 0.01:
            warns.append(f"stockCheck[{i}]({nm}): alignRate({ar}) 与 hitN/8({n / 8.0:.3f}) 不一致")
        if not isinstance(x.get('avgPct'), (int, float)):
            warns.append(f"stockCheck[{i}]({nm}): avgPct 缺失或非数值（8 只标的当日平均涨幅）")
    return warns


def check_impact_merge(D):
    """R100z81：同一条要闻内出现多个 theme 完全相同的 impact 分组 → 数据层碎片化
    （本应合并为 1 组 8 只去重）。前端已按 theme 归并兜底，但源头仍须禁止，故 WARN。"""
    warns = []
    from collections import Counter
    for mk in ('ashare', 'us'):
        sec = D.get(mk) or {}
        for key in ('bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews'):
            for i, nw in enumerate(sec.get(key) or []):
                if not isinstance(nw, dict):
                    continue
                themes = [str(im.get('theme') or im.get('sector') or '').strip()
                          for im in (nw.get('impacts') or []) if isinstance(im, dict)]
                cnt = Counter(t for t in themes if t)
                for th, c in cnt.items():
                    if c > 1:
                        warns.append(
                            f"{mk}.{key}[{i}]({(nw.get('sector') or '')[:14]}): "
                            f"同 theme「{th}」被拆成 {c} 个 impact 分组（应合并为 1 组 8 只去重，"
                            f"避免前端重复标题 + 稀疏网格）")
    return warns


def check_outlook_depth(D):
    """R100z81：大盘趋势研判 outlook 深度 + 禁英文内部字段名。
    <300 字 → WARN；含 cal_factor / alignRate 等内部变量名 → WARN（CRO 是合法医药术语，不查）。"""
    warns = []
    for mk in ('ashare', 'us'):
        d = (D.get(mk) or {}).get('outlook')
        if not d:
            continue
        text = str(d)
        if len(text) < 300:
            warns.append(f"{mk}.outlook 仅 {len(text)} 字（硬要求 300-500 字，深度不足）")
        for kw in ('cal_factor', 'alignRate'):
            if kw in text:
                warns.append(f"{mk}.outlook 含英文内部字段名「{kw}」（须改写为中文叙述，"
                             f"如「与节前量化给出的落袋/回补倾向一致」）")
    return warns


def check_reason_depth(D):
    """R100z81：板块异动 reason.detail 与题材掘金 logic 深度 + 模板化检测。
    reason.detail <40 字或题材掘金 logic <40 字 → WARN；跨板块高度雷同（模板化套话）→ WARN。"""
    warns = []
    from difflib import SequenceMatcher
    for mk in ('ashare', 'us'):
        sec = D.get(mk) or {}
        for key in ('sectorsUp', 'sectorsDown'):
            for i, s in enumerate(sec.get(key) or []):
                if not isinstance(s, dict):
                    continue
                det = str((s.get('reason') or {}).get('detail') or '').strip()
                if det and len(det) < 40:
                    warns.append(f"{mk}.{key}[{i}]({(s.get('name') or '')[:14]}): 行业原因仅 {len(det)} 字"
                                 f"（<40，须写满 60-110 字含研报/政策/事件催化）")
        for i, t in enumerate(sec.get('themePicks') or []):
            if not isinstance(t, dict):
                continue
            lg = str(t.get('logic') or '').strip()
            if lg and len(lg) < 40:
                warns.append(f"{mk}.themePicks[{i}]({(t.get('event') or '')[:14]}): 逻辑传导仅 {len(lg)} 字"
                             f"（<40，须写满 70-140 字四级传导链）")
    # 模板化：同一市场内多条 reason.detail 两两相似度 ≥0.85（雷同套话）
    for mk in ('ashare', 'us'):
        dets = [str((s.get('reason') or {}).get('detail') or '').strip()
                for s in (((D.get(mk) or {}).get('sectorsUp') or []) + ((D.get(mk) or {}).get('sectorsDown') or []))
                if isinstance(s, dict) and (s.get('reason') or {}).get('detail')]
        n = len(dets)
        hit = False
        for a in range(n):
            if hit:
                break
            for b in range(a + 1, n):
                if len(dets[a]) >= 20 and len(dets[b]) >= 20:
                    if SequenceMatcher(None, dets[a], dets[b]).ratio() >= 0.85:
                        warns.append(f"{mk}: 板块异动行业原因存在高度雷同（相似度 "
                                     f"{SequenceMatcher(None, dets[a], dets[b]).ratio():.0%}），"
                                     f"疑似模板化套话，须按各板块真实催化分别撰写")
                        hit = True
                        break
    return warns


def check_note_leak(D):
    """R100z81：verification / stockCheck 的 note 禁写 alignRate= 等英文内部字段名
    （前端已清洗，但源头须禁止，故 WARN）。"""
    warns = []
    ap = D.get('aiPrediction') or {}
    v = ap.get('verification')
    if not isinstance(v, dict):
        return warns
    for d in (v.get('details') or []):
        if isinstance(d, dict) and ('alignRate' in str(d.get('note') or '') or 'cal_factor' in str(d.get('note') or '')):
            warns.append(f"aiPrediction.verification.details({str(d.get('sector') or '')[:14]}): note 含英文内部字段名（须中文叙述）")
    for sc in (v.get('stockCheck') or []):
        if isinstance(sc, dict) and ('alignRate' in str(sc.get('note') or '') or 'cal_factor' in str(sc.get('note') or '')):
            warns.append(f"aiPrediction.verification.stockCheck({str(sc.get('sector') or '')[:14]}): note 含 alignRate= 等英文内部字段名（前端已清洗，源头须禁写）")
    return warns


def check_calibration(D):
    """R100z11：置信度后验校准门槛落地校验——读 calibration_state.json 的降档门槛，
    当日标「高确信」的条数若超过门槛 → WARN（台账已生效但当日越线）。
    ★R100z13：文件名不带点——GitHub Pages 不发布 dotfile，前端要靠 fetch('calibration_log.json')
    画命中横条，两个台账都必须能在 Pages 上被读到。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calibration_state.json')
    if not os.path.exists(path):
        return ["未找到 calibration_state.json（track_calibration.py 未运行，置信度后验校准门槛未生效）"]
    try:
        state = json.load(open(path, encoding='utf-8'))
    except Exception as e:
        return [f"校准台账 calibration_state.json 解析失败（{e}）"]
    warns = []
    secs = (D.get('aiPrediction') or {}).get('sectors') or []
    gates = state.get('gates') or {}
    for key, cap_field in (('高', 'maxHighConf'), ('中', 'maxConf')):
        gate = gates.get(key) or {}
        cap = gate.get(cap_field)
        if cap is None:
            continue
        if cap == 0:
            n = len([s for s in secs if isinstance(s, dict) and str(s.get('confidence')) == '高'])
            if n:
                warns.append(f"校准门槛：「{key}」层已禁用（{gate.get('reason')}），但当日仍标 {n} 条「高确信」")
        elif isinstance(cap, int) and cap == 1:
            n = len([s for s in secs if isinstance(s, dict) and str(s.get('confidence')) == '高'])
            if n > 1:
                warns.append(f"校准门槛：当日最多 1 条「高确信」（{gate.get('reason')}），实际 {n} 条")
    return warns


def check_openoutlook_loop(D):
    """R100z13：开盘前瞻兑现闭环的两头闸门（WARN 级）。

    ★2026-10-01 修隐 bug：本函数原先与下面的「六维度深度」同名 check_openoutlook，
    被后定义者整体覆盖成死代码，闭环闸门一直没接线。现改名 check_openoutlook_loop，
    与 check_openoutlook（深度）并存，两个都在 main 里调用。

    用户拍板口径：T 日 08:30 写 openOutlook（含结构化假设 openOutlook.pos），
    T 日 17:00 叙事步打分开进 openOutlook.verification，T+1 08:30 新前瞻整字段覆盖旧的。
    因此只有「pos 与 verification 不同时出现」才算漏项（单看缺失会每天必报假 WARN）。"""
    warns = []
    oo = D.get('openOutlook')
    if not isinstance(oo, dict):
        return warns
    pos, ver = oo.get('pos'), oo.get('verification')
    has_pos = isinstance(pos, dict)
    has_ver = isinstance(ver, dict)
    if has_pos and not has_ver:
        warns.append("openOutlook.pos 已写但 openOutlook.verification 缺失"
                     "（17:00 叙事步未跑 score_openoutlook.py 打分，或没把结果写回：前瞻兑现闭环断了一天）")
    elif has_ver and not has_pos:
        warns.append("openOutlook.verification 存在但 openOutlook.pos 缺失"
                     "（08:30 未写结构化假设 {tendency,bandLo,bandHi,volExpect}，事后没法核对这份前瞻该不该被判命中）")
    elif has_pos and has_ver:
        for k in ('tendency', 'bandLo', 'bandHi'):
            if pos.get(k) in (None, ''):
                warns.append(f"openOutlook.pos.{k} 缺失（结构化前瞻假设不完整，打分口径会漂）")
                break
        for k in ('score', 'verdict'):
            if ver.get(k) in (None, ''):
                warns.append(f"openOutlook.verification.{k} 缺失（17:00 叙事步打分结果没写全）")
                break
    return warns


def check_verification(D):
    """R99j/R100z6m：顶层 aiPrediction.verification 必须是对象（严禁纯字符串），
    且 details 与 sectors 一一对应 → WARN（不阻断部署，但缺失会导致命中闭环失效）。"""
    warns = []
    ap = D.get('aiPrediction') or {}
    v = ap.get('verification')
    if v is None:
        return ["顶层 aiPrediction.verification 缺失（R99j：须为对象 {total,hit,miss,summary,details}，命中验证闭环失效）"]
    if isinstance(v, str):
        return ["顶层 aiPrediction.verification 是字符串，必须是对象（R99j 硬口径）"]
    if not isinstance(v, dict):
        return [f"顶层 aiPrediction.verification 类型异常（{type(v).__name__}），须为对象"]
    if len(v) < 4:
        warns.append(f"aiPrediction.verification 字段不全（当前 {sorted(v.keys())}，缺 total/hit/miss/summary/details 之一）")
    det = v.get('details')
    if isinstance(det, list):
        n_sec = len(ap.get('sectors') or [])
        if n_sec and len(det) != n_sec:
            warns.append(f"aiPrediction.verification.details({len(det)} 条) 与 sectors({n_sec} 个) 不一一对应（R100z6m）")
    else:
        warns.append("aiPrediction.verification.details 缺失或不是数组")
    return warns


def check_openoutlook(D):
    """R98p：顶层 openOutlook 必须存在且六维度齐全（消息面/政策面/外围映射/板块轮动/风险与避险/结论）、
    正文 ≥400 字 → WARN（不阻断部署）。"""
    warns = []
    oo = D.get('openOutlook')
    if not isinstance(oo, dict):
        return ["顶层 openOutlook 缺失或不是对象（开盘前瞻缺失）"]
    content = str(oo.get('content') or '')
    if len(content) < 400:
        warns.append(f"openOutlook.content 仅 {len(content)} 字（硬要求 450-650 字，深度不足）")
    dims = ['消息面', '政策面', '外围映射', '板块轮动', '风险与避险', '结论']
    miss = [d for d in dims if f"【{d}】" not in content]
    if miss:
        warns.append(f"openOutlook 缺维度标题：{'、'.join(miss)}")
    return warns


def check_risk_blacklist(D, scope='all'):
    """R100z13：个股硬风险黑名单（FAIL 硬拦）。

    用户口径：一只票遭大股东减持 / 限售解禁 / ST / 业绩预减，方向说得再对也会被市场锤，
    命中即换股、不进 8 只池。本闸读 stock_risk_blacklist.py --write 产出的
    risk_blacklist.json（源不可达的维度走 unknown，不判安全也不算命中）。

    注意：文件缺失只 WARN（脚本还没跑），只有「黑名单里确实有、且又被选进 8 只池」
    才 FAIL——那才是必须拦下来的选股。

    R100z76-④：severity 分级处置——
      red   → FAIL（硬剔，须换股，不进 8 只池）
      orange→ WARN（警示，可保留但需重点关注，不强制换股）
      yellow→ 跳过（仅作风险参考，如股东质押，不报错）
    旧版 JSON 缺 severity 字段时默认按 red 处理（保持硬闸语义，不静默放行）。

    R100z16：scope 决定扫哪些节点——
      all / ai  → aiPrediction.sectors（17:00、08:30、周日任务职责）
      all / us  → us + ashare 五节与 bullish/bearish（07:00、周日任务职责；
                  这两个任务自己就是这些标的的选股者，必须自己过闸，不能
                  把黑名单推给 17:00 叙事步去背，否则等于没闸）
      all / star→ duanban.star.picks（9:45 职责；star 同样由本任务自己挑，
                  9:45 又禁止池外补股，若不过闸就等于 star 这一支完全没有硬闸）

    ⚠️ R100z41 补记：scope='star' 原本不在任何分支里，check_risk_blacklist 直接
    返回空、main() 的 violations 也恒为空——「--scope star 跑出 ALL OK」其实是
    闸门空转，不是真的校验通过。现已按上述口径补齐。"""
    root = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(root, 'risk_blacklist.json')
    # R100z76-④：早期返回统一为 (fails, warns) 二元组，避免 main() 解包崩溃；
    # 文件缺失/损坏属 WARN（脚本还没跑），不硬拦部署（与 docstring 口径一致）。
    if not os.path.exists(path):
        return [], ["未找到 risk_blacklist.json（stock_risk_blacklist.py --write 未跑，"
                "个股硬风险黑名单没进闸门）"]
    try:
        with open(path, encoding='utf-8') as f:
            bl = json.load(f)
    except Exception as e:
        return [], [f"risk_blacklist.json 解析失败（{e}）"]
    if not isinstance(bl, dict):
        return [], ["risk_blacklist.json 结构异常（须含 blacklist 对象）"]

    bl_map = bl.get('blacklist') or {}

    def scan(stocks, where, hard=True, asof=None):
        """hard=True → 这是**选股池**（要买的东西），red 命中即 FAIL「须换股」。
        hard=False → 这是**要闻引用**（只是提到这只票），red 命中降级 WARN：
        新闻里出现 ST 股是事实，要求「换股」是错的口径 —— 真正要拦的是
        「把 ST 股写成受益/推荐标的」（R100z167：600079/600311 被 ST 命中后
        一直以「须换股，不得留在 8 只池」挂在审计里，而它们只在 intlNews/bearNews
        的 impacts 里，根本不在 8 只池 —— 假 FAIL 会训练人忽略真 FAIL）。
        asof=成稿日（仅 star.picks 用）：命中公告日期晚于成稿日 = 事后新增信息，
        当日精选按 prompt 硬约束**不可改写**，降 WARN 并点名「下一交易日自动剔除」。
        """
        for st in (stocks or []):
            if not isinstance(st, dict):
                continue
            code = str(st.get('code') or '').replace('.', '').replace('sh', '').replace('sz', '').zfill(6)
            hit = bl_map.get(str(code))
            if not hit:
                continue
            # R100z76-④：severity 分级处置——red 硬剔(FAIL)、orange 警示(WARN)、yellow 参考(跳过)。
            # 旧版 JSON 缺 severity 字段时默认 red（保持硬闸语义，不静默放行）。
            sev = hit.get('severity') or 'red'
            reasons = '；'.join(hit.get('reasons') or [])
            nm = hit.get('name') or st.get('name') or ''
            if sev == 'red':
                if hard:
                    late = [ds for ds in re.findall(r"(20\d{2}-\d{2}-\d{2})", reasons)
                            if asof and ds > asof]
                    if late:
                        warns_out.append(
                            f"开盘精选事后新增风险(红)：{code} {nm}（{reasons}）@ {where}"
                            f"——命中公告日期 {max(late)} 晚于精选成稿日 {asof}，属成稿后才披露的信息；"
                            f"当日精选不可改写（prompt 硬约束），下一交易日 09:45 用刷新后的黑名单自动剔除")
                    else:
                        out.append(f"个股硬风险黑名单命中(红/硬剔)：{code} {nm}"
                                   f"（{reasons}）——须换股，不得留在 8 只池（R100z13 硬闸）")
                else:
                    warns_out.append(f"要闻引用黑名单标的(红)：{code} {nm}（{reasons}）@ {where}"
                                     f"——引用事实可以，但不得作为受益/推荐标的；"
                                     f"若在 impacts/池里充当受益股，请换非 ST 的同板块替代")
            elif sev == 'orange':
                warns_out.append(f"个股硬风险黑名单命中(橙/警示)：{code} {nm}"
                                 f"（{reasons}）——可保留但需重点关注，不强制换股")
            else:  # yellow / none / 其它：仅风险参考，不报错
                continue

    out, warns_out = [], []
    if scope in ('all', 'ai'):
        for sec in ((D.get('aiPrediction') or {}).get('sectors') or []):
            if isinstance(sec, dict):
                scan(sec.get('stocks'), 'aiPrediction')
    if scope in ('all', 'us'):
        for mk in ('us', 'ashare'):
            sec = D.get(mk) or {}
            for key in ('bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews', 'bullish', 'bearish'):
                for nw in (sec.get(key) or []):
                    if not isinstance(nw, dict):
                        continue
                    scan(nw.get('stocks'), f"{mk}.{key}", hard=False)
                    for imp in (nw.get('impacts') or []):
                        if isinstance(imp, dict):
                            scan(imp.get('stocks'), f"{mk}.{key}.impacts", hard=False)
    if scope in ('all', 'star'):
        # R100z41：🌟开盘精选的 picks 是本任务自己挑的，不许池外补股，
        # 那就必须自己过闸——否则 9:45 整条链路完全没有硬风险拦截面。
        star = (D.get('duanban') or {}).get('star') or {}
        scan(star.get('picks'), 'duanban.star.picks', asof=str(star.get('date') or '')[:10] or None)
        # R100z167：断板确认池/观察池也要过一遍。它们是 check_duanban 的机械产物，
        # 没法「要求它换股」（会天天误报），但读者会当反包候选看 —— 命中就 WARN 让人看见。
        db = D.get('duanban') or {}
        for key in ('confirmed', 'watching'):
            scan(db.get(key), f'duanban.{key}', hard=False)
    return out, warns_out


def check_logic_depth(D):
    """R100z15 缺口⑤：内容深度没人管 → 机器兜底。

    体检实测（2026-10-01）：aiPrediction.sectors[].logic 只有 32~55 字，
    而 R98p 定的标准是 110-160 字；更关键的是 validate 里**没有任何闸管字数**——
    写多短全靠 AI 自觉，偷懒了照样上线。这里补上。

    R100z162（2026-10-21）：阈值与提示文案统一到现行 G16 口径（logic 200 / main 60）。
    旧版阈值 40/80 与「写满 110-160 字」是 R98p 时代的数字，照着写 160 字反而会被
    G16 拦下——两套口径打架，等于把 AI 往 FAIL 上引导。现与 audit_pipeline 同源。
        logic < 200 字 → FAIL（G16 地板，低于即拦部署）
        logic < 240 字 → WARN（离 200-260 区间上沿还差一截）
        deduce.main < 60 字 → FAIL（G16 地板）
    """
    hards, warns = [], []
    ai = D.get('aiPrediction') or {}
    for s in (ai.get('sectors') or []):
        if not isinstance(s, dict):
            continue
        nm = str(s.get('sector') or '')[:16]
        lg = str(s.get('logic') or '').strip()
        if len(lg) < 200:
            hards.append(f"aiPrediction.{nm}.logic 仅 {len(lg)} 字（<200，G16 地板）——"
                         f"须按 200-260 字写足映射推演（驱动链/受益环节/验证信号）")
        elif len(lg) < 240:
            warns.append(f"aiPrediction.{nm}.logic 仅 {len(lg)} 字（目标 200-260 字）——偏薄")
        dd = s.get('deduce') or {}
        if isinstance(dd, dict):
            mm = str(dd.get('main') or '').strip()
            if len(mm) < 60:
                hards.append(f"aiPrediction.{nm}.deduce.main 仅 {len(mm)} 字（<60，G16 地板）——"
                             f"主推理由未说清")
    return hards, warns


def check_exit(D):
    """R100z15 缺口③：只有买入、没有卖出 → 每只标的必须带退出纪律。

    exit = {stop, target, falsify}：止损位 / 目标位 / 证伪条件，三者缺一不可。
    止损位若在现价之上（比现价还高）属于明显错误，单独点名。"""
    warns = []
    for s in ((D.get('aiPrediction') or {}).get('sectors') or []):
        if not isinstance(s, dict):
            continue
        nm = str(s.get('sector') or '')[:16]
        for st in (s.get('stocks') or []):
            if not isinstance(st, dict):
                continue
            tag = f"{st.get('code')} {st.get('name') or ''}".strip()
            ex = st.get('exit')
            if not isinstance(ex, dict) or not ex:
                warns.append(f"aiPrediction.{nm}.{tag}: 缺 exit（须含 stop / target / falsify 三项）——"
                             f"没有退出纪律的清单只是「想买什么」，不是交易计划")
                continue
            for k in ('stop', 'target', 'falsify'):
                if not (ex.get(k) or ('' if k == 'falsify' else None)):
                    warns.append(f"aiPrediction.{nm}.{tag}.exit.{k} 缺失")
            stop, last = ex.get('stop'), st.get('lastClose')
            if isinstance(stop, (int, float)) and isinstance(last, (int, float)) and stop >= last:
                warns.append(f"aiPrediction.{nm}.{tag}.exit.stop({stop}) ≥ 现价({last})——"
                             f"止损位在现价上方，逻辑荒谬")
    return warns


def check_stock_pool(D):
    """R100z15 缺口②：选股引擎必须进决策链 —— **但只管「断板反包」这一类槽位**。

    ⚠️ 口径铁律（R100z160c，2026-10-21 用户指出，勿回归）：
        8 只 = ①板块龙头 + ②相关概念股 + ③小盘人气股 + ④断板反包（N 只），
        其中**只有 ④ 必须取自机械池**；①②③ 本来就是 AI 按四类配比表选的
        （「游资/机构偏好的板块票」正落在 ③小盘人气：涨停基因好、换手活跃）。
        所以「池里只有 2 只、AI 选了 6 只池外」**不是违规**，是分层表要求的
        （N=2 → 龙头2 概念2 小盘2 断板2）。判据因此只能是：
            池内有 m 只断板候选（m≥1）而断板槽位一只都没取自池内 → FAIL
            m≥2 而只用了 1 只                                  → WARN
            池内 0 只 / 板块名没对上                             → WARN（全自选，属设计内）
        **禁止**再把「8 只必须整组照抄」或「outside≥2 即 FAIL」写回来：
        它与「每板块恰好 8 只」互斥，照抄必踩线（2026-10-21 08:30 实报）。
    """
    root = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(root, 'stock_pool.json')
    if not os.path.exists(path):
        return ([], ["未找到 stock_pool.json（build_stock_pool.py --write 未跑，"
                     "选股引擎没进决策链，AI 手挑的票无人兜底）"])
    try:
        with open(path, encoding='utf-8') as f:
            pool = json.load(f)
    except Exception as e:
        return ([], [f"stock_pool.json 解析失败（{e}）"])

    by = pool.get('bySector') or {}
    if not isinstance(by, dict) or not by:
        return ([], ["stock_pool.json 里 bySector 为空——机械池没产出，等于没接回决策链"])

    # 复用 build_stock_pool 的板块匹配（避免两份别名表各自漂移）
    try:
        sys.path.insert(0, root)
        import build_stock_pool as bsp
        _, sim, aliases_of = bsp.norm, bsp.sim, bsp.SECTOR_ALIAS
    except Exception:
        bsp = None

    def _segs(x):
        """板块名 → 段列表（按 ·、/，切）。用于段级精确匹配。"""
        return [y.strip() for y in re.split(r'[·、/／,，\s]+', str(x or '')) if y.strip()]

    def find_pool(sector):
        if sector in by:
            return sector, by[sector]
        # R100z160：段级精确匹配（字符 Jaccard 的结构性盲区）。
        # 预测板块名常写「大类」而池键是「大类·细分」——'半导体设备' vs '半导体设备·国产替代'
        # 去噪后 Jaccard 只有 0.375 < 0.5，'通信设备' vs '通信设备·光模块' 0.333 < 0.5，
        # 于是两个板块明明有机械候选却被判「无该板块候选」→ 照抄约束对它们整轮失明。
        # 只做**整段相等**（不做子串，避免 '消费电子' 被 '消费·食品加工' 的 '消费' 误牵）。
        ss = _segs(sector)
        if ss:
            for k, v in by.items():
                ks = _segs(k)
                if ks and (ss[0] == ks[0] or ss[0] in ks or ks[0] in ss):
                    return k, v
        if bsp:
            al = aliases_of.get(sector, [])
            best, bs = None, 0.0
            for k in by:
                s = sim(sector, k)
                ali = any((a == k) or (k in a) or (a in k) for a in al)
                if ali:
                    return k, by[k]
                if s > bs:
                    best, bs = k, s
            if bs >= 0.5:
                return best, by[best]
        return None, None

    hards, warns = [], []

    # R100z17 时序闸（2026-10-01 体检发现的结构性缺陷）：
    # 机械池若用「比预测更新的收盘数据」重算，再拿去考当日早上写死的预测，必然零交集——
    # 那不是 AI 自选违规，是检验时序错位。
    # R100z163（2026-10-21）修正判据：原来拿 pool['date'] 比 pred_date，而 date 是
    # **建键所对的预测日**（= aiPrediction.date）。08:30 盘前按当日板块名重键后
    # （build_stock_pool.py --sectors），date 直接等于 pred_date → 时序闸每天误触发，
    # 「④ 断板必须取自池内」这条硬约束整天降级成提示 —— 又一个「规则看着在、其实从不执行」。
    # 改用新增的 candidateDate（候选数据源的交易日 ashare.tradeDate）：
    #   08:30 重键 → candidateDate = 前一交易日 < pred_date → 照抄约束**生效**（这是本意）
    #   17:00（16:00 数据步已更新当日收盘）→ candidateDate = 当日 = pred_date → 降级为提示
    # 旧快照无 candidateDate 时回落到 date，行为与改动前完全一致。
    pred_date = str((D.get('aiPrediction') or {}).get('date') or '')
    pool_date = str(pool.get('date') or '')
    pool_src_date = str(pool.get('candidateDate') or pool_date or '')
    if pred_date and pool_src_date and pool_src_date >= pred_date:
        warns.append(f"stock_pool.json 的候选数据日({pool_src_date}) 不早于 aiPrediction(date={pred_date})："
                     f"机械池是用不早于预测日的收盘数据重算的，08:30 定稿的预测无从照抄，"
                     f"照抄约束降级为提示；下一交易日的 07:00/08:30/周日校验仍按同一规则硬拦（R100z17 时序闸）")
        return [], warns

    # R100z160c：判据改为「断板槽位是否取自池内」，不再拿 8 只整体算池外数。
    # 分层表（TIERS）才是配比的唯一事实源；本函数不重复校验配比，只校验「选择权是否上交」。
    idle, filled = [], 0     # idle=池内无该板块；filled=真·有机械候选的板块数

    def _code(x):
        return str(x or '').replace('.SH', '').replace('.SZ', '').zfill(6)

    for s in ((D.get('aiPrediction') or {}).get('sectors') or []):
        if not isinstance(s, dict):
            continue
        sector = str(s.get('sector') or '')
        if not sector:
            continue
        key, lst = find_pool(sector)
        via_global = False
        stocks = [st for st in (s.get('stocks') or []) if isinstance(st, dict)]
        chosen = {_code(st.get('code')) for st in stocks if st.get('code')}
        # 断板槽位＝note 以「断板反包」开头的那几只（与 check_tiered 同一套 cat_of 识别）
        db_chosen = {_code(st.get('code')) for st in stocks
                     if st.get('code') and cat_of(st.get('note')) == 'D'}
        if lst is None:
            # R100z167（2026-10-21 用户实报「怎么那么多遗留问题」）：
            # 旧逻辑在这里 WARN 一句「板块名未对齐，属设计内」就 continue —— 等于
            # 「④ 断板必须取自池内」对这批板块**整轮失明**，而页面看上去一切正常。
            # 池键是 AI 自由文本板块名、与同花顺行业名天然会漂移，靠人每天手动重键不是修法。
            # 兜底改为「全池断板候选」：范围比单板块大，但**绝不会把 AI 凭空捏的票当成池内票**，
            # 硬约束因此永不失明；键对上时仍走上面的板块级精确核对。
            gl = [it for v in by.values() for it in (v or [])
                  if str(it.get('src') or '').startswith('断板')]
            if not gl:
                idle.append(sector)
                warns.append(f"aiPrediction.{sector}: 机械池内无同名板块（板块名未对齐），"
                             f"且全池无任何断板候选——断板槽位按四类配比表自选，须在汇报点名")
                continue
            lst = gl
            via_global = True
            warns.append(f"aiPrediction.{sector}: 板块名与池键未对齐（池按上一交易日板块建键）"
                         f"——已改用**全池 {len(gl)} 只断板候选**核对，「④ 断板必须取自池内」照常生效；"
                         f"08:30 定完板块名后按当日名重键可恢复板块级精确核对"
                         f"（build_stock_pool.py --write --sectors \"…\"）")
        tag = '（池键未对齐，按全池断板候选核对）' if via_global else ''
        cand = {_code(x.get('code')) for x in lst}
        cand.discard('000000')
        if not cand:
            idle.append(sector)
            warns.append(f"aiPrediction.{sector}: 机械池该板块存在但候选为 0 只——"
                         f"断板槽位按四类配比表自选（N=0 → 龙头2 概念2 小盘4），须在汇报点名")
            continue
        filled += 1
        if not chosen:
            continue

        # 池内「断板反包」类候选（其余 src=谐波/吸筹/强势/补位 可填任何槽位，不作硬约束）
        db_pool = {_code(x.get('code')) for x in lst
                   if str(x.get('src') or '').startswith('断板')}
        db_pool.discard('000000')
        used_db = db_chosen & cand          # 断板槽位里取自池内的
        used_all = chosen & cand            # 任意槽位里取自池内的（谐波/吸筹/强势被用上也算）
        # R100z165（2026-10-21 全链路体检·L4 未撞型）：原来 need_db = min(len(db_pool), 5)，
        #   拿「池内候选数」当应使用数。只要池里断板候选多于今日断板槽位数就必然误报：
        #   实测造 3 只池内候选、AI 断板槽位只有 1 个（TIERS[1]=(2,2,3) 合规），
        #   却报「断板候选 3 只，只用了 1 只」——槽位只有 1 个，物理上不可能用 3 只。
        #   槽位数 = len(db_chosen)（note 以「断板反包」开头的只数），应使用数不能超过它。
        #   池内 3/槽位 1/用 1 → 不报；池内 2/槽位 2/用 1 → 才报（那才是真的漏用）。
        need_db = min(len(db_pool), len(db_chosen)) or 0

        if db_pool and not used_db:
            hards.append(f"aiPrediction.{sector}: 机械池{tag}有 {len(db_pool)} 只断板反包达标候选"
                         f"（{'、'.join(sorted(db_pool)[:5])}），断板槽位却 {len(db_chosen)} 只全部自选"
                         f"——断板这一类必须取自池内，AI 只保留解释权、交出选择权（R100z15）")
        elif len(db_pool) >= 2 and len(used_db) < need_db:
            warns.append(f"aiPrediction.{sector}: 机械池{tag}断板候选 {len(db_pool)} 只，"
                         f"断板槽位只用了 {len(used_db)} 只"
                         f"（{'、'.join(sorted(db_pool - used_db)[:4])} 未用）——"
                         f"须在 note 说明取舍理由（黑名单换股可接受）")
        elif used_all:
            # 正常：池里有货也用了；池外的那几只按四类配比表补（不是「补位」，别再说成违规）
            extra = len(chosen) - len(used_all)
            if extra > 0:
                warns.append(f"aiPrediction.{sector}: 池内用 {len(used_all)} 只 + "
                             f"四类配比自选 {extra} 只（机械池共 {len(cand)} 只）——"
                             f"属设计内，须在汇报点名「该板块机械池仅 {len(cand)} 只」")

    if idle and filled == 0:
        warns.append(f"⚠️ 机械池本轮对该批板块整体无候选：{len(idle)} 个板块（{'、'.join(idle)}）"
                     f"在 stock_pool.json(建键日={pool_date}/候选数据日={pool_src_date}) 里找不到同名板块——"
                     f"断板槽位整轮只能按四类配比表自选（①②③ 本就自选，受影响的是 ④ 断板一类）；"
                     f"若只是板块名对不上（池按上一交易日板块建键），**按当日板块名重键即可**："
                     f"build_stock_pool.py --write --sectors \"{'，'.join(idle[:6])}\"（R100z163）；"
                     f"若重键后仍为 0 只，才是候选真没货（须在汇报逐条点名）")
    return hards, warns


def check_source_names(D):
    """R91h/R91j 来源命名软闸门：全文只允许「彭博社」「路透社」「华尔街日报」，
    出现 彭博新闻社/路透通讯社/WSJ 或简称「彭博」「路透」→ WARN（不阻断部署）。"""
    forb = re.compile(r'彭博新闻社|路透通讯社|WSJ')
    short = re.compile(r'彭博(?!社)|路透(?!社)')
    hits = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            if forb.search(o):
                hits.append('旧全称/WSJ: ' + o[:44])
            elif short.search(o):
                hits.append('简称: ' + o[:44])
    walk(D)
    out = [f"来源命名违规（只允许 彭博社/路透社/华尔街日报）：{h}" for h in hits[:8]]
    if len(hits) > 8:
        out.append(f"……另有 {len(hits) - 8} 处同类违规")
    return out


def check_news_sources(D):
    """R100z53：新闻来源白名单机器闸（软拦——出 WARN 不阻断部署，但要改）。

    覆盖 ashare / us 五节要闻 + weekendNews 全节的 `source` 字段。
    白名单内容见 news_sources.py（含本次扩入的 证监会 / 沪深北三大交易所 / 中证登 /
    财联社 / 华尔街见闻 / 证券时报 / 中国证券报 / 第一财经 等部委与官方机构）。
    """
    out = []

    def one(mk, key, it):
        if not isinstance(it, dict):
            return
        reason, hint = news_sources.check_source(it.get('source'))
        if reason:
            title = str(it.get('title') or it.get('sector') or '')[:16]
            out.append(f"{mk}.{key} 来源不合规[{reason}]《{title}》→ {hint}")

    for mk in ('ashare', 'us'):
        sec = D.get(mk) or {}
        for key in ('bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews'):
            for it in (sec.get(key) or []):
                one(f"{mk}.{key}", key, it)
    wk = D.get('weekendNews') or {}
    if isinstance(wk, dict):
        for key, items in wk.items():
            if isinstance(items, list):
                for it in items:
                    one(f"weekendNews.{key}", key, it)
    return out[:10]


def check_lianban_notes(D):
    """R91j 连板标注软闸门：标的 note 中的「N连板」必须与东财涨停池真实连板一致
    （离线口径：ashare.lianban 模块 lbc），不符/虚构 → WARN（不阻断部署）。"""
    lb = (D.get('ashare') or {}).get('lianban') or []
    lbc = {str(s.get('code') or ''): int(s.get('lbc') or 0)
           for s in lb if isinstance(s, dict)}
    warns = []

    def walk(o):
        if isinstance(o, dict):
            note = o.get('note')
            code = str(o.get('code') or '')
            if isinstance(note, str) and code and '连板' in note:
                m = re.search(r'(\d+)连板', note)
                if m:
                    n, real = int(m.group(1)), lbc.get(code, 0)
                    if real < n:
                        warns.append(
                            f"{code} {(o.get('name') or '')} note「{note}」"
                            f"与涨停池真实连板不符（连板梯队 lbc={real}）——连板数只能取自东财涨停池")
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(D)
    return warns


def check_story_quality(D):
    """R91j 用户反馈软闸门：断板反包 story 文本质量。
    ① 严禁出现「（形态：…）」形态描述——形态已有弹窗「形态」独立字段展示，
       story 只写消息面（利好/利空依据），走势描述不构成利好也不入 story；
    ② 严禁出现省略号「…」截断——story 须完整（可依据 bullRefs/bearRefs 全文重建）。"""
    db = D.get('duanban')
    if not isinstance(db, dict):
        return []
    warns = []
    for pool in ('confirmed', 'watching'):
        for e in db.get(pool) or []:
            if not isinstance(e, dict):
                continue
            st = e.get('story') or ''
            code, name = e.get('code'), e.get('name')
            if '（形态' in st or '仅作背景' in st:
                warns.append(f"{pool}.{code} {name} story 含「（形态：…）」形态描述——形态已在独立字段展示，story 只写消息面")
            if '…' in st:
                warns.append(f"{pool}.{code} {name} story 含省略号截断——请依据 bullRefs/bearRefs 全文重建完整 story")
    return warns


def check_star_module(D):
    """R98k 软闸门：① 双池上涨概率全池拉平（如全为 50%）→ 疑似未校准 WARN；
    ② duanban.star（9:45 开盘精选）picks 数量不限（≥1，R98l）；
       picks 允许三类：a) 双池内标的（src='pool'）；b) src='board' 的板块动量标的
       （早盘真实强势板块领涨股，R98n，允许池外）；c) src='pw'/'hz'/'acc' 的技术池标的
       （谐波/九门/吸筹，R100m 前一日 16:00 数据步产物，允许池外）。
       纯池外且无合法 src/无板块依据的标的 → WARN。"""
    db = D.get('duanban')
    if not isinstance(db, dict):
        return []
    warns = []
    entries = [e for pool in ('confirmed', 'watching') for e in (db.get(pool) or [])
               if isinstance(e, dict)]
    probs = [e.get('probability') for e in entries if isinstance(e.get('probability'), (int, float))]
    if len(probs) >= 8 and len(set(probs)) == 1:
        warns.append(f"断板反包上涨概率全部为 {probs[0]}%——疑似未校准拉平（R98k：脚本基线按情绪分布，AI 校准严禁全池统一）")
    star = db.get('star')
    if star is not None:
        if not isinstance(star, dict):
            warns.append("duanban.star 非对象（应为 {date,time,marketLine,sentiment,picks,pushText,deskCheck}）")
        else:
            pool_codes = {str(e.get('code')) for e in entries}
            picks = star.get('picks') or []
            if not isinstance(picks, list) or len(picks) < 1:
                warns.append("duanban.star.picks 缺失或为空（至少 1 只，数量不限——R98l）")
            for p in picks:
                if not isinstance(p, dict):
                    continue
                c = str(p.get('code') or '')
                if c and c not in pool_codes:
                    src = p.get('src')
                    name_ok = bool(str(p.get('name') or '').strip())
                    # R98n：允许 src='board' 的板块动量标的（早盘真实强势板块领涨股，需有板块依据）
                    if src == 'board' and name_ok and str(p.get('sector') or '').strip():
                        continue
                    # R100m：允许 src='pw'/'hz'/'acc' 的技术池标的（谐波/九门/吸筹，前一日 16:00 数据步产物）
                    if src in ('pw', 'hz', 'acc') and name_ok:
                        continue
                    warns.append(f"duanban.star.picks {c} {p.get('name')} 不在断板反包双池内且非合规来源标的（精选只能从双池/板块动量/技术池筛）")
            if not str(star.get('pushText') or '').strip():
                warns.append("duanban.star.pushText 为空（微信推送深度分析缺失，10:00 自动化 AI 须补写）")
            # R100z185：交易预案对账（09:45 步骤 1.5 由 desk_verify.py 写入）。
            # 缺失合法（没跑或非交易日），但**存在就必须结构完整**——页面直接按 state 上色，
            # 状态值写出界会静默渲染成灰块，比不显示更坏。
            dc = star.get('deskCheck')
            if dc is not None:
                if not isinstance(dc, dict):
                    warns.append("duanban.star.deskCheck 非对象（应为 desk_verify.py 写的 payload）")
                else:
                    if not str(dc.get('planDate') or '').strip():
                        warns.append("duanban.star.deskCheck.planDate 为空——页面标题会显示「预案 → 开盘」但看不出是哪天的预案")
                    ok_states = {'buy', 'wait', 'chase', 'drop', 'limit', 'unknown'}
                    vs = dc.get('verdicts')
                    if not isinstance(vs, list) or not vs:
                        warns.append("duanban.star.deskCheck.verdicts 缺失或为空——对账块会整块不渲染")
                    else:
                        bad_state = [f"{v.get('code')}→{v.get('state')}" for v in vs
                                     if isinstance(v, dict) and v.get('state') not in ok_states]
                        if bad_state:
                            warns.append("duanban.star.deskCheck.verdicts 出现非法 state %s（合法值 %s）"
                                         % ('、'.join(bad_state[:4]), '/'.join(sorted(ok_states))))
    return warns


def check_top_boards(D):
    """R91j/m：duanban.topBoards（近3日板块TOP10，申万行业口径）缺失/非数组/字段缺失 → WARN。"""
    db = D.get('duanban')
    if not isinstance(db, dict):
        return []
    tb = db.get('topBoards')
    if not isinstance(tb, list):
        return ["duanban.topBoards 缺失或非数组——近3日板块TOP10 模块将不显示，请由 check_duanban.py --module 生成"]
    warns = []
    for i, b in enumerate(tb):
        if not isinstance(b, dict):
            warns.append(f"topBoards[{i}] 非对象")
            continue
        for k in ('name', 'pct3', 'pctToday'):
            if b.get(k) is None:
                warns.append(f"topBoards[{i}]（{b.get('name') or '?'}）缺 {k}")
    return warns


def reorder_inplace(D):
    """无破坏性地把所有标的清单重排为 龙头→概念→小盘人气→断板反包（同类内保序）。
    仅调顺序，不改数量与内容；幂等。"""
    ORDER = {'L': 0, 'C': 1, 'X': 2, 'D': 3}

    def r(stocks):
        if not isinstance(stocks, list):
            return stocks
        return sorted(stocks, key=lambda s: ORDER.get(cat_of((s or {}).get('note', '')), 9))

    for mk in ('ashare', 'us'):
        sec = D.get(mk) or {}
        for key in ('bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews'):
            for nw in (sec.get(key) or []):
                if isinstance(nw, dict):
                    for imp in (nw.get('impacts') or []):
                        if isinstance(imp, dict) and isinstance(imp.get('stocks'), list):
                            imp['stocks'] = r(imp['stocks'])
        for key in ('bullish', 'bearish'):
            for g in (sec.get(key) or []):
                if isinstance(g, dict) and isinstance(g.get('stocks'), list):
                    g['stocks'] = r(g['stocks'])
    ai = D.get('aiPrediction') or {}
    for s in (ai.get('sectors') or []):
        if isinstance(s, dict) and isinstance(s.get('stocks'), list):
            s['stocks'] = r(s['stocks'])


def fix_duanban_inplace(D):
    """duanban 断板反包模块软闸门（R87 / R88 打标制）：非主板标的自动剔除、
    sentiment 非法补 neutral、probability 补 50，缺 bullRefs/kline 告警。
    模块整体缺失不处理（属 16:00 数据步职责）。返回 (改动?, 告警列表)。"""
    db = D.get('duanban')
    if not isinstance(db, dict):
        return False, []
    warns, changed = [], False
    for pool in ('confirmed', 'watching'):
        kept = []
        for e in (db.get(pool) or []):
            if not isinstance(e, dict):
                continue
            code = str(e.get('code') or '')
            if board_bad(code):
                warns.append(f"duanban.{pool}: 非沪深主板标的 {code} {(e.get('name') or '')} → 自动剔除")
                changed = True
                continue
            sent = str(e.get('sentiment') or '')
            if sent not in ('bull', 'bear', 'neutral'):
                e['sentiment'] = 'neutral'
                warns.append(f"duanban.{pool}: {code} sentiment 缺失/非法（{sent!r}）→ 自动补 neutral")
                changed = True
            p = e.get('probability')
            if not isinstance(p, (int, float)) or not (0 <= p <= 100):
                e['probability'] = 50
                warns.append(f"duanban.{pool}: {code} probability 缺失/非法（{p!r}）→ 自动补 50")
                changed = True
            if e.get('sentiment') == 'bull' and not e.get('bullRefs'):
                warns.append(f"duanban.{pool}: {code} {(e.get('name') or '')} 口径利好但无 bullRefs（请复核）")
            if e.get('sentiment') == 'bear' and not e.get('bearRefs'):
                warns.append(f"duanban.{pool}: {code} {(e.get('name') or '')} 口径利空但无 bearRefs（请复核）")
            if not isinstance(e.get('story'), str):
                warns.append(f"duanban.{pool}: {code} story 缺失/非法（应为小作文文本，请复核）")
            if not e.get('kline'):
                warns.append(f"duanban.{pool}: {code} 无 kline（前端无K线图可画）")
            kept.append(e)
        if db.get(pool) != kept:
            db[pool] = kept
            changed = True
    return changed, warns


def check_selection_modules(D):
    """2026-09-26 选股扩展模块（谐波/吸筹/量化/必带数据）结构校验。

    软闸门：仅 WARN 不阻断部署（与质量类一致）。这些模块由新脚本在 16:00 数据步流水线生成，
    缺失时不报（流水线未跑不代表坏）；存在时校验关键字段与主板约束。
    """
    warns = []
    MAIN = re.compile(r'^(60|00)\d{4}$')

    h = D.get('harmonic')
    if h is not None:
        if not isinstance(h, dict):
            warns.append("harmonic 应为对象")
        else:
            for pool in ('confirmPool', 'watchPool'):
                arr = h.get(pool)
                if arr is None:
                    continue
                if not isinstance(arr, list):
                    warns.append(f"harmonic.{pool} 非数组"); continue
                for i, it in enumerate(arr):
                    if not isinstance(it, dict):
                        warns.append(f"harmonic.{pool}[{i}] 非对象"); continue
                    c = str(it.get('code') or '')
                    if not MAIN.match(c):
                        warns.append(f"harmonic.{pool}[{i}] code {c} 非 60/00 沪深主板")
                    for fld in ('pattern', 'stage', 'prz', 'stop', 'target1', 'target2', 'points', 'ratios'):
                        if fld not in it:
                            warns.append(f"harmonic.{pool}[{i}] 缺字段 {fld}")

    a = D.get('accumulation')
    if a is not None:
        if not isinstance(a, dict):
            warns.append("accumulation 应为对象")
        else:
            for i, it in enumerate(a.get('scored') or []):
                if not isinstance(it, dict):
                    warns.append(f"accumulation.scored[{i}] 非对象"); continue
                c = str(it.get('code') or '')
                if not MAIN.match(c):
                    warns.append(f"accumulation.scored[{i}] code {c} 非 60/00 沪深主板")
                if 'score' not in it or 'grade' not in it:
                    warns.append(f"accumulation.scored[{i}] 缺 score/grade")

    p = D.get('powerScreen')
    if p is not None:
        if not isinstance(p, dict):
            warns.append("powerScreen 应为对象")
        else:
            for i, it in enumerate(p.get('passed') or []):
                if not isinstance(it, dict):
                    warns.append(f"powerScreen.passed[{i}] 非对象"); continue
                c = str(it.get('code') or '')
                if not MAIN.match(c):
                    warns.append(f"powerScreen.passed[{i}] code {c} 非 60/00 沪深主板")
                if 'score' not in it:
                    warns.append(f"powerScreen.passed[{i}] 缺 score")

    # R100h：macroChecklist 校验已随必带数据清单模块下线删除
    return warns


def main():
    as_json = '--json' in sys.argv
    # R100z16 --no-fix：纯只读校验，不自动补 direction / 不剔 duanban / 不重排 / 不写盘。
    # 9:45 唯一写入口是 duanban.star，跑默认模式反会被 validate 改掉 duanban 池内
    # probability 与池成员、重排 aiPrediction 标的，等于从后门绕过任务自己的禁令。
    no_fix = '--no-fix' in sys.argv
    # R100z16 --scope：让每个自动化只对自己写的节点负责，拆掉
    # 「FAIL 全在 aiPrediction 却要 9:45 / 07:00 修到 ALL OK」的死锁——
    # 那两个任务被绝对禁止改 aiPrediction，等于被要求修自己碰不了的东西。
    scope = 'all'
    for _i, _a in enumerate(sys.argv):
        if _a == '--scope' and _i + 1 < len(sys.argv):
            scope = sys.argv[_i + 1]
        elif _a.startswith('--scope='):
            scope = _a.split('=', 1)[1]
    if scope not in ('all', 'ai', 'us', 'star'):
        print(f"[ERROR] --scope 非法：{scope}（可选 all / ai / us / star）")
        sys.exit(2)
    D = load_data()

    # 先自动补救 direction（缺失/非法 → 中性）、duanban 软闸门（剔除非主板/补概率），
    # 再无破坏性地按统一顺序重排（龙头→概念→小盘人气→断板反包）；均写回但不阻断部署。
    dir_warns = fix_direction_inplace(D) if not no_fix else []
    duanban_changed, duanban_warns = (fix_duanban_inplace(D) if not no_fix else (False, []))
    if (not no_fix) and ('--no-fix-order' not in sys.argv or duanban_changed):
        reorder_inplace(D)
        # R100z165（L10 写盘原子化）：先 .tmp 再 os.replace
        _tmp = 'data.js.tmp'
        with open(_tmp, 'w', encoding='utf-8') as f:
            f.write('window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';\n')
        os.replace(_tmp, 'data.js')

    violations = []
    imp_cnt_warns = check_impact_count(D)
    quality_warns = check_news_quality(D)
    fresh_warns = check_freshness(D)
    horizon_warns = check_horizon(D)
    # R100z10 新增闸门：deduce 结构硬校验 / verification 闭环软校验 / openOutlook 六维度软校验
    # R100z152g：check_deduce 内部按 scope 过滤白名单（ai 组/ us 组），这里如实传入即可
    deduce_hards, deduce_warns = check_deduce(D, scope=scope)
    # R100z11：预期差结构化字段 / 标的层面验证 / 置信度校准门槛
    exp_hards, exp_warns = check_exp(D)
    veri_warns = check_verification(D)
    stock_warns = check_stockcheck(D)
    calib_warns = check_calibration(D)
    src_warns = check_source_names(D)
    nsrc_warns = check_news_sources(D)   # R100z53：新闻来源白名单机器闸
    lb_warns = check_lianban_notes(D)
    tb_warns = check_top_boards(D)
    # R100z81：碎片化 / 深度 / 英文泄漏 机器闸（按 scope 决定谁负责：新闻+outlook+reason 归 us 职责，
    # verification.note 泄漏归 ai 职责——与 17:00 叙事步「先 --scope ai 再 --scope us」两次校验口径一致）
    imp_merge_warns = check_impact_merge(D) if scope in ('all', 'us') else []
    outlook_warns = check_outlook_depth(D) if scope in ('all', 'us') else []
    reason_warns = check_reason_depth(D) if scope in ('all', 'us') else []
    note_leak_warns = check_note_leak(D) if scope in ('all', 'ai') else []
    st_warns = check_story_quality(D)
    star_warns = check_star_module(D)
    sel_warns = check_selection_modules(D)
    oo_warns = check_openoutlook(D)                    # R100z13：开盘前瞻六维度深度
    ooloop_warns = check_openoutlook_loop(D)           # R100z13：前瞻兑现闭环两头闸
    risk_fails, risk_warns = check_risk_blacklist(D, scope=scope)   # R100z76-④：个股硬风险黑名单（red=FAIL / orange=WARN / yellow=跳过）
    logic_hards, logic_warns = check_logic_depth(D)    # R100z15：内容深度机器闸
    exit_warns = check_exit(D)                         # R100z15：退出纪律（止损/目标/证伪）
    pool_hards, pool_warns = check_stock_pool(D)       # R100z15：机械选股池是否真进决策链
    # R100z16：只把「本职责范围内」的 FAIL 计入 violations。
    # R100z152g：deduce 白名单已含 us 组（要闻/理由/资金/美股），故 us scope 也须计入——
    # hards 已在 check_deduce 内按 scope 过滤，此处只放行、不重复过滤。
    if scope in ('all', 'ai', 'us'):
        violations.extend(deduce_hards)
        violations.extend(exp_hards)
        violations.extend(logic_hards)
        violations.extend(pool_hards)
    if scope in ('all', 'ai', 'us', 'star'):  # R100z41：star 也要计（此前空转，闸门形同虚设）
        violations.extend(risk_fails)

    if scope in ('all', 'us'):
        for mk in ('ashare', 'us'):
            sec = D.get(mk) or {}
            for key in ('bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews'):
                for i, nw in enumerate(sec.get(key) or []):
                    if not isinstance(nw, dict):
                        continue
                    # 五节要闻的 8 只口径是**整条新闻扁平合计8 只**（N 分层表本身就是
                    # 按 8 只的总数定义的，如 N≥5 → 1/1/1/5），不是「每个 impacts 分组各8 只」。
                    # 此前这里按分组逐个调check_tiered，导致每组只有 1~5 只被硬判FAIL——
                    # 与 check_impact_count / 前端渲染的扁平口径自相矛盾，且会让 --scope us 永久红。
                    # 现改为：①逐组校验「非空 + 主板过滤」；②整条新闻扁平合计走 check_tiered 全套。
                    for j, imp in enumerate(nw.get('impacts') or []):
                        if not isinstance(imp, dict):
                            continue
                        g = imp.get('stocks')
                        gname = f"{mk}.{key}[{i}].impacts[{j}]({(imp.get('theme') or '')[:14]})"
                        if not isinstance(g, list) or not g:
                            violations.append(f"{gname}: stocks 缺失或为空")
                            continue
                        for s in g:
                            if isinstance(s, dict) and board_bad(s.get('code')):
                                violations.append(
                                    f"{gname}: 含非沪深主板标的 {s.get('code')} {s.get('name', '')}")
                    flat = [s for imp in (nw.get('impacts') or []) if isinstance(imp, dict)
                            for s in (imp.get('stocks') or []) if isinstance(s, dict)]
                    # 扁平口径下同一 code 可跨组复用（上限 2 次，见 check_news_quality 的 C 口径），
                    # 故先按 code 去重再做「恰好 8 只互异」判定，避免与设计意图冲突。
                    seen = {}
                    for s in flat:
                        seen.setdefault(str(s.get('code')), s)
                    uniq = list(seen.values())
                    if len(uniq) != len(flat):
                        # 有跨组复用时按去重口径校验，并单独提示复用情况（不判FAIL）
                        check_tiered(uniq, f"{mk}.{key}[{i}]({(nw.get('sector') or '')[:14]})", violations)
                    else:
                        check_tiered(flat, f"{mk}.{key}[{i}]({(nw.get('sector') or '')[:14]})", violations)
            for key in ('bullish', 'bearish'):
                for i, g in enumerate(sec.get(key) or []):
                    if isinstance(g, dict):
                        check4(g.get('stocks'), f"{mk}.{key}[{i}]({(g.get('theme') or '')[:14]})", violations)

    if scope in ('all', 'ai'):
        ai = D.get('aiPrediction') or {}
        for i, s in enumerate(ai.get('sectors') or []):
            if isinstance(s, dict):
                check_tiered(s.get('stocks'), f"aiPrediction.{(s.get('sector') or '')[:16]}", violations)

    if as_json:
        print(json.dumps({'ok': not violations, 'direction_warnings': dir_warns,
                          'impact_count_warnings': imp_cnt_warns,
                          'quality_warnings': quality_warns,
                          'freshness_warnings': fresh_warns,
                          'horizon_warnings': horizon_warns,
                          'source_name_warnings': src_warns,
                          'lianban_warnings': lb_warns,
                          'top_boards_warnings': tb_warns,
                          'story_warnings': st_warns,
                          'star_warnings': star_warns,
                          'deduce_warnings': deduce_warns,
                          'verification_warnings': veri_warns,
                          'exp_warnings': exp_warns,
                          'stockcheck_warnings': stock_warns,
                          'calibration_warnings': calib_warns,
                          'openoutlook_warnings': oo_warns,
                          'openoutlook_loop_warnings': ooloop_warns,
                          'risk_blacklist_violations': risk_fails,
                          'risk_blacklist_warnings': risk_warns,
                          'logic_depth_warnings': logic_warns,
                          'impact_merge_warnings': imp_merge_warns,
                          'outlook_depth_warnings': outlook_warns,
                          'reason_depth_warnings': reason_warns,
                          'note_leak_warnings': note_leak_warns,
                          'exit_warnings': exit_warns,
                          'stock_pool_warnings': pool_warns,
                          'duanban_warnings': duanban_warns,
                          'violations': violations}, ensure_ascii=False, indent=2))
    for w in dir_warns:
        print("[WARN] direction 自动补救:", w)
    for w in imp_cnt_warns:
        print("[WARN] impacts 分组数:", w)
    for w in quality_warns:
        print("[WARN] 要闻质量:", w)
    for w in fresh_warns:
        print("[WARN] 时间戳:", w)
    for w in horizon_warns:
        print("[WARN] horizon:", w)
    for w in duanban_warns:
        print("[WARN] duanban:", w)
    for w in src_warns:
        print("[WARN] 来源命名:", w)
    for w in nsrc_warns:
        print("[WARN] 新闻来源白名单:", w)
    for w in lb_warns:
        print("[WARN] 连板标注:", w)
    for w in tb_warns:
        print("[WARN] 板块TOP10:", w)
    for w in st_warns:
        print("[WARN] story质量:", w)
    for w in star_warns:
        print("[WARN] 🌟开盘精选:", w)
    for w in deduce_warns:
        print("[WARN] deduce:", w)
    for w in veri_warns:
        print("[WARN] verification:", w)
    for w in exp_warns:
        print("[WARN] exp:", w)
    for w in stock_warns:
        print("[WARN] stockCheck:", w)
    for w in calib_warns:
        print("[WARN] calibration:", w)
    for w in oo_warns:
        print("[WARN] openOutlook:", w)
    for w in ooloop_warns:
        print("[WARN] openOutlook兑现闭环:", w)
    for w in sel_warns:
        print("[WARN] 选股扩展模块:", w)
    for w in risk_warns:
        print("[WARN] 硬风险黑名单(警示):", w)
    for w in logic_warns:
        print("[WARN] 内容深度:", w)
    for w in imp_merge_warns:
        print("[WARN] impacts 碎片化:", w)
    for w in outlook_warns:
        print("[WARN] 大盘研判深度/英文:", w)
    for w in reason_warns:
        print("[WARN] 行业原因/题材逻辑深度:", w)
    for w in note_leak_warns:
        print("[WARN] 验证note英文泄漏:", w)
    for w in exit_warns:
        print("[WARN] 退出纪律:", w)
    for w in pool_warns:
        print("[WARN] 机械选股池:", w)
    if violations:
        print(f"[FAIL] 共 {len(violations)} 处违规：")
        for v in violations:
            print("  -", v)
        sys.exit(1)
    print("ALL OK: 全部 impacts/aiPrediction 恰好 8 只且配比符合 N 分层表、"
          "顺序为龙头→概念→小盘人气→断板反包，bullish/bearish 恰好 4 只，全部标的为沪深主板；"
          "macroNews/intlNews/bankViews 均含合法 direction（看涨/看跌/中性）。")


if __name__ == '__main__':
    main()

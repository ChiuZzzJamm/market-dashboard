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
   kline 输出 WARNING。模块整体缺失不检查（16:00 自动化职责）。

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
import subprocess
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# R100z53：新闻来源白名单从「只写在五条自动化 prompt 正文里」下沉成机器闸门，
# 判定口径单一来源 news_sources.py（prompt 只指路，不再各写一份免得口径漂移）。
import news_sources

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
    node = subprocess.run(
        ['/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node', '-e',
         "const fs=require('fs');let s=fs.readFileSync('data.js','utf8');"
         "let m=s.match(/window\\s*\\.\\s*DASHBOARD_DATA\\s*=\\s*(\\{[\\s\\S]*\\});?\\s*$/);"
         "process.stdout.write(JSON.stringify(eval('('+m[1]+')')))"],
        capture_output=True, text=True)
    return json.loads(node.stdout)


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
            + ("| 含无法识别类别note" + dict(cnt)['?'] if cnt['?'] else ''))
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


def check_deduce(D):
    """R99m/R99o/R99p：aiPrediction 每板块必须内嵌 deduce 三候选推演（前端推演段依赖）。
    结构性缺失（deduce 非对象 / cands 数≠2 / conf 非枚举 / counter 缺失或为空 / 自带「主推：」「反证：」前缀）
    → 硬违规（FAIL，阻断部署）；cands 文本过短等质量项 → WARN。"""
    hards, warns = [], []
    ap = D.get('aiPrediction') or {}
    sectors = ap.get('sectors') or []
    for i, s in enumerate(sectors):
        if not isinstance(s, dict):
            continue
        tag = f"aiPrediction.sectors[{i}]({str(s.get('sector'))[:14]})"
        d = s.get('deduce')
        if not isinstance(d, dict):
            hards.append(f"{tag}: 缺 deduce 三候选推演对象（R99m 内嵌硬约束，前端推演段将空白）")
            continue
        cands = d.get('cands')
        if not isinstance(cands, list) or len(cands) != 2:
            hards.append(f"{tag}: deduce.cands 必须恰好 2 个候选（当前 {0 if cands is None else len(cands)} 个）")
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
                for bad in ('主推：', '反证：'):
                    if v.startswith(bad):
                        hards.append(f"{tag}: deduce.{key} 自带「{bad}」前缀（R99o：前端会重复渲染成「{bad}{bad}」）")
                if len(v.strip()) < 6:
                    warns.append(f"{tag}: deduce.{key} 过短（{len(v.strip())} 字），推演信息量不足")
    if not sectors:
        warns.append("aiPrediction.sectors 为空，deduce 校验跳过")
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
    # R100z12 闸门口径修正：verification 由 16:00 填写，08:30（盘前）与周日（date=下周一）
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
    T 日 16:00 打分开进 openOutlook.verification，T+1 08:30 新前瞻整字段覆盖旧的。
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
                     "（16:00 未跑 score_openoutlook.py 打分，或没把结果写回：前瞻兑现闭环断了一天）")
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
                warns.append(f"openOutlook.verification.{k} 缺失（16:00 打分结果没写全）")
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

    R100z16：scope 决定扫哪些节点——
      all / ai  → aiPrediction.sectors（16:00、08:30、周日任务职责）
      all / us  → us + ashare 五节与 bullish/bearish（07:30、周日任务职责；
                  这两个任务自己就是这些标的的选股者，必须自己过闸，不能
                  把黑名单推给 16:00 去背，否则等于没闸）
      all / star→ duanban.star.picks（9:45 职责；star 同样由本任务自己挑，
                  9:45 又禁止池外补股，若不过闸就等于 star 这一支完全没有硬闸）

    ⚠️ R100z41 补记：scope='star' 原本不在任何分支里，check_risk_blacklist 直接
    返回空、main() 的 violations 也恒为空——「--scope star 跑出 ALL OK」其实是
    闸门空转，不是真的校验通过。现已按上述口径补齐。"""
    root = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(root, 'risk_blacklist.json')
    if not os.path.exists(path):
        return ["未找到 risk_blacklist.json（stock_risk_blacklist.py --write 未跑，"
                "个股硬风险黑名单没进闸门）"]
    try:
        with open(path, encoding='utf-8') as f:
            bl = json.load(f)
    except Exception as e:
        return [f"risk_blacklist.json 解析失败（{e}）"]
    if not isinstance(bl, dict):
        return ["risk_blacklist.json 结构异常（须含 blacklist 对象）"]

    bl_map = bl.get('blacklist') or {}

    def scan(stocks, where):
        for st in (stocks or []):
            if not isinstance(st, dict):
                continue
            code = str(st.get('code') or '').replace('.', '').replace('sh', '').replace('sz', '').zfill(6)
            hit = bl_map.get(str(code))
            if not hit:
                continue
            reasons = '；'.join(hit.get('reasons') or [])
            out.append(f"个股硬风险黑名单命中：{code} {hit.get('name') or st.get('name') or ''}"
                       f"（{reasons}）——须换股，不得留在 8 只池（R100z13 硬闸）")

    out = []
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
                    scan(nw.get('stocks'), f"{mk}.{key}")
                    for imp in (nw.get('impacts') or []):
                        if isinstance(imp, dict):
                            scan(imp.get('stocks'), f"{mk}.{key}.impacts")
    if scope in ('all', 'star'):
        # R100z41：🌟开盘精选的 picks 是本任务自己挑的，不许池外补股，
        # 那就必须自己过闸——否则 9:45 整条链路完全没有硬风险拦截面。
        scan(((D.get('duanban') or {}).get('star') or {}).get('picks'), 'duanban.star.picks')
    return out


def check_logic_depth(D):
    """R100z15 缺口⑤：内容深度没人管 → 机器兜底。

    体检实测（2026-10-01）：aiPrediction.sectors[].logic 只有 32~55 字，
    而 R98p 定的标准是 110-160 字；更关键的是 validate 里**没有任何闸管字数**——
    写多短全靠 AI 自觉，偷懒了照样上线。这里补上：
        logic < 40 字 → FAIL（这不是「写得短」，是没写完）
        logic < 80 字 → WARN（离 110 字下限还差一截）
        deduce.main < 30 字 → WARN
    """
    hards, warns = [], []
    ai = D.get('aiPrediction') or {}
    for s in (ai.get('sectors') or []):
        if not isinstance(s, dict):
            continue
        nm = str(s.get('sector') or '')[:16]
        lg = str(s.get('logic') or '').strip()
        if len(lg) < 40:
            hards.append(f"aiPrediction.{nm}.logic 仅 {len(lg)} 字（<40，等于没写完）——"
                         f"须按 R98p 写满 110-160 字的映射推演（R100z15 硬闸）")
        elif len(lg) < 80:
            warns.append(f"aiPrediction.{nm}.logic 仅 {len(lg)} 字（<80，下限 110 字）——内容偏薄")
        dd = s.get('deduce') or {}
        if isinstance(dd, dict):
            mm = str(dd.get('main') or '').strip()
            if len(mm) < 30:
                warns.append(f"aiPrediction.{nm}.deduce.main 仅 {len(mm)} 字（<30）——主推理由未说清")
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
    """R100z15 缺口②：选股引擎没进决策链 → 板块**有机械候选时，AI 必须照抄机械池**。

    体检实测：断板池 42 确认 + 32 观察 = 74 只候选，48 只预测标的里只有 6 只（12.5%）来自断板池，
    剩下 42 只是 AI 看 note 手写——而硬风险黑名单拦掉的 4 只恰好全在这批手写票里。
    黑名单能拦坏票，拦不住「好票是瞎挑的」，所以这里把选择权机械化：
        机械池里有货 → AI 选的必须是它的子集（候选不足 8 只时允许给少，但绝不许塞池外的票）
        机械池里没货 → WARN（应补 SECTOR_ALIAS 或换板块），不硬拦
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

    def find_pool(sector):
        if sector in by:
            return sector, by[sector]
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
    # 机械池 stock_pool.json 由 16:00 任务在第 5.9 步 `--write` 当日重建，而顶层 aiPrediction
    # 是同日 08:30 定稿的（16:00 只补 verification、不整改 sectors）。因此 16:00 用「当日收盘
    # 重算出的机械池」去考「当日早上写死的预测」，必然大面积零交集 —— 这不是 AI 自选违规，
    # 是检验时序错位。此类场景降级为提示，真正的照抄约束留给下一个交易日的 07:30/08:30/周日
    # 校验（那时 pool.date < pred.date，比对有效）。
    pred_date = str((D.get('aiPrediction') or {}).get('date') or '')
    pool_date = str(pool.get('date') or '')
    if pred_date and pool_date and pool_date >= pred_date:
        warns.append(f"stock_pool.json(date={pool_date}) 不早于 aiPrediction(date={pred_date})："
                     f"机械池在本任务周期内被重建，08:30 定稿的预测无从照抄，照抄约束降级为提示；"
                     f"下一交易日的 07:30/08:30/周日校验仍按同一规则硬拦（R100z17 时序闸）")
        return [], warns

    for s in ((D.get('aiPrediction') or {}).get('sectors') or []):
        if not isinstance(s, dict):
            continue
        sector = str(s.get('sector') or '')
        if not sector:
            continue
        key, lst = find_pool(sector)
        chosen = set()
        for st in (s.get('stocks') or []):
            if isinstance(st, dict) and st.get('code'):
                chosen.add(str(st['code']).replace('.SH', '').replace('.SZ', '').zfill(6))
        if lst is None:
            warns.append(f"aiPrediction.{sector}: 机械选股池无该板块候选"
                         f"{'（补 SECTOR_ALIAS 或换板块）' if bsp else ''}"
                         f"——有候选时必须照抄，不许自己点名")
            continue
        cand = set(str(x.get('code') or '').replace('.SH', '').replace('.SZ', '').zfill(6) for x in lst)
        if not chosen:
            continue
        outside = chosen - cand
        if outside and (len(chosen) - len(chosen & cand)) >= 2:
            # 只差 1 只视为「机械候选里有但漏抄」，提示；差 ≥2 视为自己另起炉灶，硬拦
            if len(outside) >= 2:
                hards.append(f"aiPrediction.{sector}: 选了 {len(chosen)} 只，其中 {len(outside)} 只不在机械选股池"
                             f"（{'、'.join(sorted(outside)[:5])}）——机械池里有货就必须照抄，"
                             f"AI 只保留解释权、交出选择权（R100z15）")
            else:
                warns.append(f"aiPrediction.{sector}: 有 {len(outside)} 只漏抄机械选股池"
                             f"（{'、'.join(sorted(outside))}），请核对")
            continue
        if chosen and not (chosen & cand):
            hards.append(f"aiPrediction.{sector}: 8 只标的与机械选股池（{len(cand)} 只）零交集——"
                         f"和候选池完全无关（R100z15）")
        elif len(cand) and len(chosen) < len(cand):
            warns.append(f"aiPrediction.{sector}: 机械候选 {len(cand)} 只，只采用了 {len(chosen)} 只"
                         f"（未抄满不算错，但须在 note 说明取舍理由）")
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
       （谐波/九门/吸筹，R100m 前一日 16:00 产物，允许池外）。
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
            warns.append("duanban.star 非对象（应为 {date,time,marketLine,sentiment,picks,pushText}）")
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
                    # R100m：允许 src='pw'/'hz'/'acc' 的技术池标的（谐波/九门/吸筹，前一日 16:00 产物）
                    if src in ('pw', 'hz', 'acc') and name_ok:
                        continue
                    warns.append(f"duanban.star.picks {c} {p.get('name')} 不在断板反包双池内且非合规来源标的（精选只能从双池/板块动量/技术池筛）")
            if not str(star.get('pushText') or '').strip():
                warns.append("duanban.star.pushText 为空（微信推送深度分析缺失，10:00 自动化 AI 须补写）")
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
    模块整体缺失不处理（属 16:00 自动化职责）。返回 (改动?, 告警列表)。"""
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

    软闸门：仅 WARN 不阻断部署（与质量类一致）。这些模块由新脚本在 16:00 流水线生成，
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
    # 「FAIL 全在 aiPrediction 却要 9:45 / 07:30 修到 ALL OK」的死锁——
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
        with open('data.js', 'w', encoding='utf-8') as f:
            f.write('window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';\n')

    violations = []
    imp_cnt_warns = check_impact_count(D)
    quality_warns = check_news_quality(D)
    fresh_warns = check_freshness(D)
    horizon_warns = check_horizon(D)
    # R100z10 新增闸门：deduce 结构硬校验 / verification 闭环软校验 / openOutlook 六维度软校验
    deduce_hards, deduce_warns = check_deduce(D)
    # R100z11：预期差结构化字段 / 标的层面验证 / 置信度校准门槛
    exp_hards, exp_warns = check_exp(D)
    veri_warns = check_verification(D)
    stock_warns = check_stockcheck(D)
    calib_warns = check_calibration(D)
    src_warns = check_source_names(D)
    nsrc_warns = check_news_sources(D)   # R100z53：新闻来源白名单机器闸
    lb_warns = check_lianban_notes(D)
    tb_warns = check_top_boards(D)
    st_warns = check_story_quality(D)
    star_warns = check_star_module(D)
    sel_warns = check_selection_modules(D)
    oo_warns = check_openoutlook(D)                    # R100z13：开盘前瞻六维度深度
    ooloop_warns = check_openoutlook_loop(D)           # R100z13：前瞻兑现闭环两头闸
    risk_violations = check_risk_blacklist(D, scope=scope)   # R100z13：个股硬风险黑名单（命中即 FAIL）
    logic_hards, logic_warns = check_logic_depth(D)    # R100z15：内容深度机器闸
    exit_warns = check_exit(D)                         # R100z15：退出纪律（止损/目标/证伪）
    pool_hards, pool_warns = check_stock_pool(D)       # R100z15：机械选股池是否真进决策链
    # R100z16：只把「本职责范围内」的 FAIL 计入 violations。
    if scope in ('all', 'ai'):
        violations.extend(deduce_hards)
        violations.extend(exp_hards)
        violations.extend(logic_hards)
        violations.extend(pool_hards)
    if scope in ('all', 'ai', 'us', 'star'):  # R100z41：star 也要计（此前空转，闸门形同虚设）
        violations.extend(risk_violations)

    if scope in ('all', 'us'):
        for mk in ('ashare', 'us'):
            sec = D.get(mk) or {}
            for key in ('bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews'):
                for i, nw in enumerate(sec.get(key) or []):
                    if not isinstance(nw, dict):
                        continue
                    for j, imp in enumerate(nw.get('impacts') or []):
                        if isinstance(imp, dict):
                            check_tiered(imp.get('stocks'),
                                         f"{mk}.{key}[{i}].impacts[{j}]({(imp.get('theme') or '')[:14]})",
                                         violations)
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
                          'risk_blacklist_violations': risk_violations,
                          'logic_depth_warnings': logic_warns,
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
    for w in logic_warns:
        print("[WARN] 内容深度:", w)
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

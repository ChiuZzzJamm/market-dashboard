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
import re
import subprocess
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

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
    ② duanban.star（10:00 开盘精选）picks 数量不限（≥1，R98l）；
       picks 允许两类：a) 双池内标的；b) src='board' 的板块动量标的（早盘真实强势板块领涨股，R98n，允许池外）。
       纯池外且无 src='board'/无板块依据的标的 → WARN。"""
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
                    # R98n：允许 src='board' 的板块动量标的（早盘真实强势板块领涨股，可池外入选）
                    if (p.get('src') == 'board'
                            and str(p.get('name') or '').strip()
                            and str(p.get('sector') or '').strip()):
                        continue
                    warns.append(f"duanban.star.picks {c} {p.get('name')} 不在断板反包双池内且非板块动量标的（精选只能从池内或强势板块筛）")
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


def main():
    as_json = '--json' in sys.argv
    D = load_data()

    # 先自动补救 direction（缺失/非法 → 中性）、duanban 软闸门（剔除非主板/补概率），
    # 再无破坏性地按统一顺序重排（龙头→概念→小盘人气→断板反包）；均写回但不阻断部署。
    dir_warns = fix_direction_inplace(D)
    duanban_changed, duanban_warns = fix_duanban_inplace(D)
    if '--no-fix-order' not in sys.argv or duanban_changed:
        reorder_inplace(D)
        with open('data.js', 'w', encoding='utf-8') as f:
            f.write('window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';\n')

    violations = []
    imp_cnt_warns = check_impact_count(D)
    quality_warns = check_news_quality(D)
    fresh_warns = check_freshness(D)
    horizon_warns = check_horizon(D)
    src_warns = check_source_names(D)
    lb_warns = check_lianban_notes(D)
    tb_warns = check_top_boards(D)
    st_warns = check_story_quality(D)
    star_warns = check_star_module(D)

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
    for w in lb_warns:
        print("[WARN] 连板标注:", w)
    for w in tb_warns:
        print("[WARN] 板块TOP10:", w)
    for w in st_warns:
        print("[WARN] story质量:", w)
    for w in star_warns:
        print("[WARN] 🌟开盘精选:", w)
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

#!/usr/bin/env python3
"""预测成绩单（R100z161）—— 把「AI 预测准不准」从 AI 自述变成可核对的账。

## 为什么要有这个脚本

`calibration_log.json` 里已有 11 个交易日 × 6 条 = 66 条板块预判，每条都记了
predicted / actualPct / excessPct / result / conf。`open` 段另有 11 天开盘前瞻打分。
但页面上 **只显示当天那一段** `aiPrediction.verification`，第二天就被覆盖。

结果是：用户看到「六条判断两条兑现」这种 AI 自己写的总结，无从核对，
也永远不知道「过去两个月命中率多少、错在哪个方向、该信几分」。
用户 2026-10-24 的原话是「准确率不高，这部分要怎么优化」——
在不知道基线的情况下，任何「优化」都是瞎调。

本脚本只做一件事：**把台账算成成绩单，并基于成绩自动生成使用建议。**
它不预测、不改预测、不评价预测得好不好，只报数。

## 输出（写进 data.js 的 forecastScore 节点）

    sector  板块预判：总数/严格命中/宽口径/跑赢率/超额均值 + 按置信度、按方向、近 5 日
    forward 开盘前瞻：均分/倾向/点位/量能三项命中
    usage   基于上面数字**自动生成**的使用建议（数字变了结论跟着变）
    verdict 一句话结论（同样由数字决定，不由人手写）

## 为什么 `usage` 必须自动生成

如果这段文字是人写的，它就会一直停在我写它那天的判断，哪怕数据已经反转。
所以每个结论都挂在阈值上：数据变好 → 结论自动变温和；数据变差 → 自动变严厉。
这也是本项目「禁写死基线数字」的延伸：连结论都不许写死。

## 用法

    python3 forecast_scorecard.py            # 算并写入 data.js 的 forecastScore
    python3 forecast_scorecard.py --view     # 只打印，不写盘
    python3 forecast_scorecard.py --self-test
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import common                                    # noqa: E402

LOG_FILE = os.path.join(ROOT, 'calibration_log.json')

# 门槛与口径：写死在代码里，不通过参数暴露，避免「调参让成绩好看」
MIN_N = 30          # 少于 30 条不下结论（与 signal_attribution 一致）
MIN_DAYS = 5        # 少于 5 个交易日不下结论
MIN_GROUP_N = 5     # 单个分组少于 5 条标 small：n=1 的 100% 不是能力，是巧合
RECENT_DAYS = 5     # 近 5 日趋势
CONF_ORDER = ('高', '中', '低')
# 「中性」是有效预测方向，不能丢：丢一条会让分组样本合计对不上总数，
# 而那一类恰恰最需要单独看（判「不涨不跌」比判方向更难）。
DIR_LABEL = {'up': '看涨', 'down': '看跌', 'flat': '中性'}
DIR_ORDER = ('up', 'down', 'flat')

# 三个判定阈值——超过就出对应结论。改这里等于改结论口径，需同步自检。
TH_EXCESS_BEAT = 45.0     # 跑赢率低于此值 → 板块推荐不能直接用
TH_CONF_GAP = 10.0        # 高置信比中置信低这么多 pp → 标签失效
TH_DIR_GAP = 15.0         # 看涨比看跌低这么多 pp → 方向能力失衡
TH_BAND_GOOD = 70.0       # 点位「至少半对」达标线
TH_TEND_LOW = 40.0        # 方向判词低于此值视为不可用


# ---------------------------------------------------------------- 读台账

def load_log(path=LOG_FILE):
    if not os.path.exists(path):
        return {}, {}
    with open(path, 'r', encoding='utf-8') as f:
        d = json.load(f) or {}
    return (d.get('log') or {}), (d.get('open') or {})


def _pct(h, n):
    return round(h * 100.0 / n, 1) if n else None


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


# ---------------------------------------------------------------- 统计

def stat_rows(rows):
    """一组预判记录 → 基础统计。严格命中 / 宽口径（含部分兑现）/ 超额。"""
    n = len(rows)
    if not n:
        return {'n': 0, 'strict': 0, 'widely': 0, 'strictHit': None, 'wideHit': None,
                'beatN': 0, 'beatRate': None, 'excessAvg': None, 'excessMed': None}
    strict = sum(1 for r in rows if r.get('result') == '命中')
    part = sum(1 for r in rows if r.get('result') == '部分兑现')
    ex = [x for x in (_f(r.get('excessPct')) for r in rows) if x is not None]
    beat = sum(1 for x in ex if x > 0)
    return {
        'n': n,
        'strict': strict,
        'widely': strict + part,
        'strictHit': _pct(strict, n),
        'wideHit': _pct(strict + part, n),
        'beatN': beat,
        'beatRate': _pct(beat, len(ex)) if ex else None,
        'excessAvg': round(sum(ex) / len(ex), 2) if ex else None,
        'excessMed': round(sorted(ex)[len(ex) // 2], 2) if ex else None,
    }


def group_by(rows, key, order):
    """按维度分组统计。

    `small` 标记很关键：n=1 的组算出 100% 命中率，读者会当真。
    小样本的百分比不是能力，是巧合——必须让前端能把它标出来，
    否则「中性 1 条 = 100%」会和「看跌 16 条 = 62.5%」看起来同等可信。
    """
    out = []
    for k in order:
        g = [r for r in rows if str(r.get(key) or '') == k]
        if not g:
            continue
        s = stat_rows(g)
        s[{'conf': 'conf', 'dirClass': 'dir'}[key]] = k
        if key == 'dirClass':
            s['label'] = DIR_LABEL.get(k, k)
        s['small'] = len(g) < MIN_GROUP_N
        out.append(s)
    return out


def recent_slice(log, days=RECENT_DAYS):
    """近 N 个交易日的预判（按日期排序取尾部，不用日历推算，避免节假日出错）。"""
    ds = sorted(log.keys())
    if not ds:
        return [], []
    take = ds[-days:]
    rows = []
    for d in take:
        rows.extend(log[d])
    return take, rows


def open_stat(op):
    """开盘前瞻：11 天三项拆分。点位允许 0.5（偏离 ≤1%），单独统计半对。"""
    days = sorted(op.keys())
    n = len(days)
    if not n:
        return {'days': 0}
    sc = [_f(op[d].get('score')) for d in days]
    sc = [x for x in sc if x is not None]
    tend = [op[d].get('tendencyHit') for d in days]
    tend = [x for x in tend if x is not None]
    band = [op[d].get('bandHit') for d in days]
    band = [x for x in band if x is not None]
    vol = [op[d].get('volHit') for d in days]
    vol = [x for x in vol if x is not None]
    band_full = sum(1 for x in band if x == 1)
    band_half = sum(1 for x in band if x == 0.5)
    return {
        'days': n,
        'scoreAvg': round(sum(sc) / len(sc), 3) if sc else None,
        'passDays': sum(1 for x in sc if x >= 0.5),
        'tendencyN': len(tend),
        'tendencyHit': sum(1 for x in tend if x == 1),
        'tendencyRate': _pct(sum(1 for x in tend if x == 1), len(tend)) if tend else None,
        'bandN': len(band),
        'bandFull': band_full,
        'bandHalf': band_half,
        # 「至少半对」才是点位能力的真实口径：0 次全错说明区间画得住
        'bandAnyRate': _pct(band_full + band_half, len(band)) if band else None,
        'volN': len(vol),
        'volHit': sum(1 for x in vol if x == 1),
        'volRate': _pct(sum(1 for x in vol if x == 1), len(vol)) if vol else None,
        'recent': [{'date': d, 'score': _f(op[d].get('score')),
                    'verdict': op[d].get('verdict')} for d in days[-8:]],
    }


# ---------------------------------------------------------------- 自动结论

def build_usage(sector, conf, dirs, fwd, enough):
    """把统计数字翻成「该怎么用」。每条都挂在阈值上，数据变了自动变。

    原则：只说「这个信号在什么条件下可以怎么用」，不说「预测准/不准」这种笼统评价。

    每条返回 `{'h': 结论, 'x': 依据}`：
      h 是页面上**默认可见**的一行（硬限 ≤ 26 字，含数字），只放「能不能用」；
      x 是推导过程，渲染进折叠区。
    2026-10-27 改：原先 5 条各 50–103 字、全部默认展开，一张卡光结论就 289 字，
    把上面三条 hero 数字和条形图全压住了——读者要的是「这条信号我能不能用」，
    不是半屏推理。拆成「结论行 + 折叠依据」后可见字数 289 → 96。
    """
    U = []
    if not enough:
        return [{'h': '样本不足，不给建议',
                 'x': '需 ≥%d 条且 ≥%d 个交易日，当前 %d 条 / %d 日。'
                      '样本不够时任何结论都是噪声，宁可不说。'
                      % (MIN_N, MIN_DAYS, sector['n'], sector['days'])}]

    br = sector['beatRate']
    ea = sector['excessAvg']

    # ① 能不能直接拿来选股
    if br is not None and br < TH_EXCESS_BEAT:
        U.append({
            'h': '<b>板块推荐不能直接当选股依据</b>',
            'x': '%d 条里只有 %.0f%% 跑赢基准、超额均值 %+.2f%%（中位 %+.2f%%）。'
                 '方向对也可能是「涨了但没跑赢」——真正决定收益的是超额，不是涨跌符号。'
                 % (sector['n'], br, ea or 0, sector['excessMed'] or 0)})
    else:
        U.append({
            'h': '板块推荐可作选股方向参考',
            'x': '超额为正（跑赢率 %.0f%%、均值 %+.2f%%），可作方向参考，但仍需个股层面二次筛选。'
                 % (br or 0, ea or 0)})

    # ② 置信度标签是否可信
    hi = next((c for c in conf if c.get('conf') == '高'), None)
    mid = next((c for c in conf if c.get('conf') == '中'), None)
    if hi and mid and hi['n'] >= 5 and mid['n'] >= 10:
        gap = (mid['strictHit'] or 0) - (hi['strictHit'] or 0)
        if gap >= TH_CONF_GAP:
            U.append({
                'h': '「高置信」是反向指标：%.0f%% 对 %.0f%%'
                     % (hi['strictHit'] or 0, mid['strictHit'] or 0),
                'x': '标高的 %d 条命中 %.0f%%、标中的 %d 条命中 %.0f%%，高置信反而低 %.0f 个百分点。'
                     '这是典型的过度自信——最像样的理由往往已经把利好提前打进价格。'
                     '结论：<b>不要把「高置信」当加仓理由</b>，宁可等它被市场证实。'
                     % (hi['n'], hi['strictHit'], mid['n'], mid['strictHit'], gap)})
        elif gap <= -TH_CONF_GAP:
            U.append({
                'h': '置信度分层有效：高确实更准',
                'x': '高 %d 条命中 %.0f%%、中 %d 条命中 %.0f%%，高置信更准 %.0f 个百分点，'
                     '可以按置信度分配关注度。'
                     % (hi['n'], hi['strictHit'], mid['n'], mid['strictHit'], -gap)})
        else:
            U.append({
                'h': '置信度分层没有区分度',
                'x': '高 %d 条 %.0f%%、中 %d 条 %.0f%%，差 %.0f 个百分点，在阈值内。'
                     '也就是说「我觉得很有把握」这个感觉目前不携带额外信息。'
                     % (hi['n'], hi['strictHit'], mid['n'], mid['strictHit'], abs(gap))})

    # ③ 看涨 vs 看跌的能力差
    up = next((x for x in dirs if x.get('dir') == 'up'), None)
    dn = next((x for x in dirs if x.get('dir') == 'down'), None)
    if up and dn and up['n'] >= 15 and dn['n'] >= 8:
        upr, dnr = up['strictHit'] or 0, dn['strictHit'] or 0
        if dnr - upr >= TH_DIR_GAP:
            U.append({
                'h': '看跌准得多：%.0f%% 对 %.0f%%，只当排除项' % (dnr, upr),
                'x': '能力是不对称的：看涨 %d 条命中 %.0f%%、看跌 %d 条命中 %.0f%%，差 %.0f 个百分点。'
                     '风险识别明显强于机会识别——利空传导直接（价格立刻动），'
                     '利好容易被提前抢跑（等你看到的时候已经被买过了）。'
                     '用法：<b>看跌结论可当排除项用</b>（回避它点名的板块），'
                     '<b>看涨结论只作观察</b>，不当买入理由。'
                     % (up['n'], upr, dn['n'], dnr, dnr - upr)})
        elif upr - dnr >= TH_DIR_GAP:
            U.append({
                'h': '看涨明显强于看跌，做空判断要打折',
                'x': '看涨（%d 条 %.0f%%）明显强于看跌（%d 条 %.0f%%），'
                     '这套判断偏多头视角，做空/避险判断要打折。'
                     % (up['n'], upr, dn['n'], dnr)})
        else:
            U.append({
                'h': '方向能力基本对称，没有系统性偏向',
                'x': '看涨 %.0f%%、看跌 %.0f%%。' % (upr, dnr)})

    # ④ 开盘前瞻：点位 vs 方向
    if fwd.get('days', 0) >= 5:
        bAny = fwd.get('bandAnyRate') or 0
        tr = fwd.get('tendencyRate') or 0
        if bAny >= TH_BAND_GOOD and tr < TH_TEND_LOW:
            U.append({
                'h': '用它的点位挂单，别信它的方向判词',
                'x': '点位 %d/%d 至少半对（%.0f%%），但方向只有 %d/%d 命中（%.0f%%）。'
                     '用法：<b>用它的点位区间挂单，不要用它的「偏强/承压」下注</b>。'
                     % (fwd['bandFull'] + fwd['bandHalf'], fwd['bandN'], bAny,
                        fwd['tendencyHit'], fwd['tendencyN'], tr)})
        elif tr < TH_TEND_LOW:
            U.append({
                'h': '开盘前瞻方向判词不足以作依据',
                'x': '方向判词只有 %.0f%% 命中（%d/%d）。等它连续 10 日跑过 50%% 再考虑采用。'
                     % (tr, fwd['tendencyHit'], fwd['tendencyN'])})
        else:
            U.append({
                'h': '开盘前瞻两项都过线，可作次日调仓参考',
                'x': '方向命中 %.0f%%、点位至少半对 %.0f%%。' % (tr, bAny)})

    # ⑤ 趋势：近 5 日 vs 全期
    r5 = sector.get('recent5') or {}
    if r5.get('n') and sector.get('strictHit') is not None:
        d = (r5['strictHit'] or 0) - sector['strictHit']
        if d >= 10:
            U.append({
                'h': '近 %d 日手感在变好（%+.0fpp）' % (RECENT_DAYS, d),
                'x': '近 %d 日命中 %.0f%%，比全期 %.0f%% 高 %.0f 个百分点。'
                     '但要小心是行情风格适配（普涨里看涨天然容易中）而非能力提升。'
                     % (RECENT_DAYS, r5['strictHit'], sector['strictHit'], d)})
        elif d <= -10:
            U.append({
                'h': '近期在退步（%+.0fpp），下调权重' % (d),
                'x': '近 %d 日命中 %.0f%%，比全期 %.0f%% 低 %.0f 个百分点。'
                     % (RECENT_DAYS, r5['strictHit'], sector['strictHit'], -d)})
    return U


def grade_of(sector):
    """把「严格命中 + 跑赢率」压成一个可比较的等级，verdict 与 flags 共用。"""
    st = sector.get('strictHit') or 0
    br = sector.get('beatRate') or 0
    return ('可用' if st >= 55 and br >= 50 else
            '弱有效' if st >= 40 or br >= 45 else
            '无效')


def build_flags(sector, conf, dirs, fwd, enough):
    """机器可判的开关——预测生成步骤直接读这里，闸门也校验这里。

    为什么不用文字：文字结论人读了会点头，但机器没法拦。这里每一条都是
    「某个阈值越线 → 下一次预测必须怎么改」的硬约束，AI 无权绕过。
    """
    F = {
        'enough': enough,
        'grade': grade_of(sector) if enough else None,
        'confInverted': False,
        'maxHighConf': None,      # 0=全部禁标高；1=最多 1 条；None=不限制
        'dirAsym': None,          # upWeak / downWeak
        'bandAnyRate': fwd.get('bandAnyRate'),
        'tendencyRate': fwd.get('tendencyRate'),
        'requireBand': False,     # 必须先给点位区间，倾向判词不得单独当结论
    }
    if not enough:
        return F

    hi = next((c for c in conf if c.get('conf') == '高'), None)
    mid = next((c for c in conf if c.get('conf') == '中'), None)
    if hi and mid and hi['n'] >= 5 and mid['n'] >= 10:
        gap = (mid['strictHit'] or 0) - (hi['strictHit'] or 0)
        if gap >= TH_CONF_GAP:
            F['confInverted'] = True
            F['maxHighConf'] = 0

    up = next((x for x in dirs if x.get('dir') == 'up'), None)
    dn = next((x for x in dirs if x.get('dir') == 'down'), None)
    if up and dn and up['n'] >= 15 and dn['n'] >= 8:
        upr, dnr = up['strictHit'] or 0, dn['strictHit'] or 0
        if dnr - upr >= TH_DIR_GAP:
            F['dirAsym'] = 'upWeak'
        elif upr - dnr >= TH_DIR_GAP:
            F['dirAsym'] = 'downWeak'

    bAny = F['bandAnyRate']
    tr = F['tendencyRate']
    if bAny is not None and tr is not None:
        F['requireBand'] = bAny >= TH_BAND_GOOD and tr < TH_TEND_LOW
    return F


def build_verdict(sector, fwd, enough):
    """一句话结论，由数字决定。禁人手写。"""
    if not enough:
        return '样本不足，暂不下结论'
    st = sector['strictHit'] or 0
    br = sector['beatRate'] or 0
    grade = grade_of(sector)
    oa = fwd.get('scoreAvg')
    return '板块预判 %d 条严格命中 %.0f%%、跑赢基准 %.0f%%，评定为「%s」；开盘前瞻 %d 日均分 %s（合格线 0.50）。' % (
        sector['n'], st, br, grade, fwd.get('days', 0),
        ('%.2f' % oa) if oa is not None else '—')


# ---------------------------------------------------------------- 组装

def build(D=None):
    log, op = load_log()
    rows = []
    for d in sorted(log.keys()):
        rows.extend(log[d])
    days = len(log)

    sector = stat_rows(rows)
    sector['days'] = days
    conf = group_by(rows, 'conf', CONF_ORDER)
    dirs = group_by(rows, 'dirClass', DIR_ORDER)

    take, rrows = recent_slice(log)
    r5 = stat_rows(rrows)
    r5['days'] = len(take)
    r5['range'] = [take[0], take[-1]] if take else []
    sector['recent5'] = r5

    fwd = open_stat(op)
    enough = sector['n'] >= MIN_N and days >= MIN_DAYS

    usage = build_usage(sector, conf, dirs, fwd, enough)

    return {
        'updatedAt': common.now_str() if hasattr(common, 'now_str') else '',
        'asOf': (sorted(log.keys())[-1] if log else None),
        'criteria': {
            'minN': MIN_N, 'minDays': MIN_DAYS, 'recentDays': RECENT_DAYS,
            'minGroupN': MIN_GROUP_N,
            'strict': '命中 = 板块涨跌方向与预测一致且幅度非噪声；部分兑现 = 方向对但幅度不足',
            'bench': '超额 = 板块涨跌 − 当日基准（全市场个股涨幅中位数或同口径板块均值，见台账 benchUsed）',
        },
        'sector': sector,
        'byConf': conf,
        'byDir': dirs,
        'forward': fwd,
        'usage': usage,
        'flags': build_flags(sector, conf, dirs, fwd, enough),
        'verdict': build_verdict(sector, fwd, enough),
        'enough': enough,
    }


def print_report(r):
    s = r['sector']
    print('=== 板块预判成绩单（%s，%d 个交易日）===' % (r['asOf'] or '—', s['days']))
    print('  总数 %d｜严格命中 %s%%（%d 条）｜宽口径 %s%%（含部分兑现）'
          % (s['n'], s['strictHit'], s['strict'], s['wideHit']))
    print('  跑赢基准 %s%%｜超额均值 %+.2f%%｜中位 %+.2f%%'
          % (s['beatRate'], s['excessAvg'] or 0, s['excessMed'] or 0))
    print()
    print('  --- 按置信度 ---')
    for c in r['byConf']:
        print('    %-2s n=%-3d 严格 %5s%%  宽 %5s%%' % (c['conf'], c['n'], c['strictHit'], c['wideHit']))
    print('  --- 按方向 ---')
    for c in r['byDir']:
        print('    %-3s n=%-3d 严格 %5s%%  宽 %5s%%' % (c['label'], c['n'], c['strictHit'], c['wideHit']))
    r5 = s['recent5']
    print('  --- 近 %d 日（%s）---' % (RECENT_DAYS, '~'.join(r5.get('range') or [])))
    print('    n=%-3d 严格 %5s%%  超额均值 %+.2f%%'
          % (r5['n'], r5['strictHit'], r5['excessAvg'] or 0))
    f = r['forward']
    print()
    print('=== 开盘前瞻（%d 天）===' % f.get('days', 0))
    print('  均分 %s（合格线 0.50，过线 %d 天）'
          % (f.get('scoreAvg'), f.get('passDays', 0)))
    print('  倾向 %s%%（%d/%d）｜点位至少半对 %s%%（全对 %d + 半对 %d / %d）｜量能 %s%%（%d/%d）'
          % (f.get('tendencyRate'), f.get('tendencyHit'), f.get('tendencyN'),
             f.get('bandAnyRate'), f.get('bandFull'), f.get('bandHalf'), f.get('bandN'),
             f.get('volRate'), f.get('volHit'), f.get('volN')))
    print()
    print('=== 结论 ===')
    print('  ' + r['verdict'])
    for u in r['usage']:
        # 2026-10-27：usage 每条改成 {'h': 结论, 'x': 依据}，页面只展开 h。
        # 这里两段都打出来——终端是给我看的，越详细越好，和页面取舍相反。
        print('  · ' + u['h'].replace('<b>', '').replace('</b>', ''))
        print('    ' + u['x'].replace('<b>', '').replace('</b>', ''))
    # 这一段是给「生成下一步预测」的脚本/人看的：文字结论可以略读，开关不能
    F = r.get('flags') or {}
    print()
    print('=== 生成约束 flags（R100z161：写 aiPrediction / openOutlook 前必须照此执行）===')
    cap = F.get('maxHighConf')
    print('  grade=%s  enough=%s' % (F.get('grade'), F.get('enough')))
    print('  maxHighConf=%s  → %s' % (
        cap, '一条「高确信」都不许标' if cap == 0
        else ('最多 %d 条「高确信」' % cap if isinstance(cap, int) else '不限制')))
    print('  dirAsym=%s  → %s' % (
        F.get('dirAsym'), {'upWeak': '看涨结论须写明与历史失败批次有何不同，不得只给情绪判词',
                           'downWeak': '看跌结论须写明与历史失败批次有何不同'}.get(
            F.get('dirAsym'), '方向能力均衡，不额外限制')))
    print('  requireBand=%s  → %s' % (
        F.get('requireBand'),
        'openOutlook.pos 必填 bandLo/bandHi 且 lo<hi，不得只给倾向判词'
        if F.get('requireBand') else '区间非强制'))
    print('  依据：倾向 %s%%（%d/%d）｜点位至少半对 %s%%（%d/%d）'
          % (F.get('tendencyRate'), f.get('tendencyHit', 0), f.get('tendencyN', 0),
             F.get('bandAnyRate'), f.get('bandFull', 0), f.get('bandN', 0)))


# ---------------------------------------------------------------- 自检

def _mk(d, sector, pred, res, conf, exc, dircls='up'):
    return {'date': d, 'sector': sector, 'predicted': pred, 'dirClass': dircls,
            'actualPct': 1.0, 'excessPct': exc, 'result': res, 'conf': conf}


def self_test():
    ok = True

    def chk(name, cond):
        nonlocal ok
        print(('  [OK] ' if cond else '  [FAIL] ') + name)
        if not cond:
            ok = False

    # ① 基础统计：命中率 / 宽口径 / 跑赢率
    rows = [_mk('2026-01-01', 'A', '走强', '命中', '中', 1.0),
            _mk('2026-01-01', 'B', '走强', '未命中', '中', -2.0),
            _mk('2026-01-01', 'C', '走强', '部分兑现', '高', 0.5),
            _mk('2026-01-01', 'D', '走强', '未命中', '高', -1.0)]
    s = stat_rows(rows)
    chk('严格命中 = 1/4 = 25.0（实测 %s）' % s['strictHit'], s['strictHit'] == 25.0)
    chk('宽口径 = 2/4 = 50.0（实测 %s）' % s['wideHit'], s['wideHit'] == 50.0)
    chk('跑赢 = 2/4 = 50.0（实测 %s）' % s['beatRate'], s['beatRate'] == 50.0)
    chk('超额均值 = -0.375 → -0.38（实测 %s）' % s['excessAvg'], s['excessAvg'] == -0.38)

    # ② 分组：置信度 / 方向
    conf = group_by(rows, 'conf', CONF_ORDER)
    chk('置信度两组（高/中）实测 %d' % len(conf), len(conf) == 2)
    chk('高置信 n=2', next(c for c in conf if c['conf'] == '高')['n'] == 2)
    # n=2 的组算出来的 50%/100% 不是能力，是巧合——必须能被前端标出来
    chk('n<5 的组带 small 标记',
        next(c for c in conf if c['conf'] == '高').get('small') is True)
    d2 = group_by(rows + [_mk('2026-01-01', 'E', '承压', '命中', '中', 1.0, 'down')],
                  'dirClass', DIR_ORDER)
    chk('方向两组（看涨/看跌）', len(d2) == 2)
    chk('看跌标签 = 看跌（实测 %s）' % d2[1]['label'], d2[1]['label'] == '看跌')
    # 反向测试：真实台账里有 1 条 dirClass='flat'（判「中性」）。漏掉它会让
    # 分组样本合计 ≠ 总数，而这种错在页面上看不出来——只表现为「莫名少了一条」。
    d3 = group_by(rows + [_mk('2026-01-01', 'F', '中性', '命中', '中', 0.1, 'flat')],
                  'dirClass', DIR_ORDER)
    chk('中性单独成组且不丢（实测 %d 组，合计 %d 条）'
        % (len(d3), sum(x['n'] for x in d3)),
        len(d3) == 2 and sum(x['n'] for x in d3) == 5
        and next(x for x in d3 if x['dir'] == 'flat')['label'] == '中性'
        and next(x for x in d3 if x['dir'] == 'flat')['n'] == 1)

    # ③ 空输入不炸
    z = stat_rows([])
    chk('空组 n=0 且比率 None', z['n'] == 0 and z['strictHit'] is None)

    # ④ 样本不足 → 必须拒绝下结论（反向测试：别让少量样本冒充结论）
    u = build_usage({'n': 3, 'days': 1, 'strictHit': 66.7, 'beatRate': 66.7,
                     'excessAvg': 1.0, 'excessMed': 1.0, 'recent5': {}},
                    [], [], {'days': 1}, enough=False)
    chk('样本不足 → 只输出 1 条「样本不足」', len(u) == 1 and '样本不足' in u[0]['h'])

    # ⑤ 置信度反转必须被抓到（本次真实数据就是这种）
    hi_rev = [{'conf': '高', 'n': 9, 'strictHit': 11.1, 'wideHit': 22.2},
              {'conf': '中', 'n': 57, 'strictHit': 40.4, 'wideHit': 61.4}]
    u = build_usage({'n': 66, 'days': 11, 'strictHit': 36.4, 'beatRate': 39.4,
                     'excessAvg': -0.42, 'excessMed': -0.4, 'recent5': {}},
                    hi_rev, [], {'days': 0}, enough=True)
    chk('高置信低于中置信 → 判定「反向指标」',
        any('反向指标' in x['h'] for x in u))

    # ⑥ 置信度正常时不许误报反向
    hi_ok = [{'conf': '高', 'n': 20, 'strictHit': 60.0, 'wideHit': 70.0},
             {'conf': '中', 'n': 40, 'strictHit': 35.0, 'wideHit': 50.0}]
    u2 = build_usage({'n': 60, 'days': 10, 'strictHit': 43.0, 'beatRate': 55.0,
                      'excessAvg': 0.3, 'excessMed': 0.2, 'recent5': {}},
                     hi_ok, [], {'days': 0}, enough=True)
    chk('高置信确实更准 → 判「分层有效」且不出现「反向」',
        any('分层有效' in x['h'] for x in u2)
        and not any('反向指标' in x['h'] + x['x'] for x in u2))

    # ⑦ 方向失衡
    dirs = [{'dir': 'up', 'label': '看涨', 'n': 49, 'strictHit': 26.5, 'wideHit': 45.0},
            {'dir': 'down', 'label': '看跌', 'n': 16, 'strictHit': 62.5, 'wideHit': 70.0}]
    u3 = build_usage({'n': 66, 'days': 11, 'strictHit': 36.4, 'beatRate': 39.4,
                      'excessAvg': -0.42, 'excessMed': -0.4, 'recent5': {}},
                     [], dirs, {'days': 0}, enough=True)
    chk('看涨远弱于看跌 → 判「不对称」', any('不对称' in x['h'] + x['x'] for x in u3))

    # ⑧ 点位强 / 方向弱 → 必须建议「用区间不用方向」
    fw = {'days': 11, 'scoreAvg': 0.467, 'passDays': 3,
          'tendencyN': 11, 'tendencyHit': 2, 'tendencyRate': 18.2,
          'bandN': 11, 'bandFull': 6, 'bandHalf': 5, 'bandAnyRate': 100.0,
          'volN': 10, 'volHit': 4, 'volRate': 40.0}
    u4 = build_usage({'n': 66, 'days': 11, 'strictHit': 36.4, 'beatRate': 39.4,
                      'excessAvg': -0.42, 'excessMed': -0.4, 'recent5': {}},
                     [], [], fw, enough=True)
    chk('区间可靠+方向不可靠 → 建议用区间',
        any('用它的点位' in x['h'] and '不要用它的「偏强' in x['x'] for x in u4))

    # ⑨ 结论分级
    v_bad = build_verdict({'n': 66, 'strictHit': 36.4, 'beatRate': 39.4}, {'days': 11, 'scoreAvg': 0.467}, True)
    v_ok = build_verdict({'n': 66, 'strictHit': 60.0, 'beatRate': 55.0}, {'days': 11, 'scoreAvg': 0.6}, True)
    chk('低命中判「无效」', '评定为「无效」' in v_bad)
    chk('高命中判「可用」', '评定为「可用」' in v_ok)
    chk('样本不足不评级', '样本不足' in build_verdict({'n': 5, 'strictHit': 80, 'beatRate': 80}, {}, False))

    # ⑩ open_stat 半对口径
    op = {'2026-01-01': {'score': 0.8, 'tendencyHit': 1, 'bandHit': 1, 'volHit': 0},
          '2026-01-02': {'score': 0.2, 'tendencyHit': 0, 'bandHit': 0.5, 'volHit': 1}}
    f = open_stat(op)
    chk('点位至少半对 = 2/2 = 100%%（实测 %s）' % f['bandAnyRate'], f['bandAnyRate'] == 100.0)
    chk('点位全对 = 1（实测 %s）' % f['bandFull'], f['bandFull'] == 1)
    chk('倾向命中 1/2 = 50%%（实测 %s）' % f['tendencyRate'], f['tendencyRate'] == 50.0)
    chk('均分 = 0.5（实测 %s）' % f['scoreAvg'], f['scoreAvg'] == 0.5)

    # ⑪ flags：这是预测生成环节真正读的开关，必须能被闸门逐项复算
    sec = {'n': 66, 'days': 11, 'strictHit': 36.4, 'beatRate': 39.4,
           'excessAvg': -0.42, 'excessMed': -0.4, 'recent5': {}}
    F = build_flags(sec, hi_rev, dirs, fw, enough=True)
    chk('高置信反向 → maxHighConf=0（实测 %s）' % F['maxHighConf'], F['maxHighConf'] == 0)
    chk('高置信反向 → confInverted=True', F['confInverted'] is True)
    chk('看涨远弱 → dirAsym=upWeak（实测 %s）' % F['dirAsym'], F['dirAsym'] == 'upWeak')
    chk('点位 100% / 倾向 18% → requireBand=True', F['requireBand'] is True)
    chk('等级随命中率走：36.4%% 判无效（实测 %s）' % F['grade'], F['grade'] == '无效')
    # 反向测试：数据反过来时这些开关必须全灭，否则闸门会天天冤枉正常预测
    F2 = build_flags(sec, hi_ok, [{'dir': 'up', 'label': '看涨', 'n': 49, 'strictHit': 58.0},
                                 {'dir': 'down', 'label': '看跌', 'n': 16, 'strictHit': 52.0}],
                     dict(fw, tendencyRate=72.0, bandAnyRate=60.0), enough=True)
    chk('置信有效 + 方向均衡 + 倾向可靠 → 三个开关全不触发',
        F2['confInverted'] is False and F2['maxHighConf'] is None
        and F2['dirAsym'] is None and F2['requireBand'] is False)
    F3 = build_flags(sec, hi_rev, dirs, fw, enough=False)
    chk('样本不足 → 不产生任何约束（实测 maxHighConf=%s）' % F3['maxHighConf'],
        F3['maxHighConf'] is None and F3['dirAsym'] is None and F3['requireBand'] is False
        and F3['grade'] is None)

    print('[SELF-TEST] ' + ('OK' if ok else 'FAIL'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--view', action='store_true', help='只打印，不写盘')
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    r = build()
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0

    print_report(r)
    if a.view:
        print('\n[VIEW] 未写盘')
        return 0

    base = ROOT
    D = common.load_dashboard_data(base)
    if D is None:
        print('[FAIL] data.js 解析失败')
        return 1
    r['updatedAt'] = D.get('updatedAt') or ''
    D['forecastScore'] = r
    common.write_data_js(base, D, label='[forecastScore]')
    print('\n[OK] forecastScore 已写入 data.js')
    return 0


if __name__ == '__main__':
    sys.exit(main())

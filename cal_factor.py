#!/usr/bin/env python3
"""A股日历 / 流动性窗口因子（R100z11）。

作用：给盘前推演一个「今天是日历上的什么位置」的确定性输入，避免 AI 自算日历误判，
并在长假前 1-3 日 / 月末 / 季末 / 周五系统性下调「隔夜外盘映射」权重、提高落袋倾向。

用法：
    python3 cal_factor.py                    # 默认按今天判定
    python3 cal_factor.py --date 2026-10-09  # 指定日期（仅测/离线用）
    python3 cal_factor.py --json             # 只输出 JSON（供自动化解析）

输出（JSON）：
    {date, weekday, aShareClosed, tags:[...], outWeight, lockIn, drift, note}

口径约定（与 references/predict-calibration.md 一致）：
    outWeight  隔夜外盘映射权重系数（1.00 常态；越低表示外盘只作背景、A股自身结构做主推）
    lockIn     落袋 / 减仓倾向（0.00~0.45，越高表示当日越偏向兑现而非追高）
    drift      流动性回补上浮（节后首日 / 月初首日）
    posLo/posHi/posText  建议仓位带（R100z13：把日历因子落到可记录、可回测的数字上，
                         整成向下取整，posText 表示「建议不超过该上限」，如「5-6 成」）
"""
import argparse
import json
import sys
from datetime import date, timedelta

# 2026 年中国法定节假日（含调休）区间。★年度维护项：跨年须对照国务院放假通知更新，
# 或用 --holiday-file '{"2027-01-01":["2027-01-01","2027-01-03"]}' 覆盖。
HOLIDAYS_2026 = {
    '元旦': ['2026-01-01', '2026-01-03'],
    '春节': ['2026-02-15', '2026-02-21'],
    '清明': ['2026-04-04', '2026-04-06'],
    '劳动节': ['2026-05-01', '2026-05-05'],
    '端午': ['2026-06-19', '2026-06-21'],
    '中秋': ['2026-09-25', '2026-09-27'],
    # ★2026-10-01 修正（R100z14）：原写 10-08 会误框国庆后复牌首日。
    # 交易所公告原文：10月1日(四)至10月7日(三)休市，10月8日(四)起照常开市。
    '国庆': ['2026-10-01', '2026-10-07'],
}

def _d(s):
    y, m, dd = (int(x) for x in s.split('-'))
    return date(y, m, dd)


# 复牌首日白名单：交易所明确规定照常开市，但日期落在假期区间端点上。
# 这类日子一旦被假期区间误框 → 08:30 的「A股交易日守卫」会直接结束，
# 当天不生成盘前前瞻/AI预测/推送，等于节后复牌首日裸奔（R100z14 实际踩过）。
# 本白名单强制把这些日期从休市区间里剔除，比靠人工维护表格更稳。
REOPEN_OVERRIDE_2026 = {
    _d('2026-10-08'),  # 国庆（10/1-10/7 休市）后复牌首日
}


def holiday_ranges(holidays, reopen=()):
    """构造休市区间。reopen 里的日期强制剔除出区间（复牌首日白名单），
    返回 (区间列表, 被剔除的日期列表)。"""
    out, skipped = [], []
    for name, (a, b) in holidays.items():
        da, db = _d(a), _d(b)
        if da in reopen:
            da, skipped = da + timedelta(days=1), skipped + [da.isoformat()]
        if db in reopen:
            db, skipped = db - timedelta(days=1), skipped + [db.isoformat()]
        if da <= db:
            out.append((name, da, db))
    return out, skipped


def is_trading_day(d, ranges):
    """周一~周五 且 不在任何假期区间内 → 交易日。"""
    if d.weekday() >= 5:
        return False
    return not any(a <= d <= b for _, a, b in ranges)


def month_last_trading_days(d, ranges):
    """本月最后 3 个交易日（含 d 之后）。"""
    base = d.replace(day=28) + timedelta(days=6)
    base = base.replace(day=1) - timedelta(days=1)
    days, cur = [], base
    while len(days) < 3:
        if is_trading_day(cur, ranges):
            days.append(cur)
        cur -= timedelta(days=1)
    return days


def evaluate(day, holidays, reopen=()):
    ranges, skipped = holiday_ranges(holidays, reopen)
    tags = []
    note = []
    if skipped:
        note.append('已按复牌白名单把 %s 从休市区间剔除（交易所规定照常开市）' % '、'.join(skipped))

    if not is_trading_day(day, ranges):
        tags.append('A股休市')
        note.append('A股当日休市（节假/周末），盘前推演应直接结束，不生成 aiPrediction/openOutlook')
        return {'date': day.isoformat(), 'weekday': '一二三四五六日'[day.weekday()],
                'aShareClosed': True, 'tags': tags,
                'outWeight': 0.0, 'lockIn': 0.0, 'drift': 0.0,
                'note': '；'.join(note)}

    # 1) 长假前：距下一个假期首日 ≤3 个交易日
    gap, holiday_name = None, None
    for name, a, b in ranges:
        if a > day:
            tmp, cur = [], day + timedelta(days=1)
            while cur < a and len(tmp) < 10:
                if is_trading_day(cur, ranges):
                    tmp.append(cur)
                cur += timedelta(days=1)
            if gap is None or len(tmp) < gap:
                gap, holiday_name = len(tmp), name
    if gap is not None and gap <= 3:
        tags.append(f'长假前{holiday_name}(剩{gap}个交易日)')
        note.append(f'距{gap}个交易日后进入{holiday_name}休市，隔夜外盘映射胜率系统性下降，落袋倾向抬升')

    # 2) 月末 / 季末
    last_days = month_last_trading_days(day, ranges)
    window = set(last_days)
    if day in window:
        tag = '季末' if (day.month in (3, 6, 9, 12)) else '月末'
        tags.append(tag)
        order = len([x for x in last_days if x >= day])  # 该日是本月倒数第几交易日
        qty = '季' if tag == '季末' else '月'
        note.append(f'本月最后交易日窗口（倒数第 {order} 个交易日，{qty}末资金结算与考核效应，外盘映射权重下调）')

    # 3) 周五（周末效应，隔夜外围不可验证）
    if day.weekday() == 4:
        tags.append('周五')
        note.append('周五叠加周末持仓风险溢价，外盘映射只作背景，方向判断以 A股自身量能/连板承接为主')

    # 4) 节后首日 / 月初首日（流动性回补）
    prev = day - timedelta(days=1)
    # ★R100z14 修正：原写法 `not any(a <= prev <= b)` 语义反了——
    #   只有「前一日在假期区间内」才是节后（复牌）首日；写成取反会让真正复牌日漏标，
    #   导致节后首日的 drift +0.10 / lockIn -0.10 从不生效（2026 国庆 10-08 实测漏标）。
    if any(a <= prev <= b for _, a, b in ranges):
        tags.append('节后首日')
        note.append('节后首日流动性回补、前日踏空盘补涨，外盘映射权重可小幅上浮')
    if day.day <= 3:
        tags.append('月初首日')
        note.append('月初首日资金回补，落袋倾向低')

    # 5) 权重与落袋
    out_weight, lock_in, drift = 1.00, 0.00, 0.00
    if any(t.startswith('长假前') for t in tags):
        out_weight, lock_in = 0.55, 0.40
    elif '月末' in tags or '季末' in tags:
        out_weight, lock_in = 0.65, 0.30
    elif '周五' in tags:
        out_weight, lock_in = 0.70, 0.15
    if '节后首日' in tags or '月初首日' in tags:
        drift, lock_in = 0.10, max(0.0, lock_in - 0.10)
    out_weight = round(min(1.0, out_weight + drift), 2)

    # 5) 建议仓位带（R100z13）：仓位 = 基准 - 落袋倾向 + 流动性回补，可记录、可回测
    pos_lo, pos_hi = 0.60, 0.70          # 常态基准 6-7 成
    if any(t.startswith('长假前') for t in tags):
        pos_lo, pos_hi = 0.35, 0.45
    elif '月末' in tags or '季末' in tags:
        pos_lo, pos_hi = 0.45, 0.55
    elif '周五' in tags:
        pos_lo, pos_hi = 0.45, 0.55
    if '节后首日' in tags or '月初首日' in tags:
        pos_lo, pos_hi = round(pos_lo + 0.05, 2), round(pos_hi + 0.05, 2)
    pos_lo, pos_hi = max(0.0, min(0.95, pos_lo)), max(0.0, min(0.95, pos_hi))
    pos_text = f'{int(pos_lo * 10)}-{int(pos_hi * 10)} 成'
    note.append(f'建议仓位带 {pos_text}（而非只给权重不给仓位：这条数字进校准台账，季度回看「长假前降仓」到底赚没赚）')

    summary = '、'.join(tags) if tags else '普通交易日（无显著日历窗口）'
    return {'date': day.isoformat(), 'weekday': '一二三四五六日'[day.weekday()],
            'aShareClosed': False, 'tags': tags,
            'outWeight': out_weight, 'lockIn': lock_in, 'drift': drift,
            'posLo': pos_lo, 'posHi': pos_hi, 'posText': pos_text,
            'note': f'日历标签：{summary}。' + '；'.join(note)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--holiday-file', default=None,
                    help='JSON: {"2027-01-01": ["2027-01-01","2027-01-03"], ...} 覆盖内置节假日表')
    a = ap.parse_args()
    hol = dict(HOLIDAYS_2026)
    if a.holiday_file:
        raw = json.load(open(a.holiday_file, encoding='utf-8'))
        for k, v in raw.items():
            hol[k] = v
    day = _d(a.date) if a.date else date.today()
    reopen = set(REOPEN_OVERRIDE_2026)
    r = evaluate(day, hol, reopen=reopen)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    # ⚠️ 退出码恒为 0，请勿「顺手改得更正确」：
    #   ① 所有自动化（07:30/08:30/9:45/16:00/周末）一律以 stdout 里的 `aShareClosed` 字段
    #      判休市，没有一处看 returncode；
    #   ② audit_pipeline.py 的 G6 用 `c != 0` 判 cal_factor 是否「执行成功」，而 G6 固定喂
    #      `--date 2026-10-08`（开市日）——一旦改成「休市 return 1」，休市日跑 audit 时
    #      这条还会因为 10-08 本身开市而侥幸不炸，但语义已经埋雷，谁改到别处谁背锅。
    #   R100z55：此处原写作 `return 0 if not r['aShareClosed'] else 0`，两个分支都是 0，
    #   属写残的死代码（看起来像「休市返回 1」，实际永远 0）。已显式收敛为 `return 0` 并留档。
    return 0


if __name__ == '__main__':
    sys.exit(main())

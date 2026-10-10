# -*- coding: utf-8 -*-
"""macro_calendar.py — 宏观事件前瞻层（R100z117，item 6）。

产出未来 N 日内的确定性宏观事件日历，写回 data.js 顶层 macroCalendar，供前端渲染
「宏观事件前瞻」卡片。事件分两类：
  - 国内：CPI/PPI（每月~9日）、工业增加值/固投/社零（每月~15日）、MLF（约每月15日）、
    LPR（每月20日）、GDP（季度，1/18、4/16、7/15、10/19）；
  - 外围：美联储 FOMC 议息（已公布 2026 日程，约每 6 周一次）。
所有日期均为「预告」，标注「以官方公布为准」——本脚本不做任何外推或编造，只把
公开可查的定期披露节奏与已公布的 FOMC 日程搬到看板。解禁/打新需个股级数据源，
不在本模块范围（卡片底部注明）。

设计纪律（与 enrich_policy 一致）：
  - 零网络、纯函数；失败兜底写空数组并 exit 0，绝不阻断流水线。
  - 只新增 macroCalendar 一个顶层键，不改写其它字段。
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HORIZON_DAYS = 45

FOMC_2026 = ['2026-01-27', '2026-03-17', '2026-04-28', '2026-06-09',
             '2026-07-28', '2026-09-15', '2026-10-27', '2026-12-08']
GDP_2026 = ['2026-04-16', '2026-07-15', '2026-10-19', '2027-01-17']

# (日, 名称, 类型, 注记) 的月度固定节奏
MONTHLY = [
    (9, 'CPI / PPI 通胀数据', '国内', '统计局每月~9日公布，遇假顺延；以官方为准'),
    (15, '工业增加值 / 固投 / 社零', '国内', '统计局国民经济运行数据，每月~15日；以官方为准'),
    (15, 'MLF 续作', '国内', '央行中期借贷便利，约每月15日；以央行公告为准'),
    (20, 'LPR 报价', '国内', '贷款市场报价利率，每月20日；以央行授权披露为准'),
]

# R100z117b（用户 2026-10-10）：每类事件补充「埋伏方向 + 风险提示」，供前端渲染。
# R100z117c（用户 2026-10-10：埋伏方向落到具体标的）：每条 impact 末尾附观察标的。
#   标的纪律：个股只选 60/00 沪深主板龙头（同看板硬约束，禁 688/300 系），
#   ETF 选主流宽基/行业基金（5 开头沪市）；均为「板块联动观察标的、非荐股」，
#   卡片底部注记已声明。代码均为公开长期存在的主流标的，不做外推。
IMPACT_MAP = {
    'CPI / PPI 通胀数据': (
        'PPI 回升利上游资源（煤炭/有色/化工）弹性最大，CPI 温和回升利必选消费提价逻辑；'
        '盘面常提前 1-2 个交易日抢跑，可关注低吸而非追高。'
        '观察标的：中国神华 601088、紫金矿业 601899（上游资源），'
        '煤炭ETF 515220、黄金ETF 518880（商品联动），五粮液 000858（消费提价）',
        '通胀双双不及预期则顺周期涨价逻辑证伪，资源股易回吐涨幅'),
    '工业增加值 / 固投 / 社零': (
        '经济强相关方向——工程机械/建材/基建链与白酒等顺周期消费；'
        '固投超预期对基建、社零超预期对大消费均有提前埋伏价值。'
        '观察标的：三一重工 600031、恒立液压 601100（工程机械），'
        '海螺水泥 600585（建材），贵州茅台 600519（顺周期消费）',
        '数据不及预期直接压制顺周期情绪，谨防利好出尽高开低走'),
    'MLF 续作': (
        '放量续作利好银行、地产等资金敏感板块，红利与利率债同步受益；'
        '利率持平则影响中性，重点看量的信号。'
        '观察标的：招商银行 600036、工商银行 601398（银行），'
        '保利发展 600048（地产），红利ETF 510880（红利资产）',
        '缩量续作易引发流动性收敛担忧，短端利率上行压制高估值方向'),
    'LPR 报价': (
        '下调利好地产链（按揭成本）与券商（增量资金预期）；'
        '不动则观察银行息差修复逻辑。'
        '观察标的：万科A 000002、保利发展 600048（地产链），'
        '中信证券 600030、华泰证券 601688（券商），券商ETF 512000',
        '宽松预期连续落空时地产链易回踩，注意兑现节奏'),
    'GDP 季度数据': (
        '超预期利多大盘权重与顺周期（金融/周期制造），'
        '可提前关注指数权重方向；符合预期则看结构分项（消费/制造业投资）。'
        '观察标的：中国平安 601318、招商银行 600036（金融权重），'
        '沪深300ETF 510300（指数权重一篮子）',
        '不及预期易引发全年目标担忧，市场或切向防御红利'),
    '美联储 FOMC 议息会议': (
        '鸽派利好贵金属（黄金股）与 A 股成长映射（算力/半导体），'
        '人民币资产风险偏好回升；会前 1-2 日外围波动放大，适合等落地再跟。'
        '观察标的：山东黄金 600547、紫金矿业 601899（贵金属），黄金ETF 518880，'
        '中兴通讯 000063、北方华创 002371（成长映射）',
        '鹰派表态推升美债利率，压制全球风险资产，隔日 A 股成长板块承压'),
}


def _d(s):
    y, m, dd = (int(x) for x in s.split('-'))
    return datetime.date(y, m, dd)


def build(today):
    end = today + datetime.timedelta(days=HORIZON_DAYS)
    ev = []
    for s in FOMC_2026:
        dt = _d(s)
        if today <= dt <= end:
            ev.append((dt, '美联储 FOMC 议息会议', '外围',
                       '利率决议+点阵图；以美联储官网公布的日程为准'))
    for s in GDP_2026:
        dt = _d(s)
        if today <= dt <= end:
            ev.append((dt, 'GDP 季度数据', '国内', '统计局季度 GDP；以官方为准'))
    # 月度节奏：覆盖 today 当月到 end 所在月
    months = []
    cur = datetime.date(today.year, today.month, 1)
    while cur <= end:
        months.append(cur)
        # 下个月
        if cur.month == 12:
            cur = datetime.date(cur.year + 1, 1, 1)
        else:
            cur = datetime.date(cur.year, cur.month + 1, 1)
    for mo in months:
        for day, name, typ, note in MONTHLY:
            try:
                dt = datetime.date(mo.year, mo.month, day)
            except ValueError:
                continue
            if today <= dt <= end:
                ev.append((dt, name, typ, note))
    ev.sort(key=lambda x: x[0])
    out = []
    for dt, name, typ, note in ev:
        imp, rsk = IMPACT_MAP.get(name, ('', ''))
        out.append({
            'date': dt.isoformat(),
            'daysUntil': (dt - today).days,
            'name': name,
            'type': typ,
            'note': note,
            'impact': imp,
            'risk': rsk,
        })
    return out


def _load(path):
    s = open(path, encoding='utf-8').read()
    m = re_search(s)
    if not m:
        raise RuntimeError('data.js 解析失败')
    return json.loads(m.group(1))


import re


def re_search(s):
    m = re.search(r'window\.DASHBOARD_DATA\s*=\s*(\{.*\});?\s*$', s, re.S)
    return m


def _dump(path, D):
    with open(path, 'w', encoding='utf-8') as f:
        f.write('window.DASHBOARD_DATA = ' +
                json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';\n')


def main():
    path = os.path.join(HERE, 'data.js')
    if not os.path.exists(path):
        print('[macro_calendar] data.js 不存在，跳过')
        return 0
    try:
        today = datetime.date.today()
        ev = build(today)
        D = _load(path)
        D['macroCalendar'] = {
            'generatedAt': today.isoformat(),
            'horizonDays': HORIZON_DAYS,
            'events': ev,
            'note': '仅含公开可查的定期宏观披露节奏与已公布 FOMC 日程，均为预告；'
                    '埋伏方向中的观察标的仅为板块联动示例（主板龙头/主流ETF），非荐股；'
                    '解禁/打新需个股级数据源，未纳入本层。',
        }
        _dump(path, D)
        print(f'[macro_calendar] 产出未来 {HORIZON_DAYS} 日宏观事件 {len(ev)} 条（截至 '
              f'{(today + datetime.timedelta(days=HORIZON_DAYS)).isoformat()}）')
        return 0
    except Exception as e:  # noqa: BLE001
        try:
            D = _load(path)
            D['macroCalendar'] = {'generatedAt': datetime.date.today().isoformat(),
                                  'horizonDays': HORIZON_DAYS, 'events': [], 'note': '生成异常，已写空'}
            _dump(path, D)
        except Exception:
            pass
        print(f'[macro_calendar] 异常已兜底（写空）：{e!r}')
        return 0


if __name__ == '__main__':
    sys.exit(main())

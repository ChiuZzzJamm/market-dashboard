# -*- coding: utf-8 -*-
"""新闻来源白名单（R100z53 新增，机器闸门 + prompt 指路共用单一事实源）。

背景：此前「新闻源白名单」只存在于五条自动化的 prompt 正文里（人工约束、跑完才复盘），
没有机器闸门兜底——AI 某次写了个「某某财经自媒体」或把「证监会」写成「CSRC」都不会被拦。
本次下沉为代码：validate_stocks.check_news_sources 读本文件判定，五条自动化 prompt 也
只写「以 news_sources.py 为准」，不再各写一份（避免口径漂移）。

白名单分四类：
  1) 官方监管机构 / 交易所（含本次新增的证监会、沪深北三大交易所、中证登）
  2) 主流财经媒体（国内 + 国际 agency）
  3) 政府部门 / 央行 / 部委
  4) 数据服务商 + 券商研报/投行观点
"""

# (关键词, 类别) —— 命中任一关键词即视为白名单内来源；判定用「包含」而非全等，
# 因为 AI 会写「中国证监会」「上海证券交所」这类变体，全等会天天误报。
SOURCES = [
    # ---- 1) 监管机构 / 交易所（R100z53 本次扩入） ----
    ("证监会", "official"), ("中国证券监督管理委员会", "official"),
    ("上交所", "exchange"), ("上海证券交易所", "exchange"),
    ("深交所", "exchange"), ("深圳证券交易所", "exchange"),
    ("北交所", "exchange"), ("北京证券交易所", "exchange"),
    ("中证登", "exchange"), ("中国证券登记结算", "exchange"),
    ("证券交易所", "exchange"),
    # ---- 2) 国内主流财经媒体 ----
    ("财联社", "media"), ("华尔街见闻", "media"), ("证券时报", "media"),
    ("中国证券报", "media"), ("第一财经", "media"), ("上海证券报", "media"),
    ("证券日报", "media"), ("经济参考报", "media"), ("财新", "media"),
    ("新华社", "media"), ("人民网", "media"), ("央视", "media"),
    ("中国日报", "media"), ("科技日报", "media"), ("每日经济新闻", "media"),
    ("21世纪经济报道", "media"), ("界面新闻", "media"), ("e公司", "media"),
    # ---- 3) 部委 / 央行 / 宏观部门 ----
    ("央行", "gov"), ("中国人民银行", "gov"),
    ("财政部", "gov"), ("工信部", "gov"), ("发改委", "gov"),
    ("商务部", "gov"), ("国资委", "gov"), ("统计局", "gov"),
    ("国家能源局", "gov"), ("证监会汇", "gov"), ("金融监管总局", "gov"),
    ("外汇局", "gov"), ("海关总署", "gov"), ("农业农村部", "gov"),
    # ---- 4) 数据服务商 ----
    ("同花顺", "data"), ("东方财富", "data"), ("通达信", "data"),
    ("巨潮", "data"), ("上市公司公告", "data"),
    # ---- 5) 投行 / 券商研报观点 ----
    ("高盛", "ib"), ("摩根士丹利", "ib"), ("大摩", "ib"),
    ("摩根大通", "ib"), ("小摩", "ib"), ("花旗", "ib"), ("瑞银", "ib"),
    ("美银", "ib"), ("汇丰", "ib"), ("野村", "ib"), ("美银美林", "ib"),
    ("中信证券", "ib"), ("中信建投", "ib"), ("国泰君安", "ib"),
    ("华泰证券", "ib"), ("招商证券", "ib"), ("广发证券", "ib"),
    ("中泰证券", "ib"), ("方正证券", "ib"), ("海通证券", "ib"),
    ("东吴证券", "ib"), ("开源证券", "ib"), ("研报", "ib"),
    # ---- 6) 国际通讯社 / 外媒 ----
    ("彭博社", "intl"), ("路透社", "intl"), ("华尔街日报", "intl"),
    ("CNBC", "intl"), ("FT", "intl"), ("金融时报", "intl"),
    ("Bloomberg", "intl"), ("Nikkei", "intl"), ("日经", "intl"),
]

# 明确禁止的写法（拆字/简写/旧全称）——与 validate_stocks.check_source_names 重复但保留，
# 因为 news_sources 也会被 prompt 之外的脚本（如 weekendNews 校验）引用。
FORBIDDEN = ["彭博新闻社", "路透通讯社", "WSJ"]


def source_allowed(s):
    """来源字符串是否落在白名单内。返回 (bool, 命中的类别或空串)。"""
    t = str(s or "").strip()
    if not t:
        return False, ""
    for kw, cat in SOURCES:
        if kw.lower() in t.lower():
            return True, cat
    return False, ""


def check_source(s):
    """返回 None 表示合规；返回 (违规原因, 建议) 表示不合规。"""
    t = str(s or "").strip()
    if not t:
        return "来源为空", "填真实来源（证监会/交易所/财联社 等白名单内机构或媒体）"
    for f in FORBIDDEN:
        if f.lower() in t.lower():
            return f"来源用了禁用写法「{f}」", "改用全称：彭博社 / 路透社 / 华尔街日报"
    ok, _ = source_allowed(t)
    if not ok:
        hint = ("改用白名单内机构/媒体（证监会、沪深北交易所、中证登、财联社、华尔街见闻、"
                "证券时报、中国证券报、第一财经、央行及部委、同花顺/东财/通达信、券商研报、三大通讯社）")
        return "来源不在白名单", hint
    return None, None


if __name__ == "__main__":
    self_test = [
        ("证监会", True), ("中国证监会", True), ("上交所", True), ("深圳证券交易所", True),
        ("财联社", True), ("华尔街见闻", True), ("证券时报", True), ("第一财经", True),
        ("央行", True), ("工信部", True), ("同花顺", True), ("中信证券", True),
        ("彭博社", True), ("路透社", True), ("华尔街日报", True), ("CNBC", True),
        ("彭博新闻社", False), ("路透通讯社", False), ("WSJ", False),
        ("某个财经自媒体", False), ("", False), (None, False),
    ]
    bad = 0
    for s, expect in self_test:
        ok, _ = source_allowed(s) if s else (False, "")
        forbid = any(f.lower() in str(s or "").lower() for f in FORBIDDEN)
        good = (ok and not forbid) == expect
        if not good:
            bad += 1
            print(f"  FAIL {s!r} 期望 {expect} 实得 {(ok and not forbid)}")
    print(f"news_sources 自检: {len(self_test) - bad}/{len(self_test)} 通过")
    raise SystemExit(1 if bad else 0)

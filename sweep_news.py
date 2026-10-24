#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""要闻信息等级排序 + 盘面杂音剔除（R100z157）。

背景（2026-10-20 全站审查 BUG-5 / BUG-6）：
  ① 用户一直以为「要闻按信息等级排列」——实际是**否**。五节排列是硬编码固定顺序
     （利好→利空→宏观→国际→投行），而组内 bullNews/bearNews 直接按 AI 写入顺序渲染，
     宏观/国际/投行只按 direction 排（看涨→中性→看跌），**没有任何 level/重要度维度**，
     重要要闻可能沉底。
  ② 杂音剔除（`stock_news.NEWS_STATS_PAT`：丢「N只股涨停」「附股」「炸板」「涨停股复盘」）
     只作用在**个股新闻抓取层**，没下沉到「AI 写五节要闻」环节——AI 若把盘面统计复盘
     写成一条要闻，此前没有任何环节能拦下。

本脚本做两件事（确定性、零网络、幂等）：
  1) 给 ashare / us 的五节要闻逐条打 `level`（0=重点 / 1=关注 / 2=常规），组内稳定排序；
  2) 标题命中盘面统计正则的条目直接剔除（每组至少保留 1 条，避免整节空白）。

排序口径（组内**稳定排序**，同等级保持 AI 原写入序 / 已有的方向序）：
  level 升序 → direction（看涨 0 / 中性 1 / 看跌 2）。

等级判定是**关键词命中制**，不调 LLM、不看 AI 自我评价——这样同一批新闻任何时候跑
都得到同一个等级，可复核、可解释。前端 index.html 的 newsLevelOf() 是同一套词表的 JS 镜像，
数据层没打 level（如 16:00 数据步刚写完还没部署）时由前端兜底计算，保证「排序」永不缺位。

用法：
    python3 sweep_news.py           # 清洗 + 打级 + 排序 + 写回 data.js
    python3 sweep_news.py --dry     # 只打印将要做什么，不写盘
    python3 sweep_news.py --json    # 机器可读统计（供 audit_pipeline 复核）
接入点：deploy.sh 在 enrich_policy.py **之前**调用（一次接入全部会部署的自动化）。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import write_data_js  # noqa: E402

# 与 stock_news 共用同一份盘面统计正则（单一事实源，别在别处再抄一遍）
# ⚠️ R100z204 起 stock_news 的词表拆了两层：这里 import 的 `NEWS_STATS_PAT` 是**共用核心**
#    （榜单/统计/复盘栏目——要闻里也不该有）；它另有一份 `NEWS_STOCK_ONLY`
#    （ETF 基金稿/指数点位/涨停潮/N股飘红）只在个股弹窗层生效 —— 那些在要闻里
#    恰恰是资金面与板块热度的正经信号（「宽基ETF净流入1900亿」是宏观要闻）。
#    **别为了「统一」把两层合并**：合并当天 G22 就报「要闻含噪音」，那是误伤。
try:
    from stock_news import NEWS_STATS_PAT
except Exception:  # stock_news 依赖较多，导入失败时退化为最小规则，不阻断部署
    NEWS_STATS_PAT = (
        re.compile(r'\d+\s*只(?:涨停|跌停|上涨|下跌)?股'),
        re.compile(r'附股'),
        re.compile(r'连板股|涨停股复盘|炸板|涨跌停股'),
        re.compile(r'(?:午间|收盘|早盘|盘中|尾盘)[^，。]{0,6}(?:分析|盘点|速递|播报)'),
    )

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(ROOT, "data.js")
NEWS_KEYS = ("bullNews", "bearNews", "macroNews", "intlNews", "bankViews")

# ---------------- 等级词表（level 0 = 重点 / 1 = 关注 / 2 = 常规） ----------------
# 0 级：系统性、顶层政策与监管、重大宏观数据、海外央行与地缘冲击——影响全市场定价的锚
LEVEL0_KW = (
    # 国内顶层政策 / 监管
    "国常会", "国务院", "政治局", "中央经济工作会议", "中央金融工作会议", "两会", "十五五",
    "证监会", "央行", "中国人民银行", "金融监管总局", "财政部", "发改委", "工信部",
    "商务部", "国资委", "统计局", "海关总署", "国家能源局", "外汇局", "中央汇金", "汇金",
    # 货币与市场制度
    "降准", "降息", "加息", "LPR", "MLF", "逆回购", "公开市场操作", "印花税", "注册制",
    "退市", "减持新规", "市值管理", "平准基金", "暂停IPO", "全面注册制",
    # 宏观数据
    "CPI", "PPI", "GDP", "社融", "新增信贷", "PMI", "非农", "失业率", "零售销售",
    "工业增加值", "固定资产投资", "外汇储备", "贸易顺差", "进出口总额",
    # 海外央行 / 地缘 / 管制
    "美联储", "FOMC", "议息", "欧洲央行", "日本央行", "关税", "制裁", "出口管制",
    "实体清单", "OPEC", "地缘政治",
)

# 1 级：产业与公司层面的实质变化（订单/并购/产能/技术节点）、机构评级、海外龙头动向
LEVEL1_KW = (
    "中标", "订单", "大单", "并购", "收购", "重组", "增持", "回购", "定增", "配股",
    "扩产", "投产", "量产", "涨价", "提价", "涨价函", "业绩预告", "业绩快报", "扭亏",
    "预增", "预亏", "出货量", "装机", "销量", "出口额", "渗透率", "产能利用率", "库存",
    "获批", "临床", "三期", "首台", "国产替代", "技术突破", "专利", "认证",
    "高盛", "摩根士丹利", "大摩", "摩根大通", "小摩", "花旗", "瑞银", "美银", "汇丰",
    "中金", "中信证券", "中信建投", "招商证券", "华泰证券", "国泰君安", "海通证券",
    "英伟达", "台积电", "苹果", "特斯拉", "微软", "谷歌", "亚马逊", "三星",
    "海力士", "美光", "ASML", "OpenAI",
)

def _text(it):
    return (str(it.get("title") or "") + "｜" + str(it.get("sector") or "") + "｜"
            + str(it.get("summary") or "")[:120])


def news_level(it):
    """确定性打分：命中 0 级词 → 0；否则命中 1 级词 → 1；否则 2。"""
    t = _text(it)
    for kw in LEVEL0_KW:
        if kw in t:
            return 0
    for kw in LEVEL1_KW:
        if kw in t:
            return 1
    return 2


def is_noise(title):
    """盘面统计类标题（「56只股涨停」「附股」「炸板」…）——只是把代码列在正文，不是要闻。"""
    s = str(title or "")
    return any(p.search(s) for p in NEWS_STATS_PAT)


def sort_key(it):
    lv = it.get("level")
    lv = lv if isinstance(lv, int) else 2
    d = it.get("direction")
    # direction 可能是「利好A股流动性」这类短语，只认含「看涨/看跌」的语义
    if isinstance(d, str):
        dr = 0 if "看涨" in d else (2 if "看跌" in d else 1)
    else:
        dr = 1
    return (lv, dr)


def sweep_market(mk, sec, stat):
    """就地清洗 + 打级 + 排序某一市场的五节要闻。返回是否有改动。"""
    changed = False
    for key in NEWS_KEYS:
        arr = sec.get(key)
        if not isinstance(arr, list) or not arr:
            continue
        kept = []
        dropped = []
        for it in arr:
            if not isinstance(it, dict):
                continue
            if is_noise(it.get("title")):
                dropped.append(it.get("title"))
                continue
            kept.append(it)
        # 整节被清空时，退回保留原条目（宁可留一条噪音，也不要整节消失）
        if not kept and arr:
            kept = [x for x in arr if isinstance(x, dict)]
            dropped = []
            print(f"[warn] {mk}.{key} 全部命中噪音规则，整节保留（请人工复核）")
        before = [(x.get("level"), x.get("title")) for x in kept]
        for it in kept:
            it["level"] = news_level(it)
        kept.sort(key=sort_key)          # 稳定排序：同 level 同方向保持 AI 原写入序
        after = [(x.get("level"), x.get("title")) for x in kept]
        if dropped:
            stat["dropped"].extend([f"{mk}.{key}: {t}" for t in dropped])
            changed = True
        if before != after:
            changed = True
        sec[key] = kept
        for it in kept:
            stat["levels"][str(it["level"])] += 1
    return changed


def load_data(path=DATA_JS):
    s = open(path, encoding="utf-8").read()
    m = re.search(r"window\.DASHBOARD_DATA\s*=\s*(\{.*\});?\s*$", s, re.S)
    if not m:
        raise RuntimeError("data.js 解析失败（找不到 window.DASHBOARD_DATA）")
    return json.loads(m.group(1))


def save_data(D, path=DATA_JS):
    # R100z179b：原来直接 open(path,"w")，与硬约束 11 相悖（19:30 链会调本脚本）。
    write_data_js(path, D, label="[sweep_news]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        D = load_data()
    except Exception as e:
        print(f"[warn] 读取 data.js 失败，跳过要闻清洗（不阻断部署）：{e}")
        if a.json:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 0

    stat = {"dropped": [], "levels": {"0": 0, "1": 0, "2": 0}, "changed": []}
    for mk in ("ashare", "us"):
        sec = D.get(mk)
        if not isinstance(sec, dict):
            continue
        if sweep_market(mk, sec, stat):
            stat["changed"].append(mk)

    if a.json:
        print(json.dumps({"ok": True, **stat}, ensure_ascii=False))
        return 0

    lv = stat["levels"]
    print(f"[OK] 要闻等级：重点 {lv['0']} 条 / 关注 {lv['1']} 条 / 常规 {lv['2']} 条"
          f"（改动市场：{'、'.join(stat['changed']) or '无（已是最新）'}）")
    if stat["dropped"]:
        print(f"[OK] 剔除盘面杂音 {len(stat['dropped'])} 条：")
        for t in stat["dropped"]:
            print("     - " + str(t)[:80])
    if a.dry:
        print("[dry] 未写盘")
        return 0
    if stat["changed"] or not os.path.exists(DATA_JS):
        save_data(D)
        print("[OK] 已写回 data.js")
    else:
        print("[OK] 无需写盘（幂等）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stock_concept.py — 个股题材/概念标签（同花顺 F10「概念题材」页，R100z325）。

## 为什么要有这个脚本

看板上的标的 pill 一直显示**同花顺一级行业**（纺织制造 / 汽车制造 / 工业金属）。
行业是静态分类，跟不上题材：2026-11-10 实测
  · 浙文影业 601599 → 行业「纺织制造」，实际炒 **短剧游戏 / AI应用 / DeepSeek / 多模态AI**；
  · 航天晨光 600501 → 行业「汽车制造」，实际是 **大飞机 / 卫星导航 / 商业航天**；
  · 中国卫星 600118 → 行业「飞机制造」，实际是 **商业航天 / 低空经济 / 6G**。
用户原话：「很多标的的板块都是错的，能不能改成题材、相关概念？」——按行业找补涨会找错方向。

## 数据从哪来

`http://basic.10jqka.com.cn/<code>/concept.html`（GBK HTML，无需 cookie/JS），
概念名在 `<td class="gnName" clid="886060">短剧游戏</td>`。实测 60/60 命中、0.25s/只、
每只 2~43 个概念（中位 10）。这是**第三方客观标签**，不是 AI 写的，可回溯（带 clid）。

## 输出

`data.js` 顶层 `stockConcepts`：
```
{asOf, tradeDate, src, coverage:{want,got}, items:{
   "601599": {name, industry, concepts:[{n,clid}], hot:[名,名], asOf}
}}
```
  · `hot` = 过滤掉泛概念后的前 2 个（pill 用 1 个，弹窗用 2 个）；
  · `concepts` 保留全量，供弹窗展开；
  · 抓不到就**不写该只**（前端回退行业），绝不编一个概念出来。

## 不抓哪些

只抓「当天真的要展示的标的」（各池 + 精选 + 连板 + 要闻受益股 + AI 预测标的），
不抓全市场 5000 只——一天 200 次请求、约 50 秒；本地 `.concept_cache/` 同日复用，
重复部署不重复抓。
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402

CACHE_DIR = os.path.join(HERE, ".concept_cache")
SRC = "同花顺F10概念题材"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
HOT_N = 2

# 泛概念：是标签但不是题材方向。留着会让 pill 显示「国企改革」「深股通」「中报预增」，
# 读者拿它去做补涨映射会一头雾水（用户 2026-11-10 明确要「题材、相关概念」）。
# 只做**展示层**过滤，`concepts` 全量仍保留，弹窗可以展开看到。
GENERIC = set("""
国企改革 央企国企改革 地方国企改革 上海国企改革 一带一路 融资融券 深股通 沪股通 港股通
人民币贬值受益 人民币升值受益 专精特新 预盈预增 预亏预减 扭亏为盈 年报预增 中报预增 三季报预增
MSCI概念 标普道琼斯A股 富时罗素概念 参股银行 参股券商 参股新三板 参股保险 参股期货 创投 举牌
高送转 员工持股 股权转让(并购重组) 回购增持 破净股 低价股 小盘股 中盘股 大盘股 绩优股 超跌反弹
昨日涨停 昨日连板 昨日触板 今日涨停 龙虎榜 机构重仓 基金重仓 QFII重仓 社保重仓 养老金
信托重仓 券商重仓 保险重仓 ST板块 中字头股票 超级品牌 新型工业化 乡村振兴 西部大开发
长三角一体化 京津冀一体化 雄安新区 粤港澳大湾区 成渝特区 上海自贸区 自由贸易港 水利
同花顺漂亮100 漂亮100 茅指数 宁组合 沪深300 中证500 上证50 深证100 创业板综 深证红利
证金持股 汇金持股 国家队持股 机构调研 股权激励 定向增发 可转债 参股民营银行
""".split())


_GEN_RE = re.compile(r"^\d{4}年?(一季报|中报|三季报|年报|半年报)?(预增|预减|预盈|预亏|扭亏|业绩)")
_GEN_RE2 = re.compile(r"(同花顺.*\d+$|中特估|中字头|证金持股|汇金持股|国家队)")


def is_generic(name):
    """是不是「标签」而不是「题材」。

    除了 GENERIC 里的固定词，还有两类要按模式剔：
      · 带年份的业绩标签：`2026中报预增` / `2026三季报预增`（黑名单只写死了无年份形态，
        实测漏掉了 2026 这一批，导致题材里出现「2026中报预增」）；
      · 风格类：高股息精选 / 高股息 / 破净 / 低市盈率 —— 那是选股风格，不是炒作方向。
    """
    n = str(name or "").strip()
    if not n:
        return True
    return (n in GENERIC or bool(_GEN_RE.match(n)) or bool(_GEN_RE2.search(n))
            or n in ("高股息精选", "高股息", "低市盈率", "低价股"))


def _help_guard():
    """`--help` 只打用法，绝不执行主流程（K17：写 data.js 的脚本必须接住 --help）。"""
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__ or "(本脚本无说明)")
        raise SystemExit(0)


def displayed_codes(D):
    """当天要展示的标的（去重）→ {code: name}。与页面各池一一对应，不多抓。"""
    out = {}

    def add(c, n):
        c = str(c or "")
        if c.startswith(("60", "00")):
            out.setdefault(c, str(n or ""))

    db = D.get("duanban") or {}
    for key in ("confirmed", "watching"):
        for e in (db.get(key) or []):
            add(e.get("code"), e.get("name"))
    cp = D.get("chenPool") or {}
    for key in ("strict", "loose", "watching"):
        for e in (cp.get(key) or []):
            add(e.get("code"), e.get("name"))
    for e in ((D.get("breakoutPool") or {}).get("passed") or []):
        add(e.get("code"), e.get("name"))
    hz = D.get("harmonic") or {}
    for e in (hz.get("confirmPool") or []) + (hz.get("watchPool") or []):
        add(e.get("code"), e.get("name"))
    for e in ((D.get("accumulation") or {}).get("scored") or []):
        add(e.get("code"), e.get("name"))
    for p in ((db.get("star") or {}).get("picks") or []):
        add(p.get("code"), p.get("name"))
    for x in ((D.get("ashare") or {}).get("lianban") or []):
        add(x.get("code"), x.get("name"))
    for sec in ((D.get("aiPrediction") or {}).get("sectors") or []):
        for t in (sec.get("stocks") or []):
            add(t.get("code"), t.get("name"))
    for sec in ("bullNews", "bearNews", "macroNews", "intlNews", "bankViews"):
        for news in ((D.get("ashare") or {}).get(sec) or []):
            for im in (news.get("impacts") or []):
                for t in (im.get("stocks") or []):
                    add(t.get("code"), t.get("name"))
    # 这三处也会在页面上以「票 + 板块」的形式出现，漏了就会在那些位置回退成行业：
    # 今日买点清单 / 昨夜挂单核对 / 强板块落后股。
    for b in ((db.get("star") or {}).get("buyList") or []):
        add(b.get("code"), b.get("name"))
    for r in (((db.get("star") or {}).get("timingExec") or {}).get("rows") or []):
        add(r.get("code"), r.get("name"))
    for o in (((D.get("timingPlan") or {}).get("orders")) or []):
        add(o.get("code"), o.get("name"))
    return out


def _industry(D, code):
    """行业名只作兜底：优先 sectorMap，其次各池条目自带 sector。"""
    sm = D.get("sectorMap") or {}
    if sm.get(code):
        return str(sm[code])
    db = D.get("duanban") or {}
    cp = D.get("chenPool") or {}
    holders = [db.get("confirmed") or [], db.get("watching") or [],
               cp.get("strict") or [], cp.get("loose") or [], cp.get("watching") or [],
               (D.get("breakoutPool") or {}).get("passed") or [],
               (D.get("accumulation") or {}).get("scored") or []]
    for arr in holders:
        for e in arr:
            if str(e.get("code")) == code and e.get("sector"):
                return str(e["sector"])
    return ""


def fetch_concepts(code, tries=2):
    """抓一只票的概念 → [(name, clid)]；失败返回 None（调用方决定是否跳过）。"""
    url = "http://basic.10jqka.com.cn/%s/concept.html" % code
    for i in range(tries):
        try:
            pr = subprocess.run(["curl", "-s", "-m", "15", "-A", UA,
                                 "-e", "http://basic.10jqka.com.cn/", url],
                                capture_output=True, timeout=25)
            txt = pr.stdout.decode("gbk", errors="ignore")
        except Exception:                                  # noqa: BLE001
            txt = ""
        rows = re.findall(r'class="gnName"\s*clid="(\d+)"\s*>\s*([^<]+?)\s*</td>', txt)
        if not rows:
            # 少数票 clid 属性顺序不同，退回只取名字
            rows2 = [(str(i), n) for n in
                     re.findall(r'class="gnName"[^>]*>\s*([^<]+?)\s*</td>', txt)]
            rows = rows2
        if rows:
            return [(n.strip(), c) for c, n in rows if n.strip()]
        if i + 1 < tries:
            import time
            time.sleep(0.4)
    return None


def _cache_path(code):
    return os.path.join(CACHE_DIR, "%s.json" % code)


def load_cache(code, today):
    try:
        with open(_cache_path(code), encoding="utf-8") as f:
            obj = json.load(f)
        if obj.get("asOf") == today and obj.get("concepts"):
            return obj
    except Exception:                                      # noqa: BLE001
        pass
    return None


def save_cache(code, obj):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(code), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:                                      # noqa: BLE001
        pass


def build(D, today, limit=0, refresh=False, verbose=True):
    want = displayed_codes(D)
    codes = list(want.items())
    if limit:
        codes = codes[:limit]
    items, miss = {}, []
    for i, (code, name) in enumerate(codes, 1):
        obj = None if refresh else load_cache(code, today)
        if obj is None:
            got = fetch_concepts(code)
            if got is None:
                miss.append(code)
                continue
            names = [n for n, _ in got]
            # 全是泛概念（国企改革/深股通/中报预增…）时不写 hot：那是标签不是题材方向，
            # 硬顶上去会让卡片写「南方航空 · 国企改革」，比显示行业更误导。前端会自动回退行业。
            hot = [n for n in names if not is_generic(n)][:HOT_N]
            obj = {"name": name, "asOf": today,
                   "industry": _industry(D, code),
                   "concepts": [{"n": n, "clid": c} for n, c in got],
                   "hot": hot}
            save_cache(code, obj)
        # 缓存里可能存着旧口径的 hot（例如「2026中报预增」这种带年份的业绩标签）：
        # 每次读缓存都按当前 is_generic 重算一遍，避免改了黑名单却对旧缓存不生效。
        names = [str(x.get("n") or "") for x in (obj.get("concepts") or []) if isinstance(x, dict)]
        obj["hot"] = [n for n in names if n and not is_generic(n)][:HOT_N]
        if not obj.get("industry"):
            obj["industry"] = _industry(D, code)
            save_cache(code, obj)
        items[code] = obj
        if verbose and i % 25 == 0:
            print("  [concept] %d/%d …" % (i, len(codes)), flush=True)
    return {
        "asOf": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "tradeDate": str((D.get("ashare") or {}).get("tradeDate") or ""),
        "src": SRC,
        "coverage": {"want": len(codes), "got": len(items)},
        "items": items,
    }, miss


def main():
    ap = argparse.ArgumentParser(description="个股题材/概念标签（同花顺 F10 概念题材页）")
    ap.add_argument("--dry", action="store_true", help="只打印不写回")
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 只（调试用）")
    ap.add_argument("--refresh", action="store_true", help="忽略当日缓存，全部重抓")
    a = ap.parse_args()

    D = common.load_dashboard_data(HERE)
    if not D:
        print("[FAIL] 读不到 data.js")
        return 1
    today = datetime.date.today().isoformat()
    node, miss = build(D, today, limit=a.limit, refresh=a.refresh)
    cov = node["coverage"]
    print("[concept] 标的 %d 只，取到概念 %d 只（%.0f%%），失败 %d 只%s"
          % (cov["want"], cov["got"],
             (cov["got"] * 100.0 / cov["want"]) if cov["want"] else 100,
             len(miss), ("：" + "、".join(miss[:6])) if miss else ""))
    if a.limit:
        for c, it in list(node["items"].items())[:5]:
            print("   %s %s → %s" % (c, it.get("name"), " / ".join(it.get("hot") or [])))
    if a.dry:
        print("[DRY] 未写回")
        return 0
    if not node["items"]:
        print("[FAIL] 一只都没抓到，保留旧节点不覆盖（抓不到 ≠ 没有）")
        return 0
    D["stockConcepts"] = node
    common.write_data_js(os.path.join(HERE, "data.js"), D, label="[stock_concept]")
    print("[concept] 已写入 data.js 顶层 stockConcepts（%d 只，asOf=%s）"
          % (len(node["items"]), node["asOf"]))
    return 0


if __name__ == "__main__":
    _help_guard()
    sys.exit(main())

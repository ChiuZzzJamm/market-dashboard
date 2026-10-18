#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R100z149 个股消息库（stockNews）：把「每只标的自己的消息」做成 data.js 顶层节点。

背景（用户 2026-10-19 连续两次反馈）：
  断板池/技术池/连板/板块/预测标的点开弹窗，消息面只有「中性口径：暂无明确
  利好/利空依据」——因为原数据层只有「板块级要闻」，个股公告与个股新闻从来
  没进过 data.js。同花顺个股页有「终止收购」这类公司公告，看板却看不到。

本脚本做两件事（零 AI、纯 HTTP、确定性）：
  1. 东财公告 API（复用 check_duanban.fetch_notices_batch，含当日缓存）
  2. 东财资讯搜索 API（按股票名搜近 N 日新闻，过滤与本公司强相关者）
产出顶层节点：
  stockNews = {
    "600693": {"name":"东百集团","notices":[{date,title,column,dir}],
               "news":[{time,title,summary,source,dir}],"updatedAt":"..."}
  }
只写有内容的标的（无公告无新闻不占体积）。

用法：
  python3 stock_news.py                 # 全量（data.js 里所有 6 位 A 股标的）
  python3 stock_news.py --limit 30      # 只跑前 30 只（联调用）
  python3 stock_news.py --days 14       # 新闻回看天数
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_duanban as cd  # 复用公告抓取 + 关键词分类（同一口径，避免两套词表漂移）

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(ROOT, 'data.js')
NEWS_CACHE_DIR = os.path.join(ROOT, '.news_cache')
NEWS_API = "https://search-api-web.eastmoney.com/search/jsonp"
NEWS_DAYS = 14          # 个股新闻回看天数
NEWS_PAGE_SIZE = 20     # 单只搜索条数（再按相关性与日期过滤）
NEWS_MAX_KEEP = 3       # 单只最多保留条数（体积优先：data.js 已 5MB+，小程序 60s 超时）
NOTICE_MAX_KEEP = 3     # 单只最多保留公告条数
SUMMARY_MAX = 72        # 摘要截断（控制 data.js 体积：538 只 × 6 条 × 72 字 ≈ 300KB）
BUDGET = float(os.environ.get('STOCK_NEWS_BUDGET', '600'))  # 全流程秒级预算
# 跳过「当日缓存读取」强制重抓（写入照常覆盖旧缓存）。
# 用途：改了过滤/归一化逻辑后要对全量标的生效时不必 `rm -rf .news_cache`
# ——那是几百文件的目录，会被安全删除策略拦下（2026-10-19 实测）。
# NEWS_FORCE=1 python3 stock_news.py
NEWS_FORCE = os.environ.get('NEWS_FORCE', '0') == '1'

# 新闻是自由文本，公告那套「整词命中」不够用（"三日即终止的跨界收购"命中不了
# 「终止收购」）。这里用「事件词 + 对象词」同时出现判定，仍然保守：
# 只有两个词都在同一段（标题或摘要头 80 字）出现才判方向。
NEWS_BEAR_PAIRS = (
    ('终止', ('收购', '重组', '合作', '框架', '定增', '重大资产')),
    ('减持', ('股份', '股东', '计划', '公告')),
    ('立案', ('调查', '告知书', '决定书')),
    ('处罚', ('罚款', '警示函', '监管')),
    ('预亏', ('业绩', '亏损', '净利')),
    ('下滑', ('净利', '业绩', '营收')),
    ('诉讼', ('败诉', '判决', '仲裁')),
    ('停产', ('停工', '复产', '整改')),
    ('退市', ('风险', '警示', '终止上市')),
    ('跌停', ('连续', '一字', '封单')),
)
NEWS_BULL_PAIRS = (
    ('中标', ('项目', '合同', '金额', '候选')),
    ('签订', ('合同', '协议', '订单')),
    ('获批', ('批准', '注册', '临床', '上市许可')),
    ('预增', ('业绩', '净利', '同比')),
    ('回购', ('股份', '方案', '注销')),
    ('增持', ('股东', '计划', '股份')),
    ('涨价', ('产品', '提价', '调价')),
    ('投产', ('项目', '产能', '量产')),
    ('重组', ('并购', '资产', '预案')),
    ('涨停', ('连续', '一字', '封单')),
)
# 纯盘面数据类新闻（龙虎榜/资金流/涨跌幅排名）价值低，排在方向性消息之后
NEWS_LOW_VALUE = ('龙虎榜', '资金流向', '主力资金', '涨幅榜', '跌幅榜', '换手率',
                  '大宗交易', '融资余额', '股东户数')
# 评论/述评类（不构成新消息，占位没价值）
NEWS_COMMENTARY = ('侃股', '时评', '观察家', '记者观察', '编后语', '社论', '述评')
# R100z149b：盘面统计类标题（"56只股涨停 最大封单资金19.88亿元" / "45只股上午收盘涨停(附股)"）。
# 这类文章只是把个股代码列在正文里，被现有「摘要前 60 字含代码」判据误判为个股新闻，
# 显示在弹窗「个股消息」段就是噪音。标题命中即丢弃（与个股真实动态无关）。
NEWS_STATS_PAT = (
    re.compile(r'\d+\s*只股'),
    re.compile(r'附股'),
    re.compile(r'连板股|涨停股复盘|炸板'),
    re.compile(r'^\s*\d+\.\d+亿元资金'),
)


def _norm(s):
    """全角→半角归一化（2026-10-19 实测：data.js 里 000032 名为全角『深桑达Ａ』，
    而资讯标题写作半角『深桑达A』→ 名称匹配恒定失败、整只票抓不到个股新闻）。"""
    out = []
    for ch in str(s or ''):
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - 0xFEE0))
        elif ch == '　':
            out.append(' ')
        else:
            out.append(ch)
    return ''.join(out)


def classify_news(title, summary=''):
    """新闻方向：先走公告词表，再用「事件词+对象词」组合判定；都不中返回 neutral。"""
    d = cd.classify_notice(title)
    if d:
        return d
    text = str(title or '') + '｜' + str(summary or '')[:80]
    bear = any(a in text and any(b in text for b in bs) for a, bs in NEWS_BEAR_PAIRS)
    bull = any(a in text and any(b in text for b in bs) for a, bs in NEWS_BULL_PAIRS)
    if bear and not bull:
        return 'bear'
    if bull and not bear:
        return 'bull'
    return 'neutral'


def _today_str():
    return datetime.now().strftime('%Y-%m-%d')


def fetch_stock_news(name, code, days=NEWS_DAYS):
    """东财资讯搜索：按「名称 代码」搜近 days 日新闻 → [{time,title,summary,source,dir}]。

    相关性过滤（必须满足其一，否则丢弃）：
      ① 标题含公司名；② 摘要前 60 字含公司名或含股票代码。
    这样能滤掉搜索 API 返回的泛市场新闻（"周末要闻汇总"之类）。
    """
    name = str(name or '').strip()
    code = str(code or '').strip()
    if not re.fullmatch(r'\d{6}', code) or not name:
        return []
    nm = _norm(name)
    os.makedirs(NEWS_CACHE_DIR, exist_ok=True)
    cp = os.path.join(NEWS_CACHE_DIR, code + '.json')
    today = _today_str()
    if not NEWS_FORCE:
        try:
            if os.path.exists(cp):
                cached = json.load(open(cp, encoding='utf-8'))
                if cached.get('date') == today:
                    return cached.get('list') or []
        except Exception:
            pass

    # R100z149b：双轮搜索。第一轮「名称 代码」，第二轮「纯名称」。
    # 实测单轮带代码时东财会把代码当独立关键词，返回大量仅正文列到该代码的涨停榜
    # 文章（"56只股涨停…"），把真正的个股新闻挤出前 20 条；补一轮纯名称显著提高精度。
    raw, raw2 = '', ''
    for kw in (f"{nm} {code}", nm):
        param = {
            "uid": "", "keyword": kw,
            "type": ["cmsArticleWebOld"], "client": "web", "clientVersion": "curr",
            "clientType": "web",
            "param": {"cmsArticleWebOld": {
                "searchScope": "default", "sort": "time", "pageIndex": 1,
                "pageSize": NEWS_PAGE_SIZE, "preTag": "", "postTag": ""
            }},
        }
        url = NEWS_API + '?cb=cb&param=' + quote(json.dumps(param, ensure_ascii=False))
        try:
            for _ in range(2):
                r = cd.curl_text(url) or ''
                if r.strip():
                    break
                time.sleep(1.5)
        except Exception:
            r = ''
        if kw.startswith(nm + ' '):
            raw = r
        else:
            raw2 = r
    items = []
    for r in (raw, raw2):
        if not r:
            continue
        m = re.search(r'^[^(]*\((.*)\)\s*;?\s*$', r.strip(), re.S)
        body = m.group(1) if m else r
        try:
            obj = json.loads(body)
            items.extend(((obj.get('result') or {}).get('cmsArticleWebOld')) or [])
        except Exception:
            pass
    # 两轮结果按 title 去重（同一条资讯可能两次都返回）
    _uniq, items2 = set(), []
    for x in items:
        _k = re.sub(r'\s+', '', re.sub(r'<[^>]+>', '', str(x.get('title') or '')))
        if _k and _k not in _uniq:
            _uniq.add(_k)
            items2.append(x)
    items = items2
    if not raw and not raw2:
        # R100z149b：两次请求都没拿到响应体（超时/限流）→ 不落缓存，下次自然重试
        return []
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    out, seen = [], set()
    for x in items:
        title = re.sub(r'<[^>]+>', '', str(x.get('title') or '')).strip()
        summary = re.sub(r'<[^>]+>', '', str(x.get('content') or '')).strip()
        tm = str(x.get('date') or '').strip()
        if not title or not tm or tm[:10] < cutoff:
            continue
        # 盘面统计类标题（"56只股涨停" / "…(附股)"）：正文列出代码 ≠ 个股消息，直接丢
        if any(p.search(title) for p in NEWS_STATS_PAT):
            continue
        head = _norm(title) + '｜' + summary[:60]
        if nm not in head and code not in head:
            continue
        key = re.sub(r'\s+', '', title)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            'time': tm[:16],
            'title': title[:80],
            'summary': summary[:SUMMARY_MAX],
            'source': str(x.get('mediaName') or '东方财富资讯')[:20],
            'dir': classify_news(title, summary),
        })
    # 排序：方向性消息在前 → 非盘面数据类在前 → 时间新在前；再截断
    def rank(n):
        directional = 2 if n['dir'] in ('bull', 'bear') else 0
        plain = 0 if any(k in n['title'] for k in NEWS_LOW_VALUE) else 1
        return (-directional, -plain, n['time'])
    out.sort(key=rank)
    # 同一事件常被多家媒体同时报道（东百「终止收购」一次来 5 条）——
    # 两层压缩：① 标题 2-gram 高度重叠的直接丢；② 同一天同一方向最多留 3 条。
    picked, per_day = [], {}
    for n in out:
        if any(k in n['title'] for k in NEWS_COMMENTARY):  # 评论类不占消息位
            continue
        grams = {n['title'][i:i + 2] for i in range(max(0, len(n['title']) - 1))}
        dup = False
        for p in picked:
            pg = {p['title'][i:i + 2] for i in range(max(0, len(p['title']) - 1))}
            if grams and pg and len(grams & pg) / min(len(grams), len(pg)) >= 0.45:
                dup = True
                break
        if dup:
            continue
        k = (n['time'][:10], n['dir'])
        if per_day.get(k, 0) >= 2:
            continue
        per_day[k] = per_day.get(k, 0) + 1
        picked.append(n)
        if len(picked) >= NEWS_MAX_KEEP:
            break
    out = picked
    # 写缓存：R100z149b —— 只在「请求确实成功拿到响应体」时落盘。
    # 背景：8 并发下偶发超时/限流 → raw='' → out=[] → 空列表被 TTL 当日缓存，
    #       这只票当天永久显示「暂无个股消息」，且重跑也读缓存不重试。
    #       2026-10-19 实测：龙溪股份 / 华控赛格 / 华斯股份 / 深桑达Ａ 缓存为 {"list":[]}，
    #       但直打同一 API hitsTotal=52 / 322 明明有数据 —— 一次抖动锁死一整天。
    #       失败不落盘 → 下次运行自然重试。请求成功但确实无新闻（raw 非空、items 空）照常缓存。
    if raw or raw2:
        try:
            with open(cp, 'w', encoding='utf-8') as f:
                json.dump({'date': today, 'list': out}, f, ensure_ascii=False)
        except Exception:
            pass
    return out


def collect_targets(D):
    """扫 data.js 所有渲染面，收集需要用到的 A 股标的 {code: name}。"""
    out = {}

    def add(code, name=''):
        code = str(code or '').strip()
        if not re.fullmatch(r'\d{6}', code):
            return
        if code not in out and name:
            out[code] = str(name).strip()

    db = D.get('duanban') or {}
    for pool in ('confirmed', 'watching', 'failPool'):
        for e in db.get(pool) or []:
            add(e.get('code'), e.get('name'))
    for e in (db.get('star') or {}).get('picks') or []:
        add(e.get('code'), e.get('name'))
    for key in ('harmonic', 'accumulation', 'powerScreen'):
        m = D.get(key) or {}
        for f in ('confirmPool', 'watchPool', 'passed', 'scored', 'strong', 'watch', 'failPool'):
            for e in m.get(f) or []:
                add(e.get('code'), e.get('name'))
    for e in (D.get('ashare') or {}).get('lianban') or []:
        add(e.get('code'), e.get('name'))
    for mk in ('ashare', 'us'):
        m = D.get(mk) or {}
        for f in ('sectorsUp', 'sectorsDown'):
            for sec in m.get(f) or []:
                for t in sec.get('tops') or []:
                    add(t.get('code'), t.get('name'))
    for sec in (D.get('aiPrediction') or {}).get('sectors') or []:
        for e in sec.get('stocks') or []:
            add(e.get('code'), e.get('name'))
    for mk in ('ashare', 'us'):
        m = D.get(mk) or {}
        for f in ('bullNews', 'bearNews', 'macroNews', 'intlNews', 'bankViews'):
            for n in m.get(f) or []:
                for im in n.get('impacts') or []:
                    for st in im.get('stocks') or []:
                        add(st.get('code'), st.get('name'))
    return out


def build(targets, days=NEWS_DAYS):
    codes = list(targets.keys())
    t0 = time.time()
    print(f'[stock_news] 标的 {len(codes)} 只，并发抓公告+新闻…', flush=True)

    notices = {}
    if codes:
        try:
            notices = cd.fetch_notices_batch(codes, budget=min(240, BUDGET * 0.4))
        except Exception as e:
            print(f'[stock_news] 公告抓取失败（降级）：{e}', flush=True)

    news = {}

    def work(code):
        return code, fetch_stock_news(targets.get(code), code, days)

    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(work, c) for c in codes]
        for fut in as_completed(futs):
            try:
                c, lst = fut.result()
                if lst:
                    news[c] = lst
            except Exception:
                pass
            done += 1
            if done % 50 == 0:
                print(f'[stock_news] 新闻 {done}/{len(codes)}  {time.time() - t0:.0f}s', flush=True)
            if time.time() - t0 > BUDGET:
                print('[stock_news] 预算用尽，提前收尾（剩余标的保持原值）', flush=True)
                break

    out = {}
    for code, name in targets.items():
        nl = []
        for x in (notices.get(code) or [])[:NOTICE_MAX_KEEP]:
            title = str(x.get('title') or '')
            # 公告 API 的 title 常带公司名前缀（「东百集团关于…」/「东百集团股票交易…」），
            # 与弹窗已显示的公司名重复，剥掉省体积
            if name and title.startswith(name):
                title = title[len(name):].lstrip('：:').strip()
            nl.append({
                'date': x.get('date') or '',
                'title': title[:70],
                'column': x.get('column') or '',
                'dir': cd.classify_notice(x.get('title')) or 'neutral',
            })
        nw = news.get(code) or []
        if not nl and not nw:
            continue
        out[code] = {'name': name, 'notices': nl, 'news': nw}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 只（联调）')
    ap.add_argument('--days', type=int, default=NEWS_DAYS)
    ap.add_argument('--dry', action='store_true', help='只统计不写文件')
    args = ap.parse_args()

    s = open(DATA_JS, encoding='utf-8').read()
    head = 'window.DASHBOARD_DATA = '
    D = json.loads(s[s.index('{'):s.rindex('}') + 1])
    targets = collect_targets(D)
    if args.limit:
        targets = dict(list(targets.items())[:args.limit])
    news = build(targets, args.days)

    n_notice = sum(1 for v in news.values() if v.get('notices'))
    n_news = sum(1 for v in news.values() if v.get('news'))
    print(f'[stock_news] 有公告 {n_notice} 只 / 有新闻 {n_news} 只 / 写入 {len(news)} 只')
    if args.dry:
        for k, v in list(news.items())[:3]:
            print(k, json.dumps(v, ensure_ascii=False)[:300])
        return

    old = D.get('stockNews') or {}
    D['stockNews'] = news
    if old and not NEWS_FORCE:
        # 合并：本次抓到的覆盖旧值；本次没抓到但有旧值的保留（接口抖动不至于清空）。
        # ⚠️ NEWS_FORCE=1（全量强制重抓）时**不做兜底** —— 否则改了过滤/归一化逻辑后，
        #    新结果为空或变少的标的会保留旧条目，噪音永远清不掉。
        #    2026-10-19 实测：华阳新材旧条目含「涨停股复盘：16股封单超亿元」
        #    「187只股中线走稳」等盘面统计类，加严过滤后 news=0 却被兜底保留。
        for k, v in old.items():
            if k not in D['stockNews'] and k in targets:
                D['stockNews'][k] = v
    with open(DATA_JS, 'w', encoding='utf-8') as f:
        f.write(head + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';\n')
    print(f'[stock_news] 已写回 data.js（stockNews {len(D["stockNews"])} 只）')


if __name__ == '__main__':
    main()

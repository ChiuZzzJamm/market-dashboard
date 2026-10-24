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
# 单只最多保留条数。**R100z205：3 → 6，因为去噪腾出来的名额必须装真消息，不能让深度缩水。**
# 实测（2026-10-25）：cap=3 时 1547 条里 608 条是盘面统计，真正占着名额的是噪音；
# 去噪后 cap 仍为 3 → 单只均 2.90 → 1.87 条、166 只只剩 ≤1 条，用户当场指出
# 「让你优化没让你减少要闻深度」。cap 提到 6 后同样 14 日窗口下真消息填得回 4-6 条。
# 体积代价实测约 +0.3MB（data.js 7.0 → 7.3MB），比噪声换深度更划算。
NEWS_MAX_KEEP = 6
# R100z207：抓取阶段先多留一些当**候选**，等 build() 做完「跨标的去重」再截到 6。
# 原因：跨标的去重（同一标题挂在 ≥3 只票上且不含本公司名 → 板块/统计稿）只有拿到
#   全局视图才判得出，而它恰恰是最能抓干净的一类噪音 —— 正则可以穷举，盘点稿的
#   标题模板穷举不完（实测漏了 448 条「概念涨X%，主力资金净流入这些股」这种）。
#   如果先按 6 截断再去重，被删掉的名额就空着没人填 → 又变成「优化着深度变少」。
NEWS_CAND_KEEP = 12
NOTICE_MAX_KEEP = 3     # 单只最多保留公告条数
SUMMARY_MAX = 180       # 新闻摘要字数上限。东财 content 原文实测 p90=147 / max=154，
                        # 取 180 可完整保留（原 72 字把 1251/1513 条砍成半句，用户实报「显示不全」）
BUDGET = float(os.environ.get('STOCK_NEWS_BUDGET', '600'))  # 全流程秒级预算
# 跳过「当日缓存读取」强制重抓（写入照常覆盖旧缓存）。
# 用途：改了过滤/归一化逻辑后要对全量标的生效时不必 `rm -rf .news_cache`
# ——那是几百文件的目录，会被安全删除策略拦下（2026-10-19 实测）。
# NEWS_FORCE=1 python3 stock_news.py
NEWS_FORCE = os.environ.get('NEWS_FORCE', '0') == '1'

# ---------------------------------------------------------------- K9 环比闸门
# R100z208（用户 2026-10-25 授权「做吧」）：为什么需要这个闸门 ——
#   2026-10-25 那次「深度恢复」里我自引了一个 bug：`merge_depth` 用「code 在今天的
#   targets 里」判是否整只补回，池子一轮换，36 只票连同弹窗里的真公告一起消失。
#   **标的数 533 → 530，只掉了 0.6%，我盯着数字看了半天没看出来**，是用户点开某只
#   票发现「内容变少了」才抓到的。结论：总数量这个指标，人眼对「少 30 条」不敏感，
#   但对「少 30%」敏感。所以把判断交给机器：**不看具体少了什么，只看少了多少**。
#
# 双基线，缺一不可：
#   ① 日环比  vs 上一次存盘值 → 抓断崖（池子轮换 / 接口整片超时 / 去噪规则写宽了）
#   ② 五日峰  vs 近 5 次写盘的最高值 → 抓慢性流失（每天掉 7%，日环比永不报警，
#      但 3 天累计 -20%，弹窗内容一天比一天薄，同样没人发现）
RATIO_MAX_DROP = 0.10        # 单项跌幅 > 10% 判异常（改大等于关闸门，check_code K9 会拦）
RATIO_MIN_BASE = 50          # 基数 < 50 不判：8 条掉到 7 条是 -12.5%，纯抖动
RATIO_PEAK_N = 5             # 五日峰的窗口（次）
STATS_HIST = os.path.join(ROOT, '.cache', 'stocknews_stats.json')
ALERT_JS = os.path.join(ROOT, '.cache', 'k9_alert.json')

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
# R100z150 补强：「27只涨停股」「78只跌停股」「午间涨跌停股分析」——原 `\d+\s*只股`
# 要求「只股」连续，「只涨停股」中间隔字所以漏网（用户实报 8 条残留）。
# R100z204（用户 2026-10-25「好的，去噪音」）：实测库里 1547 条新闻里 556 条（36%）是纯盘面统计，
# 标题里既没有「N只股」也没有「附股」，全从旧正则的缝里漏过去。四轮扫出四个家族：
#   ① 统计：今日盘中突破半年线个股(11 次) / XX行业资金流入榜(27 条) / 主力动向：…特大单净流入95.56亿元
#   ② 栏目：A股收评 / 午报 / 盘前情报 / 公告避雷针 / 投资日历 / 四点半观市 / 中证快报（136 条）
#   ③ 产品：化工ETF汇添富(159093)标的指数涨超1%（基金营销稿 126 条）
#   ④ 综述：超4800只个股飘红 / 昨日行情回顾 / 15只概念股低估值且大幅回调
# 统一判据：**主语是市场/行业/概念/产品，而不是这家公司**。三条保守边界（都是真消息，必须留）：
#   · 「慕思股份连收3个涨停板」「天威视讯涨停走出2连板」——个股涨跌停动态是真消息，
#     所以只丢「N股/N只个股+涨停」这种带计数的市场统计，不丢「涨停」本身；
#   · 「300198，被立案调查！机构龙虎榜抢筹名单出炉」是立案利空，不能因「名单出炉」丢，
#     所以名单类只认括注形式（`(名单)`/`（附名单）`）与前缀明确的「潜力股/受益股/高股息名单」；
#   · 「长鑫科技市值重回3万亿，再登A股榜首」是公司消息，所以榜类不写 `股榜`（会命中「A股榜首」）。
# ⚠️ 两层词表（别合并）：
#   `NEWS_STATS_PAT`   **共用**核心 —— sweep_news.py 直接 `from stock_news import NEWS_STATS_PAT`
#                      去清洗「五节要闻」，所以这里只能放「个股与要闻都不该出现」的东西：
#                      榜单/统计/复盘栏目（主语是市场，不是任何一条事件）。
#   `NEWS_STOCK_ONLY`  **个股层专有** —— 指数点位、ETF 基金营销稿、涨停潮、N股飘红这类，
#                      在「要闻」里恰恰是资金面/板块热度的正经信号（例如
#                      「四家头部基金各出2亿自购 宽基ETF净流入1900亿」是宏观要闻），
#                      只有在「某只票的弹窗」里才是「跟我没关系」。R100z204 第一版把这两类
#                      混进共用表，G22 立刻报「要闻含噪音 2 条」——那是要闻层被误伤，已拆开。
NEWS_STATS_PAT = (
    re.compile(r'\d+\s*只(?:涨停|跌停|上涨|下跌)?股'),     # 185只股 / 27只涨停股 / 78只跌停股
    re.compile(r'附股'),
    re.compile(r'连板股|涨停股复盘|炸板|涨跌停股'),
    re.compile(r'(?:午间|收盘|早盘|盘中|尾盘)[^，。]{0,6}(?:分析|盘点|速递|播报)'),
    re.compile(r'^\s*\d+\.\d+亿元资金'),
    # —— R100z204 盘面统计家族（个股与要闻都不该有） ——
    re.compile(r'(?:突破|站上|失守)[^，。]{0,10}(?:均线|年线|半年线|季线)'),  # 今日盘中突破半年线个股
    re.compile(r'资金(?:流入|流出)榜|资金流向日报|主力动向'),                # XX行业资金流入榜 / 主力动向：
    re.compile(r'特大单净(?:流入|流出)'),                                   # N股特大单净流入资金超2亿元
    re.compile(r'(?:概念|板块)[^，。]{0,16}(?:上涨|下跌)[\d.]+%'),            # 固态电池概念下跌1.25%，主力资金净流出202股
    re.compile(r'[万亿]元?主力资金'),                                       # 14.32亿主力资金净流入，航空发动机概念涨2.92%
    re.compile(r'行业今日净(?:流入|流出)资金'),                              # 汽车行业今日净流入资金9.64亿元…
    re.compile(r'涨跌停情况梳理|揭秘涨停|涨停分析|涨跌停统计'),               # 10月23日A股涨跌停情况梳理
    re.compile(r'\d+\s*(?:只|多只)?(?:个股|股)[^，。]{0,6}(?:涨停|跌停)'),     # 沪指涨0.12% 机械设备板块10股涨停！
    re.compile(r'[（(【][^）)】]{0,10}名单[）)】]'),                            # 107家公司获机构调研（附名单）
    re.compile(r'潜力股名单|受益股名单|高股息名单|概念股名单|牛股名单'),       # 业绩潜力股名单来了
    re.compile(r'排行榜|牛股榜|人气榜|成交活跃股'),                            # 北交所成交活跃股排行榜
    re.compile(r'一览[：:]|\d+股[^。]{0,12}一览|一览$'),                        # A股股票回购一览：149家公司…
    re.compile(r'停复牌汇总|停复牌一览'),                                     # 10月14日盘前停复牌汇总
    # —— R100z204 第二家族：报刊栏目/资讯汇总 ——
    # 实测「去噪音」第二轮发现的更大一族（136 条）：标题是栏目名，正文把几十只票罗列一遍，
    # 相关性判据（摘要前 60 字含名称或代码）必然命中，于是每只被列到的票都吃进同一条聚合文。
    # 判据：标题里带「固定栏目名」。**只列明确的栏目名，不写「快报」「名单」「复盘」这种泛词**——
    # `快报` 会吃掉「【调研快报】XX接待…」（单只票的调研，有价值），
    # `复盘` 会吃掉「涨停股复盘」（另有规则），所以这里逐个点名。
    re.compile(r'早报|午报|晚报|收评|午评|盘前情报|盘前必读|盘前速览|公告集锦|盘后公告'),
    re.compile(r'投资日历|避雷针|焦点复盘|牛股复盘|四点半观市|重点速递|机会速递|中证快报'),
    re.compile(r'券商评级观察|盘后固定价格交易'),                            # 10月23日盘后固定价格交易额9.15亿元
    # 筹码/成交统计（第三家族里与「产品」无关的那半）
    # ⚠️ 必须写成 `筹码新动向|筹码趋向集中`，不能写裸 `筹码集中` ——
    #    「星源材质…前十大流通股东换仓后筹码集中度提升」是公司真消息，会被误杀。
    re.compile(r'筹码新动向|筹码趋向集中|每笔成交量|平均股价|营业部最青睐|融资客青睐'),
    # —— R100z204 第五批：栏目名点名（收尾 52 条；库里剩下的最后一批聚合文） ——
    # 「45只A股筹码大换手（10月19日）」「上证早知道｜600110，…；300006，…」（一篇列三只票）
    # 「晚间公告｜10月23日这些公告有看头」「10月22日早间新闻精选」「深沪北百元股数量达172只」
    re.compile(r'筹码大换手|上证早知道|早间新闻精选|晚间公告[|｜]|这些公告有看头|公告精选'),
    re.compile(r'百元股|家公司(?:公布|披露)(?:三季报|年报|业绩)|强势股追踪|预喜率'),
)

# 个股弹窗专有：这些在「要闻」里是正经信号（资金面/板块热度/指数位置），只在个股层算噪音
NEWS_STOCK_ONLY = (
    re.compile(r'ETF'),                                                     # 基金/ETF 营销稿（126 条）
    re.compile(r'沪指|上证指数|深证成指|创业板指|科创50|三大指数'),           # 指数点位复盘
    re.compile(r'杠杆资金|两融余额'),                                        # 两融余额缩水…杠杆资金大幅加仓165股
    re.compile(r'涨停潮'),                                                   # 人形机器人板块掀涨停潮
    re.compile(r'\d+\s*(?:只)?(?:个股|股)\s*(?:飘红|翻绿|收红|收绿|上涨|下跌)'),  # 超4800只个股飘红
    re.compile(r'\d+只(?:概念股|成分股|受益股|低价股|滞涨股)'),                # 15只概念股低估值且大幅回调
    re.compile(r'一图看懂|行情回顾|机构研判'),                                # 昨日行情回顾 / 一图看懂回购公司
    re.compile(r'概念(?:盘初|盘中|午后|尾盘)(?:活跃|走强|走弱|拉升|回调|异动)'),  # 创新药概念盘初活跃
    # ⚠️ R100z205：`多只个股走强`/`多股涨停` 这类**从共用表移到这一层**——
    #    「机器人概念午后拉升，多只个股走强」在要闻里是**板块广度信号**（用户要看的就是它），
    #    只有在某只票的弹窗里才等于「这条不是我的消息」。是 check_code 的 K8 哨兵
    #    用 must_keep 样本撞出来的：原来写在 NEWS_STATS_PAT，要闻层会静默吃掉这类稿子，
    #    而 G22 只查「要闻里还有没有噪音」，永远发现不了「真信号被删」。
    #    判据：主语是「一批票」（板块广度），不是市场指数，也不是这家公司。
    re.compile(r'多只(?:热门股|个股|概念股|业绩)|多股(?:涨停|大涨|爆发|逆市)'),
    # ⚠️ R100z207 补漏（都是「主语是市场/一批票」但老正则写窄了漏掉的写法）。
    #    每条都单独量过错杀：在现网 2259 条上命中数 vs 「标题含本公司名」的条数，
    #    下面这四组误杀全为 0（唯一有风险的是龙虎榜，见下一行注释）。
    re.compile(r'^\d{6}[，,][^，。]{0,12}(?:直线涨停|分钟涨停|涨停|跌停|复牌|封板|异动)'),
    #    ↑ 「300296，三分钟直线涨停！两大板块，逆市大涨！」以**别人的代码**开头，挂 8 只票。
    #      收紧到「代码开头 + 12 字内出现涨跌停/复牌」，不会碰到「600884，拟向海螺集团定增」这类真消息。
    re.compile(r'主力资金净流入(?:这些股|\d+股|超\d+)'),                        # …概念涨4.45%，主力资金净流入70股
    re.compile(r'(?:概念|板块)涨[\d.]+%'),                                    # 原规则只认「上涨/下跌」，单字「涨」全漏
    re.compile(r'\d+\s*家公司?[^，。]{0,8}(?:预告|披露)|\d+\s*股净利润'),        # 168家公司预告前三季度业绩 / 72股净利润增幅翻倍
    #    ⚠️ 龙虎榜**不能裸判**：「郑中设计(002811)龙虎榜数据(10-13)」是这只票自己的龙虎榜，
    #       属正当消息（68 条）。只杀通用盘点稿「龙虎榜动向：7股获机构及北向资金集体净买入」。
    re.compile(r'龙虎榜动向[：:]\s*\d+\s*股'),
)


def is_stats_title(title):
    """个股消息层的噪音判定 = 共用核心 + 个股专有（R100z204）。

    抽成函数是给「清洗存量」用的：库里已有的 noise 只能用同一套判据删，
    两处各写一份正则会漂移（改了一处忘了另一处，噪音清了半年又长回来）。
    用法见 `--purge-stats`。要闻层只用 `NEWS_STATS_PAT`（见文件头的两层说明）。
    """
    t = str(title or '')
    return any(p.search(t) for p in NEWS_STATS_PAT) or any(p.search(t) for p in NEWS_STOCK_ONLY)


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


def _clip_sent(text, limit=SUMMARY_MAX):
    """按句读截断，避免把一句话砍成半截。

    背景（用户 2026-10-19 实报「个股新闻内容没显示全」）：原实现 `summary[:72]`
    硬切，1251/1513 条都断在句中，出现"…与景德镇陶阳置业有限公司签"这种残句。
    东财 content 原文 p90=147 / max=154，limit=180 时基本不触发截断；
    真超长时优先回退到最后一个句末标点，其次逗号，都没有才加省略号。
    """
    t = str(text or '').strip()
    if len(t) <= limit:
        return t
    seg = t[:limit]
    for mk in ('。', '！', '？', '；', '. ', '! ', '? '):
        i = seg.rfind(mk)
        if i >= limit * 0.55:
            return seg[:i + len(mk)].rstrip()
    i = max(seg.rfind('，'), seg.rfind('、'), seg.rfind(','))
    if i >= limit * 0.55:
        return seg[:i].rstrip() + '…'
    return seg.rstrip() + '…'


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
                    # R100z204：读缓存也过一遍判定 —— 改规则当天，缓存里还是旧口径，
                    # 直接用会让新规则「当天不生效」，第二天又被下一次写入固化。
                    return [n for n in (cached.get('list') or [])
                            if not is_stats_title(n.get('title'))]
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
        # 盘面统计类标题（"56只股涨停" / "…(附股)" / "XX行业资金流入榜"）：正文列出代码 ≠ 个股消息，直接丢
        if is_stats_title(title):
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
            'summary': _clip_sent(summary),
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
    # ⚠️ R100z205：2-gram 阈值 0.45 → 0.60。0.45 太松——两只不同的票在同一板块、
    #    标题结构相近（「XX股份：前三季度净利润同比增长35%」vs「YY股份：前三季度净利润
    #    同比增长40%」）时 2-gram 重合率能超 0.45，把**不同公司的真消息**判成重复。
    #    cap 提到 6 后名额够了，宁可少去重也不能误杀深度（用户 2026-10-25 原话）。
    picked, per_day = [], {}
    for n in out:
        if any(k in n['title'] for k in NEWS_COMMENTARY):  # 评论类不占消息位
            continue
        grams = {n['title'][i:i + 2] for i in range(max(0, len(n['title']) - 1))}
        dup = False
        for p in picked:
            pg = {p['title'][i:i + 2] for i in range(max(0, len(p['title']) - 1))}
            if grams and pg and len(grams & pg) / min(len(grams), len(pg)) >= 0.60:
                dup = True
                break
        if dup:
            continue
        k = (n['time'][:10], n['dir'])
        if per_day.get(k, 0) >= 3:
            continue
        per_day[k] = per_day.get(k, 0) + 1
        picked.append(n)
        if len(picked) >= NEWS_CAND_KEEP:   # R100z207：先留候选，跨标的去重后再截到 NEWS_MAX_KEEP
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


def _drop_crossdup(out, min_stocks=3):
    """跨标的去重：**同一标题挂在 ≥min_stocks 只票上、且标题里没有这家公司的名字** → 板块/统计稿。

    为什么这条判据比正则根本（R100z207，2026-10-25 用户第二次问「还有没有噪音」时加的）：
    正则只能穷举**已知**的标题模板，而东财的盘点稿模板是无穷的 —— 实测清完 608 条后
    又冒出 448 条，全是这一类：「人形机器人概念涨1.46%，主力资金净流入这些股」挂在
    24 只票上、「ChatGPT概念涨4.45%，主力资金净流入70股」挂 10 只、
    「003021，直线涨停！5天3板」挂 8 只（连代码都是别人的）、
    「10月23日晚间沪深上市公司重大事项公告最新快递」挂 8 只。
    它们的共同点不是措辞，是**结构**：一篇稿子被搜索接口同时命中多只票，而稿子本身
    根本没提其中任何一只。这个判据不需要维护词表，也不会随模板变化失效。

    为什么阈值取 3 而不是 2：2 只共用可能真是巧合（关联公司同日发同一份公告、
    同一事件被同一篇稿子提到两家），实测 ≥2 会删 827 条，开始碰到「宁德时代遭减持」
    这类只该在一只票里留的稿子；≥3 删 448 条，抽检 20 条全是真的盘点稿。
    标题含本公司名/代码的一律保留 —— 「宁德时代遭主力减持」挂在 8 只票上时，
    宁德时代那一份是对的，删的是另外 7 只。
    """
    t2c = {}
    for code, item in out.items():
        for n in item.get('news') or []:
            k = re.sub(r'\s+', '', str(n.get('title') or ''))
            t2c.setdefault(k, set()).add(code)
    n_drop = 0
    for code, item in out.items():
        lst = item.get('news')
        if not lst:
            continue
        nm = _norm(item.get('name') or '')
        keep = []
        for n in lst:
            k = re.sub(r'\s+', '', str(n.get('title') or ''))
            ti = _norm(n.get('title') or '')
            if len(t2c.get(k, ())) >= min_stocks and nm not in ti and code not in ti:
                n_drop += 1
                continue
            keep.append(n)
        if keep:
            item['news'] = keep
        else:
            item.pop('news', None)
    return n_drop


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
        # R100z150：只写非空的一侧。原先无条件同时带 notices/news 两个键，
        # 「有新闻无公告」的标的会产出 200+ 个 `notices: []`，全部被 audit G9 判为
        # 「白名单外空内容」刷屏（掩盖真正的空字段）。前端一律用 (x.notices || []) 取值，
        # 缺键安全。
        item = {'name': name}
        if nl:
            item['notices'] = nl
        if nw:
            item['news'] = nw
        out[code] = item

    # R100z207：先拿全局视图做跨标的去重（噪音不占名额），再截到 NEWS_MAX_KEEP。
    # 顺序不能反 —— 先截断再去重，被删的名额就空着，深度又掉了。
    n_drop = _drop_crossdup(out)
    n_cut = 0
    for item in out.values():
        lst = item.get('news')
        if lst and len(lst) > NEWS_MAX_KEEP:
            n_cut += len(lst) - NEWS_MAX_KEEP
            item['news'] = lst[:NEWS_MAX_KEEP]
    if n_drop or n_cut:
        print(f'[stock_news] 全局去噪：跨标的盘点稿丢 {n_drop} 条；'
              f'候选 {NEWS_CAND_KEEP} 截到 {NEWS_MAX_KEEP} 裁掉 {n_cut} 条', flush=True)
    # 新闻被删空的标的：只留 name 的摘掉（否则 audit G9「白名单外空内容」）
    for code in [c for c, v in out.items() if not v.get('notices') and not v.get('news')]:
        out.pop(code, None)
    return out


def _sn_stats(sn):
    """三项指标：标的数 / 新闻条数 / 公告条数。K9 只跟这三个数打交道。"""
    sn = sn or {}
    return {
        'stocks': len(sn),
        'news': sum(len(v.get('news') or []) for v in sn.values()),
        'notices': sum(len(v.get('notices') or []) for v in sn.values()),
    }


RATIO_FIELDS = (('stocks', '标的数'), ('news', '新闻数'), ('notices', '公告数'))


def _load_stats_hist():
    """读最近若干次**成功写盘**的统计值（给五日峰当基线）。读不到就当没有，不影响日环比。"""
    try:
        with open(STATS_HIST, encoding='utf-8') as f:
            h = json.load(f)
        return h if isinstance(h, list) else []
    except Exception:
        return []


def _save_stats_hist(hist, cur, keep=10):
    """写盘**成功后**才调用 —— 被闸门拦下的那次绝不能进历史，否则基线被自己拉低，
    明天就比今天这个低值算了，等于闸门自己把门槛拆了。"""
    hist = (hist or [])[-keep:]
    row = dict(cur)
    row['at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    hist.append(row)
    try:
        os.makedirs(os.path.dirname(STATS_HIST), exist_ok=True)
        tmp = STATS_HIST + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(hist[-keep:], f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATS_HIST)
    except Exception as e:
        print(f'[stock_news] K9 基线写入失败（不影响本次写盘）：{e}', flush=True)
    return hist


def guard_ratio(new, old, peak=None, max_drop=None, min_base=None, allow=False):
    """环比闸门：把「掉了多少」和「掉了什么」解耦，只认数量。

    返回 (breaches, rows)：
      breaches  超阈值且未豁免的项 [{'key','label','base','cur','pct','why'}]
      rows      全量对照表，给日志/审计用

    ⚠️ `max_drop` / `min_base` 必须能外部注入（默认才取常量）—— 否则 check_code 的
    K9 只能验「常量写对了」，验不出「判定真的用了这个常量」。踩过的坑：K8 把
    `_drop_crossdup(min_stocks=99)` 等于关掉，而样本照样全 PASS。
    """
    max_drop = RATIO_MAX_DROP if max_drop is None else max_drop
    min_base = RATIO_MIN_BASE if min_base is None else min_base
    peak = peak if isinstance(peak, dict) else {}
    rows, breaches = [], []
    for key, label in RATIO_FIELDS:
        base_d = int((old or {}).get(key) or 0)
        base_p = int(peak.get(key) or 0)
        cur = int((new or {}).get(key) or 0)
        row = {'key': key, 'label': label, 'prev': base_d, 'peak': base_p, 'cur': cur,
               'pct': 0.0, 'why': ''}
        if base_d <= 0:
            row['why'] = '无基线（首次/上份为空）'
            rows.append(row)
            continue
        # 拿两个基线里**更高的那个**做分母：既要防断崖，也要防慢性流失
        base = max(base_d, base_p)
        row['base'] = base
        if base < min_base:
            row['why'] = f'基数 {base} < {min_base}，不判'
            rows.append(row)
            continue
        pct = (cur - base) / float(base)
        row['pct'] = pct
        if cur >= base * (1 - max_drop):
            row['why'] = '正常'
            rows.append(row)
            continue
        src = '五日峰' if base_p > base_d else '上次'
        row['why'] = f'{src} {base} → {cur}'
        if allow:
            row['why'] += '（已豁免）'
            rows.append(row)
            continue
        breaches.append({'key': key, 'label': label, 'base': base, 'cur': cur,
                         'pct': pct, 'why': row['why']})
        rows.append(row)
    return breaches, rows


def _fmt_rows(rows):
    out = []
    for r in rows:
        pct = r.get('pct') or 0
        out.append('  %-4s 上次 %5d / 峰值 %5d → 本次 %5d  %+6.1f%%  %s' %
                   (r['label'], r['prev'], r['peak'], r['cur'], pct * 100, r['why']))
    return '\n'.join(out)


def _fresh(v, cutoff):
    """旧标的里是否还有**回看窗口内**、且不是噪音的条目（news 比 time / notices 比 date）。"""
    for field, tk in (('news', 'time'), ('notices', 'date')):
        for x in (v.get(field) or []):
            if str(x.get(tk) or '')[:10] < cutoff:
                continue
            if field == 'news' and is_stats_title(x.get('title')):
                continue
            return True
    return False


def merge_depth(sn, old_sn, days=NEWS_DAYS):
    """把旧快照里**仍在回看窗口内**的条目并回 sn —— 深度只增不减（R100z205/206）。

    为什么需要（用户 2026-10-25 原话「让你优化没让你减少要闻深度」）：
    脚本一直是「本次抓取结果整份替换」。接口一抖动（限流/超时/单只无返回），某只票
    本次抓到 0 条，而它旧的 3 条真消息**还在有效期内** → 弹窗当场变空，且当天缓存
    把这个空结果固化一整天。实测 2026-10-25 那次提升保留条数的重抓，128 只票的
    公告 3 条 → 0 条（公告 API 抖动），新闻虽然涨到 2455 条，公告却从 747 掉到 570
    —— 这就是「优化着优化着深度少了」。

    规则：新抓到的排前面；旧条目按 (date/title) 去重后补位；各自不超过上限；
    **只补窗口内的**——否则一条旧闻会永远赖在弹窗里，从「深度」变成「陈旧」。

    ⚠️ R100z206（2026-10-25 用户第二次追问「深度是不是和之前一致」时抓到的真 bug）：
    整只补回的旧判据是「code 在今天的 targets 里」，**这是错的**。`targets` 的语义是
    「今天要抓谁」（由断板池/谐波/吸筹/板块 tops 等**当天会轮换**的渲染面拼出来），
    不是「今天该显示谁」。池子一轮换，36 只票当天没进 targets，整份替换一冲，它们
    连同弹窗里的真公告一起消失 —— 赣锋锂业 3 条、三一重工 3 条、东方中科 3 条、
    诺德股份 3 条、杉杉股份 3 条…全没了，标的数 533 → 530 还看不出问题（因为有
    33 只是新进的），用户点开某只票才发现「内容变少了」。
    改判据：**旧条目里还有窗口内、非噪音的内容**就整只补回，跟 targets 无关；
    真空壳（两侧都空 / 全是 14 天前的）才不复活 —— 这才是 audit G9 想拦的东西。
    附带：从旧快照恢复时，news 必须先过 `is_stats_title`，否则会把已清的噪音带回来。

    返回 (补回的条目数, 整只复活的标的数)。
    ⚠️ R100z207：**跨标的去重要在「补之前」做，不能补完再做**。
    实测踩过「补-删循环」：补进来 → 因为同一标题现在挂到 3 只票上了 → 被判盘点稿删掉 →
    下一轮又从同一份快照补回来，**永不幂等**（第二次跑报「补 319 条 / 去噪 315 条」）。
    正确顺序：① 先清当前存量的跨标的重复；② 对**快照自身**过一遍（计数以快照为准，
    快照不变 → 去重结果固定 → 补进来的一定不会再触发判定）；③ 逐条补，补完不再去重。
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    n_dup = _drop_crossdup(sn)      # ① 存量先清
    _drop_crossdup(old_sn)          # ② 快照自身去重（结果稳定，保证幂等）
    n_fill = n_back = 0
    for code, ov in (old_sn or {}).items():
        nv = sn.get(code)
        if nv is None:
            if not _fresh(ov, cutoff):
                continue
            nv = sn[code] = {'name': ov.get('name') or ''}
            n_back += 1
        if not nv.get('name'):
            nv['name'] = ov.get('name') or ''
        for field, cap, tk in (('news', NEWS_MAX_KEEP, 'time'),
                               ('notices', NOTICE_MAX_KEEP, 'date')):
            cur = list(nv.get(field) or [])
            if len(cur) >= cap:
                continue
            seen = {re.sub(r'\s+', '', str(x.get('title') or '')) for x in cur}
            for x in (ov.get(field) or []):
                if len(cur) >= cap:
                    break
                if str(x.get(tk) or '')[:10] < cutoff:
                    continue
                if field == 'news' and is_stats_title(x.get('title')):
                    continue     # 旧快照可能带噪音，恢复不等于回滚过滤规则
                k = re.sub(r'\s+', '', str(x.get('title') or ''))
                if not k or k in seen:
                    continue
                cur.append(x)
                seen.add(k)
                n_fill += 1
            if cur:
                nv[field] = cur[:cap]
    # 只补出 name、两侧都空的 → 摘掉（否则 audit G9「白名单外空内容」）
    for code in [c for c, v in sn.items()
                 if not v.get('notices') and not v.get('news')]:
        sn.pop(code, None)
    return n_fill, n_back, n_dup


def _snapshot_stock_news(path):
    """读一份 data.js 快照里的 stockNews（给 --merge-depth 用）。"""
    t = open(path, encoding='utf-8').read()
    return (json.loads(t[t.index('{'):t.rindex('}') + 1]) or {}).get('stockNews') or {}


def purge_stats(D):
    """把 data.js 里**已入库**的盘面统计类条目按 is_stats_title 删掉（纯本地、不联网）。

    为什么单独一个入口：过滤规则只在抓取时生效，改完规则若跑 `NEWS_FORCE=1` 全量重抓
    要几百次 HTTP（还有被限流的风险）；而存量清洗是确定性字符串判定，本地一条命令就完了。
    顺带把「删空了的标的」整个摘掉 —— 否则会留下一堆只有 `{name}` 的空壳，
    audit G9「白名单外空内容」会刷屏。
    """
    sn = D.get('stockNews') or {}
    n_del = n_hit = 0
    emptied = []
    for code, v in sn.items():
        lst = v.get('news') or []
        keep = [n for n in lst if not is_stats_title(n.get('title'))]
        if len(keep) != len(lst):
            n_hit += 1
            n_del += len(lst) - len(keep)
            if keep:
                v['news'] = keep
            else:
                v.pop('news', None)
        if not v.get('notices') and not v.get('news'):
            emptied.append(code)
    # R100z207：存量也要过一遍跨标的去重（抓取侧已做，但存量是旧管线产的）
    n_dup = _drop_crossdup(sn)
    emptied = [c for c in sn if not sn[c].get('notices') and not sn[c].get('news')]
    for c in emptied:
        sn.pop(c, None)
    return n_del, n_hit, emptied, n_dup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 只（联调）')
    ap.add_argument('--days', type=int, default=NEWS_DAYS)
    ap.add_argument('--dry', action='store_true', help='只统计不写文件')
    ap.add_argument('--purge-stats', action='store_true',
                    help='只按现行规则清洗 data.js 里已有的盘面统计类噪音（不联网），'
                         '改完 NEWS_STATS_PAT 后用它清存量')
    ap.add_argument('--merge-depth', default='',
                    help='把指定 data.js 快照里仍在窗口内的 stockNews 条目并回当前 data.js'
                         '（不联网）。用于接口抖动导致深度掉下去后的《只增不减》修补')
    ap.add_argument('--allow-drop', action='store_true',
                    help='显式放行 K9 环比闸门（单项跌幅 >10% 也写盘）。只在确认下降合法时用，'
                         '如收紧过滤规则后的首次全量重抓')
    ap.add_argument('--stats', action='store_true',
                    help='只打印 current data.js 的三项统计与历史基线（不联网、不写盘）')
    args = ap.parse_args()

    s = open(DATA_JS, encoding='utf-8').read()
    head = 'window.DASHBOARD_DATA = '
    D = json.loads(s[s.index('{'):s.rindex('}') + 1])

    if args.stats:
        cur = _sn_stats(D.get('stockNews'))
        hist = _load_stats_hist()
        print('[stock_news] 当前 data.js：标的 %d / 新闻 %d / 公告 %d' %
              (cur['stocks'], cur['news'], cur['notices']))
        if not hist:
            print('[stock_news] 无历史基线（%s 不存在）' % STATS_HIST)
        else:
            print('[stock_news] 最近 %d 次写盘：' % len(hist))
            for row in hist[-RATIO_PEAK_N:]:
                print('  %s  标的 %4d / 新闻 %5d / 公告 %4d' %
                      (row.get('at', '?'), row.get('stocks', 0),
                       row.get('news', 0), row.get('notices', 0)))
            _peak = {}
            for row in hist[-RATIO_PEAK_N:]:
                for k, _l in RATIO_FIELDS:
                    _peak[k] = max(int(_peak.get(k) or 0), int(row.get(k) or 0))
            print('[stock_news] 近 %d 次峰值：标的 %d / 新闻 %d / 公告 %d' %
                  (RATIO_PEAK_N, _peak.get('stocks', 0), _peak.get('news', 0),
                   _peak.get('notices', 0)))
        return

    if args.purge_stats:
        before = sum(len(v.get('news') or []) for v in (D.get('stockNews') or {}).values())
        n_del, n_hit, emptied, n_dup = purge_stats(D)
        after = sum(len(v.get('news') or []) for v in (D.get('stockNews') or {}).values())
        print(f'[stock_news] 存量清洗：正则类删 {n_del} 条（涉及 {n_hit} 只）/ '
              f'跨标的盘点稿删 {n_dup} 条 / 清空并摘除 {len(emptied)} 只 / '
              f'新闻 {before} → {after} 条')
        if emptied:
            print('[stock_news] 摘除：' + ','.join(sorted(emptied)[:30])
                  + (' …' if len(emptied) > 30 else ''))
        if args.dry:
            return
        with open(DATA_JS + '.tmp', 'w', encoding='utf-8') as f:
            f.write(head + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';\n')
        os.replace(DATA_JS + '.tmp', DATA_JS)
        print(f'[stock_news] 已写回 data.js（stockNews {len(D["stockNews"])} 只）')
        return

    if args.merge_depth:
        old = _snapshot_stock_news(args.merge_depth)
        cur = D.get('stockNews') or {}
        b_n = sum(len(v.get('news') or []) for v in cur.values())
        b_t = sum(len(v.get('notices') or []) for v in cur.values())
        n_fill, n_back, n_dup = merge_depth(cur, old)
        a_n = sum(len(v.get('news') or []) for v in cur.values())
        a_t = sum(len(v.get('notices') or []) for v in cur.values())
        print(f'[stock_news] 深度并集（快照 {os.path.basename(args.merge_depth)}）：'
              f'补 {n_fill} 条 / 整只复活 {n_back} 只 / 去噪 {n_dup} 条 / 新闻 {b_n} → {a_n} / '
              f'公告 {b_t} → {a_t} / 标的 {len(cur)} 只')
        if args.dry:
            return
        with open(DATA_JS + '.tmp', 'w', encoding='utf-8') as f:
            f.write(head + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';\n')
        os.replace(DATA_JS + '.tmp', DATA_JS)
        print(f'[stock_news] 已写回 data.js（stockNews {len(D["stockNews"])} 只）')
        return

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
    # ⚠️ K9 顺序硬约束：基线必须在 merge_depth **之前**取。
    #    merge_depth 头一句就是 `_drop_crossdup(old_sn)`，会**就地**删掉旧快照里的
    #    跨标的盘点稿；取晚了基线偏低，该报的断崖就报不出来（和「先截断再去重」同类的坑）。
    base_stats = _sn_stats(old)
    D['stockNews'] = news
    if old and not NEWS_FORCE:
        # 合并：本次抓到的覆盖旧值；本次没抓到但有旧值的保留（接口抖动不至于清空）。
        # ⚠️ NEWS_FORCE=1（全量强制重抓）时**不做兜底** —— 否则改了过滤/归一化逻辑后，
        #    新结果为空或变少的标的会保留旧条目，噪音永远清不掉。
        #    2026-10-19 实测：华阳新材旧条目含「涨停股复盘：16股封单超亿元」
        #    「187只股中线走稳」等盘面统计类，加严过滤后 news=0 却被兜底保留。
        # R100z205：从「整只兜底」升级成 merge_depth 的**逐条并集** —— 原因见函数注释
        #    （整只兜底救不了「公告 3 条变 0 条」这种半只残废，实测掉过 177 条）。
        # R100z206：整只补回不再看 targets（池子轮换 ≠ 该清空弹窗），改看「旧条目是否还在窗口内」。
        n_fill, n_back, n_dup = merge_depth(D['stockNews'], old)
        if n_fill or n_back:
            print(f'[stock_news] 深度并集：补回 {n_fill} 条窗口内条目、'
                  f'整只复活 {n_back} 只（今天没进 targets 但旧内容仍有效）、'
                  f'跨标的去噪 {n_dup} 条')

    # ---- K9 环比闸门：宁可少一天新内容，也不让弹窗集体变空 ----
    after = _sn_stats(D['stockNews'])
    hist = _load_stats_hist()
    peak = {}
    for row in hist[-RATIO_PEAK_N:]:
        for k, _l in RATIO_FIELDS:
            peak[k] = max(int(peak.get(k) or 0), int(row.get(k) or 0))
    waive = ''
    if args.allow_drop:
        waive = '--allow-drop'
    elif NEWS_FORCE:
        waive = 'NEWS_FORCE=1（全量重抓，本来就会变）'
    elif args.limit:
        waive = '--limit 联调'
    breach, rows = guard_ratio(after, base_stats, peak=peak, allow=bool(waive))
    if base_stats['stocks']:
        print('[stock_news] K9 环比：\n' + _fmt_rows(rows))
        if waive:
            print(f'[stock_news] K9 已豁免（{waive}）')
    if breach:
        n_txt = sum(1 for b in breach if b['key'] == 'news')
        n_tot = sum(b['base'] - b['cur'] for b in breach)
        print('[stock_news] ================= K9 环比闸门：拦下本次写盘 ================',
              flush=True)
        for b in breach:
            print('  ✗ %s %d → %d（%+.1f%%）'
                  % (b['label'], b['base'], b['cur'], b['pct'] * 100), flush=True)
        print(f'  合计比基线少 {n_tot} 条，单项超阈值 {RATIO_MAX_DROP:.0%}'
              f'{"（其中新闻类 %d 项）" % n_txt if n_txt else ""}', flush=True)
        print('  data.js 保持上一版不动 —— 这是有意的：宁可一天没新内容，'
              '也不让所有弹窗一起变空（用户实报过「内容变少」）。', flush=True)
        print('  常见原因：① 池子轮换把整只标的带走 ② 接口整片超时/限流 '
              '③ 过滤规则写宽了（误杀真消息）', flush=True)
        print('  处置：先 `python3 stock_news.py --stats` 看基线，确认下降合法后加 '
              '--allow-drop 重跑：', flush=True)
        print('        STOCK_NEWS_BUDGET=600 python3 stock_news.py --allow-drop', flush=True)
        print('  报告：%s' % ALERT_JS, flush=True)
        try:
            os.makedirs(os.path.dirname(ALERT_JS), exist_ok=True)
            with open(ALERT_JS, 'w', encoding='utf-8') as f:
                json.dump({'at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           'base': base_stats, 'peak': peak, 'after': after,
                           'breaches': breach, 'rows': rows, 'waived': waive},
                          f, ensure_ascii=False, indent=1)
        except Exception as e:
            print(f'  （告警文件写入失败：{e}）', flush=True)
        return 3        # 与「抓取失败」区分：闸门拦截是确定性判断，不是网络问题

    # R100z165（L10 写盘原子化）：先 .tmp 再 os.replace
    _tmp = DATA_JS + '.tmp'
    with open(_tmp, 'w', encoding='utf-8') as f:
        f.write(head + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';\n')
    os.replace(_tmp, DATA_JS)
    _save_stats_hist(hist, after)   # 只在写盘成功后记基线（拦下的那次不能进历史）
    print(f'[stock_news] 已写回 data.js（stockNews {len(D["stockNews"])} 只）')


if __name__ == '__main__':
    # ⚠️ 必须传 sys.exit：K9 拦截时 main() 返回 3，裸 `main()` 会把退出码扔掉，
    #    自动化那边永远看到 exit 0 —— 闸门报了半天，「失败」这件事却传不出去。
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_pipeline.py · 七自动化 + 流水线一次性机器验收（R100z17）

把此前「靠人逐条翻脚本 / 翻 prompt」的体检方式，改成机器可执行的验收清单。

用法:
    python3 audit_pipeline.py             # 全量验收（推荐，几十秒）
    python3 audit_pipeline.py --json      # 机器可读输出
    python3 audit_pipeline.py --deep      # 追加只读脚本冒烟（慢，走网络）

退出码:
    0 = 无 FAIL   1 = 存在 FAIL   2 = 用法错误 / 基线文件缺失

验收分组:
    G1  语法闸门      所有 *.py 编译 / *.sh 语法
    G2  脚本契约      prompt 引用的脚本与 flag 在代码里真实存在
    G3  校验纪律      凡调 validate_stocks.py 必须带 --no-fix --scope
    G4  基线一致性    audit_expectations.json 自身完整（禁项编号连续等）
    G5  源码纪律断言  R100z16 修复项固化（merge 保留 star / validate 只读 / 黑名单 scope）
    G6  语义闸门      cal_factor 对节后首日判定正确
    G7  校验器实跑    五个 scope 全跑并留痕（不硬拦历史 FAIL）
    G8  数据结构      前端引用的顶层键 / 脚本必需顶层键是否齐备
    G9  空内容扫描    空串与空数组（带结构化白名单，豁免项需写明理由）
    G9b 前端兜底反证   被豁免的空字段必须①前端不引用②渲染前有真值兜底，
                      反证「空内容安全」这件事本身是可机器复验的，不靠人翻 index.html
    G10 日期新鲜度    aiPrediction/openOutlook/ashare/us 日期 vs 最近交易日；
                      openOutlook.pos / duanban.star 按「下一个交易日是否已到」分流
    G11 只读冒烟(--deep)
    G12 半死链路      data.js 顶层产出必须登记「流程主人」+ 被自动化点名 + 脚本零孤儿
    G13 节假日表      common 与 cal_factor 两份手工节假日表同源等价 + 覆盖到期前 30 天 WARN 提醒跨年更新
    G14 路径断裂      prompt 内脚本绝对路径必须含完整 dashboard/ 或走 cd 相对调用（禁「缺 dashboard/ 裸路径」）
    G16 叙事深度      防 AI 偷懒·叙事主字段字数硬下限（A 股≥240 / 美股≥200 / outlook≥300 等，低于即 FAIL 拦部署）
    G17 防模板复读    防 AI 偷懒·同一句 ≥14 字在 ≥3 个叙述字段复现 = 万能模板尾巴（R100z96：us 页 34 条统一收尾事故），FAIL 拦部署
    G22 要闻等级噪音  五节要闻必须有 level（0重点/1关注/2常规）且标题不含盘面统计噪音（R100z157，WARN 级复核）
    G23 跨票措辞复读  连板/断板 story 的同一句（≥14 字）在 ≥3 只票且 ≥30% 池内重复 = 同段模板化（R100z157，WARN 级；
                      G17 只管「跨字段」，管不到「每票一个字段」的同段复用）
    G24 未核查披露    黑名单维度没取到数据（verdict != full）时，渲染文本里不得出现
                      「已核查无风险」「黑名单全部通过」等绝对安全表述（R100z160，FAIL）
    G25 记忆层体检    工作区 MEMORY.md 是「注入层」，超限会被 inject 截断导致硬约束读不全
                      （2026-10-21 实测 19.9KB 已被截断）→ 超 8KB 或路由表缺失即 WARN（R100z161）
    G26 机械池键对齐   stock_pool.json 的 bySector 键集必须与 aiPrediction.sectors 的板块名对得上；
                      对不上时 check_stock_pool 对该板块整轮失明，「④ 断板必须取自机械池」
                      静默降级为提示（R100z163，WARN）
    G27 记忆可达性     MEMORY.md 路由指向的 24.x 节必须真能 grep 到标题；「届时」紧贴过期日期
                      仍以未来时态陈述；>30 天日志未蒸馏（R100z166）
    G28 新鲜度一致性   页头 updatedAt 必须 = 各分格戳的最大值（顶层字段曾被 5 个脚本互相覆盖，
                      实测 A股 17:20 已更新、页头还停在 16:01）；ashare.updatedAt 必须与
                      tradeDate 同日（该格原先由 AI 手写，长期挂着 10-20 的旧值）；
                      分格清单与 refresh_stamp.py 的 SLOTS 逐项一致（R100z167，FAIL 拦部署）
    G29 名称与风险卫生 ① data.js 里每只个股的简称必须与行情源一致（AI 凭记忆写，实测挂着
                      韦尔股份/银泰黄金/郑煤机/人福医药/荣华实业等 7 个旧名，两只已戴帽
                      ST 却看不出来）；② 硬风险 red 标的不得留在推荐位（603330 在 10-09
                      就公告减持，到 10-21 还挂在断板确认池和量化筛选里）——
                      分别由 sync_names.py / risk_gate.py 保证，本闸复验（R100z168）
    G30 展示硬偏好     把用户说过的审美红线机器化，避免「闸门全绿但截图上很丑」再发生：
                      ① 风险徽章三档符号必须统一 ⚠️（上一版自作主张改成 ！/？ 被否）；
                      ② 三档底色必须**亮度单调递减**且相邻差 ≥35（10px 小圆点上黑与深灰不可分，
                         「颜色分档」会退化成「一个通道」）；③ 徽章字号 ≥11px；
                      ④ 涨红跌绿两色常量必须在；⑤ 断板页徽章图例三档必须齐（R100z171）
    G39 开盘预测子页  三块齐全 + DOM 顺序（前瞻→AI预测→预案）+ od 为默认子页且子页自带
                      tab 菜单（否则 dbSubTab 记住 od 后整页无入口可切走）（R100z162，FAIL）
    G40 复盘页与槽位  🌟 复盘台账独占页（三卡 + 独占 CSS + hash 直达）与悬浮组四槽位对齐
                      （返回按钮按槽位落位、槽距 offsetTop 实测；旧贴底写法会让第 4 个按钮
                      掉出组外）（R100z162，FAIL）
    G41 视觉规范全链  标题/账户块/AI 关系行/四入口四返回可拖+拖后不吞点击/字体继承/字号 10 档
    G42 设计令牌      字号/圆角必须走 --fs-* / --radius-* 变量，禁止硬编码（白名单里的也算）
    G43 股票卡排版    「名称+代码」不可断（.nm 包裹）+ 机械池只标少数派且用压角圆点不占布局
    G44 僵尸镜像节点  ashare 下不得存在与顶层同名的容器——旧副本没删干净时，它既不被渲染
                      也不被校验（G12/G16 只查顶层），却可能被 prompt 继续喂着长大（R100z171，FAIL）
    G45 弹窗导航分组  列表点开的 K 线弹窗必须**先建组再开**（stkOpenIn），openStkModal 入口须
                      兜底关箭头（stkSyncNav）；内联 onclick 直开弹窗一律禁止——那会让 ←/→
                      翻上一个读者点过的清单（R100h 犯过一次、R100z200 又犯一次，FAIL）
    G46 板块名可解析   预测板块名（AI 手写主题名）必须能落到 allBoards 的行业名；别名表
                      board_alias.json 与 index.html 的 pctAlias 逐字同源（R100z224，FAIL）
    G47 买入清单真值   duanban.star.buyList 是**唯一会被当下单指令读**的字段，查的不是
                      「有没有字段」而是「会不会让人做错事」：state 与现价必须自洽、
                      票必须能回溯到对应池（防凭空造票）、买区上下沿不得颠倒、只能沪深主板、
                      断板反包不得编目标价；外加生成端/渲染端接线断言（R100z232，FAIL）
"""
import ast
import json
import os
import re
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PYBIN = sys.executable
# R100z55：原为无兜底硬编码路径，托管 node 升版后 G-* 闸门里的 node -e 会整体失败。
# 审计闸门是 deploy 前的唯一放行口，这里断掉等于误判 FAIL（进而禁止上线），必须留兜底。
_NODE_CAND = "/Users/loccco/.workbuddy/binaries/run-node"
NODE = _NODE_CAND if os.path.exists(_NODE_CAND) else "node"
VFLAGS = ("--no-fix",)

# R100z64：pyflakes 装在托管 venv 里。它就是靠人肉扫抓不到那类 bug 的答案——
# compileall 只验语法，「undefined name」要运行到那一行才 NameError，而流水线
# 可能因为休市压根没跑过，把炸弹埋到复牌首日才炸（本轮实测踩中，10-08 会崩流水线）。
_PYF_CANDS = (
    "/Users/loccco/.workbuddy/binaries/python/envs/default/bin/python",
    "python3", "python",
)

# R100z17：data.js 里「结构性可空」的字段——不是缺陷，是脚本/前端设计预期。
# 每条豁免必须给出豁免理由，且由 G9b 强制反证「前端不会渲染出空白」：
#   ① 前端根本不引用该字段（纯数据层字段，空值无害）
#   ② 前端引用处全部带真值兜底（if/三元/|| ''/typeof==='string' 等）
# 以后若要新增豁免，必须同时补一条 G9b 能验证的理由，不许只往表里塞名字。
EMPTY_WHITELIST = [
    # --- 被动技术池：脚本只出形态/量化，AI 叙事由 19:30 叙事步 AI 写回，脚本阶段天然空 ---
    (r"harmonic\.(confirmPool|watchPool|failPool)\[\d+\]\.(story|deduce)",
     "R100z4q：harmonic_detect 从旧池继承叙事，新形态条目脚本阶段无叙事，等 AI 写回"),
    (r"(accumulation|breakoutPool)\.(scored|passed|strong|watch)\[\d+\]\.(story|deduce)",
     "R100z21：scored 全量 + strong/watch 分池副本同构，AI 叙事位脚本阶段为空、由 AI 写回"),
    (r"(accumulation|breakoutPool)\.(scored|strong|watch)\[\d+\]\.sector",
     "R100z21：strong/watch 分池副本沿用 scored 的板块字段，sec_map 查不到时留空"),
    # --- 结构化可选字段：脚本写 foo or ''，无值时该维度不存在 ---
    (r"(harmonic|accumulation|breakoutPool)\.(confirmPool|watchPool|scored|passed|failPool)\[\d+\]\.sector",
     "sec_map 查不到板块时留空；前端按板块分组，无板块归入默认组不渲染空标签"),
    (r"duanban\.(confirmed|watching)\[\d+\]\.bullType",
     "R91p 打标加权制：个股×2/板块×1，既非利好也非利空即中性→留空"),
    (r"duanban\.(confirmed|watching)\[\d+\]\.(boardPctZt|boardPctToday|bkCode)",
     "非板块（个股）断板条目没有板块涨幅/板块代码，结构可选字段"),
    (r"ashare\.lianban\[\d+\]\.hybk",
     "连板条目无对应行业板块时为空；前端按行业分组并做空组过滤"),
    (r"ashare\.lianban\[\d+\]\.open_num",
     "非一字/无换手口径的连板条目留空；前端显示 '-' 兜底"),
    (r"us\.breadth\.",
     "R98i 预留口径位：非交易日/脚本未覆盖时为空，前端整体隐藏该卡"),
    # --- 结构性预留 / 已下线模块 ---
    (r"themePicks$",                    # R98j 已下线
     "R98j：主题速览模块已下线，字段保留防旧键丢失"),
    (r"excluded$",                      # 结构预留
     "无剔除项时为空数组"),
    (r"(harmonic|accumulation|breakoutPool)\.summary$",
     "脚本可能不给名，前端真值兜底"),
    (r"aiPrediction\.verification",     # 非交易日/null 属正常
     "08:30 未写 verification 时前端不渲染验证卡"),
    (r"openOutlook\.verification",      # 08:30 未写 pos 时 [SKIP]
     "同上，未写 pos 时 score_openoutlook 直接 [SKIP]，前端不渲染"),
    (r"aiPrediction\.sectors\[\d+\]\.exp",
     "R100z11：非预期型板块不落 exp，expBadge 返回空串不渲染"),
    (r"(bullish|bearish)$",
     "R98i：外盘方向每股独立写，无观点即空"),
    (r"harmonic\.failPool$",     # 当天无谐波失效标的即空数组
     "R100z27：失效池无新增即空数组，属正常日度状态（不是脚本漏跑）"
     "；前端 dbFailHtml/谐波失效区统一走 if(!x.length) 兜底显示『暂无失效标的』"),
    (r"policySignals\[\d+\]\.(sectors|targets)",
     "R100z107b：政策解码无识别到受益板块/量化目标时为空数组，前端按 '-' 显示"),
    # R100z190：强板块落后股——「今天没有强板块」是这套规则的正常输出，不是脚本漏跑。
    (r"duanban\.leadLag\.(picks|sectors)$",
     "R100z190：lead_lag 约六成交易日无候选（回测 34/90 日有信号），picks/sectors 为空属正常。"
     "R100z225 起前端**不再引用 picks**（今日清单那半已整块删除，只留复盘台账的回测块），"
     "属理由①「纯数据层字段、空值无害」；它仍有下行读者——lead_lag 同时把当日候选登记进 "
     "signal_ledger.entries[date].leadLag，由 signal_attribution 逐日回算真实胜率，不是僵尸字段"),
    # R100z218：陈学长池——他的口径比断板反包严得多（连板≥2 + 断板日放量≥2 倍），空池是常态。
    (r"chenPool\.(watching|failPool|failedDates)$",
     "R100z218：观察池「今天没有走到差一根K线的标的」、失效池「今天没有标的失效」都是正常日度状态；"
     "failedDates 为空＝没有 K 线缺口（有值才渲染⚠️行，空值不渲染，是正确方向）。"
     "前端 chenPoolBlock 用 `if (!list.length) return ... 暂无标的` 兜底，dbFailHtml 用 "
     "`if (!failPool.length)` 兜底显示『暂无失效标的』——三条路径都不渲染空白"),
    (r"^policySignals$",
     "R100z107b：当日无政策源新闻时 policySignals 为空数组（正常）"),
    (r"atrStops\.skipped$",
     "R100z160：atr_stops 没有「K 线不足被跳过」的标的时为空数组（正常日度状态，不是漏跑）；"
     "被跳过的票根本不会进 atrStops.items，前端只读 items、不引用 skipped"),
    (r"tradeDesk\.portfolio\.warn$",
     "R100z160：组合仓位既没超单板块上限、也没超组合目标仓位时为空数组（这正是想要的状态）；"
     "前端 renderTradeDesk 用 (pf.warn||[]).forEach 兜底，空数组不渲染任何预警条"),
]

DATA_KEYS_REQUIRED = [
    "us", "ashare", "duanban", "panorama", "aiPrediction", "openOutlook",
    "stkKlines", "stkKlineNames", "harmonic", "accumulation", "breakoutPool",
]

VALIDATE_SCOPES = ("all", "ai", "us", "star")


# ---------------------------------------------------------------- G12 表（产出 · 生产方 · 流程主人）
# R100z62：G12「半死链路」闸门的登记表——每个 data.js 顶层节点必须登记「流程主人」。
#
# 为什么要这张表（这就是此前「每查一次都能再挖出新半死链路」的根因）：
#   改代码与同步自动化流程之间，此前没有任何机器可复验的约束。脚本写了个新顶层节点、
#   前端接了线，但五个自动化的 prompt 一个字没提，它就这么静静地摆着，直到下一次体检
#   换了个视角才被翻出来（fullScan / weekendNews / stopTrack / sectorMap 各踩过一次）。
#   闸门 G2 只查「prompt 引用的脚本存不存在」——那是单向检查，反向的「产出有没有被消费」
#   没人守，所以半死链路能长期存活。
#
# 字段：
#   require : 看板自动化的 prompt 必须点名的关键词（大小写不敏感）。留空 = 豁免点名。
#   proof   : 豁免的机器可复验反证，成立后豁免才作数（不写 = 不校验反证）。
#               intermediate：脚本间中间产物（前端不引用它）
#               mapping     ：静态映射表（dict[str,str]，内容不随交易日变化）
#   note    : 为什么是这个主人 / 为什么豁免
#
# 新增 data.js 顶层节点时：**必须在此登记**，G12 会直接 FAIL。不许靠「跑一遍闸门没报错」
# 蒙混过关，也不许往表里塞名字却不写 note。
PRODUCT_OWNERSHIP = {
    "aiPrediction": {
        "require": ("aiPrediction", "AI 预测", "预测卡"),
        "note": "19:30 叙事步写回 AI 叙事/概率，前端 AI 预测卡直接用",
    },
    "ashare": {
        "require": ("ashare", "A 股", "A股", "大盘"),
        "note": "A 股大盘与板块行情的主节点",
    },
    "us": {
        "require": ("us", "美股"),
        "note": "美股行情节点，07:00 / 08:30 两条任务各自负责一段",
    },
    "panorama": {
        "require": ("panorama", "全景", "韩", "日"),
        "note": "韩日早盘全景，覆盖在全景卡",
    },
    "duanban": {
        "require": ("duanban", "断板"),
        "note": "断板反包确认池/观察池/失效池",
    },
    "chenPool": {
        "require": ("chenPool", "陈学长"),
        "note": ("R100z218：陈学长断板反包**第二套并列口径**（chen_pool.py 产出，严格池/放宽池/观察池）。"
                 "与 duanban 并列而非取代——用户 2026-10-25 明确要求「建一个陈学长技术指标，"
                 "而不是更新断板反包」，目的是让两套指标用同一结算口径并行留痕，一周后比胜率。"
                 "禁把它改写成 duanban 的参数分支。"),
    },
    "harmonic": {
        "require": ("harmonic", "谐波"),
        "note": "谐波形态池",
    },
    "accumulation": {
        "require": ("accumulation", "吸筹"),
        "note": "吸筹形态池",
    },
    "breakoutPool": {
        "require": ("breakoutPool", "横盘启动", "breakout_pool"),
        "note": ("R100z233：横盘启动池（breakout_pool.py，**涨停锚口径 v2**）——"
                 "T 日涨停为锚 → 洗盘 5~30 日、均量/涨停前 5 日均量≤0.80 → 破底允许 1~2 日"
                 "收回 → 收盘较涨停板 +30% 即算启动、移出本池；证伪价＝涨停日最低×0.98。"
                 "**取代九门 powerScreen**，也取代本模块 v1 的「60 日几何平台」（v1 无涨停锚，"
                 "用户 4 张正样本 0/4 通过，且 +15% 命中率 20.3% 低于随机的 23.5%）。"
                 "v2 命中率 33.5%（随机 23.5%）。不设目标价——止盈由用户自己决定。"),
    },
    "openOutlook": {
        "require": ("openOutlook", "开盘前瞻", "前瞻"),
        "note": "开盘前瞻节点",
    },
    "stkKlines": {
        "require": ("stkKlines", "K 线", "K线"),
        "note": "K 线内联数据，前端弹窗取数用",
    },
    "stkKlineNames": {
        "require": ("stkKlineNames",),
        "note": "K 线代码对照表，与 stkKlines 配套",
    },
    "stopTrack": {
        "require": ("stopTrack", "止损跟踪"),
        "note": "R100z61b 新增的止损台账统计卡，只能由 stop_tracker.py 刷写，AI 禁手写",
    },
    "fullScan": {
        "require": (), "proof": "intermediate",
        "note": "脚本间中间产物：读方是 harmonic_detect/accumulation_score，不进前端渲染",
    },
    "weekendNews": {
        "require": (), "proof": "intermediate",
        "note": "脚本间中间产物：读方是 push_notify/validate_stocks，不进前端渲染",
    },
    "sectorMap": {
        "require": (), "proof": "mapping",
        "note": "R100v2 新浪行业代码→行业名静态映射（数千条），embed_klines 写、前端板块兜底读；"
                "内容不随交易日变化，故流程不逐日核对（但仍须是 dict[str,str] 静态映射形态）",
    },
    "updatedAt": {
        "require": (),
        "note": "整版生成时间戳，前端只渲染成文案，不参与任何决策，豁免点名",
    },
    "policySignals": {
        "require": ("policySignals", "政策解码", "政策信号", "enrich_policy"),
        "note": "R100z107b：enrich_policy.py 解码新闻标题为政策信号，19:30 叙事步在新闻写回后调用写回；前端政策信号解码面板渲染",
    },
    "macroCalendar": {
        "require": (),
        "note": "R100z117 ⑥：macro_calendar.py 产出的未来 45 日宏观事件日历（FOMC/GDP/CPI/PPI/MLF/LPR/工增固投社零），"
                "由 deploy.sh 流水线调用（零网络纯函数，兜底写空 exit 0）；前端「📅 宏观事件日历」卡渲染（renderMacroCalendar）",
    },
    "strategy": {
        "require": (),
        "note": "R100z117 ①：strategy_engine.py 聚合政策/AI预测/断板反包三类信号为盘前关注清单+仓位纪律+回测反馈闭环，"
                "由 deploy.sh 流水线调用（读 cal_factor/calibration_state/portfolio_curve，兜底写空 exit 0）；"
                "前端「🎯 盘前策略」卡渲染（renderStrategy）",
    },
    "stockNews": {
        "require": ("stock_news.py", "个股消息库", "stockNews"),
        "note": "R100z149：stock_news.py 抓东财个股公告 + 资讯搜索，按 code 汇总成 {code:{name,notices,news}}，"
                "覆盖断板（含失效池）/谐波/吸筹/横盘启动/连板/板块成分/AI预测/要闻影响标的；"
                "前端弹窗（网页 db-modal）统一渲染「个股消息」段。"
                "16:00 数据步第 4.5 步点名（四模块之后、汇报之前；R100z162 更正：旧注记为「断板主生成前」，"
                "与 16:00 实际步骤顺序（2 断板 → 3 要闻 → 4 四模块 → 4.5 个股消息）不符）。",
    },
    "atrStops": {
        "require": ("atrStops", "atr_stop", "ATR"),
        "note": "R100z160：atr_stop.py 逐票算 ATR(14) 自适应止损（20 日结构位、k 系数、2.5%~10% 宽度钳制、"
                "风险预算仓位、R 倍数目标），是**全站止损口径的唯一权威源**；16:00 数据步点名运行，"
                "前端弹窗退出纪律（exitForCode → atrExitOf）与交易执行台共用同一份数字。",
    },
    "tradeDesk": {
        "require": ("tradeDesk", "signal_rank"),
        "note": "R100z160：signal_rank.py 五因子打分（动量/趋势/量能/位置/波动）+ 三项硬性否决 + 排名取前 8，"
                "16:00 数据步点名运行；前端「今日交易执行台」卡渲染（renderTradeDesk）。",
    },
    "signalPerf": {
        "require": ("signalPerf", "signal_attribution"),
        "note": "R100z160：signal_attribution.py 按指标来源回算 T+1/3/5/10 收益与超额、给建议权重，"
                "16:00 数据步点名运行；前端「指标表现 · 谁准谁不准」卡渲染（renderSignalPerf）。",
    },
    "forecastScore": {
        "require": ("forecastScore", "forecast_scorecard"),
        "note": "R100z161：forecast_scorecard.py 把 calibration_log.json 的板块预判台账（log）与开盘前瞻台账（open）"
                "结算成跨日命中率：按置信度、按方向、近 5 日分层，并**按阈值自动生成使用建议**（不许人手写结论）。"
                "16:00 数据步点名运行；前端「预测复盘 · 谁准谁不准」卡渲染（renderForecastScore）。"
                "存在理由：此前页面只有 AI 自己写的当日总结，没有累计命中率，用户无从判断该信几分。",
    },
}


class Report:
    def __init__(self):
        self.items = []      # (group, level, msg)
        self.counts = {"FAIL": 0, "WARN": 0, "PASS": 0}

    def add(self, group, level, msg):
        self.counts[level] = self.counts.get(level, 0) + 1
        self.items.append((group, level, msg))

    def ok(self, g, m): self.add(g, "PASS", m)
    def warn(self, g, m): self.add(g, "WARN", m)
    def fail(self, g, m): self.add(g, "FAIL", m)
    # R100z60 清理：原 Report.info() 全项目零调用点（只有这行 def 自己出现），
    # 连带 counts["INFO"] 恒为 0 —— 它是个「永远跑不到的分支 + 一辈子是 0 的计数器」。
    # 真要加提示信息，直接用 ok()/warn() 更省心，别再造一个只进不出的级别。

    def exit_code(self):
        return 1 if self.counts.get("FAIL", 0) else 0


# ---------------------------------------------------------------- 工具
def sh(cmd, timeout=120, cwd=HERE):
    try:
        p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", "TIMEOUT"
    except Exception as e:                                  # noqa: BLE001
        return 127, "", str(e)


def load_data_json():
    """用 node 把 data.js 的 window.DASHBOARD_DATA 解析成 dict。"""
    js = ("const fs=require('fs');global.window=global;"
          "eval(fs.readFileSync('data.js','utf8'));"
          "process.stdout.write(JSON.stringify(window.DASHBOARD_DATA));")
    tmp = "/tmp/_audit_data.json"
    code, out, err = sh(f"{NODE} -e \"{js}\" > {tmp} 2>/dev/null", timeout=120)
    if code != 0 or not os.path.exists(tmp):
        raise RuntimeError(f"data.js 解析失败 code={code} {err[:200]}")
    with open(tmp, encoding="utf-8") as f:
        return json.load(f)


def argparse_flags(path):
    """抽取脚本支持的命令行 flag。

    两种实现都算数：① argparse 的 add_argument 字面量；② 手写 sys.argv 解析
    （validate_stocks.py 用的就是后者，只认字面量）。
    """
    flags = set()
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
    except Exception:                                       # noqa: BLE001
        return flags
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "add_argument":
            continue
        for a in node.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("-"):
                flags.add(a.value.split("=")[0])
    # 手写 argv 解析（形如 '--no-fix' 字符串比较）同样视为支持
    for tok in ("--no-fix", "--scope", "--json", "--write", "--date", "--days",
                "--limit", "--module", "--out", "--label"):
        if f"'{tok}'" in src or f'"{tok}"' in src:
            flags.add(tok)
    return flags


def walk_paths(obj, prefix=""):
    """产出 (path, value)：路径形如 us.bullNews / aiPrediction.sectors[0].name"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            yield p, v
            yield from walk_paths(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_paths(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


# ---------------------------------------------------------------- G1
def _pyflakes():
    """跑 pyflakes。返回 (clean, 用的解释器 basename, 输出)。

    退出码 0 = 零告警，1 = 有告警（输出到 stdout）。三个解释器都找不到就放过，
    不硬拦——闸门断掉等于误判 FAIL。
    """
    for b in _PYF_CANDS:
        c, _, _ = sh(f"{b} -c 'import pyflakes'")
        if c != 0:
            continue                       # 这个解释器没装 pyflakes，换下一个
        c, out, _ = sh(f"{b} -m pyflakes *.py", timeout=180)
        return c == 0, b, (out or "").strip()
    return True, None, ""


def g1_syntax(r):
    bad = []
    for fn in sorted(os.listdir(HERE)):
        if fn.endswith(".py"):
            c, _, _ = sh(f"{PYBIN} -m py_compile {fn}")
            if c != 0:
                bad.append(fn)
        elif fn.endswith(".sh"):
            c, _, _ = sh(f"bash -n {fn}")
            if c != 0:
                bad.append(fn)
    n_py = len([f for f in os.listdir(HERE) if f.endswith(".py")])
    n_sh = len([f for f in os.listdir(HERE) if f.endswith(".sh")])
    if bad:
        r.fail("G1", f"语法错误：{', '.join(bad)}")

    # pyflakes：抓 compileall 看不见的 undefined name / 未用 import / 重定义
    clean, bin_, out = _pyflakes()
    if not clean:
        r.fail("G1", "pyflakes 静态检查未通过（undefined name / 未用 import / 重复定义）："
                     + out.replace("\n", " | ")[:400])
    elif not bad:
        tag = f"via {os.path.basename(bin_)}" if bin_ else "环境无 pyflakes，跳过"
        r.ok("G1", f"{n_py} 个 .py + {n_sh} 个 .sh 语法全部通过，pyflakes 零告警 {tag}")


# ---------------------------------------------------------------- G2/G3
def g2_g3_contract(r, exp):
    autos = exp.get("automations", [])
    if not autos:
        r.fail("G4", "audit_expectations.json 缺 automations，基线需重抽")
        return
    missing_total, flag_bad_total, v裸 = [], [], []
    for a in autos:
        aid = a.get("id", "?")[:12]
        for s in a.get("scripts", []):
            f = s.get("file")
            if not f or not os.path.exists(os.path.join(HERE, f)):
                missing_total.append(f"{aid}:{f}")
                continue
            real = argparse_flags(os.path.join(HERE, f))
            for fl in s.get("flags", []) or []:
                if fl.startswith("-") and fl not in real:
                    flag_bad_total.append(f"{f} {fl}")
            # G3 校验纪律：validate_stocks.py 必须带 --no-fix --scope
            if f == "validate_stocks.py":
                fl = set(s.get("flags", []) or [])
                if "--no-fix" not in fl:
                    v裸.append(f"{aid}: 缺 --no-fix（默认会写回 data.js）")
                if "--scope" not in fl:
                    v裸.append(f"{aid}: 缺 --scope（会把 us/ashare FAIL 记到本任务账上）")
                elif fl and any(x.startswith("--scope=") for x in fl):
                    pass
                else:
                    sc = [x for x in fl if x and not x.startswith("-")]
                    if sc and sc[0] not in VALIDATE_SCOPES:
                        v裸.append(f"{aid}: scope 非法 {sc[0]}")
    if missing_total:
        r.fail("G2", "prompt 引用了不存在的脚本：; ".join(missing_total))
    else:
        r.ok("G2", f"{len(autos)} 份 prompt 引用的脚本全部存在于磁盘（无悬空引用）")
    if flag_bad_total:
        r.fail("G2", "prompt 用了脚本不支持的 flag：; ".join(sorted(set(flag_bad_total))))
    else:
        n_flag = sum(len(s.get("flags") or []) for a in autos for s in a.get("scripts", []))
        r.ok("G2", f"{n_flag} 个 flag 全部在对应脚本 argparse 中真实声明")
    if v裸:
        r.fail("G3", "validate 校验纪律缺失 → " + " | ".join(v裸))
    else:
        n_v = sum(1 for a in autos for s in a.get("scripts", []) if s.get("file") == "validate_stocks.py")
        r.ok("G3", f"{n_v} 处 validate_stocks.py 调用全部带 --no-fix --scope")

    for s in autos:
        pass
    # 职责边界声明完整性
    for a in autos:
        if not a.get("writesFields") or not a.get("neverWrites"):
            r.fail("G4", f"{a.get('id','?')[:12]} 缺 writesFields/neverWrites 声明")
    else:
        r.ok("G4", f"{len(autos)} 份 prompt 均已声明写入范围与禁触字段")


# ---------------------------------------------------------------- G4 基线
def _cirkoumber(ch):
    """①→1 … ⑳→20，㉑→21 … ㉞→54"""
    o = ord(ch)
    if 0x2460 <= o <= 0x2473:
        return o - 0x2460 + 1
    if 0x3251 <= o <= 0x325E:
        return o - 0x3251 + 21
    return None


def g4_baseline(r, exp):
    autos = exp.get("automations", [])
    gaps, no_mc = [], []
    counts = []
    for a in autos:
        nums = []
        for x in (a.get("forbidden", []) or []):
            for ch in str(x):
                v = _cirkoumber(ch)
                if v:
                    nums.append(v)
        nums = sorted(set(nums))
        counts.append(len(nums))
        if nums and nums != list(range(1, len(nums) + 1)):
            gaps.append(f"{a.get('id','')[:12]}: 禁项编号跳号（{nums}）")
        if not a.get("mustContain"):
            no_mc.append(a.get("id", "?")[:12])
    n_mc = sum(len(a.get("mustContain") or []) for a in autos)
    if gaps:
        r.fail("G4", "; ".join(gaps))
    else:
        r.ok("G4", f"{len(autos)} 份 prompt 的「绝对禁止」编号无跳号/重复"
                   f"（{'/'.join(str(c) for c in counts)} 条，随拆分动态变化）")
    if no_mc:
        r.fail("G4", f"基线缺 mustContain 短语：{', '.join(no_mc)}")
    else:
        r.ok("G4", f"{n_mc} 条 mustContain 短语基线齐备（prompt 漂移时由基线重抽发现）")


# ---------------------------------------------------------------- G5 源码纪律
def g5_source_assertions(r):
    # 1) merge_duanban.py 必须保留最近交易日 star（R100z16 时序修复）
    p = os.path.join(HERE, "merge_duanban.py")
    if not os.path.exists(p):
        r.fail("G5", "merge_duanban.py 缺失")
    else:
        src = open(p, encoding="utf-8").read()
        if "timedelta" not in src:
            r.fail("G5", "merge_duanban.py 未导入 timedelta（star 过期判定会算错）")
        elif "> yesterday" not in src and ">= yesterday" not in src:
            r.fail("G5", "merge_duanban.py 未采用「>= yesterday 保留 star」逻辑（会清场 duanban.star）")
        else:
            r.ok("G5", "merge_duanban.py：保留最近交易日 star（od >= yesterday），不会清场")

    # 2) validate_stocks.py 必须支持 --no-fix/--scope，且 --no-fix 时不写盘
    p = os.path.join(HERE, "validate_stocks.py")
    if not os.path.exists(p):
        r.fail("G5", "validate_stocks.py 缺失")
        return
    src = open(p, encoding="utf-8").read()
    if "--no-fix" not in src or "--scope" not in src:
        r.fail("G5", "validate_stocks.py 不支持 --no-fix / --scope")
    else:
        r.ok("G5", "validate_stocks.py 支持 --no-fix / --scope")
    try:
        tree = ast.parse(src)
    except Exception:                                       # noqa: BLE001
        r.fail("G5", "validate_stocks.py 无法解析（AST 失败）")
        return
    # 找到写盘语句所在行号，与 `if not no_fix` 分支比较
    write_lines, guard_lines = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                ce = item.context_expr
                if isinstance(ce, ast.Call) and isinstance(ce.func, ast.Name) and ce.func.id == "open":
                    for a in ce.args:
                        if isinstance(a, ast.Constant) and a.value == "w":
                            write_lines.append(node.lineno)
        if isinstance(node, ast.If):
            t = ast.unparse(node.test)
            if "no_fix" in t and "not " in t:
                guard_lines.append((node.lineno, node.end_lineno))
    guarded = any(lo <= ln <= hi for ln in write_lines for lo, hi in guard_lines)
    if not write_lines:
        r.warn("G5", "validate_stocks.py 未发现写盘语句（行为变化需复核）")
    elif guarded:
        r.ok("G5", "validate_stocks.py 写盘语句位于 `if not no_fix` 分支内 → --no-fix 纯只读")
    else:
        r.fail("G5", "validate_stocks.py 存在不受 --no-fix 保护的写盘路径")
    # 3) 黑名单闸必须支持 scope 且扫到 us/ashare
    if "scope" not in src:
        r.fail("G5", "check_risk_blacklist 不支持 scope（us/ashare 侧漏闸）")
    elif "bullish" not in src or "bearish" not in src:
        r.fail("G5", "check_risk_blacklist 未覆盖 us.bullish/bearish")
    else:
        r.ok("G5", "check_risk_blacklist(D, scope=) 已扩扫 us/ashare 五节与 bullish/bearish")


# ---------------------------------------------------------------- G6 语义
def g6_calendar(r):
    c, out, err = sh(f"{PYBIN} cal_factor.py --date 2026-10-08 --json", timeout=60)
    if c != 0:
        r.fail("G6", f"cal_factor.py 对 2026-10-08 执行失败：{err[:120]}")
        return
    try:
        o = json.loads(out)
    except Exception:                                       # noqa: BLE001
        r.fail("G6", "cal_factor.py 输出非 JSON（--json 契约破损）")
        return
    if o.get("aShareClosed") is not False:
        r.fail("G6", "cal_factor 未把 2026-10-08 判为开市（节后首个交易日判定错误）")
    else:
        r.ok("G6", f"cal_factor：2026-10-08 开市（tags={o.get('tags')} pos={o.get('posText')}）")
    # 逐日序列：10-01~10-07 必须全休市、10-08 起开市
    seq = {}
    for d in ("2026-10-05", "2026-10-07", "2026-10-08", "2026-10-09"):
        _, o2, _ = sh(f"{PYBIN} cal_factor.py --date {d} --json", timeout=60)
        try:
            seq[d] = json.loads(o2).get("aShareClosed")
        except Exception:                                   # noqa: BLE001
            seq[d] = None
    expect = {"2026-10-05": True, "2026-10-07": True, "2026-10-08": False, "2026-10-09": False}
    bad = [f"{d}={seq[d]}" for d, e in expect.items() if seq.get(d) != e]
    if bad:
        r.fail("G6", "日历序列判定错误（NEXT_DAY 会算错）：" + ", ".join(bad))
    else:
        r.ok("G6", "国庆日历序列正确：10-05/10-07 休市 → 10-08 首个交易日 → 10-09 开市")


# ---------------------------------------------------------------- G7 校验器实跑
def g7_validate_scopes(r):
    for scope in VALIDATE_SCOPES:
        c, out, err = sh(f"{PYBIN} validate_stocks.py {' '.join(VFLAGS)} --scope {scope}", timeout=180)
        tail = [l for l in (out + err).splitlines() if l.strip()][-3:]
        summary = " / ".join(tail)[:160]
        if c == 0:
            okk = any("ALL OK" in l for l in tail)
            if okk:
                r.ok("G7", f"scope={scope} → ALL OK")
            else:
                r.warn("G7", f"scope={scope} exit0 但未见 ALL OK：{summary}")
        else:
            n = sum(1 for l in (out + err).splitlines() if l.startswith("[FAIL]"))
            if n:
                r.warn("G7", f"scope={scope} 存历史 FAIL ×{n}（归对应任务处理，不硬拦）：{summary}")
            else:
                r.warn("G7", f"scope={scope} exit={c}：{summary[:120]}")


# ---------------------------------------------------------------- G8/G9/G10 数据
def us_last_closed(now=None):
    """最近一个**已收盘**的美股交易日（美东口径，R100z181）。

    为什么不能直接用 `prior_us_trading_day(北京今天)`：北京时间 00:00–05:00 这一段，
    美东还停在前一天的盘中（北京 10-24 00:50 = 美东 10-23 12:50，周五未收盘），
    当天收盘尚未发生，但北京日期已经翻页 → 期望值多算一天 → **每天凌晨必报一条假 WARN**
    （「us.tradeDate 早于最近已收盘美股交易日」）。假 WARN 与假 FAIL 同罪（24.16d）：
    它会训练人忽略 G10。

    口径：换算到美东，16:00 收盘之后当天才算「已收盘」；否则取严格前一个交易日。
    无 tzdata 时按 EDT（UTC-4）兜底；naive 输入一律按 Asia/Shanghai 解释（本项目只在京时区跑）。
    """
    import common as _c
    if now is None:
        now = datetime.now()
    if now.tzinfo is None:
        try:
            from zoneinfo import ZoneInfo
            now = now.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        except Exception:                                    # noqa: BLE001
            pass
    try:
        from zoneinfo import ZoneInfo
        et = now.astimezone(ZoneInfo("America/New_York"))
    except Exception:                                        # noqa: BLE001
        from datetime import timedelta
        et = now - timedelta(hours=12)
    d = et.date()
    if et.hour >= 16 and _c.is_us_trading_day(d):
        return d
    return _c.prior_us_trading_day(d)


def us_expected_floor(exp, now=None):
    """G10 用的**美股日期下限**：周末把期望退到「周五→周四」（R100z163）。

    为什么需要它：07:00 任务排程是**周二至周五**（BYDAY=TU,WE,TH,FR），周六/周日不跑；
    而周五美股收盘落在北京时间周六 04:00，由**周日 22:00 周末任务**补采
    （08:30 prompt 原文：「周一 07:00 不排程，`us.tradeDate` 应为周五，由周日 22:00 周末任务抓取，属正常」）。
    所以周六全天 + 周日补采前，data.js 里最新只能是**周四收盘**。
    旧写法直接拿「最近已收盘美股交易日」（周五）去比 → 每个周末上午必报一条假 WARN，
    又变回「永远为真的 WARN 训练人忽略 G10」（24.16d）。真失败由**周一**那次抓到。

    抽成模块级函数是为了能单测（同 `us_last_closed()` 的做法）。
    """
    if now is None:
        now = datetime.now()
    if now.weekday() >= 5:                                   # 周六 / 周日
        import common as _cc
        return _cc.prior_us_trading_day(exp) or exp
    return exp


def g8_g9_g10_data(r):
    try:
        D = load_data_json()
    except Exception as e:                                  # noqa: BLE001
        r.fail("G8", f"data.js 解析失败：{e}")
        return
    # G8 顶层键
    miss = [k for k in DATA_KEYS_REQUIRED if k not in D]
    if miss:
        r.fail("G8", f"data.js 缺必需顶层键：{', '.join(miss)}")
    else:
        r.ok("G8", f"data.js 必需顶层键齐备（{len(DATA_KEYS_REQUIRED)} 个，另有 {len(D)} 个键）")
    # 前端引用的 D.xxx 是否都存在
    try:
        html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
        used = sorted({m for m in __import__("re").findall(r"\bD\.([a-zA-Z_][\w]*)", html)})
    except Exception:                                       # noqa: BLE001
        used = []
    miss_front = [u for u in used if u not in D and not any(u in k for k in D)]
    if miss_front:
        r.warn("G8", f"前端引用但 data.js 无对应顶层键：{', '.join(miss_front)}")
    else:
        r.ok("G8", f"前端 index.html 引用的 {len(used)} 个 D.* 顶层键均能在 data.js 找到")

    # G9 空内容
    empties, skipped, seen = [], set(), set()
    for path, v in walk_paths(D):
        if path in seen:
            continue                                    # walk_paths 标量会重复产出
        seen.add(path)
        if any(re.search(pat, path) for pat, _ in EMPTY_WHITELIST):
            skipped.add(path)
            continue
        if path.endswith((".bullRefs", ".bearRefs")):   # 无对应方向新闻即空数组
            skipped.add(path)
            continue
        # R100z162：check_duanban 的 nd()/add_bull_code() 对 aiPrediction 来源的 ref 刻意留
        #   time=""（_fmt_ref 用 `if x` 跳过、前端完全不读 bullRefs/bearRefs），属结构性空值。
        #   不豁免则每天固定报 5 条「bullRefs[1].time=空串」假 WARN，久而久之闸门被无视。
        if re.search(r"\.(?:bull|bear)Refs\[\d+\]\.time$", path):
            skipped.add(path)
            continue
        if v == "":
            empties.append(f"{path}=空串")
        elif v == []:
            empties.append(f"{path}=空数组")
    if empties:
        r.warn("G9", f"发现 {len(empties)} 处空内容（白名单外）：" + "; ".join(empties[:12]) +
               (" …" if len(empties) > 12 else ""))
    else:
        r.ok("G9", f"无白名单外的空串/空数组（豁免 {len(skipped)} 处结构性空值，"
                   f"由 G9b 反证其前端安全）")

    # G9b 前端兜底反证：被白名单豁免的空字段，前端必须①不引用 ②有真值兜底
    # —— 目的是让「空内容安全」这一点机器可复验，而不是每次靠人翻 index.html。
    try:
        src_html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
    except Exception:                                   # noqa: BLE001
        src_html = ""
    # 从豁免规则里抽出被允许的「末尾字段名」
    guard_fields = ["story", "deduce", "sector", "bullType", "boardPctZt",
                    "boardPctToday", "bkCode", "hybk", "open_num", "summary",
                    "exp", "verification", "bullish", "bearish", "themePicks",
                    "excluded"]
    # 判定原则：只抓「高置信的真事故」——字段被直接加进 HTML 字符串拼接（post 紧跟 +）
    # 且左侧 80 字符内完全没有 if/?/&&/||/typeof/esc(/函数参数位 等任何兜底迹象。
    # 这类写法一旦字段为空，页面上就是一块空白；其余（有兜底 / 只做赋值 / 纯比较）
    # 一律放过——宁可漏报也不误报，误报会让这条闸门被人长期无视。
    GUARD_RE = re.compile(r"(if|\?|\&\&|\|\||==|!=|typeof|return|\bvar\b|\bconst\b|"
                          r"\blet\b|esc\(|deduceConfBadge\(|\(\s*$)")
    tripwires = []
    for fld in guard_fields:
        for m in re.finditer(r"\.%s\b" % re.escape(fld), src_html):
            pre = src_html[max(0, m.start() - 80):m.start()]
            post = src_html[m.end():m.end() + 10]
            if not post.startswith("+"):
                continue                                # 不是直接拼进 HTML
            if GUARD_RE.search(pre):
                continue                                # 左侧已有兜底迹象 → 安全
            tripwires.append(f".{fld} → …{post.strip()[:12]}")
    if tripwires:
        r.warn("G9b", "豁免空字段存在无兜底的裸字符串拼接（会渲染空白）：" +
               "; ".join(sorted(set(tripwires))[:6]))
    else:
        r.ok("G9b", f"{len(guard_fields)} 类豁免空字段全部通过前端反证"
                    f"（无裸拼接 / 无引用 / 渲染前有真值判断）")

    # G10 日期新鲜度
    def latest_trading_day():
        d = datetime.now().strftime("%Y-%m-%d")
        for _ in range(12):
            c, out, _ = sh(f"{PYBIN} cal_factor.py --date {d} --json", timeout=60)
            try:
                if json.loads(out).get("aShareClosed") is False:
                    return d
            except Exception:                               # noqa: BLE001
                pass
            from datetime import timedelta
            d = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        return None

    last = latest_trading_day()
    checks = [
        ("aiPrediction.date", (D.get("aiPrediction") or {}).get("date")),
        ("openOutlook.date", (D.get("openOutlook") or {}).get("date")),
        ("ashare.tradeDate", (D.get("ashare") or {}).get("tradeDate")),
    ]
    stale = [f"{k}={v}" for k, v in checks if v and last and v < last]
    if stale:
        r.warn("G10", f"日期早于最近交易日 {last}：{'; '.join(stale)}（休市期正常，换日前需复核）")
    else:
        r.ok("G10", f"各模块日期均 ≥ 最近交易日 {last}")

    # 美股单独口径（R100z167 / R100z181 修）：美东收盘落在北京时间次日凌晨，美股交易日历又与 A 股不同。
    # 旧写法把 us.tradeDate 塞进上面那张表与 A 股最近交易日比 —— 结果**每天晚上必然命中**
    # 「us.tradeDate 早于最近交易日」（北京 21:00 时美东 10-21 还没收盘，最新只能是 10-20），
    # 一条永远为真的 WARN，等于训练人忽略 G10。现改为与「最近已收盘美股交易日」比。
    # ⚠️ R100z181：期望值曾取 `prior_us_trading_day(北京今天)`，在北京 00:00–05:00 会多算一天
    # （北京 10-24 00:50 = 美东 10-23 12:50 盘中，当天收盘还没发生）→ 每天凌晨一条假 WARN。
    # 已抽成模块级 `us_last_closed()`（美东口径 + 16:00 收盘判断），可直接单测。
    # 交易日历已下沉 common.py（与 07:00 取数脚本同源，不再有第三份手工副本）。
    try:
        us_td = str((D.get("us") or {}).get("tradeDate") or "")[:10]
        exp = us_last_closed()
        # R100z163（2026-10-24 排查第 6 条「7 条自动化是否有 bug」时撞到）：
        #   07:00 任务排程是 **周二至周五**（BYDAY=TU,WE,TH,FR），周六/周日不跑；
        #   周五美股收盘落在北京时间周六 04:00，**由周日 22:00 周末任务补采**
        #   （08:30 prompt 里就是这么写的：「周一 07:00 不排程，us.tradeDate 应为周五，
        #   由周日 22:00 周末任务抓取，属正常」）。
        #   因此周六全天 + 周日补采前，data.js 里最新只能是**周四收盘** —— 旧写法拿周五去比，
        #   每个周末上午必报一条假 WARN，又回到「永远为真的 WARN 训练人忽略 G10」。
        #   周末把下限放到 `prior_us_trading_day(exp)`（周五→周四）；真失败由**周一**那次抓到。
        floor = us_expected_floor(exp)
        if us_td and floor:
            if us_td == exp.isoformat():
                r.ok("G10", f"us.tradeDate={us_td} = 最近已收盘美股交易日")
            elif us_td > exp.isoformat():
                r.ok("G10", f"us.tradeDate={us_td} 不早于最近已收盘美股交易日 {exp.isoformat()}")
            elif us_td >= floor.isoformat() and floor < exp:
                r.ok("G10", f"us.tradeDate={us_td}，周末 07:00 不排程，"
                            f"最近收盘 {exp.isoformat()} 由周日 22:00 周末任务补采（符合设计）")
            else:
                r.warn("G10", f"us.tradeDate={us_td} 早于最近已收盘美股交易日 {exp.isoformat()}"
                              f"——07:00 取数步未跑，或美股数据未换日")
    except Exception as e:                                   # noqa: BLE001
        r.warn("G10", f"美股交易日历核对跳过：{e}")

    # openOutlook.pos 闭环（R100z13 → 19:30 score_openoutlook 打分依赖）
    # 分流：① 前瞻已为「下一交易日」写了但缺 pos → 真缺陷（当天 19:30 必然 [SKIP]）
    #      ② 前瞻尚未为下一交易日写 → 属正常未到期，INFO 不告警
    def next_trading_day(start):
        d = start
        for _ in range(14):
            c, out, _ = sh(f"{PYBIN} cal_factor.py --date {d} --json", timeout=60)
            try:
                if json.loads(out).get("aShareClosed") is False:
                    return d
            except Exception:                           # noqa: BLE001
                pass
            from datetime import timedelta
            d = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        return None

    oo = D.get("openOutlook") or {}
    oo_date = oo.get("date")
    nxt = next_trading_day(datetime.now().strftime("%Y-%m-%d")) if last else None
    pos_bad = (not isinstance(oo.get("pos"), dict)
               or not {"tendency", "bandLo", "bandHi", "volExpect"} <= set(oo["pos"]))
    if not pos_bad:
        r.ok("G10", f"openOutlook.pos 完整（date={oo_date}，19:30 前瞻打分闭环可用）")
    elif nxt and oo_date and oo_date >= nxt:
        r.fail("G10", f"openOutlook(date={oo_date}) 已为下一交易日 {nxt} 写了前瞻却缺结构化 pos"
                      f"（tendency/bandLo/bandHi/volExpect）→ 当日 19:30 score_openoutlook 将 [SKIP]。"
                      f"本条硬拦 deploy：08:30 当次就该发现，不许拖到 19:30")
    elif nxt:
        r.ok("G10", f"openOutlook 尚未为下一交易日 {nxt} 写入前瞻（date={oo_date}）"
                    f"——08:30 自动化到期后补写；届时 audit 会校验 pos 是否完整，缺 pos 即 FAIL")

    # duanban.star 存在性（R100z16 清场事故回归）
    # 休市期缺失属预期（9:45 在下一个交易日才重建），仅当最近交易日刚过 1 天仍缺才判 FAIL
    # R100z83（2026-10-08 实战教训）：交易日 **10:00 前** star 缺失降为 WARN——
    #   star 由 9:45 任务写入，07:00/08:30 任务先于它部署，若此时判 FAIL 会逼着
    #   前置任务按「FAIL 必须修正」条款提前跑 update_star.py 补造 star（08:11 实际发生），
    #   违反「9:45 是 star 唯一写入口」的职责边界、产出开盘前低质量版本。
    #   10:00 后（9:45 窗口已过）仍缺才是真事故，恢复硬拦。
    today = datetime.now().strftime("%Y-%m-%d")
    _before_star_window = datetime.now().hour < 10  # 9:45 任务窗口（留 15 分钟宽限）未过
    if "star" not in (D.get("duanban") or {}):
        # 口径：star 由 9:45 在「交易日当天」写入。
        # ① 下一个交易日还没到（休市期）→ 缺失是必然，PASS（不是缺陷）
        # ② 下一交易日已到 / 就是今天、但当前 <10:00 → 9:45 尚未跑，WARN（不是缺陷）
        # ③ 下一交易日已到 / 就是今天、且已过 10:00 仍缺 → 真事故（历史上 merge 会把它清场）
        if not nxt or nxt > today:
            r.ok("G10", f"duanban.star 当前缺失属预期：下一个交易日 {nxt or '—'} 尚未到"
                        f"（9:45 开盘精选届时写入；R100z16 后 merge 不再清场）")
        elif _before_star_window:
            r.warn("G10", f"duanban.star 缺失但当前在 10:00 前（下一交易日 {nxt} 当天、9:45 开盘精选"
                          f"尚未跑）——属预期，07:00/08:30 任务**不要**跑 update_star.py 补造，照常部署；"
                          f"10:00 后仍缺才升级 FAIL")
        else:
            r.fail("G10", f"duanban.star（🌟开盘精选）缺失，而下一交易日 {nxt} 已到且已过 9:45 窗口"
                          f"——历史曾因 merge 时序被清场，属真事故，须排查清场根因并补回")
    else:
        sd = (D["duanban"]["star"] or {}).get("date")
        if sd and last and sd < last:
            r.warn("G10", f"duanban.star.date={sd} 早于最近交易日 {last}（9:45 未跑则正常）")
        else:
            r.ok("G10", f"duanban.star 存在（date={sd}）")


# ---------------------------------------------------------------- G11
def g11_smoke(r, deep=False):
    # ① 始终运行的「导入级冒烟」（无网络）：捕获 import 级静默 rot——
    #    仅靠 py_compile 抓不到 import 期故障（缺依赖 / 循环 import / 全局副作用崩），
    #    一旦部署链路里有模块导入失败，G1 语法闸也拦不住，会在复牌/首跑才炸。
    #    故固化进闸门、所有自动化部署都跑（不再依赖 --deep 才会触发）。
    import importlib
    mods = ["cal_factor", "build_stock_pool", "track_calibration",
            "stock_risk_blacklist", "market_bench", "common", "audit_pipeline"]
    bad_imp = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:                              # noqa: BLE001
            bad_imp.append(f"{m}: {type(e).__name__}: {e}"[:100])
    if bad_imp:
        r.fail("G11", "模块导入冒烟失败（部署前静默 rot 风险）：" + "; ".join(bad_imp))
    else:
        r.ok("G11", f"{len(mods)} 个流水线模块导入冒烟通过（无 import 级故障）")

    if not deep:
        return
    # ② --deep 时的「脚本网络冒烟」：逐个实跑（可能走网络，慢）
    cands = [
        ("cal_factor.py --json", "cal_factor.py --json"),
        ("build_stock_pool.py --json", "build_stock_pool.py --json"),
        ("track_calibration.py --json", "track_calibration.py --json"),
        ("stock_risk_blacklist.py --json", "stock_risk_blacklist.py --json"),
        ("market_bench.py --json", "market_bench.py --json"),
    ]
    bad = []
    for name, args in cands:
        c, out, err = sh(f"{PYBIN} {args}", timeout=90)
        if c not in (0, 1):
            bad.append(f"{name} exit={c} {err[:80]}")
    if bad:
        r.fail("G11", "只读脚本冒烟异常：" + "; ".join(bad))
    else:
        r.ok("G11", f"{len(cands)} 个只读脚本冒烟可执行（exit 0/1 均视为契约正常）")


# ---------------------------------------------------------------- G12
def load_auto_prompts():
    """读看板自动化的 prompt 全文。

    单一事实源是 DB 本身，不用基线快照（快照会随代码演进而腐化）。
    注意 R100z62：DB 里 deleted_at IS NULL 的自动化有 7 条，混着「公益花园·照顾宠物」
    与「美团每日自动领券」（cwds 不在本项目）——必须用 cwds 收窄，否则会把别的任务的
    prompt 当成看板流程，得出「无人点名」这类假结论。
    """
    try:
        import sqlite3
        db = os.path.expanduser("~/.workbuddy/workbuddy.db")
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = c.execute(
            "SELECT name, prompt, cwds, rrule FROM automations "
            "WHERE deleted_at IS NULL AND cwds LIKE '%market dashboard%'"
        ).fetchall()
        return [{"name": n, "text": (t or ""), "cwds": w, "rrule": rr}
                for n, t, w, rr in rows]
    except Exception as e:                                   # noqa: BLE001
        print(f"[G12] 读不到自动化 prompt（{e}），跳过点名校验")
        return []


def _proof_ok(kind, key, val, html):
    """豁免反证是否仍成立。不成立 = 豁免理由已失效，必须重新登记。"""
    if kind == "intermediate":
        return key not in html
    if kind == "mapping":
        return isinstance(val, dict) and all(
            isinstance(k, str) and isinstance(v, str) and v for k, v in val.items()
        )
    return True


def g12_deadlinks(r):
    """半死链路闸门：产出必须有人认领、被流程点名；脚本不许当孤儿。"""
    D = load_data_json()
    html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
    autos = load_auto_prompts()
    allp = "\n".join(a["text"] for a in autos)

    # ① 无主产出：新增顶层节点却没登记流程主人 → 直接 FAIL（前端渲染了也没人核对）
    unknown = [k for k in D if k not in PRODUCT_OWNERSHIP]
    for k in sorted(unknown):
        r.fail("G12", f"无主产出：data.js 顶层节点 {k} 未登记流程主人——"
                      f"先在 PRODUCT_OWNERSHIP 里补 owner/note，不许靠「闸门没报错」蒙混")

    # ② 点名校验 + 豁免反证
    dead, exempt_broken, named_n = [], [], 0
    for k, spec in PRODUCT_OWNERSHIP.items():
        val = D.get(k)
        req = tuple(x.lower() for x in (spec.get("require") or ()))
        if req:
            hit = [a["name"] for a in autos if any(x in a["text"].lower() for x in req)]
            if hit:
                named_n += 1
            else:
                # 前端在渲染却没人点名 = 真半死（FAIL）；纯后台产物 = WARN
                dead.append(f"{k}（前端渲染={k in html}）")
                if k in html:
                    r.fail("G12", f"半死链路：{k} 前端已渲染，但七条看板自动化无一条点名"
                                  f" {list(spec['require'])}——要么补进 prompt，要么在"
                                  f" PRODUCT_OWNERSHIP 登记 owner 并写明豁免理由")
                else:
                    r.warn("G12", f"半死链路（后台）：{k} 无自动化点名 {list(spec['require'])}")
        # 豁免反证
        kind = spec.get("proof")
        if kind and not _proof_ok(kind, k, val, html):
            exempt_broken.append(k)
            r.fail("G12", f"豁免已失效：{k} 原本按「{kind}」豁免，但现状不再满足"
                          f"（intermediate=前端不引用它；mapping=dict[str,str] 静态映射）")

    if not dead and not exempt_broken:
        r.ok("G12", f"{len(PRODUCT_OWNERSHIP)} 个顶层产出全部有主人、{named_n} 个被自动化点名、"
                    f"豁免项反证成立（{len([k for k, s in PRODUCT_OWNERSHIP.items() if s.get('proof')])} 个）")

    # ②b 自动化元数据：cwds 必须是合法 JSON 数组且指向本项目，rrule 不许空。
    # 这条是「不改就出事」的硬约束——手写漏一个双引号会存成 [/path] 字符串，
    # 自动化照跑但不干任何事，且不报错，只能靠这个闸门抓。
    bad_cwds, bad_rrule = [], []
    for a in autos:
        try:
            cs = json.loads(a["cwds"])
            if not (isinstance(cs, list) and cs and all(isinstance(x, str) and x for x in cs)):
                bad_cwds.append(f"{a['name']}:cwds=({a['cwds']!r} 不是 JSON 数组)")
            elif not any(HERE in x for x in cs):
                bad_cwds.append(f"{a['name']}:cwds 未指向本项目")
        except Exception as e:                               # noqa: BLE001
            bad_cwds.append(f"{a['name']}:cwds 解析失败({e})")
        if not (a.get("rrule") or "").strip():
            bad_rrule.append(a["name"])
    if bad_cwds:
        r.fail("G12", "自动化工作目录非法（自动化会静默跑空不报错）：" + "; ".join(bad_cwds))
    elif bad_rrule:
        r.warn("G12", f"自动化缺 rrule（可能是一次性任务）：{', '.join(bad_rrule)}")
    else:
        r.ok("G12", f"{len(autos)} 条看板自动化 cwds 均为合法 JSON 数组且指向本项目、rrule 非空")

    # ②c 流程纪律：会 deploy.sh 上线的自动化必须接审计闸门；prompt 不得写死闸门基线数字。
    # R100z65：此前 audit_pipeline 只接在 19:30 与周日 22:00 两条上，07:00/08:30/9:45
    # 三条都会 deploy.sh 上线却绕过闸门——「闸门只长在部分任务身上」也是半死链路。
    # 而写死 PASS=24 这类基线数字，闸门一增项就必然误判（现自报 27）。
    miss_gate = [a["name"] for a in autos
                 if "deploy.sh" in a["text"] and "audit_pipeline" not in a["text"]]
    hard_base = [a["name"] for a in autos if re.search(r"PASS\s*=\s*\d+", a["text"])]
    if miss_gate:
        r.fail("G12", "会 deploy.sh 上线却没接流水线审计闸门（闸门只长在部分任务身上 = 半死链路）："
                      + "; ".join(miss_gate))
    else:
        r.ok("G12", f"{len(autos)} 条看板自动化凡会 deploy.sh 的均已接 audit_pipeline 闸门")
    if hard_base:
        r.warn("G12", "prompt 里写死了闸门基线数字（PASS=N），闸门增项后必然误判，"
                      "应改为以脚本自报 + 退出码为准：" + "; ".join(hard_base))

    # ③ 脚本孤儿：prompt / 其它 py 源码 / shell 脚本 三处都不出现该模块名。
    # 注意必须比模块名（去 .py）——代码里写的是 `import kline_cache`，拿 'kline_cache.py'
    # 去 substr 匹配必然失败，会把满地引用的热脚本误报成孤儿。
    # R100z157：补 shell 层——deploy.sh 是 enrich_policy / sweep_news / market_structure 这类
    # 「零网络确定性脚本」的唯一调用方，只扫 py + prompt 会把它们全部误报成孤儿。
    pyfiles = sorted(f for f in os.listdir(HERE) if f.endswith(".py"))
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", errors="ignore").read()
            for f in pyfiles}
    for f in sorted(x for x in os.listdir(HERE) if x.endswith(".sh")):
        try:
            srcs[f] = open(os.path.join(HERE, f), encoding="utf-8", errors="ignore").read()
        except Exception:                                    # noqa: BLE001
            pass
    orphan = []
    for f in pyfiles:
        mod = f[:-3]
        if mod in allp:
            continue                                  # 自动化 prompt 直接点名
        if any(mod in s for n, s in srcs.items() if n != f):
            continue                                  # 别处 import / subprocess / shell 引用
        orphan.append(f)
    if orphan:
        r.warn("G12", f"疑似孤儿脚本（prompt / py 源码 / shell 三处均无引用，动态引用 ex.submit/ex.map 需人工确认）："
                      f"{', '.join(sorted(orphan))}")
    else:
        r.ok("G12", f"{len(pyfiles)} 个 py 全部有调用方（prompt 点名或 py/shell 引用）")


def g13_holiday_table(r):
    """节假日表闸门：两份手工表必须同源等价 + 覆盖前瞻提醒跨年更新。

    背景（R100z66 体检发现）：common.HOLIDAY_RANGES（is_trade_day /
    last_trade_day / today_trade_date 用）与 cal_factor.HOLIDAYS
    （aShareClosed / 日历标签用）是两份手工副本，注释各自标了「跨年须同步更新」——
    纯靠人记必漏。任一处漏更 → 两个脚本对「今天是否交易日」判定打架：
    一个剔除节假日、一个不剔，tradeDate / aShareClosed 口径分裂且无任何报错。
    另外表目前只到 2026-10-07，2027-01-01 起节假日将全部被当交易日 →
    需要在 12 月初（覆盖到期前 30 天）提前 WARN 提醒更新 2027 年度表。"""
    try:
        sys.path.insert(0, HERE)
        import common as _cm
        import cal_factor as _cf
    except Exception as e:                                   # noqa: BLE001
        r.fail("G13", f"无法导入 common/cal_factor 校验节假日表：{e}")
        return
    # ① 同源等价：两份表展开成 (start, end) 序列后必须完全一致
    a = sorted((str(x[0]), str(x[1])) for x in (getattr(_cm, "HOLIDAY_RANGES", []) or []))
    b = sorted((str(v[0]), str(v[-1]))
               for v in (getattr(_cf, "HOLIDAYS", {}) or {}).values())
    if a != b:
        r.fail("G13", f"两份节假日表不同源（is_trade_day 与 cal_factor.aShareClosed 会判定打架）："
                      f"common={a} vs cal_factor={b}——必须同步更新")
    else:
        r.ok("G13", f"common 与 cal_factor 节假日表同源等价（{len(a)} 段）")
    # ② 覆盖前瞻：表尾距今不足 30 天 → WARN（提醒更新下一年度表，WARN 不拦部署）
    try:
        from datetime import date as _date
        max_d = _date.fromisoformat(max(x[1] for x in a)) if a else None
        if max_d is None:
            r.fail("G13", "节假日表为空——is_trade_day 将把所有日子（含节假日）当交易日")
        else:
            left = (max_d - _date.today()).days
            if left < 30:
                r.warn("G13", f"节假日表最晚只覆盖到 {max_d}（剩 {left} 天）——"
                              "请对照国务院放假通知更新下一年度表（common.py + cal_factor.py 两处同步，"
                              "只改一处会被本闸门 ① 拦下）")
            else:
                r.ok("G13", f"节假日表覆盖到 {max_d}（剩 {left} 天，暂无需更新）")
    except Exception as e:                                   # noqa: BLE001
        r.fail("G13", f"节假日表覆盖前瞻检查失败：{e}")
def g14_prompt_broken_path(r):
    """G14 路径断裂闸门（R100z71）：prompt 内禁止出现「/WorkBuddy/market <空格><脚本>.py」
    这类签名——合法路径是 /WorkBuddy/market dashboard/...（含空格需整体引号或走 cd 相对调用）。

    R100z71 发现：07:00 / 周日两条任务曾因路径损坏（缺 `dashboard/` 且未加引号）导致
    `update_us_from_quotes` / `check_duanban` / `stock_risk_blacklist` / `build_stock_pool`
    调用把目录当脚本跑、静默失败（python3: can't open file '/Users/loccco/WorkBuddy/market'），
    us 美股行情与断板池长期未更新、且无人察觉。G2 只校验 audit_expectations.json 里的
    相对文件名是否存在于磁盘，扫不到 prompt 内写死的绝对路径，故补本闸固化。

    注意：本闸只拦「缺 dashboard/ 的裸路径签名」，不拦「cd /WorkBuddy/market dashboard && python3 ...」
    这种未引号 cd 形式——后者在自动化执行器内能正确运行（19:30/08:30/09:45 及本任务内
    多行正确调用均为该形式，已实证可部署），本地裸 shell 测出的 cd 报错属执行上下文差异。"""
    autos = load_auto_prompts()
    bad = []
    pat = re.compile(r'/Users/loccco/WorkBuddy/market\s+[A-Za-z0-9_]+\.(?:py|sh)')
    for a in autos:
        for m in pat.finditer(a["text"]):
            ln = a["text"][:m.start()].count("\n") + 1
            bad.append(f"{a['name']} L{ln}: {m.group(0)}")
    if bad:
        r.fail("G14", "prompt 内存在「路径断裂」签名（/WorkBuddy/market 后缺 dashboard/ 且未加引号，"
                     "脚本调用会静默失败、把目录当脚本跑）：" + "; ".join(bad))
    else:
        r.ok("G14", f"{len(autos)} 份 prompt 无路径断裂签名（脚本调用均含完整 dashboard/ 或走 cd 相对调用）")


def g15_interp_bins(r):
    """G15 解释器入口闸门（R100z76d 初建 → R100z77 升级）：prompt/脚本引用的解释器入口必须可用。

    根因修复：版本号不再写死，改为自修复包装脚本 run-node / run-python
    （动态解析托管版最高版本目录，缺失时回退 PATH）。本闸校验两类引用：
      (a) 残留硬版本路径 versions/X.Y.Z/bin/(node|python3) —— 必须存在；
      (b) 包装脚本 /Users/loccco/.workbuddy/binaries/run-(node|python) —— 必须存在、
          可执行、且能解析到一个真实存在的解释器二进制（glob 到 versions/*/bin/(node|python3)）。
    """
    autos = load_auto_prompts()
    ver_pat = re.compile(
        r'(/Users/loccco/\.workbuddy/binaries/(?:node|python)/versions/[^/\s]+/bin/(?:node|python3))')
    problems = []

    # (a) 残留版本路径：必须存在
    for a in autos:
        for m in ver_pat.finditer(a["text"]):
            path = m.group(1)
            if not os.path.exists(path):
                ln = a["text"][:m.start()].count("\n") + 1
                problems.append(f"{a['name']} L{ln}: 硬版本路径不存在 {path}")

    # (b) 包装脚本：存在 + 可执行 + 能解析到真实二进制；缺失则自愈重建
    WRAP_TPL = {
        "node": "#!/bin/sh\nROOT=/Users/loccco/.workbuddy/binaries/node/versions\n"
                '[ -e "$ROOT/current/bin/node" ] && exec "$ROOT/current/bin/node" "$@"\n'
                'd=$(ls -d "$ROOT"/*/ 2>/dev/null | sort -V | tail -1)\n'
                '[ -n "$d" ] && [ -x "${d}bin/node" ] && exec "${d}bin/node" "$@"\n'
                'exec node "$@"\n',
        "python": "#!/bin/sh\nROOT=/Users/loccco/.workbuddy/binaries/python/versions\n"
                  '[ -e "$ROOT/current/bin/python3" ] && exec "$ROOT/current/bin/python3" "$@"\n'
                  'd=$(ls -d "$ROOT"/*/ 2>/dev/null | sort -V | tail -1)\n'
                  '[ -n "$d" ] && [ -x "${d}bin/python3" ] && exec "${d}bin/python3" "$@"\n'
                  'exec python3 "$@"\n',
    }
    for kind, bin_name in (("node", "node"), ("python", "python3")):
        wp = f"/Users/loccco/.workbuddy/binaries/run-{kind}"
        if not os.path.exists(wp):
            # 自愈：缺失即重建（保证 pre-deploy 闸门跑完必定存在，自动化不会因文件丢失而崩）
            try:
                with open(wp, "w", encoding="utf-8") as fh:
                    fh.write(WRAP_TPL[kind])
                os.chmod(wp, 0o755)
                r.ok("G15", f"解释器包装脚本缺失已自愈重建：{wp}")
            except Exception as e:  # noqa: BLE001
                problems.append(f"解释器包装脚本缺失且重建失败 {wp}：{e}")
                continue
        elif not os.access(wp, os.X_OK):
            try:
                os.chmod(wp, 0o755)
                r.ok("G15", f"解释器包装脚本不可执行已自愈修复权限：{wp}")
            except Exception as e:  # noqa: BLE001
                problems.append(f"解释器包装脚本不可执行且修复失败 {wp}：{e}")
                continue
        root = f"/Users/loccco/.workbuddy/binaries/{kind}/versions"
        real = []
        try:
            for d in os.listdir(root):
                cand = os.path.join(root, d, "bin", bin_name)
                if os.path.exists(cand):
                    real.append(cand)
        except FileNotFoundError:
            pass
        if not real:
            problems.append(f"{wp} 解析不到任何托管 {bin_name}（{root} 下无版本目录）")

    if problems:
        r.fail("G15", "解释器入口不可用：" + "; ".join(problems))
    else:
        r.ok("G15", f"{len(autos)} 份 prompt 引用的解释器入口均可用（run-node/run-python 自修复生效）")


def _content_of(v):
    """顶层 openOutlook 是对象 {date, content, source, pos}（R100z13 起）。

    R100z160：此前 G16/G17 直接对它取 str() —— 量出来的是「dict 的 repr」（含键名与 pos
    子对象），openOutlook 的 300 字地板因此形同虚设（实测 str=1528 而正文 1359），
    字数区间也无法核对。统一改为取 content 正文；非 dict 时原样返回（兼容旧数据/纯字符串）。
    """
    if isinstance(v, dict):
        return str(v.get("content") or "")
    return v


def collect_g16_checks(D):
    """G16 叙事深度字段清单采集（R100z136 从 g16_narrative_depth 拆出）。

    check_narrative.py 落笔前预检复用本函数——预检与审计闸门必须同一份清单、
    同一套阈值，禁止出现两套口径。返回 [(label, text, floor), ...]。
    """
    checks = []  # (label, text, floor)

    def add(label, text, floor):
        checks.append((label, str(text or ""), floor))

    # 板块异动 + 五节要闻（A 股 / 美股）
    for mk, sec_floor, news_floor in (("ashare", 240, 240), ("us", 200, 200)):
        sec = D.get(mk) or {}
        for arr in ("sectorsUp", "sectorsDown"):
            for i, s in enumerate(sec.get(arr) or []):
                if isinstance(s, dict):
                    add(f"{mk}.{arr}[{i}].reason.detail",
                        (s.get("reason") or {}).get("detail"), sec_floor)
        for s in ("bullNews", "bearNews", "macroNews", "intlNews", "bankViews"):
            for i, nw in enumerate(sec.get(s) or []):
                if isinstance(nw, dict):
                    add(f"{mk}.{s}[{i}].summary", nw.get("summary"), news_floor)
        if sec.get("outlook"):
            add(f"{mk}.outlook", sec.get("outlook"), 300)

    # 美股顶层 summary（脚本生成；2026-10-12 前仅 ~124 字偏薄，用户要求与 A 股同口径扩充）
    _us_sum = (D.get("us") or {}).get("summary")
    if _us_sum:
        add("us.summary", _us_sum, 150)

    # AI 预测逻辑链 + 开盘前瞻（顶层）
    ap = D.get("aiPrediction") or {}
    for i, s in enumerate(ap.get("sectors") or []):
        if isinstance(s, dict):
            add(f"aiPrediction.sectors[{i}].logic", s.get("logic"), 200)
    if D.get("openOutlook"):
        add("openOutlook", _content_of(D.get("openOutlook")), 300)
    if isinstance(ap, dict) and ap.get("openOutlook"):
        add("aiPrediction.openOutlook", _content_of(ap.get("openOutlook")), 300)

    # A 股题材掘金 logic（R100z129 补齐：docstring 早已声明 240，但代码一直漏检，
    # 只靠 19:30 prompt 软约束 240-420 —— 用户 2026-10-13 拍板补机器闸）
    for i, t in enumerate((D.get("ashare") or {}).get("themePicks") or []):
        if isinstance(t, dict):
            add(f"ashare.themePicks[{i}].logic", t.get("logic"), 240)

    # 连板天梯 story（A 股）
    for i, s in enumerate((D.get("ashare") or {}).get("lianban") or []):
        if isinstance(s, dict):
            add(f"ashare.lianban[{i}].story", s.get("story"), 120)

    # 断板反包 confirmed/watching story（R100z148：检查面从 ashare.duanban 改到顶层
    # duanban——ashare.duanban 是 2026-10-14 后无任何读写方的死节点（前端/
    # 脚本均只读写顶层 D.duanban），已从 data.js 剔除；顶层才是真实渲染的数据源）
    db = D.get("duanban") or {}
    for pool in ("confirmed", "watching"):
        for i, e in enumerate(db.get(pool) or []):
            if isinstance(e, dict):
                add(f"duanban.{pool}[{i}].story", e.get("story"), 100)

    # 吸筹 scored/strong/watch note
    # R100z161c：路径修正——吸筹节点在**顶层** `accumulation`，`ashare` 里根本没有这个键
    #   （2026-10-21 实测 ashare 子键 27 个、无 accumulation；顶层 accum 有 scored 20 + strong 2
    #   + watch 18 = 40 条 note）。旧写法读 ashare.accumulation → 恒空 → **90 字地板从未生效**，
    #   是 R100z148 把断板从 ashare.duanban 改到顶层 duanban 时漏改的孪生兄弟。
    #   保留 ashare 兜底只为兼容万一回迁，正常走顶层。
    _acc = D.get("accumulation") or (D.get("ashare") or {}).get("accumulation") or {}
    for grp in ("scored", "strong", "watch"):
        for i, e in enumerate(_acc.get(grp) or []):
            if isinstance(e, dict):
                add(f"accumulation.{grp}[{i}].note", e.get("note"), 90)

    # 9:45 开盘精选「深度分析」（R100z161c 补闸）。二节速查表早写着 pushText 300-700 /
    #   sentiment 120-200，但 G16/G17 都不扫 duanban.star → 两个字段长期零保护。
    #   只加地板不加上限（超上限归 check_narrative 的 OVER 清单，硬拦会逼 AI 删内容）。
    _star = (D.get("duanban") or {}).get("star") or {}
    if isinstance(_star, dict):
        if _star.get("pushText"):
            add("duanban.star.pushText", _star.get("pushText"), 300)
        if _star.get("sentiment"):
            add("duanban.star.sentiment", _star.get("sentiment"), 120)

    # deduce 三候选推演（全站任意条目，R100z96：main/counter 曾被 prompt 定为「一句话」口径）
    def _deduce_walk(o, k=[0]):
        if isinstance(o, dict):
            dd = o.get("deduce")
            if isinstance(dd, dict):
                add(f"deduce#{k[0]}.main", dd.get("main"), 60)
                add(f"deduce#{k[0]}.counter", dd.get("counter"), 40)
                for j, c in enumerate(dd.get("cands") or []):
                    if isinstance(c, dict):
                        add(f"deduce#{k[0]}.cands[{j}].text", c.get("text"), 40)
                        el = c.get("eliminate")
                        add(f"deduce#{k[0]}.cands[{j}].eliminate",
                            el if isinstance(el, str) else "", 15)
                k[0] += 1
            for v in o.values():
                _deduce_walk(v, k)
        elif isinstance(o, list):
            for v in o:
                _deduce_walk(v, k)
    _deduce_walk(D)

    return checks


def g16_narrative_depth(r):
    """G16 防 AI 偷懒·叙事深度硬闸（R100z94）。

    背景：用户复盘满意 A 股深度（板块 285-390 / 题材 371-411 / 要闻 333-431 字），
    但美股 us.* 长期只有 60-110 字（根因：07:00 / 周日两条自动化 prompt 把字数上限
    写成 60-110）。量化页（谐波/横盘启动/吸筹）无叙事字段渲染，其空 story 是设计行为，不在这
    里管；chip 标签类字段（lianban.reason / 各池 note / star.picks.note / failPool.reason）
    也非主叙事，不在这管。本闸只管「真正渲染给用户的长文主字段」。

    阈值（字符数，留足安全垫、远在偷懒区之上）：
        A 股 板块 reason.detail / 题材 logic / 五节要闻 summary → 240
        A 股 outlook / us.outlook → 300
        A 股 aiPrediction.sectors[].logic → 200
        A 股 开盘前瞻 openOutlook → 300
        A 股 lianban[].story → 120；duanban confirmed/watching[].story → 100
        A 股 accumulation.{scored,strong,watch}[].note → 90
        美股 板块 reason.detail / 五节要闻 summary → 200
        美股 顶层 summary（脚本生成，数据驱动多维复盘）→ 150
    低于阈值即 FAIL（拦部署）；阈值取「当前已达标数据的最小值再下探一档」，
    既能挡住偷懒，又不误伤正常波动。

    R100z136：清单采集拆到 collect_g16_checks（与 check_narrative.py 预检共用）。
    """
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G16", f"data.js 解析失败：{e}")
        return

    checks = collect_g16_checks(D)
    fails = []
    for label, text, floor in checks:
        if len(text) < floor:
            fails.append(f"{label} 仅 {len(text)} 字（<{floor}，疑似偷懒/未写完）")
    if fails:
        r.fail("G16", f"发现 {len(fails)} 处叙事字段过短（防 AI 偷懒硬闸）："
               + "；".join(fails[:15]) + (" …" if len(fails) > 15 else ""))
    else:
        r.ok("G16", f"全站叙事字段深度达标（校验 {len(checks)} 处，阈值 15~300 字）")


def collect_g17_owners(D):
    """G17 复读句归属采集（R100z136 从 g17_template_tails 拆出）。

    check_narrative.py 预检复用本函数。返回 {sentence: set(field label)}——
    同一句 ≥14 字被 ≥3 个不同叙述字段共用即判「万能模板尾巴」。
    与 g17_template_tails 共用同一扫描面与阈值，不存在两套口径。
    """
    owner = {}  # sentence -> set(field label)

    def put(text, label):
        # R100z160：分句符补 `；`，与 G23 的 sentences() 对齐。此前只按 。！？ 切，
        # AI 用「；」把多个整句串成一段长句时，被复用的**后半截**落在同一"句"里就躲过检测
        # （2026-10-21 实测：4 只电力股共用同一条 bearRef，板块段写同一句只在 ； 后重复）。
        for s in re.split(r"(?<=[。！？；])", str(text or "")):
            s = s.strip()
            if len(s) >= 14:
                owner.setdefault(s, set()).add(label)

    for mk in ("ashare", "us"):
        sec = D.get(mk) or {}
        for arr in ("sectorsUp", "sectorsDown"):
            for i, s in enumerate(sec.get(arr) or []):
                if isinstance(s, dict):
                    put((s.get("reason") or {}).get("detail"), f"{mk}.{arr}[{i}]")
        for s in ("bullNews", "bearNews", "macroNews", "intlNews", "bankViews"):
            for i, nw in enumerate(sec.get(s) or []):
                if isinstance(nw, dict):
                    put(nw.get("summary"), f"{mk}.{s}[{i}]")
        if sec.get("outlook"):
            put(sec.get("outlook"), f"{mk}.outlook")
    ap = D.get("aiPrediction") or {}
    for i, s in enumerate(ap.get("sectors") or []):
        if isinstance(s, dict):
            put(s.get("logic"), f"aiPrediction.sectors[{i}]")
    for i, t in enumerate((D.get("ashare") or {}).get("themePicks") or []):   # R100z129：与 G16 扫描面保持一致
        if isinstance(t, dict):
            put(t.get("logic"), f"ashare.themePicks[{i}]")
    put(_content_of(D.get("openOutlook")), "openOutlook")
    if isinstance(ap, dict) and ap.get("openOutlook"):
        put(_content_of(ap.get("openOutlook")), "aiPrediction.openOutlook")
    for i, s in enumerate((D.get("ashare") or {}).get("lianban") or []):
        if isinstance(s, dict):
            put(s.get("story"), f"ashare.lianban[{i}]")
    # R100z148：与 G16 扫描面一致——顶层 duanban（ashare.duanban 死节点已剔除）
    dbsec = D.get("duanban") or {}
    for pool in ("confirmed", "watching"):
        for i, e in enumerate(dbsec.get(pool) or []):
            if isinstance(e, dict):
                put(e.get("story"), f"duanban.{pool}[{i}]")
    # ⚠️ 吸筹 note **刻意不纳入 G17**（R100z161c 实测后决定，别再"补进来"）：
    #   它的 90 字地板在 G16 里补了（此前读错路径恒空），但 note 正文由
    #   accumulation_score.py 按形态分支**脚本拼装**，同一形态必然同句——
    #   2026-10-21 试纳入 G17 立即报 8 句复读（最高「3日缩量连跌后已收复过半跌幅；」×40 处），
    #   全部只落在 accumulation 域内。这不是 AI 偷懒，硬拦会天天 FAIL 且 AI 无从修。
    #   要治只能改脚本文案多样性（见 PROJECT-MEMORY 三节），不归 G17 管。
    # R100z161c：9:45 深度分析纳入复读面（AI 手工文本，与 outlook/要闻常出自同一批措辞）
    _star17 = (D.get("duanban") or {}).get("star") or {}
    if isinstance(_star17, dict) and _star17.get("pushText"):
        put(_star17.get("pushText"), "duanban.star.pushText")
    # 全站 deduce
    def _ded_walk(o):
        if isinstance(o, dict):
            dd = o.get("deduce")
            if isinstance(dd, dict):
                lbl = f"deduce#{id(dd) % 10000}"
                put(dd.get("main"), lbl + ".main")
                put(dd.get("counter"), lbl + ".counter")
                for c in dd.get("cands") or []:
                    if isinstance(c, dict):
                        put(c.get("text"), lbl + ".cand")
            for v in o.values():
                _ded_walk(v)
        elif isinstance(o, list):
            for v in o:
                _ded_walk(v)
    _ded_walk(D)

    return owner


def g17_template_tails(r):
    """G17 防 AI 偷懒·防模板复读闸（R100z96）。

    事故：us.* 34 条叙述字段全部以同一句万能尾巴收尾（25×「操作上需结合盘面强度
    与成交量变化动态评估，切忌单因子外推。」+ 9×「映射到A股…避免追高。」）——
    深度达标但内容模板化，用户一眼识破「又偷懒了」。本闸按「同一句 ≥14 字出现在
    ≥3 个不同叙述字段」判万能模板，FAIL 拦部署。扫描面与 G16 相同（真渲染的长文
    主字段 + 全站 deduce 三候选），不含 chip 标签与量化池。

    R100z136：归属采集拆到 collect_g17_owners（与 check_narrative.py 预检共用）。
    """
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G17", f"data.js 解析失败：{e}")
        return

    owner = collect_g17_owners(D)
    dups = sorted(((s, o) for s, o in owner.items() if len(o) >= 3),
                  key=lambda x: -len(x[1]))
    if dups:
        top = dups[:3]
        det = "；".join(f"「{s[:30]}…」×{len(o)} 处" for s, o in top)
        r.fail("G17", f"发现 {len(dups)} 句万能模板复读（≥3 个字段共用同一句，防 AI 偷懒硬闸）：{det}")
    else:
        r.ok("G17", f"无跨字段模板复读（扫描 {sum(len(o) for o in owner.values())} 句次 / {len(owner)} 句）")


def g18_policy_signals(r):
    """G18 政策信号解码闸门（R100z107b）：policySignals 若存在则必须结构合法。

    由 enrich_policy.py 在 19:30 叙事步写回。非 19:30 叙事步部署时该节点可能尚未生成
    （由前一次 19:30 叙事步持久化在 data.js 中），缺失属预期 → WARN 不拦。

    R100z178（2026-10-23）：加「合法但过期」判据 —— policySignals 非空、结构合法，但全部
    `date` 早于 `ashare.tradeDate` → WARN。这是 enrich_policy 降级保留上一版时的痕迹
    （旧脚本异常会静默写空、G18 全绿，页面政策卡空着也没人报警，本条同时修了那个洞）。"""
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G18", f"data.js 解析失败：{e}")
        return
    ps = D.get("policySignals")
    if ps is None:
        r.warn("G18", "policySignals 尚未生成（应由 19:30 叙事步 enrich_policy.py 写回）——"
                     "非 19:30 叙事步部署时为预期，19:30 后会补齐")
        return
    if not isinstance(ps, list):
        r.fail("G18", "policySignals 不是数组")
        return
    req = {"title", "issuer", "docType", "binding", "signalClass",
           "tier", "score", "sectors", "targets", "date", "market"}
    bad = []
    for i, it in enumerate(ps):
        if not isinstance(it, dict):
            bad.append(f"[{i}] 非对象"); continue
        miss = req - set(it.keys())
        if miss:
            bad.append(f"[{i}]{str(it.get('title', ''))[:10]} 缺{miss}")
        if not isinstance(it.get("sectors"), list) or not isinstance(it.get("targets"), list):
            bad.append(f"[{i}] sectors/targets 非数组")
        if it.get("tier") not in ("S", "A", "B", "C"):
            bad.append(f"[{i}] tier 非法 {it.get('tier')}")
    if bad:
        r.fail("G18", "policySignals 结构异常：" + "; ".join(bad[:8]))
        return
    tiers = {t: sum(1 for x in ps if x.get("tier") == t) for t in "SABC"}
    if not ps:
        r.ok("G18", "policySignals 结构合法（0 条）—— 今日要闻标题均无发文主体、按 R100z111 口径"
                    "正确过滤时属正常；真降级（解码/写盘异常）enrich_policy 会打 [DEGRADED] 并 exit 2，"
                    "不会走到这里")
    else:
        r.ok("G18", f"policySignals 结构合法（{len(ps)} 条，"
                    f"S{tiers['S']}/A{tiers['A']}/B{tiers['B']}/C{tiers['C']}）")
    # R100z178（2026-10-23）降级痕迹检测：enrich_policy 异常时会**保留上一版 policySignals**
    # 而不是静默清空，此时列表结构完全合法、上面那条 ok 照发，页面却挂着旧交易日的政策卡。
    # 用 date 与 ashare.tradeDate 比对，把「合法但过期」这一类打出来。
    td = str((D.get("ashare") or {}).get("tradeDate") or "")[:10]
    dates = [str(x.get("date") or "")[:10] for x in ps if isinstance(x, dict)]
    dates = [d for d in dates if re.match(r"^20\d{2}-\d{2}-\d{2}$", d)]
    if ps and dates and re.match(r"^20\d{2}-\d{2}-\d{2}$", td) and max(dates) < td:
        r.warn("G18", f"policySignals {len(ps)} 条全是 {max(dates)} 及更早的解码结果，"
                      f"落后于交易日 {td} —— 多为 enrich_policy 降级保留（见该脚本 [DEGRADED] 输出），"
                      f"页面政策卡展示的是旧交易日；补跑 enrich_policy.py，或核对 decode_policy 主体词表")


def g19_summary_freshness(r):
    """G19 板块综述·日期与行情一致性闸门（R100z118，修复 10/12「日期新、内容旧」事故）。

    事故：2026-10-12 的 ashare.tradeDate 已更新为当日，但 ashare.summary 仍是 10/9 的
    旧收盘综述（含「10月9日」「沪指3813.79点涨0.05%」等陈旧点位），上线即「日期更新、
    内容没更新」。本闸检测两类陈旧信号，FAIL 拦部署，强迫 19:30 每轮用当日数据重写：
      ① summary 里出现「M月D日」日期字面量且 ≠ tradeDate 的月日（说明在讲旧交易日）；
      ② summary 里出现收盘型点位（数字须紧贴「点/涨/跌」信号，排除成交额/市值等「X亿元」
         类非指数数值误伤）且数值与当日任一指数 point 偏差 > 2.0（说明在引用旧收盘点位）。
    盘中极值（早盘/盘中/最低/最高/探至/一度/开盘/午盘/尾盘）视为允许，不误伤。"""
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G19", f"data.js 解析失败：{e}")
        return
    a = D.get("ashare") or {}
    summary = a.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        r.ok("G19", "ashare.summary 为空或缺失（跳过，按其他闸覆盖）")
        return
    trade_date = str(a.get("tradeDate") or "")
    # ① 旧日期字面量
    md = re.findall(r"(\d{1,2})月(\d{1,2})日", summary)
    stale_md = []
    if trade_date:
        try:
            t_m, t_d = int(trade_date.split("-")[1]), int(trade_date.split("-")[2])
            for mm, dd in md:
                if (int(mm), int(dd)) != (t_m, t_d):
                    stale_md.append(f"{int(mm)}月{int(dd)}日")
        except Exception:                                     # noqa: BLE001
            pass
    # ② 陈旧收盘点位
    idx_pts = []
    for it in (a.get("indices") or []):
        if isinstance(it, dict) and isinstance(it.get("point"), (int, float)):
            idx_pts.append(float(it.get("point")))
    stale_pts = []
    for num in re.findall(r"(\d{4}\.\d{2})", summary):
        v = float(num)
        if not (1000 <= v <= 20000):
            continue
        if any(abs(v - p) <= 2.0 for p in idx_pts):
            continue
        pos = summary.find(num)
        suffix = summary[pos + len(num):].lstrip() if pos >= 0 else ""
        # 仅认定「收盘型点位」引用：数字须紧贴「点/涨/跌」信号（如「3813.79点」「3813.79涨」），
        # 否则「9876.54 亿元」成交额/市值/净买额等非指数数值会被误伤（宽松的上下文关键词会
        # 因邻近的指数句泄露「点/涨」而误判，故必须锚定在数字本身后缀）。
        if not any(ch in suffix[:3] for ch in ("点", "涨", "跌")):
            continue
        ctx = summary[max(0, pos - 25): pos + len(num) + 25] if pos >= 0 else ""
        if any(k in ctx for k in ("早盘", "盘中", "最低", "最高", "探至", "一度", "开盘", "午盘", "尾盘")):
            continue
        stale_pts.append(f"{num}（上下文：「{ctx.strip()}…」）")
    problems = []
    if stale_md:
        problems.append("含旧交易日日期字面量：" + "、".join(sorted(set(stale_md))))
    if stale_pts:
        problems.append("含与当日指数偏差>2.0的陈旧收盘点位：" + "；".join(stale_pts[:5]))
    if problems:
        r.fail("G19", "ashare.summary 疑似陈旧（日期已更新但内容未重写）：" + "；".join(problems)
               + "——summary 必须每轮按当日 indices/sectors/breadth 重写，不得沿用上一交易日 narrative")
    else:
        r.ok("G19", "ashare.summary 与当日 tradeDate/indices 一致（无陈旧日期或收盘点位）")


def collect_g20_dups(D):
    """G20 跨板块共享模板窗口采集（R100z136 从 g20_sector_deduce_no_template 拆出）。

    check_narrative.py 预检复用本函数。返回 [(mk, window, labels), ...]：
    同一市场内任一 ≥12 字连续子串（含 ≥6 个汉字）同时出现在 ≥3 个板块的
    deduce.main/counter 中即判跨板块模板套话。与 g20 同扫描面同阈值。
    """
    out = []
    for mk in ("ashare", "us"):
        sec = D.get(mk) or {}
        fields = []  # (label, text)
        for arr in ("sectorsUp", "sectorsDown"):
            for i, s in enumerate(sec.get(arr) or []):
                if isinstance(s, dict):
                    dd = s.get("deduce")
                    if isinstance(dd, dict):
                        if dd.get("main"):
                            fields.append((f"{mk}.{arr}[{i}].main", str(dd.get("main"))))
                        if dd.get("counter"):
                            fields.append((f"{mk}.{arr}[{i}].counter", str(dd.get("counter"))))
        win_owner = {}  # window -> set(field label)
        for label, text in fields:
            n = len(text)
            for st in range(0, n - 11):
                w = text[st:st + 12]
                if len(re.findall(r"[\u4e00-\u9fff]", w)) < 6:
                    continue
                win_owner.setdefault(w, set()).add(label)
        for w, o in sorted(win_owner.items(), key=lambda x: -len(x[1])):
            if len(o) >= 3:
                out.append((mk, w, sorted(o)))
    return out


def g20_sector_deduce_no_template(r):
    """G20 板块 deduce 防模板化闸门（R100z118，修复 us 板块 deduce 跨板块套话）。

    事故：us.sectorsUp/Down 的 deduce.main/counter 普遍套用同一模板句（仅换板块名/涨跌幅，
    如「X周五强势收涨，资金主动配置确认主线；跟踪龙头站稳均线即确认延续」），五个板块雷同。
    本闸在「同一市场」内检测：任一 ≥12 字连续子串同时出现在 ≥3 个板块的 deduce.main（或
    counter）中 → 判定为跨板块共享模板，FAIL 拦部署，强迫逐板块按真实驱动重写。
    阈值 12 字 + 跨 3 板块，正常多样化写作不会误伤。

    R100z136：窗口采集拆到 collect_g20_dups（与 check_narrative.py 预检共用）。
    """
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G20", f"data.js 解析失败：{e}")
        return

    dups = collect_g20_dups(D)
    problems = []
    by_mk = {}
    for mk, w, labels in dups:
        by_mk.setdefault(mk, []).append((w, labels))
    for mk, items in by_mk.items():
        top = items[:2]
        det = "；".join(f"「{w}」×{len(o)} 板块" for w, o in top)
        problems.append(f"{mk}：{len(items)} 处跨≥3板块共享≥12字模板句（{det}）")
    if problems:
        r.fail("G20", "板块 deduce 存在跨板块模板套话（防偷懒硬闸）：" + "；".join(problems)
               + "——deduce.main/counter 必须按各板块真实隔夜/当日驱动分别撰写，每板块结论与验证信号必须不同")
    else:
        r.ok("G20", "板块 deduce 无跨≥3板块共享≥12字模板句（逐板块定制）")


def g21_failpool_disjoint(r):
    """G21 失效归档池与在池互斥闸门（R100z120，修复「同一标的既在确认池又在失效池」）。

    事故：2026-10-12 截图发现 603185 弘元绿能、002912 中新赛克等标的同时出现在断板确认池与
    断板失效池。根因：生成侧 roll_fail_pool 滚动保留旧归档时未排除当前已在池的 code——
    某票先失效归档、之后又重新走完形态达标进池，旧归档未清，导致同一 code 两池共存。
    失效池语义本应为「已失效且当前不在池」。修复：common.roll_fail_pool 增 exclude_codes 参数，
    各模块生成脚本在滚动旧归档时剔除当前在池 code（check_duanban / harmonic_detect /
    accumulation_score / breakout_pool）。本闸做硬校验，防止该 bug 回归：四模块 failPool 的
    code 集合与各自在池（confirmed+watching / confirmPool+watchPool / scored / passed）的
    code 集合必须互斥，有交集即 FAIL。"""
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G21", f"data.js 解析失败：{e}")
        return
    specs = [
        ("duanban", ("confirmed", "watching"), "confirmed+watching"),
        ("harmonic", ("confirmPool", "watchPool"), "confirmPool+watchPool"),
        ("breakoutPool", ("passed",), "passed"),
        ("accumulation", ("scored",), "scored"),
    ]
    problems = []
    for mod, fields, label in specs:
        node = D.get(mod) or {}
        in_pool = set()
        for f in fields:
            for it in (node.get(f) or []):
                if isinstance(it, dict) and it.get("code"):
                    in_pool.add(str(it.get("code")))
        fail = node.get("failPool") or []
        fail_codes = set()
        for it in fail:
            if isinstance(it, dict) and it.get("code"):
                fail_codes.add(str(it.get("code")))
        overlap = in_pool & fail_codes
        if overlap:
            problems.append(f"{mod}：{label} 与 failPool 共 {len(overlap)} 只重叠（"
                           + "、".join(sorted(overlap)[:10])
                           + ("…" if len(overlap) > 10 else "") + "）")
    if problems:
        r.fail("G21", "失效归档池与在池非互斥（bug 回归）：" + "；".join(problems)
               + "——失效池语义=已失效且当前不在池；生成侧 roll_fail_pool 必须传 exclude_codes 剔除在池 code")
    else:
        r.ok("G21", "四模块 failPool 与各自在池 code 完全互斥（无重叠）")


def g22_news_level_noise(r):
    """G22 要闻信息等级 + 盘面杂音闸门（R100z157，WARN 级）。

    背景：用户一直以为要闻「按信息等级排列」，实测五节组内完全按 AI 写入顺序渲染，
    没有任何 level 维度；杂音正则（NEWS_STATS_PAT）也只护个股新闻抓取层，AI 把
    「N只股涨停/附股/炸板」写成要闻时无人拦。清洗+打级由 sweep_news.py 在 deploy.sh
    里完成（幂等、零网络）。本闸只做**复核**：
      · 存在盘面统计类标题 → WARN（说明 sweep_news 没跑到或跑失败，或 AI 刚写回新条目）
      · 存在缺 level 的条目 → WARN（同上，16:00/19:30 步骤在部署前跑本闸属正常态）
    刻意设为 WARN 而不 FAIL：19:30 的 prompt 先跑 audit 再 deploy，而 sweep 在 deploy.sh 里，
    若做成 FAIL 会让「AI 刚写好、还没到部署」的正常态直接卡死叙事步。
    """
    try:
        from sweep_news import NEWS_KEYS, is_noise
    except Exception as e:                                   # noqa: BLE001
        r.warn("G22", f"无法导入 sweep_news 口径（{e}），跳过要闻等级复核")
        return
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G22", f"data.js 解析失败：{e}")
        return
    total = missing = 0
    noise = []
    for mk in ("ashare", "us"):
        sec = D.get(mk) or {}
        for key in NEWS_KEYS:
            for it in (sec.get(key) or []):
                if not isinstance(it, dict):
                    continue
                total += 1
                if it.get("level") not in (0, 1, 2):
                    missing += 1
                if is_noise(it.get("title")):
                    noise.append(f"{mk}.{key}: {str(it.get('title'))[:36]}")
    if not total:
        r.ok("G22", "五节要闻为空（非交易日/待写），跳过等级复核")
        return
    if noise:
        r.warn("G22", f"要闻仍含盘面统计类噪音 {len(noise)} 条（deploy.sh 的 sweep_news 会自动剔除）："
               + "；".join(noise[:4]) + ("…" if len(noise) > 4 else ""))
    if missing:
        r.warn("G22", f"{missing}/{total} 条要闻无 level（0重点/1关注/2常规）——"
               "部署前 sweep_news.py 会补齐；若已部署仍缺，说明该跳没跑")
    if not noise and not missing:
        lv = {0: 0, 1: 0, 2: 0}
        for mk in ("ashare", "us"):
            sec = D.get(mk) or {}
            for key in NEWS_KEYS:
                for it in (sec.get(key) or []):
                    if isinstance(it, dict) and it.get("level") in lv:
                        lv[it["level"]] += 1
        r.ok("G22", f"要闻等级齐备且无盘面噪音（{total} 条：重点 {lv[0]} / 关注 {lv[1]} / 常规 {lv[2]}）")


def g23_cross_stock_phrasing(r):
    """G23 跨标的措辞复读软闸（R100z157，WARN 级）。

    背景（审查报告「优化-5」）：G17 拦的是「同一句被 ≥3 个**字段**共用」，而连板 story 的
    【风险】段、断板 story 的尾巴句属于「每只票各有一个独立字段」，同一条模板句在 13 只票里
    各出现一次，G17 的跨字段口径扫不到——整池票的叙事会退化成「只换名字的同一篇文章」。

    判定：把 story 按句切分（。；！？），归一化去空白/标点后统计「同一句出现在几只票」，
    当某句在 ≥3 只票重复且占比 ≥30% 时，判为模板化措辞并 WARN（只提示、不拦部署——
    真实场景里同一板块多只票共享行业背景句是合理的，硬拦会逼 AI 编造差异）。

    只扫两个池：ashare.lianban（连板）与 duanban.confirmed/watching（断板反包）。
    """
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G23", f"data.js 解析失败：{e}")
        return

    def sentences(txt):
        out = []
        for seg in re.split(r"[。；！？\n]", str(txt or "")):
            s = re.sub(r"[\s\u3000]", "", seg)
            if len(s) >= 14:
                out.append(s)
        return out

    pools = []
    lb = [x for x in ((D.get("ashare") or {}).get("lianban") or []) if isinstance(x, dict)]
    if lb:
        pools.append(("连板 story", lb))
    db = D.get("duanban") or {}
    dp = [x for x in (db.get("confirmed") or []) + (db.get("watching") or []) if isinstance(x, dict)]
    if dp:
        pools.append(("断板 story", dp))

    hits, scanned = [], 0
    for label, items in pools:
        cnt = {}
        for it in items:
            txt = it.get("story") or ""
            for s in set(sentences(txt)):
                cnt[s] = cnt.get(s, 0) + 1
                scanned += 1
        n = len(items)
        for s, c in cnt.items():
            if c >= 3 and c / n >= 0.3:
                hits.append(f"{label}「{s[:26]}…」{c}/{n} 只共用")
    if not scanned:
        r.ok("G23", "连板/断板池为空，跳过跨标的措辞复核")
        return
    if hits:
        r.warn("G23", f"同段措辞在多只票间重复（模板化倾向，{len(hits)} 处）：" + "；".join(hits[:4])
               + ("…" if len(hits) > 4 else ""))
    else:
        r.ok("G23", f"连板/断板故事逐票措辞无跨票模板复用（扫描 {scanned} 句次）")


def g24_unchecked_disclosure(r):
    """G24 未核查维度不得写成「已核查无风险」（R100z160）。

    事故（2026-10-21 08:30 实报）：报告里写「黑名单已核查无风险」，而当日
    risk_blacklist.json 实测 nameSrc=local（push2 不可达 → ST/退市维度没判）、
    unknown 188 条全是 np-anotice HTTP 567（减持/业绩预减维度整批没取到）——
    三个维度根本没过数据，却给出了绝对结论。把「未知」当「安全」卖是这类闸门最坏的失效模式：
    黑名单本来就拦不住坏票的「漏网」，再叠一句"已核查"就等于把兜底也拆了。

    判据：risk_blacklist.json 的 verdict != full（或 dims 里存在 unchecked/partial）时，
    data.js 任何渲染文本出现绝对化安全表述即 FAIL；verdict=full 时该表述合法，不拦。
    文本模式取窄（只认明确的"已核查+无风险"组合），避免误伤「无风险偏好」「风险可控」这类正常措辞。
    """
    path = os.path.join(HERE, "risk_blacklist.json")
    if not os.path.exists(path):
        r.ok("G24", "未找到 risk_blacklist.json，跳过未核查披露复核（黑名单未跑，G-黑名单闸自行提示）")
        return
    try:
        bl = json.load(open(path, encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001
        r.fail("G24", f"risk_blacklist.json 解析失败：{e}")
        return
    if not isinstance(bl, dict):
        r.fail("G24", "risk_blacklist.json 结构异常（须为对象）")
        return

    dims = bl.get("dims") or {}
    verdict = bl.get("verdict")
    if verdict is None:          # 旧版 JSON 没有 verdict：按 unknown/failed 推断，不静默放行
        verdict = "full" if not (bl.get("unknown") or bl.get("failed")) else "partial"
    bad_dims = [k for k, v in dims.items()
                if isinstance(v, dict) and v.get("status") not in (None, "ok")]
    if verdict == "full" and not bad_dims:
        r.ok("G24", f"黑名单 {bl.get('checked', 0)} 只各维度均已核查（verdict=full），"
                    f"允许「已核查」表述")
        return

    ABS_PAT = re.compile(
        r"已核查无风险|核查无风险|经核查无风险|核查后无风险|确认无风险"
        r"|黑名单.{0,4}?(?:全部|均已|已)?(?:核查)?(?:通过|完毕|无风险)"
        r"|硬风险(?:已)?全部排除")
    NEG = re.compile(r"(未|没|尚未|无法|不能|不应|不等于|并非)")
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G24", f"data.js 解析失败：{e}")
        return

    hits = set()
    for p, v in walk_paths(D):
        # walk_paths 对每个标量会产出两次（父键一次、递归叶子一次），这里用集合去重
        if not isinstance(v, str):
            continue
        for m in ABS_PAT.finditer(v):
            pre = v[max(0, m.start() - 4):m.start()]
            if NEG.search(pre):          # 「尚未通过」「不是因为…」属如实/否定表述
                continue
            if re.search(r"未|没|尚未", m.group(0)):   # 「黑名单未全部通过」是如实披露，不算违规
                continue
            hits.add(f"{p}：{m.group(0)}")
    hits = sorted(hits)

    un = bl.get("uncheckedDims") or []
    detail = ("；".join(un[:4]) if un else
              f"verdict={verdict}，未达 ok 的维度："
              f"{'、'.join(bad_dims) or '见 unknown（旧版 JSON 无 dims 字段）'}")
    if hits:
        r.fail("G24", f"黑名单维度未核查（{detail}）却出现绝对安全表述"
                      f"（{len(hits)} 处）：" + "；".join(hits[:4])
                      + (" …" if len(hits) > 4 else "")
                      + "——须改为原样点名未核查维度（R100z160）")
    else:
        # 不占 WARN 基线：维度缺失是数据源问题（每日都会发生），它本身不该成为常态告警；
        # 真正要拦的是「把缺失说成安全」，故判 PASS 但把口径写进输出，方便汇报照抄。
        r.ok("G24", f"黑名单维度未全核查（{detail}）——本次文本无绝对表述；"
                    f"但汇报（微信/HANDOVER）必须原样点名未核查维度，严禁写「已核查无风险」")


def g25_memory_hygiene(r):
    """G25 记忆注入层体检（R100z161）。

    事故（2026-10-21）：`.workbuddy/memory/MEMORY.md` 自称「故意写短」，实际涨到
    **19,929 字节 / 52 行**，其中「维护」4 行占 27%（全是 changelog）。宿主注入时
    **静默截断**，硬约束读不全 —— 当天我就把「8 只四类配比」这条早已写在事实源里的
    规则判成"没写过"，连错两版判据。记忆层不设闸，就会「越优化越读不到」。

    只查两件事：① 体积（>8KB 说明又在堆 changelog）；② 路由表在位（否则不知道去读哪节）。
    只 WARN 不 FAIL —— 记忆臃肿不影响 data.js 正确性，但必须响亮提示。
    """
    path = os.path.join(HERE, ".workbuddy", "memory", "MEMORY.md")
    if not os.path.exists(path):
        r.ok("G25", "本工作区无 MEMORY.md，跳过")
        return
    try:
        raw = open(path, "rb").read()
        txt = raw.decode("utf-8", "ignore")
    except Exception as e:                                   # noqa: BLE001
        r.warn("G25", f"MEMORY.md 读取失败：{e}")
        return
    size, hard = len(raw), 8192
    probs = []
    if size > hard:
        probs.append(f"{size} 字节 > {hard} 上限（超 {size - hard}）——长段结论迁去 PROJECT-MEMORY.md")
    # R100z171：原判据是字面量 `"先读那一节" in txt`，我把小节标题改写成「动之前先读哪节」
    # 就直接失明（WARN 报「缺路由表」其实表还在）——凡是**判文案字面量**的 gate 都会这样断。
    # 改为结构判据：`## 0.` 小节 + 「先读」语义 + 紧跟一张「要动什么 / 读哪节」表。
    if not re.search(r"(?m)^##\s*0\..*(先读|读哪节)", txt):
        probs.append("缺「## 0. 要动这块，先读那一节」路由小节（没有它就只能凭印象 grep）")
    elif "| 要动什么" not in txt:
        probs.append("「## 0.」路由小节里没有「| 要动什么 |」表格（标题在、表没了＝空转）")
    if probs:
        r.warn("G25", "记忆注入层需整治：" + "；".join(probs) + "（R100z161）")
    else:
        r.ok("G25", f"MEMORY.md {size} 字节，路由表在位")


def g26_pool_sector_keys(r):
    """G26 机械池板块键对齐（R100z163）。

    事故（2026-10-21）：机械池在**前一交易日 19:30** 按当时的 `aiPrediction.sectors` 建键，
    次日 08:30 盘前重新定板块、改几个名字，整批键就对不上——当天实测 6 个板块里 4 个
    「无同名板块」（医疗保健 / 消费电子 / 计算机设备 / 航空运输）。而 `check_stock_pool`
    是按板块名找池的，找不到就只 WARN「点名即可」→ **「④ 断板反包必须取自机械池」
    这条硬约束对这 4 个板块整轮失明**，页面看上去一切正常。
    prompt 当时还写着「严禁带 --write，本任务不重建机械池」，等于把错位锁死一整天。

    修法：08:30 定完板块名后跑一次
        build_stock_pool.py --write --sectors "板块1,板块2,…"
    （只换键不换股，候选仍由四个引擎机械产出）。本闸只做核对，WARN 不拦部署——
    真正的硬拦在 check_stock_pool：键对齐了它才有数据可判。
    """
    pool_path = os.path.join(HERE, "stock_pool.json")
    if not os.path.exists(pool_path):
        r.warn("G26", "未找到 stock_pool.json（build_stock_pool.py 未跑）——机械池没进决策链")
        return
    try:
        pool = json.load(open(pool_path, encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001
        r.warn("G26", f"stock_pool.json 解析失败：{e}")
        return
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.warn("G26", f"data.js 读取失败，跳过键对齐复核：{e}")
        return
    keys = [k for k in (pool.get("bySector") or {}) if str(k).strip()]
    names = [str((s or {}).get("sector") or "").strip()
             for s in ((D.get("aiPrediction") or {}).get("sectors") or [])
             if str((s or {}).get("sector") or "").strip()]
    if not keys or not names:
        r.warn("G26", f"机械池键 {len(keys)} 个 / 预测板块 {len(names)} 个——有一侧为空，无法对齐")
        return

    def segs(x):
        """段级切分（·、/／),与 validate_stocks.check_stock_pool 同一口径。
        不做子串，避免「消费电子」被「消费·食品加工」误牵。"""
        return [y.strip() for y in re.split(r"[·、/／,，\s]+", str(x or "")) if y.strip()]

    def hit(name):
        if name in keys:
            return name
        ss = segs(name)
        if ss:
            for k in keys:
                ks = segs(k)
                if ks and (ss[0] == ks[0] or ss[0] in ks or ks[0] in ss):
                    return k
        return None

    missing = [n for n in names if not hit(n)]
    used = {hit(n) for n in names if hit(n)}
    extra = [k for k in keys if k not in used]
    if not missing and not extra:
        r.ok("G26", f"机械池键与预测板块一一对齐（{len(names)} 个板块，候选数据日 "
                    f"{pool.get('candidateDate') or pool.get('date') or '未知'}）")
        return
    # R100z167：池键漂移本身**不再是缺陷**——check_stock_pool 已改为「键没对上就用全池
    # 断板候选核对」，硬约束永不失明。这里只判定还有没有可核对的断板候选：
    # 有 → ok（记明漂移数量，不给运维派活）；一只都没有 → 那才是真·无货，WARN。
    db_all = [it for v in (pool.get("bySector") or {}).values() for it in (v or [])
              if str(it.get("src") or "").startswith("断板")]
    if db_all:
        r.ok("G26", f"池键与预测板块有 {len(missing)} 个不同名（{('、'.join(missing))}），"
                    f"但全池 {len(db_all)} 只断板候选已兜底核对，「④ 断板取自池内」不失明；"
                    f"08:30 重键（--sectors）可恢复板块级精确核对")
        return
    parts = []
    if missing:
        parts.append(f"{len(missing)} 个预测板块在池里没有键（{('、'.join(missing))}）")
    if extra:
        parts.append(f"池里有 {len(extra)} 个已不用的旧键（{('、'.join(extra))}）")
    r.warn("G26", "机械池键与预测板块错位，且**全池无任何断板候选**："
                  + "；".join(parts) + "——断板槽位整轮只能自选，须在汇报点名（R100z167）")


def g27_memory_reachability(r):
    """G27 记忆可达性 / 时效 / 积压（R100z166，2026-10-21）。

    事故一（可达性，当天真实发生）：`PROJECT-MEMORY.md` 的 `24.11` / `24.12` 被写成
    `## 二十四·十一、` / `## 二十四·十二、`（中文编号 + 误用二级），而 `MEMORY.md`
    路由表写的是阿拉伯数字 `24.11` / `24.12`。结果 `grep 24.11` 只命中 `### 24.11a`，
    **主标题命不中** —— 该节对检索者等于不存在。我当天正是靠记忆里「只看断板槽位」
    那半句去改代码，而同一节的完整判据读不到。
    G25 只数「路由表行数 ≥ N」，**不验证路由指向的目标是否真实存在**，这里补上。

    事故二（时效）：运行期记忆里 7 处「下一个 A 股交易日：2026-10-08，届时…」
    在 13 天后仍以未来时态陈述。记忆是追加式的，**旧条目的「届时」永远停在未来**，
    读到会误判当前状态（以为节后还没开盘）。

    判据（可达性）：`MEMORY.md` 里每个 `24.x` 必须在 `PROJECT-MEMORY.md` 有 `24.x` 开头标题。
      FAIL —— 确定性强、零误报，修法 30 秒（给标题补阿拉伯数字），
      不修就等于「记忆里有一条我永远读不到的规则」。
    判据（时效）：`届时 / 下一个 / 下次` **前 30 字符内**有日期、该日期 < 今天、且整行未带
      快照类标记（`已过期/快照/勿当现状/已作废/已自愈`）→ 陈旧叙述。WARN。
      ⚠️ 窗口必须收窄到 30 字符。首版按「整行任一处过期日期」判，8 条命中里 5 条误报：
        · 常驻提醒「**下次放假前**只需复查这一条还在不在」（旧日期在 500 字外）
        · 已带标记「以下整条是 2026-10-16 深夜的**快照**…勿当现状读」
        · changelog「部署：未提交，延续「等**下次**自动化跑」」（R100zNNN 条目，日期天然在行首）
      真正会让人误判当前状态的只有「日期紧贴关键字」这一种，例如
      「下一个 A 股交易日：**2026-10-08**（国庆休市），届时…」。
    判据（积压）：日志 >30 天未蒸馏 → WARN。本闸顺便充当蒸馏的**执行者**
      （此前该规则只写在记忆里，全项目零执行点，属空转型）。
    """
    base = os.path.join(HERE, ".workbuddy", "memory")
    mem = os.path.join(base, "MEMORY.md")
    pm = os.path.join(base, "PROJECT-MEMORY.md")
    import glob                                              # noqa: PLC0415
    from datetime import date as dt_date                     # noqa: PLC0415
    if not os.path.exists(mem) or not os.path.exists(pm):
        r.ok("G27", "本工作区无 MEMORY.md / PROJECT-MEMORY.md，跳过")
        return
    try:
        mtxt = open(mem, encoding="utf-8", errors="ignore").read()
        ptxt = open(pm, encoding="utf-8", errors="ignore").read()
    except Exception as e:                                   # noqa: BLE001
        r.warn("G27", f"记忆文件读取失败：{e}")
        return

    # ---- 1) 路由可达性：MEMORY.md 引用的 24.x 必须有同名标题 ----
    # ⚠️ 必须要求「标题里的节号与引用**完全相等**」：首版用 `24\.13(?![0-9])`，
    #    结果 MEMORY.md 引 `24.13` 时，`#### 24.13a` 这种子项标题就能满足它 ——
    #    故障注入（把 `### 24.13` 改回 `### 二十四·十三、`）居然仍然 PASS。
    #    而原版 24.11 事故正是这个形状（主标题中文、子标题 24.11a 在）。
    #    收尾改成 `(?![0-9A-Za-z])`，子标题不再冒充主标题。
    refs = sorted({m.group(0) for m in re.finditer(r"24\.\d+[a-z]?", mtxt)})
    missing = [x for x in refs
               if not re.search(r"^#{2,4}\s*" + re.escape(x) + r"(?![0-9A-Za-z])", ptxt, re.M)]
    if missing:
        r.fail("G27", f"MEMORY.md 路由指向的节在 PROJECT-MEMORY.md 里 grep 不到："
                      f"{('、'.join(missing))}——给标题补阿拉伯数字（如「## 二十四·十一」→「### 24.11」）"
                      f"，否则该节等于不存在（R100z166）")
    else:
        r.ok("G27", f"路由可达：MEMORY.md 引用 {len(refs)} 个 24.x，全部命中标题")

    # ---- 2) 时效：关键字前 30 字符内的日期是否已过 ----
    today = dt_date.today()
    KW = ("届时", "下一个", "下次")
    SNAP = ("已过期", "快照", "勿当现状", "已作废", "已自愈")
    DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
    stale = []

    def stale_in(label, txt):
        for i, line in enumerate(txt.split("\n"), 1):
            if any(s in line for s in SNAP):
                continue
            for kw in KW:
                pos = line.find(kw)
                if pos < 0:
                    continue
                near = DATE_RE.findall(line[max(0, pos - 30):pos])
                m2 = re.match(r"[^0-9]{0,10}(20\d{2}-\d{2}-\d{2})", line[pos:])
                cand = near[-1] if near else (m2.group(1) if m2 else None)
                if cand:
                    try:
                        if dt_date.fromisoformat(cand) < today:
                            stale.append(f"{label}:{i}（{cand}）")
                    except ValueError:
                        pass
                break

    stale_in("MEMORY.md", mtxt)
    stale_in("PROJECT-MEMORY.md", ptxt)
    for p in sorted(glob.glob(os.path.join(base, "automations", "*", "memory.md"))):
        try:
            stale_in(os.path.basename(os.path.dirname(p))[:8],
                     open(p, encoding="utf-8", errors="ignore").read())
        except Exception:                                    # noqa: BLE001
            continue
    if stale:
        r.warn("G27", f"{len(stale)} 处「届时/下一个」紧贴已过日期却仍以未来时态陈述"
                      f"（{('、'.join(stale[:6]))}{'…' if len(stale) > 6 else ''}）"
                      f"——行尾加「（⏳ 已过期：…）」，别删原文（R100z166）")

    # ---- 3) 积压：>30 天日志未蒸馏 ----
    olds = []
    for p in sorted(glob.glob(os.path.join(base, "20*.md"))):
        try:
            d = dt_date.fromisoformat(os.path.basename(p)[:-3])
        except ValueError:
            continue
        age = (today - d).days
        if age > 30:
            olds.append(f"{os.path.basename(p)[:-3]}({age}天)")
    if olds:
        r.warn("G27", f"{len(olds)} 份日志超 30 天未蒸馏（{('、'.join(olds[:6]))}）"
                      f"——结论并入 PROJECT-MEMORY.md 后删除，否则检索边界无限扩大（R100z166）")


# G28 分格时间戳表：与 refresh_stamp.py 的 SLOTS **必须逐项一致**，G28 会跨文件并排比。
# 历史 bug 类型就是这类「两处各写一份清单」——只在一边加字段，另一边静默漏取。
STAMP_SLOTS = [
    (("ashare", "updatedAt"), "A股收盘"),
    (("us", "updatedAt"), "美股收盘"),
    (("panorama", "updatedAt"), "全球市场全景"),
    (("harmonic", "updatedAt"), "谐波形态"),
    (("accumulation", "updatedAt"), "吸筹打分"),
    (("breakoutPool", "updatedAt"), "横盘启动"),
    (("fullScan", "updatedAt"), "全量扫描"),
    (("stopTrack", "updatedAt"), "止损跟踪"),
]


def _stamp_slots_from_script():
    """从 refresh_stamp.py 源码里取 SLOTS 字面量（不 import，避免执行副作用）。"""
    p = os.path.join(HERE, "refresh_stamp.py")
    try:
        tree = ast.parse(open(p, encoding="utf-8").read())
    except Exception:                                        # noqa: BLE001
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SLOTS" for t in node.targets):
            try:
                return [(tuple(pth), lbl) for pth, lbl in ast.literal_eval(node.value)]
            except Exception:                                # noqa: BLE001
                return None
    return None


def g28_freshness_consistency(r):
    """G28 数据新鲜度一致性（R100z167，2026-10-21，用户实报「怎么那么多遗留问题」）。

    事故一（页头撒谎）：页面头部「数据更新」读 data.js **顶层** `updatedAt`，而这一个字段
    被 5 个脚本各自覆盖 —— 谁最后跑谁赢。实测当天 A股 17:20 已更新，页头还停在
    `16:01（韩股 10/21 收盘 已更新）`；第二天更糟，页头写 10-20 而数据已是 10-21。
    根因不是「某个脚本忘了写」，而是**这个字段没有单一主人**。
    修法：各市场/模块只写自己那一格（`ashare.updatedAt` / `us.updatedAt` /
    `panorama.updatedAt` / 四模块 / `stopTrack`），部署时由 `refresh_stamp.py` 取**最大
    时间戳**生成顶层文案 —— 单一来源、幂等。本闸守的就是这件事有没有真的发生。

    事故二（ashare 那格没人写）：`ashare.updatedAt` 长期由 AI 手写，脚本只写 tradeDate。
    于是出现「`ashare.updatedAt` 仍显示 10-20 17:22」被当成免责声明挂在报告里好几天。
    现在 `update_ashare_sectors.py` 与 tradeDate/status **同进同退**，本闸校验同日。

    判据：
      a) 顶层 `updatedAt` 的时间戳 == 各分格里的最大值。不等 → FAIL，
         修法一行：跑 `refresh_stamp.py`（deploy.sh 已内置，说明有人绕过 deploy 直接改了数据）。
      b) `ashare.updatedAt` 的日期 == `ashare.tradeDate`。缺/不同日 → FAIL。
      c) 各分格戳日期早于 `ashare.tradeDate` → WARN（该格是旧交易日的货，但页面照挂着）。
      d) `refresh_stamp.py` 的 SLOTS 与本地 STAMP_SLOTS 不一致 → FAIL（清单漂移＝静默漏取）。
      e) `duanban.generatedAt` 早于 `ashare.tradeDate` → **FAIL**（断板是本看板核心模块，
         陈旧断板一路部署上线＝产品事故；prompt 侧 19:30 第 0 步的核验此前查的是两个不存在的键，
         闸门这边若再只给 WARN，就是双重不设防 —— R100z178）。
         四模块 `tradeDate` 早于 `ashare.tradeDate` → WARN（full_scan 失败不清场是 16:00
         prompt 显式允许的降级，不是漏跑，所以不升级）。
         `duanban.generatedAt` 缺失/不可解析 → WARN（无法判断，不拦；但要点出来）。
    非交易日（status 含「休市」）或 tradeDate 不可解析时整条跳过 —— 休市期数据不动是正常的。
    """
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.warn("G28", f"data.js 解析失败，跳过新鲜度一致性核对：{e}")
        return

    # ---- d) 清单漂移（先查，后面两条判据都依赖它）----
    script_slots = _stamp_slots_from_script()
    if script_slots is None:
        r.warn("G28", "读不到 refresh_stamp.py 的 SLOTS（文件缺失或写法变了），"
                      "无法并排比对各格清单，请确认该脚本仍在部署链里")
    else:
        mine = {tuple(p) for p, _ in STAMP_SLOTS}
        theirs = {tuple(p) for p, _ in script_slots}
        if mine != theirs:
            only_a = sorted(".".join(p) for p in mine - theirs)
            only_b = sorted(".".join(p) for p in theirs - mine)
            r.fail("G28", f"分格清单漂移：audit 有而 refresh_stamp 没有 {only_a or '无'}；"
                          f"refresh_stamp 有而 audit 没有 {only_b or '无'} —— "
                          f"两处必须逐项一致，否则新加的那一格永远进不了页头（R100z167）")
        else:
            r.ok("G28", f"分格清单一致：{len(mine)} 格（ashare/us/panorama/四模块/stopTrack）")

    a = D.get("ashare") or {}
    td = str(a.get("tradeDate") or "").strip()[:10]
    status = str(a.get("status") or "")
    if ("休市" in status) or ("非交易日" in status) or not re.match(r"^20\d{2}-\d{2}-\d{2}$", td):
        r.ok("G28", f"非交易日/休市（status={status or '空'}，tradeDate={td or '空'}），"
                    f"跳过新鲜度一致性核对")
        return

    TS = re.compile(r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})")

    def dig(path):
        cur = D
        for k in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(k)
        return cur

    # ---- b) ashare 那格必须与 tradeDate 同日 ----
    m = TS.search(str(a.get("updatedAt") or ""))
    if not m:
        r.fail("G28", "ashare.updatedAt 缺失或没有可解析时间戳 —— "
                      "update_ashare_sectors.py 必须与 tradeDate/status 同时写入该格（R100z167）")
    elif m.group(1) != td:
        r.fail("G28", f"ashare.updatedAt 停在 {m.group(1)}，而 tradeDate 已是 {td} —— "
                      f"该格只能由脚本写，AI 手写/漏写都会造成新鲜度谎报（R100z167）")

    # ---- a) 顶层 = 各格最大值 ----
    rows, old_slots = [], []
    for path, label in STAMP_SLOTS:
        v = dig(path)
        mm = TS.search(str(v or ""))
        if not mm:
            continue
        rows.append(("%s %s" % (mm.group(1), mm.group(2)), label))
        if mm.group(1) < td:
            old_slots.append(f"{label}={mm.group(1)}")
    if not rows:
        r.warn("G28", "各分格都没有可解析的 updatedAt，无法核对页头权威值")
    else:
        best_ts, best_label = max(rows, key=lambda x: x[0])
        top = TS.search(str(D.get("updatedAt") or ""))
        if not top:
            r.fail("G28", "顶层 updatedAt 缺失/不可解析 —— 页头「数据更新」会空白")
        else:
            top_ts = "%s %s" % (top.group(1), top.group(2))
            if top_ts != best_ts:
                r.fail("G28", f"页头时间戳 {top_ts} ≠ 各格最新 {best_ts}（{best_label}）—— "
                              f"顶层 updatedAt 被某个脚本覆盖成自己的时间了。"
                              f"修法：跑 refresh_stamp.py（deploy.sh 已内置该步）（R100z167）")
            else:
                r.ok("G28", f"页头时间戳 = 各格最新 {top_ts}（{best_label}）")

    # ---- c) 分格戳落后于当日交易日 ----
    if old_slots:
        r.warn("G28", f"{len(old_slots)} 格戳早于交易日 {td}：{'、'.join(old_slots[:5])}"
                      f"——该格内容是旧交易日的货，页面照挂着（非交易日误标时属正常）")

    # ---- e) 四模块/断板 快照日期落后 ----
    # R100z178（2026-10-23）：断板与四模块**分级**处理。此前两者合并成一条 WARN，等于
    # 「断板是昨天的」这件事上，prompt 侧的保险丝（19:30 第 0 步核验）查的字段名根本不存在、
    # 闸门这边又只给黄灯 —— **双重不设防**，陈旧的断板可以一路部署上线。断板是本看板的核心模块，
    # 所以升级为 FAIL。四模块保持 WARN：16:00 prompt 显式允许 full_scan 失败时「不写 fullScan、
    # 继续跑后三脚本即可」，判 FAIL 会与既有容错设计正面冲突（那是有意为之的降级，不是漏跑）。
    lag_mod = []
    for key in ("harmonic", "accumulation", "breakoutPool", "fullScan"):
        v = str((D.get(key) or {}).get("tradeDate") or "")[:10]
        if v and v < td:
            lag_mod.append(f"{key}.tradeDate={v}")
    dg = str((D.get("duanban") or {}).get("generatedAt") or "")[:10]
    if not re.match(r"^20\d{2}-\d{2}-\d{2}$", dg):
        r.warn("G28", "duanban.generatedAt 缺失或不可解析（%s）—— 无法判断断板是否为当日；"
                      "注意 duanban 没有 `date`/`updatedAt` 这两个键，唯一时效字段就是 generatedAt"
                      "（R100z178）" % (dg or "空"))
    elif dg < td:
        r.fail("G28", f"断板快照落后于交易日：duanban.generatedAt={dg} < ashare.tradeDate={td} "
                      f"—— 页面上整个断板反包模块展示的是旧交易日的货，属产品事故级。"
                      f"修法：补跑 check_duanban.py + merge_duanban.py（16:00 数据步第 2 步）")
    if lag_mod:
        r.warn("G28", f"模块快照落后于交易日 {td}：{'、'.join(lag_mod)}"
                      f"——16:00 数据步漏跑或未换日，页面对应板块展示的是旧交易日"
                      f"（四模块保持 WARN：full_scan 失败不清场是 16:00 prompt 显式允许的降级）")


def _run_tool(script, *args, timeout=180):
    """跑维护/校验类小工具，返回 (rc, out, err)；找不到解释器或异常 → rc=-1。"""
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        return -1, "", "缺文件 %s" % script
    try:
        p = subprocess.run([sys.executable, path] + list(args), cwd=HERE,
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "%s 超时（%ds）" % (script, timeout)
    except Exception as e:                                   # noqa: BLE001
        return -1, "", "%s 执行异常：%s" % (script, e)


def _tail(out, n=3):
    lines = [x.strip() for x in (out or "").splitlines() if x.strip()]
    return "；".join(lines[-n:])


def g29_name_and_risk_hygiene(r):
    """G29 个股简称一致性 + 硬风险 red 不得留在推荐位（R100z168，2026-10-21）。

    由来（用户第二次贴截图问「怎么那么多遗留问题」之后又浮出来的一层）：
      · 名字层：AI 写要闻/精选时凭记忆填简称，实测 7 只已更名（韦尔股份→豪威集团、
        银泰黄金→山金国际、郑煤机→中创智领、金贵银业→湖南白银…），其中
        `600079 人福医药→ST人福`、`600311 荣华实业→*ST荣华` 是最危险的 ——
        **已戴帽 ST 却显示成正常公司，还被摆在「板块受益标的」里**。
        黑名单自己取的是实时行情名，两边不一致 → 同一只票两套名字。
      · 风险层：`stock_risk_blacklist.py` 每天都在算 red，但只有 `update_star.py` 会过滤；
        `check_duanban` / `breakout_pool` / `accumulation_score` 压根不读 →
        603330 天洋新材 10-09 就公告减持，到 10-21 仍挂在「断板确认池」和「量化筛选」里。

    分工：`sync_names.py` 负责校正、`risk_gate.py` 负责清理（均幂等，deploy.sh 已内置）；
    本闸只做**复验**，保证有人绕过 deploy 直接改数据时能被拦住。

    判据：
      a) `sync_names.py --check` rc=1（有差异）→ FAIL；rc=2（行情源不可达，不可判定）→ WARN。
      b) `risk_gate.py --check` rc≠0（red 仍在推荐位）→ FAIL。
      c) ST/退市型 red 出现在要闻 impacts 的 stocks 里 → WARN（语义提示：利空位放 ST 股
         是合理的，受益位不合理；名字已带 ST 前缀，由人工确认语义）。
    """
    # ---- a) 简称一致性 ----
    rc, out, err = _run_tool("sync_names.py", "--check")
    if rc == 0:
        r.ok("G29", "个股简称与行情源全部一致（%s）" % (_tail(out, 1) or "幂等"))
    elif rc == 1:
        detail = "；".join(ln.strip() for ln in (out or "").splitlines() if "→" in ln)
        r.fail("G29", "个股简称与行情源不一致（AI 写了旧名/旧帽）：%s —— "
                      "跑 sync_names.py --write（deploy.sh 已内置）" % (detail[:400] or _tail(out, 3)))
    elif rc == 2:
        r.warn("G29", "行情名源不可达，无法核对简称新鲜度（本次不可判定，非「已对齐」）：%s"
                      % (_tail(out, 2) or err))
    else:
        r.warn("G29", "sync_names.py 执行异常（rc=%s）：%s" % (rc, _tail(err, 2) or _tail(out, 2)))

    # ---- b) 推荐位不得留 red ----
    rc, out, err = _run_tool("risk_gate.py", "--check")
    if rc == 0:
        r.ok("G29", "四个推荐池（断板确认/观察、开盘精选、量化筛选、吸筹三池）无硬风险 red 标的")
    elif rc == 1:
        hits = [ln.strip() for ln in (out or "").splitlines() if "✂" in ln or "仍有" in ln]
        r.fail("G29", "硬风险 red 标的仍留在推荐位：%s —— 跑 risk_gate.py --write"
                      "（deploy.sh 已内置）" % ("；".join(hits[:4]) or _tail(out, 3)))
    else:
        r.warn("G29", "risk_gate.py 执行异常（rc=%s）：%s" % (rc, _tail(err, 2) or _tail(out, 2)))

    # ---- c) ST 股出现在要闻 impacts（语义提示，不拦部署）----
    try:
        bl = json.load(open(os.path.join(HERE, "risk_blacklist.json"), encoding="utf-8"))
        st_red = {str(c) for c, v in ((bl.get("blacklist") or {})).items()
                  if (v or {}).get("severity") == "red"
                  and any("ST/退市" in str(x) for x in (v.get("reasons") or []))}
    except Exception:                                        # noqa: BLE001
        st_red = set()
    if not st_red:
        return
    try:
        D = load_data_json()
    except Exception:                                        # noqa: BLE001
        return
    where = []

    def walk(o, path):
        if isinstance(o, dict):
            c = o.get("code")
            if isinstance(c, str) and c in st_red:
                where.append("%s(%s)" % (path, c))
            for k, v in o.items():
                walk(v, path + "." + k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, "%s[%d]" % (path, i))

    for mkt in ("ashare", "us"):
        node = D.get(mkt) or {}
        for sec in ("intlNews", "news", "policyNews", "annNews", "chipNews",
                    "bullNews", "bullish"):
            if sec in node:
                walk(node[sec], "%s.%s" % (mkt, sec))
    if where:
        r.warn("G29", "ST/退市型标的出现在要闻「非利空段」impacts %d 处：%s —— "
                      "受益位不得放 ST/退市股，请让 AI 换同板块非 ST 替代（名字已带 ST 前缀）" %
                      (len(where), "、".join(where[:6])))
    else:
        r.ok("G29", "要闻「非利空段」impacts 无 ST/退市型标的（利空段 bearNews/bearish 不设限）")


def _css_color(s, body, prop="background"):
    """从一段 CSS 声明里取颜色实值，支持 `var(--x)` 回查 `:root`。

    R100z163（2026-10-24）：视觉规范把 6 种近似背景色统一成 var(--bg-2) / var(--accent-soft)
    之后，原先只认字面 hex 的 G30 立刻误判「缺底色定义」——闸门必须跟着实现走，
    否则「统一配色」和「配色闸门」天然对撞，下一次谁都不敢动颜色。
    取不到（var 在 :root 里没定义）仍返回 None，让调用方按缺档 FAIL——放宽的是写法，不是标准。
    """
    m = re.search(r"%s:\s*(#[0-9a-fA-F]{3,8}|var\(--[\w-]+\))" % prop, body)
    if not m:
        return None
    v = m.group(1)
    if v.startswith("var("):
        name = v[6:-1].strip()
        mv = re.search(re.escape(name) + r"\s*:\s*(#[0-9a-fA-F]{3,8})", s)
        return mv.group(1) if mv else None
    return v


def _css_px(s, body, prop="font-size"):
    """从一段 CSS 声明里取 px 数值，支持 `var(--x)` 回查 `:root`。

    R100z164（2026-10-24）：把 255 处写死的 font-size 接到 --fs-* 令牌之后，
    G30 的「徽章字号过小」检查立刻把 `.risk-dot` 判成「未声明」——因为它只认 `font-size: 11.5px`。
    这和上一轮统一背景色时 `_css_color()` 遇到的是同一个坑：**实现变写法，闸门没跟上**。
    取不到（var 在 :root 里没定义）仍返回 None，让调用方按 WARN/FAIL 处理——放宽写法，不放宽标准。
    """
    m = re.search(r"%s:\s*([\d.]+)px" % re.escape(prop), body)
    if m:
        return float(m.group(1))
    m = re.search(r"%s:\s*var\((--[\w-]+)\)" % re.escape(prop), body)
    if not m:
        return None
    mv = re.search(re.escape(m.group(1)) + r"\s*:\s*([\d.]+)px", s)
    return float(mv.group(1)) if mv else None


def _hex_lum(h):
    """#rgb / #rrggbb → 感知亮度 0~255（0.299R+0.587G+0.114B）。"""
    h = (h or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        rr, gg, bb = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None
    return 0.299 * rr + 0.587 * gg + 0.114 * bb


def _wcag_contrast(fg, bg):
    """WCAG 2.1 对比度（含 sRGB gamma 校正）。返回值 ≈1~21，AA 正文要求 ≥4.5。"""
    def lin(h):
        h = (h or "").lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            return None
        out = []
        for i in (0, 2, 4):
            try:
                c = int(h[i:i + 2], 16) / 255.0
            except ValueError:
                return None
            out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    a, b = lin(fg), lin(bg)
    if a is None or b is None:
        return None
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def g30_display_prefs(r):
    """G30 展示硬偏好：把用户说过的审美红线机器化（R100z171，2026-10-21 深夜）。

    由来：用户连问两轮「检查报告」之后仍然一眼看出「徽章好丑」——
    根因不是逻辑错，而是**审美不在可编程判据里**：我把三档符号从 ⚠️ 改成 ！/？，
    `audit_pipeline` 51 条全绿，因为没有任何一条 gate 会因为「符号选得丑」FAIL。
    本闸的作用就是把用户**已经明确表达过**的展示偏好固化成判据，
    让「下一次谁（包括我自己）再改坏」在 deploy 前就撞墙，而不是等用户截图。

    判据（全部只看 index.html 源码，不跑浏览器，稳且快）：
      a) `riskBadge()` 内三档 `sym` 必须都是 ⚠️ —— 用户 2026-10-21 明确「还是改回 ⚠️」。
      b) `.risk-dot.sev-red/orange/yellow` 三档 `background` 必须**亮度单调递减**
         （红=最深 → 橙=中 → 黄=最浅），且相邻差 ≥35。
         这条是防「又一次只有一个编码通道」：实测 10px 胶囊上 `#141414` 与 `#6e6e6e`
         肉眼不可分，配色写了等于没写。
      c) `.risk-dot` 字号 ≥11px（同上，10px 时 emoji 会把底色盖满）。
      d) 涨红 `#f0435c` / 跌绿 `#0f9d78` 两个常量必须在（A 股惯例，别反）。
      e) `.risk-tip`（弹窗内长条风险提示）同样三档灰阶，且**字色对比度 ≥4.5:1**（WCAG AA）。
         用户 2026-10-21 23:04：「底色也按三档改，字体还是黄色字体」。
         这条必须同时管「深浅单调」和「对比度」——#ffd54a 黄字压 #8c8c8c 只有 2.4:1，
         只判单调的话照样做出一版看不清的东西。
      f) 断板页图例三档 span 必须齐（写了图例却漏一档，等于图例本身在骗人）。
    """
    p = os.path.join(HERE, "index.html")
    try:
        s = open(p, encoding="utf-8").read()
    except Exception as e:                                    # noqa: BLE001
        r.warn("G30", "index.html 读不到，本次无法校验展示偏好：%s" % e)
        return

    # ---- a) 徽章符号统一 ⚠️ ----
    mb = re.search(r"function riskBadge\([\s\S]{0,4000}?\n\}", s)
    if not mb:
        r.warn("G30", "index.html 里找不到 riskBadge() 函数体，跳过符号校验")
    else:
        syms = re.findall(r"sym\s*=\s*'([^']*)'", mb.group(0))
        bad = [x for x in syms if x != "⚠️"]
        if not syms:
            r.fail("G30", "riskBadge() 里没有可识别的 sym 赋值 —— 徽章符号已脱离统一口径，"
                          "用户 2026-10-21 定稿为三档统一 ⚠️")
        elif bad:
            r.fail("G30", "风险徽章符号不统一：%s（应为全部 ⚠️，底色深浅分档）—— "
                          "用户明确否掉 ！/？ 那一版，见 24.21④" % "、".join(bad))
        else:
            r.ok("G30", "风险徽章三档符号统一 ⚠️（%d 处）" % len(syms))

    # ---- b) 三档底色亮度单调 + 跨度 ----
    lum, missing = {}, []
    for k in ("red", "orange", "yellow"):
        mk = re.search(r"\.risk-dot\.sev-%s\s*\{([^}]*)\}" % k, s)
        bgs = _css_color(s, mk.group(1)) if mk else None
        if not bgs:
            missing.append(k)
            continue
        v = _hex_lum(bgs)
        if v is None:
            missing.append(k)
        else:
            lum[k] = v
    if missing:
        r.fail("G30", "风险徽章缺底色定义：%s —— 三档必须 黑底/深灰底/淡灰底 齐全"
                      "（用户 2026-10-21 指定，见 24.21④）" % "、".join(missing))
    else:
        a, b, c = lum["red"], lum["orange"], lum["yellow"]
        if not (a < b < c):
            r.fail("G30", "风险徽章底色亮度未「深→浅」递减：red=%.0f orange=%.0f yellow=%.0f —— "
                          "严重度靠底色深浅表达，顺序反了就是误导" % (a, b, c))
        elif min(b - a, c - b) < 35:
            r.fail("G30", "风险徽章相邻档亮度差过小（red=%.0f orange=%.0f yellow=%.0f，"
                          "最小差 %.0f < 35）—— 小尺寸下肉眼分不出，等于又退回单通道"
                   % (a, b, c, min(b - a, c - b)))
        else:
            r.ok("G30", "风险徽章三档底色深→浅递减且跨度足够（%.0f / %.0f / %.0f）" % (a, b, c))

    # ---- c) 徽章字号 ----
    md = re.search(r"\.risk-dot\s*\{([^}]*)\}", s)
    fs = _css_px(s, md.group(1)) if md else None
    if fs is None:
        r.warn("G30", ".risk-dot 未声明 font-size（或 var 指向的令牌在 :root 里没有 px 值），"
                      "无法判断徽章是否过小")
    elif fs < 11:
        r.fail("G30", "风险徽章 font-size=%.1fpx < 11px —— emoji 会盖满胶囊、底色不可辨，"
                      "三档配色失效（实测教训，见 24.21④）" % fs)
    else:
        r.ok("G30", "风险徽章字号 %.1fpx ≥ 11px（emoji 不会盖满胶囊）" % fs)

    # ---- d) 涨红跌绿 ----
    lack = [c for c in ("#f0435c", "#0f9d78") if c.lower() not in s.lower()]
    if lack:
        r.fail("G30", "涨跌色常量缺失：%s —— A 股惯例涨红（#f0435c）跌绿（#0f9d78），别反"
                      % "、".join(lack))
    else:
        r.ok("G30", "涨跌色常量在位（涨红 #f0435c / 跌绿 #0f9d78，A 股惯例）")

    # ---- e) 长条风险提示徽章（.risk-tip）也走三档灰阶，且黄字必须读得清 ----
    # 用户 2026-10-21 23:04：「底色也按三档改，字体还是黄色字体」。
    # 这条不是抄 spec 就完事：#ffd54a 压在全灰阶上对比度只有 2.4:1（AA 要求 4.5），
    # 淡灰底更是 0.8:1（等于白底黄字）。所以判据必须**同时**管「深浅单调」和「对比度」——
    # 只管前者，做出来的东西用户照样看不清。
    if ".risk-tip" in s:
        t_lum, t_miss, t_low = {}, [], []
        for k in ("red", "orange", "yellow"):
            mk = re.search(r"\.risk-tip\.sev-%s\s*\{([^}]*)\}" % k, s)
            body = mk.group(1) if mk else ""
            bg = _css_color(s, body)
            fg = _css_color(s, body, "color")
            if not bg or not fg:
                t_miss.append(k)
                continue
            v = _hex_lum(bg)
            if v is None:
                t_miss.append(k)
                continue
            t_lum[k] = v
            cr = _wcag_contrast(fg, bg)
            if cr is not None and cr < 4.5:
                t_low.append("%s 档 %s/%s 对比度 %.1f:1" % (k, fg, bg, cr))
        if t_miss:
            r.fail("G30", "风险提示长条徽章缺档或配色不全：%s —— 三档须 黑底/深灰底/淡灰底 + 黄字"
                          "（用户 2026-10-21 23:04 指定）" % "、".join(t_miss))
        elif not (t_lum["red"] < t_lum["orange"] < t_lum["yellow"]):
            r.fail("G30", "风险提示长条徽章底色亮度未「深→浅」：red=%.0f orange=%.0f yellow=%.0f"
                   % (t_lum["red"], t_lum["orange"], t_lum["yellow"]))
        elif t_low:
            r.fail("G30", "风险提示长条徽章字色对比度不足 WCAG AA（<4.5:1）：%s —— "
                          "黄字压不住这么浅的底，换更深底或改暗金字" % "；".join(t_low))
        else:
            r.ok("G30", "风险提示长条徽章三档灰阶 + 黄字对比度均 ≥4.5:1（亮度 %.0f/%.0f/%.0f）"
                   % (t_lum["red"], t_lum["orange"], t_lum["yellow"]))

    # ---- f) 图例三档齐 ----
    # R100z162（2026-10-24）：用户明确要求删掉「风险徽章（⚠️ 底色越深越严重）」这行图例，
    # 原话「这个删掉，我知道就行不用提示」。所以「图例必须存在」这条不再成立——
    # 但仍要拦住真正的坏情况：三档 class 若在渲染层被删/改名，徽章会全变成同一种颜色，
    # 那才是真正让人猜（用户 2026-10-21 问「颜色怎么不一样」就是那次）。
    if "风险徽章" in s:
        lg = s[s.find("风险徽章"):s.find("风险徽章") + 900]
        gone = [k for k in ("red", "orange", "yellow") if ("sev-" + k) not in lg]
        if gone:
            r.fail("G30", "风险徽章图例缺档：%s —— 图例必须三档齐全" % "、".join(gone))
        else:
            r.ok("G30", "断板页风险徽章图例三档齐全（⚠️ 底色越深越严重）")
    else:
        used = [k for k in ("red", "orange", "yellow") if ("sev-" + k) in s]
        if len(used) < 3:
            r.fail("G30", "风险徽章只渲染 %d 档（%s）—— 图例可删，但三档底色必须仍在，"
                          "否则严重/警示/参考全同色，等于没分级" % (len(used), "、".join(used) or "无"))
        else:
            r.ok("G30", "风险徽章三档仍在渲染（图例已按用户 2026-10-24 要求删除）")


def g31_deduce_prefix(r):
    """G31 deduce 双标签「反证：反证是…」（R100z173，2026-10-22）。

    由来：用户截图圈出板块卡的 `反证：反证是资金涨2.10%却净流出4.71亿元`。
    根因**不是**渲染层去重失效——是清洗只认 `反证[:：]`（带冒号），而 AI 实测写了
    19 条 `反证是…`（无冒号）→ 剥不掉 → 渲染层再补一个「反证：」= 双标签。
    这类问题闸门一直看不见，因为它是「数据内容形态」而不是结构错误。

    判据（三处都查，缺一处就等于留一条复发路径）：
      a) data.js 的 `deduce.main` / `deduce.counter` **不得**自带
         「主推：/反证：/主推是/反证是」前缀——数据层不带标签是既定口径，标签一律渲染层加。
      b) index.html 的 `_ddClean()` 前缀正则必须**同时认冒号与「是」**两种变体
         （渲染层兜底：即便数据脏了，页面也不该出现双标签）。
      c) 每处 `dd-counter` 拼接若直接取对象字段（`esc(x.counter)`）必须过 `_ddClean`。
         2026-10-22 实测就漏了一处（2330 行的降级路径），改了三处里的两处不算改完。
    """
    # ---- a) 数据层不得自带前缀 ----
    # R100z174：判据不再自带一套正则（旧版只认「：/是」，`反证在资金：` 因此漏检），
    # 谓语白名单直接复用 common 的 _DD_KEEP——三套正则各写一遍、改一处漏两处，
    # 就是这次「19 条修好了、第 20 条还在」的直接原因。
    # ⚠️ 必须判**原始值**，不能判「清洗后」：清洗函数正常工作的话，剥完当然不带前缀，
    # 那这条判据就永远绿了（反向测试当场发现过一次，别改回去）。
    from common import _DD_KEEP
    dirty = re.compile(r"^(?:主推|反证)(?!\s*" + _DD_KEEP + r")")
    viol, tot = [], 0

    def walk(o, path=""):
        nonlocal tot
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("main", "counter") and isinstance(v, str) and v.strip():
                    tot += 1
                    if dirty.match(v.strip()):
                        viol.append("%s.%s「%s…」" % (path or "?", k, v.strip()[:14]))
                else:
                    walk(v, path + "/" + k)
        elif isinstance(o, list):
            for i, x in enumerate(o):
                walk(x, path + "[%d]" % i)

    # ⚠️ 这里**不用 load_data_json()**：它靠 node 在当前工作目录读 data.js，
    # 反向测试（把改动写进临时目录再跑）会静默读回真实文件 → 假绿。
    # 自己按 HERE 解析，闸门在任意 cwd 下都测的是同一份文件。
    try:
        with open(os.path.join(HERE, "data.js"), encoding="utf-8") as f:
            txt = f.read()
        _i = txt.index("{", txt.index("window.DASHBOARD_DATA"))
        D = json.loads(txt[_i:].rstrip().rstrip(";"))
    except Exception as e:                                    # noqa: BLE001
        r.warn("G31", "data.js 读不到，跳过数据层前缀校验：%s" % e)
    else:
        walk(D)
        if viol:
            r.fail("G31", "data.js 有 %d 处 deduce 自带「主推/反证」前缀（渲染层会再补一次 → "
                          "页面显示「反证：反证是…」）：%s；跑 `python3 fix_dd_prefix.py --write` 清洗"
                   % (len(viol), "；".join(viol[:3])))
        else:
            r.ok("G31", "data.js 的 %d 处 deduce.main/counter 均无自前缀（标签只在渲染层加）" % tot)

    p = os.path.join(HERE, "index.html")
    try:
        s = open(p, encoding="utf-8").read()
    except Exception as e:                                    # noqa: BLE001
        r.warn("G31", "index.html 读不到，跳过渲染层兜底校验：%s" % e)
        return

    # ---- b) _ddClean 的三种前缀形态 + 谓语白名单必须都在 ----
    mb = re.search(r"function _ddClean\([\s\S]{0,2000}?\n\}", s)
    if not mb:
        r.fail("G31", "index.html 里找不到 _ddClean() —— 渲染层去重入口没了，"
                      "任何人写「反证是…」都会直接显示成双标签")
        return
    body = mb.group(0)
    need = [("[:：]", "冒号型「反证：」"), ("是", "「反证是」型"),
            ("在", "「反证在资金：」型"), ("成立", "谓语白名单（防「反证成立」被削成「成立」）")]
    miss = [lab for tok, lab in need if tok not in body]
    if miss:
        r.fail("G31", "_ddClean() 缺前缀形态：%s —— 缺一种，AI 写那种就会原样漏到页面"
               % "、".join(miss))
    else:
        r.ok("G31", "_ddClean() 覆盖 冒号/是/在<标记词> 三种前缀 + 谓语白名单")

    # ---- b2) 校验器必须复用同一份正则，不许自带副本 ----
    # 事故（2026-10-22 R100z175）：validate_stocks.py 曾自带 `(主推|反证)\s*([:：]|是)`，
    # 只认冒号与「是」，「反证在资金：」「反证证券跌1.21%」在**数据层被静默放行**，
    # 全靠部署前的本闸门兜住。五份副本 = 必改三漏一，这里把「不许再开第六份」钉死。
    vp = os.path.join(HERE, "validate_stocks.py")
    try:
        vs = open(vp, encoding="utf-8").read()
    except Exception as e:                                    # noqa: BLE001
        r.warn("G31", "validate_stocks.py 读不到，跳过校验器口径核对：%s" % e)
    else:
        code_lines = [ln for ln in vs.splitlines() if not ln.strip().startswith("#")]
        own = [ln.strip()[:60] for ln in code_lines if "主推|反证" in ln]
        if "from common import _DD_PREFIX_RE" not in vs:
            r.fail("G31", "validate_stocks.py 未复用 common._DD_PREFIX_RE —— 校验器自带副本"
                          "必然与清洗器漂移（曾漏掉「在资金：」与裸拼两种形态）")
        elif own:
            r.fail("G31", "validate_stocks.py 里仍有自写的「主推|反证」正则：%s —— "
                          "删掉它，只认 common._DD_PREFIX_RE" % own[0])
        else:
            r.ok("G31", "校验器复用 common._DD_PREFIX_RE（无自写副本）")

    # ---- c) 每个渲染点都必须过 _ddClean ----
    naked = []
    for ln, line in enumerate(s.splitlines(), 1):
        if "dd-counter" not in line:
            continue
        for expr in re.findall(r"esc\(\s*([A-Za-z_$][\w$]*\s*\.\s*counter)\s*\)", line):
            naked.append("L%d esc(%s)" % (ln, expr))
    if naked:
        r.fail("G31", "有 %d 处 dd-counter 直接渲染原始 counter 字段、没过 _ddClean：%s —— "
                      "只要数据脏就成了双标签（2026-10-22 实测漏过一处降级路径）"
               % (len(naked), "、".join(naked)))
    else:
        r.ok("G31", "全部 dd-counter 渲染点均经 _ddClean 清洗")

    # ---- d) 三方口径一致性：真跑 Python 与 JS，逐例比对（不看字面量） ----
    # 教训：b) 只看「正则里有没有某个字符」，改坏了分支顺序/断言语义它照样绿。
    # 这里把同一个样例集喂给两边，输出必须逐字相同，且等于期望值。
    cases = [
        ("反证在资金：涨2.10%却净流出4.71亿元", "涨2.10%却净流出4.71亿元"),
        ("反证是去化要等2027年才反映到出栏", "去化要等2027年才反映到出栏"),
        ("反证：反证是资金已经用脚投票", "资金已经用脚投票"),
        ("主推资金避险切换至红利低波", "资金避险切换至红利低波"),
        ("反证证券跌1.21%，非银净流出", "证券跌1.21%，非银净流出"),
        ("反证在于业绩：三季度预亏", "三季度预亏"),
        ("主推：核心是平台补贴", "核心是平台补贴"),
        ("反证成立，因为量能没跟上", "反证成立，因为量能没跟上"),      # 谓语例外：不许剥
        ("主推不成立，属于误读", "主推不成立，属于误读"),              # 谓语例外：不许剥
        ("反证已被证伪，别再提", "反证已被证伪，别再提"),              # 谓语例外：不许剥
    ]
    from common import strip_dd_prefix
    py = [strip_dd_prefix(c) for c, _ in cases]
    py_bad = [c for (c, want), got in zip(cases, py) if got != want]
    if py_bad:
        r.fail("G31", "common.strip_dd_prefix 剥离结果与期望不符：%s —— 推送给微信的文案会带双标签"
               % "；".join("「%s」" % c[:18] for c in py_bad[:2]))
    try:
        from common import find_node
        node = find_node()
    except Exception as e:                                    # noqa: BLE001
        node = None
        r.warn("G31", "找不到 node，跳过 JS 侧实测比对（只校验了 Python 侧）：%s" % e)

    if node:
        import tempfile
        try:
            d = tempfile.mkdtemp()
            js = os.path.join(d, "ddcheck.js")
            with open(js, "w", encoding="utf-8") as f:
                f.write(body + "\nvar CASES = " + json.dumps([c for c, _ in cases], ensure_ascii=False) + ";\n")
                f.write("console.log(JSON.stringify(CASES.map(function(c){return _ddClean(c);})));\n")
            out = subprocess.run([str(node), js], capture_output=True, text=True, timeout=30)
            js_res = json.loads((out.stdout or "null").strip() or "null")
        except Exception as e:                                # noqa: BLE001
            r.warn("G31", "JS 侧 _ddClean 跑不起来（%s），渲染层口径未经实测" % e)
        else:
            diff = [(c, a, b) for (c, _), a, b in zip(cases, py, js_res or []) if a != b]
            if not js_res or len(js_res) != len(cases):
                r.warn("G31", "JS 侧返回 %s 条结果（期望 %d），比对不完整"
                       % (len(js_res or []), len(cases)))
            elif diff:
                r.fail("G31", "渲染层 _ddClean 与 common.strip_dd_prefix 口径不一致（%d/%d 例）：%s —— "
                              "网页与微信推送会出现两种文案"
                       % (len(diff), len(cases),
                          "；".join("「%s」py=「%s」js=「%s」" % (c[:12], a[:12], b[:12])
                                   for c, a, b in diff[:2])))
            else:
                r.ok("G31", "Python/JS 两侧剥离结果逐例一致（%d 例，含 3 条谓语反例未被误剥）"
                     % len(cases))


def g32_stop_ledger_truth(r):
    """G32 止损台账自证：页面上的汇总不得与台账明细矛盾（专治「假 0」）。

    为什么需要（R100z181c，2026-10-24）：`stop_tracker.summarize()` 曾直接读
    `e.get('touched')` / `e['p5Pct']`，而台账把结果存在 `e['res']` 里 —— 三个键全取到
    None，汇总恒为「命中 0 条 / 0% / 5 日均值 —」，同一屏下面的逐日明细却是另一条
    代码路径算的、加起来 306 条。**汇总与明细自相矛盾，却没有任何闸门报警**，于是
    页面把「止损从未被触及」当成了正常态：这是最需要被否证的一条纪律
    （止损位到底设得对不对），被伪装成了已验证。

    判据只取「结构上不可能」的那一条：明细里存在 touched=True，而汇总报 0。
    不比较 total/均值 —— 页面是快照、台账每天更新，差一两天属正常滞后，
    拿它当 FAIL 会变成每天必现的噪音（24.16d：假信号比没闸更危险）。
    """
    led_p = os.path.join(HERE, "stop_ledger.json")
    if not os.path.exists(led_p):
        r.warn("G32", "缺 stop_ledger.json，止损有效性无法自证（跳过）")
        return
    try:
        led = json.load(open(led_p, encoding="utf-8"))
        entries = led.get("entries") or {}
    except Exception as e:                                     # noqa: BLE001
        r.fail("G32", "stop_ledger.json 解析失败：%s" % e)
        return
    if not entries:
        r.warn("G32", "台账 entries 为空，止损有效性无样本（跳过）")
        return

    done = hit = 0
    p5 = []
    for _dt, rows in entries.items():
        for e in rows:
            res = e.get("res") or {}
            if res.get("noKline") is False and not res.get("pending"):
                done += 1
                if res.get("touched"):
                    hit += 1
                if res.get("p5Pct") is not None:
                    p5.append(res["p5Pct"])
    if done == 0:
        r.warn("G32", "台账尚无可判定样本（全部 pending / 缺 K 线）")
        return
    rate = hit / done

    try:
        D = load_data_json()
        st = D.get("stopTrack") or {}
    except Exception as e:                                     # noqa: BLE001
        r.fail("G32", "读 data.js 的 stopTrack 失败：%s" % e)
        return

    shown_t, shown_rate = st.get("touched"), st.get("touchRate")
    if not st:
        r.warn("G32", "data.js 无 stopTrack 节点，页面不展示止损有效性（跳过）")
        return
    if (shown_t in (0, None)) and hit > 0:
        r.fail("G32", "页面 stopTrack 报「触及 %s 条 / %s」，但台账明细实为 %d/%d = %.1f%% —— "
                      "汇总与明细取数路径不一致，前端把「止损常年不被触及」当正常态展示。"
                      "修 stop_tracker.summarize 的 res 取值后重跑 16:00"
               % (shown_t, shown_rate, hit, done, rate * 100))
        return
    # 方向性检查：比率偏差超过 5 个百分点才提示（快照滞后属正常）
    if shown_rate is not None and abs(float(shown_rate) - rate) > 0.05:
        r.warn("G32", "页面触及率 %.1f%% 与台账 %.1f%% 相差 >5pp —— 页面快照偏旧，"
                      "下次 16:00/19:30 会自动刷新" % (float(shown_rate) * 100, rate * 100))
    else:
        r.ok("G32", "止损台账汇总与明细一致（触及 %d/%d = %.1f%%，5 日均值 %s，页面同步）"
             % (hit, done, rate * 100,
                ("%+.2f%%" % (sum(p5) / len(p5))) if p5 else "样本不足"))


def g33_atr_stop_consistency(r):
    """G33 止损口径唯一性：同一只票在 atrStops 与 tradeDesk 里必须是同一个止损价（R100z160）。

    为什么需要（2026-10-24）：页面现在有两处会显示止损价——弹窗「退出纪律」读
    `atrStops`，交易执行台的清单行读 `tradeDesk`。两个节点由两个脚本分别写，
    如果各自算一套，页面对同一只票会给出**两个不同的止损价**，而且两边都自洽、
    都不报错。这类「看起来对、实际互相打脸」的错误，只能靠跨节点对账发现。

    同时校验三条内部自洽（都比"看起来合理"更强）：
      ① stop < close（止损必须在收盘价下方，否则不是止损）；
      ② stopWidPct 与 (close-stop)/close 一致（防宽度字段与价位脱节）；
      ③ posPct ≈ min(20, 0.5% ÷ 宽度)（防仓位不是按风险预算算的，而是拍脑袋）。
    """
    try:
        D = load_data_json()
    except Exception as e:                                     # noqa: BLE001
        r.fail("G33", "读 data.js 失败：%s" % e)
        return
    A = D.get("atrStops") or {}
    T = D.get("tradeDesk") or {}
    items = A.get("items") or {}
    if not items:
        r.fail("G33", "atrStops.items 为空 —— 止损权威源缺失，弹窗退出纪律会整体退回固定百分比口径")
        return
    params = A.get("params") or {}
    risk_budget = float(params.get("riskBudgetPct") or 0.5)
    max_pos = float(params.get("maxPosPct") or 20.0)

    bad_rel, bad_wid, bad_pos = [], [], []
    for code, it in items.items():
        c, s = it.get("close"), it.get("stop")
        w, p = it.get("stopWidPct"), it.get("posPct")
        if not isinstance(c, (int, float)) or not isinstance(s, (int, float)) or c <= 0:
            bad_rel.append("%s(价%d/%d)" % (code, c if isinstance(c, (int, float)) else -1,
                                            s if isinstance(s, (int, float)) else -1))
            continue
        if not (0 < s < c):
            bad_rel.append("%s(%.2f→%.2f)" % (code, c, s))
            continue
        real_w = (c - s) / c * 100.0
        if w is None:
            bad_wid.append("%s(缺宽度)" % code)
        elif abs(float(w) - real_w) > 0.3:
            bad_wid.append("%s 标%.2f/实%.2f" % (code, float(w), real_w))
        if w and p is not None:
            want = min(max_pos, risk_budget / (float(w) / 100.0))
            if abs(float(p) - want) > 0.9:
                bad_pos.append("%s 标%.1f/应%.1f" % (code, float(p), want))

    # 跨节点对账：执行清单里的每一只，止损必须与 atrStops 同值
    drift = []
    not_in = []
    for pk in (T.get("picks") or []):
        code = str(pk.get("code") or "")
        src = items.get(code)
        if not src:
            not_in.append(code)
            continue
        ps, ss = pk.get("stop"), src.get("stop")
        if ps is None or ss is None or abs(float(ps) - float(ss)) > 0.005:
            drift.append("%s 执行台 %s vs atrStops %s"
                         % (code, ("%.2f" % float(ps)) if ps is not None else "空",
                            ("%.2f" % float(ss)) if ss is not None else "空"))

    if bad_rel:
        r.fail("G33", "atrStops 有 %d 只止损不在收盘价下方（不是止损）：%s"
               % (len(bad_rel), "; ".join(bad_rel[:6])))
    if bad_wid:
        r.fail("G33", "atrStops 宽度字段与价位不符 %d 只（标 vs 实）：%s"
               % (len(bad_wid), "; ".join(bad_wid[:6])))
    if bad_pos:
        r.fail("G33", "atrStops 仓位不是按风险预算算的 %d 只（标 vs 应）：%s"
               % (len(bad_pos), "; ".join(bad_pos[:6])))
    if drift:
        r.fail("G33", "同一只票两处止损不一致（弹窗读 atrStops、执行台读 tradeDesk）：%s"
               % "; ".join(drift[:6]))
    if not_in:
        r.warn("G33", "执行清单 %d 只不在 atrStops 里（K 线不足被跳过？）：%s"
               % (len(not_in), ", ".join(not_in[:6])))
    if not (bad_rel or bad_wid or bad_pos or drift):
        r.ok("G33", "止损口径唯一：%d 只价位/宽度/仓位自洽，执行清单与 atrStops 逐只同值"
                    "（风险预算 %.1f%%、仓位上限 %.0f%%）" % (len(items), risk_budget, max_pos))


def g34_desk_structure(r):
    """G34 执行清单结构：数量、单板块集中度、价位关系、仓位合计必须与声明一致。

    为什么需要：`tradeDesk.picks` 是「今天买什么」的唯一入口，一旦这里悄悄多了
    单板块第 3 只，散户就会把同一个赌注下三遍，而页面上看不出任何异常——
    「8 只清单」看着依然分散。所以单板块上限必须由闸门校验，不能只写在脚本里。
    """
    try:
        D = load_data_json()
    except Exception as e:                                     # noqa: BLE001
        r.fail("G34", "读 data.js 失败：%s" % e)
        return
    T = D.get("tradeDesk") or {}
    picks = T.get("picks") or []
    if not picks:
        r.warn("G34", "tradeDesk.picks 为空（池子当日无通过否决的标的，或脚本未跑）")
        return
    tiers = T.get("tiers") or {}
    pf = T.get("portfolio") or {}
    bad = []

    if len(picks) > 8:
        bad.append("清单 %d 只 > 上限 8 只" % len(picks))
    if tiers.get("desk") not in (None, len(picks)):
        bad.append("tiers.desk=%s 与 picks 实际 %d 只不符" % (tiers.get("desk"), len(picks)))

    sec_n = {}
    for p in picks:
        sec_n[p.get("sector") or "未分类"] = sec_n.get(p.get("sector") or "未分类", 0) + 1
    # 「未分类」是板块映射缺失的占位符，不是一个板块——把浙商银行、赤峰黄金、汇顶科技
    # 合并计数会得出「同板块超 2 只」的假 FAIL。上游 signal_rank 也不给它配额，
    # 这里必须同步，否则闸门与脚本口径打架（一方放行、一方判 FAIL）。
    over = {k: v for k, v in sec_n.items() if v > 2 and k != "未分类"}
    if over:
        bad.append("单板块超 2 只：" + ", ".join("%s×%d" % (k, v) for k, v in over.items()))
    # 但「未分类」不能无限放行：它占掉的仓位是真实的，只数多到一定程度说明
    # 板块映射大面积缺失，这套「单板块 ≤2」的分散逻辑已经名存实亡，必须报出来。
    unc_n = sec_n.get("未分类", 0)
    if unc_n > 4:
        bad.append("板块缺失 %d 只 > 4：映射大面积缺失，同板块上限实际未生效" % unc_n)

    for p in picks:
        code, c, s, t = (str(p.get("code") or ""), p.get("close"),
                         p.get("stop"), p.get("target"))
        if not all(isinstance(x, (int, float)) for x in (c, s, t)) or not c:
            bad.append("%s 价位缺失" % code)
            continue
        if not (0 < s < c):
            bad.append("%s 止损 %s 不在收盘 %s 下方" % (code, s, c))
        if t <= c:
            bad.append("%s 目标 %s 不高于收盘 %s" % (code, t, c))
        q = p.get("q")
        if not isinstance(q, (int, float)) or not (0 <= q <= 100):
            bad.append("%s 量化分异常 %s" % (code, q))

    # 仓位合计：没压缩时 = 单票加总；压缩过时 = cappedPosPct
    ps = [p.get("posPct") for p in picks if isinstance(p.get("posPct"), (int, float))]
    summed = round(sum(ps), 1) if ps else 0.0
    stated = pf.get("cappedPosPct")
    stated = float(stated) if isinstance(stated, (int, float)) else float(pf.get("sumPosPct") or 0)
    if abs(summed - stated) > 0.4:
        bad.append("单票仓位加总 %.1f%% ≠ 组合声明 %.1f%%" % (summed, stated))

    if bad:
        r.fail("G34", "执行清单结构有问题：" + "; ".join(bad[:6]))
    else:
        r.ok("G34", "执行清单 %d 只、%d 个板块（单板块 ≤2）、价位关系正确、仓位加总 %.1f%% 与组合声明一致"
             % (len(picks), len(sec_n), summed))


def g35_atr_evidence_truth(r):
    """G35 ATR 证据必须由回测产出，且句子里每个数字都跟 evidenceRows 对得上。

    为什么需要：`atrStops.evidence` 曾是一句**手写字符串**（"9,060 个入场点、68.7%"），
    K 线每天在长、回测结果会漂移，页面却永远显示那组旧数字——用户看到的是
    「实测证据」，实际是三天前的一次人工转录。这类错误不会报错、只会安静地骗人。

    现在 atr_stop.build() 每次实时回测并同时写 evidenceRows（结构化）与 evidence（句子），
    本闸门把两者锁成同一份数字：句子里的百分比必须能在行数据里找到，
    样本数必须三行一致（「同条件」回测的前提），否则说明有人又改了句子。
    """
    try:
        D = load_data_json()
    except Exception as e:                                     # noqa: BLE001
        r.fail("G35", "读 data.js 失败：%s" % e)
        return
    A = D.get("atrStops") or {}
    ev = str(A.get("evidence") or "")
    rows = A.get("evidenceRows") or []
    if not ev and not rows:
        r.warn("G35", "atrStops.evidence 与 evidenceRows 均为空（atr_stop.py 未跑？）")
        return
    bad = []
    if not rows:
        bad.append("有 evidence 句但缺 evidenceRows（数字无法核对）")
    elif len(rows) < 2:
        bad.append("evidenceRows 仅 %d 行（至少要有旧口径与新口径两行可比）" % len(rows))
    if not ev:
        bad.append("有 evidenceRows 但 evidence 句为空（页面会缺说明）")

    if rows:
        ns = {x.get("n") for x in rows}
        if len(ns) != 1:
            bad.append("三行样本数不一致 %s——「同条件回测」的前提是同一批入场点" % sorted(ns))
        if rows[0].get("key") != "fixed":
            bad.append("首行不是旧口径 fixed（%s），对比表会把新口径当基准" % rows[0].get("key"))
        for x in rows:
            for fld in ("touchRate", "winRate"):
                v = x.get(fld)
                if not isinstance(v, (int, float)) or not (0 <= v <= 1):
                    bad.append("%s.%s=%s 不是 0-1 比例" % (x.get("key"), fld, v))
                else:
                    want = "%.1f%%" % (v * 100)
                    if ev and want not in ev:
                        bad.append("evidence 句里找不到 %s=%s（句子与行数据脱节）"
                                   % (x.get("key"), want))
            if not (isinstance(x.get("retPct"), (int, float))):
                bad.append("%s.retPct 缺失" % x.get("key"))
        if rows[0].get("n"):
            nstr = "{:,}".format(rows[0]["n"])
            if ev and nstr not in ev:
                bad.append("evidence 句样本数与 evidenceRows 不符（句里无 %s）" % nstr)
        # 方向性自洽：新口径的触及率必须低于旧口径，否则「换口径」这件事本身就是错的。
        # （如果哪天数据真的翻了，应该让闸门报警去人工看，而不是让页面继续宣称更好。）
        if len(rows) >= 2:
            if not rows[-1]["touchRate"] < rows[0]["touchRate"]:
                bad.append("新口径触及率 %.1f%% 不低于旧口径 %.1f%%——换口径的收益不成立，需人工复核"
                           % (rows[-1]["touchRate"] * 100, rows[0]["touchRate"] * 100))
            if not rows[-1]["winRate"] > rows[0]["winRate"]:
                bad.append("新口径胜率 %.1f%% 不高于旧口径 %.1f%%——同样需复核"
                           % (rows[-1]["winRate"] * 100, rows[0]["winRate"] * 100))

    if bad:
        r.fail("G35", "ATR 证据与回测行不自洽：" + "; ".join(bad[:6]))
    else:
        r.ok("G35", "ATR 证据句与 %d 行回测数据逐项一致（%s 个入场点：触及 %.1f%% → %.1f%%、胜率 %.1f%% → %.1f%%）"
             % (len(rows), "{:,}".format(rows[0]["n"]),
                rows[0]["touchRate"] * 100, rows[-1]["touchRate"] * 100,
                rows[0]["winRate"] * 100, rows[-1]["winRate"] * 100))


def g36_desk_exec_plan(r):
    """G36 次日执行参数自洽 —— 「明天怎么下单」不能自相矛盾。

    为什么需要：R100z161 给每只票加了 plan（进场区间 / 追高上限 / 放弃线），
    这张表才从「收盘排名」变成「能照着做的预案」。但四个价位一旦算错，
    用户会照着错误指令下单——比没有指令更糟：
      · 放弃线比止损还低  → 等于让人多亏一截才认错
      · 追高上限高于目标  → 追进去就是负期望
      · 区间倒序          → 页面显示「11.40~10.80」这种没法执行的区间
    这些都不会报错，只会安静地害人，所以必须逐只对账。
    """
    try:
        D = load_data_json()
    except Exception as e:                                     # noqa: BLE001
        r.fail("G36", "读 data.js 失败：%s" % e)
        return
    T = D.get("tradeDesk") or {}
    picks = T.get("picks") or []
    if not picks:
        r.warn("G36", "tradeDesk.picks 为空（signal_rank.py 未跑？）")
        return

    bad, noPlan = [], []
    for p in picks:
        code = p.get("code") or "?"
        pl = p.get("plan")
        if not pl:
            noPlan.append(code)
            continue
        a, lo, hi, ch = (pl.get("abandon"), pl.get("entryLo"),
                         pl.get("entryHi"), pl.get("chase"))
        stop, close, tgt = p.get("stop"), p.get("close"), p.get("target")
        vals = [a, lo, hi, ch]
        if any(not isinstance(v, (int, float)) for v in vals):
            bad.append("%s 执行参数不全是数字 %s" % (code, vals))
            continue
        if not (a < lo < hi < ch):
            bad.append("%s 四档顺序错乱 abandon=%s entryLo=%s entryHi=%s chase=%s"
                       % (code, a, lo, hi, ch))
        if isinstance(stop, (int, float)) and a < stop - 0.005:
            bad.append("%s 放弃线 %.2f 低于止损 %.2f——等于让人多亏一截才认错"
                       % (code, a, stop))
        if isinstance(tgt, (int, float)) and ch >= tgt:
            bad.append("%s 追高上限 %.2f ≥ 目标 %.2f——追进去是负期望" % (code, ch, tgt))
        if isinstance(close, (int, float)) and not (a <= close <= ch):
            bad.append("%s 收盘价 %.2f 不在执行区间 [%s, %s] 内" % (code, close, a, ch))
        if not (pl.get("ifGapUp") and pl.get("ifInBand") and pl.get("ifGapDown")):
            bad.append("%s 三档情景文案不全（高开/区间内/低开）" % code)

    # 有效期：清单是给**下一个交易日**用的，过期没重算就会误导人
    td, vu = str(T.get("tradeDate") or "")[:10], str(T.get("validUntil") or "")[:10]
    if not vu:
        bad.append("tradeDesk.validUntil 缺失——页面无法说明这张表什么时候作废")
    elif td and vu <= td:
        bad.append("validUntil(%s) 不晚于 tradeDate(%s)" % (vu, td))

    # 个别票缺 plan 只 WARN（数据不全时宁可不展示）；超过一半才 FAIL
    if len(noPlan) * 2 > len(picks):
        bad.append("%d/%d 只缺执行参数——这张表会退回「只有收盘价的排名」"
                   % (len(noPlan), len(picks)))

    if bad:
        r.fail("G36", "；".join(bad[:6]))
    else:
        r.ok("G36", "%d 只执行参数自洽（放弃线 ≤ 止损、追高 < 目标、四档有序），有效期至 %s"
             % (len(picks) - len(noPlan), vu or "—"))


def g37_forecast_score_truth(r):
    """G37 预测成绩单必须与台账逐项对得上 —— 不许「AI 自己给自己打分」。

    为什么需要：这张卡的价值全在「数字可信」。如果 data.js 里的命中率
    与 calibration_log.json 算出来的不一致，那它就退化成了又一段 AI 自述，
    比没有更糟（因为它看起来像客观统计）。
    所以本闸门**重算一遍台账**，逐项比对；并且要求 usage 非空——
    有一堆数字却不给「该怎么用」，读者仍然不知道该信几分。
    """
    import json as _json
    try:
        D = load_data_json()
    except Exception as e:                                     # noqa: BLE001
        r.fail("G37", "读 data.js 失败：%s" % e)
        return
    F = D.get("forecastScore") or {}
    if not F:
        r.warn("G37", "forecastScore 为空（forecast_scorecard.py 未跑？）")
        return

    logp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_log.json")
    if not os.path.exists(logp):
        r.warn("G37", "缺 calibration_log.json，无法核对成绩单")
        return
    try:
        with open(logp, encoding="utf-8") as f:
            L = _json.load(f) or {}
    except Exception as e:                                     # noqa: BLE001
        r.fail("G37", "读 calibration_log.json 失败：%s" % e)
        return

    log = L.get("log") or {}
    op = L.get("open") or {}
    rows = [x for d in log for x in (log.get(d) or [])]
    n = len(rows)
    hit = sum(1 for x in rows if x.get("result") == "命中")
    want_strict = round(hit * 100.0 / n, 1) if n else None

    S = F.get("sector") or {}
    bad = []
    if S.get("n") != n:
        bad.append("成绩单 n=%s 而台账 %d 条" % (S.get("n"), n))
    if want_strict is not None and abs((S.get("strictHit") or 0) - want_strict) > 0.05:
        bad.append("严格命中 %s%% 与台账重算 %s%% 不一致" % (S.get("strictHit"), want_strict))
    if S.get("days") != len(log):
        bad.append("覆盖 %s 日而台账 %d 日" % (S.get("days"), len(log)))
    if (F.get("forward") or {}).get("days") != len(op):
        bad.append("开盘前瞻 %s 天而台账 %d 天" % ((F.get("forward") or {}).get("days"), len(op)))

    # 结论必须随数据走：样本够却没给 usage = 有账不解读；样本不够却给一堆结论 = 拿噪声装结论
    usage = F.get("usage") or []
    if F.get("enough") and not usage:
        bad.append("样本已够但 usage 为空——只报数不解读，读者仍不知该信几分")
    if (not F.get("enough")) and len(usage) > 1:
        bad.append("样本不足却给出 %d 条结论——样本不够时任何结论都是噪声" % len(usage))
    if not str(F.get("verdict") or ""):
        bad.append("verdict 为空")
    elif n and ("%d 条" % n) not in str(F.get("verdict")):
        bad.append("verdict 未提及样本数 %d 条（结论应能追溯到样本量）" % n)

    # 分层样本之和必须等于总数：漏一组说明分组键有拼写差异（如「看涨」vs「up」）
    for key in ("byConf", "byDir"):
        ss = sum((x.get("n") or 0) for x in (F.get(key) or []))
        if ss and ss != n:
            bad.append("%s 分组样本合计 %d ≠ 总数 %d（分组键不一致）" % (key, ss, n))

    if bad:
        r.fail("G37", "；".join(bad[:5]))
    else:
        r.ok("G37", "成绩单与台账一致（%d 条 / %d 日 / 前瞻 %d 天），结论 %d 条由阈值生成"
             % (n, len(log), len(op), len(usage)))


def g38_flags_respected(r):
    """G38 forecastScore.flags 必须被下一次预测真的执行 —— 否则成绩单只是张统计表。

    为什么需要：R100z161 之前，命中率只出现在页面上，AI 生成预测时读不读
    全凭自觉。结果就是「明明高置信 11%、页面白纸黑字写着反向指标，
    第二天照标 3 条高确信」。这里把 flags 变成硬约束：

      ① flags 必须能被 byConf/byDir/forward 复算出来（防手改）；
      ② maxHighConf=0 → 预测里一条「高」都不许有；=1 → 最多 1 条；
      ③ requireBand=True → openOutlook.pos 必须带 bandLo/bandHi 且 lo<hi
         （倾向判词只有 18% 命中，靠它下注等于抛硬币）。

    不约束方向条数：看涨弱是「怎么用」的问题，不是「不许看涨」。
    """
    try:
        D = load_data_json()
    except Exception as e:                                     # noqa: BLE001
        r.fail("G38", "读 data.js 失败：%s" % e)
        return
    F = D.get("forecastScore") or {}
    if not F:
        r.warn("G38", "forecastScore 为空，flags 无从校验")
        return
    FL = F.get("flags") or {}
    if not FL:
        r.fail("G38", "forecastScore.flags 缺失——预测生成读不到任何约束开关")
        return

    bad = []
    conf = F.get("byConf") or []
    dirs = F.get("byDir") or []
    fwd = F.get("forward") or {}
    S = F.get("sector") or {}

    # ① 复算：flags 与分层数据必须自洽
    if S.get("n", 0) >= 30 and S.get("days", 0) >= 5:
        hi = next((c for c in conf if c.get("conf") == "高"), None)
        mid = next((c for c in conf if c.get("conf") == "中"), None)
        want_inv = bool(hi and mid and hi.get("n", 0) >= 5 and mid.get("n", 0) >= 10
                        and ((mid.get("strictHit") or 0) - (hi.get("strictHit") or 0)) >= 10.0)
        if bool(FL.get("confInverted")) != want_inv:
            bad.append("confInverted=%s 与分层数据复算 %s 不一致（flags 被手改？）"
                       % (FL.get("confInverted"), want_inv))
        if want_inv and FL.get("maxHighConf") != 0:
            bad.append("confInverted=True 但 maxHighConf=%s（应强制 0）" % FL.get("maxHighConf"))
        if not want_inv and FL.get("maxHighConf") == 0:
            bad.append("分层数据不支持反向，maxHighConf 却被压成 0（冤枉正常预测）")
        up = next((x for x in dirs if x.get("dir") == "up"), None)
        dn = next((x for x in dirs if x.get("dir") == "down"), None)
        want_asym = None
        if up and dn and up.get("n", 0) >= 15 and dn.get("n", 0) >= 8:
            g = (dn.get("strictHit") or 0) - (up.get("strictHit") or 0)
            if g >= 15.0:
                want_asym = "upWeak"
            elif -g >= 15.0:
                want_asym = "downWeak"
        if FL.get("dirAsym") != want_asym:
            bad.append("dirAsym=%s 与方向分层复算 %s 不一致" % (FL.get("dirAsym"), want_asym))
    bAny, tr = fwd.get("bandAnyRate"), fwd.get("tendencyRate")
    if bAny is not None and tr is not None:
        want_band = bool(bAny >= 70.0 and tr < 40.0)
        if bool(FL.get("requireBand")) != want_band:
            bad.append("requireBand=%s 与前瞻复算 %s 不一致"
                       % (FL.get("requireBand"), want_band))

    # ② 高置信条数上限：这是「承诺强度」，不是语气
    ap = D.get("aiPrediction") or {}
    secs = ap.get("sectors") or []
    if secs:
        nh = sum(1 for s in secs if s.get("confidence") == "高")
        cap = FL.get("maxHighConf")
        if cap is not None and nh > cap:
            bad.append("aiPrediction 标了 %d 条「高」置信，上限 %d（成绩单：高置信 %d 条命中 %s%%）%s"
                       % (nh, cap,
                          next((c.get("n") for c in conf if c.get("conf") == "高"), 0),
                          next((c.get("strictHit") for c in conf if c.get("conf") == "高"), "—"),
                          "——禁标高已生效，请改「中」" if cap == 0 else ""))

    # ③ 前瞻必须给区间：倾向判词 18% 命中，单独用等于抛硬币
    if FL.get("requireBand"):
        pos = (D.get("openOutlook") or {}).get("pos") or {}
        lo, hi = pos.get("bandLo"), pos.get("bandHi")
        if lo is None or hi is None:
            bad.append("requireBand=True 但 openOutlook.pos 缺 bandLo/bandHi（倾向命中仅 %s%%）"
                       % (tr if tr is not None else "—"))
        elif not (isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo < hi):
            bad.append("openOutlook.pos 区间无效：bandLo=%s bandHi=%s" % (lo, hi))

    if bad:
        r.fail("G38", "；".join(bad[:4]))
    else:
        r.ok("G38", "flags 已生效（高置信上限 %s / 区间强制 %s / 方向提示 %s）"
             % (FL.get("maxHighConf"), FL.get("requireBand"), FL.get("dirAsym") or "无"))


def g39_open_desk_page(r):
    """G39 开盘预测子页：三块齐全 + 顺序正确 + 进得去也走得掉（R100z162，2026-10-24）。

    由来：用户 2026-10-24 要求「AI 预测分析整个模块挪到技术看板，新增开盘预测；
    次日交易预案也放这里，在 AI 预测分析下面」。三块顺序是**有语义的**——
    先看方向（盘前前瞻）→ 再看依据（AI 预测）→ 最后看作法（次日预案）。
    顺序反了就是「先看买什么、再补为什么」，决策链是倒的。

    这类「搬页」改动最典型的故障不是报错，是**静默降级**：容器在、渲染函数也在，
    但某一块忘了挂上去，页面照样能开、闸门照样绿，只是用户少看一块内容。
    更隐蔽的是子页没有自己的 tab 菜单——`dbSubTab` 记住 'od' 之后整页无入口可走，
    用户被锁死在开盘预测页，下次进来还是它。

    判据（只看源码，不跑浏览器）：
      a) #db-od 内四个容器齐全：db-od-nav / weekend-news / ai-prediction / trade-desk；
      b) 顺序必须 nav → weekend-news → ai-prediction → trade-desk；
      c) subnavHtml() 的 tabs 里 'od' 存在且排第一（盘前唯一要看的一页）；
      d) switchSubTab 的 map 里有 od → 'db-od'（否则切过去白屏）；
      e) renderOpenDesk() 真的依次调用三个渲染函数；
      f) renderDuanban() 给 #db-od-nav 塞过 subnavHtml()（否则进得去出不来）。
    """
    p = os.path.join(HERE, "index.html")
    try:
        s = open(p, encoding="utf-8").read()
    except Exception as e:                                    # noqa: BLE001
        r.warn("G39", "index.html 读不到，跳过开盘预测子页校验：%s" % e)
        return

    bad = []

    # ---- a) b) 容器齐全 + 顺序 ----
    # R100z162：切片到下一个子页容器为止。用 DOM 顺序而不是「函数里有调度」判顺序——
    # 用户说的「放到下面」是**视觉顺序**，只有 DOM 顺序能保证它。
    i0 = s.find('<div id="db-od"')
    if i0 < 0:
        bad.append('找不到 #db-od 容器（开盘预测子页整块没了）')
    else:
        i1 = s.find('<div id="db-pools"', i0)
        seg = s[i0:i1 if i1 > 0 else i0 + 4000]
        want = ["db-od-nav", "weekend-news", "ai-prediction", "trade-desk"]
        pos = {}
        for key in want:
            j = seg.find('id="%s"' % key)
            if j < 0:
                bad.append("开盘预测子页缺 #%s" % key)
            else:
                pos[key] = j
        if len(pos) == 4:
            seq = sorted(pos, key=lambda k: pos[k])
            if seq != want:
                bad.append("子页块顺序 %s，应为 盘前前瞻→AI预测→次日预案（用户要求预案在预测下面）"
                           % "→".join(seq))

    # ---- c) tabs 里 od 排第一 ----
    mt = re.search(r"var tabs = \[([\s\S]{0,1500}?)\];", s)
    if not mt:
        bad.append("subnavHtml() 里找不到 tabs 定义——子页菜单结构已变，无法确认 od 入口")
    else:
        tb = mt.group(1)
        ks = re.findall(r"k:\s*'([a-z]+)'", tb)
        if "od" not in ks:
            bad.append("子页菜单缺 od（开盘预测进不去）")
        elif ks[0] != "od":
            bad.append("子页菜单第一个是 '%s'，应为 od（开盘预测是盘前第一页）" % ks[0])

    # ---- d) switchSubTab 的映射 ----
    # R100z162：必须在 switchSubTab() 函数体内找。全文找 `var map = {` 会命中别处
    # （首页有一处 `var map = { '加确认': 'add' ... }`），那是本闸自己写错的第一版。
    ms = re.search(r"function switchSubTab\(k\)\s*\{([\s\S]{0,2000}?)\n\}", s)
    if not ms:
        bad.append("找不到 switchSubTab() —— 子页切换逻辑已变")
    else:
        mm = re.search(r"var map = \{([^}]*)\};", ms.group(1))
        if not mm:
            bad.append("switchSubTab() 里找不到子页 map —— 切页逻辑已变")
        elif not re.search(r"od:\s*'db-od'", mm.group(1)):
            bad.append("switchSubTab 的 map 缺 od → 'db-od'（点开盘预测会白屏）")

    # ---- e) 三块渲染入口齐全 ----
    mf = re.search(r"function renderOpenDesk\(\)\s*\{([\s\S]{0,1200}?)\n\}", s)
    if not mf:
        bad.append("找不到 renderOpenDesk()——三块没有统一渲染入口（搬页后谁来画？）")
    else:
        fb = mf.group(1)
        miss = [fn for fn in ("renderOpenOutlook", "renderAiPrediction", "renderTradeDesk")
                if fn not in fb]
        if miss:
            bad.append("renderOpenDesk() 未调用 %s（该块搬进子页后没人画＝空白）"
                       % "、".join(miss))
        # R100z162：只查「函数名出现过」是不够的——第一版埋雷实测漏过一颗：
        # 把 `var td = getElementById('trade-desk')` 改成 `= null`，`renderTradeDesk()`
        # 这行还在源码里（只是拿不到容器、画不出去），闸门照样绿。所以还要查
        # 「拿到容器 + 写回容器」这两步，缺任一步就等于那块永远白着。
        if "getElementById('trade-desk')" not in fb:
            bad.append("renderOpenDesk() 没去取 #trade-desk 容器——预案渲染出来无处可写")
        elif "td.innerHTML" not in fb:
            bad.append("renderOpenDesk() 取了 #trade-desk 却没写回（td.innerHTML）——预案那块永远空")

    # ---- f) 子页自带菜单 ----
    if 'id="db-od-nav"' in s and not re.search(
            r"getElementById\('db-od-nav'\)[\s\S]{0,200}?innerHTML\s*=\s*subnavHtml\(\)", s):
        bad.append("#db-od-nav 从未被塞入 subnavHtml()——dbSubTab 记住 od 后整页无入口可切走")

    if bad:
        r.fail("G39", "；".join(bad[:4]))
    else:
        r.ok("G39", "开盘预测子页三块齐全且顺序正确（前瞻→AI预测→预案），od 为默认子页且可切走")


def g40_score_page_and_slots(r):
    """G40 🌟 复盘台账页 + 悬浮组四槽位对齐（R100z162，2026-10-24）。

    由来：用户 2026-10-24 把两张成绩卡（指标表现 / 预测复盘）指出来说「这两个放到
    新的一页去」，并指定入口 icon 用 🌟、「逻辑和其他几个保持一致」。
    「一致」的具体含义是四条：点进去独占、页内有返回按钮、离开记住浏览位置、hash 可直达。

    另一条独立风险在**悬浮组槽位**：组从 3 个按钮扩到 4 个后，页内返回按钮若仍按
    「贴组底」定位（旧写法 `t + h - 46`），🌟 返回会落进组外或压到 🧰 上——
    这正是用户 2026-10-10 反馈过的「点进去鼠标跑到别的图标上」。所以返回按钮必须
    按槽位（第 i 个 = t + i×槽距）落位，槽距取组内相邻按钮 offsetTop 差实测值，
    不写死 60（缩放/移动端字号变化会变）。

    判据：
      a) #score-view / #score-body / #sc-date / #sc-entry / #sc-entry-in 五个锚点齐全；
      b) CSS 三件套齐（容器默认隐藏 + body.score-open 隐藏其余 + 显示本页）；
      c) renderScoreView() 同时渲染 renderSignalPerf / renderForecastScore / chenCompareHtml /
         renderLeadLag（少一张＝用户要的丢一半）；且 renderLeadLag **恰好一个调用点、且无参**——
         R100z199 把它从断板页搬到复盘页，两处各挂一份就会出现「同一张卡两个页面数字不同步」；
         R100z221 又按「今天买什么 / 这套口径能不能信」拆成两半，结果用户 2026-10-26 看到两块
         直接问「这两个是一个东西吗？我不是问你这个模块要不要删掉吗」；R100z225 把 picks 那半
         整块删掉、函数回到无参，闸门改为「恰好一处 + 不许传参」。将来若再拆，先改这条判据。
      d) hash '#score' 至少两处（初始直达 + popstate 返回）；
      e) 槽位表 RAIL 把 4 个页内替身按「🐼→📄→🧰→📋」顺序指到组内第 0..3 个按钮；
         layout() 用实测偏移 gm.off[i] 落位，且 CSS 里不再出现 calc(50% ± N) 的槽位副本；
      f) 几何每次现算（读 flex-direction + gap + 按钮宽高），不缓存槽距、不写死常量。
     R100z165（2026-10-24 用户：「这套槽位重做一遍」）把原来「CSS 一套 calc、JS 一套 i×槽距」
     的双份来源合并成一份：CSS 只管外观，定位全归 layout()。因此本闸门也从「查 place() 里有没有
     _slot(2)」改成「查槽位表顺序 + 落位是否走实测偏移」——判据必须跟着实现走，
     但查的是行为（谁落哪一槽、偏移从哪来），不是函数名。
    """
    p = os.path.join(HERE, "index.html")
    try:
        s = open(p, encoding="utf-8").read()
    except Exception as e:                                    # noqa: BLE001
        r.warn("G40", "index.html 读不到，跳过复盘台账页校验：%s" % e)
        return

    bad = []

    # ---- a) 页骨架 ----
    for anchor in ('id="score-view"', 'id="score-body"', 'id="sc-date"',
                   'id="sc-entry"', 'id="sc-entry-in"'):
        if anchor not in s:
            bad.append("缺 %s" % anchor.replace('id="', '#').rstrip('"'))

    # ---- b) CSS 三件套 ----
    if not re.search(r"#score-view\s*\{\s*display:\s*none", s):
        bad.append("#score-view 没有默认 display:none（未进入时就占位，主页面会被顶下去）")
    if "body.score-open .wrap > *:not(#score-view)" not in s:
        bad.append("缺 `body.score-open .wrap > *:not(#score-view){display:none}`——"
                   "进页后主页面仍显示，不是「独占」")
    if not re.search(r"body\.score-open\s+#score-view\s*\{[^}]*display:\s*block", s):
        bad.append("缺 `body.score-open #score-view{display:block}`——进去是空白页")

    # ---- c) 三张卡都在 ----
    mf = re.search(r"function renderScoreView\(\)\s*\{([\s\S]{0,2500}?)\n\}", s)
    if not mf:
        bad.append("找不到 renderScoreView()——复盘页没有渲染入口")
    else:
        fb = mf.group(1)
        miss = [fn for fn in ("renderSignalPerf", "renderForecastScore", "chenCompareHtml",
                              "renderLeadLag")
                if fn not in fb]
        if miss:
            bad.append("renderScoreView() 未渲染 %s（复盘页的成绩卡少一张）"
                       % "、".join(miss))
    # R100z199：搬页只能搬一次。调用点 >1 说明断板页那份没删干净，两处各渲染一份，
    # 将来改一边忘另一边，两个页面的同一个数字会不一致（而且没人会去对照）。
    # R100z221：曾改成「按 mode 各一个」（'picks' 断板页 / 'bt' 复盘台账）。
    # R100z225（用户 2026-10-26：「这两个是一个东西吗？我不是问你这个模块要不要删掉吗？」）：
    #   picks 那半整块删除，函数回到**无参**、只出回测。规矩更严了——恰好一个调用点，
    #   且不许再传 mode：传了就说明有人想把清单拆出去，而拆出去那次正是用户看到两块的原因。
    # ⚠️ 与 G30 同一条规矩：**注释命中也算命中**。写代码注释时不要把这几个字面量原样抄进去，
    #    要么说「mode 取 'bt'」，要么走 prose；否则闸门会把注释数成调用点（本轮踩过一次）。
    bare = re.findall(r"(?<!function )renderLeadLag\(\s*\)", s)
    if len(bare) != 1:
        bad.append("renderLeadLag() 应恰好 1 个调用点（复盘台账），实际 %d 个"
                   "——多一处就是同一张回测卡在两个页面各渲染一份，数字迟早不同步" % len(bare))
    witharg = re.findall(r"(?<!function )renderLeadLag\(\s*['\"]", s)
    if witharg:
        bad.append("renderLeadLag 被传了 %d 次参数——R100z225 起它无参（picks 那半已删；"
                   "清单要么并进复盘台账那一块、要么不加，严禁再拆两半各挂一页）" % len(witharg))

    # ---- d) hash 直达 ----
    nh = s.count("'#score'") + s.count('"#score"')
    if nh < 2:
        bad.append("hash '#score' 只出现 %d 处——初始直达与 popstate 返回须各一处" % nh)

    # ---- e) 槽位表顺序（页内替身 → 组内第 i 个按钮）----
    mrail = re.search(r"var RAIL = \[([\s\S]{0,900}?)\];", s)
    if not mrail:
        bad.append("找不到槽位表 RAIL——页内替身与组内按钮的对应关系没有单一来源")
    else:
        rb = mrail.group(1)
        # 表的条目是 `{ slot: g.children[N], iPage: 变量 }`；变量名是实现细节，
        # 先用 `var 变量 = getElementById('id')` 反解成 id 再比，改名不会误报。
        amap = dict(re.findall(r"var\s+(\w+)\s*=\s*document\.getElementById\('([\w-]+)'\)", s))
        pairs = re.findall(r"slot:\s*g\.children\[(\d+)\]\s*,\s*iPage:\s*(\w+)", rb)
        want = {"gn-jump": 0, "gn-entry-in": 1, "db-entry-in": 2, "sc-entry-in": 3}
        names = {"gn-jump": "🐼", "gn-entry-in": "📄", "db-entry-in": "🧰", "sc-entry-in": "📋"}
        got = {}
        for idx, var in pairs:
            got[amap.get(var, var)] = int(idx)
        for el_id, idx in want.items():
            if el_id not in got:
                bad.append("RAIL 缺 %s（%s 页内按钮不跟随悬浮组）" % (el_id, names[el_id]))
            elif got[el_id] != idx:
                bad.append("RAIL 把 %s 指到第 %d 槽（应为第 %d 槽）——点进去按钮会跳一格"
                           % (el_id, got[el_id] + 1, idx + 1))
        if len(pairs) > 4:
            bad.append("RAIL 有 %d 条（应 4 条）——多出的槽位没有对应按钮" % len(pairs))

    # ---- e2) 落位必须走实测偏移，CSS 不许再抄一份槽位 ----
    il = s.find("function layout(")
    if il < 0:
        bad.append("找不到 layout()——悬浮组定位逻辑没了")
    else:
        lb = s[il:il + 1800]
        if "gm.off[i]" not in lb:
            bad.append("layout() 未按实测偏移 gm.off[i] 落位——页内替身会与组内按钮错开")
        if re.search(r"t\s*\+\s*h\s*-\s*46", lb):
            bad.append("layout() 仍用贴底写法（t + h - 46）——4 个按钮时返回键会掉出组外")
        if re.search(r"\*\s*60\b|\*\s*lastGap", lb):
            bad.append("layout() 里还有硬编码槽距（×60 / ×lastGap）——应统一用 gm.off[i]")
    if re.search(r"top:\s*calc\(50%\s*[+-]", s):
        bad.append("CSS 里仍有 calc(50% ± N) 槽位——槽位一旦抄两份，改布局必错一份")

    # ---- f) 几何现算（flex-direction + gap + 按钮宽高）----
    ig = s.find("function geom(")
    if ig < 0:
        bad.append("缺 geom()——槽位几何没有单一计算入口")
    else:
        gb = s[ig:ig + 1200]
        for need, why in (("flexDirection", "未读 flex-direction，横排/竖排切换时槽位会错"),
                          ("rowGap", "未读 rowGap，竖排槽距写死"),
                          ("columnGap", "未读 columnGap，横排槽距写死"),
                          ("getComputedStyle", "未从 computed style 取尺寸，display:none 时量不到"),
                          # 手机端那排是有底色的胶囊条（padding 5px 7px），漏算内边距
                          # 会让页内替身整体偏 8px —— 又是「点进去按钮跳一格」。
                          ("paddingLeft", "未读 paddingLeft，容器一加内边距页内替身就整体偏移")):
            if need not in gb:
                bad.append("geom() %s" % why)
        # 读到 padding 还不够：必须真的加进槽位偏移（只读不用＝白读）
        moff = re.search(r"off\.push\(\{[^}]*\}", gb)
        if not moff:
            bad.append("geom() 找不到 off.push({...})——槽位偏移没有落点")
        elif 'pl' not in moff.group(0) or 'pt' not in moff.group(0):
            bad.append("geom() 的 off.push 没用 pl/pt——容器有内边距时替身会整体偏移")

    if bad:
        r.fail("G40", "；".join(bad[:4]))
    else:
        r.ok("G40", "🌟 复盘台账页齐全（指标复盘→预测复盘→反包复盘→落后股回测四段顺序、"
                    "hash 直达、独占 CSS、落后股 picks/bt 两半各一处），悬浮组四槽位按实测槽距对齐")


def g41_visual_norms(r):
    """G41 视觉规范与悬浮按钮全链路（R100z163，2026-10-24）。

    由来：用户这一轮连着三条都是「看起来不对」，没有一条是逻辑错——
      ① 「次日交易预案改成交易预案」（标题冗余，锚定日/有效期已在同一行）
      ② 「📋 怎么不能拖拽？？？你的记忆又有问题了？」（新按钮只接了 place()，漏 bindDrag()）
      ③ 「以产品经理视角统一全页面视觉规范」（248 处 font-size 有 23 种取值、控件不继承字体）
    闸门全绿但用户一眼看出不对，说明判据只管了「功能在不在」，没管「一致不一致」。
    这一条把可感知的一致性也锁住。

    判据：
      a) 交易预案标题必须是「交易预案」，不许回退成「次日交易预案」；
      b) renderTradeDesk 必须产出「与 AI 预测的关系」行；
         （R100z166：原判据还要求「账户与目标」块 + ACCT.equity + acctProfile 三件，
          2026-10-24 用户明确要求删掉那块：「这个删掉，你作为专业的交易员，从你做出的看板中
          都赚不到钱，那这个看板有什么用？」——它把「本金 3 万 / 11-30 前翻倍」摊成期望值外推，
          结论落成「这张表保证的是别让 3 万继续缩水」。账没算错，错在把「目标不成立」写成产品自述。
          判据随之删除，**不是为凑绿而放宽**：被删的是产品需求本身，不是校验强度。
          现在反向锁住：源码里不许再出现 td-acct / acctProfile，防下一次「补回来」。）
      c) 悬浮按钮「三件套」齐全：四个入口 + 四个页内返回，且 #sc-entry-in 必须 bindDrag()——
         这是用户 2026-10-24 亲自撞到的 bug（位置会跟随、自己拖不动）；
         且拖拽后的 click 抑制必须是时间戳窗口（suppressClick），不能粘性布尔（会吞下一次点击）；
      d) 字体：button/input/select/textarea 显式继承 var(--font)（控件默认不继承 body 字体）；
      e) 字号档位必须落在白名单内——防「下次再加一个 13px」把 6 档阶梯重新打散。
    """
    p = os.path.join(HERE, "index.html")
    try:
        s = open(p, encoding="utf-8").read()
    except Exception as e:                                    # noqa: BLE001
        r.warn("G41", "index.html 读不到，跳过视觉规范校验：%s" % e)
        return
    bad = []

    # ---- a) 标题 ----
    if "次日交易预案" in s:
        # 注释里也不许出现：这条标题被用户点名简化过一次，留一句就能被下一次复制回去
        bad.append("源码里仍有「次日交易预案」字样——用户 2026-10-24 要求标题就叫「交易预案」")

    # ---- b) 交易预案里的两块 ----
    mt = re.search(r"function renderTradeDesk\(\)\s*\{([\s\S]*?)\n\}", s)
    if not mt:
        bad.append("找不到 renderTradeDesk() —— 交易预案卡整块没了")
    else:
        tb = mt.group(1)
        # R100z166：判据从「必须有」翻成「不许回来」。这块的字面结论就是
        # 「这张表能保证的是别让 3 万继续缩水」+「距目标还差 2.84 万」——
        # 谁再补回来，等于产品又一次自证无用（用户 2026-10-24 原话）。
        if "td-acct" in tb or "acctProfile" in s or "ACCT.equity" in s:
            bad.append("交易预案卡又出现「账户与目标」块（td-acct / acctProfile / ACCT.equity）"
                       "——用户 2026-10-24 明确要求删除：把「目标不成立」写成产品自述")
        if "td-rel" not in tb and "与上方 AI 预测的关系" not in tb:
            bad.append("交易预案卡缺「与 AI 预测的关系」行——两套标的并排却不解释关系，读者会以为互相打架")

    # ---- c) 悬浮按钮三件套 ----
    entry = ["gn-entry", "db-entry", "sc-entry"]
    back = ["db-entry-in", "gn-entry-in", "sc-entry-in"]
    for i in entry + back:
        if ('id="%s"' % i) not in s:
            bad.append("缺悬浮按钮 #%s" % i)
    # R100z165：槽位体系重做后不再有 slotIndex()，改成「每个页内替身都必须 bindDrag(变量, 槽号)」。
    # 判据走 id→变量名解析，不写死变量名（变量名属于实现细节，改个名不该让闸门误报）。
    alias = dict(re.findall(r"var\s+(\w+)\s*=\s*document\.getElementById\('([\w-]+)'\)", s))
    id2var = {v: k for k, v in alias.items()}
    for iid, nm in (("gn-jump", "🐼"), ("gn-entry-in", "📄"),
                    ("db-entry-in", "🧰"), ("sc-entry-in", "📋")):
        var = id2var.get(iid)
        if not var:
            bad.append("#%s 没有绑定到变量（三件套第一环就断了）" % iid)
            continue
        if not re.search(r"bindDrag\(\s*%s\s*,\s*\d+\s*\)" % re.escape(var), s):
            bad.append("%s 页内键(#%s) 没接 bindDrag()——位置会跟随、自己拖不动"
                       "（用户 2026-10-24 原话「他怎么不能拖拽」）" % (nm, iid))
    # 拖拽后的 click 抑制必须是「时间戳窗口」，不能是粘性布尔：
    # 指针落在按钮外松手时浏览器不补发 click，粘性标记会永久留 true，下一次正常点击先被吃掉
    # （2026-10-24 线上实测踩到：拖完 📋 想返回，第一下没反应）。
    # 判据必须查「判定式 + 盖戳」两处，只查字符串存在会被一行 `var suppressClick = 0;` 蒙过去
    # —— 埋雷实测：把判定改回粘性 moved、把盖戳删掉，只查存在性的版本两颗雷全漏。
    if not re.search(r"if\s*\(\s*Date\.now\(\)\s*<\s*suppressClick\s*\)", s):
        bad.append("拖拽后的 click 抑制不是时间戳窗口（应判 Date.now() < suppressClick）——"
                   "粘性 moved 在指针落于按钮外时会残留，下一次正常点击被吞一次")
    if not re.search(r"suppressClick\s*=\s*Date\.now\(\)\s*\+", s):
        bad.append("拖拽结束时没盖抑制戳（suppressClick = Date.now() + N）——"
                   "没有这行，判定式永远为假，拖完会误触按钮本身")

    # ---- d) 字体继承 ----
    if not re.search(r"button,\s*input,\s*select,\s*textarea[^{]*\{[^}]*font-family:\s*var\(--font\)", s):
        bad.append("button/input/select/textarea 没显式继承 var(--font)——表单控件默认用浏览器 UA 字体，"
                   "全站字体就不统一了")

    # ---- e) 字号档位白名单 ----
    allowed = {"10.5", "11.5", "12.5", "13.5", "15", "16", "20", "32", "36", "42"}
    used = set(re.findall(r"font-size:\s*([0-9.]+)px", s))
    extra = sorted(used - allowed, key=float)
    if extra:
        bad.append("字号跑出阶梯白名单：%s（应为 %s）——每多一档，并排两张卡就多一分不像同一个产品"
                   % ("、".join(extra) + "px", "/".join(sorted(allowed, key=float))))

    if bad:
        r.fail("G41", "；".join(bad[:4]))
    else:
        r.ok("G41", "视觉规范与悬浮按钮全链路完整（标题/账户块/AI 关系行/四入口四返回可拖+拖后不吞点击/字体继承/字号 10 档）")


def g42_design_tokens(r):
    """G42 设计令牌强制：字号/圆角必须走变量（R100z164，2026-10-24）。

    由来：R100z163 我已经建好了 --fs-* 全套令牌，并在 G41 里断言「字号只有 10 档」——
    但只断言了**取值**，没断言**写法**。实际那 255 处全是裸 px，只有 3 处用了变量。
    结果用户切四页看到「几个标题大小都不一样」：首页 32px、其余三页 20px，
    而两边都「在白名单里」，G41 全绿。
    → 教训：白名单管得住「有没有跑偏的值」，管不住「值是不是同源」。
       只要还是各写各的 px，改一处就不会带动其他地方，「统一」只是这一次的巧合。

    判据（全部落在会随 bug 变形的表达式上）：
      a) 全文不允许出现 `font-size: <数字>px` —— 必须 var(--fs-*)
      b) 四个页面标题（.hero h1 / .db-title）必须是 var(--fs-page)
      c) .status-line 与 .card h2 必须同字号档 + 同色 + 都带圆点标记
      d) border-radius 的字面值只允许 999px / 50% / ≤8px（小徽章、圆点），
         10/12/14/16/20 这类容器圆角必须走 --r-note / --r-block / --r-card
    """
    p = os.path.join(HERE, "index.html")
    try:
        s = open(p, encoding="utf-8").read()
    except Exception as e:                                    # noqa: BLE001
        r.warn("G42", "index.html 读不到，跳过设计令牌校验：%s" % e)
        return
    bad = []

    # a) 裸字号
    raw_fs = re.findall(r"font-size:\s*([\d.]+)px", s)
    if raw_fs:
        from collections import Counter
        c = Counter(raw_fs)
        bad.append("仍有 %d 处写死 font-size（%s）——必须走 var(--fs-*)，否则改一档不会联动"
                   % (len(raw_fs), ", ".join("%spx×%d" % (k, v) for k, v in c.most_common(4))))

    # b) 页面标题档位
    if not re.search(r"\.hero h1,\s*\.db-title\s*\{[^}]*font-size:\s*var\(--fs-page\)", s):
        bad.append("页面标题（.hero h1 / .db-title）未使用 var(--fs-page) —— 四页标题会各走各的档位")
    if "--fs-page:" not in s:
        bad.append(":root 未定义 --fs-page —— 页面级标题没有独立档位")

    # c) 同级卡标题同款
    m_st = re.search(r"\.status-line\s*\{([^}]*)\}", s)
    m_h2 = re.search(r"\.card h2[^{]*\{([^}]*)\}", s)
    if not m_st or not m_h2:
        bad.append("找不到 .status-line 或 .card h2 定义 —— 同级标题一致性无法核对")
    else:
        if "var(--fs-h2)" not in m_st.group(1):
            bad.append(".status-line 字号未对齐 .card h2（应同为 var(--fs-h2)）—— 同卡不同标题档")
        if m_st.group(1).count("var(--ink)") == 0:
            bad.append(".status-line 颜色未对齐 .card h2（应为 var(--ink)）—— 一个蓝一个黑")
        if ".status-line::before" not in s:
            bad.append(".status-line 缺标题圆点标记（.card h2 有）—— 两张卡并排不是一个体系")

    # d) 圆角字面值
    # 豁免：echarts 的 extraCssText 是图表 tooltip 的内联样式串，不属于页面设计系统。
    allowed_small = {"999px", "50%", "0"}
    offenders = {}
    for line in s.split("\n"):
        if "extraCssText" in line:
            continue
        for m in re.finditer(r"border-radius:\s*([^;]+);", line):
            v = m.group(1).strip()
            vals = [x.strip() for x in v.split()]
            for x in vals:
                if x.startswith("var(") or x in allowed_small:
                    continue
                num = re.match(r"([\d.]+)px$", x)
                if num and float(num.group(1)) <= 8:
                    continue
                offenders[v] = offenders.get(v, 0) + 1
    if offenders:
        bad.append("圆角仍有非令牌值：%s —— 容器圆角只允许 --r-card/--r-block/--r-note"
                   % ", ".join("%s×%d" % (k, v) for k, v in list(offenders.items())[:4]))

    if bad:
        for b in bad:
            r.fail("G42", b)
    else:
        r.ok("G42", "设计令牌到位（字号全走 var(--fs-*) / 页面标题统一 --fs-page / 同级卡标题同款 / 圆角仅三档）")


def g43_stock_chip_layout(r):
    """G43 股票卡「名称+代码」不可断 + 徽章只标少数派（R100z168，2026-10-24）。

    由来（用户原话）：「这个怎么歪七扭八的，一会代码换行，一会代码就在名称旁边，
    已经标注自选的是什么意思，没标注的又是什么意思」。

    ① 折行的根因不是字体，是排版没有「不可断的整体」：
       <b>名称</b><span.cd>代码</span> 各自 nowrap，但**两个 inline 元素的边界仍然可以断行**。
       实测「名称 4 字(50px) + 间距 3 + 代码(35.2~37.8px)」= 88.2~90.8px，而卡片内容宽正好 90.8px
       → 代码里 1 多的窄、8 多的宽，**同一行文字只因数字组合不同就随机折行**（48 张卡折了 22 张，
       3 字名 0/6 折、4 字名 22/42 折）。
       修法：两者裹进 .nm（flex），名称 flex:0 1 auto + ellipsis（窄了就省略，绝不折行），代码 flex:none。

    ② 徽章的根因是「标了多数派」：原判据 poolHit === false（= AI 补位）当天命中 43/48 = 90%，
       等于没标注；且「自选」在股票软件里默认指「我的自选股」，读者会当成可点的收藏按钮。
       改成只标 poolHit === true（机械池命中，当天 5/48）。

    ③ R100z169（2026-10-24，用户：「能不能把机械池这个徽章删掉，换成在标的右上角增加一个小蓝圆圈…
       这样所有标的方块的尺寸就是一样的了」）：文字徽章本身是**卡片高度不一致的唯一原因**
       —— 实测无徽章 51px、有徽章 68px（多出整整一行 17px），家电家居 / 光模块 两个板块因此
       同时存在两种高度。改成右上角 6px 圆点（.pool-hit::after），不占任何布局宽度。
       圆点**必须压角**（top/right:-2px）而不能画在框内：预测卡内容宽只有 90.8px，
       「华瓷股份 001216」= 名 50 + 间隙 3 + 码 36.7 = 89.7，右端距边框只剩 4px，
       圆点进框内要么盖住代码末位、要么把名称挤成省略号（折行问题换形式复发）。

    判据（反向锁死，防止下一轮把废弃写法补回来）：
      a) 每个 .stock-chip 生成点必须紧接 <span class="nm"><b>，数量必须对上
      b) .stock-chip .nm 必须 display:flex；.nm b 必须带 text-overflow:ellipsis
      c) 判据必须是 poolHit === true（不许回到 false）；不许再出现 class="self-tag" 文字徽章；
         生成点必须拼 ' pool-hit'；::after 必须 absolute + 圆形；基础规则必须 overflow:visible
         且**不得加 padding**（内缩腾位会把折行弄回来）
      d) ai-foot 必须用文字说明「圆点」—— 圆点没有文字，图例是它唯一的解释
    """
    p = os.path.join(HERE, "index.html")
    try:
        s = open(p, encoding="utf-8").read()
    except Exception as e:                                    # noqa: BLE001
        r.warn("G43", "index.html 读不到，跳过股票卡排版校验：%s" % e)
        return
    bad = []

    # a) 生成点必须包 .nm
    # 注意：class="stock-chips"（容器）与 class="stock-chip"（卡片）只差一个 s，必须用负向断言区分，
    # 否则容器会被算成「没包 .nm 的卡片」（首次实现就踩了：6 处 vs 4 处）。
    n_chip = len(re.findall(r'class="stock-chip(?!s)', s))
    n_nm = s.count('<span class="nm"><b>')
    if n_chip < 4:
        bad.append("只找到 %d 处 .stock-chip 生成点（应 ≥4：AI 预测 / 开盘精选 / 谐波池 / 连板梯队）"
                   "—— 少一处说明某个板块的卡片不再走统一排版" % n_chip)
    elif n_nm != n_chip:
        bad.append("%d 处 .stock-chip 只有 %d 处包了 <span class=\"nm\"><b> —— "
                   "裸 <b>+<span.cd> 在临界宽度会随机折行（R100z168 根因）" % (n_chip, n_nm))

    # b) CSS 契约
    if not re.search(r"\.stock-chip \.nm \{[^}]*display:\s*flex", s):
        bad.append(".stock-chip .nm 未设 display:flex —— 名称与代码不在同一行容器，折行会复发")
    if not re.search(r"\.stock-chip \.nm b \{[^}]*text-overflow:\s*ellipsis", s):
        bad.append(".stock-chip .nm b 缺 text-overflow:ellipsis —— 窄视口下名称会溢出到代码上")

    # c) 圆点口径（R100z169：文字徽章 → 右上角压角圆点）
    m_base = re.search(r"\.stock-chip\.pool-hit\s*\{([^}]*)\}", s)
    m_dot = re.search(r"\.stock-chip\.pool-hit::after\s*\{([^}]*)\}", s)
    if "self-tag" in s:
        bad.append("源码里又出现 self-tag 文字徽章（CSS 或生成串任一）—— 它会独占一行，"
                   "同板块卡片高度立刻不一致（实测无徽章 51px / 有徽章 68px），R100z169 已改成右上角圆点")
    if "poolHit === false" in s:
        bad.append("判据又回到 poolHit === false（标 AI 补位）—— 该口径当天会命中 90% 的卡片，"
                   "等于没有标注；正确做法是标少数派 poolHit === true")
    if "poolHit === true" not in s:
        bad.append("找不到 poolHit === true 的判据 —— 机械池命中的票没有任何标识")
    if "' pool-hit'" not in s:
        bad.append("生成点没有拼 ' pool-hit' 类 —— 圆点不会渲染出来")
    if not m_base:
        bad.append("缺 .stock-chip.pool-hit 基础规则 —— 无法保证 overflow:visible，半个圆会被裁掉")
    elif "overflow:visible" not in m_base.group(1).replace(" ", ""):
        bad.append(".stock-chip.pool-hit 没设 overflow:visible —— top/right:-2px 的半个圆会被裁掉")
    elif "padding" in m_base.group(1):
        bad.append(".stock-chip.pool-hit 里加了 padding —— 内缩腾位会把「名称+代码」重新挤到折行"
                   "（R100z168 的折行会以另一种形式复发）")
    if not m_dot:
        bad.append("缺 .stock-chip.pool-hit::after 规则 —— 右上角圆点没有样式")
    else:
        css = m_dot.group(1).replace(" ", "")
        if "position:absolute" not in css:
            bad.append("圆点不是 position:absolute —— 它会占掉 6px 布局宽度，名称+代码重新折行")
        if "border-radius:50%" not in css:
            bad.append("圆点缺 border-radius:50% —— 会显示成方块")

    # d) 图例：圆点没有文字，图例是它唯一的解释
    m_foot = re.search(r'class="ai-foot">([^\n]{0,1600})', s)
    if not m_foot:
        bad.append("找不到 .ai-foot 标的筛选口径段 —— 圆点没有图例可挂")
    elif "圆点" not in m_foot.group(1):
        bad.append(".ai-foot 没用文字说明「右上角圆点」的含义 —— 圆点本身不带文字，"
                   "触屏上又没有 title 提示，缺图例读者无从判断")

    if bad:
        for b in bad:
            r.fail("G43", b)
    else:
        r.ok("G43", "股票卡排版到位（.nm 包裹 %d/4 处 / 名称可省略代码不缩 / 圆点标少数派机械池且不占布局 / 脚注有图例）"
             % n_nm)


def g44_zombie_mirror_nodes(r):
    """G44 僵尸镜像节点：`ashare` 下不得存在与顶层同名的容器（R100z171，2026-10-24）。

    由来（第三次踩同一个坑）：同一份数据被搬过一次家，旧位置的副本**没有任何代码删它**，
    于是永久留在 data.js 里当僵尸——而闸门只查顶层键（G12 孤儿键 / G16 读顶层），
    副本既不被读、也不被查，还能继续被 prompt 喂着长大。

      ① `ashare.duanban`（2026-10-14 后无读写方）→ G16 改读顶层时才被发现；
      ② `ashare.accumulation` → 吸筹 90 字地板**从未生效**（R100z161c 才发现），
         因为 G16 读的是不存在的路径，恒空即恒过；
      ③ `ashare.aiPrediction`（本轮）→ 19,511 字符副本，前端注释第 2859 行早写着
         「只读顶层 aiPrediction.verification」，push_notify 也不读，**零读方零写方**；
         可 19:30 prompt 仍以「研判/推送文案与兼容读取路径引用」为由要求每天同步写一份，
         同一条 prompt 里却又写「严禁引用 ashare.aiPrediction 旧预测集」——写它、又不许读它。
         危害不是那 33KB，而是**哪天 AI 只写了副本**：页面验证块不更新、四道闸门全绿。

    判据（类级，不写死单个节点名）：对每个顶层容器键 K，若 `ashare[K]` 存在即 FAIL。
    这样以后任何节点再搬家、旧副本没删干净，都会在第一次部署就被拦下，不靠人记得。
    """
    try:
        D = load_data_json()
    except Exception as e:                                  # noqa: BLE001
        r.warn("G44", f"data.js 读取失败，跳过僵尸镜像校验：{e}")
        return
    ah = D.get("ashare")
    if not isinstance(ah, dict):
        r.ok("G44", "ashare 节点不存在或非字典，无镜像可比")
        return
    ghosts = []
    for k in D.keys():
        if k == "ashare" or not isinstance(D.get(k), (dict, list)):
            continue
        if k in ah:
            size = len(json.dumps(ah[k], ensure_ascii=False))
            ghosts.append((k, size))
    if ghosts:
        for k, size in ghosts:
            r.fail("G44", f"ashare.{k} 是顶层 {k} 的僵尸镜像（{size} 字符）：全库零读方零写方，"
                          f"G12/G16 只查顶层，所以它既不被渲染也不被校验，却可能被 prompt 喂着长大——"
                          f"一旦哪天写回只落到副本，页面不更新而所有闸门全绿。"
                          f"删副本前先 grep 一遍 prompt 里的 ashare.{k}，同步删掉写入指令")
        return
    r.ok("G44", f"ashare 下无僵尸镜像节点（比对了 {sum(1 for k in D if isinstance(D.get(k), (dict, list)))} 个顶层容器键）")


def g45_modal_nav_group(r):
    """G45 弹窗导航分组（R100z200，2026-10-25）。

    事故：用户 2026-10-25 截图指出「标的页面的左右按钮不是跟着交易预案里面标的切换的」。
    根因是同一个坑犯了第二次（第一次 R100h，吸筹/九门卡片）：`openStkModal()` 只认全局
    `STK_GRP`，而任何**内联 onclick 直接开弹窗**的列表都从不设置分组，于是箭头翻的是
    「上一位读者点过的那个列表」的残留顺序——点交易预案的票能翻到龙虎榜去。

    为什么必须上闸门而不是靠记性：这个坑的特点是**功能完全不报错**。箭头在、能翻、能看 K 线，
    只是翻错了清单；只有用户逐票核对才会发现。而且新增任何一张可点列表都会重新引入它。

    判据（三条都要，缺一条就挡不住）：
      a) `openStkModal()` 函数体里有 `stkSyncNav(code)` —— 兜住所有仍走内联 onclick 的旧调用点：
         代码不在当前分组里就关箭头，宁可没箭头，也不给一个会翻到别处的箭头；
      b) 全文不得有 `onclick="openStkModal(` —— 列表点击必须走 `stkOpenIn(list, idx)` 先建组；
      c) 三处「清单型」列表的生成串必须显式带 `stkOpenIn(`：龙虎榜 `.lhb-go`、
         交易预案 `.td-name`、今日不做/备选 `.td-x-nm`。
    """
    p = os.path.join(HERE, "index.html")
    try:
        s = open(p, encoding="utf-8").read()
    except Exception as e:                                    # noqa: BLE001
        r.warn("G45", "index.html 读不到，跳过弹窗导航校验：%s" % e)
        return

    bad = []
    i = s.find("function openStkModal(")
    if i < 0:
        bad.append("找不到 openStkModal()——K 线弹窗入口没了")
    else:
        body = s[i:i + 2400]
        if "stkSyncNav(code)" not in body:
            bad.append("openStkModal() 入口没调 stkSyncNav(code)——旧调用点会沿用上一组残留顺序，"
                       "点交易预案能翻到龙虎榜")
    n = s.count('onclick="openStkModal(') + s.count("onclick=\\\"openStkModal(")
    if n:
        bad.append("还有 %d 处内联 onclick 直开 openStkModal——必须先 stkOpenIn(list, idx) 建组" % n)
    for need, why in (("stkOpenIn(window._LHB_GRP,", "龙虎榜行"),
                      ("stkOpenIn(window._TD_GRP.p,", "交易预案主清单"),
                      ("stkOpenIn(window._TD_GRP.' + kind + '", "今日不做/备选折叠区")):
        if need not in s:
            bad.append("%s 未走 stkOpenIn（箭头会跟着上一位读者点过的清单翻）" % why)

    if bad:
        r.fail("G45", "；".join(bad[:3]))
    else:
        r.ok("G45", "弹窗导航分组一致：openStkModal 兜底 stkSyncNav、无内联直开、"
                    "龙虎榜/交易预案/今日不做·备选三处清单均先建组再开")


def g46_sector_board_names(r):
    """G46 预测板块名可解析 + 别名表同源（R100z224，2026-10-26）。

    事故：`aiPrediction.sectors[].sector` 由 AI 每周手写，它写**主题名**（创新药 / 光模块 /
    半导体设备 / 石油开采），而 `ashare.allBoards` 里是**同花顺行业名**（化学制药 / 通信设备 /
    半导体 / 油气开采及服务）。2026-10-22 / 10-23 / 10-26 三天实测，6 个预测板块里分别有
    3 / 5 / 4 个对不上，**每周重演**。后果不是报错，是：

      - `entry_zone.py` 按名精确匹配 → 算不出 `fundCheck`/`entry` → 该板块买点整块为空；
      - 前端涨幅走 `pctAlias` 别名 → 照常显示。

    于是「页面一切正常，只是买点区安静地空着」——这类问题只有闸门能发现，靠看页面看不出来。

    判据（两条都要）：
      a) `board_alias.py --check` 必须通过：`board_alias.json` 与 `index.html` 的 `pctAlias`
         **逐字同源**。两份手工副本必然漂移（节假日表踩过，G13 同款），而漂移的后果是
         「买点按 A 板算、涨幅按 B 板算」，谁都发现不了；
      b) 用 data.js 实跑 `validate_stocks.check_sector_board`，不许有 FAIL。

    为什么 b 不能只依赖 G7：G7 把 validate_stocks 的所有 FAIL **一律降级为 WARN**（设计如此，
    因为历史遗留 FAIL 归对应任务处理）。新加的这条若只走 G7，就永远只是 WARN，
    「22:00 就挡住」等于没做。这里单独硬拦。
    """
    # a) 别名表同源
    try:
        import board_alias as _ba
        ok, msg = _ba.check()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G46", f"board_alias.py 无法导入/执行，别名表同源校验失效：{e}")
        return
    if not ok:
        r.fail("G46", f"别名表两边不同源：{msg}")
        return

    # b) 板块名可解析
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.warn("G46", f"data.js 读取失败，跳过板块名校验：{e}")
        return
    try:
        import validate_stocks as _vs
        fails, warns = _vs.check_sector_board(D, scope="ai")
    except Exception as e:                                   # noqa: BLE001
        r.fail("G46", f"validate_stocks.check_sector_board 无法执行：{e}")
        return
    boards = len(((D.get("ashare") or {}).get("allBoards") or []))
    if fails:
        r.fail("G46", "；".join(fails[:3]))
        return
    if boards < 20:
        r.warn("G46", f"allBoards 仅 {boards} 个板块，板块名无法核对（多半 16:00 抓取失败）")
        return
    r.ok("G46", f"预测板块名全部可落到 allBoards（{boards} 个行业板块）；{msg}；"
                f"{len(warns)} 个主题名走别名映射（买点 basis 已注明按哪条指数算）")


def g47_buy_list_truth(r):
    """G47 今日买入清单：结构 / 价格自洽 / 票必须在池里（R100z232，2026-10-26）。

    背景：用户第 4 项要求——当天若三池（断板反包确认池 / 横盘启动贴沿 / 陈学长双池）
    命中，就要在开盘精选里直接给「当日买入 + 买入价格区间」。清单由
    `update_star.py::build_buy_list()` 生成，写进 `duanban.star.buyList`。

    这张清单和别的只读展示字段不同，它是**唯一会被当成下单指令读的东西**，
    错的方式也特别安静，所以这里查的都是「页面照常漂亮、但读的人会做错事」的几种：

      a) **state 与价格不自洽**：`state='buy'`（页面写「在买区内」）而现价其实在区间外。
         改算法/改滑点参数时最容易脱节——页面不会报错，只会平静地骗人。
      b) **凭空造票**：清单里的 code 在三个池里都找不到。这条最严重，等于页面自己编标的。
      c) **买区上下沿颠倒**（low > high）：箱体几何算错时会出现。
      d) **非沪深主板**（688/300/8xx）：硬约束 3，进了清单就等于推荐不能买的东西。
      e) **断板反包被塞了目标价**：那一池回测只验证到「次日盘口成交 + T日低点作废」，
         给它编一个 targetPrice 就是造假。反向断言。
      f) **接线断了**：`build_buy_list` 没人调、`st.buyList` 前端没人渲染——
         清单静默消失，而所有字段级校验都会「因为没有数据而跳过」显示全绿。

    f 是这道闸存在的主要理由：字段校验只保护「有数据时的数据」，接线的完整性只能靠源码断言。
    """
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.warn("G47", f"data.js 读取失败，跳过买入清单校验：{e}")
        return

    # ---- f) 接线完整性：生成端 + 渲染端 ----
    try:
        up = open(os.path.join(HERE, "update_star.py"), encoding="utf-8").read()
        htm = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G47", f"无法读取 update_star.py / index.html，接线校验失效：{e}")
        return
    wire = []
    if "def build_buy_list(" not in up:
        wire.append("update_star.py 缺 build_buy_list()")
    if '"buyList": buy_rows' not in up and '"buyList":buy_rows' not in up:
        wire.append("update_star.py 未把 buy_rows 写进 star['buyList']")
    if "build_buy_list(" not in up.split("def build_buy_list(", 1)[-1].split("\ndef ", 1)[-1]:
        wire.append("update_star.py 的 build_buy_list 没有被调用（只定义不接线）")
    if "st.buyList" not in htm:
        wire.append("index.html 未渲染 st.buyList")
    if wire:
        r.fail("G47", "买入清单接线断了：" + "；".join(wire))
        return

    # ---- 结构 ----
    star = ((D.get("duanban") or {}).get("star") or {})
    bl = star.get("buyList")
    summ = star.get("buySummary") or {}
    if bl is None:
        # 兼容窗口：9:45 任务升级前的存量 data.js 没有这个字段。不判 FAIL，
        # 但必须说清「没跑」而不是「没买点」——两者在页面上长得一样。
        r.warn("G47", "duanban.star.buyList 缺失（9:45 任务尚未按新口径重跑）；"
                      "缺失 ≠ 今日无买点")
        return
    if not isinstance(bl, list):
        r.fail("G47", f"duanban.star.buyList 类型应为 list，实际 {type(bl).__name__}")
        return
    if not isinstance(summ, dict) or summ.get("n") != len(bl):
        r.fail("G47", "buySummary.n=%r 与 buyList 实际 %d 只不符（汇总与明细脱节）"
                      % (summ.get("n") if isinstance(summ, dict) else None, len(bl)))
        return

    # 各池的真实成员集合（用于 b 造票检查）
    duanban_pool = set()
    for p in (D.get("duanban") or {}).get("confirmed") or []:
        if isinstance(p, dict):
            duanban_pool.add(str(p.get("code")))
    bp_pool = {str(x.get("code")) for x in
               ((D.get("breakoutPool") or {}).get("passed") or []) if isinstance(x, dict)}
    chen_strict = {str(x.get("code")) for x in
                   ((D.get("chenPool") or {}).get("strict") or []) if isinstance(x, dict)}
    chen_loose = {str(x.get("code")) for x in
                  ((D.get("chenPool") or {}).get("loose") or []) if isinstance(x, dict)}
    pick_pool = {str(p.get("code")) for p in (star.get("picks") or []) if isinstance(p, dict)}

    def _n(v):
        try:
            return None if v is None or v == "" else float(v)
        except (TypeError, ValueError):
            return None

    bad = []
    for i, it in enumerate(bl):
        tag = "buyList[%d]" % i
        if not isinstance(it, dict):
            bad.append("%s 不是对象" % tag)
            continue
        code = str(it.get("code") or "")
        src = it.get("src")
        tag = "buyList[%d](%s)" % (i, code or "?")
        # 必需字段
        miss = [k for k in ("code", "name", "src", "srcZh", "low", "high",
                            "invalid", "state", "stateZh") if it.get(k) in (None, "")]
        if miss:
            bad.append("%s 缺字段 %s" % (tag, "/".join(miss)))
            continue
        if not code.startswith(("60", "00")):
            bad.append("%s 非沪深主板（硬约束 3）" % tag)
        if src not in ("chen_strict", "chen_loose", "duanban", "bp"):
            bad.append("%s src=%r 不在白名单" % (tag, src))
            continue
        # d) 票必须在池里
        member = {"chen_strict": chen_strict, "chen_loose": chen_loose,
                  "bp": bp_pool, "duanban": (duanban_pool | pick_pool)}[src]
        if code not in member:
            bad.append("%s 声称来自 %s，但该池里没有这只票（凭空造票）" % (tag, src))
        # c) 买区上下沿
        lo, hi, last, inv = _n(it.get("low")), _n(it.get("high")), _n(it.get("price")), _n(it.get("invalid"))
        if lo is None or hi is None:
            bad.append("%s 买区缺数值" % tag)
            continue
        if lo > hi:
            bad.append("%s 买区上下沿颠倒（low %.2f > high %.2f）" % (tag, lo, hi))
        if inv is not None and lo > 0 and inv > lo:
            bad.append("%s 证伪价 %.2f 高于买区下沿 %.2f（跌穿才算错，不该在买区上方）"
                       % (tag, inv, lo))
        # a) state 与价格自洽
        st = it.get("state")
        if last is None:
            exp = "unknown"
        elif last > hi * 1.0001:
            exp = "over"
        elif last < lo * 0.9999:
            exp = "under"
        else:
            exp = "buy"
        if st != exp:
            bad.append("%s state=%s 但现价 %s 对买区 %.2f~%.2f 应为 %s（页面会写着「%s」而价格在区外）"
                       % (tag, st, ("%.2f" % last) if last is not None else "缺失",
                          lo, hi, exp, it.get("stateZh")))
        # e) 断板反包不许给目标价
        if src == "duanban" and it.get("target") not in (None, ""):
            bad.append("%s 断板反包池给了目标价 %.2f——该池回测未验证目标价，属编数字"
                       % (tag, _n(it.get("target")) or 0))

    if bad:
        r.fail("G47", "；".join(bad[:4]) + ("（共 %d 处）" % len(bad) if len(bad) > 4 else ""))
        return
    if not bl:
        r.ok("G47", "买入清单为空（三池当日无买点，属常态）；接线与结构校验通过")
        return
    n_buy = sum(1 for x in bl if x.get("state") == "buy")
    r.ok("G47", "买入清单 %d 只（现价在买区内 %d 只）；买区/证伪价自洽、票全部可回溯到池、"
                "断板反包未编目标价；生成端与渲染端接线完整" % (len(bl), n_buy))


def main():
    argv = sys.argv[1:]
    as_json = "--json" in argv
    deep = "--deep" in argv
    if "--help" in argv:
        print(__doc__)
        return 0

    base = os.path.join(HERE, "audit_expectations.json")
    if not os.path.exists(base):
        print("[FAIL] 缺 audit_expectations.json（prompt 基线），先重抽基线再验收")
        return 2
    exp = json.load(open(base, encoding="utf-8"))

    r = Report()
    g1_syntax(r)
    g2_g3_contract(r, exp)
    g4_baseline(r, exp)
    g5_source_assertions(r)
    g6_calendar(r)
    g7_validate_scopes(r)
    g8_g9_g10_data(r)
    g12_deadlinks(r)
    g13_holiday_table(r)
    if deep:
        g11_smoke(r, deep=deep)
    else:
        g11_smoke(r, deep=False)
    g14_prompt_broken_path(r)
    g15_interp_bins(r)
    g16_narrative_depth(r)
    g17_template_tails(r)
    g18_policy_signals(r)
    g19_summary_freshness(r)
    g20_sector_deduce_no_template(r)
    g21_failpool_disjoint(r)
    g22_news_level_noise(r)
    g23_cross_stock_phrasing(r)
    g24_unchecked_disclosure(r)
    g25_memory_hygiene(r)
    g26_pool_sector_keys(r)
    g27_memory_reachability(r)
    g28_freshness_consistency(r)
    g29_name_and_risk_hygiene(r)
    g30_display_prefs(r)
    g31_deduce_prefix(r)
    g32_stop_ledger_truth(r)
    g33_atr_stop_consistency(r)
    g34_desk_structure(r)
    g35_atr_evidence_truth(r)
    g36_desk_exec_plan(r)
    g37_forecast_score_truth(r)
    g38_flags_respected(r)
    g39_open_desk_page(r)
    g40_score_page_and_slots(r)
    g41_visual_norms(r)
    g42_design_tokens(r)
    g43_stock_chip_layout(r)
    g44_zombie_mirror_nodes(r)
    g45_modal_nav_group(r)
    g46_sector_board_names(r)
    g47_buy_list_truth(r)

    if as_json:
        print(json.dumps({"counts": r.counts, "items": r.items}, ensure_ascii=False, indent=2))
    else:
        groups = []
        for g, lv, msg in r.items:
            if not groups or groups[-1][0] != g:
                groups.append((g, []))
            groups[-1][1].append((lv, msg))
        print("=" * 78)
        print(" audit_pipeline · 七自动化与流水线一次性机器验收 (R100z17)")
        print("=" * 78)
        for g, rows in groups:
            print(f"\n[{g}]")
            for lv, msg in rows:
                print(f"  {lv:<4} {msg}")
        print("\n" + "-" * 78)
        print(f"  PASS={r.counts.get('PASS',0)}  WARN={r.counts.get('WARN',0)}  "
              f"FAIL={r.counts.get('FAIL',0)}  INFO={r.counts.get('INFO',0)}")
        print("-" * 78)
    return r.exit_code()


if __name__ == "__main__":
    sys.exit(main())

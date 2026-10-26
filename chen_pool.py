#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""陈学长「断板反包」独立技术指标（data.js 顶层 chenPool）。

================================================================================
为什么单独建一个指标（2026-10-25 用户要求）
================================================================================
原话：「建一个陈学长技术指标，而不是更新断板反包，这样过一周可以回测哪个胜率高，
也能看两个技术指标选出来的标的有什么区别。」

所以本脚本**只读不改** check_duanban.py / merge_duanban.py / duanban 节点：
断板反包池保持原样做对照，本脚本产出并行的第二套池。两边用**同一套结算口径**
（见 settle_one）算胜率，结果才可比。

口径来源：抖音「陈学长不吃面」（自称狼哥/狼王）269 份视频蒸馏 ——
技能 duanban-fanbao-chen（SKILL.md / GLOSSARY.md / EVIDENCE.md）+ 工作区
`/Users/loccco/WorkBuddy/2026-10-25-16-47-48/陈学长断板反包/` 的实证结论。

================================================================================
口径（2026-10-25 冻结；回测出结果前不许改这些数字，否则就是拿未来调参）
================================================================================
连板数不是门槛 —— 只有一套规则，「T 日连板数 ≥ 2」不作准入门槛。
  用户 2026-10-25 纠正：「陈学长没有要求连板≥2，首板也可以。」
  他确实说过 001「你连板都没有，你反包什么呢兄弟」，但那是**偏好/打分**语境，不是准入条件；
  把它当硬门槛是我方早期的取舍（当时的注释写过「这是单一条件里边际最大的一条」= 统计结论，
  不是他的规则），已删。首板进池后连板数仍然算出来、仍然显示在卡片与弹窗上、仍然参与排序 ——
  「有连板前情」是他的加分项，不是门票。这条改动同时把「断板池 87% 是首板」这个
  两池差异的主因拆掉了，所以两个池的重合度会明显上升，差异主要来自「爆量断板」这一层。

硬结构 —— strict 严格池与 loose 放宽池都要求：

  S1 沪深主板 60/00，最新收盘 ≥ 4 元
       他 041：「四块钱以下的不要」。这条没被数据反驳，照做。
  S2 T 日涨停（涨幅 ≥ 9.8%，主板口径）
  S3 T 日连板数 —— **只计算、只展示、只排序，不做门槛**（见上）
       实测边际仍在（首板 触+3.5% 36.2% → 2 连板 50.9% → 3 连板 61.5%），
       所以卡片上照样写「N 板」，读者能自己按这个维度再筛一遍。
       ⚠️ 连板数由 K 线自算（calc_lbc），不用涨停池 lbc 字段——涨停池只回看 5 天，
       且东财兜底源根本没有该字段，靠字段会在降级时把全池误杀。
  S4 T+1 断板（未续板）且量比 V(T+1)/V(T) ≥ 2.0，**不设上限**
       他 008：「这根大阴它的量要越大越好。」实测 1.1~3.5 倍之间毫无区别
       （37.5/37.5/38.7），3.5 倍以上才是信息所在（3.5–5 倍 46.3%、5–8 倍 49.7%）。
       看板 `1.1 <= r1 <= 3.5` 的上限把最好一档砍掉了。
       >8 倍均值转负（-0.35%），不排除但前端标「爆量尾部风险」。
  S5 T+2 起缩量：V(T+2) < V(T+1)
  S6 洗盘 ≤ 3 日（T 日距今 2~4 个交易日）
       他 025：「洗个七八天？没道理。」实测缩量 1 天反包率 23.0% > 2 天 18.4% > 3 天 14.8%，
       故窗口收到 4 天（看板是 5 天）。
  S7 收盘不破 T 日最低价 ×0.98
       他 269 说破位只是「进入观察」，但实测「破了 T 日开盘价但没破 low」那 580 笔
       反而更好（触+3.5% 41.9% vs 守住 37.3%）→ 锚点用 low 比用开盘价更合适。
  S8 最新收盘 ≥ MA5 ×0.97（站稳）

软条件 —— 只有 strict 严格池额外要求（这两条最严，会把样本砍到 1/6）：

  S9 T 日缩量：V(T) / 前 5 日均量 ≤ 1.0
       他 008：「缩量代表着高度控盘，没有什么人出也没有什么进。」
       实测单调 16 个点：<0.8 → 触+3.5% 44.7%，>3 → 28.9%。
  S10 T+1 大阴：涨跌幅 ≤ -3%
       他 008：「砸得越深越好，一步洗到位。」实测 <-7% 触+3.5% 50.6%，
       而 -3~0% 是全部档位里唯一均值为负的（-0.06%、33.6%）。

⭐ 为什么拆 strict / loose 两个池：单测「叠加越多越好」在记忆里已被证伪——
   ⑦⑧ 两条叠加后样本从 1105 掉到 132，边际只剩 2~3 个点。所以两个池都留痕，
   一周后能直接回答「他这两条软条件到底值不值」。这是本模块存在的意义。

⭐ 为什么没有漏斗（用户 2026-10-25「这个删掉」）：
   漏斗解释的是「为什么只剩这几只」，但它把「一天的分层统计」放到了用户每天要看的位置，
   而且连板那道门（当时拦下 110 只）本身是错的。判定逻辑没变，只是不再向 data.js 输出 funnel。
   判据的可解释性改由「口径折叠块 + 卡片脚注的形态串」承担。

================================================================================
明确不采纳的三条（他的原话，但数据方向相反）—— 不许后人再「补上」
================================================================================
  ① 「三十五十亿市值」（041）：实测 >100 亿 触+3.5% 44.0% > 30–50 亿 33.6%，
     完全反向。他那句话的上下文是「成交量好不好算」= 执行便利性，不是收益优势。
  ② 「最上面的不做」（041）：实测高位组（收盘/120日最高 >0.95）41.9% > 低位组 32.4%。
     他指的是「没有连板前情、没人接力」的那种高位，不是有连板的强势高位。
  ③ 「已经反过一次的不做」（006）：实测已反过一次 52.7% vs 还没反过 36.8%，差 4 倍。
     他指的是「反过之后又走弱两天」，数据里多数是「还在连板中的强势票」= 动量。
  → 这三条是「配他的执行方式才成立」的条件，当筛选器用会反向。

================================================================================
日线做不到的（如实标注，不假装实现）
================================================================================
分时单根量 ≥ 流通市值 0.5%／量额双放／量能递增／盘口撤单／量化特征（九十度角、锯齿分时）／
板块内角色（将军·元帅·气氛组）／自选池熟悉度。这些是他真正的决策依据，
但免费源无历史分时。页面写「缺口」，不写「已实现」。

================================================================================
买点 / 卖点 / 止损 / 仓位 —— 看板完全没有，这里给
================================================================================
  买点：确认日**次日开盘**买入（确认式：等洗盘缩量走完再进，不追涨）。
        他 149：「我的目的就是今天要走，我想要的就是 3、4、5 厘米。」
  卖点：+5% 当天兑现（TARGET_PCT），或持仓满 10 个交易日无条件走（HOLD_DAYS）。
        他 006：「抓一只羊一般不超过两天。」（原法 +3.5% / 2 日，已按实测改为 +5% / 10 日）
  止损：洗盘段（T+1..确认日）最低价。他 269：「破了第一天小阴的支撑位……就是止损的时候。」

  ⚠️ R100z233（2026-10-26）：上面三条是**他本人的原法**，实测已改为 +5% / 持 10 日 /
      止损锚=T 日最低价（原法 +0.701% → 实测 +3.054%）。**这是对原法的主动偏离，
      不是我读错了他的规则**——原话引文保留在这里，就是为了让读者能一眼看出差在哪、
      以及为什么改（原因见 RULE_FROZEN_AT 上方的分解表）。
  仓位：RISK_BUDGET / 止损距离 —— 单笔最大亏总仓 1.5%（他的原话是「高 4 低 6」那套
        按位置分档的量法，但那一档没在视频里量化到可复现，所以这里只用能算的部分：
        亏多少由「仓位 × 止损距离」决定，止损越宽仓位越小，1.5% 封顶）。
        ⚠️ 这不是可选建议：实测放开连板/爆量后亏超 -5% 的比例从 8.4% 升到 14.3%，
        只改筛选不改仓位就是把胜率换成爆仓概率。

================================================================================
用法
================================================================================
  python3 chen_pool.py --module --out chen_draft.json   # 只出草稿（不写 data.js）
  python3 chen_pool.py --merge                          # 出草稿并合并进 data.js（含留痕结算）
  python3 chen_pool.py --settle-only                    # 只结算留痕，不动池

退出码恒为 0（池空是合法状态）。抓取失败会在 stderr 明确告警，并保留既有 chenPool。
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import check_duanban as CD                       # 复用抓取/打标（只 import，不改它）
from common import write_data_js, write_json_atomic, roll_fail_pool

DATA_JS = os.path.join(HERE, "data.js")
LEDGER = os.path.join(HERE, "chen_ledger.json")

# ---------------------------------------------------------------- 冻结参数
# R100z221（用户 2026-10-26）：「不要有什么 v1v2，就只有一个版本」。
# 原先这里挂着 RULE_VERSION 常量、台账每一条都写 ruleVersion、页面口径块里写「本版的修正点，
# 上一版误把连板≥2 当成准入门槛」——三处都在解释一段已经不存在的历史，读者要花力气才能确认
# 「我现在看的到底是哪一版」。现在只留冻结日期：参数在回测出结果之前定死、出结果后不调参，
# 这个承诺用日期表达就够了，不需要版本号（只有一个版本时，版本号是噪音）。
# ⚠️ 台账里已有的 13 条样本**不删**：旧口径（要求连板≥2）选出来的票是新口径的子集，
#    它们同样满足今天的全部条件，混在一起不构成污染；删掉等于凭空减少样本量。
#    这也与审核意见 §6「清掉旧版从零计数」不同——那条建议假定两套规则互斥，实际是包含关系。
#
# R100z233（2026-10-26）：**冻结被解除了，日期随之更新到 2026-10-26**。
# 2026-10-25 那次冻结的承诺是「回测出结果前不调参」；现在 829 笔样本已跑完
# （`chen_study.py` 基线 → `rule_final_check.py` 八组候选 → 训练/样本外分段验证），
# 按用户当日「深度优化陈学长」的指令，把实测更优的口径换进来，并重新冻结。
# 改了三处、只这三处：入场加「距MA5≥3%」、止盈 3.5%→5%、持有 2→10 日、止损锚
# 洗盘段最低价→T日最低价。逐项分解与反直觉说明见下方 R100z233 常量块。
# 解冻不是「随手调参」：新口径的样本外（2026-01~）是 +2.972%，训练段 +3.090%，
# 两段一致；而原法样本外只有 +0.770%。没有这条对比就不该解冻。
RULE_FROZEN_AT = "2026-10-26"
PRICE_MIN = 4.0          # S1 股价下限（元）
VOL_UP_MIN = 2.0         # S4 断板日放量倍数下限（无上限）
SHRINK_MAX = 1.0         # S9 T 日缩量上限 V(T)/前5日均量
BIGDROP_MAX = -3.0       # S10 T+1 涨跌幅上限（%），≤ -3 才算大阴
MAX_WASH_DAYS = 4        # S6 T 日距今最多 4 个交易日（洗盘 ≤3 日）
LOW_TOL = 0.98           # S7 破位容忍 2%
MA5_TOL = 0.97           # S8 站稳容忍 3%
TARGET_PCT = 5.0         # 卖点 +5%（为什么从 3.5% 改到 5%：见下方口径说明的对照表）
STOP_PCT_FALLBACK = -3.0  # 止损兜底（无 T 日最低价时）
HOLD_DAYS = 10           # 持仓上限（交易日；为什么从 2 日改到 10 日：同上）
RISK_BUDGET = 1.5        # 单笔最大亏总仓 %
# R100z233b：**旧口径的数值必须留在代码里**，不是为了兼容旧代码，而是为了让台账里
# 换版前就开仓的样本能按它自己的参数结算。settle_one 改读条目自带的 tgtPct/holdDays 之后，
# 这两个常量是「历史条目的缺省值」——删掉它们，那 9 条 2026-10-25 的样本会被 5%/10 日
# 重新结算，等于换版那天把历史胜率悄悄改写一遍（这正是我差点犯的错：第一次跑 --merge
# 时它们已经被按 +5% 记成 win 了）。
TARGET_PCT_PREV = 3.5    # 换参前的止盈（只给台账旧条目结算用，别删）
HOLD_DAYS_PREV = 2       # 换参前的持有上限（同上）

# ---------------------------------------------------------------- 口径（2026-10-26 定稿）
# 出处：`chen_study.py` / `rule_final_check.py`，全市场 829 笔基线样本，
# 训练 2024-01~2025-12 / 样本外 2026-01~ 分开验（冻结参数不动，只改这三处）。
#
#   口径                                        全样本      训练      样本外    胜率
#   原法（wash_low 止损 / +3.5% / 持 2 日）    +0.701%   +0.671%   +0.770%   59.6%
#   本口径（zt_low 止损 / +5% / 持 10 日 / 本过滤） +3.054%   +3.090%   +2.972%   83.9%
#
# 三处改动各自单独测过的效果（不是可加关系，每一项都是和原法比）：
#   · 止损锚 wash_low → zt_low（T 日最低价）：59.6% → 67.6%，+0.701% → +1.010%
#     原因：洗盘段最低价常常只比买价低 2~3%，等于把止损挂在噪声里，日内正常
#     波动就被打掉；T 日最低价是形态的**定义边界**，破了才说明反包失败。
#   · 止盈/持有 +3.5%/2 日 → +5%/10 日：+1.446%（原法只持 2 天，走不完反包的第二段）
#   · 距 MA5 ≥ 3%（MA5_GAP_MIN）：这是最强的一条，见下。
#
# ⚠️ 反直觉但实测，别「好心」改回去：距 MA5 **越远越好**。
#      0~2%：45.4% 胜率 / +0.018%      2~5%：过渡段
#      >5% ：68.6% 胜率 / +1.078%
#   直觉以为「刚站上 MA5 就买最便宜」，实测相反——刚贴住 MA5 说明反包还没被
#   资金确认，只是止跌；真正要走的那一波，确认日收盘就已经明显脱离 MA5 了。
#
# 密度：1.50 只/交易日（602 笔 / 341 个信号日），池子不会空转也不会灌水。
MA5_GAP_MIN = 3.0        # 确认日收盘相对 MA5 的最小溢价（%）
# R100z222（审核意见 §19.4）：次新股硬门槛。Tencent 主源回 70 根、新浪备用回 40 根，
# 上市不足 35 个交易日的票两根源都会短于这个数——用它判「次新」比任何日期字段都可靠
# （get_kline 只回 K 线，拿不到上市日）。他明确不做新股：首板要「横盘吸筹」之后，
# 新股没有横盘；而 calc_lbc 会把上市首日的 +44% 当成「涨停 + 1 板」。
NEW_STOCK_MIN_BARS = 35
# 审核意见 §8：止损距离 > 10% 的票单独标记（本口径持有 10 日，宽止损是可达的，
# 标记含义已从「止损形同虚设」改为「止损偏远、仓位压到底仍偏险」，见 trade_params）。
STOP_FAR_PCT = 10.0
# 已删除 LBC_MIN（连板门槛）：连板数仍由 calc_lbc 算出，只展示/排序，不拦截。
# 用户 2026-10-25：「陈学长没有要求连板≥2，首板也可以」。


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- 判定
def calc_lbc(kl, iT, th):
    """从 K 线自算 T 日连板数（含 T 日）。容忍 0.3 个点（前复权微调）。

    为什么不用涨停池的 lbc 字段：① 涨停池只回看 5 天，更早起的连板数不到；
    ② 东财兜底源（fetch_zt_pool_em）根本没有 lbc 字段，靠它会在降级时把全池误杀成「首板」。
    """
    cnt = 0
    i = iT
    while i >= 1:
        c0 = float(kl[i - 1].get("close") or 0)
        c1 = float(kl[i].get("close") or 0)
        if c0 <= 0:
            break
        if (c1 - c0) / c0 * 100 >= th - 0.3:
            cnt += 1
            i -= 1
        else:
            break
    return cnt


def _lbc_txt(lbc):
    """连板数的中文写法。首板是合法候选，写成「1板」会让人以为门槛还在。"""
    n = int(lbc or 0)
    if n <= 0:
        return "涨停"
    return "首板" if n == 1 else ("%d板" % n)


def _ma5(kl, j):
    """第 j 根（含）往前 5 根收盘均值。j < 4 时返回 0（不够 5 根不算站稳）。"""
    if j < 4:
        return 0.0
    s = 0.0
    for i in range(j - 4, j + 1):
        c = float(kl[i].get("close") or 0)
        if c <= 0:
            return 0.0
        s += c
    return s / 5.0


def _structure(code, kl, zt_iso, min_days_ago):
    """chen_stage / chen_watch 共用的前置硬结构（S1/S2/S4/S7）。

    R100z222（审核意见 §18）：原先两个函数把这套判定逐行复制了两遍，注释还写着
    「共用前置判定，避免两套口径」——那正是 24.10 那类死链的标准来源：下次只改一处
    （VOL_UP_MIN / LOW_TOL），新增的票就按老规则判，而你看不出来。
    现在只有这一份实现，两个池的差异被压缩成「min_days_ago 传 1 还是 2」和
    后面各自的收尾（确认 vs 待缩量/待企稳）。

    返回 dict（键见 return）或 None（不成立 / 窗口外）。
    """
    n = len(kl)
    # 次新股门槛（§19.4）：K 线不足 35 根 → 上市太近，他明确不做（首板要有横盘吸筹）
    if n < NEW_STOCK_MIN_BARS:
        return None
    if not re.match(r"^(60|00)\d{4}$", str(code)):
        return None
    iT = next((i for i, k in enumerate(kl) if str(k.get("day")) == zt_iso), None)
    if iT is None:
        return None

    def CL(i):
        return float(kl[i].get("close") or 0)

    def LO(i):
        return float(kl[i].get("low") or 0)

    def VO(i):
        return float(kl[i].get("volume") or 0)

    def PC(i):
        if i < 1 or CL(i - 1) <= 0:
            return 0.0
        return (CL(i) - CL(i - 1)) / CL(i - 1) * 100

    # S1 股价
    if CL(n - 1) < PRICE_MIN:
        return None
    # S2 T 日涨停
    th = CD.limit_ratio(code)
    if PC(iT) < th:
        return None
    vT = VO(iT)
    if vT <= 0:
        return None
    # S3 连板数（只算不拦：首板同样是合法候选——用户 2026-10-25 纠正）
    lbc = calc_lbc(kl, iT, th)
    days_ago = n - 1 - iT
    if days_ago < min_days_ago or days_ago > MAX_WASH_DAYS:
        return None
    # S4 T+1 断板放量
    i1 = iT + 1
    if i1 > n - 1:
        return None
    r1 = VO(i1) / vT
    if PC(i1) >= th or r1 < VOL_UP_MIN:
        return None
    # S7 不破 T 日低点（先查，破了直接失效，不再进观察池）
    lowT = LO(iT)
    wash_low = min([LO(i) for i in range(i1, n)] or [0])
    if lowT > 0 and min(CL(i) for i in range(i1, n)) < lowT * LOW_TOL:
        return None

    # 软条件用到的量能基准（S9：T 日量 / 前 5 日均量）
    v5 = [VO(i) for i in range(max(0, iT - 5), iT)]
    avg5 = (sum(v5) / len(v5)) if v5 else 0.0
    return {
        "n": n, "iT": iT, "i1": i1, "i2": iT + 2, "lbc": lbc, "th": th,
        "r1": r1, "drop1": PC(i1), "days_ago": days_ago, "wash_low": wash_low,
        # 止损锚用 T 日最低价。它固定在 T 日、不随后续走势移动，
        # 是「反包失败」的定义边界；wash_low 会随着确认后的K线滚滚下移，
        # 同一笔交易昨天和今天算出的止损价不一样，台账没法复现。
        "zt_low": lowT,
        "shrink_t": (vT / avg5) if avg5 > 0 else None,
        "zt_md": "%s/%s" % (zt_iso[5:7], zt_iso[8:10]),
        "CL": CL, "VO": VO,
    }


def chen_stage(code, kl, zt_iso):
    """单只候选的陈学长口径判定（确认池）。

    返回 dict：
      {pool:'confirmed', tier:'strict'|'loose', stage, form, metrics,
       days_ago, stop_price, confirm_idx, since_confirm}
    或 None（不成立 / 窗口外）。

    R100z222（审核意见 §1/§2/§4）：**确认日不再等于「T+2 那一天」**。
    S5（缩量）钉死在 T+2，但 S8（站稳 MA5）原来只看最后一根——于是
      · 一只票在 T+2 确认后，只要形态不破就会连着挂 T+3、T+4，页面把「确认后第 3 天」
        当今天的买入信号，台账却只记第一天（§2）；
      · form 里写「N日缩量」，而 N 实际是「涨停日距今几天」，且最后一根的量从没验过（§4）。
    现在从 T+2 起逐根找**第一根站上 MA5 的交易日**，那就是它真实的确认日；
    `since_confirm = 0` 表示今天就是确认日，>0 表示这只票是几天前该买的。
    判定只用 confirm_idx 及其之前的数据，不含任何前视。
    """
    st = _structure(code, kl, zt_iso, 2)
    if not st:
        return None
    n, i2, VO, CL = st["n"], st["i2"], st["VO"], st["CL"]
    if i2 > n - 1:
        return None                  # 还没到 T+2 = 待缩量（走 chen_watch）
    if not (0 < VO(i2) < VO(st["i1"])):
        return None                  # S5 缩量未过

    # S8 站稳 MA5 → 从 T+2 起第一根站上的交易日 = 确认日
    confirm_i = None
    for j in range(i2, n):
        m = _ma5(kl, j)
        if m > 0 and CL(j) >= m * MA5_TOL:
            confirm_i = j
            break
    if confirm_i is None:
        return None

    # S8b：确认日必须**明显脱离** MA5，不能只是贴住。
    # 用 confirm_i 当天的收盘与 MA5 比，只用当天及之前的数据，无前视。
    # 这条把期望从 +0.70% 拉到 +3.05%（详见文件头 R100z233 表的 0~2% vs >5% 对比）。
    _m5c = _ma5(kl, confirm_i)
    gap_ma5 = ((CL(confirm_i) / _m5c - 1) * 100) if _m5c > 0 else -99.0
    if gap_ma5 < MA5_GAP_MIN:
        return None

    # ---- 软条件（只影响 strict / loose 分档）
    shrink_t = st["shrink_t"]
    drop1 = st["drop1"]
    soft_ok = (shrink_t is not None and shrink_t <= SHRINK_MAX and drop1 <= BIGDROP_MAX)
    tier = "strict" if soft_ok else "loose"

    lbc, r1, wash_low = st["lbc"], st["r1"], st["wash_low"]
    # 止损锚 = T 日最低价（固定）；取不到才退回洗盘段最低价
    stop_anchor = st.get("zt_low") or wash_low
    lag = confirm_i - st["i1"]              # 断板日 → 确认日 间隔（交易日）
    since = n - 1 - confirm_i               # 确认后已过几个交易日（0 = 今天确认）
    lag_txt = "次日缩量确认" if lag == 1 else ("%d日缩量确认" % lag)
    form = "%s %s涨停→次%.1f倍量收%+.1f%%→%s·站稳MA5" % (
        st["zt_md"], _lbc_txt(lbc), r1, drop1, lag_txt)
    if shrink_t is not None:
        form += "（T日量%.2f倍均量）" % shrink_t
    form += "（较MA5+%.1f%%）" % gap_ma5
    if since > 0:
        # 老票必须自曝：否则读者会把它当今天的买入信号（§2 的核心问题）
        form += "（确认后第%d日·非当日新信号）" % (since + 1)

    metrics = {
        "lbc": lbc,
        "volRatio": round(r1, 2),
        "shrinkT": round(shrink_t, 2) if shrink_t is not None else None,
        "drop1": round(drop1, 2),
        # §4：washDays 的字面意思被误读成「缩量了几天」。改成两个语义明确的字段：
        "confirmLag": lag,          # 断板日到确认日隔了几个交易日
        "sinceConfirm": since,      # 确认后又过了几个交易日（0 = 今天确认）
        "washDays": since,          # 兼容旧读者，值同 sinceConfirm（确认后第 N 天）
        "stopPrice": round(stop_anchor, 2) if stop_anchor > 0 else None,
        "tailRisk": r1 > 8.0,
    }
    return {"pool": "confirmed", "tier": tier, "stage": "缩量确认", "form": form,
            "metrics": metrics, "days_ago": st["days_ago"], "stop_price": stop_anchor,
            "confirm_day": str(kl[confirm_i].get("day") or ""),
            "confirm_idx": confirm_i, "since_confirm": since}


def chen_watch(code, kl, zt_iso):
    """观察池：硬结构已走到「断板放量 + 未破位」，只差缩量/企稳。

    R100z222：前置判定改调 `_structure`（原先两个函数是逐行复制，§18）。
    """
    st = _structure(code, kl, zt_iso, 1)
    if not st:
        return None
    n, i1, CL = st["n"], st["i1"], st["CL"]
    lbc, r1, wash_low = st["lbc"], st["r1"], st["wash_low"]
    stop_anchor = st.get("zt_low") or wash_low        # 与确认池同锚（T 日最低价）
    if i1 == n - 1:
        stage, form = "待缩量", "%s %s→次%.1f倍量断板·待缩量" % (st["zt_md"], _lbc_txt(lbc), r1)
    else:
        m = _ma5(kl, n - 1)
        if m > 0 and CL(n - 1) < m * MA5_TOL:
            stage, form = "待企稳", "%s %s→次%.1f倍量断板→已缩量·待收复MA5" % (
                st["zt_md"], _lbc_txt(lbc), r1)
        else:
            return None
    return {"pool": "watch", "tier": None, "stage": stage, "form": form,
            "metrics": {"lbc": lbc, "volRatio": round(r1, 2),
                        "stopPrice": round(stop_anchor, 2) if stop_anchor > 0 else None},
            "days_ago": st["days_ago"], "stop_price": stop_anchor,
            "confirm_day": "", "confirm_idx": None, "since_confirm": None}


# ---------------------------------------------------------------- 交易参数
def trade_params(ref_close, stop_price):
    """买点/卖点/止损/仓位 —— 全部由冻结口径推出，不含任何主观调速。

    ref_close：确认日收盘价（买价基准用它的次日开盘，这里先给参考值）。
    止损：T 日最低价（原为洗盘段最低价，实测洗盘段更窄、更容易被插针打掉）；缺失时用 -3% 兜底（不编更宽的数）。
    仓位：RISK_BUDGET / 止损距离，上限 30%（三条线不可能全仓一只）。

    R100z222（审核意见 §8）：`stopFar` / `riskBreach` 两个标记位。
    实测池里出现过止损距 26.4% 的票。持有窗口从 2 日拉到 10 日之后，这条标记的
    **含义变了**：原来 2 天窗口里跌 26% 几乎不可能，标记等于「裸持到日收盘平」；
    现在 10 天窗口里 10%+ 的止损距离是**正常可达**的，所以它不再是「止损形同虚设」，
    而是「止损很远、仓位已被压到下限，单笔风险仍然偏大」——文案按新含义重写，
    不留一句被参数改动架空的话。**不剔除**（那会改选股，属于策略决策），只在卡上标出来：
      · stopFar     = 止损距离 > STOP_FAR_PCT（10%），标记「止损偏远、仓位已压到底」
      · riskBreach  = 5% 的仓位下限把 1.5% 的风险预算顶破了（dist > 30% 时才可能）
    """
    buy = float(ref_close or 0)
    stop = float(stop_price or 0)
    if buy <= 0:
        return {}
    if stop <= 0 or stop >= buy:
        stop = round(buy * (1 + STOP_PCT_FALLBACK / 100), 2)
    # 最小止损距离 2%：A 股日线正常波动就有 2–3%，止损位若离买价不到 2%
    # （洗盘最后一天的低点常常贴着收盘），任何噪音都会把仓位打掉——那不是策略失败，
    # 是止损设在噪音里。收紧到 2% 后仓位自然降到 ≤75%/上限 30%，风险预算仍守 1.5%。
    if (buy - stop) / buy * 100 < 2.0:
        stop = round(buy * 0.98, 2)
    dist = (buy - stop) / buy * 100
    pos = min(30, max(5, round(RISK_BUDGET / dist * 100))) if dist > 0.2 else 5
    risk = round(pos * dist / 100, 2)
    # 只有「5% 的仓位下限」真的把预算顶破才算 breach：dist=26% 时 5.77→6 只是取整（1.56%），
    # 不值得报警；dist≥35% 才会变成 1.75% 以上，那才是预算失效。
    breach = risk > RISK_BUDGET * 1.15
    note = "单笔最大亏总仓 %.1f%%：仓位 %d%% × 止损 %.1f%%" % (
        RISK_BUDGET, pos, dist)
    if breach:
        note += "｜⚠️ 止损 %.1f%% 过宽，%d%% 的仓位下限已顶破预算（实际单笔亏 %.2f%%）" % (
            dist, pos, risk)
    if dist > STOP_FAR_PCT:
        note += "｜⚠️ 止损距离 >%.0f%%，仓位已压到下限仍需 %.2f%% 单笔风险，别加仓" % (
            STOP_FAR_PCT, risk)
    return {
        "refClose": round(buy, 2),
        "buyRule": "确认日次日开盘买入（等洗盘缩量走完再进，不追涨）",
        "targetPct": TARGET_PCT,
        "targetPrice": round(buy * (1 + TARGET_PCT / 100), 2),
        "stopPrice": round(stop, 2),
        "stopPct": round(dist, 2),
        "holdDays": HOLD_DAYS,
        "posPct": pos,
        "riskPct": risk,
        "stopFar": dist > STOP_FAR_PCT,
        "riskBreach": breach,
        "riskNote": note,
    }


# ---------------------------------------------------------------- 候选收集
def collect_candidates(dates, limit):
    """收集候选：code -> {name, hybk, zt:[YYYY-MM-DD...]}。返回 (cand, failed_dates)。"""
    cand, failed = {}, []
    for d in dates:
        pool = CD.fetch_zt_pool(d)
        if pool is None:
            failed.append(d)
            print("[warn] 涨停池 %s 拉取失败（限频/网络），该日候选缺失" % d, file=sys.stderr)
            continue
        for p in pool or []:
            code = str(p.get("c") or "")
            if not re.fullmatch(r"\d{6}", code):
                continue
            rec = cand.setdefault(code, {"name": p.get("n") or code,
                                         "hybk": p.get("hybk") or "", "zt": []})
            if d not in rec["zt"]:
                rec["zt"].append(d)
        time.sleep(0.4)
    return cand, failed


def eval_candidate(code, rec, kl):
    """对候选的每个涨停日做判定，确认池优先于观察池。

    不带漏斗计数器（用户要求删漏斗）：每个涨停日都按同一口径判，
    取「最好的那个涨停日」作为该票的阶段——连板票在窗口内可能有多个涨停日，
    它究竟从哪一波起算，取决于哪个涨停日走到了最后一步。
    """
    best = None
    for d in sorted(rec["zt"], reverse=True):
        iso = "%s-%s-%s" % (d[:4], d[4:6], d[6:])
        st = None
        try:
            st = chen_stage(code, kl, iso)
        except Exception:
            st = None
        if not st:
            try:
                st = chen_watch(code, kl, iso)
            except Exception:
                st = None
        if not st:
            continue
        if best is None or (st["pool"] == "confirmed" and best["stage_info"]["pool"] != "confirmed"):
            best = {"code": code, "name": rec["name"], "hybk": rec["hybk"],
                    "ztDate": iso, "stage_info": st, "kl": kl}
    return best


# ---------------------------------------------------------------- 留痕与结算
def load_ledger():
    if not os.path.exists(LEDGER):
        return {"entries": [], "updatedAt": ""}
    try:
        d = json.load(open(LEDGER, encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError("ledger 不是 dict")
        d.setdefault("entries", [])
        # R100z221：原先这里每次强制写 d["version"]=RULE_VERSION（旧写法只 setdefault，
        # 文件已存在就永远停在旧版，台账头顶与条目会对不上）。现在只有一个口径、没有版本号，
        # 这个字段连同条目级的 ruleVersion 一起删除；读到旧台账时顺手清掉，避免僵尸字段
        # 又被人当成分组维度（无读者字段＝僵尸字段，24.45 的教训）。
        d.pop("version", None)
        # R100z222（审核意见 §1）：旧条目只有 entryDate，读进来时就地补齐两个新字段——
        # confirmDate 沿用原 entryDate（不改变已结算样本的买价，历史结果可复现），
        # firstSeenDate 同样沿用。这样下游只读新字段，不必到处写 `or entryDate`。
        for x in d.get("entries") or []:
            if not isinstance(x, dict):
                continue
            if not x.get("confirmDate"):
                x["confirmDate"] = x.get("entryDate") or ""
            if not x.get("firstSeenDate"):
                x["firstSeenDate"] = x.get("entryDate") or x.get("confirmDate") or ""
            # R100z233b：旧条目（记在 2026-10-26 换口径之前）没有 tgtPct/holdDays/ruleFrom。
            # 按「开仓时在用的参数」补齐 = 旧值，并把 ruleFrom 标成换版前那天——
            # 这样它们既不会被新参数重新结算，也不会混进新口径的胜率表。
            if not x.get("tgtPct") or not x.get("holdDays"):
                x.setdefault("tgtPct", TARGET_PCT_PREV)
                x.setdefault("holdDays", HOLD_DAYS_PREV)
            if not x.get("ruleFrom"):
                x["ruleFrom"] = "legacy"
        return d
    except Exception as e:
        print("[warn] 留痕台账读取失败（%s），按空台账继续" % e, file=sys.stderr)
        return {"entries": [], "updatedAt": ""}


def settle_one(e, kl):
    """统一结算口径（两个池、两套指标共用，不然胜率不可比）。

    买价 = 确认日**次一交易日开盘**（唯一可成交的时点；用确认日收盘价属偷看未来）。
    窗口 = 次日起 `holdDays` 个交易日（**取条目自带的参数**，缺省才用当前常量）。
      · 止盈：**盘中最高** ≥ +tgtPct% → 赢（可挂单，按 +tgtPct% 计）
      · 止损：**收盘**跌破止损价 → 亏（按当日收盘价计）
        ⚠️ 为什么止损看收盘、止盈看盘中：这是实盘可执行的不对称——
        止盈能挂单，止损只有收盘才确认破位（两个池的形态判定也都是收盘口径，
        盘中最低价破位就出局等于用噪音选股，会把两边胜率一起压低）。
      · 同一日先判止损（保守）
      · 都没触 → 窗口末收盘平（expired，按实际收益计）
      · 开盘价已 ≤ 止损 → **不进场**（notExecuted，不计入胜负）

    R100z222（审核意见 §9）：实测 2,479 个事件里 226 笔（9.1%）开盘价已在止损位之下。
    真实操作里「跌破止损位不接」是纪律，不是可选项；原来这些被记成正常交易，
    等于把「你根本不会下的单」也算进胜率。改成 notExecuted 并单独计数（skip），
    分母里不含它们——它们既不是赢也不是亏，是没做。

    R100z233b：止盈线/持有窗口改为**读条目自带字段** `tgtPct` / `holdDays`。
    理由：参数一改，所有「已开仓未结算」的样本会被新参数重新解释，历史胜率在换版当天
    被静默改写。冻结在条目上才是可复现的（止损价 stopPrice 本来就是这么做的）。
    """
    tgt_pct = float(e.get("tgtPct") or TARGET_PCT)
    hold_days = int(e.get("holdDays") or HOLD_DAYS)
    days = [str(k.get("day")) for k in kl]
    # R100z222（审核意见 §1）：结算基准改成 confirmDate（数据日）——买入永远是它的
    # 下一根开盘，语义与「看到信号之后才买」一致；entryDate 只作旧台账的兼容回退。
    key = str(e.get("confirmDate") or e.get("entryDate") or "")
    if not key:
        return None
    # R100z219：原来是 days.index(entryDate) 精确匹配。踩过一次——脚本在周日 23:5x 跑，
    # entryDate 写成「周日」这个非交易日，index() 抛 ValueError → 返回 None →
    # 那 13 条样本永远不会结算，而且**不报错**（胜率表永远 0 样本、看着像"还没到期"）。
    # 改成「≤ entryDate 的最后一根 K 线」：确认日恰好是交易日时行为与 index() 完全一致；
    # 落在周末/节假日时就近回落到上一个交易日，买价仍是它之后那根的开盘——
    # 这才是实盘能成交的时点（周五收盘确认、周一开盘买）。
    idx = -1
    for i, d in enumerate(days):
        if d <= key:
            idx = i
    if idx < 0:
        return None
    if idx + 1 > len(kl) - 1:
        return None
    buy = float(kl[idx + 1].get("open") or 0)
    if buy <= 0:
        return None
    stop = float(e.get("stopPrice") or 0)
    if stop > 0 and buy <= stop:
        # §9：开盘就在止损位之下 → 纪律上不进场。记为 notExecuted，不进胜负分母。
        return {"status": "settled", "result": "notExecuted", "exitDay": days[idx + 1],
                "exitPrice": round(buy, 2), "buyPrice": round(buy, 2), "retPct": 0.0,
                "note": "开盘价 %.2f 已跌破止损位 %.2f，按纪律不进场" % (buy, stop)}
    tgt = buy * (1 + tgt_pct / 100)
    last = min(idx + hold_days, len(kl) - 1)
    for j in range(idx + 1, last + 1):
        hi = float(kl[j].get("high") or 0)
        cl = float(kl[j].get("close") or 0)
        hit_stop = stop > 0 and cl <= stop
        hit_tgt = hi >= tgt
        if hit_stop:
            return {"status": "settled", "result": "loss", "exitDay": days[j],
                    "exitPrice": round(cl, 2), "buyPrice": round(buy, 2),
                    "retPct": round((cl - buy) / buy * 100, 2),
                    "note": "收盘跌破止损位"
                            + ("（同日也触过 +%.1f%%，保守算亏）" % tgt_pct if hit_tgt else "")}
        if hit_tgt:
            return {"status": "settled", "result": "win", "exitDay": days[j],
                    "exitPrice": round(tgt, 2), "buyPrice": round(buy, 2),
                    "retPct": round(tgt_pct, 2),
                    "note": "盘中触 +%.1f%% 当天兑现" % tgt_pct}
    if len(kl) - 1 >= idx + hold_days:
        c = float(kl[idx + hold_days].get("close") or 0)
        return {"status": "settled", "result": "expired", "exitDay": days[idx + hold_days],
                "exitPrice": round(c, 2), "buyPrice": round(buy, 2),
                "retPct": round((c - buy) / buy * 100, 2),
                "note": "持仓 %d 日未触目标也未破止损，收盘平" % hold_days}
    return None


def pool_stats(entries):
    """胜率统计。`n = win + loss + expired`，`skip` 是「开盘破止损没进场」的条数。

    R100z222（审核意见 §11）：notExecuted 单列 `skip`，**不进 n**——它们不是输赢，
    是没做。混进分母会把胜率稀释成「包含你根本不会下的单」的数。

    R100z233b：只统计 `ruleFrom == RULE_FROZEN_AT`（当前口径）的样本，换口径前的样本
    单独计数进 `legacyN`。否则每改一次参数，历史胜率表就在页面上自己改写一次——
    这不是「统计」，是给记分牌重新判分。
    """
    allin = list(entries or [])
    legacy = [e for e in allin if (e.get("ruleFrom") or "legacy") != RULE_FROZEN_AT]
    entries = [e for e in allin if (e.get("ruleFrom") or "legacy") == RULE_FROZEN_AT]
    settled = [e for e in entries if (e.get("result") or {}).get("status") == "settled"]
    skip = sum(1 for e in settled if (e["result"] or {}).get("result") == "notExecuted")
    # §7：止损位取不到（stopPrice ≤ 0）的条目**不进同一张表**——没有止损时只有止盈和到期，
    # 亏的那一档被系统性抹掉，胜率天然偏高；而缺止损的恰恰多是对照组。
    nostop = sum(1 for e in settled
                 if (e["result"] or {}).get("result") != "notExecuted"
                 and not float(e.get("stopPrice") or 0))
    done = [e for e in settled
            if (e["result"] or {}).get("result") != "notExecuted"
            and float(e.get("stopPrice") or 0)]
    if not done:
        return {"n": 0, "win": 0, "loss": 0, "expired": 0, "skip": skip,
                "noStop": nostop, "legacyN": len(legacy), "winRate": None,
                "avgRet": None, "avgWin": None, "avgLoss": None, "odds": None}
    rets = [float((e["result"] or {}).get("retPct") or 0) for e in done]
    win = sum(1 for e in done if (e["result"] or {}).get("result") == "win")
    loss = sum(1 for e in done if (e["result"] or {}).get("result") == "loss")
    exp = len(done) - win - loss
    wl = [r for r in rets if r > 0]
    ll = [r for r in rets if r <= 0]
    avg_win = round(sum(wl) / len(wl), 2) if wl else None
    avg_loss = round(sum(ll) / len(ll), 2) if ll else None
    odds = round(abs(avg_win / avg_loss), 2) if (avg_win and avg_loss) else None
    return {
        "n": len(done), "win": win, "loss": loss, "expired": exp, "skip": skip,
        "noStop": nostop, "legacyN": len(legacy),
        "winRate": round(win / len(done) * 100, 1),
        "avgRet": round(sum(rets) / len(rets), 2),
        "avgWin": avg_win,
        "avgLoss": avg_loss,
        "odds": odds,
    }


def settle_ledger(ledger, kl_cache):
    """结算所有 open 条目。kl_cache: code -> kline（并发预先抓好）。"""
    n_settled = 0
    for e in ledger.get("entries") or []:
        if (e.get("result") or {}).get("status") == "settled":
            continue
        kl = kl_cache.get(str(e.get("code")))
        if not kl:
            continue
        r = settle_one(e, kl)
        if r:
            e["result"] = r
            n_settled += 1
    return n_settled


def record_entries(ledger, pool_key, entries, today, tier=None, cross=None):
    """把当日池内标的写入台账。同一 (pool, code, confirmDate) 只记一次。

    R100z222（审核意见 §1/§10）：字段拆成语义明确的两个日期 ——
      · `confirmDate`  结算基准（= 数据日；买入永远是它的下一根开盘）
      · `firstSeenDate` 页面展示用（这只票第一次出现在池子里的那天）
    原来只有一个 `entryDate`，同时承担「入池日」和「确认日」两个含义，
    结果结算买在了确认信号出现**之前**那根 K 线的开盘（实测把胜率虚高约 12 个点）。
    `entryDate` 保留 = confirmDate，只为兼容历史台账与还没更新的前端。
    """
    have = {(x.get("pool"), str(x.get("code")), x.get("confirmDate") or x.get("entryDate"))
            for x in ledger.get("entries") or []}
    # R100z222：原注释写「已在池但结果未结算的旧条目，把 stopPrice 更新为最新」，
    # 但下面只有 continue、没有任何更新动作（审核意见 §10）。实际行为是**止损价在
    # 入池日冻结**——这是对的（回测必须可复现），所以把注释改成事实，别再让人以为它会动。
    live = {str(x.get("code")) for x in ledger.get("entries") or []
            if (x.get("result") or {}).get("status") != "settled"}
    added = 0
    for e in entries:
        code = str(e.get("code") or "")
        if not code:
            continue
        cd = e.get("confirmDate") or today
        if (pool_key, code, cd) in have:
            continue
        if code in live:
            continue          # 该票已有一条未结算记录，不重复记（同一波形态只算一次）
        ledger["entries"].append({
            "pool": pool_key, "tier": tier, "code": code,
            # R100z221：条目级 ruleVersion 已删——只有一套口径，这个字段恒为同一个值，
            # 却会诱使后人按它分组做「新旧对比」（样本各只剩几条，比出来的差异全是噪音）。
            "name": e.get("name") or "",
            "confirmDate": cd,
            "firstSeenDate": e.get("firstSeenDate") or cd,
            "entryDate": cd,
            # §19.1：记账当时的两池交集标签（both / chen / db）——事后按今天的池子反推
            # 会把历史样本标错，标签必须一次定死。批次内有混合时由条目自带的 `_cross` 覆盖。
            "cross": e.get("_cross") or cross,
            "ztDate": e.get("ztDate") or "",
            # 止损价必须与页面展示的、能成交的那个一致（trade.stopPrice 含 2% 最小距离），
            # 否则会出现「页面写 30、回测按 31 结算」的静默口径分叉。
            "stopPrice": (e.get("trade") or {}).get("stopPrice")
                         or (e.get("metrics") or {}).get("stopPrice") or e.get("stopPrice"),
            # R100z233b：止盈线、持有窗口、口径起始日一起冻结在条目上。
            # 只冻结 stopPrice 是不够的——2026-10-26 改口径时，9 条 10-25 开仓的样本
            # 就是被新的 +5%/10 日重新结算成了 win(5.0)，胜率表在换版当天自己变好看。
            # 冻结之后 pool_stats 只统计 ruleFrom == 当前口径 的样本，旧样本单列 legacyN。
            "ruleFrom": RULE_FROZEN_AT,
            "tgtPct": TARGET_PCT,
            "holdDays": HOLD_DAYS,
            "result": None,
        })
        added += 1
    return added


# ---------------------------------------------------------------- 元信息装配
def build_entries(hits, D, tier_filter=None):
    """把命中记录装配成前端要的卡片结构（含 K 线/板块/情绪/交易参数）。

    R100z222（审核意见 §17）：**调用方只调一次**。原先 main() 里对 strict / loose / watch
    各调一遍，而这里每次都会跑 fetch_ths_board_map / fetch_board_map / _stock_clist_agg /
    fetch_notices_batch / tag_entries + 每只票一次 fetch_ths_board_pct_on —— 同一天的同三份
    数据被拉三遍，公告拉三遍，打标跑三遍。16 只票时看不出来，断板池那种 50+ 只的规模
    就会顶到 300s 预算。现在一次装配全部命中，调用方按 tier / pool 切三份。
    `tier_filter` 保留只为兼容外部调用，正常路径不传。
    """
    ths_map = CD.fetch_ths_board_map()
    ths_names = set(ths_map.keys())
    board_map = CD.fetch_board_map()
    ind_agg = CD._stock_clist_agg()
    entries = []
    for p in hits:
        si = p["stage_info"]
        if tier_filter and si.get("tier") != tier_filter:
            continue
        kl = p["kl"][-60:]
        bars = []
        for b in kl:
            try:
                bars.append([str(b.get("day")),
                             round(float(b.get("open") or 0), 2),
                             round(float(b.get("close") or 0), 2),
                             round(float(b.get("high") or 0), 2),
                             round(float(b.get("low") or 0), 2),
                             int(float(b.get("volume") or 0))])
            except Exception:
                continue
        pct = 0.0
        if len(kl) >= 2 and float(kl[-2].get("close") or 0) > 0:
            pct = round((float(kl[-1].get("close") or 0) - float(kl[-2].get("close") or 0))
                        / float(kl[-2].get("close")) * 100, 2)
        ths_name = CD.match_ths_industry(p["hybk"], ths_names)
        # 截断护栏（与 check_duanban.build_module 同口径，R100z170 方向）
        if ths_name and p["hybk"] and ths_name.startswith(p["hybk"]) and ths_name != p["hybk"]:
            ths_name = None
        if ths_name:
            bpt = round(ths_map[ths_name]["pct"], 2)
            bk_code = ths_map[ths_name]["code"]
            bpz = CD.fetch_ths_board_pct_on(bk_code, p["ztDate"])
            sector = ths_name
        else:
            ind_name = CD._industry_of(p["hybk"], ind_agg)
            if ind_name:
                bpt = round(ind_agg[ind_name]["pct"], 2)
                bk_code, bpz = None, None
            else:
                bk_code, bpt = CD.resolve_board(p["hybk"], board_map)
                bpz = CD.fetch_board_pct_on(bk_code, p["ztDate"]) if bk_code else None
            sector = p["hybk"] or "其他"
        ref_close = float(kl[-1].get("close") or 0) if kl else 0.0
        tp = trade_params(ref_close, si.get("stop_price"))
        entries.append({
            "code": p["code"], "name": p["name"], "sector": sector,
            "ztDate": p["ztDate"], "stage": si["stage"], "form": si["form"],
            "tier": si.get("tier"), "metrics": si.get("metrics") or {},
            # R100z222：两个日期由判定层带回（观察池没有确认日，留空由 main 兜底）。
            # 页面「新入池」高亮读 firstSeenDate，台账结算读 confirmDate。
            "confirmDate": si.get("confirm_day") or "",
            "firstSeenDate": "",
            "pct": pct, "boardPctToday": bpt, "boardPctZt": bpz, "bkCode": bk_code,
            "probability": None, "probNote": "",
            "trade": tp, "kline": bars,
        })
    # 公告 + 情绪打标（复用断板模块的同一套，不改它的行为）
    try:
        notices = CD.fetch_notices_batch([e["code"] for e in entries])
        for e in entries:
            e["notices"] = notices.get(str(e["code"])) or []
    except Exception as ex:
        print("[warn] 公告抓取失败（不影响主流程）: %s" % ex, file=sys.stderr)
    try:
        entries = CD.tag_entries(entries, D)
    except Exception as ex:
        print("[warn] 情绪打标失败（不影响主流程）: %s" % ex, file=sys.stderr)
        for e in entries:
            e.setdefault("sentiment", "neutral")
            e.setdefault("bullRefs", [])
            e.setdefault("bearRefs", [])
            e.setdefault("story", "")
    for e in entries:
        e.pop("notices", None)   # 中间字段，不落 data.js
    return entries


def build_compare(chen_codes, db_codes, ledger):
    """当日池差异 + 历史胜率对比。

    R100z222（审核意见 §14）：`chen_codes` / `db_codes` 由调用方统一传**确认池**，
    不再一边含观察池、一边不含——那种不对称会把「同时在陈学长观察池和断板确认池里」的票
    错标成「只有断板选中」，who-picked-who 这张图就失真了。

    §19.1：除了按 pool 分组，再按 `cross`（两池都选中 / 只有陈学长 / 只有断板）分组。
    这才是「谁更准」真正要回答的问题，而 `cross` 在**记账当时**就打好了标签
    （见 record_entries 的 cross 参数），所以历史样本不会因为今天的池子变了而漂移。
    """
    chen_s, db_s = set(chen_codes), set(db_codes)
    ents = ledger.get("entries") or []
    groups, cross = {}, {}
    for e in ents:
        groups.setdefault(e.get("pool") or "?", []).append(e)
        if e.get("cross"):
            cross.setdefault(e["cross"], []).append(e)
    stats = {k: pool_stats(v) for k, v in groups.items()}
    for k, v in cross.items():
        stats["cross_" + k] = pool_stats(v)
    return {
        "today": {
            "both": sorted(chen_s & db_s),
            "chenOnly": sorted(chen_s - db_s),
            "duanbanOnly": sorted(db_s - chen_s),
        },
        "stats": stats,
        "ledgerN": len(ents),
        # R100z233b：换口径前的样本条数（页面据此说明「它们单独结算、不进上表」）。
        # 放在这里而不是让前端累加各行 stats[].legacyN：同一只票既在某池行、又在交集行，
        # 前端累加会重复计数。
        "legacyN": sum(1 for e in ents
                       if (e.get("ruleFrom") or "legacy") != RULE_FROZEN_AT),
        "settledN": sum(1 for e in ents if (e.get("result") or {}).get("status") == "settled"),
    }


# ---------------------------------------------------------------- 主流程
def build_module(args):
    dates = CD.recent_trade_dates(args.days)
    cand, failed_dates = collect_candidates(dates, args.limit)
    if not cand:
        # 抓空必须显式说出来（项目铁律：抓不到 ≠ 当天没有标的）。
        # 原来这条信息只在漏斗卡片底部显示，漏斗删掉后改走 stderr，避免静默降级。
        if failed_dates:
            print("[warn] %d 个交易日的涨停池没抓到（%s）—— 保留既有 chenPool"
                  % (len(failed_dates), "、".join(failed_dates)), file=sys.stderr)
        return None

    cands = sorted(cand.items(), key=lambda kv: max(kv[1]["zt"]), reverse=True)[:args.limit]
    t0 = time.time()
    try:
        deadline = float(os.environ.get("CHEN_DEADLINE", "300"))
    except ValueError:
        deadline = 300.0

    hits, failed_kl = [], []
    lock = threading.Lock()

    def worker(item):
        code, rec = item
        sym = CD.sina_symbol(code)
        if not sym:
            return code, rec, None
        return code, rec, CD.get_kline(code, sym)

    done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(worker, it) for it in cands]
        for fut in as_completed(futs):
            code, rec, kl = fut.result()
            done += 1
            if not kl:
                with lock:
                    failed_kl.append(code)
            else:
                hit = eval_candidate(code, rec, kl)
                if hit:
                    with lock:
                        hits.append(hit)
            if done % 80 == 0:
                print("[info] 已判定 %d/%d 只，用时 %.0fs" % (done, len(cands), time.time() - t0),
                      file=sys.stderr)
            if time.time() - t0 > deadline:
                for f in futs:
                    f.cancel()
                print("[warn] 达到时间预算 %.0fs，取消剩余候选" % deadline, file=sys.stderr)
                break

    # 二轮重试（预算充足时）
    if failed_kl and time.time() - t0 < deadline * 0.7:
        time.sleep(10)
        for code in list(failed_kl):
            if time.time() - t0 > deadline:
                break
            sym = CD.sina_symbol(code)
            if not sym:
                continue
            kl = CD.get_kline(code, sym)
            if not kl:
                continue
            # R100z222（审核意见 §5）：重试成功必须把它从 failed_kl 里删掉，
            # 否则「抓到了」和「没抓到」同时出现在 failedCodes 里，前端会报一条假告警。
            with lock:
                if code in failed_kl:
                    failed_kl.remove(code)
            rec = cand.get(code) or {}
            hit = eval_candidate(code, rec, kl)
            if hit:
                with lock:
                    hits.append(hit)
            time.sleep(0.4)

    hits = [h for h in hits if str(h["code"]).startswith(("60", "00"))]
    print("[info] 候选 %d 只 → 命中 %d 只（用时 %.0fs）"
          % (len(cands), len(hits), time.time() - t0), file=sys.stderr)

    # 数据日 = 最新一根 K 线的日期，不是脚本运行时间。
    # 实测过：周日 23:5x 起跑、跨到周一 00:02，generatedAt 写成 10-26，而数据其实是 10-23 收盘的。
    # 页面上「数据日 2026-10-26」会让读者以为周一的盘已经出来（与 19:30 跨午夜那类坑同源）。
    trade_date = ""
    for h in hits:
        d = str((h.get("kl") or [{}])[-1].get("day") or "")
        if d > trade_date:
            trade_date = d
    if not trade_date and dates:
        # dates 是 YYYYMMDD 降序（check_duanban.recent_trade_dates），转成与 K 线 day 一致的 ISO
        d = dates[0]
        trade_date = "%s-%s-%s" % (d[:4], d[4:6], d[6:]) if len(d) == 8 else d

    D = {}
    try:
        D = CD.load_dashboard(args.dashboard or DATA_JS)
    except Exception as e:
        print("[warn] data.js 解析失败（%s），情绪一律按 neutral 标注" % e, file=sys.stderr)

    # §17：只装配一次，三份按 tier / pool 切——原先调三次＝三份网络抓取 + 三遍公告 + 三遍打标
    _all = build_entries(hits, D)
    strict = [e for e in _all if e.get("tier") == "strict"]
    loose = [e for e in _all if e.get("tier") == "loose"]
    watch = [e for e in _all if not e.get("tier")]

    mod = {
        "generatedAt": _now(),
        "tradeDate": trade_date,
        # R100z221：ruleVersion 已删（用户 2026-10-26：「不要有什么 v1v2，就只有一个版本」）；
        # 只留冻结日期——「参数在回测出结果之前定死」这个承诺用日期表达，不需要版本号。
        "ruleFrozenAt": RULE_FROZEN_AT,
        "note": (
            "陈学长口径（%s）：主板股价≥4元 → T日涨停（首板或连板都算，连板只作排序加分）→ "
            "T+1放量≥2倍断板（无上限）→ 未破T日低点 → T+2缩量 → 站稳MA5"
            "且确认日收盘较MA5≥%.1f%%（阈值口径见 ruleFrozenAt 冻结说明）；"
            "严格池另加「T日缩量≤1.0 + T+1跌≥3%%」两条软条件。"
            "买点=确认日次日开盘，卖点=+%.1f%%当天兑现或持仓%d日，止损=T日最低价，"
            "仓位=单笔亏总仓1.5%%。分时量能/盘口/板块角色是日线缺口，未假装实现。"
            % (RULE_FROZEN_AT, MA5_GAP_MIN, TARGET_PCT, HOLD_DAYS)
        ),
        "strict": strict,
        "loose": loose,
        "watching": watch,
        "failedDates": failed_dates,
        # R100z222（审核意见 §5）：failed_kl 原来只 print 一句就没了——一只票今天 K 线没抓到
        # 就进不了 hits，于是被失效池滚动写成「形态失效」，而页面上看不出任何异常
        # （违反「抓不到 ≠ 没有」）。现在给它出口：落盘 + 前端显式告警 + 不进失效池。
        "failedCodes": sorted(set(failed_kl)),
        "compare": {},
        "track": {},
    }
    return mod


def main():
    ap = argparse.ArgumentParser(description="陈学长断板反包独立技术指标（chenPool）")
    ap.add_argument("--days", type=int, default=7,
                    help="回看交易日数（默认 7，与 check_duanban.py 的 --days 7 对齐；"
                         "两个池的候选全集不同会让 A/B 的 both/only 统计带噪，审核意见 §16）")
    ap.add_argument("--limit", type=int, default=300, help="最多判定候选数")
    ap.add_argument("--module", action="store_true", help="输出 chenPool 模块 JSON")
    ap.add_argument("--out", default=None, help="模块 JSON 同时写入该文件")
    ap.add_argument("--merge", action="store_true", help="合并进 data.js（含留痕结算）")
    ap.add_argument("--settle-only", action="store_true", help="只结算留痕，不动池")
    ap.add_argument("--dashboard", default=None, help="data.js 路径（默认同目录）")
    args = ap.parse_args()

    ledger = load_ledger()
    today = datetime.now().strftime("%Y-%m-%d")

    # ---------- 只结算 ----------
    if args.settle_only:
        codes = sorted({str(e.get("code")) for e in ledger.get("entries") or []
                        if (e.get("result") or {}).get("status") != "settled"})
        klc = {}
        for c in codes:
            sym = CD.sina_symbol(c)
            if sym:
                kl = CD.get_kline(c, sym)
                if kl:
                    klc[c] = kl
        n = settle_ledger(ledger, klc)
        ledger["updatedAt"] = _now()
        write_json_atomic(LEDGER, ledger, label="[chen]")
        print("[settle] 结算 %d 条（open %d 条未到期）" % (n, len(codes) - n))
        return

    mod = build_module(args)

    if mod is None:
        # 抓取全败：保留既有 chenPool，绝不清空（与 check_duanban 的 R91m 防护同思路）
        print("[warn] 涨停池全源不可达，保留既有 chenPool", file=sys.stderr)
        if args.merge:
            try:
                old = CD.load_dashboard(args.dashboard or DATA_JS).get("chenPool") or {}
                if old:
                    print("[warn] 既有池：严格 %d / 放宽 %d / 观察 %d"
                          % (len(old.get("strict") or []), len(old.get("loose") or []),
                             len(old.get("watching") or [])), file=sys.stderr)
                    return
            except Exception:
                pass
        return

    # ---------- 留痕（无论是否 merge 都写台账，保证不漏样本）----------
    if not args.merge and not args.out:
        print(json.dumps(mod, ensure_ascii=False, indent=1))
        return

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(json.dumps(mod, ensure_ascii=False, indent=1) + "\n")
        print("[chen] 草稿已写入 %s" % args.out, file=sys.stderr)

    if not args.merge:
        print(json.dumps(mod, ensure_ascii=False, indent=1))
        return

    # ---------- 合并进 data.js ----------
    old = {}
    try:
        old = CD.load_dashboard(args.dashboard or DATA_JS)
    except Exception as e:
        print("[warn] 旧 data.js 读取失败（%s），按全新建池" % e, file=sys.stderr)
    old_mod = (old.get("chenPool") or {})

    # R100z222（审核意见 §1/§13）：一个日期拆成两个，页面只出现「数据日」和「确认日」。
    #   confirmDate  = 数据日（tradeDate）——结算买价永远是它的下一根 K 线开盘，
    #                  即「看到今天这张卡片、明天开盘买」，与页面承诺一致，不可能偷看未来；
    #   firstSeenDate= 这只票第一次出现在池子里的那天（沿用上一版，供「新入池」高亮）。
    # 原先只有一个 entryDate：观察池→确认池升级时它会沿用 D1（断板当天），
    # 结算就在 D2 开盘成交——而 D2 收盘才确认。实测这条把胜率虚高约 12 个点。
    _data_day = str(mod.get("tradeDate") or "") or today
    prev_seen = {}
    for pool in ("strict", "loose", "watching"):
        for e in old_mod.get(pool) or []:
            if e.get("code"):
                prev_seen[str(e["code"])] = (e.get("firstSeenDate")
                                             or e.get("entryDate") or "")
    for pool in ("strict", "loose", "watching"):
        for e in mod[pool]:
            c = str(e["code"])
            if not e.get("confirmDate"):
                e["confirmDate"] = _data_day       # 观察池没有确认日 → 用数据日占位
            e["firstSeenDate"] = prev_seen.get(c) or today
            e["entryDate"] = e["confirmDate"]      # 兼容旧前端/旧台账读取方

    # 失效池滚动
    cur_codes = {str(e["code"]) for p in ("strict", "loose", "watching") for e in mod[p]}
    # §5：K 线抓取失败的票「状态未知」，不能判形态失效——单独挑出来，不进失效池。
    _failed = set(mod.get("failedCodes") or [])
    prev_all = {}
    for pool in ("strict", "loose", "watching"):
        for e in old_mod.get(pool) or []:
            if e.get("code"):
                prev_all[str(e["code"])] = e
    new_fails = []
    for c, e in prev_all.items():
        if c in cur_codes or c in _failed:
            continue
        new_fails.append({"code": c, "name": e.get("name") or "",
                          "sector": e.get("sector") or "", "form": e.get("form") or "",
                          "reason": "形态失效（不再满足放量断板+缩量站稳结构）",
                          "entryDate": _data_day})
    mod["failPool"] = roll_fail_pool(old_mod.get("failPool") or [], new_fails, _data_day,
                                     exclude_codes=cur_codes | _failed)

    # 台账：记录当日池。
    # §19.1：先把「今天谁选中了谁」算出来，记进条目的 cross 标签——标签必须记账当时定死，
    # 事后按今天的池子反推会把历史样本标错。
    db_mod = old.get("duanban") or {}
    _db_set = {str(e.get("code")) for e in (db_mod.get("confirmed") or [])}
    for e in mod["strict"] + mod["loose"]:
        e["_cross"] = "both" if str(e.get("code")) in _db_set else "chen"
    record_entries(ledger, "chen_strict", mod["strict"], today, tier="strict")
    record_entries(ledger, "chen_loose", mod["loose"], today, tier="loose")
    # 断板反包对照：只读它的池，不写它。
    # R100z222（审核意见 §3）：判断「新入池」不能看 entryDate == 今天——entryDate 是会被沿用的
    # 入池日，这个等式几乎永远不成立（实测 40 只票的 entryDate 全在 10-19~10-22，没有一只等于
    # 断板模块的 10-25）→ 对照组 40 只全员被 continue，「谁更准」的另一半样本永远是 0。
    # 改成「台账里有没有这个 (pool, code)」——台账本身就是判据。
    known = {(x.get("pool"), str(x.get("code"))) for x in ledger.get("entries") or []}
    db_entries = []
    for e in (db_mod.get("confirmed") or []):
        c = str(e.get("code"))
        if ("duanban", c) in known:
            continue
        db_entries.append({"code": c, "name": e.get("name"),
                           "ztDate": e.get("ztDate"),
                           "confirmDate": _data_day, "firstSeenDate": _data_day,
                           "_cross": "db",
                           "metrics": {"stopPrice": _db_stop(e)}})
    record_entries(ledger, "duanban", db_entries, today, cross="db")
    for e in mod["strict"] + mod["loose"]:
        e.pop("_cross", None)

    # 结算（并发抓 open 记录的 K 线）
    open_codes = sorted({str(e.get("code")) for e in ledger.get("entries") or []
                         if (e.get("result") or {}).get("status") != "settled"
                         and str(e.get("code"))})
    klc = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(CD.get_kline, c, CD.sina_symbol(c)): c
                for c in open_codes if CD.sina_symbol(c)}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                kl = fut.result()
            except Exception:
                kl = None
            if kl:
                klc[c] = kl
    n_settled = settle_ledger(ledger, klc)
    ledger["updatedAt"] = _now()
    write_json_atomic(LEDGER, ledger, label="[chen]")

    # 对比（§14：两侧都用**确认池**口径，不再一边含观察池一边不含）
    chen_codes = [e["code"] for e in mod["strict"] + mod["loose"]]
    db_codes = [e.get("code") for e in (db_mod.get("confirmed") or [])]
    mod["compare"] = build_compare(chen_codes, db_codes, ledger)
    # R100z233b：`since` 只从**当前口径**的样本里取。原写法取全台账最小值，
    # 换口径后页面会写成「起始日 2026-10-22 / 已结算 13 条」而表格里样本是 0——
    # 两个数字各说一套，读者只会认为这张表算错了。
    _cur = [e for e in ledger.get("entries") or []
            if (e.get("ruleFrom") or "legacy") == RULE_FROZEN_AT]
    mod["track"] = {
        "since": min([e.get("confirmDate") or e.get("entryDate")
                      for e in _cur
                      if (e.get("confirmDate") or e.get("entryDate"))] or [today]),
        # 与 since 同口径：只数当前口径的已结算条数（旧口径由 legacyN 单独交代）
        "settledN": sum(1 for e in _cur
                        if (e.get("result") or {}).get("status") == "settled"),
        "openN": sum(1 for e in _cur
                     if (e.get("result") or {}).get("status") != "settled"),
        "justSettled": n_settled,
        "legacyN": mod["compare"].get("legacyN", 0),
        "settleRule": ("买价=确认日（数据日）次一交易日开盘；窗口 %d 个交易日；"
                       "止盈按盘中触 +%.1f%% 算（可挂单），止损按收盘跌破止损位算（盘中不算），"
                       "开盘即破止损视为不进场（不进胜负分母），都不触按窗口末收盘计。"
                       "两个池用同一口径，胜率才可比。" % (HOLD_DAYS, TARGET_PCT)),
    }

    old["chenPool"] = mod
    write_data_js(args.dashboard or DATA_JS, old, label="[chen]")

    print("[chen] 严格池 %d / 放宽池 %d / 观察 %d / 失效 %d｜今日结算 %d 条"
          % (len(mod["strict"]), len(mod["loose"]), len(mod["watching"]),
             len(mod["failPool"]), n_settled), file=sys.stderr)


def _db_stop(e):
    """断板池标的的止损位。**与陈学长同源**：洗盘段（T+1..最新）最低价。

    R100z222（审核意见 §7）：原来取「K 线末 5 根最低」，和陈学长的「洗盘段最低价」不是一回事
    → 两边的「亏」门槛不同 → 胜率不可直接比（而这张表存在的唯一目的就是比）。
    `duanban.confirmed` 没有 stop 字段，但有 `ztDate` + kline，能算出同一口径：
    T 日 = ztDate 那根，洗盘段 = 它之后的每一根，取 low 最小。

    算出 ≤0 或落在最后一根之后时回退到「末 5 根最低」；仍取不到就返回 0——
    0 会在 pool_stats 里被单列成 `noStop`（不进胜负分母），不会被当成「没有止损的盈利单」。
    """
    try:
        kl = e.get("kline") or []
        if not kl:
            return 0
        bars = [b for b in kl if isinstance(b, list) and len(b) >= 6]
        if not bars:
            return 0
        ztd = str(e.get("ztDate") or "")
        iT = None
        for i, b in enumerate(bars):
            if str(b[0]) == ztd:
                iT = i
                break
        if iT is not None and iT + 1 <= len(bars) - 1:
            lows = [float(b[4]) for b in bars[iT + 1:]]
            if lows and min(lows) > 0:
                return round(min(lows), 2)
        lows = [float(b[4]) for b in bars[-5:]]
        return round(min(lows), 2) if lows else 0
    except Exception:
        return 0


if __name__ == "__main__":
    main()

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
v1 口径（2026-10-25 冻结；回测出结果前不许改这些数字，否则就是拿未来调参）
================================================================================
硬结构 —— strict 严格池与 loose 放宽池都要求：

  S1 沪深主板 60/00，最新收盘 ≥ 4 元
       他 041：「四块钱以下的不要」。这条没被数据反驳，照做。
  S2 T 日涨停（涨幅 ≥ 9.8%，主板口径）
  S3 T 日连板数 ≥ 2
       他 001：「你连板都没有，你反包什么呢兄弟。」——单一条件里边际最大的一条：
       实测 首板 触+3.5% 36.2% → 2 连板 50.9% → 3 连板 61.5%。
       看板池 87% 是首板，正是他明确不做的。
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
  卖点：+3.5% 当天兑现（TARGET_PCT），或持仓满 2 个交易日无条件走（HOLD_DAYS）。
        他 006：「抓一只羊一般不超过两天。」
  止损：洗盘段（T+1..确认日）最低价。他 269：「破了第一天小阴的支撑位……就是止损的时候。」
  仓位：RISK_BUDGET / 止损距离 —— 单笔最大亏总仓 1.5%（他 146 的「高 4 低 6」量化版）。
        ⚠️ 这不是可选建议：实测放开连板/爆量后亏超 -5% 的比例从 8.4% 升到 14.3%，
        只改筛选不改仓位就是把胜率换成爆仓概率。

================================================================================
用法
================================================================================
  python3 chen_pool.py --module --out chen_draft.json   # 只出草稿（不写 data.js）
  python3 chen_pool.py --merge                          # 出草稿并合并进 data.js（含留痕结算）
  python3 chen_pool.py --funnel                         # 只看漏斗：今天卡在哪一关
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
RULE_VERSION = "v1"
RULE_FROZEN_AT = "2026-10-25"
PRICE_MIN = 4.0          # S1 股价下限（元）
LBC_MIN = 2              # S3 连板数下限
VOL_UP_MIN = 2.0         # S4 断板日放量倍数下限（无上限）
SHRINK_MAX = 1.0         # S9 T 日缩量上限 V(T)/前5日均量
BIGDROP_MAX = -3.0       # S10 T+1 涨跌幅上限（%），≤ -3 才算大阴
MAX_WASH_DAYS = 4        # S6 T 日距今最多 4 个交易日（洗盘 ≤3 日）
LOW_TOL = 0.98           # S7 破位容忍 2%
MA5_TOL = 0.97           # S8 站稳容忍 3%
TARGET_PCT = 3.5         # 卖点 +3.5%
STOP_PCT_FALLBACK = -3.0  # 止损兜底（无洗盘段最低价时）
HOLD_DAYS = 2            # 持仓上限（交易日）
RISK_BUDGET = 1.5        # 单笔最大亏总仓 %

# 漏斗关名（顺序 = 判定顺序；前端按此顺序画漏斗）
FUNNEL_STEPS = [
    ("base", "涨停池候选"),
    ("mainboard", "主板+股价≥4元"),
    ("zt", "T日涨停"),
    ("lbc", "连板≥2（他的核心）"),
    ("break", "T+1断板"),
    ("volup", "断板日放量≥2倍"),
    ("low", "未破T日低点"),
    ("shrink", "T+2缩量"),
    ("stand", "站稳MA5"),
    ("soft", "软条件不足→放宽池"),
]


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


def chen_stage(code, kl, zt_iso, fn=None):
    """单只候选的陈学长 v1 口径判定。

    返回 dict：
      {pool:'confirmed'|'watch', tier:'strict'|'loose'|None, stage, form,
       metrics, days_ago, stop_price, wash_low}
    或 None（不成立 / 窗口外）。fn 为漏斗计数器（失败时记最后一关）。
    """
    def _f(step):
        if fn is not None:
            fn[step] = fn.get(step, 0) + 1
        return None

    n = len(kl)
    if n < 12:
        return _f("base")
    if not re.match(r"^(60|00)\d{4}$", str(code)):
        return _f("mainboard")
    iT = next((i for i, k in enumerate(kl) if str(k.get("day")) == zt_iso), None)
    if iT is None:
        return _f("base")

    def CL(i):
        return float(kl[i].get("close") or 0)

    def OP(i):
        return float(kl[i].get("open") or 0)

    def HI(i):
        return float(kl[i].get("high") or 0)

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
        return _f("mainboard")
    # S2 T 日涨停
    th = CD.limit_ratio(code)
    if PC(iT) < th:
        return _f("zt")
    vT = VO(iT)
    if vT <= 0:
        return _f("zt")
    # S3 连板 ≥ 2
    lbc = calc_lbc(kl, iT, th)
    if lbc < LBC_MIN:
        return _f("lbc")
    days_ago = n - 1 - iT
    if days_ago < 2 or days_ago > MAX_WASH_DAYS:
        return _f("break")
    # S4 T+1 断板放量
    i1 = iT + 1
    r1 = VO(i1) / vT
    if PC(i1) >= th:
        return _f("break")
    if r1 < VOL_UP_MIN:
        return _f("volup")
    # S7 不破 T 日低点（先查，破了直接失效，不再进观察池）
    lowT = LO(iT)
    wash_low = min([LO(i) for i in range(i1, n)] or [0])
    if lowT > 0 and min(CL(i) for i in range(i1, n)) < lowT * LOW_TOL:
        return _f("low")
    # S5 T+2 缩量
    i2 = iT + 2
    if i2 > n - 1:
        return _f("shrink")          # 还没到 T+2 = 待缩量
    shrunk = 0 < VO(i2) < VO(i1)
    # S8 站稳 MA5
    ma5 = sum(CL(i) for i in range(n - 5, n)) / 5.0 if n >= 5 else 0.0
    stand = ma5 > 0 and CL(n - 1) >= ma5 * MA5_TOL
    if not shrunk:
        return _f("shrink")
    if not stand:
        return _f("stand")

    # ---- 软条件（只影响 strict / loose 分档）
    v5 = [VO(i) for i in range(max(0, iT - 5), iT)]
    avg5 = (sum(v5) / len(v5)) if v5 else 0.0
    shrink_t = (vT / avg5) if avg5 > 0 else None
    drop1 = PC(i1)
    soft_ok = (shrink_t is not None and shrink_t <= SHRINK_MAX and drop1 <= BIGDROP_MAX)
    tier = "strict" if soft_ok else "loose"

    zt_md = "%s/%s" % (zt_iso[5:7], zt_iso[8:10])
    wash = days_ago - 1
    form = "%s %d板涨停→次%.1f倍量收%+.1f%%→%d日缩量·站稳MA5" % (
        zt_md, lbc, r1, drop1, wash)
    if shrink_t is not None:
        form += "（T日量%.2f倍均量）" % shrink_t
    metrics = {
        "lbc": lbc,
        "volRatio": round(r1, 2),
        "shrinkT": round(shrink_t, 2) if shrink_t is not None else None,
        "drop1": round(drop1, 2),
        "washDays": wash,
        "stopPrice": round(wash_low, 2) if wash_low > 0 else None,
        "tailRisk": r1 > 8.0,
    }
    return {"pool": "confirmed", "tier": tier, "stage": "缩量确认", "form": form,
            "metrics": metrics, "days_ago": days_ago, "stop_price": wash_low}


def chen_watch(code, kl, zt_iso, fn=None):
    """观察池：硬结构已走到「断板放量 + 未破位」，只差缩量/企稳。

    与 chen_stage 共用前置判定，避免两套口径（这是 24.10 那类死链的典型来源）。
    """
    n = len(kl)
    if n < 12 or not re.match(r"^(60|00)\d{4}$", str(code)):
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

    if CL(n - 1) < PRICE_MIN:
        return None
    th = CD.limit_ratio(code)
    if PC(iT) < th:
        return None
    vT = VO(iT)
    if vT <= 0:
        return None
    lbc = calc_lbc(kl, iT, th)
    if lbc < LBC_MIN:
        return None
    days_ago = n - 1 - iT
    if days_ago < 1 or days_ago > MAX_WASH_DAYS:
        return None
    i1 = iT + 1
    if i1 > n - 1:
        return None
    r1 = VO(i1) / vT
    if PC(i1) >= th or r1 < VOL_UP_MIN:
        return None
    lowT = LO(iT)
    if lowT > 0 and min(CL(i) for i in range(i1, n)) < lowT * LOW_TOL:
        return None
    zt_md = "%s/%s" % (zt_iso[5:7], zt_iso[8:10])
    wash_low = min([LO(i) for i in range(i1, n)] or [0])
    if i1 == n - 1:
        stage, form = "待缩量", "%s %d板→次%.1f倍量断板·待缩量" % (zt_md, lbc, r1)
    else:
        ma5 = sum(CL(i) for i in range(n - 5, n)) / 5.0 if n >= 5 else 0.0
        if ma5 > 0 and CL(n - 1) < ma5 * MA5_TOL:
            stage, form = "待企稳", "%s %d板→次%.1f倍量断板→已缩量·待收复MA5" % (zt_md, lbc, r1)
        else:
            return None
    return {"pool": "watch", "tier": None, "stage": stage, "form": form,
            "metrics": {"lbc": lbc, "volRatio": round(r1, 2),
                        "stopPrice": round(wash_low, 2) if wash_low > 0 else None},
            "days_ago": days_ago, "stop_price": wash_low}


# ---------------------------------------------------------------- 交易参数
def trade_params(ref_close, stop_price):
    """买点/卖点/止损/仓位 —— 全部由 v1 口径推出，不含任何主观调速。

    ref_close：确认日收盘价（买价基准用它的次日开盘，这里先给参考值）。
    止损：洗盘段最低价；缺失时用 -3% 兜底（不编更宽的数）。
    仓位：RISK_BUDGET / 止损距离，上限 30%（三条线不可能全仓一只）。
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
    return {
        "refClose": round(buy, 2),
        "buyRule": "确认日次日开盘买入（等洗盘缩量走完再进，不追涨）",
        "targetPct": TARGET_PCT,
        "targetPrice": round(buy * (1 + TARGET_PCT / 100), 2),
        "stopPrice": round(stop, 2),
        "stopPct": round(dist, 2),
        "holdDays": HOLD_DAYS,
        "posPct": pos,
        "riskNote": "单笔最大亏总仓 %.1f%%：仓位 %d%% × 止损 %.1f%%（他 146 的高4低6）"
                    % (RISK_BUDGET, pos, dist),
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


def eval_candidate(code, rec, kl, fn=None):
    """对候选的每个涨停日做判定，确认池优先于观察池。

    漏斗只对**最新涨停日**计数（fn 只传一次）：否则连板票会在窗口内每个涨停日各记一次，
    「卡在连板 151」看着像 151 只、其实只有 ~70 只，漏斗就不可读了。
    """
    best = None
    for k, d in enumerate(sorted(rec["zt"], reverse=True)):
        iso = "%s-%s-%s" % (d[:4], d[4:6], d[6:])
        st = None
        try:
            st = chen_stage(code, kl, iso, fn if k == 0 else None)
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
        return {"version": RULE_VERSION, "entries": [], "updatedAt": ""}
    try:
        d = json.load(open(LEDGER, encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError("ledger 不是 dict")
        d.setdefault("entries", [])
        return d
    except Exception as e:
        print("[warn] 留痕台账读取失败（%s），按空台账继续" % e, file=sys.stderr)
        return {"version": RULE_VERSION, "entries": [], "updatedAt": ""}


def settle_one(e, kl):
    """统一结算口径（两个池、两套指标共用，不然胜率不可比）。

    买价 = 确认日**次一交易日开盘**（唯一可成交的时点；用确认日收盘价属偷看未来）。
    窗口 = 次日起 HOLD_DAYS 个交易日。
      · 止盈：**盘中最高** ≥ +3.5% → 赢（可挂单，按 +3.5% 计）
      · 止损：**收盘**跌破止损价 → 亏（按当日收盘价计）
        ⚠️ 为什么止损看收盘、止盈看盘中：这是实盘可执行的不对称——
        止盈能挂单，止损只有收盘才确认破位（两个池的形态判定也都是收盘口径，
        盘中最低价破位就出局等于用噪音选股，会把两边胜率一起压低）。
      · 同一日先判止损（保守）
      · 都没触 → 窗口末收盘平（expired，按实际收益计）
    """
    days = [str(k.get("day")) for k in kl]
    try:
        idx = days.index(str(e.get("entryDate")))
    except ValueError:
        return None
    if idx + 1 > len(kl) - 1:
        return None
    buy = float(kl[idx + 1].get("open") or 0)
    if buy <= 0:
        return None
    stop = float(e.get("stopPrice") or 0)
    tgt = buy * (1 + TARGET_PCT / 100)
    last = min(idx + HOLD_DAYS, len(kl) - 1)
    for j in range(idx + 1, last + 1):
        hi = float(kl[j].get("high") or 0)
        cl = float(kl[j].get("close") or 0)
        hit_stop = stop > 0 and cl <= stop
        hit_tgt = hi >= tgt
        if hit_stop:
            return {"status": "settled", "result": "loss", "exitDay": days[j],
                    "exitPrice": round(cl, 2), "buyPrice": round(buy, 2),
                    "retPct": round((cl - buy) / buy * 100, 2),
                    "note": "收盘跌破止损位" + ("（同日也触过 +3.5%，保守算亏）" if hit_tgt else "")}
        if hit_tgt:
            return {"status": "settled", "result": "win", "exitDay": days[j],
                    "exitPrice": round(tgt, 2), "buyPrice": round(buy, 2),
                    "retPct": TARGET_PCT, "note": "盘中触 +3.5% 当天兑现"}
    if len(kl) - 1 >= idx + HOLD_DAYS:
        c = float(kl[idx + HOLD_DAYS].get("close") or 0)
        return {"status": "settled", "result": "expired", "exitDay": days[idx + HOLD_DAYS],
                "exitPrice": round(c, 2), "buyPrice": round(buy, 2),
                "retPct": round((c - buy) / buy * 100, 2),
                "note": "持仓 %d 日未触目标也未破止损，收盘平" % HOLD_DAYS}
    return None


def pool_stats(entries):
    done = [e for e in entries if (e.get("result") or {}).get("status") == "settled"]
    if not done:
        return {"n": 0, "win": 0, "loss": 0, "expired": 0, "winRate": None,
                "avgRet": None, "avgWin": None, "avgLoss": None}
    rets = [float((e["result"] or {}).get("retPct") or 0) for e in done]
    win = sum(1 for e in done if (e["result"] or {}).get("result") == "win")
    loss = sum(1 for e in done if (e["result"] or {}).get("result") == "loss")
    exp = len(done) - win - loss
    wl = [r for r in rets if r > 0]
    ll = [r for r in rets if r <= 0]
    return {
        "n": len(done), "win": win, "loss": loss, "expired": exp,
        "winRate": round(win / len(done) * 100, 1),
        "avgRet": round(sum(rets) / len(rets), 2),
        "avgWin": round(sum(wl) / len(wl), 2) if wl else None,
        "avgLoss": round(sum(ll) / len(ll), 2) if ll else None,
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


def record_entries(ledger, pool_key, entries, today, tier=None):
    """把当日池内标的写入台账。同一 (pool, code, entryDate) 只记一次。

    已在池的老标的沿用原 entryDate（不重复记），新标的记今天。
    """
    have = {(x.get("pool"), str(x.get("code")), x.get("entryDate"))
            for x in ledger.get("entries") or []}
    # 已在池但结果未结算的旧条目，把 stopPrice 更新为最新（止损位随洗盘低点移动）
    live = {str(x.get("code")) for x in ledger.get("entries") or []
            if (x.get("result") or {}).get("status") != "settled"}
    added = 0
    for e in entries:
        code = str(e.get("code") or "")
        if not code:
            continue
        ed = e.get("entryDate") or today
        if (pool_key, code, ed) in have:
            continue
        if code in live and ed != today:
            continue          # 该票已有一条未结算记录，不重复记
        ledger["entries"].append({
            "pool": pool_key, "tier": tier, "code": code,
            "name": e.get("name") or "", "entryDate": ed,
            "ztDate": e.get("ztDate") or "",
            # 止损价必须与页面展示的、能成交的那个一致（trade.stopPrice 含 2% 最小距离），
            # 否则会出现「页面写 30、回测按 31 结算」的静默口径分叉。
            "stopPrice": (e.get("trade") or {}).get("stopPrice")
                         or (e.get("metrics") or {}).get("stopPrice") or e.get("stopPrice"),
            "result": None,
        })
        added += 1
    return added


# ---------------------------------------------------------------- 元信息装配
def build_entries(hits, D, tier_filter=None):
    """把命中记录装配成前端要的卡片结构（含 K 线/板块/情绪/交易参数）。"""
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
    """当日池差异 + 历史胜率对比。"""
    chen_s, db_s = set(chen_codes), set(db_codes)
    ents = ledger.get("entries") or []
    groups = {}
    for e in ents:
        groups.setdefault(e.get("pool") or "?", []).append(e)
    return {
        "today": {
            "both": sorted(chen_s & db_s),
            "chenOnly": sorted(chen_s - db_s),
            "duanbanOnly": sorted(db_s - chen_s),
        },
        "stats": {k: pool_stats(v) for k, v in groups.items()},
        "ledgerN": len(ents),
        "settledN": sum(1 for e in ents if (e.get("result") or {}).get("status") == "settled"),
    }


# ---------------------------------------------------------------- 主流程
def build_module(args):
    dates = CD.recent_trade_dates(args.days)
    cand, failed_dates = collect_candidates(dates, args.limit)
    if not cand:
        return None, {"failedDates": failed_dates}

    cands = sorted(cand.items(), key=lambda kv: max(kv[1]["zt"]), reverse=True)[:args.limit]
    t0 = time.time()
    try:
        deadline = float(os.environ.get("CHEN_DEADLINE", "300"))
    except ValueError:
        deadline = 300.0

    funnel_count = {}
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
                local = {}
                hit = eval_candidate(code, rec, kl, local)
                with lock:
                    for k, v in local.items():
                        funnel_count[k] = funnel_count.get(k, 0) + v
                    if hit:
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
            rec = cand.get(code) or {}
            local = {}
            hit = eval_candidate(code, rec, kl, local)
            with lock:
                for k, v in local.items():
                    funnel_count[k] = funnel_count.get(k, 0) + v
                if hit:
                    hits.append(hit)
            time.sleep(0.4)

    hits = [h for h in hits if str(h["code"]).startswith(("60", "00"))]
    print("[info] 候选 %d 只 → 命中 %d 只（用时 %.0fs）"
          % (len(cands), len(hits), time.time() - t0), file=sys.stderr)

    D = {}
    try:
        D = CD.load_dashboard(args.dashboard or DATA_JS)
    except Exception as e:
        print("[warn] data.js 解析失败（%s），情绪一律按 neutral 标注" % e, file=sys.stderr)

    strict = build_entries([h for h in hits if h["stage_info"].get("tier") == "strict"], D)
    loose = build_entries([h for h in hits if h["stage_info"].get("tier") == "loose"], D)
    watch = build_entries([h for h in hits if h["stage_info"]["pool"] == "watch"], D)

    # 漏斗：按判定顺序累计（数字 = 「最后一关卡在这一步的候选数」）
    funnel = [{"step": k, "label": lab, "n": funnel_count.get(k, 0)}
              for k, lab in FUNNEL_STEPS]
    funnel[0]["n"] = len(cands)     # 分母 = 实际判定的候选数，不是分页前的池子大小
    # 末关「软条件不足→放宽池」在 chen_stage 里数不准：漏斗只对**最新涨停日**计数
    # （见 eval_candidate 注释），而一只票可能是在更早那个涨停日走完全程的，
    # 于是它进了放宽池、却不经过带计数器的路径。实测就差了 1（降档 2 只 vs 放宽池 3 只），
    # 页面上一眼能看出自相矛盾。这一行本来就是「放宽池有几只」，直接取放宽池长度，
    # 既定义精确又不会和下面的卡片打架。
    # of = 这一行的分母（进池数）而不是候选总数：3/4 说的是「进了池的 4 只里 3 只被降档，
    # 严格池只剩 1 只」，3/196 说的是「占全部候选 1.5%」——后者一点用都没有，
    # 而前者正是「这两条软条件到底有多严」的唯一度量。
    _passed = len(strict) + len(loose) + len(watch)
    for f in funnel:
        if f["step"] == "soft":
            f["n"] = len(loose)
            f["of"] = _passed
    # 自洽校验：分母 = 八道淘汰关之和 + 进池数。不平就是计数器漏加了一步
    # （R100z218b 实测抓到过：末关 soft 从来没累加，放宽池 3 只却显示 0，
    #  读者会以为那 3 只是硬结构走出来的，恰好把「软条件值不值」的证据抹掉）。
    # soft 是降档不是淘汰，不计入这个恒等式——它统计的是「进池的里面有几只被降档」。
    _lost = sum(f["n"] for f in funnel[1:] if f["step"] != "soft")
    if _lost + _passed != funnel[0]["n"]:
        print("[warn] 漏斗不自洽：分母 %d ≠ 各关 %d + 进池 %d（差 %d）"
              % (funnel[0]["n"], _lost, _passed,
                 funnel[0]["n"] - _lost - _passed), file=sys.stderr)
    mod = {
        "generatedAt": _now(),
        "ruleVersion": RULE_VERSION,
        "ruleFrozenAt": RULE_FROZEN_AT,
        "note": (
            "陈学长口径 v1（2026-10-25 冻结）：主板股价≥4元 → T日涨停 → 连板≥2 → "
            "T+1放量≥2倍断板（无上限）→ 未破T日低点 → T+2缩量 → 站稳MA5；"
            "严格池另加「T日缩量≤1.0 + T+1跌≥3%」两条软条件。"
            "买点=确认日次日开盘，卖点=+3.5%当天兑现或持仓2日，止损=洗盘段最低价，"
            "仓位=单笔亏总仓1.5%。分时量能/盘口/板块角色是日线缺口，未假装实现。"
        ),
        "funnel": funnel,
        "strict": strict,
        "loose": loose,
        "watching": watch,
        "failedDates": failed_dates,
        "compare": {},
        "track": {},
    }
    return mod, {"cand": cand, "funnel": funnel_count}


def main():
    ap = argparse.ArgumentParser(description="陈学长断板反包独立技术指标（chenPool）")
    ap.add_argument("--days", type=int, default=5, help="回看交易日数（默认5）")
    ap.add_argument("--limit", type=int, default=300, help="最多判定候选数")
    ap.add_argument("--module", action="store_true", help="输出 chenPool 模块 JSON")
    ap.add_argument("--out", default=None, help="模块 JSON 同时写入该文件")
    ap.add_argument("--merge", action="store_true", help="合并进 data.js（含留痕结算）")
    ap.add_argument("--funnel", action="store_true", help="只打印漏斗（今天卡在哪一关）")
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

    mod, extra = build_module(args)

    if args.funnel:
        funnel = (mod or {}).get("funnel") or []
        print("\n陈学长 v1 漏斗（%d 日窗口，候选 %d 只）"
              % (args.days, len((extra or {}).get("cand") or {})))
        for f in funnel:
            print("  %-24s %4d" % (f["label"], f["n"]))
        return

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

    # 入池日沿用（首日新入池高亮，与断板页同口径）
    prev_entry = {}
    for pool in ("strict", "loose", "watching"):
        for e in old_mod.get(pool) or []:
            if e.get("code") and e.get("entryDate"):
                prev_entry[str(e["code"])] = e["entryDate"]
    for pool in ("strict", "loose", "watching"):
        for e in mod[pool]:
            c = str(e["code"])
            e["entryDate"] = prev_entry.get(c) or today

    # 失效池滚动
    cur_codes = {str(e["code"]) for p in ("strict", "loose", "watching") for e in mod[p]}
    prev_all = {}
    for pool in ("strict", "loose", "watching"):
        for e in old_mod.get(pool) or []:
            if e.get("code"):
                prev_all[str(e["code"])] = e
    new_fails = []
    for c, e in prev_all.items():
        if c in cur_codes:
            continue
        new_fails.append({"code": c, "name": e.get("name") or "",
                          "sector": e.get("sector") or "", "form": e.get("form") or "",
                          "reason": "形态失效（不再满足连板+放量断板+缩量站稳结构）",
                          "entryDate": today})
    mod["failPool"] = roll_fail_pool(old_mod.get("failPool") or [], new_fails, today,
                                     exclude_codes=cur_codes)

    # 台账：记录当日池
    db_mod = old.get("duanban") or {}
    record_entries(ledger, "chen_strict", mod["strict"], today, tier="strict")
    record_entries(ledger, "chen_loose", mod["loose"], today, tier="loose")
    # 断板反包对照：只读它的池，不写它。
    # ⚠️ 只记**今天新入池**的（entryDate == today）。历史条目不能补记——它们的止损位
    # 只能用今天的 K 线倒推，那是前视偏差；宁可样本少，也不许台账里混进用未来算的止损。
    db_entries = []
    for e in (db_mod.get("confirmed") or []):
        _ed = e.get("entryDate") or today
        if _ed != today:
            continue
        db_entries.append({"code": e.get("code"), "name": e.get("name"),
                           "ztDate": e.get("ztDate"), "entryDate": _ed,
                           "metrics": {"stopPrice": _db_stop(e)}})
    record_entries(ledger, "duanban", db_entries, today)

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

    # 对比
    chen_codes = [e["code"] for e in mod["strict"] + mod["loose"]]
    db_codes = [e.get("code") for e in (db_mod.get("confirmed") or []) + (db_mod.get("watching") or [])]
    mod["compare"] = build_compare(chen_codes, db_codes, ledger)
    mod["track"] = {
        "since": min([e.get("entryDate") for e in ledger.get("entries") or []
                      if e.get("entryDate")] or [today]),
        "settledN": mod["compare"].get("settledN", 0),
        "openN": len(open_codes) - n_settled,
        "justSettled": n_settled,
        "settleRule": ("买价=确认日次一交易日开盘；窗口 %d 个交易日；止盈按盘中触 +3.5%% 算（可挂单），"
                       "止损按收盘跌破止损位算（盘中不算），都不触按窗口末收盘计。"
                       "两个池用同一口径，胜率才可比。" % HOLD_DAYS),
    }

    old["chenPool"] = mod
    write_data_js(args.dashboard or DATA_JS, old, label="[chen]")

    print("[chen] 严格池 %d / 放宽池 %d / 观察 %d / 失效 %d｜今日结算 %d 条"
          % (len(mod["strict"]), len(mod["loose"]), len(mod["watching"]),
             len(mod["failPool"]), n_settled), file=sys.stderr)


def _db_stop(e):
    """断板池标的的止损位：优先用前端同口径（T 日最低价），退而用其 kline 末 5 根最低。"""
    try:
        kl = e.get("kline") or []
        if not kl:
            return 0
        lows = [float(b[4]) for b in kl[-5:] if isinstance(b, list) and len(b) >= 5]
        return round(min(lows), 2) if lows else 0
    except Exception:
        return 0


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🌟 开盘精选（R98k→R99，每日 9:45 自动化调用）。

职责（脚本自包含、AI 只复核不改结构）：
  1. 读 data.js 断板反包双池（07:00 复核后的 confirmed/watching，sentiment/probability 已校准）；
  2. 抓开盘半小时盘面：指数涨跌+成交额（腾讯 gtimg，本机可达）、
     同花顺行业涨跌幅（fetch_ths_industries）、行业主力资金净额（fetch_ths_funds）；
  3. 个股当日实时涨幅批量快照（腾讯 gtimg，一次批量）；
  4. 确定性筛选「板块挂钩」标的写入 duanban.star（R99b）：
     合格板块 = 开盘涨幅>0（板块情绪正向）且 主力净流入>0（资金正向）的板块；
     精选 = 池内非利空标的、其板块与合格板块匹配（互含模糊匹配），按上涨概率降序；
     无合格板块则 picks 为空（宁缺勿滥），绝不放板块不挂钩的标的；
     R100z156：行业/资金接口抓空（连续 2 次）按「数据缺口」降级——不做板块挂钩判定，
     marketLine/sentiment/pushText 三处显式标注，**不写成「无合格板块」**；
  5. marketLine / sentiment 写模板句（R99：只写开盘后板块情绪与资金情绪，不再含集合竞价），
     pushText 留空——由自动化 AI 复核改写为「📖 分析」。

只写 duanban.star（含 generatedAt 不动），其余字段一律不动；东财被 WAF 不影响（同花顺/腾讯源）。
用法：python3 update_star.py [--dry]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_ashare_sectors as uas  # noqa: E402  （复用 curl/UA/同花顺解析）

# R100z55：原为无兜底硬编码路径，托管 node 升版后目录消失即崩；改为存在性判断 + PATH 兜底。
_NODE_CAND = "/Users/loccco/.workbuddy/binaries/run-node"
NODE = _NODE_CAND if os.path.exists(_NODE_CAND) else "node"
IDX_QT = "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
IDX_NAMES = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指"}

# R100z192（2026-10-24）：竞价跳空门槛。数字全部来自 24.50 的回测，**不在这里写结论**。
# 回测（1152 个「断板确认池股票-日」样本、开盘买当日收盘卖、扣 0.20%）：
#   竞价跳空 < −2%  → +0.99%、中位 +0.06%、胜率 50.8%、日聚类 t=+2.15  ← 唯一 t>2 的档
#   −2~−1%          → +0.14%、中位 −0.07%、胜率 50.0%、t=+0.55
#   −1~0%           → +0.03%、中位 −0.46%、胜率 45.4%、t=+0.36
#   0~+1%           → −0.16%、中位 −0.25%、胜率 46.9%、t=−0.37
#   +1~+2%          → −0.45%、中位 −0.94%、胜率 40.0%、t=−1.31  ← 最差
#   > +2%           → −0.33%、中位 −0.18%、胜率 48.8%、t=−1.05
# 注意：−5~−3% 那档只有 +0.18%/中位 −0.69%/t=0.04，**所以不是「越低越好」**，
#      这里只做「≥0 不追、≤−2% 可接」的两端标注，中间档如实写「中性」，禁止拿它排序。
GAP_REPAIR = -2.0   # ≤ 此值：低开修复档（唯一过 t=2 的一档）
GAP_NOCHASE = 0.0   # ≥ 此值：高开档（回测全负，最差 +1~+2%）


def gap_tag(open_pct):
    """开盘价相对昨收的位置 → (tag, 中文)。

    R100z193（2026-10-24 用户口径）：**只描述价格事实，不再做「接/不追」判断**。
    9:15–9:20 的竞价参考价可撤单、是假的；但 9:25 的成交价是真成交，所以 openPct
    本身是可靠的事实，不可靠的是「用开盘高低去推断强弱」这个用法。动作交给 tape.py。
    """
    if open_pct is None:
        return "unknown", "开盘价缺失"
    if open_pct <= GAP_REPAIR:
        return "repair", "低开"
    if open_pct > GAP_NOCHASE:
        return "nochase", "高开"
    return "neutral", "平开"


# ---------------------------------------------------------------- R100z232 今日买入清单
# 用户 2026-10-26 第 4 项：「若当天有符合断板反包池、横盘启动池、陈学长池的标的时，
# 提示当日买入，且包含买入价格区间」。同一句话里还明确了：**不要「昨日持仓·今日离场」**，
# 离场他自己看。所以本节只回答一个问题——今天买什么、多少钱买、跌到哪算买错了。
#
# 三池三套买区，每一套都锚在**池子自己已经算出来、且与回测同源**的价格上，不新造系数：
#   · 横盘启动 bp   : entry.low ~ entry.high（箱体几何：箱顶×0.985 ~ 箱顶×1.005）
#   · 陈学长 chen   : 池里 trade.stopPrice / targetPrice / holdDays / posPct 全齐，直接搬
#   · 断板反包 pool : 池里**没有任何价格纪律字段**（只有 kline/pct/probability/probNote），
#                     所以买区只给「现价 ± 滑点」这条可执行带（回测按次日盘口成交，滑点容差
#                     不是预测），证伪价复用 stop_tracker.mech_stop 的 **T 日最低价**（唯一
#                     权威锚），**目标价与持有天数不给**——不编一个没被回测验证过的数
#   · 谐波 / 吸筹   : 同样没有价格纪律字段 → **不进清单**。硬凑一个买区就是编数字，
#                     而编出来的数字会被当成纪律执行，比不显示更危险。
BUY_SLIP = 1.0          # 滑点容差 %：只是买区半宽，不是收益预测
BUY_SRC_ZH = {
    'chen_strict': '陈学长·严格池',
    'chen_loose': '陈学长·放宽池',
    'duanban': '断板反包·确认池',
    'bp': '横盘启动·贴沿',
}
BUY_ORDER = ('chen_strict', 'duanban', 'bp', 'chen_loose')


# ---------------------------------------------------------------- R101 开盘执行核对
# 用户 2026-10-27 第 1 问：「不止是能看能复盘，还要能在通过开盘后的 15 分通过开盘精选
# 给到能下单」。这一段的定位必须说清，否则会被误读成又一版选股清单：
#
#   timing_plan.py（16:00 数据步跑）→ 昨晚就把「挂什么价、挂哪只」定完了
#   本段（9:45）→ **只核对**开盘价有没有跌破形态下沿、现价相对挂单价在哪，不改清单
#
# 为什么 9:45 不能再筛：回测（duanban_timing.py）里这套单笔毛利 +0.58%，
# 买入价贵 0.5% 年化就从 +58.6% 掉到 −6.8%。9:45 相对 9:25 通常已偏 0.3~1%，
# 「等 15 分钟看情况再决定」这个动作本身就把 alpha 吃光了。
def build_timing_exec(D, quotes):
    """读 data.js 的 timingPlan（昨晚生成），用 9:45 实时行情核对 → 执行表。

    状态口径（只描述事实，不做推荐）：
        go      开盘价仍在形态下沿之上 → 按限价挂单可成交
        broken  开盘价已跌破形态下沿 → 形态作废，撤单
        missed  开盘价已高于限价 → 挂单不会成交（不追）
        市价参考 现价 vs 限价的位置，供用户判断是否还要补单
    """
    plan = (D.get('timingPlan') or {})
    orders = plan.get('orders') or []
    if not orders:
        return None
    today = datetime.now().strftime('%Y-%m-%d')
    rows = []
    for o in orders:
        c = str(o.get('code') or '')
        q = quotes.get(c) or {}
        op = _num(q.get('open'))
        last = _num(q.get('last'))
        lim = _num(o.get('limit'))
        floor = _num(o.get('floor'))
        state, zh = 'unknown', '行情抓空'
        if op is not None and floor is not None and lim is not None:
            if op < floor:
                state, zh = 'broken', '开盘已破形态下沿'
            elif op > lim:
                state, zh = 'missed', '开盘高于限价'
            else:
                state, zh = 'go', '开盘在限价内，可挂'
        rows.append({
            'code': c, 'name': o.get('name') or '', 'sector': o.get('sector') or '',
            'limit': lim, 'floor': floor, 'stop': _num(o.get('stop')),
            'tp': _num(o.get('tp')),
            't1Close': _num(o.get('t1Close')), 'volRatio': o.get('volRatio'),
            'ztDate': o.get('ztDate'),
            'open': op, 'last': last,
            'openPct': _num(q.get('openPct')),
            'vsLimit': (round((op / lim - 1) * 100, 2) if (op and lim) else None),
            'state': state, 'stateZh': zh,
        })
    n_go = sum(1 for r in rows if r['state'] == 'go')
    n_broken = sum(1 for r in rows if r['state'] == 'broken')
    n_missed = sum(1 for r in rows if r['state'] == 'missed')
    bt = plan.get('bt') or {}
    return {
        'asOf': today,
        'time': datetime.now().strftime('%H:%M'),
        'tradeDate': plan.get('tradeDate'),
        'buyDate': plan.get('buyDate'),
        'n': len(rows),
        'nGo': n_go, 'nBroken': n_broken, 'nMissed': n_missed,
        'rows': rows,
        'bt': bt,
        'rule': (
            '这是昨晚（%s 收盘后）就定好的挂单清单，本表只核对开盘有没有破位，**不改清单、不新增标的**。'
            '「可挂」＝开盘价仍在形态下沿之上，按限价挂单能成交；'
            '「已破下沿」＝形态作废，撤单不做；「高于限价」＝挂单不会成交，也不许追价。'
            '回测口径是限价成交，追价的滑价会吃掉全部毛利：买入贵 0.5%%，年化从 +58.6%% 掉到 −6.8%%。'
            % (plan.get('tradeDate') or '')
        ),
        'riskNote': (
            # R101：唯一来源是 timing_plan.py 的 riskNote（随数据一起产出）。
            # 这里不再硬编码一份——两处各写一份等于埋一个「改了一处、另一处还是旧数字」的雷。
            plan.get('riskNote') or (
                '止损距离偏宽是这套形态的固有代价：买价相对 T 日最低价中位 +9.2%、43%% 的单子超过 +10%%。'
                '回测里 17.9%% 的笔触发止损、均亏 5.5%%，靠 46.5%% 的 +5%% 止盈覆盖。'
                '所以**单笔仓位必须小**（每只 1/32 资金），不要因为「看着确定」就加重。'
            )
        ),
        'source': 'timing_plan.py（16:00 生成）→ update_star.py（9:45 只做开盘核对）',
    }


def _num(x):
    """安全转 float：None / '' / NaN / 非数值一律 None（不做 0 兜底，0 是合法价格）。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def build_buy_list(D, picks, quotes, blacklist):
    """三个池 → (今日买入清单 rows, 汇总 summary)。

    `rows` 按 BUY_ORDER 排序：陈学长严格池 → 断板反包确认池 → 横盘启动 → 陈学长放宽池。
    `state` 是这只票**此刻**和买区的关系，不是推荐强度：
        buy  现价落在买区里（可以下单）
        over 现价已高于买区上沿（不追，等回落或就不做）
        under 现价还没到买区下沿（挂单等，别提前进）
        unknown 行情抓空（**不是「没机会」，是没数据**）
    """
    import stop_tracker as ST        # 唯一权威「T 日最低价」止损锚
    import chen_pool as CP           # 唯一权威「风险预算 / 仓位 / 目标」口径

    pick_codes = {str(p.get("code")) for p in (picks or [])}
    rows, skipped = [], []

    def _q(c):
        return quotes.get(str(c)) or {}

    def _guard(code, q, src_zh):
        """三池共用的硬门槛。返回 True=可用；False=已记入 skipped。"""
        c = str(code or "")
        if not c.startswith(("60", "00")):
            skipped.append({"code": c, "src": src_zh, "why": "非沪深主板（不在交易范围）"})
            return False
        if blacklist.get(c) == "red":
            skipped.append({"code": c, "src": src_zh, "why": "硬风险黑名单（red）"})
            return False
        live = _num(q.get("pct"))
        if live is not None and live < -2.0:
            skipped.append({"code": c, "src": src_zh, "why": f"开盘现价 {live:+.1f}%，深跌不接"})
            return False
        if live is not None and live >= 9.5:
            skipped.append({"code": c, "src": src_zh, "why": f"开盘现价 {live:+.1f}%，涨停买不进"})
            return False
        return True

    def _push(kind, code, name, sector, q, lo, hi, anchor, invalid, invalid_zh,
              target, hold_days, pos_pct, rule, extra=None):
        last = _num(q.get("last"))
        lo, hi, invalid, target = _num(lo), _num(hi), _num(invalid), _num(target)
        if last is None or lo is None or hi is None:
            state, state_zh = "unknown", "价格抓空"
        elif last > hi * 1.0001:
            state, state_zh = "over", "已高于买区"
        elif last < lo * 0.9999:
            state, state_zh = "under", "未到买区"
        else:
            state, state_zh = "buy", "在买区内"
        row = {
            "code": str(code), "name": name or "", "sector": sector or "",
            "src": kind, "srcZh": BUY_SRC_ZH.get(kind, kind),
            "price": last, "low": lo, "high": hi, "anchor": anchor,
            "invalid": invalid, "invalidZh": invalid_zh or "证伪价",
            "target": target, "holdDays": hold_days, "posPct": pos_pct,
            "rule": rule, "state": state, "stateZh": state_zh,
            "inPicks": str(code) in pick_codes,
            # 距离用现价算，不是用买区中点——用户要看到的是「现在下单，跌多少算错」
            "invalidPct": (round((invalid / last - 1) * 100, 1)
                           if (invalid and last) else None),
            "targetPct": (round((target / last - 1) * 100, 1)
                          if (target and last) else None),
        }
        if extra:
            row.update(extra)
        rows.append(row)
        return row

    # ---- ① 断板反包确认池：只收 9:45 已过「板块情绪+资金」双闸的 picks（src=pool）----
    # 观察池（watching，stage=待企稳）一律不给买点：还没企稳就没有可执行的买点，
    # 给了就是把「再等一根确认 K 线」这个前提悄悄删掉。
    for p in (picks or []):
        if p.get("src") != "pool":
            continue
        c = str(p.get("code"))
        q = _q(c)
        last = _num(q.get("last"))
        if last is None:
            skipped.append({"code": c, "src": BUY_SRC_ZH["duanban"],
                            "why": "行情抓空，给不出买区（不是没机会，是没数据）"})
            continue
        ent = None
        for e in ((D.get("duanban") or {}).get("confirmed") or []):
            if str(e.get("code")) == c:
                ent = e
                break
        inv, inv_src = (None, "")
        if ent:
            try:
                inv, inv_src = ST.mech_stop(ent, last)
            except Exception:
                inv, inv_src = None, ""
        lo = round(last * (1 - BUY_SLIP / 100.0), 2)
        hi = round(last * (1 + BUY_SLIP / 100.0), 2)
        pos = None
        if inv and inv < last:
            try:
                pos = CP.trade_params(last, inv).get("posPct")
            except Exception:
                pos = None
        _push("duanban", c, p.get("name"), p.get("sector"), q, lo, hi, "live",
              inv, ("T日最低价" if inv_src == "T日低点" else (inv_src or "证伪价")),
              None, None, pos,
              "开盘价上下 %.0f%% 内可下单（回测按次日盘口成交，超这条线就不是同一笔交易）；"
              "收盘跌破证伪价＝反包形态作废，先撤" % BUY_SLIP)

    # ---- ② 横盘启动池（涨停锚口径）：买区＝次日挂单带，证伪价＝涨停日最低×0.98 ----
    # 不给目标价（用户 2026-10-26「止盈你不用管」），只给买区 + 证伪价 + 失效线。
    for e in ((D.get("breakoutPool") or {}).get("passed") or []):
        c = str(e.get("code"))
        q = _q(c)
        if not _guard(c, q, BUY_SRC_ZH["bp"]):
            continue
        b = e.get("entry") or {}
        lo, hi = _num(b.get("low")), _num(b.get("high"))
        if lo is None or hi is None:
            skipped.append({"code": c, "src": BUY_SRC_ZH["bp"], "why": "池条目缺买区字段"})
            continue
        pos = None
        try:
            pos = CP.trade_params(hi, _num(b.get("stop"))).get("posPct")
        except Exception:
            pos = None
        _push("bp", c, e.get("name"), e.get("sector"), q, lo, hi, "anchor",
              b.get("stop"), "证伪价（跌破涨停日最低）",
              None, None, pos,
              "涨停日 %s 起洗盘 %s 个交易日、量比 %s：在 %.2f~%.2f 挂低吸；"
              "收盘站上 %.2f（较涨停板 +30%%）就算已经启动，不再追"
              % (str(e.get("limitDay") or "—")[5:], e.get("washDays"),
                 e.get("volRatio"), lo, hi, _num(b.get("invalidUp")) or 0),
              extra={"score": e.get("score"), "posPct": e.get("posPct"),
                     "washDays": e.get("washDays"), "volRatio": e.get("volRatio"),
                     "limitDay": e.get("limitDay"), "limitLow": e.get("limitLow")})

    # ---- ③ 陈学长双池：trade 块参数齐全，直接搬；观察池（watching）不给买点 ----
    for kind, key in (("chen_strict", "strict"), ("chen_loose", "loose")):
        for e in ((D.get("chenPool") or {}).get(key) or []):
            c = str(e.get("code"))
            q = _q(c)
            if not _guard(c, q, BUY_SRC_ZH[kind]):
                continue
            tr = e.get("trade") or {}
            last = _num(q.get("last"))
            # 陈学长买区＝现价±滑点（回测成交价是确认日次日开盘，9:45 的现价就是
            # 那笔交易此刻还能拿到的价）；不给箱体，因为他这套口径没有箱体。
            if last is None:
                skipped.append({"code": c, "src": BUY_SRC_ZH[kind],
                                "why": "行情抓空，给不出买区（不是没机会，是没数据）"})
                continue
            _push(kind, c, e.get("name"), e.get("sector"), q,
                  round(last * (1 - BUY_SLIP / 100.0), 2),
                  round(last * (1 + BUY_SLIP / 100.0), 2), "live",
                  tr.get("stopPrice"), "证伪价（T日最低价）",
                  tr.get("targetPrice"), tr.get("holdDays"), tr.get("posPct"),
                  "确认日次日盘口买入；买入价超过现价 %.0f%% 就放弃这一笔"
                  % BUY_SLIP,
                  extra={"stopFar": tr.get("stopFar"),
                         "riskNote": tr.get("riskNote"),
                         "stopPct": tr.get("stopPct")})

    rows.sort(key=lambda r: (BUY_ORDER.index(r["src"])
                             if r["src"] in BUY_ORDER else 9,
                             -(r.get("score") or 0)))

    n_buy = sum(1 for r in rows if r["state"] == "buy")
    by_src = {}
    for r in rows:
        by_src[r["srcZh"]] = by_src.get(r["srcZh"], 0) + 1
    if not rows:
        head = "今日三池无买点，不建仓"
        if skipped:
            head += "（%s）" % "；".join(
                "%s %s" % (s["src"], s["why"]) for s in skipped[:4])
        text = head + "。空池/无买点是常态，不要为了「今天买点什么」去池外找票。"
    else:
        seg = "、".join("%s %d 只" % (k, v) for k, v in by_src.items())
        names = "、".join("%s(%s %.2f~%.2f)" % (r["name"], r["code"], r["low"], r["high"])
                          for r in rows[:6])
        text = "今日买点 %d 只（%s）：其中在买区内 %d 只。%s" % (len(rows), seg, n_buy, names)
        if len(rows) > 6:
            text += " 等"
        if skipped:
            text += "。未给买点 %d 只（%s）" % (
                len(skipped), "；".join("%s %s" % (s["src"], s["why"]) for s in skipped[:3]))
    summary = {
        "n": len(rows),
        "nBuy": n_buy,
        "bySrc": by_src,
        "text": text,
        # 这段是**直接渲染成网页正文**的（前端 bl-rule 用 esc 原样输出，不解析 Markdown），
        # 所以两条硬规矩：① 不许带 `**` 强调符（会原样显示成星号）；
        # ② 不许出现 holdDays / stopPct 这类内部字段名（用户硬偏好：页面只出中文）。
        # 2026-10-26 首轮截图实测两种都犯了，被 L16 视觉自检抓到。
        "rule": ("买区＝此刻可以下单的价格带，不是目标价；证伪价被打穿＝形态作废，先撤不留；"
                 "持有到每只标注的持有天数上限无条件了结（回测口径）。"
                 "本节不含「昨日持仓·今日离场」——离场自行决定。"),
        "skipped": skipped,
        "slipPct": BUY_SLIP,
        # R100z246：记录**本清单用的池快照时点**（2026-10-27）。
        # 背景：清单是 9:45 盘中生成的，而横盘启动池 16:00 收盘会整体重算换名单。
        # 晚上读页面时，清单里的票「在当时的池里、不在现在的池里」，audit G47 原本
        # 一律判「凭空造票」——实测 2026-10-27 被误报 9 只，全是 10-26 收盘池的合法成员。
        # 记下快照时点后，闸门能区分两种情形：
        #   池快照日期 == 清单日期 → 同一时点，票必须对得上（真造票就 FAIL）
        #   池快照日期  > 清单日期 → 池已换过一轮，只校验「当时合法」，不追「现在还在」
        # 这不是放宽闸门，是把「跨时点比对」这个假问题从判据里摘出去。
        "poolSnapshot": {
            "duanban": str((D.get("duanban") or {}).get("generatedAt") or ""),
            "breakout": str((D.get("breakoutPool") or {}).get("tradeDate") or ""),
            "chen": str((D.get("chenPool") or {}).get("generatedAt") or ""),
        },
    }
    return rows, summary


def _gap_summary(picks):
    """开盘价位置分布（低开/平开/高开各几只）。

    R100z193 起**不再承载动作结论**——2026-10-24 用户明确反对「拿竞价定强弱」，
    动作口径移到 tapeSummary（分时盘口结构）。这里只留一个可核对的价格事实分布，
    并配一条 rule 把「不许据此下结论」写死，防止 AI 拿它编「低开可接」。
    """
    pool = [p for p in picks if p.get("src") == "pool"]
    cnt = {"repair": 0, "neutral": 0, "nochase": 0, "unknown": 0}
    for p in pool:
        cnt[p.get("gapTag") or "unknown"] = cnt.get(p.get("gapTag") or "unknown", 0) + 1
    if not pool:
        text = "断板池本次无入选标的（板块不挂钩或均被剔除），无开盘价分布可言。"
    else:
        parts = []
        if cnt["repair"]:
            parts.append(f"低开 {cnt['repair']} 只")
        if cnt["neutral"]:
            parts.append(f"平开 {cnt['neutral']} 只")
        if cnt["nochase"]:
            parts.append(f"高开 {cnt['nochase']} 只")
        if cnt["unknown"]:
            parts.append(f"开盘价取不到 {cnt['unknown']} 只（不许猜方向）")
        text = f"断板池 {len(pool)} 只开盘价位置：" + "；".join(parts) + "。"
    return {"repairN": cnt["repair"], "neutralN": cnt["neutral"],
            "nochaseN": cnt["nochase"], "unknownN": cnt["unknown"],
            "poolN": len(pool),
            "rule": "本段只是开盘价相对昨收的位置（低开/平开/高开），是价格事实、不是强弱信号，"
                    "禁止用它给标的排序，也禁止用「低开=可接、高开=不追」这类结论——"
                    "R100z193 起动作口径改由 tapeSummary 的盘口结构决定",
            "text": text}


def _tape_unknown(why):
    """分时拿不到时的**显式占位**（R100z221）。

    为什么不留空：`p["tape"]` 缺键时，AI 在 data.js 里看到的是一只看不出异常的票，
    很容易写成「盘口平稳/无明显抛压」——那是**把取数故障读成利好**。缺键还会让
    B6 台账的 `duanban.star.picks[].tape` 断在第一个元素上转 FAIL（实测闸门只下钻 [0]），
    即一次接口抖动就会拦住 19:30 的部署。所以键必须存在，值必须是「未知」。

    **字段必须与 tape.diagnose 的返回值同构**（16 个键）：prompt 写的是
    「`picks[].tape` 是每只明细（`state/zh/act/actZh/text`…）」，只补齐 state 而缺 act/actZh 的话，
    AI 读 `actZh` 会拿到 undefined，反而更容易自己编。数值项一律 `None`（= 没测到），
    不补 0 —— 0 是"测出来是 0"，与"没测"是两回事。

    `state/zh/act/actZh` 直接取 `tape.STATE['unknown']`（= 分时缺失 / wait / 不下结论），
    **不在这里另造文案**：prompt 已写死「unknown 分时抓空 → 不下结论，严禁猜方向」，
    口径必须同源才不会出现「脚本说抓空、prompt 说不下结论、页面说平稳」三种说法。
    tape 不可用时用同值硬编码兜底（except 分支正是它坏了的情况）。
    """
    try:
        import tape as _T
        zh, act, act_zh = _T.STATE["unknown"]
    except Exception:                                       # noqa: BLE001
        zh, act, act_zh = "分时缺失", "wait", "不下结论"
    return {"state": "unknown", "zh": zh, "act": act, "actZh": act_zh,
            "text": f"开盘与此刻价格未取到（{why}）。结构＝{zh}",
            "openPct": None, "livePct": None, "vsOpen": None, "vsVwap": None,
            "downVolShare": None, "breaks": None, "reclaim": None,
            "burst": None, "slope": None, "auctionShare": None, "nBars": None,
            "degraded": True, "why": why,
            "rule": "「未知」是取数故障、不是盘口正常：禁止据此判断强弱，"
                    "禁止写成「无明显抛压」「量能平稳」这类结论"}


def _tape_summary_unknown(picks, why):
    """分时整体失败时的摘要占位（与 tape.summarize 同构，前端读 text/rule）。"""
    return {"byState": {"unknown": len(picks)},
            "poolByState": {"unknown": sum(1 for p in picks if p.get("src") == "pool")},
            "text": f"本次精选 {len(picks)} 只均未取到分时数据（{why}）——结构缺失，不构成任何强弱结论。",
            "missing": [p.get("code") for p in picks],
            "rule": "结构缺失是取数故障、不是「盘口正常」：只说明没数据，"
                    "禁止当成无抛压或走势平稳"}


def load_data(data_path):
    with open(data_path, encoding="utf-8") as f:
        s = f.read()
    i = s.index("{")
    return s, json.loads(s[i:s.rindex("}") + 1])


def load_blacklist(base):
    """读 risk_blacklist.json 的 blacklist 段 → {code: severity}。

    R100z9y（2026-10-09 实战）：star 精选把硬风险黑名单标的（金溢科技 002869，
    red·两次减持公告）选进了 picks，直到 validate_stocks --scope star 才报 FAIL。
    断板池由 16:00 数据步负责、谐波/横盘启动/吸筹池也不查减持，**开盘精选是唯一有机会
    在选股环节拦住这类标的的闸口**，故在脚本里硬剔（severity=red），而不是让
    每天靠 AI 事后补救（AI 补救还会踩「配额已用尽、下一名也上不了」的坑）。
    文件缺失/损坏一律按空处理（源不可达不等于安全，但也不阻塞本任务）。
    """
    try:
        with open(os.path.join(base, "risk_blacklist.json"), encoding="utf-8") as f:
            raw = json.load(f)
        bl = raw.get("blacklist") or {}
        return {str(c): str(v.get("severity") or "") for c, v in bl.items()
                if isinstance(v, dict)}
    except Exception:
        return {}


def fetch_open_indices():
    """腾讯 gtimg 指数快照 → [{name, pct, amount亿}]；失败 []。"""
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "15", IDX_QT],
                           capture_output=True, timeout=25)
        txt = r.stdout.decode("gbk", errors="ignore")
    except Exception:
        return []
    out = []
    for m in re.finditer(r'v_(sh|sz)(\d{6})="([^"]+)"', txt):
        sym = m.group(1) + m.group(2)
        f = m.group(3).split("~")
        if len(f) <= 37:
            continue
        try:
            cur, prev = float(f[3]), float(f[4])
            pct = round((cur - prev) / prev * 100, 2) if prev else 0.0
            open_pct = None
            try:
                op = float(f[5])
                open_pct = round((op - prev) / prev * 100, 2) if prev and op > 0 else None
            except Exception:
                pass
            amt_yi = round(float(f[37]) / 10000, 0)  # gtimg 成交额单位万元 → 亿
            out.append({"name": IDX_NAMES.get(sym, f[1]), "pct": pct,
                        "openPct": open_pct, "amount": amt_yi})
        except Exception:
            continue
    return out


def fetch_stock_quotes(codes):
    """批量腾讯 gtimg 个股快照 → {code: {pct, openPct, open, last, prev}}；失败 {}。"""
    quotes = {}
    for i in range(0, len(codes), 60):
        batch = codes[i:i + 60]
        q = ",".join(("sh" if c.startswith("6") else "sz") + c for c in batch)
        try:
            r = subprocess.run(["curl", "-s", "--max-time", "15",
                                f"https://qt.gtimg.cn/q={q}"],
                               capture_output=True, timeout=25)
            txt = r.stdout.decode("gbk", errors="ignore")
        except Exception:
            continue
        for m in re.finditer(r'v_(?:sh|sz)(\d{6})="([^"]+)"', txt):
            f = m.group(2).split("~")
            if len(f) > 32:
                try:
                    d = {"pct": round(float(f[32]), 2)}  # 32=涨跌幅%
                    # R100z232：买区要有**绝对价格**锚。此前只存涨跌幅，前端算不出
                    # 「5.59~5.71 之间买」这种区间，只能显示百分比——用户第 4 项要的是价格。
                    # 3=现价 4=昨收 5=今开（gtimg 口径，GBK 文本按 ~ 分隔）。
                    try:
                        d["last"] = float(f[3]) or None
                    except Exception:
                        d["last"] = None
                    try:
                        d["open"] = float(f[5]) or None
                    except Exception:
                        d["open"] = None
                    try:
                        prev, op = float(f[4]), float(f[5])
                        d["prev"] = prev or None
                        d["openPct"] = round((op - prev) / prev * 100, 2) if prev and op > 0 else None
                    except Exception:
                        d["prev"] = None
                        d["openPct"] = None
                    quotes[m.group(1)] = d
                except Exception:
                    continue
    return quotes


def fetch_industries_resilient():
    """同花顺行业一览：抓空重试一次，仍空返回 (None, 原因)。

    R100z156（2026-10-20）：`common.fetch_ths_industries` 的契约是「抓空返回 []，
    由调用方自行走备源/兜底」，但本脚本原来只写 `or []`，把「接口抓空」静默当成
    「当日没有合格板块」——10-20 09:54 行业页被反爬打回 17KB 空页（20:05 复测已
    恢复 90 行，属间歇性），star 判定「合格板块：无」，断板池 0 只入选，页面与
    微信推送都看不出是取数故障。现改为：二次重试 → 仍空则显式降级（marketLine /
    sentiment 标数据缺口、pushText 写兜底提示、stdout 打 [WARN]），
    **绝不把「抓不到」写成「没有」**。
    """
    last_n, last_err = 0, None
    for attempt in (1, 2):
        try:
            rows = uas.fetch_ths_industries() or []
            last_err = None
        except Exception as e:  # noqa: BLE001 —— 任何抓取异常都按「抓空」处理
            rows, last_err = [], repr(e)
        last_n = len(rows)
        if last_n >= 10:
            if attempt == 2:
                print(f"[WARN] 同花顺行业接口第 2 次重试成功（{last_n} 行），本次数据可用")
            return rows, None
        if attempt == 1:
            print(f"[WARN] 同花顺行业接口仅返回 {last_n} 行（<10，疑似反爬空页），2s 后重试一次")
            time.sleep(2.0)
    reason = f"同花顺行业接口连续 2 次抓空（末次 {last_n} 行"
    if last_err:
        reason += f"，异常 {last_err}"
    return None, reason + "）"


def build_star(D, base=None):
    base = base or os.path.dirname(os.path.abspath(__file__))
    blacklist = load_blacklist(base)
    db = D.get("duanban") or {}
    pools = []
    for pool in ("confirmed", "watching"):
        pools.extend(db.get(pool) or [])

    # ---- R100m（需求7）：技术池候选——直接从 谐波/横盘启动/吸筹 池取标的（前一日 16:00 数据步结果） ----
    # 这些标的无 sentiment/probability（neutral 兜底），按池内得分排序，取头部，宁缺勿滥。
    # R100z85（量纲修正，2026-10-08 用户质询「标的映射只有断板+单一技术池」暴露）：原实现全池合并
    # 按 score 降序取前 8，但原九门量纲 score×10（8~10分=80~100）结构性碾压 谐波(确认65/观察55)
    # 与 吸筹(60~76)——当时九门前 10 全为 90/80，top8 被它垄断，谐波 12 只/吸筹 6 只候选
    # 一个都进不了终选，AI 映射段随之只写断板+单池。改为**按池配额保底**：横盘启动≤3、谐波≤3、
    # 吸筹≤2、陈学长≤2（配额决定入选资格，合并后分数只决定展示顺序），总量 10；
    # 跨池去重按 横盘启动→谐波→吸筹→陈学长 优先级（多池命中取先处理池的 label）；
    # -2% 深跌/≥9.5% 涨停剔除守卫不变。R100z221 新增陈学长池，因此总量从 8 提到 10
    # （原因见下面「配额合计必须 ≤ 切片上限」那段）。
    hz_list, bp_list, acc_list = [], [], []
    hz = D.get("harmonic") or {}
    _seen_hz = set()
    for it in (hz.get("confirmPool") or [])[:6] + (hz.get("watchPool") or [])[:6]:
        c = str(it.get("code") or "")
        if c.startswith(("60", "00")) and c not in _seen_hz:
            _seen_hz.add(c)
            hz_list.append({"code": c, "name": it.get("name"), "sector": it.get("sector") or "",
                            "src": "hz", "label": f"谐波{it.get('pattern') or ''}",
                            "score": 65 if it.get("stage") == "确认" else 55})
    # R100z232：九门（powerScreen）已下线，换成横盘启动（breakoutPool）。
    # 量纲变化：九门 score 是 0-10 的「过几门」，所以要 ×10 才和吸筹同量纲；
    # 横盘启动 score_of() 直接出 0-100，**不能再 ×10**，否则单只票 900 分直接垄断配额。
    bp = D.get("breakoutPool") or {}
    _seen_bp = set()
    for it in (bp.get("passed") or [])[:10]:
        c = str(it.get("code") or "")
        if c.startswith(("60", "00")) and c not in _seen_bp:
            _seen_bp.add(c)
            bp_list.append({"code": c, "name": it.get("name"), "sector": it.get("sector") or "",
                            "src": "bp", "label": f"横盘启动{it.get('score') or ''}分",
                            "score": int(it.get("score") or 0)})  # 量纲：0-100（已同吸筹）
    acc = D.get("accumulation") or {}
    for it in (acc.get("scored") or []):
        try:
            sc = int(it.get("score") or 0)
        except Exception:
            continue
        c = str(it.get("code") or "")
        if sc >= 60 and c.startswith(("60", "00")):
            acc_list.append({"code": c, "name": it.get("name"), "sector": it.get("sector") or "",
                             "src": "acc", "label": f"吸筹{sc}分", "score": sc})
    # ---- R100z221：陈学长池并入技术池 ----
    # 用户 2026-10-26：「开盘精选池子里也记得加入陈学长技术指标池，因为对胜率有帮助」。
    # 取严格池优先、放宽池次之；**观察中不进**——那是「还差一根确认 K 线」的票，
    # 当下没有可成交的买点，与断板观察池不进精选同一条理由。
    # 为什么这不是重复计一遍断板池：断板反包的放量上限是 3.5 倍，而实测 5~8 倍那一档
    # 触 +3.5% 的概率最高（49.7%）；这批票被断板口径砍掉、却正好落在陈学长口径
    # （断板日放量 ≥2 倍、无上限）里。重叠部分由下面的 pool_codes 去重兜住。
    chen_list = []
    ch = D.get("chenPool") or {}
    _seen_ch = set()
    for _tier, _arr, _base_sc in (("strict", ch.get("strict") or [], 80),
                                  ("loose", ch.get("loose") or [], 70)):
        for it in _arr:
            c = str(it.get("code") or "")
            if not c.startswith(("60", "00")) or c in _seen_ch:
                continue
            if (it.get("sentiment") or "") == "bear":
                continue  # 利空标的不进精选（与断板池同一条：sent == 'bear' 直接跳过）
            _seen_ch.add(c)
            _m = it.get("metrics") or {}
            try:
                _lbs = max(1, int(_m.get("lbc") or 1))
            except Exception:
                _lbs = 1
            # 排序分只在池内用：口径档位（严格 80 / 放宽 70）+ 每板 +4（最多算 3 板）。
            # 不与 ×10 的量纲混比——配额已经保证各池的上车名额，分数只决定展示顺序。
            chen_list.append({"code": c, "name": it.get("name"), "sector": it.get("sector") or "",
                              "src": "chen", "sentiment": it.get("sentiment") or "neutral",
                              "label": ("陈学长·严格池·" if _tier == "strict" else "陈学长·放宽池·")
                                       + (f"{_lbs}板" if _lbs > 1 else "首板"),
                              "score": _base_sc + 4 * min(_lbs, 3)})
    # ⚠️ 配额装配挪到 picks 定稿之后（R100z221）——理由见下面 `pool_codes` 那一段。
    # 这里只先把四个池的候选代码汇总出来，供下面批量抓个股快照用。
    _tech_codes = []
    for _l in (bp_list, hz_list, acc_list, chen_list):
        _tech_codes.extend([t["code"] for t in _l])
    # R100z241（2026-10-27 用户问「开盘精选和今日买入清单两块的区别」时抓到）：
    # 买入清单要覆盖**整池**，而上面四个 _list 是为「精选配额」截断过的（bp 只取 passed[:10]、
    # hz 取前 6+6、陈学长取严格/放宽全量但 acc 只取 ≥60 分）。于是 breakoutPool.passed 30 只里
    # 第 11~30 只**从未进过 quotes** → build_buy_list 里 last=None → 全部渲染成「价格抓空」。
    # 实测（2026-10-27 09:58 数据）：清单 32 只里 19 只 price 为空，**19 只全部落在池前 10 之外**，
    # 而同一批代码此刻直接用 curl 抓腾讯接口是 15/15 有返回 —— 说明不是数据源抖动，是我们没去抓。
    # 「价格抓空」在页面上是「没有数据」的降级文案，读者会以为行情接口坏了；
    # 真实原因是自己只抓了 10 只却列了 30 行，这是比抓空更坏的错：**数字本身是假的**。
    # 修法：把买入清单的三类来源（全量横盘启动 / 陈学长双池）也并进抓取列表。
    for _e in ((D.get("breakoutPool") or {}).get("passed") or []):
        _c = str(_e.get("code") or "")
        if _c.startswith(("60", "00")):
            _tech_codes.append(_c)
    for _k in ("strict", "loose"):
        for _e in ((D.get("chenPool") or {}).get(_k) or []):
            _c = str(_e.get("code") or "")
            if _c.startswith(("60", "00")):
                _tech_codes.append(_c)
    _tech_codes = list(dict.fromkeys(_tech_codes))

    if not pools and not _tech_codes:
        return None, "断板反包双池与四个技术池（谐波/横盘启动/吸筹/陈学长）均为空"

    # ---- 盘面：指数 / 行业涨幅 / 资金 ----
    idx = fetch_open_indices()
    ths, ths_err = fetch_industries_resilient()
    sectors = []
    for x in (ths or []):
        try:
            sectors.append({"name": x.get("name") or "", "code": x.get("code") or "",
                            "pct": round(float(x.get("pct") or 0), 2)})
        except Exception:
            continue
    sectors = [s for s in sectors if s["name"]]
    sectors.sort(key=lambda s: -s["pct"])
    # R100z170：技术池标的（谐波/横盘启动/吸筹）身上只有 sector 名、没有板块涨幅字段，
    # 结果「开盘精选」里来自技术池的条目板块涨幅整片「—」。这里按同花顺一级行业名
    # 精确命中一次补上；名字不属同花顺一级（例如主题名「创新药·CXO」）就留空，
    # 由前端 boardPctOf 三级回落兜底，绝不硬套。
    sec_map = {s["name"]: s for s in sectors}
    try:
        fund_in, fund_out = uas.fetch_ths_funds()
    except Exception:
        fund_in, fund_out = None, None

    # R100z156：行业涨幅 / 主力资金 任一条判据抓空 = 数据缺口，必须显式降级，
    # 不能像过去那样让 good_boards 自然空集、把「抓不到」说成「无合格板块」。
    gap_reason = ths_err or (None if fund_in else
                             "同花顺行业资金接口抓空（主力净流入判据缺失）")
    degraded = gap_reason is not None

    # ---- 个股快照（当日实时涨幅）：断板池 + 四个技术池候选一并快照 ----
    # R100z221：原来抓的是「配额装配后那 10 只」，而装配已挪到后面（要看断板池选走了谁），
    # 所以这里改成抓四个池的全部候选——多抓十几只，换来装配阶段能按实时涨幅顺位递补。
    # R101b（2026-10-28 实测抓到的漏抓）：`timingPlan.orders`（昨夜挂单清单）的代码原本
    # 不在抓取列表里，8 只挂单只有 1 只因为「碰巧也在横盘启动池」拿到行情，其余 7 只被
    # build_timing_exec 全部渲染成「行情抓空」——但同一批代码当时直接用 curl 抓腾讯接口
    # 8/8 有返回。这与 R100z241 的 buyList 是同一个错：**不是数据源坏，是自己没去抓**。
    # 挂单核对是 9:45 的核心动作之一，7/8 抓空等于这张表作废；故把挂单代码并进同一次
    # 批量快照（多抓 8 只，忽略不计的成本）。注意：只并入抓取，不并入候选装配。
    _fetch_codes = [str(e.get("code")) for e in pools] + _tech_codes
    for _o in (((D.get("timingPlan") or {}).get("orders")) or []):
        _c = str(_o.get("code") or "")
        if _c.startswith(("60", "00")):
            _fetch_codes.append(_c)
    quotes = fetch_stock_quotes(list(dict.fromkeys(_fetch_codes)))

    # ---- R99c：确定性筛选——仅「板块情绪正向 + 主力净流入正向」板块挂钩的池内标的 ----
    # 合格板块：涨幅>0 且 主力净流入>0；池内板块与合格板块名互含即视为挂钩。
    board_fund = {}
    if fund_in:
        for f in fund_in:
            try:
                board_fund[str(f.get("name") or "")] = float(f.get("value") or 0)
            except Exception:
                continue
    good_boards = []  # [(name, pct, fund亿)]
    for s in sectors:
        if s["pct"] <= 0:
            continue  # 板块情绪须正向
        fv = None
        for bn, bv in board_fund.items():
            if bn and (bn in s["name"] or s["name"] in bn):
                fv = bv
                break
        if fv is None or fv <= 0:
            continue  # 资金须净流入正向
        good_boards.append((s["name"], s["pct"], fv))

    if degraded:
        print(f"[WARN] {gap_reason}")
        print("[WARN] → 本次不做板块挂钩判定（严禁把「抓不到」说成「无合格板块」），"
              "断板池标的不参与精选，picks 仅保留技术池形态标的")

    def _linked(sec):
        for n, _, _ in good_boards:
            if n and sec and (n in sec or sec in n):
                return True
        return False

    sent_rank = {"bull": 0, "neutral": 1, "bear": 2}
    cand = []
    for e in pools:
        sent = e.get("sentiment") or "neutral"
        if sent == "bear":
            continue  # 利空标的不进精选
        if blacklist.get(str(e.get("code"))) == "red":
            continue  # R100z9y：硬风险黑名单（减持实施中/ST/预亏）不进精选
        sec = str(e.get("sector") or "")
        if not _linked(sec):
            continue  # R99b：板块不挂钩的不进精选
        # R100z197（2026-10-24 用户拍板）：缺失**不再兜底成 50**。
        # 原写法 `float(e.get("probability") or 50)` 等于往排序里注入一个伪中位数：
        # 实测池内真实基线最低是 48，而「不知道」被排到了比 48 更靠前的位置。
        # 现在缺失一律 None，排序排到所有有值之后（前端 index.html 的 starPool 排序
        # 本来就是 `probability == null → -1`，这里只是让脚本与前端口径一致）。
        try:
            _raw_p = e.get("probability")
            p = float(_raw_p) if _raw_p not in (None, "") else None
        except Exception:
            p = None
        q = quotes.get(str(e.get("code"))) or {}
        cand.append({"code": str(e.get("code")), "name": e.get("name"),
                     "sector": sec, "sentiment": sent,
                     "probability": (round(p, 1) if p is not None else None),
                     "pctLive": q.get("pct"), "openPct": q.get("openPct"),
                     # R100z170：双池标的的板块涨幅必须跟着搬过去。此前只搬 sector，
                     # 导致 picks 里 boardPctToday/boardPctZt 全是 None，前端整齐显示「—」
                     # （用户 2026-10-21 截图：中京电子/澳弘电子「板块涨幅 —」而池里同票有值）。
                     "boardPctToday": e.get("boardPctToday"),
                     "boardPctZt": e.get("boardPctZt"),
                     "bkCode": e.get("bkCode")})

    # R100z197：缺失（None）排最后，其次是概率降序，再按情绪。与前端 starPool 排序同口径。
    cand.sort(key=lambda x: (x["probability"] is None,
                             -(x["probability"] if x["probability"] is not None else 0.0),
                             sent_rank.get(x["sentiment"], 1)))
    picks = []
    for c in cand:  # 有基线概率的降序在前，无基线的排后（概率同则利好优先）
        sent_cn = {"bull": "利好", "neutral": "中性"}.get(c["sentiment"], c["sentiment"])
        # R100z197：无基线时写「基线未给」，不再编一个 50% 出来冒充概率。
        note = f"{c['sector']}·{sent_cn}·" + (
            f"基线{c['probability']:.0f}%" if c["probability"] is not None else "基线未给")
        _gt, _gzh = gap_tag(c.get("openPct"))
        if c.get("openPct") is not None:
            note += f"·开盘{c['openPct']:+.1f}%({_gzh})"
        if c.get("pctLive") is not None:
            note += f"·现涨{c['pctLive']:+.1f}%"
        picks.append({"code": c["code"], "name": c["name"], "sector": c["sector"],
                      "sentiment": c["sentiment"], "probability": c["probability"],
                      # R100z192：开盘价位置标注；R100z193 起它**不再是动作依据**
                      # （用户 2026-10-24：竞价可撤单、不可信；动作交给 picks[].tape）。
                      "gapTag": _gt, "gapZh": _gzh, "openPct": c.get("openPct"),
                      "note": note, "src": "pool",
                      "boardPctToday": c.get("boardPctToday"),
                      "boardPctZt": c.get("boardPctZt"),
                      "bkCode": c.get("bkCode")})

    # ---- 技术池配额装配（R100z221 重排：必须晚于 picks 定稿）----
    # 原实现把配额装配放在 picks 之前，去重只用 `_used`（技术池内部），**看不到断板池已经选走了谁**；
    # 而陈学长严格池 16 只里有 13 只同时命中断板池 → 配额给它的 2 个座位几乎每次都被
    # 合并段那句 `if t["code"] in pool_codes: continue` 挡掉，实际贡献 0 席
    # （用户 2026-10-26「开盘精选池子里也记得加入陈学长技术指标池，因为对胜率有帮助」＝没兑现）。
    # 现在先剔掉断板池已选走的票，再按 横盘启动→谐波→吸筹→陈学长 的顺序填配额，配额才真能兑现。
    # 开盘深跌(<-2%)/涨停(≥9.5%)的剔除也一起上移：原来被剔的票仍然占着一个配额位、不顺位递补，
    # 等于同一个池的座位凭空少几个（行情一差就整池消失）。
    pool_codes = {p["code"] for p in picks}
    tech, _used = [], set()
    # ⚠️ 配额合计必须 ≤ 下面切片上限：R100z85 的教训是「合并后按 score 降序取前 N」会让
    # 量纲最大的池垄断名额，配额等于白写。所以这里配额和 = 10，切片也必须是 [:10]；
    # 加池要同时改这两个数，只改一个就会静默把最后一个池全砍掉。
    for _lst, _quota in ((bp_list, 3), (hz_list, 3), (acc_list, 2), (chen_list, 2)):
        _cnt = 0
        for _t in _lst:
            if _cnt >= _quota:
                break
            if _t["code"] in _used or _t["code"] in pool_codes:
                continue
            if blacklist.get(_t["code"]) == "red":
                continue  # R100z9y：硬风险黑名单不进精选，且不占配额（顺位递补）
            _lv = (quotes.get(_t["code"]) or {}).get("pct")
            if _lv is not None and _lv < -2.0:
                continue  # 开盘深跌的形态标的暂不进精选（不占配额，顺位递补）
            if _lv is not None and _lv >= 9.5:
                continue  # 开盘即涨停（买不进），推荐无意义（不占配额，顺位递补）
            _used.add(_t["code"])
            tech.append(_t)
            _cnt += 1
    tech.sort(key=lambda x: -x["score"])
    tech = tech[:10]

    # ---- R100m（需求7）：技术池候选并入精选（排在断板池之后）----
    for t in tech:
        if t["code"] in pool_codes or len(picks) >= 12:
            continue
        q = quotes.get(t["code"]) or {}
        live = q.get("pct")
        # 深跌(<-2%) / 涨停(≥9.5%) 的剔除已在上面装配段做过（那里才有顺位递补），
        # 这里不再重复判一遍——同一条件写两处，改一处就会静默分叉。
        note = f"{t['label']}·前日16:00筛选"
        _gt, _gzh = gap_tag(q.get("openPct"))
        if q.get("openPct") is not None:
            note += f"·开盘{q['openPct']:+.1f}%({_gzh})"
        if live is not None:
            note += f"·现涨{live:+.1f}%"
        picks.append({"code": t["code"], "name": t["name"], "sector": t["sector"],
                      # R100z221：技术池条目自带情绪（陈学长池有 sentiment，谐波/横盘启动/吸筹没有 →
                      # 落到 neutral）。以前这里写死 "neutral"，会把陈学长的利空票也标成中性；
                      # 利空票在 chen_list 构造时已经剔除，这里只是不覆盖真值。
                      "sentiment": t.get("sentiment") or "neutral", "probability": None,
                      # R100z192：技术池同样标注跳空——但**它不是断板池那套结论**（买点逻辑不同），
                      # 所以只标注、不据此剔除；`src` 让前端/推送能区分两种口径。
                      "gapTag": _gt, "gapZh": _gzh, "openPct": q.get("openPct"),
                      # R100z170：技术池标的补板块涨幅（名字精确命中同花顺一级行业才写）
                      "boardPctToday": (sec_map.get(t["sector"]) or {}).get("pct"),
                      "bkCode": (sec_map.get(t["sector"]) or {}).get("code"),
                      "note": note, "src": t["src"]})

    # ---- R100z193（2026-10-24）：分时量价结构诊断 ----
    # 用户原话：「竞价往往都是虚的，9:25 之前都可以撤单……可以看开盘的分时量：
    # 下跌有量且没有承接就不看好；下跌无量、跌破分时线有承接可以观察；开盘爆量拉升也可观察」。
    # 上一版把「竞价跳空」当动作依据（gapTag/gapSummary），用户明确反对，故把解释权
    # 交给 tape.py：用腾讯 1 分钟分时算均价线、跌破次数、下跌分钟量占比、爆量倍数，
    # 输出「此刻的盘口结构 + 纪律档位」。gapTag 保留但降级为价格事实，不再定动作。
    # 失败不阻断选股：picks 只是不带 tape 字段，AI 侧按「结构未知」处理。
    tape_summary = None
    try:
        import tape as TAPE
        tm = TAPE.diagnose_codes([p["code"] for p in picks])
        for p in picks:
            t = tm.get(str(p["code"]))
            if t:
                p["tape"] = {k: v for k, v in t.items() if k != "name"}
                p["note"] += f"·分时[{t['zh']}]"
        tape_summary = TAPE.summarize(tm, picks)
        miss = [p["code"] for p in picks if str(p["code"]) not in tm]
        if miss:
            tape_summary["missing"] = miss
            tape_summary["text"] += (f"（{len(miss)} 只分时抓空：{'、'.join(miss)}，"
                                     "未给结构判定，严禁猜方向）")
            # R100z221：抓空的票也落 tape 键（= 未知），不留空键（见 _tape_unknown 注释）。
            # 同样用 setdefault：tm 里没有该票时正常不会走到覆盖，但保持与异常分支同一写法，
            # 将来 tape.py 若改成「有结果但为 None」，这里也不会把真值冲掉。
            for p in picks:
                if str(p["code"]) in miss:
                    p.setdefault("tape", _tape_unknown("分时接口未返回该票数据"))
        print(f"[tape] {tape_summary['text']}")
    except Exception as e:                                  # noqa: BLE001
        # R100z221：整体失败时也要显式降级 —— 以前是 picks 不带 tape、tapeSummary=None，
        # 前端整块「盘口结构」直接消失（用户看不出是故障），AI 也读不到「未知」两个字。
        print(f"[tape][WARN] 分时诊断失败，按「结构未知」显式降级：{type(e).__name__}: {e}")
        for p in picks:
            p.setdefault("tape", _tape_unknown(f"分时诊断失败：{type(e).__name__}"))
        tape_summary = _tape_summary_unknown(picks, f"{type(e).__name__}")

    # ---- 文案模板（AI 复核改写 sentiment/pushText） ----
    now = datetime.now()
    market_line = "，".join(
        (f"{x['name']}{x['pct']:+.2f}%（开盘{x['openPct']:+.2f}%）"
         if x.get("openPct") is not None else f"{x['name']}{x['pct']:+.2f}%")
        for x in idx) if idx else "指数快照获取失败"
    if idx:
        total_amt = sum(x.get("amount") or 0 for x in idx)
        market_line += f"，半小时两市成交约 {total_amt:.0f} 亿"
    if degraded:  # R100z156：marketLine 也要可见地标出缺口，别让页面看不出是取数故障
        market_line += "（⚠️ 行业/资金接口抓空，板块判定本次不参与）"
    top3 = "、".join(f"{s['name']}{s['pct']:+.2f}%" for s in sectors[:3]) or "—"
    bot3 = "、".join(f"{s['name']}{s['pct']:+.2f}%" for s in sectors[-3:]) or "—"
    fi_txt = "、".join(f"{x['name']}{x['value']:+.1f}亿" for x in (fund_in or [])) or "—"
    fo_txt = "、".join(f"{x['name']}{x['value']:+.1f}亿" for x in (fund_out or [])) or "—"
    # R99c：只写开盘后板块情绪与资金情绪（集合竞价强弱关系由 AI 写入 pushText）
    # R100z156：数据缺口时首句直接点明，并把「合格板块」从「无」改成「本次不判定」——
    # AI 改写 sentiment 时会读到这句，避免它把取数故障写成「板块无机会」。
    gap_head = ""
    if degraded:
        gap_head = (f"⚠️ 数据缺口：{gap_reason}。板块涨幅与主力净流入两项判据本次缺失，"
                    "按纪律不做板块挂钩判定（断板池标的不参与精选），picks 仅含技术池形态标的。"
                    "严禁把「接口抓空」写成「无合格板块」或「板块无机会」。")
        board_line = "合格板块：判据缺失，本次不判定。"
    else:
        board_line = f"合格板块（涨+净流入双正向）：{'、'.join(n for n, _, _ in good_boards) or '无'}。"
    sentiment = (gap_head
                 + f"开盘板块情绪：领涨 {top3}；领跌 {bot3}。"
                 f"资金情绪：主力净流入前列 {fi_txt}；净流出前列 {fo_txt}。"
                 + board_line
                 + "（AI 复核：请改写为完整开盘情绪判断与对断板反包标的的影响分析）")

    # R100z156：降级时预置 pushText 兜底——页面「🌟 开盘精选」正文与微信推送共用此字段，
    # 前端只渲染 pushText（不渲染 marketLine/sentiment），AI 若漏改写或改跑失败，
    # 用户仍能看到「这是取数故障」而不是一行空白。正常情况仍留空交 AI 写。
    push_text = "" if not degraded else (
        "⚠️ 本次开盘精选存在数据缺口：同花顺行业涨幅与主力资金接口连续两次抓取失败，"
        "板块情绪与资金流向两项判据缺失。脚本按既定纪律不做板块挂钩判定，"
        "因此断板反包池标的全部不参与本次精选，仅保留前一日 16:00 数据步筛出的"
        "形态类技术池标的（横盘启动/谐波/吸筹）。指数与个股实时涨跌取自腾讯行情，正常可用。"
        "此为取数故障，不代表当日「无合格板块」或市场无机会，请勿据此判断强弱；"
        "可结合盘中资金回流情况另行观察，待接口恢复后按常规口径重跑。"
    )

    # ---- R100z232：今日买入清单（用户第 4 项）----
    # 放在 picks 定稿之后、star 组装之前：它读的是 picks（断板池已过双闸）+ 三个池的
    # 原始条目（横盘启动/陈学长 的买区字段只在池条目里，picks 是精简副本）。
    # 失败不阻断：清单拿不到就只少一块，picks/pushText 照常出。
    buy_rows, buy_summary = [], None
    try:
        buy_rows, buy_summary = build_buy_list(D, picks, quotes, blacklist)
    except Exception as _e:                                   # noqa: BLE001
        buy_summary = {"n": 0, "nBuy": 0, "bySrc": {},
                       "text": "买入清单生成失败（%s）——这是脚本故障，不代表今日无买点。" % _e,
                       "rule": "", "skipped": [], "slipPct": BUY_SLIP}

    # ---- R101：时间口挂单清单的开盘核对（用户 2026-10-27 第 1 问「能下单」）----
    # 清单由 16:00 的 timing_plan.py 生成，这里**只核对开盘价有没有破位**。
    # 与上面 buyList 的分工：buyList 回答「此刻现价在不在买区」（9:45 看盘口径），
    # timingExec 回答「昨晚挂的单今天能不能成、要不要撤」（挂单口径）。两者并存、互不替代。
    timing_exec = None
    try:
        timing_exec = build_timing_exec(D, quotes)
    except Exception as _e:                                   # noqa: BLE001
        timing_exec = {"rows": [], "n": 0, "nGo": 0, "nBroken": 0, "nMissed": 0,
                       "rule": "挂单清单核对失败（%s）——这是脚本故障，不是今日无单。" % _e,
                       "riskNote": "", "bt": {}, "asOf": "", "time": ""}

    star = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "marketLine": market_line,
        "sentiment": sentiment,
        "picks": picks,
        "buyList": buy_rows,
        "buySummary": buy_summary,
        "timingExec": timing_exec,
        "gapSummary": _gap_summary(picks),
        "tapeSummary": tape_summary,
        "pushText": push_text,
        "generatedBy": "update_star.py 确定性筛选（R99c：断板池板块情绪正向+资金净流入正向挂钩，按上涨概率降序；R100m：并从谐波/吸筹池直接取标的；R100z85：技术池按池配额 防单池量纲垄断；R100z232：九门下线换横盘启动（配额 横盘启动3/谐波3/吸筹2）+ 新增 star.buyList 今日买入清单（三池买区/证伪价/目标/持有天数/仓位，不含离场提示）；R100z156：行业/资金接口抓空重试+显式降级；R100z192：picks 增 gapTag 开盘价位置标注 + star.gapSummary 分布，只标注不排序；R100z193：接入 tape.py 分时量价结构（均价线/跌破次数/下跌分钟量占比/爆量倍数），picks 增 tape 字段 + star.tapeSummary，动作口径由盘口结构决定，gapTag 降级为价格事实；R101：新增 star.timingExec——读 16:00 timing_plan.py 生成的挂单清单，用 9:45 实时行情核对开盘是否破位，只核对不改单） + 自动化 AI 复核",
    }
    return star, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印不写回")
    args = ap.parse_args()
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.js")
    base = os.path.dirname(data_path)
    _, D = load_data(data_path)
    star, err = build_star(D, base)
    if err:
        print(f"[FAIL] {err}，不写入")
        sys.exit(1)
    print(f"[star] {star['date']} {star['time']} | 精选 {len(star['picks'])} 只:")
    for p in star["picks"]:
        _pv = p["probability"]
        print(f"  {p['code']} {p['name']} | {p['note']} | "
              + (f"score源概率 {_pv}%" if _pv is not None else "score源概率 未给"))
    print("[star] marketLine:", star["marketLine"])
    # R100z232：今日买入清单（用户第 4 项）——落库前先在日志里打出来，
    # 出问题时能一眼分清「没买点」和「清单生成失败」。
    _bs = star.get("buySummary") or {}
    print("[star] 买入清单 %d 只（在买区内 %d 只）: %s"
          % (_bs.get("n") or 0, _bs.get("nBuy") or 0, _bs.get("text") or ""))
    for r in (star.get("buyList") or []):
        print("   %-6s %-8s %-14s 买区 %.2f~%.2f | 现价 %s | %s | 证伪 %s | 目标 %s | 持 %s"
              % (r["code"], r["name"][:8], r["srcZh"],
                 r["low"] or 0, r["high"] or 0,
                 ("%.2f" % r["price"]) if r["price"] is not None else "—",
                 r["stateZh"],
                 ("%.2f" % r["invalid"]) if r["invalid"] is not None else "—",
                 ("%.2f" % r["target"]) if r["target"] is not None else "—",
                 ("%d日" % r["holdDays"]) if r["holdDays"] else "—"))
    if args.dry:
        print("[DRY-RUN] 未写回")
        return
    s, D = load_data(data_path)
    D.setdefault("duanban", {})["star"] = star

    # R100z190：强板块落后股（lead_lag.py）。与精选同一时点、同一份盘面快照，
    # 但选股方向相反：精选买「已经强、概率高」的，这里买「同一强势板块里还没跟上」的。
    # 为什么要并进 9:45 这一步：这条规则的入场条件（开盘跳空 + 现价偏离）只在
    # 开盘半小时内成立，晚一步买入滑点就吃掉全部超额（回测：+0.8% 滑点 → 期望归零）。
    # 失败不能连累 star：精选已经算完了，这块只是加分项。
    ll = None
    try:
        import lead_lag as LL
        ll, llerr = LL.compute(D)
        if llerr:
            print(f"[leadLag][WARN] {llerr}")
            ll = None
    except Exception as e:                                  # noqa: BLE001
        print(f"[leadLag][WARN] 计算失败，跳过（不影响精选）：{type(e).__name__}: {e}")
        ll = None
    if ll:
        bt, bterr, hit = LL.backtest_cached(D)
        if bt:
            m, al = bt["main"], bt["alt"]
            better = al if (al.get("tDaily") or -99) > (m.get("tDaily") or -99) else m
            ll["bt"] = bt
            ll["exit"] = {"main": better, "alt": (m if better is al else al)}
            ll["span"] = {"days": bt["days"], "samples": bt["samples"],
                          "signalDays": bt["signalDays"]}
            print(f"[leadLag] 回测{'(缓存)' if hit else '(已重算)'} "
                  f"{bt['signalDays']}/{bt['days']} 日有信号，"
                  f"{better['name']} {better['ret']:+.2f}% 胜率 {better['win']*100:.0f}% "
                  f"t日聚类={better['tDaily']}")
        else:
            print(f"[leadLag][WARN] 回测未完成：{bterr}")
        ll["mode"] = "live"
        D.setdefault("duanban", {})["leadLag"] = ll
        print(f"[leadLag] {ll['date']} {ll['time']} | 候选 {len(ll['picks'])} 只"
              f"（强板块 {ll['scanned']['strongSectors']} 个 / 扫描 {ll['scanned']['quoted']} 只）")
        for p in ll["picks"]:
            print(f"  {p['code']} {p['name']} | {p['note']}")
        if not ll["picks"]:
            print(f"  （空仓）{ll.get('gapNote') or ''}")

    out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ";\n"
    # R100z179（L11 硬约束）：先写临时文件、node --check 通过再 os.replace。
    # 原写法 open(data_path,'w') 先截断再写、写完才 check——进程若在 write 中途被杀
    # （平台 90 分钟硬上限 / 超时重试），留下的是残缺 data.js，而 check 这一步
    # 根本没机会执行。临时名必须带 .js，node 22 拒绝未知扩展名。
    _tmp = data_path + ".tmp.js"
    with open(_tmp, "w", encoding="utf-8") as f:
        f.write(out)
    _chk = subprocess.run([NODE, "--check", _tmp], capture_output=True, text=True, timeout=30)
    if _chk.returncode != 0:
        os.remove(_tmp)
        raise SystemExit("[FAIL] star 写回未通过 node --check，已丢弃、data.js 保持原样：\n"
                         + (_chk.stderr or "")[:800])
    os.replace(_tmp, data_path)
    print("[star] data.js 写回完成，node --check 通过")
    # 台账登记放在写盘成功之后：登记了却没上页面 = 台账里多出一批「用户没见过的票」，
    # 后面算胜率时会把它们算成「规则的表现」，其实一次都没出场。
    if ll:
        try:
            import lead_lag as LL
            n = LL.register(ll)
            print(f"[leadLag] 台账登记 {n} 条（signal_ledger.entries.{ll['date']}.leadLag）")
        except Exception as e:                              # noqa: BLE001
            print(f"[leadLag][WARN] 台账登记失败（不影响页面）：{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()

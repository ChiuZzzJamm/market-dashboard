#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场结构面：宽度 / 拥挤度 / 连板空间（含 5 个交易日趋势）。

背景（R100z157）：A 股收盘后只有「涨跌家数 + 涨停跌停」一行平铺，没有结构判断；
美股侧有 outlook 的风险维度，A 股侧缺一个对称的、可查证的量化小节。本脚本把
ashare.breadth / ashare.lianban 里已有的字段换算成结论性指标，供前端在指数卡下方
展示，并让 17:00 叙事步 AI 有确定性的结构事实可引用（避免它自由发挥「情绪偏暖」）。

铁律：
1) 全部字段由 data.js 现有数据确定性推导，不抓新数据源、不联网、不猜。
2) 只写 ashare.structure 一个节点，data.js 用外科式字符串替换（不整文件重排）。
3) 任何异常都不阻断流水线：退出码恒 0，失败只打印 [warn]。

历史台账 market_structure.json：{"log": {"YYYY-MM-DD": {指标...}}}，按 tradeDate 记，
上限 120 个交易日，用于「较 5 个交易日前」的趋势对比。周末/重跑幂等（同键覆盖）。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "market_structure.json")
MAX_LOG = 120


def load_data():
    """返回 (data_dict, raw_text)；失败返回 (None, None)。只读，不落盘。"""
    try:
        with open(os.path.join(BASE, "data.js"), encoding="utf-8") as f:
            s = f.read()
        m = re.search(r"window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$", s)
        if not m:
            return None, None
        return json.loads(m.group(1)), s
    except Exception as e:
        print("[warn] 读取 data.js 失败：%s" % e)
        return None, None


def load_ledger():
    try:
        with open(LEDGER, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("log"), dict):
            return d
    except Exception:
        pass
    return {"log": {}}


def save_ledger(led):
    log = led.get("log") or {}
    if len(log) > MAX_LOG:
        for k in sorted(log)[: len(log) - MAX_LOG]:
            log.pop(k, None)
    try:
        with open(LEDGER, "w", encoding="utf-8") as f:
            json.dump(led, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return True
    except Exception as e:
        print("[warn] 写 market_structure.json 失败：%s" % e)
        return False


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _fy(v, nd=2):
    """数值格式化：None → '—'。"""
    if v is None:
        return "—"
    return ("%." + str(nd) + "f") % v


def zone_of(ratio):
    """按涨跌家数比划分广度区间（阈值是经验口径，写死在此处保证可复现）。"""
    if ratio is None:
        return None
    if ratio >= 3.0:
        return "普涨"
    if ratio >= 1.5:
        return "偏强"
    if ratio >= 0.67:
        return "均衡"
    if ratio >= 0.33:
        return "偏弱"
    return "普跌"


def parse_amt_yi(text):
    """'成交约 2.10 万亿' → 21000（亿元）；'成交约 8900 亿' → 8900。"""
    if not isinstance(text, str):
        return None
    m = re.search(r"([\d.]+)\s*万亿", text)
    if m:
        try:
            return round(float(m.group(1)) * 10000, 1)
        except Exception:
            return None
    m = re.search(r"([\d.]+)\s*亿", text)
    if m:
        try:
            return round(float(m.group(1)), 1)
        except Exception:
            return None
    return None


def compute_metrics(a):
    """从 ashare 节点算出结构指标；数据不足时返回 None。"""
    if not isinstance(a, dict):
        return None
    b = a.get("breadth") or {}
    up, down = _num(b.get("up")), _num(b.get("down"))
    if up is None or down is None:
        return None
    lu, ld = _num(b.get("limitUp")), _num(b.get("limitDown"))

    m = {"up": usafe(up), "down": down, "flat": _num(b.get("flat"))}
    m["upDownRatio"] = round(up / down, 2) if down else None
    m["zone"] = zone_of(m["upDownRatio"])
    m["limitUp"] = lu
    m["limitDown"] = ld
    m["ztDtRatio"] = round(lu / ld, 2) if (lu is not None and ld) else None
    m["amtYi"] = parse_amt_yi(b.get("volumeText"))

    lb = [x for x in (a.get("lianban") or []) if isinstance(x, dict)]
    if lb:
        heights = [int(x.get("lbc") or 1) for x in lb]
        m["maxLb"] = max(heights)
        tops = [str(x.get("name") or "") for x in lb
                if int(x.get("lbc") or 1) == m["maxLb"] and x.get("name")]
        m["maxLbName"] = "、".join(tops[:3])
        m["lianbanCount"] = len(lb)
        # 分歧度：同花顺涨停池 open_num>2 判为分歧（update_ashare_sectors 写入 phase）
        div = sum(1 for x in lb if x.get("phase") == "分歧")
        m["divCount"] = div
        m["divRatio"] = round(div / len(lb), 2) if lb else None
        # 接力拥挤度：连板家数 / 涨停家数（>0.5 说明赚钱效应集中在高位接力）
        if lu:
            m["relayRatio"] = round(len(lb) / lu, 2)
    else:
        for k in ("maxLb", "maxLbName", "lianbanCount", "divCount",
                  "divRatio", "relayRatio"):
            m[k] = None
    return m


def usafe(v):
    """整数原样返回（避免 float 化导致 data.js 出现 3120.0）。"""
    try:
        iv = int(v)
        return iv if iv == v else v
    except Exception:
        return v


def trend(cur, log, cur_date):
    """与最近 N（≤5）个交易日对比，返回 (height_delta, ratio_delta, amt_delta, ref_date, n)。"""
    prevs = sorted([d for d in log if d < cur_date])
    if not prevs:
        return None, None, None, None, 0
    picks = prevs[-5:]
    ref = picks[0]
    old = log.get(ref) or {}
    dh = dr = da = None
    if _num(cur.get("maxLb")) is not None and _num(old.get("maxLb")) is not None:
        dh = int(cur["maxLb"]) - int(old["maxLb"])
    if _num(cur.get("upDownRatio")) is not None and _num(old.get("upDownRatio")) is not None:
        dr = round(cur["upDownRatio"] - old["upDownRatio"], 2)
    if _num(cur.get("amtYi")) is not None and _num(old.get("amtYi")) is not None:
        da = round(cur["amtYi"] - old["amtYi"], 1)
    return dh, dr, da, ref, len(picks)


def build_text(m, dh, dr, da, ref, n):
    """确定性生成结构面一句话（无 AI 参与，重跑结果恒等）。"""
    parts = []
    if m.get("upDownRatio") is not None:
        parts.append("涨跌家数比 %s（%s）" % (_fy(m["upDownRatio"]), m.get("zone") or "—"))
    else:
        parts.append("涨跌家数比 —")
    if m.get("limitUp") is not None or m.get("limitDown") is not None:
        s = "涨停 %s 家 / 跌停 %s 家" % (
            m["limitUp"] if m["limitUp"] is not None else "—",
            m["limitDown"] if m["limitDown"] is not None else "—")
        if m.get("ztDtRatio") is not None:
            s += "（%s:1）" % _fy(m["ztDtRatio"], 1)
        parts.append(s)
    if m.get("lianbanCount"):
        s = "连板 %d 只、最高 %d 板" % (m["lianbanCount"], m["maxLb"])
        if m.get("maxLbName"):
            s += "（%s）" % m["maxLbName"]
        parts.append(s)
        if m.get("divCount") is not None:
            parts.append("高位分歧 %d 只（%.0f%%）" % (m["divCount"], (m["divRatio"] or 0) * 100))
    body = "；".join(parts) + "。"
    tail = ""

    if ref and (dh is not None or dr is not None):
        seg = []
        if dr is not None:
            seg.append("涨跌比 %+.2f" % dr)
        if dh is not None:
            seg.append("连板高度 %+d 板" % dh)
        if da is not None:
            seg.append("成交额 %+.0f 亿" % da)
        tail += "较 %d 个交易日前（%s）：%s。" % (n, ref, "、".join(seg))

    # 结论句：按「广度 × 分歧度 × 拥挤度」的优先顺序给唯一判断，避免并列多结论
    r = m.get("upDownRatio")
    div_ratio = m.get("divRatio")
    if div_ratio is not None and m.get("lianbanCount") and div_ratio >= 0.4:
        tail += "高位分歧加剧，接力性价比下降，次日需防溢价回落。"
    elif (m.get("relayRatio") or 0) >= 0.5 and (m.get("maxLb") or 0) >= 3:
        tail += "赚钱效应集中在高位接力，情绪偏亢奋，防一致性兑现。"
    elif r is not None and r >= 1.5 and (m.get("limitUp") or 0) >= 40:
        tail += "普涨格局下情绪扩散，首板补涨优于追高。"
    elif r is not None and r < 0.5:
        tail += "普跌且赚钱效应弱，宜降仓等缩量企稳。"
    else:
        tail += "结构性行情，胜负手在板块而非指数。"
    # text=完整句（供 AI 引用/复制）；brief=趋势+结论（前端指标行已经显示数字，只留判断，避免同卡重复数字）
    return body + tail, tail



BASIS = ("口径：涨跌家数=同花顺行业页 up/down 求和（含 ST）；涨停=同花顺涨停池 total、"
         "跌停=东财跌停池；连板/分歧=同花顺涨停池主板池（open_num>2 记分歧）；"
         "趋势基准=台账中最近 5 个交易日里最早的一天")


def write_structure(raw, node):
    """把 node 外科式写进 ashare（存在则替换，不存在则紧跟 breadth 之后插入）。"""
    key = '"structure":' + json.dumps(node, ensure_ascii=False, separators=(",", ":"))
    # 定位 ashare 对象范围
    i = raw.find('"ashare":{')
    if i < 0:
        print("[warn] data.js 未找到 ashare 节点")
        return None
    start = raw.find("{", i)
    depth, j = 0, start
    in_str, esc = False, False
    while j < len(raw):
        c = raw[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
        j += 1
    if depth != 0:
        print("[warn] ashare 对象括号未配对")
        return None
    seg = raw[start:j + 1]

    old = re.search(r'"structure":\{"[^"]*"', seg)
    if old:
        # 已有 structure：整段替换（要求旧节点是 JSON 对象，非 JS 字面量）
        k0 = seg.find('"structure":')
        v0 = seg.find("{", k0)
        d2, e = 0, v0
        in_str, esc = False, False
        while e < len(seg):
            c = seg[e]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    d2 += 1
                elif c == "}":
                    d2 -= 1
                    if d2 == 0:
                        break
            e += 1
        if d2 != 0:
            print("[warn] 旧 structure 节点括号未配对，改走插入路径")
            old = None
        else:
            new_seg = seg[:k0] + key + seg[e + 1:]
            new_raw = raw[:start] + new_seg + raw[j + 1:]
            return _verify(new_raw, node)
    # 插入：紧跟 breadth 对象之后
    kb = seg.find('"breadth":')
    if kb < 0:
        print("[warn] ashare 内未找到 breadth，无法定位插入点")
        return None
    vb = seg.find("{", kb)
    d3, e = 0, vb
    in_str, esc = False, False
    while e < len(seg):
        c = seg[e]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                d3 += 1
            elif c == "}":
                d3 -= 1
                if d3 == 0:
                    break
        e += 1
    if d3 != 0:
        print("[warn] breadth 对象括号未配对")
        return None
    new_seg = seg[:e + 1] + "," + key + seg[e + 1:]
    new_raw = raw[:start] + new_seg + raw[j + 1:]
    return _verify(new_raw, node)


def _verify(new_raw, node):
    """落盘前解析校验：新文本必须可解析且 structure 与预期一致。"""
    m = re.search(r"window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$", new_raw)
    if not m:
        print("[warn] 替换后文本不匹配 DASHBOARD_DATA，放弃写入")
        return None
    try:
        d = json.loads(m.group(1))
    except Exception as e:
        print("[warn] 替换后 JSON 解析失败（%s），放弃写入" % e)
        return None
    got = (d.get("ashare") or {}).get("structure")
    if not isinstance(got, dict) or got.get("text") != node.get("text"):
        print("[warn] 替换后 structure 校验不一致，放弃写入")
        return None
    return new_raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印，不写 data.js / 台账")
    args = ap.parse_args()

    D, raw = load_data()
    if D is None:
        return 0
    a = D.get("ashare") or {}
    cur_date = str(a.get("tradeDate") or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", cur_date):
        print("[warn] ashare.tradeDate 非法（%r），跳过结构面计算" % cur_date)
        return 0
    m = compute_metrics(a)
    if m is None:
        print("[warn] 涨跌家数缺失，跳过结构面计算")
        return 0

    led = load_ledger()
    dh, dr, da, ref, n = trend(m, led.get("log") or {}, cur_date)
    node = dict(m)
    node.update({"date": cur_date, "height5": dh, "ratio5": dr, "amt5": da,
                 "trendRef": ref, "trendN": n, "basis": BASIS})
    node["text"], node["brief"] = build_text(m, dh, dr, da, ref, n)
    print("[info] 结构面：%s" % node["text"])
    if args.dry:
        print("[dry] 节点：" + json.dumps(node, ensure_ascii=False))
        return 0

    # 1) 台账先记（趋势依赖它；写失败不影响 data.js 落盘）
    log = led.setdefault("log", {})
    log[cur_date] = {k: m.get(k) for k in
                     ("up", "down", "upDownRatio", "limitUp", "limitDown",
                      "ztDtRatio", "maxLb", "lianbanCount", "divCount",
                      "divRatio", "relayRatio", "amtYi")}
    save_ledger(led)

    # 2) data.js 外科式写入并复验（全字段比对，避免新增字段时误判「无变化」）
    _old = a.get("structure") or {}
    if len(_old) == len(node) and all(_old.get(k) == node.get(k) for k in node):
        print("[info] structure 无变化，跳过写盘")
        return 0
    new_raw = write_structure(raw, node)
    if not new_raw:
        return 0
    try:
        with open(os.path.join(BASE, "data.js"), "w", encoding="utf-8") as f:
            f.write(new_raw)
    except Exception as e:
        print("[warn] 写 data.js 失败：%s" % e)
        return 0
    # 3) 落盘复验（读回确认）
    D2, _ = load_data()
    ok = isinstance(((D2 or {}).get("ashare") or {}).get("structure"), dict)
    print("[info] ashare.structure 已写入（%s）" % ("复验通过" if ok else "复验失败！"))
    print("[info] 完成于 %s" % datetime.now().strftime("%H:%M:%S"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # 绝不阻断流水线
        print("[warn] market_structure 异常（已忽略）：%s" % e)
        sys.exit(0)

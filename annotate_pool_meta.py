#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 aiPrediction 标注「机械候选 vs 自选补位」（R100z157，修优化-4）。

背景：08:30 的 AI 预测跑在盘前，当日机械池还没重算，只能吃前一交易日 16:00 入库的
stock_pool.json。实测 6 个板块候选数 1/1/0/6/0/2，AI 于是大量「自选标的」补位，
页面上却看不出哪些票是机械选出来的、哪些是 AI 自己挑的，stopBasis 里那句
「（自选标的，本任务设定）」也埋在弹窗里。这架空了 build_stock_pool「AI 交出选择权」
的设计意图，用户事后无法核验。

本脚本做三件确定性的事（零网络、不判断好坏、只如实标注）：
1) 每只标的打 `poolHit`（是否出现在当日 stock_pool.json 的该板块候选里）；
2) aiPrediction 增加 `poolMeta`（候选快照日期、每板块候选数/命中数/自选数、汇总与有效期说明）；
3) 把快照日期与「今日 16:00 重算后自动替换」写进 note，让「有效期」在页面可见。

铁律：读不到 stock_pool.json 也照写（标 null 并说明缺池），任何异常 exit 0，绝不阻断部署。
"""
import argparse
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
POOL = os.path.join(BASE, "stock_pool.json")


def load_data():
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


def load_pool():
    try:
        with open(POOL, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception as e:
        print("[warn] 读取 stock_pool.json 失败（按缺池处理）：%s" % e)
    return None


def norm(s):
    """板块名归一：去空格/中点/·/・/间隔号，避免「半导体设备·国产替代」与「半导体设备· 国产替代」错配。"""
    return re.sub(r"[\s·・\-—]+", "", str(s or ""))


def find_sector(by_sector, name):
    """按归一化名找机械池里的板块；再退化为互相包含（AI 爱加后缀）。"""
    if not isinstance(by_sector, dict):
        return None, None
    n = norm(name)
    for k, v in by_sector.items():
        if norm(k) == n:
            return k, v
    for k, v in by_sector.items():
        nk = norm(k)
        if nk and (nk in n or n in nk) and abs(len(nk) - len(n)) <= 6:
            return k, v
    return None, None


def build_meta(ap, pool):
    """返回 (poolMeta, hit_map{code: bool})。pool 为 None 时全部标 poolHit=None。"""
    sectors = [s for s in (ap.get("sectors") or []) if isinstance(s, dict)]
    by = (pool or {}).get("bySector") or {}
    rows, hit_total, out_total, picked_total = [], 0, 0, 0
    hit_map = {}
    for s in sectors:
        name = s.get("sector")
        _k, cands = find_sector(by, name)
        cands = [c for c in (cands or []) if isinstance(c, dict) and c.get("code")]
        codes = {str(c["code"]) for c in cands}
        stocks = [x for x in (s.get("stocks") or []) if isinstance(x, dict)]
        hit = outside = 0
        for st in stocks:
            code = str(st.get("code") or "")
            if not code:
                continue
            ok = (code in codes) if pool is not None else None
            st["poolHit"] = ok
            hit_map[code] = ok
            if ok:
                hit += 1
            elif ok is False:
                outside += 1
        rows.append({"sector": name, "poolN": len(codes), "picked": len(stocks),
                     "hit": hit, "outside": outside})
        hit_total += hit
        out_total += outside
        picked_total += hit + outside
    meta = {
        "poolDate": (pool or {}).get("date"),
        "generatedAt": (pool or {}).get("generatedAt"),
        "sectors": rows,
        "hitTotal": hit_total,
        "outsideTotal": out_total,
        "pickedTotal": picked_total,
    }
    if pool is None:
        meta["level"] = "missing"
        meta["note"] = ("机械候选池缺失（stock_pool.json 未产出）：本轮 %d 只标的均为 AI 自选补位，"
                        "止损按「最近收盘 −3%%」纪律位设定，待下次数据步重建机械池后自动替换。" % picked_total)
        return meta, hit_map
    dist = "/".join(str(r["poolN"]) for r in rows)
    thin = [r["sector"] for r in rows if r["poolN"] < 3]
    meta["level"] = "full" if out_total == 0 else ("self" if hit_total == 0 else "partial")
    parts = ["机械候选快照：%s" % (meta["poolDate"] or "日期缺失")]
    if meta.get("generatedAt"):
        parts[-1] += "（%s 生成）" % meta["generatedAt"]
    parts.append("%d 个预测板块的候选数依次为 %s" % (len(rows), dist or "—"))
    if hit_total:
        parts.append("本轮 %d 只标的中 %d 只命中机械候选、%d 只为自选补位" % (picked_total, hit_total, out_total))
    else:
        parts.append("本轮 %d 只标的均为自选补位" % picked_total)
    if thin:
        parts.append("其中 %d 个板块候选 <3 只（%s），按四类配比自选" % (len(thin), "、".join(thin)))
    parts.append("该候选集在下一个交易日 16:00 数据步按当日行情重算后自动替换（届时本行同步刷新）")
    meta["note"] = "；".join(parts) + "。"
    return meta, hit_map


def splice(raw, key, obj):
    """把 raw 里 "key":{...} 整段替换为 obj 的 JSON 文本（保留其余字符与顺序）。"""
    i = raw.find('"%s":{' % key)
    if i < 0:
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
        return None
    return raw[:start] + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + raw[j + 1:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印，不写 data.js")
    args = ap.parse_args()

    D, raw = load_data()
    if D is None:
        return 0
    pred = D.get("aiPrediction")
    if not isinstance(pred, dict) or not pred.get("sectors"):
        print("[warn] aiPrediction 缺失或无 sectors，跳过机械池标注")
        return 0
    pool = load_pool()
    # 旧值必须在改动前抓取：build_meta 会就地改 pred（加 poolHit/poolMeta）
    old = json.loads(json.dumps(pred.get("poolMeta") or {}))
    meta, _ = build_meta(pred, pool)
    pred["poolMeta"] = meta
    print("[info] " + meta["note"])
    if args.dry:
        print("[dry] poolMeta=" + json.dumps(meta, ensure_ascii=False)[:600])
        return 0

    # 旧值比对：全字段一致则跳过写盘（幂等，避免每次部署都动 6MB 文件）
    new_raw = splice(raw, "aiPrediction", pred)
    if not new_raw:
        print("[warn] 未能定位 aiPrediction 节点，跳过写盘")
        return 0
    m = re.search(r"window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$", new_raw)
    if not m:
        print("[warn] 替换后文本不匹配 DASHBOARD_DATA，放弃写入")
        return 0
    try:
        chk = json.loads(m.group(1))
    except Exception as e:
        print("[warn] 替换后 JSON 解析失败（%s），放弃写入" % e)
        return 0
    got = ((chk.get("aiPrediction") or {}).get("poolMeta") or {})
    if got.get("note") != meta.get("note") or got.get("outsideTotal") != meta.get("outsideTotal"):
        print("[warn] 替换后 poolMeta 校验不一致，放弃写入")
        return 0
    if old.get("note") == meta.get("note") and old.get("outsideTotal") == meta.get("outsideTotal"):
        # 内容一致时仍要保证 poolHit 已在（首跑补打标），只在确有差异时写盘
        need = any(isinstance(st, dict) and "poolHit" not in st
                   for s in (pred.get("sectors") or []) for st in (s.get("stocks") or []))
        if not need:
            print("[info] poolMeta 无变化，跳过写盘")
            return 0
    try:
        with open(os.path.join(BASE, "data.js"), "w", encoding="utf-8") as f:
            f.write(new_raw)
    except Exception as e:
        print("[warn] 写 data.js 失败：%s" % e)
        return 0
    D2, _ = load_data()
    ok = isinstance((((D2 or {}).get("aiPrediction") or {}).get("poolMeta")), dict)
    print("[info] aiPrediction.poolMeta 已写入（%s）" % ("复验通过" if ok else "复验失败！"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # 绝不阻断流水线
        print("[warn] annotate_pool_meta 异常（已忽略）：%s" % e)
        sys.exit(0)

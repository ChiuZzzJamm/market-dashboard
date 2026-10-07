#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把断板反包模块草稿合并进 data.js 的 duanban 字段（R87）。

用法：
  python3 merge_duanban.py duanban_draft.json        # 从草稿文件合并
  python3 merge_duanban.py duanban_draft.json --check-only  # 只校验不写回

草稿由 check_duanban.py --module --out 生成，自动化 AI 需在合并前补全：
  - probability（0-100 整数上涨概率，必填）
  - probNote（≤30字概率依据，建议填写）
  - 可删除 excluded 复核后认为不应进池的标的；也可把 excluded 中有明确利好
    语义依据的标的移入 confirmed/watching（须同步补 probability/bullRefs）
合并时机械校验：全部标的 60/00 开头沪深主板、code/name/sector/form/kline 齐全、
probability 为 0-100 数值（缺失时补 50 并告警）。写回后自动 node --check。
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta

MAINBOARD = re.compile(r"^(60|00)\d{4}$")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sanitize(mod):
    """机械校验+补救：返回 (干净模块, 告警列表)。"""
    warns = []
    for pool in ("confirmed", "watching"):
        kept = []
        for e in mod.get(pool) or []:
            code = str(e.get("code") or "")
            if not MAINBOARD.match(code):
                warns.append(f"[WARN] {pool}.{code} 非 60/00 沪深主板，已剔除")
                continue
            for k in ("name", "sector", "form", "stage"):
                if not e.get(k):
                    e[k] = "" if k != "sector" else "其他"
                    warns.append(f"[WARN] {pool}.{code} 缺 {k}，已补默认值")
            kl = e.get("kline")
            if not isinstance(kl, list) or not kl:
                warns.append(f"[WARN] {pool}.{code} 缺 kline（前端无K线图可画）")
            p = e.get("probability")
            if not isinstance(p, (int, float)) or not (0 <= p <= 100):
                # R98k：不再一律补 50（曾致全池 50%）——按情绪补确定性基线
                _d = {"bull": 69, "neutral": 50, "bear": 31}.get(
                    e.get("sentiment") or "neutral", 50)
                e["probability"] = _d
                if not str(e.get("probNote") or "").strip():
                    e["probNote"] = "脚本补基线（原值缺失，AI 可校准）"
                warns.append(f"[WARN] {pool}.{code} probability 缺失/非法，已按情绪补基线 {_d}")
            else:
                e["probability"] = round(float(p), 1)
            if not isinstance(e.get("story"), str):
                e["story"] = ""
                warns.append(f"[WARN] {pool}.{code} story 缺失/非法，已补空串")
            else:
                # R100z3 治本：story 段落标记归一化——AI 偶发把旗标/emoji 写进【个股消息🌍】
                # 等括号内（前端靠段首【个股消息】识别加粗与旗标，括号内杂质会导致渲染为黑字）。
                # 机械净化：【】内仅保留 个股消息/板块消息(+·关键词)，剥除 emoji/旗标/多余空白。
                _s0 = e["story"]

                def _norm_marker(m):
                    inner = re.sub(
                        r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\s]+", "", m.group(2))
                    return "【" + m.group(1) + inner + "】"

                _s1 = re.sub(r"【(个股消息|板块消息)([^】]*)】", _norm_marker, _s0)
                if _s1 != _s0:
                    e["story"] = _s1
                    warns.append(
                        f"[INFO] {pool}.{code} story 段落标记已归一化（【】内剥除旗标/emoji，R100z3）")
            if not e.get("bullRefs"):
                warns.append(f"[WARN] {pool}.{code} 无 bullRefs（利好依据为空，请复核）")
            kept.append(e)
        mod[pool] = kept
    mod["excluded"] = mod.get("excluded") or []
    mod.setdefault("note", "")
    mod.setdefault("generatedAt", "")
    return mod, warns


ENRICH_FIELDS = ("boardPctToday", "boardPctZt", "bkCode")


def _inherit_enrich(mod, old_mod, warns):
    """R91m：enrichment 字段（板块涨幅/板块代码）为环境依赖数据——
    本地沙箱取不到东财行情时这些字段是 None，若整体替换会抹掉自动化环境已填的值
    （线上弹窗「板块今日涨幅/涨停日板块涨幅」回退为「—」）。
    草稿某标的该字段为 None 且旧数据有值时，继承旧值；同名/前缀匹配板块。"""
    old_idx = {}
    for pool in ("confirmed", "watching"):
        for e in (old_mod or {}).get(pool) or []:
            if isinstance(e, dict) and e.get("code"):
                old_idx[str(e["code"])] = e
    n = 0
    for pool in ("confirmed", "watching"):
        for e in mod.get(pool) or []:
            old = old_idx.get(str(e.get("code") or ""))
            if not old:
                continue
            for k in ENRICH_FIELDS:
                if e.get(k) is None and old.get(k) is not None:
                    e[k] = old[k]
                    n += 1
    if n:
        warns.append(f"[INFO] 从旧数据继承 enrichment 字段 {n} 处"
                     f"（草稿环境取不到东财行情，防止覆盖自动化已填值）")


def main():
    ap = argparse.ArgumentParser(description="断板反包模块合并进 data.js")
    ap.add_argument("draft", help="模块草稿 JSON（check_duanban.py --module --out 产出，AI 补概率后）")
    ap.add_argument("--data", default=None, help="data.js 路径（默认同目录）")
    ap.add_argument("--check-only", action="store_true", help="只校验草稿不写回")
    args = ap.parse_args()

    import os
    data_path = args.data or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data.js")
    mod, warns = sanitize(load_json(args.draft))
    for w in warns:
        print(w, file=sys.stderr)
    n_ok = len(mod["confirmed"]) + len(mod["watching"])
    n_bear = sum(1 for pool in ("confirmed", "watching")
                 for e in mod[pool] if e.get("sentiment") == "bear")
    print(f"[merge] 确认池 {len(mod['confirmed'])} 只 + 观察池 {len(mod['watching'])} 只"
          f"（其中利空口径 {n_bear} 只）", file=sys.stderr)
    if args.check_only:
        return
    if n_ok == 0:
        print("[merge] 双池均为空：写入空模块（前端显示空态），如实反映", file=sys.stderr)

    with open(data_path, encoding="utf-8") as f:
        s = f.read()
    i = s.index("{")
    j = s.rindex("}")
    D = json.loads(s[i:j + 1])
    try:
        _inherit_enrich(mod, (D.get("duanban") or {}), warns)
    except Exception as e:
        print(f"[WARN] enrichment 继承失败（不影响合并）：{e}", file=sys.stderr)
    # R100z44：entryDate（入池首日标记）兜底——check_duanban 草稿已带，但 AI 补概率
    # 环节若在 draft.json 上整条目重写可能弄丢；缺时先按 code 从旧 data.js 继承，
    # 旧数据也没有（真·新入池标的）落 generatedAt 日期（= 当日，恰是首日，前端正好亮「新」）。
    try:
        _old_ed = {}
        for pool in ("confirmed", "watching"):
            for e in (D.get("duanban") or {}).get(pool) or []:
                if isinstance(e, dict) and e.get("code") and e.get("entryDate"):
                    _old_ed[str(e["code"])] = e["entryDate"]
        _gd = str(mod.get("generatedAt") or "")[:10]
        _n_ed = 0
        for pool in ("confirmed", "watching"):
            for e in mod.get(pool) or []:
                if not e.get("entryDate"):
                    e["entryDate"] = _old_ed.get(str(e.get("code") or "")) or _gd
                    _n_ed += 1
        if _n_ed:
            print(f"[merge] entryDate 兜底补齐 {_n_ed} 处（R100z44 入池首日标记）", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] entryDate 兜底失败（不影响合并）：{e}", file=sys.stderr)
    # R98k：保留 🌟 开盘半小时精选（9:45 自动化产物，16:00 重建 duanban 时不得洗掉）。
    #
    # R100z16 修 bug：原判定是「star.date == 今天」才保留，但 07:30 复核任务在 07:00 就跑
    # merge_duanban，比 9:45 早两个多小时——那天 09:30 生成的 star 此刻 date 是昨天，
    # 于是被当成「非当日」丢弃。实证事故：2026-09-30 9:45 写入 star(picks=2)，
    # 10-01 07:30 任务跑到这里把 star 删空，此后连续多天开盘精选模块空窗。
    # 前端本就有 R100e 闸门（star.date===本地今日才渲染），旧 star 不会被展示，
    # 所以改为「最近一个交易日的 star 一律保留」，由 9:45 整字段覆盖刷新，
    # 既不产生过期展示，也不会被早跑的复核脚本清场。
    old_star = (D.get("duanban") or {}).get("star")
    if old_star and not mod.get("star"):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        od = str(old_star.get("date") or "")
        if od >= yesterday:
            mod["star"] = old_star
            print(f"[merge] 保留 🌟 开盘精选模块（{od}，未过期未清场）", file=sys.stderr)
        else:
            print(f"[merge] 丢弃过期 🌟 模块（{od}，早于 {yesterday}），待 9:45 开盘精选自动化重建",
                  file=sys.stderr)
    D["duanban"] = mod
    out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ";\n"
    with open(data_path, "w", encoding="utf-8") as f:
        f.write(out)
    try:
        subprocess.run(["/Users/loccco/.workbuddy/binaries/run-node",
                        "--check", data_path], check=True,
                       capture_output=True, timeout=30)
        print("[merge] data.js 写回完成，node --check 通过", file=sys.stderr)
    except Exception:
        subprocess.run(["node", "--check", data_path], check=True, timeout=30)
        print("[merge] data.js 写回完成，node --check 通过（系统 node）", file=sys.stderr)


if __name__ == "__main__":
    main()

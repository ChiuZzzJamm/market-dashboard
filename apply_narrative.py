#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apply_narrative.py · 叙事 JSON 补丁写回器（R100z136）

替代旧工作流「AI 生成一个几万字节的 writeback.py，整文件 Read + 整文件重写 data.js」：
2026-10-14 / 10-15 两次 16:00 任务的运行上下文都被它烧穿（5 次整文件 Write + 3 次整文件
Read，是 17:30 automation-request-refused 与 17:33 sub-run 交接的直接推手）。

新工作流：AI 只维护一个小 JSON 补丁文件（默认 /tmp/narrative_patch.json），本脚本负责
把它精确合并进 data.js。改叙事 = 改 JSON + 重跑本脚本，不再重写大脚本、不再整文件
读写 data.js；修正轮次用 Edit 工具改补丁文件即可（外科编辑，不动其他字段）。

补丁格式（路径 → 值；路径用点号分层 + [i] 数组下标）：
{
  "ashare.summary": "……",
  "ashare.sectorsUp[0].reason.detail": "……",
  "ashare.bullNews[0].summary": "……",
  "aiPrediction.verification": {"total": 6, "hit": 3, ...},
  "ashare.bullNews": [ {...}, {...} ]            # 整数组替换（新增/重排条目时用）
}
纪律：
  - 已有条目优先用叶子路径精确替换（保住脚本生成的 thsCode/tops/netInflow 等字段）；
    整条/整数组替换仅用于新增条目或有意重排。
  - 路径不合法（中间层缺失 / 下标越界 / 类型不符）→ 该项报错跳过，其余项照常应用，
    最终以非零退出码提示；落盘前做 node 语法 + 结构往返双重校验，不会写坏 data.js。
  - 重复运行安全（幂等）：同一补丁重复应用结果一致。

用法:
    python3 apply_narrative.py [patch.json]                      # 默认 /tmp/narrative_patch.json
    python3 apply_narrative.py --check                           # 只验证路径合法，不落盘
    python3 apply_narrative.py --list ashare.sectorsUp --keys name,pct   # 只读窥视当前结构
    python3 apply_narrative.py --force                           # 允许写脚本独占字段（默认禁止）

脚本独占字段（stopTrack / strategy / macroCalendar，PROJECT-MEMORY 硬约束：只能由
stop_tracker.py 与 deploy.sh 内 strategy_engine.py/macro_calendar.py 生成）默认拒绝
写入，--force 才放行——防手滑洗掉脚本产物。
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common  # noqa: E402

BASE = HERE
DATA_JS = os.path.join(BASE, "data.js")
DEFAULT_PATCH = "/tmp/narrative_patch.json"

# 脚本独占字段：AI 写回一律禁止（--force 除外）
DENY_PREFIXES = ("stopTrack", "strategy", "macroCalendar")

# --list 输出体积上限（防把 5MB 的 data.js 结构整段打回上下文）
MAX_LIST_CHARS = 12000


def parse_path(path):
    """'ashare.sectorsUp[0].reason.detail' → [('name','ashare'),('name','sectorsUp'),
    ('idx','0'),('name','reason'),('name','detail')]。空路径/非法格式抛 ValueError。"""
    if not path or not isinstance(path, str):
        raise ValueError("路径必须是非空字符串")
    toks = []
    for idx, name in re.findall(r"\[(\d+)\]|([^\[\].]+)", path):
        if idx != "":
            toks.append(("idx", int(idx)))
        elif name != "":
            toks.append(("name", name))
        else:
            raise ValueError(f"路径含空片段：{path!r}")
    if not toks:
        raise ValueError(f"路径解析结果为空：{path!r}")
    return toks


def navigate(D, toks):
    """沿 toks 走到父层，返回 (parent, last_token)。中间层缺失即抛 ValueError。"""
    cur = D
    for kind, val in toks[:-1]:
        if kind == "name":
            if not isinstance(cur, dict):
                raise ValueError(f"路径中层不是对象（试图取键 {val}）")
            if val not in cur:
                raise ValueError(f"路径中层不存在：{val}")
            cur = cur[val]
        else:
            if not isinstance(cur, list):
                raise ValueError(f"路径中层不是数组（试图取下标 {val}）")
            if val >= len(cur):
                raise ValueError(f"数组下标越界：{val}（长度 {len(cur)}）")
            cur = cur[val]
    return cur, toks[-1]


def apply_one(D, path, value, force=False):
    """应用单条补丁。返回 None 成功；raise ValueError 路径不合法。"""
    root = path.split(".", 1)[0].split("[", 1)[0]
    if root in DENY_PREFIXES and not force:
        raise ValueError(f"{path} 属于脚本独占字段 {root}（只能由 stop_tracker/deploy.sh 内"
                         f"脚本生成），默认禁止 AI 写回；确需改写请带 --force")
    toks = parse_path(path)
    parent, last = navigate(D, toks)
    kind, val = last
    if kind == "name":
        if not isinstance(parent, dict):
            raise ValueError("末层父级不是对象，无法按键赋值")
        parent[val] = value
    else:
        if not isinstance(parent, list):
            raise ValueError("末层父级不是数组，无法按下标赋值")
        if val >= len(parent):
            raise ValueError(f"数组下标越界：{val}（长度 {len(parent)}）；新增条目请整数组替换")
        parent[val] = value
    return None


def _extract_json_text():
    """从 data.js 文本中取出 DASHBOARD_DATA 的 JSON 主体。失败抛 ValueError。"""
    with open(DATA_JS, encoding="utf-8") as f:
        s = f.read()
    m = re.search(r"window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$", s)
    if not m:
        raise ValueError("data.js 中未找到 window.DASHBOARD_DATA = {...} 结构")
    return m.group(1)


def load_data_js():
    """加载 data.js 为 Python dict。

    ⚠️ R100z136：不走 common.load_dashboard_data 的 node eval 通道——JS 的
    JSON.stringify 会把 0.0 归一成 0，整文件重序列化会产生一片无意义 diff
    （int/float 区分丢失）。这里先用 Python json.loads 直接解析（完整保留
    int/float 与字面量），仅当文本不是纯 JSON（历史遗留 JS 语法）时才回退
    node 通道。保字面量 = git diff 只显示真正改动的路径。
    """
    try:
        return json.loads(_extract_json_text())
    except ValueError:
        return common.load_dashboard_data(BASE)


def save_data(D):
    """与 merge_duanban/embed_klines 等脚本一致的紧凑格式落盘 + 双重校验。"""
    out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False,
                                                  separators=(",", ":")) + ";\n"
    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write(out)
    # ① node 语法校验
    node = common.find_node()
    p = subprocess.run([node, "--check", DATA_JS], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("node --check 未过：" + (p.stderr or "")[:300])
    # ② 结构往返校验（能重新解析且顶层键不少）
    D2 = common.load_dashboard_data(BASE)
    miss = [k for k in D if k not in D2]
    if miss:
        raise RuntimeError(f"往返校验失败：顶层键丢失 {miss}")
    return len(out)


def cmd_list(path, keys):
    """只读窥视：打印某路径当前值（可按 keys 投影，控制输出体积）。"""
    D = load_data_js()
    toks = parse_path(path)
    cur = D
    for kind, val in toks:
        if kind == "name":
            if not isinstance(cur, dict) or val not in cur:
                print(f"[NOT-FOUND] {path}")
                return 3
            cur = cur[val]
        else:
            if not isinstance(cur, list) or val >= len(cur):
                print(f"[NOT-FOUND] {path}")
                return 3
            cur = cur[val]
    if keys:
        kl = [k.strip() for k in keys.split(",") if k.strip()]
        if isinstance(cur, list):
            cur = [{k: (x.get(k) if isinstance(x, dict) else None) for k in kl}
                   for x in cur if isinstance(x, dict)]
        elif isinstance(cur, dict):
            cur = {k: cur.get(k) for k in kl}
    txt = json.dumps(cur, ensure_ascii=False, indent=1)
    if len(txt) > MAX_LIST_CHARS:
        txt = txt[:MAX_LIST_CHARS] + f"\n… [截断：原长 {len(txt)} 字符，加 --keys 投影缩小]"
    print(txt)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="叙事 JSON 补丁写回器：把 /tmp/narrative_patch.json 精确合并进 data.js")
    parser.add_argument("patch", nargs="?", default=DEFAULT_PATCH,
                        help=f"补丁 JSON 路径（默认 {DEFAULT_PATCH}）")
    parser.add_argument("--check", action="store_true", help="只验证路径合法，不落盘")
    parser.add_argument("--force", action="store_true",
                        help="允许写脚本独占字段（stopTrack/strategy/macroCalendar）")
    parser.add_argument("--list", dest="list_path", default=None,
                        help="只读窥视某路径的当前值（不应用补丁）")
    parser.add_argument("--keys", default=None,
                        help="配合 --list：只投影这些键，如 name,pct")
    a = parser.parse_args()

    if a.list_path:
        return cmd_list(a.list_path, a.keys)

    if not os.path.exists(a.patch):
        print(f"[FAIL] 补丁文件不存在：{a.patch}")
        return 2
    try:
        with open(a.patch, encoding="utf-8") as f:
            patch = json.load(f)
    except Exception as e:                                   # noqa: BLE001
        print(f"[FAIL] 补丁不是合法 JSON：{e}")
        return 2
    if not isinstance(patch, dict) or not patch:
        print("[FAIL] 补丁必须是「路径 → 值」的非空 JSON 对象")
        return 2

    D = load_data_js()

    ok, bad = [], []
    for path, value in patch.items():
        try:
            apply_one(D, path, value, force=a.force)
            ok.append(path)
        except Exception as e:                               # noqa: BLE001
            bad.append(f"{path}：{e}")

    if bad:
        print(f"[FAIL] {len(bad)} 条路径不合法（未应用；其余 {len(ok)} 条已在内存中生效但"
              f"不会落盘——修好补丁后重跑）：")
        for b in bad:
            print("  - " + b)
        return 1

    if a.check:
        print(f"[CHECK-OK] {len(ok)} 条路径全部合法（未落盘）：")
        for p in ok:
            print("  + " + p)
        return 0

    try:
        size = save_data(D)
    except Exception as e:                                   # noqa: BLE001
        print(f"[FAIL] 落盘校验失败：{e}")
        return 1
    print(f"[WRITE-OK] 已应用 {len(ok)} 条补丁 → data.js（{size} 字节，node --check + "
          f"结构往返校验通过）")
    for p in ok:
        print("  + " + p)
    print("提示：叙事字数/复读/跨板块模板预检请跑 check_narrative.py；"
          "全绿后再跑 audit_pipeline.py。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

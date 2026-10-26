#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主题名 → 同花顺行业板块名 的别名表同步工具（R100z224，2026-10-26）。

## 为什么需要它

`aiPrediction.sectors[].sector` 是 AI 每周写的，它习惯写「主题名」（创新药 / 光模块 /
半导体设备），而 `ashare.allBoards` 里是**同花顺行业名**（化学制药 / 通信设备 / 半导体）。
两套名字对不上时：

  ① `entry_zone.py` 按名精确匹配 → 找不到 → 不补 `fundCheck`/`entry` → **板块买点整块缺失**；
  ② 前端 `pctOf()` 走 `pctAlias` 别名 → 能显示涨幅（所以页面「看起来正常」，
     坏处是**坏得很安静**：读者不知道这个板块的承接/不追/证伪其实一直是空的）。

2026-10-22 / 10-23 / 10-26 三天实测：6 个预测板块里分别有 3 / 5 / 4 个对不上，
每周重演。根因是**生成层没有任何硬校验**，只靠 08:30 `entry_zone.py` 打一行 WARN。

## 单一事实源

别名表只存 `board_alias.json`。前端 `index.html` 里那段 `pctAlias = {...}` 由本工具
`--sync` 从 JSON **生成**，不再手改（手改会在下一轮被覆盖）。`audit_pipeline.py` G46
锁两边逐字一致，防止有人只改一边。

## 用法

    python3 board_alias.py --check                 # 比对两边是否一致（exit 0=一致，1=漂移）
    python3 board_alias.py --sync                  # 从 JSON 重建 index.html 的 pctAlias 段
    python3 board_alias.py --add '光模块=通信设备,光学光电子'   # 加一条（校验目标真实存在）→ 自动 sync
    python3 board_alias.py --json                  # 打印整表（给 AI 读）

`--add` 的目标必须是 `ashare.allBoards` 里真实存在的板块名，否则拒绝——乱指一个不存在的
板块等于把「显示 —」换成「显示别的板块的涨幅」，那是**更坏的错**（看不出错）。
确需写还没上榜的板块，加 `--force`，但页面会显示「—」，那是显式降级。
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ALIAS_FILE = os.path.join(HERE, "board_alias.json")
INDEX_FILE = os.path.join(HERE, "index.html")

# index.html 里的段：`  pctAlias = {` … `\n  };`
_BEGIN = "pctAlias = {"
_END = "\n  };"


def load_aliases():
    """→ {主题名: [行业名, ...]}。文件缺失/损坏时返回 {}（调用方各自决定降级方式）。"""
    try:
        with open(ALIAS_FILE, encoding="utf-8") as f:
            obj = json.load(f)
        a = obj.get("aliases") or {}
        return {str(k): [str(x) for x in v] for k, v in a.items() if v}
    except Exception:                                        # noqa: BLE001
        return {}


def _render_js(aliases):
    """生成 pctAlias 段的 JS 字面量（与 index.html 现有风格逐字一致：单引号 + ', ' 分隔）。"""
    lines = ["  pctAlias = {"]
    for k in sorted(aliases):
        vals = ", ".join("'%s'" % v for v in aliases[k])
        lines.append("    '%s': [%s]," % (k, vals))
    # 末条也有逗号（JS 允许尾逗号，且这样 diff 只增删行、不改上一行）
    lines.append("  };")
    return "\n".join(lines)


def _extract_js_block(s):
    """→ (start, end) 覆盖 `pctAlias = {` 到 `};` 的绝对下标；不唯一或找不到 → None。"""
    if s.count(_BEGIN) != 1:
        return None
    i = s.index(_BEGIN)
    j = s.find(_END, i)
    if j < 0:
        return None
    return i, j + len(_END)


def _parse_js_block(blk):
    """从 JS 段解析出 {key: [v, ...]}，用于 --check 逐字比对。"""
    out = {}
    for k, v in re.findall(r"'([^']+)'\s*:\s*\[([^\]]*)\]", blk, re.S):
        out[k] = [x.strip().strip("'") for x in v.split(",") if x.strip()]
    return out


def check():
    """→ (ok, msg)。比对 index.html 内联表与 board_alias.json。"""
    aliases = load_aliases()
    if not aliases:
        return False, "board_alias.json 缺失或为空"
    try:
        s = open(INDEX_FILE, encoding="utf-8").read()
    except Exception as e:                                   # noqa: BLE001
        return False, "index.html 读不到：%s" % e
    span = _extract_js_block(s)
    if not span:
        return False, "index.html 里找不到唯一的 `pctAlias = {` … `};` 段"
    js = _parse_js_block(s[span[0]:span[1]])
    only_json = sorted(set(aliases) - set(js))
    only_js = sorted(set(js) - set(aliases))
    diff = sorted(k for k in set(aliases) & set(js) if aliases[k] != js[k])
    if only_json or only_js or diff:
        bits = []
        if only_json:
            bits.append("只在 JSON：%s" % "、".join(only_json[:6]))
        if only_js:
            bits.append("只在前端：%s" % "、".join(only_js[:6]))
        if diff:
            bits.append("映射不同：%s" % "、".join(
                "%s(%s→%s)" % (k, "/".join(js[k]), "/".join(aliases[k])) for k in diff[:4]))
        return False, "；".join(bits) + "（跑 `python3 board_alias.py --sync` 对齐）"
    return True, "别名表两边同源（%d 条）" % len(aliases)


def sync(write=True):
    """从 JSON 重建 index.html 的 pctAlias 段。→ (ok, msg)。"""
    aliases = load_aliases()
    if not aliases:
        return False, "board_alias.json 缺失或为空"
    s = open(INDEX_FILE, encoding="utf-8").read()
    span = _extract_js_block(s)
    if not span:
        return False, "index.html 里找不到唯一的 `pctAlias = {` … `};` 段（不敢瞎改）"
    # 段起点前若只有本行的缩进，把它一起吃掉，否则重建后会多出 2 个空格（缩进逐轮加深）
    ls = s.rfind("\n", 0, span[0]) + 1
    if s[ls:span[0]].strip() == "":
        span = (ls, span[1])
    new = s[:span[0]] + _render_js(aliases) + s[span[1]:]
    if new == s:
        return True, "已是最新，无需改动（%d 条）" % len(aliases)
    if write:
        # index.html 是单文件主交付物：原子写，避免半截文件（K3 同一纪律）
        tmp = INDEX_FILE + ".tmp.html"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new)
        os.replace(tmp, INDEX_FILE)
    return True, "index.html pctAlias 已按 JSON 重建（%d 条）" % len(aliases)


def _real_boards():
    """→ set(allBoards[].name)。读不到 data.js 时返回空集（--add 会跳过存在性校验并提示）。"""
    try:
        p = os.path.join(HERE, "data.js")
        raw = open(p, encoding="utf-8").read()
        D = json.loads(raw[raw.index("{"):raw.rindex(";")])
        return {str(b.get("name")) for b in ((D.get("ashare") or {}).get("allBoards") or [])
                if b.get("name")}
    except Exception:                                        # noqa: BLE001
        return set()


def add(spec, force=False):
    """`主题名=行业1,行业2` → 写进 JSON 并 sync。→ (ok, msg)。"""
    if "=" not in spec:
        return False, "格式应为 `主题名=行业名[,行业名2]`，例：'光模块=通信设备,光学光电子'"
    k, v = spec.split("=", 1)
    k = k.strip()
    vals = [x.strip() for x in v.split(",") if x.strip()]
    if not k or not vals:
        return False, "主题名或行业名为空"
    boards = _real_boards()
    if boards and not force:
        bad = [x for x in vals if x not in boards]
        if bad:
            return False, ("目标板块不在 ashare.allBoards 里：%s。"
                           "请改用真实行业名（`python3 board_alias.py --json` 看全表）；"
                           "确需登记未上榜板块加 --force" % "、".join(bad))
    obj = json.load(open(ALIAS_FILE, encoding="utf-8"))
    old = (obj.get("aliases") or {}).get(k)
    obj.setdefault("aliases", {})[k] = vals
    obj["aliases"] = dict(sorted(obj["aliases"].items(), key=lambda kv: kv[0]))
    with open(ALIAS_FILE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
    ok, msg = sync()
    return ok, "%s：%s → %s；%s" % ("更新" if old else "新增", k,
                                    "/".join(vals), msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--add", metavar="SPEC")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.add:
        ok, msg = add(a.add, force=a.force)
        print(("[board_alias] " if ok else "[board_alias][FAIL] ") + msg)
        return 0 if ok else 1
    if a.sync:
        ok, msg = sync()
        print(("[board_alias] " if ok else "[board_alias][FAIL] ") + msg)
        return 0 if ok else 1
    if a.json:
        print(json.dumps({"aliases": load_aliases()}, ensure_ascii=False, indent=1))
        return 0
    # 默认 --check
    ok, msg = check()
    print(("[board_alias] " if ok else "[board_alias][FAIL] ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

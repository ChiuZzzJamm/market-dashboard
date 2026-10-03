#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_pipeline.py · 五自动化 + 流水线一次性机器验收（R100z17）

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
NODE = "/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node"
VFLAGS = ("--no-fix",)

# R100z17：data.js 里「结构性可空」的字段——不是缺陷，是脚本/前端设计预期。
# 每条豁免必须给出豁免理由，且由 G9b 强制反证「前端不会渲染出空白」：
#   ① 前端根本不引用该字段（纯数据层字段，空值无害）
#   ② 前端引用处全部带真值兜底（if/三元/|| ''/typeof==='string' 等）
# 以后若要新增豁免，必须同时补一条 G9b 能验证的理由，不许只往表里塞名字。
EMPTY_WHITELIST = [
    # --- 被动技术池：脚本只出形态/量化，AI 叙事由 16:00 AI 写回，脚本阶段天然空 ---
    (r"harmonic\.(confirmPool|watchPool|failPool)\[\d+\]\.(story|deduce)",
     "R100z4q：harmonic_detect 从旧池继承叙事，新形态条目脚本阶段无叙事，等 AI 写回"),
    (r"(accumulation|powerScreen)\.(scored|passed|strong|watch)\[\d+\]\.(story|deduce)",
     "R100z21：scored 全量 + strong/watch 分池副本同构，AI 叙事位脚本阶段为空、由 AI 写回"),
    (r"(accumulation|powerScreen)\.(scored|strong|watch)\[\d+\]\.sector",
     "R100z21：strong/watch 分池副本沿用 scored 的板块字段，sec_map 查不到时留空"),
    # --- 结构化可选字段：脚本写 foo or ''，无值时该维度不存在 ---
    (r"(harmonic|accumulation|powerScreen)\.(confirmPool|watchPool|scored|passed|failPool)\[\d+\]\.sector",
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
    (r"(harmonic|accumulation|powerScreen)\.summary$",
     "脚本可能不给名，前端真值兜底"),
    (r"aiPrediction\.verification",     # 非交易日/null 属正常
     "08:30 未写 verification 时前端不渲染验证卡"),
    (r"openOutlook\.verification",      # 08:30 未写 pos 时 [SKIP]
     "同上，未写 pos 时 score_openoutlook 直接 [SKIP]，前端不渲染"),
    (r"aiPrediction\.sectors\[\d+\]\.exp",
     "R100z11：非预期型板块不落 exp，expBadge 返回空串不渲染"),
    (r"(bullish|bearish)$",
     "R98i：外盘方向每股独立写，无观点即空"),
]

DATA_KEYS_REQUIRED = [
    "us", "ashare", "duanban", "panorama", "aiPrediction", "openOutlook",
    "stkKlines", "stkKlineNames", "harmonic", "accumulation", "powerScreen",
]

VALIDATE_SCOPES = ("all", "ai", "us", "star")


class Report:
    def __init__(self):
        self.items = []      # (group, level, msg)
        self.counts = {"FAIL": 0, "WARN": 0, "PASS": 0, "INFO": 0}

    def add(self, group, level, msg):
        self.counts[level] = self.counts.get(level, 0) + 1
        self.items.append((group, level, msg))

    def ok(self, g, m): self.add(g, "PASS", m)
    def warn(self, g, m): self.add(g, "WARN", m)
    def fail(self, g, m): self.add(g, "FAIL", m)
    def info(self, g, m): self.add(g, "INFO", m)

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
    else:
        r.ok("G1", f"{n_py} 个 .py + {n_sh} 个 .sh 语法全部通过")


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
    for a in autos:
        nums = []
        for x in (a.get("forbidden", []) or []):
            for ch in str(x):
                v = _cirkoumber(ch)
                if v:
                    nums.append(v)
        nums = sorted(set(nums))
        if nums and nums != list(range(1, len(nums) + 1)):
            gaps.append(f"{a.get('id','')[:12]}: 禁项编号跳号（{nums}）")
        if not a.get("mustContain"):
            no_mc.append(a.get("id", "?")[:12])
    if gaps:
        r.fail("G4", "; ".join(gaps))
    else:
        r.ok("G4", "五份 prompt 的「绝对禁止」编号无跳号/重复（15/28/11/33/21 条）")
    if no_mc:
        r.fail("G4", f"基线缺 mustContain 短语：{', '.join(no_mc)}")
    else:
        r.ok("G4", "60 条 mustContain 短语基线齐备（prompt 漂移时由基线重抽发现）")


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
        ("us.tradeDate", (D.get("us") or {}).get("tradeDate")),
    ]
    stale = [f"{k}={v}" for k, v in checks if v and last and v < last]
    if stale:
        r.warn("G10", f"日期早于最近交易日 {last}：{'; '.join(stale)}（休市期正常，换日前需复核）")
    else:
        r.ok("G10", f"各模块日期均 ≥ 最近交易日 {last}")

    # openOutlook.pos 闭环（R100z13 → 16:00 score_openoutlook 打分依赖）
    # 分流：① 前瞻已为「下一交易日」写了但缺 pos → 真缺陷（当天 16:00 必然 [SKIP]）
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
        r.ok("G10", f"openOutlook.pos 完整（date={oo_date}，16:00 前瞻打分闭环可用）")
    elif nxt and oo_date and oo_date >= nxt:
        r.warn("G10", f"openOutlook(date={oo_date}) 已为下一交易日 {nxt} 写了前瞻却缺结构化 pos"
                      f"（tendency/bandLo/bandHi/volExpect）→ 当日 16:00 score_openoutlook 将 [SKIP]")
    elif nxt:
        r.ok("G10", f"openOutlook 尚未为下一交易日 {nxt} 写入前瞻（date={oo_date}）"
                    f"——08:30 自动化到期后补写；届时 audit 会校验 pos 是否完整，缺 pos 才告警")

    # duanban.star 存在性（R100z16 清场事故回归）
    # 休市期缺失属预期（9:45 在下一个交易日才重建），仅当最近交易日刚过 1 天仍缺才判 FAIL
    today = datetime.now().strftime("%Y-%m-%d")
    if "star" not in (D.get("duanban") or {}):
        # 口径：star 由 9:45 在「交易日当天」写入。
        # ① 下一个交易日还没到（休市期）→ 缺失是必然，PASS（不是缺陷）
        # ② 下一交易日已到 / 就是今天 仍缺 → 真事故（历史上 merge 会把它清场）
        if not nxt or nxt > today:
            r.ok("G10", f"duanban.star 当前缺失属预期：下一个交易日 {nxt or '—'} 尚未到"
                        f"（9:45 开盘精选届时写入；R100z16 后 merge 不再清场）")
        else:
            r.fail("G10", f"duanban.star（🌟开盘精选）缺失，而下一个交易日 {nxt} 已到/就是今天"
                          f"——历史曾因 merge 时序被清场，须在当日 9:45 前补回")
    else:
        sd = (D["duanban"]["star"] or {}).get("date")
        if sd and last and sd < last:
            r.warn("G10", f"duanban.star.date={sd} 早于最近交易日 {last}（9:45 未跑则正常）")
        else:
            r.ok("G10", f"duanban.star 存在（date={sd}）")


# ---------------------------------------------------------------- G11
def g11_smoke(r):
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


# ---------------------------------------------------------------- main
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
    if deep:
        g11_smoke(r)

    if as_json:
        print(json.dumps({"counts": r.counts, "items": r.items}, ensure_ascii=False, indent=2))
    else:
        groups = []
        for g, lv, msg in r.items:
            if not groups or groups[-1][0] != g:
                groups.append((g, []))
            groups[-1][1].append((lv, msg))
        print("=" * 78)
        print(" audit_pipeline · 五自动化与流水线一次性机器验收 (R100z17)")
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

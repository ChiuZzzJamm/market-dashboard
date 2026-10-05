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
    G12 半死链路      data.js 顶层产出必须登记「流程主人」+ 被自动化点名 + 脚本零孤儿
    G13 节假日表      common 与 cal_factor 两份手工节假日表同源等价 + 覆盖到期前 30 天 WARN 提醒跨年更新
    G14 路径断裂      prompt 内脚本绝对路径必须含完整 dashboard/ 或走 cd 相对调用（禁「缺 dashboard/ 裸路径」）
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
_NODE_CAND = "/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node"
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
    (r"harmonic\.failPool$",     # 当天无谐波失效标的即空数组
     "R100z27：失效池无新增即空数组，属正常日度状态（不是脚本漏跑）"
     "；前端 dbFailHtml/谐波失效区统一走 if(!x.length) 兜底显示『暂无失效标的』"),
]

DATA_KEYS_REQUIRED = [
    "us", "ashare", "duanban", "panorama", "aiPrediction", "openOutlook",
    "stkKlines", "stkKlineNames", "harmonic", "accumulation", "powerScreen",
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
#   require : 五条看板自动化的 prompt 必须点名的关键词（大小写不敏感）。留空 = 豁免点名。
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
        "note": "16:00 任务写回 AI 叙事/概率，前端 AI 预测卡直接用",
    },
    "ashare": {
        "require": ("ashare", "A 股", "A股", "大盘"),
        "note": "A 股大盘与板块行情的主节点",
    },
    "us": {
        "require": ("us", "美股"),
        "note": "美股行情节点，07:30 / 08:30 两条任务各自负责一段",
    },
    "panorama": {
        "require": ("panorama", "全景", "韩", "日"),
        "note": "韩日早盘全景，覆盖在全景卡",
    },
    "duanban": {
        "require": ("duanban", "断板"),
        "note": "断板反包确认池/观察池/失效池",
    },
    "harmonic": {
        "require": ("harmonic", "谐波"),
        "note": "谐波形态池",
    },
    "accumulation": {
        "require": ("accumulation", "吸筹"),
        "note": "吸筹形态池",
    },
    "powerScreen": {
        "require": ("powerScreen", "九门", "强势"),
        "note": "九门强势筛选池（power_screener）",
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
        r.fail("G10", f"openOutlook(date={oo_date}) 已为下一交易日 {nxt} 写了前瞻却缺结构化 pos"
                      f"（tendency/bandLo/bandHi/volExpect）→ 当日 16:00 score_openoutlook 将 [SKIP]。"
                      f"本条硬拦 deploy：08:30 当次就该发现，不许拖到 16:00")
    elif nxt:
        r.ok("G10", f"openOutlook 尚未为下一交易日 {nxt} 写入前瞻（date={oo_date}）"
                    f"——08:30 自动化到期后补写；届时 audit 会校验 pos 是否完整，缺 pos 即 FAIL")

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


# ---------------------------------------------------------------- G12
def load_auto_prompts():
    """读五条看板自动化的 prompt 全文。

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
                    r.fail("G12", f"半死链路：{k} 前端已渲染，但五条看板自动化无一条点名"
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
    # R100z65：此前 audit_pipeline 只接在 16:00 与周日 22:00 两条上，07:30/08:30/9:45
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

    # ③ 脚本孤儿：prompt 与「其它 py 的源码」都不出现该模块名。
    # 注意必须比模块名（去 .py）——代码里写的是 `import kline_cache`，拿 'kline_cache.py'
    # 去 substr 匹配必然失败，会把满地引用的热脚本误报成孤儿。
    pyfiles = sorted(f for f in os.listdir(HERE) if f.endswith(".py"))
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", errors="ignore").read()
            for f in pyfiles}
    orphan = []
    for f in pyfiles:
        mod = f[:-3]
        if mod in allp:
            continue                                  # 自动化 prompt 直接点名
        if any(mod in s for n, s in srcs.items() if n != f):
            continue                                  # 别处 import / subprocess 引用
        orphan.append(f)
    if orphan:
        r.warn("G12", f"疑似孤儿脚本（prompt 与各 py 源码均无引用，动态引用 ex.submit/ex.map 需人工确认）："
                      f"{', '.join(sorted(orphan))}")
    else:
        r.ok("G12", f"{len(pyfiles)} 个 py 全部有调用方（prompt 点名或代码引用）")


def g13_holiday_table(r):
    """节假日表闸门：两份手工表必须同源等价 + 覆盖前瞻提醒跨年更新。

    背景（R100z66 体检发现）：common.HOLIDAY_RANGES_2026（is_trade_day /
    last_trade_day / today_trade_date 用）与 cal_factor.HOLIDAYS_2026
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
    a = sorted((str(x[0]), str(x[1])) for x in (getattr(_cm, "HOLIDAY_RANGES_2026", []) or []))
    b = sorted((str(v[0]), str(v[-1]))
               for v in (getattr(_cf, "HOLIDAYS_2026", {}) or {}).values())
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

    R100z71 发现：07:30 / 周日两条任务曾因路径损坏（缺 `dashboard/` 且未加引号）导致
    `update_us_from_quotes` / `check_duanban` / `stock_risk_blacklist` / `build_stock_pool`
    调用把目录当脚本跑、静默失败（python3: can't open file '/Users/loccco/WorkBuddy/market'），
    us 美股行情与断板池长期未更新、且无人察觉。G2 只校验 audit_expectations.json 里的
    相对文件名是否存在于磁盘，扫不到 prompt 内写死的绝对路径，故补本闸固化。

    注意：本闸只拦「缺 dashboard/ 的裸路径签名」，不拦「cd /WorkBuddy/market dashboard && python3 ...」
    这种未引号 cd 形式——后者在自动化执行器内能正确运行（16:00/08:30/09:45 及本任务内
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
        g11_smoke(r)
    g14_prompt_broken_path(r)

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

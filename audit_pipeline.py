#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_pipeline.py · 七自动化 + 流水线一次性机器验收（R100z17）

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
    G16 叙事深度      防 AI 偷懒·叙事主字段字数硬下限（A 股≥240 / 美股≥200 / outlook≥300 等，低于即 FAIL 拦部署）
    G17 防模板复读    防 AI 偷懒·同一句 ≥14 字在 ≥3 个叙述字段复现 = 万能模板尾巴（R100z96：us 页 34 条统一收尾事故），FAIL 拦部署
    G22 要闻等级噪音  五节要闻必须有 level（0重点/1关注/2常规）且标题不含盘面统计噪音（R100z157，WARN 级复核）
    G23 跨票措辞复读  连板/断板 story 的同一句（≥14 字）在 ≥3 只票且 ≥30% 池内重复 = 同段模板化（R100z157，WARN 级；
                      G17 只管「跨字段」，管不到「每票一个字段」的同段复用）
    G24 未核查披露    黑名单维度没取到数据（verdict != full）时，渲染文本里不得出现
                      「已核查无风险」「黑名单全部通过」等绝对安全表述（R100z160，FAIL）
    G25 记忆层体检    工作区 MEMORY.md 是「注入层」，超限会被 inject 截断导致硬约束读不全
                      （2026-10-21 实测 19.9KB 已被截断）→ 超 8KB 或路由表缺失即 WARN（R100z161）
    G26 机械池键对齐   stock_pool.json 的 bySector 键集必须与 aiPrediction.sectors 的板块名对得上；
                      对不上时 check_stock_pool 对该板块整轮失明，「④ 断板必须取自机械池」
                      静默降级为提示（R100z163，WARN）
    G27 记忆可达性     MEMORY.md 路由指向的 24.x 节必须真能 grep 到标题；「届时」紧贴过期日期
                      仍以未来时态陈述；>30 天日志未蒸馏（R100z166）
    G28 新鲜度一致性   页头 updatedAt 必须 = 各分格戳的最大值（顶层字段曾被 5 个脚本互相覆盖，
                      实测 A股 17:20 已更新、页头还停在 16:01）；ashare.updatedAt 必须与
                      tradeDate 同日（该格原先由 AI 手写，长期挂着 10-20 的旧值）；
                      分格清单与 refresh_stamp.py 的 SLOTS 逐项一致（R100z167，FAIL 拦部署）
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
_NODE_CAND = "/Users/loccco/.workbuddy/binaries/run-node"
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
    # --- 被动技术池：脚本只出形态/量化，AI 叙事由 17:00 叙事步 AI 写回，脚本阶段天然空 ---
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
    (r"policySignals\[\d+\]\.(sectors|targets)",
     "R100z107b：政策解码无识别到受益板块/量化目标时为空数组，前端按 '-' 显示"),
    (r"^policySignals$",
     "R100z107b：当日无政策源新闻时 policySignals 为空数组（正常）"),
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
#   require : 看板自动化的 prompt 必须点名的关键词（大小写不敏感）。留空 = 豁免点名。
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
        "note": "17:00 叙事步写回 AI 叙事/概率，前端 AI 预测卡直接用",
    },
    "ashare": {
        "require": ("ashare", "A 股", "A股", "大盘"),
        "note": "A 股大盘与板块行情的主节点",
    },
    "us": {
        "require": ("us", "美股"),
        "note": "美股行情节点，07:00 / 08:30 两条任务各自负责一段",
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
    "policySignals": {
        "require": ("policySignals", "政策解码", "政策信号", "enrich_policy"),
        "note": "R100z107b：enrich_policy.py 解码新闻标题为政策信号，17:00 叙事步在新闻写回后调用写回；前端政策信号解码面板渲染",
    },
    "macroCalendar": {
        "require": (),
        "note": "R100z117 ⑥：macro_calendar.py 产出的未来 45 日宏观事件日历（FOMC/GDP/CPI/PPI/MLF/LPR/工增固投社零），"
                "由 deploy.sh 流水线调用（零网络纯函数，兜底写空 exit 0）；前端「📅 宏观事件日历」卡渲染（renderMacroCalendar）",
    },
    "strategy": {
        "require": (),
        "note": "R100z117 ①：strategy_engine.py 聚合政策/AI预测/断板反包三类信号为盘前关注清单+仓位纪律+回测反馈闭环，"
                "由 deploy.sh 流水线调用（读 cal_factor/calibration_state/portfolio_curve，兜底写空 exit 0）；"
                "前端「🎯 盘前策略」卡渲染（renderStrategy）",
    },
    "stockNews": {
        "require": ("stock_news.py", "个股消息库", "stockNews"),
        "note": "R100z149：stock_news.py 抓东财个股公告 + 资讯搜索，按 code 汇总成 {code:{name,notices,news}}，"
                "覆盖断板（含失效池）/谐波/吸筹/九门/连板/板块成分/AI预测/要闻影响标的；"
                "前端弹窗（网页 db-modal）统一渲染「个股消息」段。"
                "16:00 数据步第 4.5 步点名（四模块之后、汇报之前；R100z162 更正：旧注记为「断板主生成前」，"
                "与 16:00 实际步骤顺序（2 断板 → 3 要闻 → 4 四模块 → 4.5 个股消息）不符）。",
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
    counts = []
    for a in autos:
        nums = []
        for x in (a.get("forbidden", []) or []):
            for ch in str(x):
                v = _cirkoumber(ch)
                if v:
                    nums.append(v)
        nums = sorted(set(nums))
        counts.append(len(nums))
        if nums and nums != list(range(1, len(nums) + 1)):
            gaps.append(f"{a.get('id','')[:12]}: 禁项编号跳号（{nums}）")
        if not a.get("mustContain"):
            no_mc.append(a.get("id", "?")[:12])
    n_mc = sum(len(a.get("mustContain") or []) for a in autos)
    if gaps:
        r.fail("G4", "; ".join(gaps))
    else:
        r.ok("G4", f"{len(autos)} 份 prompt 的「绝对禁止」编号无跳号/重复"
                   f"（{'/'.join(str(c) for c in counts)} 条，随拆分动态变化）")
    if no_mc:
        r.fail("G4", f"基线缺 mustContain 短语：{', '.join(no_mc)}")
    else:
        r.ok("G4", f"{n_mc} 条 mustContain 短语基线齐备（prompt 漂移时由基线重抽发现）")


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
        # R100z162：check_duanban 的 nd()/add_bull_code() 对 aiPrediction 来源的 ref 刻意留
        #   time=""（_fmt_ref 用 `if x` 跳过、前端完全不读 bullRefs/bearRefs），属结构性空值。
        #   不豁免则每天固定报 5 条「bullRefs[1].time=空串」假 WARN，久而久之闸门被无视。
        if re.search(r"\.(?:bull|bear)Refs\[\d+\]\.time$", path):
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
    ]
    stale = [f"{k}={v}" for k, v in checks if v and last and v < last]
    if stale:
        r.warn("G10", f"日期早于最近交易日 {last}：{'; '.join(stale)}（休市期正常，换日前需复核）")
    else:
        r.ok("G10", f"各模块日期均 ≥ 最近交易日 {last}")

    # 美股单独口径（R100z167）：美东收盘落在北京时间次日凌晨，美股交易日历又与 A 股不同。
    # 旧写法把 us.tradeDate 塞进上面那张表与 A 股最近交易日比 —— 结果**每天晚上必然命中**
    # 「us.tradeDate 早于最近交易日」（北京 21:00 时美东 10-21 还没收盘，最新只能是 10-20），
    # 一条永远为真的 WARN，等于训练人忽略 G10。现改为与「最近已收盘美股交易日」比：
    # 期望值 = prior_us_trading_day(北京今天)（当天收盘尚未发生，故取前一个交易日）。
    # 交易日历已下沉 common.py（与 07:00 取数脚本同源，不再有第三份手工副本）。
    try:
        import common as _common                               # noqa: PLC0415
        us_td = str((D.get("us") or {}).get("tradeDate") or "")[:10]
        exp = _common.prior_us_trading_day(datetime.now().date())
        if us_td and exp:
            if us_td == exp.isoformat():
                r.ok("G10", f"us.tradeDate={us_td} = 最近已收盘美股交易日")
            elif us_td > exp.isoformat():
                r.ok("G10", f"us.tradeDate={us_td} 不早于最近已收盘美股交易日 {exp.isoformat()}")
            else:
                r.warn("G10", f"us.tradeDate={us_td} 早于最近已收盘美股交易日 {exp.isoformat()}"
                              f"——07:00 取数步未跑，或美股数据未换日")
    except Exception as e:                                   # noqa: BLE001
        r.warn("G10", f"美股交易日历核对跳过：{e}")

    # openOutlook.pos 闭环（R100z13 → 17:00 score_openoutlook 打分依赖）
    # 分流：① 前瞻已为「下一交易日」写了但缺 pos → 真缺陷（当天 17:00 必然 [SKIP]）
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
        r.ok("G10", f"openOutlook.pos 完整（date={oo_date}，17:00 前瞻打分闭环可用）")
    elif nxt and oo_date and oo_date >= nxt:
        r.fail("G10", f"openOutlook(date={oo_date}) 已为下一交易日 {nxt} 写了前瞻却缺结构化 pos"
                      f"（tendency/bandLo/bandHi/volExpect）→ 当日 17:00 score_openoutlook 将 [SKIP]。"
                      f"本条硬拦 deploy：08:30 当次就该发现，不许拖到 17:00")
    elif nxt:
        r.ok("G10", f"openOutlook 尚未为下一交易日 {nxt} 写入前瞻（date={oo_date}）"
                    f"——08:30 自动化到期后补写；届时 audit 会校验 pos 是否完整，缺 pos 即 FAIL")

    # duanban.star 存在性（R100z16 清场事故回归）
    # 休市期缺失属预期（9:45 在下一个交易日才重建），仅当最近交易日刚过 1 天仍缺才判 FAIL
    # R100z83（2026-10-08 实战教训）：交易日 **10:00 前** star 缺失降为 WARN——
    #   star 由 9:45 任务写入，07:00/08:30 任务先于它部署，若此时判 FAIL 会逼着
    #   前置任务按「FAIL 必须修正」条款提前跑 update_star.py 补造 star（08:11 实际发生），
    #   违反「9:45 是 star 唯一写入口」的职责边界、产出开盘前低质量版本。
    #   10:00 后（9:45 窗口已过）仍缺才是真事故，恢复硬拦。
    today = datetime.now().strftime("%Y-%m-%d")
    _before_star_window = datetime.now().hour < 10  # 9:45 任务窗口（留 15 分钟宽限）未过
    if "star" not in (D.get("duanban") or {}):
        # 口径：star 由 9:45 在「交易日当天」写入。
        # ① 下一个交易日还没到（休市期）→ 缺失是必然，PASS（不是缺陷）
        # ② 下一交易日已到 / 就是今天、但当前 <10:00 → 9:45 尚未跑，WARN（不是缺陷）
        # ③ 下一交易日已到 / 就是今天、且已过 10:00 仍缺 → 真事故（历史上 merge 会把它清场）
        if not nxt or nxt > today:
            r.ok("G10", f"duanban.star 当前缺失属预期：下一个交易日 {nxt or '—'} 尚未到"
                        f"（9:45 开盘精选届时写入；R100z16 后 merge 不再清场）")
        elif _before_star_window:
            r.warn("G10", f"duanban.star 缺失但当前在 10:00 前（下一交易日 {nxt} 当天、9:45 开盘精选"
                          f"尚未跑）——属预期，07:00/08:30 任务**不要**跑 update_star.py 补造，照常部署；"
                          f"10:00 后仍缺才升级 FAIL")
        else:
            r.fail("G10", f"duanban.star（🌟开盘精选）缺失，而下一交易日 {nxt} 已到且已过 9:45 窗口"
                          f"——历史曾因 merge 时序被清场，属真事故，须排查清场根因并补回")
    else:
        sd = (D["duanban"]["star"] or {}).get("date")
        if sd and last and sd < last:
            r.warn("G10", f"duanban.star.date={sd} 早于最近交易日 {last}（9:45 未跑则正常）")
        else:
            r.ok("G10", f"duanban.star 存在（date={sd}）")


# ---------------------------------------------------------------- G11
def g11_smoke(r, deep=False):
    # ① 始终运行的「导入级冒烟」（无网络）：捕获 import 级静默 rot——
    #    仅靠 py_compile 抓不到 import 期故障（缺依赖 / 循环 import / 全局副作用崩），
    #    一旦部署链路里有模块导入失败，G1 语法闸也拦不住，会在复牌/首跑才炸。
    #    故固化进闸门、所有自动化部署都跑（不再依赖 --deep 才会触发）。
    import importlib
    mods = ["cal_factor", "build_stock_pool", "track_calibration",
            "stock_risk_blacklist", "market_bench", "common", "audit_pipeline"]
    bad_imp = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:                              # noqa: BLE001
            bad_imp.append(f"{m}: {type(e).__name__}: {e}"[:100])
    if bad_imp:
        r.fail("G11", "模块导入冒烟失败（部署前静默 rot 风险）：" + "; ".join(bad_imp))
    else:
        r.ok("G11", f"{len(mods)} 个流水线模块导入冒烟通过（无 import 级故障）")

    if not deep:
        return
    # ② --deep 时的「脚本网络冒烟」：逐个实跑（可能走网络，慢）
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
    """读看板自动化的 prompt 全文。

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
                    r.fail("G12", f"半死链路：{k} 前端已渲染，但七条看板自动化无一条点名"
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
    # R100z65：此前 audit_pipeline 只接在 17:00 与周日 22:00 两条上，07:00/08:30/9:45
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

    # ③ 脚本孤儿：prompt / 其它 py 源码 / shell 脚本 三处都不出现该模块名。
    # 注意必须比模块名（去 .py）——代码里写的是 `import kline_cache`，拿 'kline_cache.py'
    # 去 substr 匹配必然失败，会把满地引用的热脚本误报成孤儿。
    # R100z157：补 shell 层——deploy.sh 是 enrich_policy / sweep_news / market_structure 这类
    # 「零网络确定性脚本」的唯一调用方，只扫 py + prompt 会把它们全部误报成孤儿。
    pyfiles = sorted(f for f in os.listdir(HERE) if f.endswith(".py"))
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", errors="ignore").read()
            for f in pyfiles}
    for f in sorted(x for x in os.listdir(HERE) if x.endswith(".sh")):
        try:
            srcs[f] = open(os.path.join(HERE, f), encoding="utf-8", errors="ignore").read()
        except Exception:                                    # noqa: BLE001
            pass
    orphan = []
    for f in pyfiles:
        mod = f[:-3]
        if mod in allp:
            continue                                  # 自动化 prompt 直接点名
        if any(mod in s for n, s in srcs.items() if n != f):
            continue                                  # 别处 import / subprocess / shell 引用
        orphan.append(f)
    if orphan:
        r.warn("G12", f"疑似孤儿脚本（prompt / py 源码 / shell 三处均无引用，动态引用 ex.submit/ex.map 需人工确认）："
                      f"{', '.join(sorted(orphan))}")
    else:
        r.ok("G12", f"{len(pyfiles)} 个 py 全部有调用方（prompt 点名或 py/shell 引用）")


def g13_holiday_table(r):
    """节假日表闸门：两份手工表必须同源等价 + 覆盖前瞻提醒跨年更新。

    背景（R100z66 体检发现）：common.HOLIDAY_RANGES（is_trade_day /
    last_trade_day / today_trade_date 用）与 cal_factor.HOLIDAYS
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
    a = sorted((str(x[0]), str(x[1])) for x in (getattr(_cm, "HOLIDAY_RANGES", []) or []))
    b = sorted((str(v[0]), str(v[-1]))
               for v in (getattr(_cf, "HOLIDAYS", {}) or {}).values())
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

    R100z71 发现：07:00 / 周日两条任务曾因路径损坏（缺 `dashboard/` 且未加引号）导致
    `update_us_from_quotes` / `check_duanban` / `stock_risk_blacklist` / `build_stock_pool`
    调用把目录当脚本跑、静默失败（python3: can't open file '/Users/loccco/WorkBuddy/market'），
    us 美股行情与断板池长期未更新、且无人察觉。G2 只校验 audit_expectations.json 里的
    相对文件名是否存在于磁盘，扫不到 prompt 内写死的绝对路径，故补本闸固化。

    注意：本闸只拦「缺 dashboard/ 的裸路径签名」，不拦「cd /WorkBuddy/market dashboard && python3 ...」
    这种未引号 cd 形式——后者在自动化执行器内能正确运行（17:00/08:30/09:45 及本任务内
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


def g15_interp_bins(r):
    """G15 解释器入口闸门（R100z76d 初建 → R100z77 升级）：prompt/脚本引用的解释器入口必须可用。

    根因修复：版本号不再写死，改为自修复包装脚本 run-node / run-python
    （动态解析托管版最高版本目录，缺失时回退 PATH）。本闸校验两类引用：
      (a) 残留硬版本路径 versions/X.Y.Z/bin/(node|python3) —— 必须存在；
      (b) 包装脚本 /Users/loccco/.workbuddy/binaries/run-(node|python) —— 必须存在、
          可执行、且能解析到一个真实存在的解释器二进制（glob 到 versions/*/bin/(node|python3)）。
    """
    autos = load_auto_prompts()
    ver_pat = re.compile(
        r'(/Users/loccco/\.workbuddy/binaries/(?:node|python)/versions/[^/\s]+/bin/(?:node|python3))')
    problems = []

    # (a) 残留版本路径：必须存在
    for a in autos:
        for m in ver_pat.finditer(a["text"]):
            path = m.group(1)
            if not os.path.exists(path):
                ln = a["text"][:m.start()].count("\n") + 1
                problems.append(f"{a['name']} L{ln}: 硬版本路径不存在 {path}")

    # (b) 包装脚本：存在 + 可执行 + 能解析到真实二进制；缺失则自愈重建
    WRAP_TPL = {
        "node": "#!/bin/sh\nROOT=/Users/loccco/.workbuddy/binaries/node/versions\n"
                '[ -e "$ROOT/current/bin/node" ] && exec "$ROOT/current/bin/node" "$@"\n'
                'd=$(ls -d "$ROOT"/*/ 2>/dev/null | sort -V | tail -1)\n'
                '[ -n "$d" ] && [ -x "${d}bin/node" ] && exec "${d}bin/node" "$@"\n'
                'exec node "$@"\n',
        "python": "#!/bin/sh\nROOT=/Users/loccco/.workbuddy/binaries/python/versions\n"
                  '[ -e "$ROOT/current/bin/python3" ] && exec "$ROOT/current/bin/python3" "$@"\n'
                  'd=$(ls -d "$ROOT"/*/ 2>/dev/null | sort -V | tail -1)\n'
                  '[ -n "$d" ] && [ -x "${d}bin/python3" ] && exec "${d}bin/python3" "$@"\n'
                  'exec python3 "$@"\n',
    }
    for kind, bin_name in (("node", "node"), ("python", "python3")):
        wp = f"/Users/loccco/.workbuddy/binaries/run-{kind}"
        if not os.path.exists(wp):
            # 自愈：缺失即重建（保证 pre-deploy 闸门跑完必定存在，自动化不会因文件丢失而崩）
            try:
                with open(wp, "w", encoding="utf-8") as fh:
                    fh.write(WRAP_TPL[kind])
                os.chmod(wp, 0o755)
                r.ok("G15", f"解释器包装脚本缺失已自愈重建：{wp}")
            except Exception as e:  # noqa: BLE001
                problems.append(f"解释器包装脚本缺失且重建失败 {wp}：{e}")
                continue
        elif not os.access(wp, os.X_OK):
            try:
                os.chmod(wp, 0o755)
                r.ok("G15", f"解释器包装脚本不可执行已自愈修复权限：{wp}")
            except Exception as e:  # noqa: BLE001
                problems.append(f"解释器包装脚本不可执行且修复失败 {wp}：{e}")
                continue
        root = f"/Users/loccco/.workbuddy/binaries/{kind}/versions"
        real = []
        try:
            for d in os.listdir(root):
                cand = os.path.join(root, d, "bin", bin_name)
                if os.path.exists(cand):
                    real.append(cand)
        except FileNotFoundError:
            pass
        if not real:
            problems.append(f"{wp} 解析不到任何托管 {bin_name}（{root} 下无版本目录）")

    if problems:
        r.fail("G15", "解释器入口不可用：" + "; ".join(problems))
    else:
        r.ok("G15", f"{len(autos)} 份 prompt 引用的解释器入口均可用（run-node/run-python 自修复生效）")


def _content_of(v):
    """顶层 openOutlook 是对象 {date, content, source, pos}（R100z13 起）。

    R100z160：此前 G16/G17 直接对它取 str() —— 量出来的是「dict 的 repr」（含键名与 pos
    子对象），openOutlook 的 300 字地板因此形同虚设（实测 str=1528 而正文 1359），
    字数区间也无法核对。统一改为取 content 正文；非 dict 时原样返回（兼容旧数据/纯字符串）。
    """
    if isinstance(v, dict):
        return str(v.get("content") or "")
    return v


def collect_g16_checks(D):
    """G16 叙事深度字段清单采集（R100z136 从 g16_narrative_depth 拆出）。

    check_narrative.py 落笔前预检复用本函数——预检与审计闸门必须同一份清单、
    同一套阈值，禁止出现两套口径。返回 [(label, text, floor), ...]。
    """
    checks = []  # (label, text, floor)

    def add(label, text, floor):
        checks.append((label, str(text or ""), floor))

    # 板块异动 + 五节要闻（A 股 / 美股）
    for mk, sec_floor, news_floor in (("ashare", 240, 240), ("us", 200, 200)):
        sec = D.get(mk) or {}
        for arr in ("sectorsUp", "sectorsDown"):
            for i, s in enumerate(sec.get(arr) or []):
                if isinstance(s, dict):
                    add(f"{mk}.{arr}[{i}].reason.detail",
                        (s.get("reason") or {}).get("detail"), sec_floor)
        for s in ("bullNews", "bearNews", "macroNews", "intlNews", "bankViews"):
            for i, nw in enumerate(sec.get(s) or []):
                if isinstance(nw, dict):
                    add(f"{mk}.{s}[{i}].summary", nw.get("summary"), news_floor)
        if sec.get("outlook"):
            add(f"{mk}.outlook", sec.get("outlook"), 300)

    # 美股顶层 summary（脚本生成；2026-10-12 前仅 ~124 字偏薄，用户要求与 A 股同口径扩充）
    _us_sum = (D.get("us") or {}).get("summary")
    if _us_sum:
        add("us.summary", _us_sum, 150)

    # AI 预测逻辑链 + 开盘前瞻（顶层）
    ap = D.get("aiPrediction") or {}
    for i, s in enumerate(ap.get("sectors") or []):
        if isinstance(s, dict):
            add(f"aiPrediction.sectors[{i}].logic", s.get("logic"), 200)
    if D.get("openOutlook"):
        add("openOutlook", _content_of(D.get("openOutlook")), 300)
    if isinstance(ap, dict) and ap.get("openOutlook"):
        add("aiPrediction.openOutlook", _content_of(ap.get("openOutlook")), 300)

    # A 股题材掘金 logic（R100z129 补齐：docstring 早已声明 240，但代码一直漏检，
    # 只靠 17:00 prompt 软约束 240-420 —— 用户 2026-10-13 拍板补机器闸）
    for i, t in enumerate((D.get("ashare") or {}).get("themePicks") or []):
        if isinstance(t, dict):
            add(f"ashare.themePicks[{i}].logic", t.get("logic"), 240)

    # 连板天梯 story（A 股）
    for i, s in enumerate((D.get("ashare") or {}).get("lianban") or []):
        if isinstance(s, dict):
            add(f"ashare.lianban[{i}].story", s.get("story"), 120)

    # 断板反包 confirmed/watching story（R100z148：检查面从 ashare.duanban 改到顶层
    # duanban——ashare.duanban 是 2026-10-14 后无任何读写方的死节点（前端/
    # 脚本均只读写顶层 D.duanban），已从 data.js 剔除；顶层才是真实渲染的数据源）
    db = D.get("duanban") or {}
    for pool in ("confirmed", "watching"):
        for i, e in enumerate(db.get(pool) or []):
            if isinstance(e, dict):
                add(f"duanban.{pool}[{i}].story", e.get("story"), 100)

    # 吸筹 scored/strong/watch note
    # R100z161c：路径修正——吸筹节点在**顶层** `accumulation`，`ashare` 里根本没有这个键
    #   （2026-10-21 实测 ashare 子键 27 个、无 accumulation；顶层 accum 有 scored 20 + strong 2
    #   + watch 18 = 40 条 note）。旧写法读 ashare.accumulation → 恒空 → **90 字地板从未生效**，
    #   是 R100z148 把断板从 ashare.duanban 改到顶层 duanban 时漏改的孪生兄弟。
    #   保留 ashare 兜底只为兼容万一回迁，正常走顶层。
    _acc = D.get("accumulation") or (D.get("ashare") or {}).get("accumulation") or {}
    for grp in ("scored", "strong", "watch"):
        for i, e in enumerate(_acc.get(grp) or []):
            if isinstance(e, dict):
                add(f"accumulation.{grp}[{i}].note", e.get("note"), 90)

    # 9:45 开盘精选「深度分析」（R100z161c 补闸）。二节速查表早写着 pushText 300-700 /
    #   sentiment 120-200，但 G16/G17 都不扫 duanban.star → 两个字段长期零保护。
    #   只加地板不加上限（超上限归 check_narrative 的 OVER 清单，硬拦会逼 AI 删内容）。
    _star = (D.get("duanban") or {}).get("star") or {}
    if isinstance(_star, dict):
        if _star.get("pushText"):
            add("duanban.star.pushText", _star.get("pushText"), 300)
        if _star.get("sentiment"):
            add("duanban.star.sentiment", _star.get("sentiment"), 120)

    # deduce 三候选推演（全站任意条目，R100z96：main/counter 曾被 prompt 定为「一句话」口径）
    def _deduce_walk(o, k=[0]):
        if isinstance(o, dict):
            dd = o.get("deduce")
            if isinstance(dd, dict):
                add(f"deduce#{k[0]}.main", dd.get("main"), 60)
                add(f"deduce#{k[0]}.counter", dd.get("counter"), 40)
                for j, c in enumerate(dd.get("cands") or []):
                    if isinstance(c, dict):
                        add(f"deduce#{k[0]}.cands[{j}].text", c.get("text"), 40)
                        el = c.get("eliminate")
                        add(f"deduce#{k[0]}.cands[{j}].eliminate",
                            el if isinstance(el, str) else "", 15)
                k[0] += 1
            for v in o.values():
                _deduce_walk(v, k)
        elif isinstance(o, list):
            for v in o:
                _deduce_walk(v, k)
    _deduce_walk(D)

    return checks


def g16_narrative_depth(r):
    """G16 防 AI 偷懒·叙事深度硬闸（R100z94）。

    背景：用户复盘满意 A 股深度（板块 285-390 / 题材 371-411 / 要闻 333-431 字），
    但美股 us.* 长期只有 60-110 字（根因：07:00 / 周日两条自动化 prompt 把字数上限
    写成 60-110）。量化页（谐波/九门/吸筹）无叙事字段渲染，其空 story 是设计行为，不在这
    里管；chip 标签类字段（lianban.reason / 各池 note / star.picks.note / failPool.reason）
    也非主叙事，不在这管。本闸只管「真正渲染给用户的长文主字段」。

    阈值（字符数，留足安全垫、远在偷懒区之上）：
        A 股 板块 reason.detail / 题材 logic / 五节要闻 summary → 240
        A 股 outlook / us.outlook → 300
        A 股 aiPrediction.sectors[].logic → 200
        A 股 开盘前瞻 openOutlook → 300
        A 股 lianban[].story → 120；duanban confirmed/watching[].story → 100
        A 股 accumulation.{scored,strong,watch}[].note → 90
        美股 板块 reason.detail / 五节要闻 summary → 200
        美股 顶层 summary（脚本生成，数据驱动多维复盘）→ 150
    低于阈值即 FAIL（拦部署）；阈值取「当前已达标数据的最小值再下探一档」，
    既能挡住偷懒，又不误伤正常波动。

    R100z136：清单采集拆到 collect_g16_checks（与 check_narrative.py 预检共用）。
    """
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G16", f"data.js 解析失败：{e}")
        return

    checks = collect_g16_checks(D)
    fails = []
    for label, text, floor in checks:
        if len(text) < floor:
            fails.append(f"{label} 仅 {len(text)} 字（<{floor}，疑似偷懒/未写完）")
    if fails:
        r.fail("G16", f"发现 {len(fails)} 处叙事字段过短（防 AI 偷懒硬闸）："
               + "；".join(fails[:15]) + (" …" if len(fails) > 15 else ""))
    else:
        r.ok("G16", f"全站叙事字段深度达标（校验 {len(checks)} 处，阈值 15~300 字）")


def collect_g17_owners(D):
    """G17 复读句归属采集（R100z136 从 g17_template_tails 拆出）。

    check_narrative.py 预检复用本函数。返回 {sentence: set(field label)}——
    同一句 ≥14 字被 ≥3 个不同叙述字段共用即判「万能模板尾巴」。
    与 g17_template_tails 共用同一扫描面与阈值，不存在两套口径。
    """
    owner = {}  # sentence -> set(field label)

    def put(text, label):
        # R100z160：分句符补 `；`，与 G23 的 sentences() 对齐。此前只按 。！？ 切，
        # AI 用「；」把多个整句串成一段长句时，被复用的**后半截**落在同一"句"里就躲过检测
        # （2026-10-21 实测：4 只电力股共用同一条 bearRef，板块段写同一句只在 ； 后重复）。
        for s in re.split(r"(?<=[。！？；])", str(text or "")):
            s = s.strip()
            if len(s) >= 14:
                owner.setdefault(s, set()).add(label)

    for mk in ("ashare", "us"):
        sec = D.get(mk) or {}
        for arr in ("sectorsUp", "sectorsDown"):
            for i, s in enumerate(sec.get(arr) or []):
                if isinstance(s, dict):
                    put((s.get("reason") or {}).get("detail"), f"{mk}.{arr}[{i}]")
        for s in ("bullNews", "bearNews", "macroNews", "intlNews", "bankViews"):
            for i, nw in enumerate(sec.get(s) or []):
                if isinstance(nw, dict):
                    put(nw.get("summary"), f"{mk}.{s}[{i}]")
        if sec.get("outlook"):
            put(sec.get("outlook"), f"{mk}.outlook")
    ap = D.get("aiPrediction") or {}
    for i, s in enumerate(ap.get("sectors") or []):
        if isinstance(s, dict):
            put(s.get("logic"), f"aiPrediction.sectors[{i}]")
    for i, t in enumerate((D.get("ashare") or {}).get("themePicks") or []):   # R100z129：与 G16 扫描面保持一致
        if isinstance(t, dict):
            put(t.get("logic"), f"ashare.themePicks[{i}]")
    put(_content_of(D.get("openOutlook")), "openOutlook")
    if isinstance(ap, dict) and ap.get("openOutlook"):
        put(_content_of(ap.get("openOutlook")), "aiPrediction.openOutlook")
    for i, s in enumerate((D.get("ashare") or {}).get("lianban") or []):
        if isinstance(s, dict):
            put(s.get("story"), f"ashare.lianban[{i}]")
    # R100z148：与 G16 扫描面一致——顶层 duanban（ashare.duanban 死节点已剔除）
    dbsec = D.get("duanban") or {}
    for pool in ("confirmed", "watching"):
        for i, e in enumerate(dbsec.get(pool) or []):
            if isinstance(e, dict):
                put(e.get("story"), f"duanban.{pool}[{i}]")
    # ⚠️ 吸筹 note **刻意不纳入 G17**（R100z161c 实测后决定，别再"补进来"）：
    #   它的 90 字地板在 G16 里补了（此前读错路径恒空），但 note 正文由
    #   accumulation_score.py 按形态分支**脚本拼装**，同一形态必然同句——
    #   2026-10-21 试纳入 G17 立即报 8 句复读（最高「3日缩量连跌后已收复过半跌幅；」×40 处），
    #   全部只落在 accumulation 域内。这不是 AI 偷懒，硬拦会天天 FAIL 且 AI 无从修。
    #   要治只能改脚本文案多样性（见 PROJECT-MEMORY 三节），不归 G17 管。
    # R100z161c：9:45 深度分析纳入复读面（AI 手工文本，与 outlook/要闻常出自同一批措辞）
    _star17 = (D.get("duanban") or {}).get("star") or {}
    if isinstance(_star17, dict) and _star17.get("pushText"):
        put(_star17.get("pushText"), "duanban.star.pushText")
    # 全站 deduce
    def _ded_walk(o):
        if isinstance(o, dict):
            dd = o.get("deduce")
            if isinstance(dd, dict):
                lbl = f"deduce#{id(dd) % 10000}"
                put(dd.get("main"), lbl + ".main")
                put(dd.get("counter"), lbl + ".counter")
                for c in dd.get("cands") or []:
                    if isinstance(c, dict):
                        put(c.get("text"), lbl + ".cand")
            for v in o.values():
                _ded_walk(v)
        elif isinstance(o, list):
            for v in o:
                _ded_walk(v)
    _ded_walk(D)

    return owner


def g17_template_tails(r):
    """G17 防 AI 偷懒·防模板复读闸（R100z96）。

    事故：us.* 34 条叙述字段全部以同一句万能尾巴收尾（25×「操作上需结合盘面强度
    与成交量变化动态评估，切忌单因子外推。」+ 9×「映射到A股…避免追高。」）——
    深度达标但内容模板化，用户一眼识破「又偷懒了」。本闸按「同一句 ≥14 字出现在
    ≥3 个不同叙述字段」判万能模板，FAIL 拦部署。扫描面与 G16 相同（真渲染的长文
    主字段 + 全站 deduce 三候选），不含 chip 标签与量化池。

    R100z136：归属采集拆到 collect_g17_owners（与 check_narrative.py 预检共用）。
    """
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G17", f"data.js 解析失败：{e}")
        return

    owner = collect_g17_owners(D)
    dups = sorted(((s, o) for s, o in owner.items() if len(o) >= 3),
                  key=lambda x: -len(x[1]))
    if dups:
        top = dups[:3]
        det = "；".join(f"「{s[:30]}…」×{len(o)} 处" for s, o in top)
        r.fail("G17", f"发现 {len(dups)} 句万能模板复读（≥3 个字段共用同一句，防 AI 偷懒硬闸）：{det}")
    else:
        r.ok("G17", f"无跨字段模板复读（扫描 {sum(len(o) for o in owner.values())} 句次 / {len(owner)} 句）")


def g18_policy_signals(r):
    """G18 政策信号解码闸门（R100z107b）：policySignals 若存在则必须结构合法。

    由 enrich_policy.py 在 17:00 叙事步写回。非 17:00 叙事步部署时该节点可能尚未生成
    （由前一次 17:00 叙事步持久化在 data.js 中），缺失属预期 → WARN 不拦。"""
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G18", f"data.js 解析失败：{e}")
        return
    ps = D.get("policySignals")
    if ps is None:
        r.warn("G18", "policySignals 尚未生成（应由 17:00 叙事步 enrich_policy.py 写回）——"
                     "非 17:00 叙事步部署时为预期，17:00 后会补齐")
        return
    if not isinstance(ps, list):
        r.fail("G18", "policySignals 不是数组")
        return
    req = {"title", "issuer", "docType", "binding", "signalClass",
           "tier", "score", "sectors", "targets", "date", "market"}
    bad = []
    for i, it in enumerate(ps):
        if not isinstance(it, dict):
            bad.append(f"[{i}] 非对象"); continue
        miss = req - set(it.keys())
        if miss:
            bad.append(f"[{i}]{str(it.get('title', ''))[:10]} 缺{miss}")
        if not isinstance(it.get("sectors"), list) or not isinstance(it.get("targets"), list):
            bad.append(f"[{i}] sectors/targets 非数组")
        if it.get("tier") not in ("S", "A", "B", "C"):
            bad.append(f"[{i}] tier 非法 {it.get('tier')}")
    if bad:
        r.fail("G18", "policySignals 结构异常：" + "; ".join(bad[:8]))
    else:
        tiers = {t: sum(1 for x in ps if x.get("tier") == t) for t in "SABC"}
        r.ok("G18", f"policySignals 结构合法（{len(ps)} 条，"
                    f"S{tiers['S']}/A{tiers['A']}/B{tiers['B']}/C{tiers['C']}）")


def g19_summary_freshness(r):
    """G19 板块综述·日期与行情一致性闸门（R100z118，修复 10/12「日期新、内容旧」事故）。

    事故：2026-10-12 的 ashare.tradeDate 已更新为当日，但 ashare.summary 仍是 10/9 的
    旧收盘综述（含「10月9日」「沪指3813.79点涨0.05%」等陈旧点位），上线即「日期更新、
    内容没更新」。本闸检测两类陈旧信号，FAIL 拦部署，强迫 17:00 每轮用当日数据重写：
      ① summary 里出现「M月D日」日期字面量且 ≠ tradeDate 的月日（说明在讲旧交易日）；
      ② summary 里出现收盘型点位（数字须紧贴「点/涨/跌」信号，排除成交额/市值等「X亿元」
         类非指数数值误伤）且数值与当日任一指数 point 偏差 > 2.0（说明在引用旧收盘点位）。
    盘中极值（早盘/盘中/最低/最高/探至/一度/开盘/午盘/尾盘）视为允许，不误伤。"""
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G19", f"data.js 解析失败：{e}")
        return
    a = D.get("ashare") or {}
    summary = a.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        r.ok("G19", "ashare.summary 为空或缺失（跳过，按其他闸覆盖）")
        return
    trade_date = str(a.get("tradeDate") or "")
    # ① 旧日期字面量
    md = re.findall(r"(\d{1,2})月(\d{1,2})日", summary)
    stale_md = []
    if trade_date:
        try:
            t_m, t_d = int(trade_date.split("-")[1]), int(trade_date.split("-")[2])
            for mm, dd in md:
                if (int(mm), int(dd)) != (t_m, t_d):
                    stale_md.append(f"{int(mm)}月{int(dd)}日")
        except Exception:                                     # noqa: BLE001
            pass
    # ② 陈旧收盘点位
    idx_pts = []
    for it in (a.get("indices") or []):
        if isinstance(it, dict) and isinstance(it.get("point"), (int, float)):
            idx_pts.append(float(it.get("point")))
    stale_pts = []
    for num in re.findall(r"(\d{4}\.\d{2})", summary):
        v = float(num)
        if not (1000 <= v <= 20000):
            continue
        if any(abs(v - p) <= 2.0 for p in idx_pts):
            continue
        pos = summary.find(num)
        suffix = summary[pos + len(num):].lstrip() if pos >= 0 else ""
        # 仅认定「收盘型点位」引用：数字须紧贴「点/涨/跌」信号（如「3813.79点」「3813.79涨」），
        # 否则「9876.54 亿元」成交额/市值/净买额等非指数数值会被误伤（宽松的上下文关键词会
        # 因邻近的指数句泄露「点/涨」而误判，故必须锚定在数字本身后缀）。
        if not any(ch in suffix[:3] for ch in ("点", "涨", "跌")):
            continue
        ctx = summary[max(0, pos - 25): pos + len(num) + 25] if pos >= 0 else ""
        if any(k in ctx for k in ("早盘", "盘中", "最低", "最高", "探至", "一度", "开盘", "午盘", "尾盘")):
            continue
        stale_pts.append(f"{num}（上下文：「{ctx.strip()}…」）")
    problems = []
    if stale_md:
        problems.append("含旧交易日日期字面量：" + "、".join(sorted(set(stale_md))))
    if stale_pts:
        problems.append("含与当日指数偏差>2.0的陈旧收盘点位：" + "；".join(stale_pts[:5]))
    if problems:
        r.fail("G19", "ashare.summary 疑似陈旧（日期已更新但内容未重写）：" + "；".join(problems)
               + "——summary 必须每轮按当日 indices/sectors/breadth 重写，不得沿用上一交易日 narrative")
    else:
        r.ok("G19", "ashare.summary 与当日 tradeDate/indices 一致（无陈旧日期或收盘点位）")


def collect_g20_dups(D):
    """G20 跨板块共享模板窗口采集（R100z136 从 g20_sector_deduce_no_template 拆出）。

    check_narrative.py 预检复用本函数。返回 [(mk, window, labels), ...]：
    同一市场内任一 ≥12 字连续子串（含 ≥6 个汉字）同时出现在 ≥3 个板块的
    deduce.main/counter 中即判跨板块模板套话。与 g20 同扫描面同阈值。
    """
    out = []
    for mk in ("ashare", "us"):
        sec = D.get(mk) or {}
        fields = []  # (label, text)
        for arr in ("sectorsUp", "sectorsDown"):
            for i, s in enumerate(sec.get(arr) or []):
                if isinstance(s, dict):
                    dd = s.get("deduce")
                    if isinstance(dd, dict):
                        if dd.get("main"):
                            fields.append((f"{mk}.{arr}[{i}].main", str(dd.get("main"))))
                        if dd.get("counter"):
                            fields.append((f"{mk}.{arr}[{i}].counter", str(dd.get("counter"))))
        win_owner = {}  # window -> set(field label)
        for label, text in fields:
            n = len(text)
            for st in range(0, n - 11):
                w = text[st:st + 12]
                if len(re.findall(r"[\u4e00-\u9fff]", w)) < 6:
                    continue
                win_owner.setdefault(w, set()).add(label)
        for w, o in sorted(win_owner.items(), key=lambda x: -len(x[1])):
            if len(o) >= 3:
                out.append((mk, w, sorted(o)))
    return out


def g20_sector_deduce_no_template(r):
    """G20 板块 deduce 防模板化闸门（R100z118，修复 us 板块 deduce 跨板块套话）。

    事故：us.sectorsUp/Down 的 deduce.main/counter 普遍套用同一模板句（仅换板块名/涨跌幅，
    如「X周五强势收涨，资金主动配置确认主线；跟踪龙头站稳均线即确认延续」），五个板块雷同。
    本闸在「同一市场」内检测：任一 ≥12 字连续子串同时出现在 ≥3 个板块的 deduce.main（或
    counter）中 → 判定为跨板块共享模板，FAIL 拦部署，强迫逐板块按真实驱动重写。
    阈值 12 字 + 跨 3 板块，正常多样化写作不会误伤。

    R100z136：窗口采集拆到 collect_g20_dups（与 check_narrative.py 预检共用）。
    """
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G20", f"data.js 解析失败：{e}")
        return

    dups = collect_g20_dups(D)
    problems = []
    by_mk = {}
    for mk, w, labels in dups:
        by_mk.setdefault(mk, []).append((w, labels))
    for mk, items in by_mk.items():
        top = items[:2]
        det = "；".join(f"「{w}」×{len(o)} 板块" for w, o in top)
        problems.append(f"{mk}：{len(items)} 处跨≥3板块共享≥12字模板句（{det}）")
    if problems:
        r.fail("G20", "板块 deduce 存在跨板块模板套话（防偷懒硬闸）：" + "；".join(problems)
               + "——deduce.main/counter 必须按各板块真实隔夜/当日驱动分别撰写，每板块结论与验证信号必须不同")
    else:
        r.ok("G20", "板块 deduce 无跨≥3板块共享≥12字模板句（逐板块定制）")


def g21_failpool_disjoint(r):
    """G21 失效归档池与在池互斥闸门（R100z120，修复「同一标的既在确认池又在失效池」）。

    事故：2026-10-12 截图发现 603185 弘元绿能、002912 中新赛克等标的同时出现在断板确认池与
    断板失效池。根因：生成侧 roll_fail_pool 滚动保留旧归档时未排除当前已在池的 code——
    某票先失效归档、之后又重新走完形态达标进池，旧归档未清，导致同一 code 两池共存。
    失效池语义本应为「已失效且当前不在池」。修复：common.roll_fail_pool 增 exclude_codes 参数，
    各模块生成脚本在滚动旧归档时剔除当前在池 code（check_duanban / harmonic_detect /
    accumulation_score / power_screener）。本闸做硬校验，防止该 bug 回归：四模块 failPool 的
    code 集合与各自在池（confirmed+watching / confirmPool+watchPool / scored / passed）的
    code 集合必须互斥，有交集即 FAIL。"""
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G21", f"data.js 解析失败：{e}")
        return
    specs = [
        ("duanban", ("confirmed", "watching"), "confirmed+watching"),
        ("harmonic", ("confirmPool", "watchPool"), "confirmPool+watchPool"),
        ("powerScreen", ("passed",), "passed"),
        ("accumulation", ("scored",), "scored"),
    ]
    problems = []
    for mod, fields, label in specs:
        node = D.get(mod) or {}
        in_pool = set()
        for f in fields:
            for it in (node.get(f) or []):
                if isinstance(it, dict) and it.get("code"):
                    in_pool.add(str(it.get("code")))
        fail = node.get("failPool") or []
        fail_codes = set()
        for it in fail:
            if isinstance(it, dict) and it.get("code"):
                fail_codes.add(str(it.get("code")))
        overlap = in_pool & fail_codes
        if overlap:
            problems.append(f"{mod}：{label} 与 failPool 共 {len(overlap)} 只重叠（"
                           + "、".join(sorted(overlap)[:10])
                           + ("…" if len(overlap) > 10 else "") + "）")
    if problems:
        r.fail("G21", "失效归档池与在池非互斥（bug 回归）：" + "；".join(problems)
               + "——失效池语义=已失效且当前不在池；生成侧 roll_fail_pool 必须传 exclude_codes 剔除在池 code")
    else:
        r.ok("G21", "四模块 failPool 与各自在池 code 完全互斥（无重叠）")


def g22_news_level_noise(r):
    """G22 要闻信息等级 + 盘面杂音闸门（R100z157，WARN 级）。

    背景：用户一直以为要闻「按信息等级排列」，实测五节组内完全按 AI 写入顺序渲染，
    没有任何 level 维度；杂音正则（NEWS_STATS_PAT）也只护个股新闻抓取层，AI 把
    「N只股涨停/附股/炸板」写成要闻时无人拦。清洗+打级由 sweep_news.py 在 deploy.sh
    里完成（幂等、零网络）。本闸只做**复核**：
      · 存在盘面统计类标题 → WARN（说明 sweep_news 没跑到或跑失败，或 AI 刚写回新条目）
      · 存在缺 level 的条目 → WARN（同上，16:00/17:00 步骤在部署前跑本闸属正常态）
    刻意设为 WARN 而不 FAIL：17:00 的 prompt 先跑 audit 再 deploy，而 sweep 在 deploy.sh 里，
    若做成 FAIL 会让「AI 刚写好、还没到部署」的正常态直接卡死叙事步。
    """
    try:
        from sweep_news import NEWS_KEYS, is_noise
    except Exception as e:                                   # noqa: BLE001
        r.warn("G22", f"无法导入 sweep_news 口径（{e}），跳过要闻等级复核")
        return
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G22", f"data.js 解析失败：{e}")
        return
    total = missing = 0
    noise = []
    for mk in ("ashare", "us"):
        sec = D.get(mk) or {}
        for key in NEWS_KEYS:
            for it in (sec.get(key) or []):
                if not isinstance(it, dict):
                    continue
                total += 1
                if it.get("level") not in (0, 1, 2):
                    missing += 1
                if is_noise(it.get("title")):
                    noise.append(f"{mk}.{key}: {str(it.get('title'))[:36]}")
    if not total:
        r.ok("G22", "五节要闻为空（非交易日/待写），跳过等级复核")
        return
    if noise:
        r.warn("G22", f"要闻仍含盘面统计类噪音 {len(noise)} 条（deploy.sh 的 sweep_news 会自动剔除）："
               + "；".join(noise[:4]) + ("…" if len(noise) > 4 else ""))
    if missing:
        r.warn("G22", f"{missing}/{total} 条要闻无 level（0重点/1关注/2常规）——"
               "部署前 sweep_news.py 会补齐；若已部署仍缺，说明该跳没跑")
    if not noise and not missing:
        lv = {0: 0, 1: 0, 2: 0}
        for mk in ("ashare", "us"):
            sec = D.get(mk) or {}
            for key in NEWS_KEYS:
                for it in (sec.get(key) or []):
                    if isinstance(it, dict) and it.get("level") in lv:
                        lv[it["level"]] += 1
        r.ok("G22", f"要闻等级齐备且无盘面噪音（{total} 条：重点 {lv[0]} / 关注 {lv[1]} / 常规 {lv[2]}）")


def g23_cross_stock_phrasing(r):
    """G23 跨标的措辞复读软闸（R100z157，WARN 级）。

    背景（审查报告「优化-5」）：G17 拦的是「同一句被 ≥3 个**字段**共用」，而连板 story 的
    【风险】段、断板 story 的尾巴句属于「每只票各有一个独立字段」，同一条模板句在 13 只票里
    各出现一次，G17 的跨字段口径扫不到——整池票的叙事会退化成「只换名字的同一篇文章」。

    判定：把 story 按句切分（。；！？），归一化去空白/标点后统计「同一句出现在几只票」，
    当某句在 ≥3 只票重复且占比 ≥30% 时，判为模板化措辞并 WARN（只提示、不拦部署——
    真实场景里同一板块多只票共享行业背景句是合理的，硬拦会逼 AI 编造差异）。

    只扫两个池：ashare.lianban（连板）与 duanban.confirmed/watching（断板反包）。
    """
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G23", f"data.js 解析失败：{e}")
        return

    def sentences(txt):
        out = []
        for seg in re.split(r"[。；！？\n]", str(txt or "")):
            s = re.sub(r"[\s\u3000]", "", seg)
            if len(s) >= 14:
                out.append(s)
        return out

    pools = []
    lb = [x for x in ((D.get("ashare") or {}).get("lianban") or []) if isinstance(x, dict)]
    if lb:
        pools.append(("连板 story", lb))
    db = D.get("duanban") or {}
    dp = [x for x in (db.get("confirmed") or []) + (db.get("watching") or []) if isinstance(x, dict)]
    if dp:
        pools.append(("断板 story", dp))

    hits, scanned = [], 0
    for label, items in pools:
        cnt = {}
        for it in items:
            txt = it.get("story") or ""
            for s in set(sentences(txt)):
                cnt[s] = cnt.get(s, 0) + 1
                scanned += 1
        n = len(items)
        for s, c in cnt.items():
            if c >= 3 and c / n >= 0.3:
                hits.append(f"{label}「{s[:26]}…」{c}/{n} 只共用")
    if not scanned:
        r.ok("G23", "连板/断板池为空，跳过跨标的措辞复核")
        return
    if hits:
        r.warn("G23", f"同段措辞在多只票间重复（模板化倾向，{len(hits)} 处）：" + "；".join(hits[:4])
               + ("…" if len(hits) > 4 else ""))
    else:
        r.ok("G23", f"连板/断板故事逐票措辞无跨票模板复用（扫描 {scanned} 句次）")


def g24_unchecked_disclosure(r):
    """G24 未核查维度不得写成「已核查无风险」（R100z160）。

    事故（2026-10-21 08:30 实报）：报告里写「黑名单已核查无风险」，而当日
    risk_blacklist.json 实测 nameSrc=local（push2 不可达 → ST/退市维度没判）、
    unknown 188 条全是 np-anotice HTTP 567（减持/业绩预减维度整批没取到）——
    三个维度根本没过数据，却给出了绝对结论。把「未知」当「安全」卖是这类闸门最坏的失效模式：
    黑名单本来就拦不住坏票的「漏网」，再叠一句"已核查"就等于把兜底也拆了。

    判据：risk_blacklist.json 的 verdict != full（或 dims 里存在 unchecked/partial）时，
    data.js 任何渲染文本出现绝对化安全表述即 FAIL；verdict=full 时该表述合法，不拦。
    文本模式取窄（只认明确的"已核查+无风险"组合），避免误伤「无风险偏好」「风险可控」这类正常措辞。
    """
    path = os.path.join(HERE, "risk_blacklist.json")
    if not os.path.exists(path):
        r.ok("G24", "未找到 risk_blacklist.json，跳过未核查披露复核（黑名单未跑，G-黑名单闸自行提示）")
        return
    try:
        bl = json.load(open(path, encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001
        r.fail("G24", f"risk_blacklist.json 解析失败：{e}")
        return
    if not isinstance(bl, dict):
        r.fail("G24", "risk_blacklist.json 结构异常（须为对象）")
        return

    dims = bl.get("dims") or {}
    verdict = bl.get("verdict")
    if verdict is None:          # 旧版 JSON 没有 verdict：按 unknown/failed 推断，不静默放行
        verdict = "full" if not (bl.get("unknown") or bl.get("failed")) else "partial"
    bad_dims = [k for k, v in dims.items()
                if isinstance(v, dict) and v.get("status") not in (None, "ok")]
    if verdict == "full" and not bad_dims:
        r.ok("G24", f"黑名单 {bl.get('checked', 0)} 只各维度均已核查（verdict=full），"
                    f"允许「已核查」表述")
        return

    ABS_PAT = re.compile(
        r"已核查无风险|核查无风险|经核查无风险|核查后无风险|确认无风险"
        r"|黑名单.{0,4}?(?:全部|均已|已)?(?:核查)?(?:通过|完毕|无风险)"
        r"|硬风险(?:已)?全部排除")
    NEG = re.compile(r"(未|没|尚未|无法|不能|不应|不等于|并非)")
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.fail("G24", f"data.js 解析失败：{e}")
        return

    hits = set()
    for p, v in walk_paths(D):
        # walk_paths 对每个标量会产出两次（父键一次、递归叶子一次），这里用集合去重
        if not isinstance(v, str):
            continue
        for m in ABS_PAT.finditer(v):
            pre = v[max(0, m.start() - 4):m.start()]
            if NEG.search(pre):          # 「尚未通过」「不是因为…」属如实/否定表述
                continue
            if re.search(r"未|没|尚未", m.group(0)):   # 「黑名单未全部通过」是如实披露，不算违规
                continue
            hits.add(f"{p}：{m.group(0)}")
    hits = sorted(hits)

    un = bl.get("uncheckedDims") or []
    detail = ("；".join(un[:4]) if un else
              f"verdict={verdict}，未达 ok 的维度："
              f"{'、'.join(bad_dims) or '见 unknown（旧版 JSON 无 dims 字段）'}")
    if hits:
        r.fail("G24", f"黑名单维度未核查（{detail}）却出现绝对安全表述"
                      f"（{len(hits)} 处）：" + "；".join(hits[:4])
                      + (" …" if len(hits) > 4 else "")
                      + "——须改为原样点名未核查维度（R100z160）")
    else:
        # 不占 WARN 基线：维度缺失是数据源问题（每日都会发生），它本身不该成为常态告警；
        # 真正要拦的是「把缺失说成安全」，故判 PASS 但把口径写进输出，方便汇报照抄。
        r.ok("G24", f"黑名单维度未全核查（{detail}）——本次文本无绝对表述；"
                    f"但汇报（微信/HANDOVER）必须原样点名未核查维度，严禁写「已核查无风险」")


def g25_memory_hygiene(r):
    """G25 记忆注入层体检（R100z161）。

    事故（2026-10-21）：`.workbuddy/memory/MEMORY.md` 自称「故意写短」，实际涨到
    **19,929 字节 / 52 行**，其中「维护」4 行占 27%（全是 changelog）。宿主注入时
    **静默截断**，硬约束读不全 —— 当天我就把「8 只四类配比」这条早已写在事实源里的
    规则判成"没写过"，连错两版判据。记忆层不设闸，就会「越优化越读不到」。

    只查两件事：① 体积（>8KB 说明又在堆 changelog）；② 路由表在位（否则不知道去读哪节）。
    只 WARN 不 FAIL —— 记忆臃肿不影响 data.js 正确性，但必须响亮提示。
    """
    path = os.path.join(HERE, ".workbuddy", "memory", "MEMORY.md")
    if not os.path.exists(path):
        r.ok("G25", "本工作区无 MEMORY.md，跳过")
        return
    try:
        raw = open(path, "rb").read()
        txt = raw.decode("utf-8", "ignore")
    except Exception as e:                                   # noqa: BLE001
        r.warn("G25", f"MEMORY.md 读取失败：{e}")
        return
    size, hard = len(raw), 8192
    probs = []
    if size > hard:
        probs.append(f"{size} 字节 > {hard} 上限（超 {size - hard}）——长段结论迁去 PROJECT-MEMORY.md")
    if "先读那一节" not in txt:
        probs.append("缺「要动这块，先读那一节」路由表（没有它就只能凭印象 grep）")
    if probs:
        r.warn("G25", "记忆注入层需整治：" + "；".join(probs) + "（R100z161）")
    else:
        r.ok("G25", f"MEMORY.md {size} 字节，路由表在位")


def g26_pool_sector_keys(r):
    """G26 机械池板块键对齐（R100z163）。

    事故（2026-10-21）：机械池在**前一交易日 17:00** 按当时的 `aiPrediction.sectors` 建键，
    次日 08:30 盘前重新定板块、改几个名字，整批键就对不上——当天实测 6 个板块里 4 个
    「无同名板块」（医疗保健 / 消费电子 / 计算机设备 / 航空运输）。而 `check_stock_pool`
    是按板块名找池的，找不到就只 WARN「点名即可」→ **「④ 断板反包必须取自机械池」
    这条硬约束对这 4 个板块整轮失明**，页面看上去一切正常。
    prompt 当时还写着「严禁带 --write，本任务不重建机械池」，等于把错位锁死一整天。

    修法：08:30 定完板块名后跑一次
        build_stock_pool.py --write --sectors "板块1,板块2,…"
    （只换键不换股，候选仍由四个引擎机械产出）。本闸只做核对，WARN 不拦部署——
    真正的硬拦在 check_stock_pool：键对齐了它才有数据可判。
    """
    pool_path = os.path.join(HERE, "stock_pool.json")
    if not os.path.exists(pool_path):
        r.warn("G26", "未找到 stock_pool.json（build_stock_pool.py 未跑）——机械池没进决策链")
        return
    try:
        pool = json.load(open(pool_path, encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001
        r.warn("G26", f"stock_pool.json 解析失败：{e}")
        return
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.warn("G26", f"data.js 读取失败，跳过键对齐复核：{e}")
        return
    keys = [k for k in (pool.get("bySector") or {}) if str(k).strip()]
    names = [str((s or {}).get("sector") or "").strip()
             for s in ((D.get("aiPrediction") or {}).get("sectors") or [])
             if str((s or {}).get("sector") or "").strip()]
    if not keys or not names:
        r.warn("G26", f"机械池键 {len(keys)} 个 / 预测板块 {len(names)} 个——有一侧为空，无法对齐")
        return

    def segs(x):
        """段级切分（·、/／),与 validate_stocks.check_stock_pool 同一口径。
        不做子串，避免「消费电子」被「消费·食品加工」误牵。"""
        return [y.strip() for y in re.split(r"[·、/／,，\s]+", str(x or "")) if y.strip()]

    def hit(name):
        if name in keys:
            return name
        ss = segs(name)
        if ss:
            for k in keys:
                ks = segs(k)
                if ks and (ss[0] == ks[0] or ss[0] in ks or ks[0] in ss):
                    return k
        return None

    missing = [n for n in names if not hit(n)]
    used = {hit(n) for n in names if hit(n)}
    extra = [k for k in keys if k not in used]
    if not missing and not extra:
        r.ok("G26", f"机械池键与预测板块一一对齐（{len(names)} 个板块，候选数据日 "
                    f"{pool.get('candidateDate') or pool.get('date') or '未知'}）")
        return
    # R100z167：池键漂移本身**不再是缺陷**——check_stock_pool 已改为「键没对上就用全池
    # 断板候选核对」，硬约束永不失明。这里只判定还有没有可核对的断板候选：
    # 有 → ok（记明漂移数量，不给运维派活）；一只都没有 → 那才是真·无货，WARN。
    db_all = [it for v in (pool.get("bySector") or {}).values() for it in (v or [])
              if str(it.get("src") or "").startswith("断板")]
    if db_all:
        r.ok("G26", f"池键与预测板块有 {len(missing)} 个不同名（{('、'.join(missing))}），"
                    f"但全池 {len(db_all)} 只断板候选已兜底核对，「④ 断板取自池内」不失明；"
                    f"08:30 重键（--sectors）可恢复板块级精确核对")
        return
    parts = []
    if missing:
        parts.append(f"{len(missing)} 个预测板块在池里没有键（{('、'.join(missing))}）")
    if extra:
        parts.append(f"池里有 {len(extra)} 个已不用的旧键（{('、'.join(extra))}）")
    r.warn("G26", "机械池键与预测板块错位，且**全池无任何断板候选**："
                  + "；".join(parts) + "——断板槽位整轮只能自选，须在汇报点名（R100z167）")


def g27_memory_reachability(r):
    """G27 记忆可达性 / 时效 / 积压（R100z166，2026-10-21）。

    事故一（可达性，当天真实发生）：`PROJECT-MEMORY.md` 的 `24.11` / `24.12` 被写成
    `## 二十四·十一、` / `## 二十四·十二、`（中文编号 + 误用二级），而 `MEMORY.md`
    路由表写的是阿拉伯数字 `24.11` / `24.12`。结果 `grep 24.11` 只命中 `### 24.11a`，
    **主标题命不中** —— 该节对检索者等于不存在。我当天正是靠记忆里「只看断板槽位」
    那半句去改代码，而同一节的完整判据读不到。
    G25 只数「路由表行数 ≥ N」，**不验证路由指向的目标是否真实存在**，这里补上。

    事故二（时效）：运行期记忆里 7 处「下一个 A 股交易日：2026-10-08，届时…」
    在 13 天后仍以未来时态陈述。记忆是追加式的，**旧条目的「届时」永远停在未来**，
    读到会误判当前状态（以为节后还没开盘）。

    判据（可达性）：`MEMORY.md` 里每个 `24.x` 必须在 `PROJECT-MEMORY.md` 有 `24.x` 开头标题。
      FAIL —— 确定性强、零误报，修法 30 秒（给标题补阿拉伯数字），
      不修就等于「记忆里有一条我永远读不到的规则」。
    判据（时效）：`届时 / 下一个 / 下次` **前 30 字符内**有日期、该日期 < 今天、且整行未带
      快照类标记（`已过期/快照/勿当现状/已作废/已自愈`）→ 陈旧叙述。WARN。
      ⚠️ 窗口必须收窄到 30 字符。首版按「整行任一处过期日期」判，8 条命中里 5 条误报：
        · 常驻提醒「**下次放假前**只需复查这一条还在不在」（旧日期在 500 字外）
        · 已带标记「以下整条是 2026-10-16 深夜的**快照**…勿当现状读」
        · changelog「部署：未提交，延续「等**下次**自动化跑」」（R100zNNN 条目，日期天然在行首）
      真正会让人误判当前状态的只有「日期紧贴关键字」这一种，例如
      「下一个 A 股交易日：**2026-10-08**（国庆休市），届时…」。
    判据（积压）：日志 >30 天未蒸馏 → WARN。本闸顺便充当蒸馏的**执行者**
      （此前该规则只写在记忆里，全项目零执行点，属空转型）。
    """
    base = os.path.join(HERE, ".workbuddy", "memory")
    mem = os.path.join(base, "MEMORY.md")
    pm = os.path.join(base, "PROJECT-MEMORY.md")
    import glob                                              # noqa: PLC0415
    from datetime import date as dt_date                     # noqa: PLC0415
    if not os.path.exists(mem) or not os.path.exists(pm):
        r.ok("G27", "本工作区无 MEMORY.md / PROJECT-MEMORY.md，跳过")
        return
    try:
        mtxt = open(mem, encoding="utf-8", errors="ignore").read()
        ptxt = open(pm, encoding="utf-8", errors="ignore").read()
    except Exception as e:                                   # noqa: BLE001
        r.warn("G27", f"记忆文件读取失败：{e}")
        return

    # ---- 1) 路由可达性：MEMORY.md 引用的 24.x 必须有同名标题 ----
    # ⚠️ 必须要求「标题里的节号与引用**完全相等**」：首版用 `24\.13(?![0-9])`，
    #    结果 MEMORY.md 引 `24.13` 时，`#### 24.13a` 这种子项标题就能满足它 ——
    #    故障注入（把 `### 24.13` 改回 `### 二十四·十三、`）居然仍然 PASS。
    #    而原版 24.11 事故正是这个形状（主标题中文、子标题 24.11a 在）。
    #    收尾改成 `(?![0-9A-Za-z])`，子标题不再冒充主标题。
    refs = sorted({m.group(0) for m in re.finditer(r"24\.\d+[a-z]?", mtxt)})
    missing = [x for x in refs
               if not re.search(r"^#{2,4}\s*" + re.escape(x) + r"(?![0-9A-Za-z])", ptxt, re.M)]
    if missing:
        r.fail("G27", f"MEMORY.md 路由指向的节在 PROJECT-MEMORY.md 里 grep 不到："
                      f"{('、'.join(missing))}——给标题补阿拉伯数字（如「## 二十四·十一」→「### 24.11」）"
                      f"，否则该节等于不存在（R100z166）")
    else:
        r.ok("G27", f"路由可达：MEMORY.md 引用 {len(refs)} 个 24.x，全部命中标题")

    # ---- 2) 时效：关键字前 30 字符内的日期是否已过 ----
    today = dt_date.today()
    KW = ("届时", "下一个", "下次")
    SNAP = ("已过期", "快照", "勿当现状", "已作废", "已自愈")
    DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
    stale = []

    def stale_in(label, txt):
        for i, line in enumerate(txt.split("\n"), 1):
            if any(s in line for s in SNAP):
                continue
            for kw in KW:
                pos = line.find(kw)
                if pos < 0:
                    continue
                near = DATE_RE.findall(line[max(0, pos - 30):pos])
                m2 = re.match(r"[^0-9]{0,10}(20\d{2}-\d{2}-\d{2})", line[pos:])
                cand = near[-1] if near else (m2.group(1) if m2 else None)
                if cand:
                    try:
                        if dt_date.fromisoformat(cand) < today:
                            stale.append(f"{label}:{i}（{cand}）")
                    except ValueError:
                        pass
                break

    stale_in("MEMORY.md", mtxt)
    stale_in("PROJECT-MEMORY.md", ptxt)
    for p in sorted(glob.glob(os.path.join(base, "automations", "*", "memory.md"))):
        try:
            stale_in(os.path.basename(os.path.dirname(p))[:8],
                     open(p, encoding="utf-8", errors="ignore").read())
        except Exception:                                    # noqa: BLE001
            continue
    if stale:
        r.warn("G27", f"{len(stale)} 处「届时/下一个」紧贴已过日期却仍以未来时态陈述"
                      f"（{('、'.join(stale[:6]))}{'…' if len(stale) > 6 else ''}）"
                      f"——行尾加「（⏳ 已过期：…）」，别删原文（R100z166）")

    # ---- 3) 积压：>30 天日志未蒸馏 ----
    olds = []
    for p in sorted(glob.glob(os.path.join(base, "20*.md"))):
        try:
            d = dt_date.fromisoformat(os.path.basename(p)[:-3])
        except ValueError:
            continue
        age = (today - d).days
        if age > 30:
            olds.append(f"{os.path.basename(p)[:-3]}({age}天)")
    if olds:
        r.warn("G27", f"{len(olds)} 份日志超 30 天未蒸馏（{('、'.join(olds[:6]))}）"
                      f"——结论并入 PROJECT-MEMORY.md 后删除，否则检索边界无限扩大（R100z166）")


# G28 分格时间戳表：与 refresh_stamp.py 的 SLOTS **必须逐项一致**，G28 会跨文件并排比。
# 历史 bug 类型就是这类「两处各写一份清单」——只在一边加字段，另一边静默漏取。
STAMP_SLOTS = [
    (("ashare", "updatedAt"), "A股收盘"),
    (("us", "updatedAt"), "美股收盘"),
    (("panorama", "updatedAt"), "全球市场全景"),
    (("harmonic", "updatedAt"), "谐波形态"),
    (("accumulation", "updatedAt"), "吸筹打分"),
    (("powerScreen", "updatedAt"), "量化筛选"),
    (("fullScan", "updatedAt"), "全量扫描"),
    (("stopTrack", "updatedAt"), "止损跟踪"),
]


def _stamp_slots_from_script():
    """从 refresh_stamp.py 源码里取 SLOTS 字面量（不 import，避免执行副作用）。"""
    p = os.path.join(HERE, "refresh_stamp.py")
    try:
        tree = ast.parse(open(p, encoding="utf-8").read())
    except Exception:                                        # noqa: BLE001
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SLOTS" for t in node.targets):
            try:
                return [(tuple(pth), lbl) for pth, lbl in ast.literal_eval(node.value)]
            except Exception:                                # noqa: BLE001
                return None
    return None


def g28_freshness_consistency(r):
    """G28 数据新鲜度一致性（R100z167，2026-10-21，用户实报「怎么那么多遗留问题」）。

    事故一（页头撒谎）：页面头部「数据更新」读 data.js **顶层** `updatedAt`，而这一个字段
    被 5 个脚本各自覆盖 —— 谁最后跑谁赢。实测当天 A股 17:20 已更新，页头还停在
    `16:01（韩股 10/21 收盘 已更新）`；第二天更糟，页头写 10-20 而数据已是 10-21。
    根因不是「某个脚本忘了写」，而是**这个字段没有单一主人**。
    修法：各市场/模块只写自己那一格（`ashare.updatedAt` / `us.updatedAt` /
    `panorama.updatedAt` / 四模块 / `stopTrack`），部署时由 `refresh_stamp.py` 取**最大
    时间戳**生成顶层文案 —— 单一来源、幂等。本闸守的就是这件事有没有真的发生。

    事故二（ashare 那格没人写）：`ashare.updatedAt` 长期由 AI 手写，脚本只写 tradeDate。
    于是出现「`ashare.updatedAt` 仍显示 10-20 17:22」被当成免责声明挂在报告里好几天。
    现在 `update_ashare_sectors.py` 与 tradeDate/status **同进同退**，本闸校验同日。

    判据：
      a) 顶层 `updatedAt` 的时间戳 == 各分格里的最大值。不等 → FAIL，
         修法一行：跑 `refresh_stamp.py`（deploy.sh 已内置，说明有人绕过 deploy 直接改了数据）。
      b) `ashare.updatedAt` 的日期 == `ashare.tradeDate`。缺/不同日 → FAIL。
      c) 各分格戳日期早于 `ashare.tradeDate` → WARN（该格是旧交易日的货，但页面照挂着）。
      d) `refresh_stamp.py` 的 SLOTS 与本地 STAMP_SLOTS 不一致 → FAIL（清单漂移＝静默漏取）。
      e) `duanban.generatedAt` / 四模块 `tradeDate` 早于 `ashare.tradeDate` → WARN。
    非交易日（status 含「休市」）或 tradeDate 不可解析时整条跳过 —— 休市期数据不动是正常的。
    """
    try:
        D = load_data_json()
    except Exception as e:                                   # noqa: BLE001
        r.warn("G28", f"data.js 解析失败，跳过新鲜度一致性核对：{e}")
        return

    # ---- d) 清单漂移（先查，后面两条判据都依赖它）----
    script_slots = _stamp_slots_from_script()
    if script_slots is None:
        r.warn("G28", "读不到 refresh_stamp.py 的 SLOTS（文件缺失或写法变了），"
                      "无法并排比对各格清单，请确认该脚本仍在部署链里")
    else:
        mine = {tuple(p) for p, _ in STAMP_SLOTS}
        theirs = {tuple(p) for p, _ in script_slots}
        if mine != theirs:
            only_a = sorted(".".join(p) for p in mine - theirs)
            only_b = sorted(".".join(p) for p in theirs - mine)
            r.fail("G28", f"分格清单漂移：audit 有而 refresh_stamp 没有 {only_a or '无'}；"
                          f"refresh_stamp 有而 audit 没有 {only_b or '无'} —— "
                          f"两处必须逐项一致，否则新加的那一格永远进不了页头（R100z167）")
        else:
            r.ok("G28", f"分格清单一致：{len(mine)} 格（ashare/us/panorama/四模块/stopTrack）")

    a = D.get("ashare") or {}
    td = str(a.get("tradeDate") or "").strip()[:10]
    status = str(a.get("status") or "")
    if ("休市" in status) or ("非交易日" in status) or not re.match(r"^20\d{2}-\d{2}-\d{2}$", td):
        r.ok("G28", f"非交易日/休市（status={status or '空'}，tradeDate={td or '空'}），"
                    f"跳过新鲜度一致性核对")
        return

    TS = re.compile(r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})")

    def dig(path):
        cur = D
        for k in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(k)
        return cur

    # ---- b) ashare 那格必须与 tradeDate 同日 ----
    m = TS.search(str(a.get("updatedAt") or ""))
    if not m:
        r.fail("G28", "ashare.updatedAt 缺失或没有可解析时间戳 —— "
                      "update_ashare_sectors.py 必须与 tradeDate/status 同时写入该格（R100z167）")
    elif m.group(1) != td:
        r.fail("G28", f"ashare.updatedAt 停在 {m.group(1)}，而 tradeDate 已是 {td} —— "
                      f"该格只能由脚本写，AI 手写/漏写都会造成新鲜度谎报（R100z167）")

    # ---- a) 顶层 = 各格最大值 ----
    rows, old_slots = [], []
    for path, label in STAMP_SLOTS:
        v = dig(path)
        mm = TS.search(str(v or ""))
        if not mm:
            continue
        rows.append(("%s %s" % (mm.group(1), mm.group(2)), label))
        if mm.group(1) < td:
            old_slots.append(f"{label}={mm.group(1)}")
    if not rows:
        r.warn("G28", "各分格都没有可解析的 updatedAt，无法核对页头权威值")
    else:
        best_ts, best_label = max(rows, key=lambda x: x[0])
        top = TS.search(str(D.get("updatedAt") or ""))
        if not top:
            r.fail("G28", "顶层 updatedAt 缺失/不可解析 —— 页头「数据更新」会空白")
        else:
            top_ts = "%s %s" % (top.group(1), top.group(2))
            if top_ts != best_ts:
                r.fail("G28", f"页头时间戳 {top_ts} ≠ 各格最新 {best_ts}（{best_label}）—— "
                              f"顶层 updatedAt 被某个脚本覆盖成自己的时间了。"
                              f"修法：跑 refresh_stamp.py（deploy.sh 已内置该步）（R100z167）")
            else:
                r.ok("G28", f"页头时间戳 = 各格最新 {top_ts}（{best_label}）")

    # ---- c) 分格戳落后于当日交易日 ----
    if old_slots:
        r.warn("G28", f"{len(old_slots)} 格戳早于交易日 {td}：{'、'.join(old_slots[:5])}"
                      f"——该格内容是旧交易日的货，页面照挂着（非交易日误标时属正常）")

    # ---- e) 四模块/断板 快照日期落后 ----
    lag = []
    for key in ("harmonic", "accumulation", "powerScreen", "fullScan"):
        v = str((D.get(key) or {}).get("tradeDate") or "")[:10]
        if v and v < td:
            lag.append(f"{key}.tradeDate={v}")
    dg = str((D.get("duanban") or {}).get("generatedAt") or "")[:10]
    if re.match(r"^20\d{2}-\d{2}-\d{2}$", dg) and dg < td:
        lag.append(f"duanban.generatedAt={dg}")
    if lag:
        r.warn("G28", f"模块快照落后于交易日 {td}：{'、'.join(lag)}"
                      f"——16:00 数据步漏跑或未换日，页面对应板块展示的是旧交易日")


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
        g11_smoke(r, deep=deep)
    else:
        g11_smoke(r, deep=False)
    g14_prompt_broken_path(r)
    g15_interp_bins(r)
    g16_narrative_depth(r)
    g17_template_tails(r)
    g18_policy_signals(r)
    g19_summary_freshness(r)
    g20_sector_deduce_no_template(r)
    g21_failpool_disjoint(r)
    g22_news_level_noise(r)
    g23_cross_stock_phrasing(r)
    g24_unchecked_disclosure(r)
    g25_memory_hygiene(r)
    g26_pool_sector_keys(r)
    g27_memory_reachability(r)
    g28_freshness_consistency(r)

    if as_json:
        print(json.dumps({"counts": r.counts, "items": r.items}, ensure_ascii=False, indent=2))
    else:
        groups = []
        for g, lv, msg in r.items:
            if not groups or groups[-1][0] != g:
                groups.append((g, []))
            groups[-1][1].append((lv, msg))
        print("=" * 78)
        print(" audit_pipeline · 七自动化与流水线一次性机器验收 (R100z17)")
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

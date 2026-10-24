#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_code.py · 脚本层机检（R100z179b）

背景（2026-10-23 用户第四轮追问「怎么每天都能查出问题？认真查了没？要下一次检查没问题」）：

前三轮查出来的东西有个共同点 —— **全部位于「脚本怎么写盘 / 怎么写时间戳 / 怎么回填」这一层**，
而现有闸门（audit_pipeline / check_automations）查的是「data.js 长什么样」和「prompt 说了什么」，
**中间那一层没人管**。于是同类问题换个脚本又冒出来：你修 `retag_duanban.py` 的原子写，
另外三个脚本（`macro_calendar` / `strategy_engine` / `sweep_news`）还在直接 `open(path,'w')` ——
其中两个就在 deploy.sh 的链条中间，被 kill 一次就是 0 字节 data.js、全站白屏、日志一行字、闸门全绿。

本脚本把这一层里**可判定**的部分固化成代码，让「脚本层有没有问题」也变成 exit code：

  K1 语法       全部 .py 过 py_compile、全部 .sh 过 `bash -n`
  K2 本地导入     `from common import x` 里的 x 在 common.py 里真的定义了
                 （改函数名忘改引用 = NameError 炸全链，硬约束 8 的自动化版）
                 反向也查：**调用了本地模块的公共函数，本文件就必须导入过它**
                 （2026-10-23 实撞：sweep_news 换成 write_data_js 却漏了 import，
                  py_compile 绿、正向 K2 绿，最后靠 audit 的 pyflakes 才抓到）
  K3 写盘原子性   data.js 写者必须登记在册，且每个成员都有原子证据
                 （`os.replace` 或 `common.write_data_js`）；新写者未登记 → FAIL
                 —— 硬约束 11 的自动化版：**谁漏了**不再靠人看
  K4 网络超时     `requests.get/post`、`urlopen` 等直调必须带 `timeout=`
                 （不带就是「可能永久挂住」，而平台 90 分钟硬上限会直接杀掉整条链）
  K5 死脚本       既没被 7 份 prompt / deploy.sh 引用、也没被任何脚本 import 的 .py
                 → WARN（手工工具是合法的，故不 FAIL；只防「写了忘接」）
  K6 字段契约     7 份 prompt 里出现的 `a.b.c` 路径，逐个对着 data.js 下钻
                 （`panorama.us` / `aiPrediction.source` 这类「看着合理但不存在」的路径，
                 会让 AI 白跑一步或写出野字段 —— L18 的自动化版）
  K7 工序锁       会写 data.js 的自动化必须 `pipeline_lock.py acquire` + `release`
                 （R100z180 实撞：周日 21:00/22:00 两条都漏了锁，60 分钟间隔 + 整份读改写
                 = 互相覆盖；「谁该持锁」以前只写在记忆里没有判据 → 现在有 exit code）

★ 写规则时踩过的两个坑（别重犯）：
  1. **`s.get(...)` 不是网络调用**：`s` 在多数脚本里是 dict，`s.get('f5Tier')` 是 dict.get。
     用 `ast.unparse(func)` 精确匹配 `requests.get` 之类才不误报（首版误报 90+ 处）。
  2. **硬编码日期不能当 bug**：`cal_factor` / `common` 的节假日表、每个文件头的 changelog
     都是合法日期字面量。想查「写死今天的日期」只能查**代码路径**而非字符串，收益低于噪声，故不做。

用法:
    python3 check_code.py            # 全部检查（FAIL 时 exit 1）
    python3 check_code.py --verbose  # 附明细（谁引用了谁、每个写者的原子实现位置）
    python3 check_code.py --only K3  # 只跑某一类（K1-K8）
"""
import argparse
import ast
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.expanduser('~/.workbuddy/workbuddy.db')

# ---------------------------------------------------------------- 登记表
# data.js 的写者（整份读-改-写）。新增写者必须在这里登记 —— K3 会做双向对账：
# 表里有、盘上没有 → FAIL（脚本被删，登记该清）；盘上有、表里没有 → FAIL（新写者未审）。
# 原子实现的两种合法形态：调 `common.write_data_js()`（推荐，唯一入口），
# 或脚本内自己 `.tmp` + `os.replace`（历史实现，等价安全）。
DATA_WRITERS = [
    'accumulation_score.py',    # 吸筹打分写 pool / 分数
    'annotate_pool_meta.py',    # 池元信息
    'apply_narrative.py',       # AI 叙事写回
    'atr_stop.py',              # R100z160：atrStops（全站唯一权威止损源）
    'build_stock_pool.py',      # 机械选股池
    'common.py',                # write_data_js() 本体（唯一入口，自身即实现）
    'embed_klines.py',          # 内嵌 stkKlines
    'enrich_policy.py',         # policySignals 解码
    'entry_zone.py',            # R100z194：aiPrediction.sectors[].fundCheck/entry（走 write_data_js）
    'fix_dd_prefix.py',         # 去掉「DD」前缀
    'forecast_scorecard.py',    # R100z161：forecastScore（预测成绩单；只读 calibration_log.json）
    'full_scan.py',             # 全市场扫描
    'harmonic_detect.py',       # 谐波形态
    'lead_lag.py',              # R100z190：duanban.leadLag（强板块落后股；走 write_data_js）
    'lhb.py',                   # R100z195：ashare.lhb（龙虎榜席位结构；走 write_data_js）
    'macro_calendar.py',        # macroCalendar（走 write_data_js）
    'market_bench.py',          # 行业基准回填
    'market_structure.py',      # 市场结构
    'merge_duanban.py',         # 断板合并
    'power_screener.py',        # 强势筛选
    'refresh_stamp.py',         # updatedAt 单一主人（正则手术式改写）
    'retag_duanban.py',         # 断板重打标
    'risk_gate.py',             # 个股风险闸
    'signal_attribution.py',    # R100z160：signalPerf（指标归因台账）
    'signal_rank.py',           # R100z160：tradeDesk（五因子排名与执行清单）
    'star_ai_rewrite.py',       # 09:45 精选文案
    'stock_news.py',            # stockNews
    'stop_tracker.py',          # 止损跟踪
    'strategy_engine.py',       # strategy 卡片（走 write_data_js）
    'sweep_news.py',            # 要闻分级（走 write_data_js）
    'sync_names.py',            # 简称同步
    'update_ashare_sectors.py',  # A 股板块
    'update_kr_jp.py',          # 日韩早盘
    'update_star.py',           # 09:45 精选落库
    'update_us_from_quotes.py',  # 美股行情
    'validate_stocks.py',       # 标的校验（--no-fix 关掉重排写回）
]

# 「原子写 + 提到 data.js，但写的不是 data.js」—— 明确豁免，防止 heuristic 误判。
# 判据统一：detected = 文本含 data.js 且真的调用 os.replace/write_data_js。
# 落在 detected 里却不在下面两张表任何一张的 → K3 FAIL（新写者必须审一遍）。
NON_DATA_WRITERS = {
    'kline_cache.py': '原子写的是 K 线缓存文件，只是顺带解析 data.js',
    'pipeline_lock.py': '原子写的是锁文件 / 心跳',
}

# 死脚本白名单：合法的「手工跑、不进自动化」工具，写明用途才准留
DEAD_OK = {
    'fix_dd_prefix.py': '一次性修数工具（历史 DD 前缀），手工触发',
    'track_calibration.py': '校准曲线统计，手工触发',
    'score_openoutlook.py': '开盘前瞻打分，手工/被 import',
    'stock_fundflow.py': '个股资金流对照，手工触发',
    'decode_policy.py': '政策词表纯函数，被 enrich_policy import',
    'kline_cache.py': 'K 线缓存模块，被 accumulation_score import',
}

NET_CALLS = {
    'requests.get', 'requests.post', 'requests.put', 'requests.head',
    'urlopen', 'urllib.request.urlopen', 'urllib2.urlopen',
    'httpx.get', 'httpx.post',
}


# ---------------------------------------------------------------- 记录器
class R:
    def __init__(self):
        self.rows = []
        self.fail_n = self.warn_n = self.pass_n = 0

    def ok(self, code, msg):
        self.pass_n += 1
        self.rows.append(('PASS', code, msg))

    def warn(self, code, msg):
        self.warn_n += 1
        self.rows.append(('WARN', code, msg))

    def fail(self, code, msg):
        self.fail_n += 1
        self.rows.append(('FAIL', code, msg))

    def dump(self):
        print()
        print('-' * 78)
        for st, code, msg in self.rows:
            print('  %-4s %-3s %s' % (st, code, msg))
        print('-' * 78)
        print('  PASS=%d  WARN=%d  FAIL=%d' % (self.pass_n, self.warn_n, self.fail_n))
        print('-' * 78)
        return 1 if self.fail_n else 0


# ---------------------------------------------------------------- 工具
def _py_files():
    return sorted(glob.glob(os.path.join(HERE, '*.py')))


def _read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def _module_names(src):
    """模块级可见名字：def/class/赋值/annassign/import/try 内的赋值。"""
    names = set()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add(a.asname or a.name.split('.')[0])
    return names


# ---------------------------------------------------------------- K1
def k1_syntax(r):
    bad = []
    for p in _py_files():
        tmp = tempfile.mktemp(suffix='.pyc')
        try:
            import py_compile
            py_compile.compile(p, cfile=tmp, doraise=True)
        except Exception as e:
            msg = [x for x in str(e).strip().split('\n') if x.strip()]
            bad.append('%s: %s' % (os.path.basename(p), (msg[-1] if msg else '')[:140]))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    if bad:
        for b in bad:
            r.fail('K1', 'Python 语法错误：%s' % b)
    else:
        r.ok('K1', '%d 个 .py 全部通过 py_compile' % len(_py_files()))

    shs = sorted(glob.glob(os.path.join(HERE, '*.sh')))
    shbad = []
    for p in shs:
        cp = subprocess.run(['bash', '-n', p], capture_output=True, text=True)
        if cp.returncode != 0:
            shbad.append('%s: %s' % (os.path.basename(p), (cp.stderr or '').strip()[:120]))
    if shbad:
        for b in shbad:
            r.fail('K1', 'Shell 语法错误：%s' % b)
    else:
        r.ok('K1', '%d 个 .sh 全部通过 bash -n' % len(shs))


# ---------------------------------------------------------------- K2
def k2_imports(r):
    mods = {os.path.splitext(os.path.basename(p))[0]: p for p in _py_files()}
    probs = []
    for p in _py_files():
        try:
            tree = ast.parse(_read(p))
        except SyntaxError:
            continue        # K1 已报
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level or node.module not in mods:
                continue
            have = _module_names(_read(mods[node.module]))
            for a in node.names:
                if a.name == '*':
                    continue
                if a.name not in have:
                    probs.append('%s:%d 从 %s 导入 %s，但该模块没有这个符号'
                                 % (os.path.basename(p), node.lineno, node.module, a.name))
    if probs:
        for x in probs:
            r.fail('K2', x + ' —— 改函数名忘改引用会 NameError 炸全链')
    else:
        r.ok('K2', '本地模块导入符号全部存在（%d 个模块互查）' % len(mods))


def _bound_names(tree):
    """本文件内「已绑定」的名字全集（保守：局部变量也算绑定，宁可漏报不误杀）。"""
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name == '*':
                    continue
                bound.add((a.asname or a.name).split('.')[0])
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Global):
            bound.update(node.names)
    return bound


def k2b_unbound(r):
    """K2 反向：调用了本地模块的公共函数，但本文件没有导入 / 没有定义。

    为什么需要它（2026-10-23 实撞，别重犯）：`sweep_news.py` 把 `open(path,'w')` 换成
    `write_data_js(...)` 时**漏了那行 import**。当时 K1 `py_compile` 通过（语法完全合法）、
    K2 正向检查也通过（文件里压根没有 import 语句可供对账）—— 这一层是**空的**，
    最后是 audit_pipeline 的 pyflakes 抓出来的。为了让「脚本层闸门」自己就能拦住，
    这里补一条正向可判的规则：**调用了本地模块的公共函数 → 必须在本文件里绑定过同名名字**。

    保守口径：名字在任意位置做过 Store（含局部变量、函数参数、循环变量、except 名）
    都算已绑定；`from m import *` 视为全绑定。宁可漏报，不可把正常代码判死。
    """
    mods = {os.path.splitext(os.path.basename(p))[0]: p for p in _py_files()}
    # 候选名字集：本地模块**模块级公开 def/class**。为什么不从「已有的 import 语句」反推？
    # 因为那样会漏掉本案：sweep_news 是**唯一**导入 write_data_js 的文件，它漏了 import 之后
    # 整个项目里就没有一条 `from common import write_data_js`，反向规则直接空转（首版实测
    # 反向测试拿到「无可互查的本地函数名」，闸门假绿）。改成以「谁定义了」为锚才抓得住。
    localfn = {}
    for name, p in mods.items():
        try:
            tree = ast.parse(_read(p))
        except SyntaxError:
            continue
        for node in tree.body:            # 只看模块顶层，函数内嵌套 def 不算公共 API
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith('_'):
                    localfn.setdefault(node.name, set()).add(name)
    if not localfn:
        r.ok('K2', '无可互查的本地公共函数名')
        return
    probs = []
    for p in _py_files():
        base = os.path.basename(p)
        src = _read(p)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        bound = _bound_names(tree)
        star = [n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and not n.level and n.module in mods
                and any(a.name == '*' for a in n.names)]
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Name):
                continue
            name = n.func.id
            if name not in localfn or name in bound:
                continue
            if any(name in _module_names(_read(mods[m])) for m in star):
                continue
            probs.append('%s:%d 调用了 %s（定义在 %s），但本文件没有导入它'
                         % (base, getattr(n, 'lineno', 0), name,
                            '/'.join(sorted(localfn[name]))))
    if probs:
        for x in probs:
            r.fail('K2', x + ' —— 语法合法但一跑就 NameError')
    else:
        r.ok('K2', '本地公共函数调用点全部有配套导入（%d 个名字互查）' % len(localfn))


# ---------------------------------------------------------------- K3
def _uses_atomic(src):
    """AST 判定「真的调用了原子写」—— 不能只做字符串匹配：
    check_code.py 自己的文档里就写着 write_data_js，纯文本匹配会把它算成写者。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = ast.unparse(node.func)
        if fn.endswith('write_data_js') or fn == 'os.replace':
            return True
    return False


def k3_atomic(r, verbose=False):
    """data.js 写者的双向对账 + 裸写检测。

    检测口径（唯一，别改）：detected = 源码文本含 `data.js` 且 AST 里真的调用了
    `os.replace` 或 `common.write_data_js`。用 AST 而不是字符串，是因为
    `check_code.py` 自己的文档里就写着这两个名字，纯文本匹配会把它算成写者。
    """
    detected = set()
    for p in _py_files():
        src = _read(p)
        if 'data.js' not in src:
            continue
        if _uses_atomic(src):
            detected.add(os.path.basename(p))

    reg = set(DATA_WRITERS)
    known = reg | set(NON_DATA_WRITERS)

    unknown = sorted(detected - known)
    missing = sorted(reg - detected)
    if unknown:
        r.fail('K3', '新 data.js 写者未登记 %d 个：%s —— 先确认用 write_data_js() 或 '
                     '`.tmp`+os.replace 再登记进 DATA_WRITERS；写的是别的文件则登记 NON_DATA_WRITERS'
                     % (len(unknown), '、'.join(unknown[:6]) + ('…' if len(unknown) > 6 else '')))
    if missing:
        r.fail('K3', '登记的写者盘上无「data.js + 原子写」证据 %d 个：%s —— 脚本被删要清登记，'
                     '否则是原子写被回退' % (len(missing), '、'.join(missing[:6]) + ('…' if len(missing) > 6 else '')))
    if not unknown and not missing:
        r.ok('K3', 'data.js 写者双向对账一致：登记 %d 个 / 盘上检出 %d 个 / 豁免 %d 个'
                    '（全部具备原子写证据）' % (len(reg), len(detected), len(NON_DATA_WRITERS)))
        if verbose:
            for m in sorted(reg):
                print('      %-30s ← 已登记' % m)
            for m in sorted(NON_DATA_WRITERS):
                print('      %-30s ← 豁免：%s' % (m, NON_DATA_WRITERS[m]))

    # 裸写检测：登记成员里若出现「目标是 data.js 的 open(...,'w') 且不带 tmp」→ FAIL
    naked = []
    for m in sorted(reg & detected):
        src = _read(os.path.join(HERE, m))
        if 'write_data_js' in src and m != 'common.py':
            if re.search(r'window\.DASHBOARD_DATA\s*=\s*[\'"]', src):
                naked.append('%s 同时调 write_data_js 又自己拼 data.js 头' % m)
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'open'):
                continue
            mode = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for kw in node.keywords:
                if kw.arg == 'mode' and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            if not mode or not (set(mode) & set('wax')):
                continue
            tgt = ast.unparse(node.args[0]) if node.args else ''
            if 'tmp' in tgt.lower():
                continue
            if re.search(r"data\.js|^DATA(_JS|_PATH)?$|^path$|^DATA$", tgt):
                naked.append('%s:%d open(%s, %r) 未走临时文件' % (m, node.lineno, tgt, mode))
    if naked:
        for x in naked:
            r.fail('K3', '非原子写 data.js：%s —— 进程被杀即 0 字节，全站白屏' % x)
    else:
        r.ok('K3', '未发现绕过临时文件的 data.js 裸写')

    k3b_json(r)


# 关键 JSON 状态文件：被杀不会白屏，但会变成**静默降级**——
# `_cal_factor()` 读到半截 JSON → 走 except → 用默认阈值继续跑，
# 「后验校准」这个功能从此安静地不生效。比白屏更难发现，所以同样要机器盯。
CRITICAL_JSON = (
    'stock_pool.json',
    'calibration_state.json',
    'calibration_log.json',
    'portfolio_curve.json',
    'strategy_feedback.json',
    'market_bench.json',
)


def _const_map(tree):
    """模块级「常量名 → 值表达式文本」，用来把 `open(LOG_FILE, 'w')` 还原成真实文件名。"""
    m = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            m[node.targets[0].id] = ast.unparse(node.value)
    return m


def k3b_json(r):
    """关键 JSON 的裸写扫描（全量 .py，不限登记表成员）。

    为什么单列：硬约束 11 只点了 `data.js` / `stock_pool.json`，于是 2026-10-23 一扫发现
    `calibration_state` / `calibration_log` / `portfolio_curve` / `strategy_feedback`
    共 5 处仍是裸 `open(...,'w')`（`track_calibration` 4 处 + `strategy_engine` 1 处）。
    规则本身不新，缺的是**覆盖面** —— 所以这里不按登记表扫，按「目标文件名」扫。
    常量要穿透：源码里写的是 `open(LOG_FILE, 'w')`，字面只有 `LOG_FILE`。
    """
    bad = []
    for p in _py_files():
        base = os.path.basename(p)
        src = _read(p)
        if not any(t in src for t in CRITICAL_JSON):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        cmap = _const_map(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'open' and node.args):
                continue
            mode = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for kw in node.keywords:
                if kw.arg == 'mode' and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            if not mode or not isinstance(mode, str) or not (set(mode) & set('wax')):
                continue
            tgt = ast.unparse(node.args[0])
            real = cmap.get(tgt, tgt) if isinstance(node.args[0], ast.Name) else tgt
            hit = [t for t in CRITICAL_JSON if t in real or t in tgt]
            if not hit:
                continue
            if 'tmp' in tgt.lower() or 'tmp' in real.lower():
                continue
            bad.append('%s:%d open(%s, %r) → %s 未走临时文件'
                       % (base, node.lineno, tgt, mode, hit[0]))
    if bad:
        for x in bad:
            r.fail('K3', '关键 JSON 非原子写：%s —— 被杀留半截，下游 except 后静默降级'
                         '（改用 common.write_json_atomic）' % x)
    else:
        r.ok('K3', '关键 JSON（%d 类）全部走原子写' % len(CRITICAL_JSON))


# ---------------------------------------------------------------- K4
def k4_timeout(r):
    bad = []
    for p in _py_files():
        try:
            tree = ast.parse(_read(p))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = ast.unparse(node.func)
            if fn not in NET_CALLS:
                continue
            if 'timeout' not in {k.arg for k in node.keywords}:
                bad.append('%s:%d %s(...) 未设 timeout' % (os.path.basename(p), node.lineno, fn))
    if bad:
        for x in bad:
            r.fail('K4', '%s —— 无超时的网络调用可能永久挂住，平台 90 分钟上限会杀掉整条链' % x)
    else:
        r.ok('K4', '直连网络调用全部显式设了 timeout（%d 类调用点）' % len(NET_CALLS))


# ---------------------------------------------------------------- K6
NEG_RE = re.compile(r'严禁|禁止|不得|勿写|禁写|不写|别写|不可写|不要写'
                     r'|不再写|不要再写|已删|已删除|已从 data\.js 删除')
# R100z171：`不要再写 X` / `X 已删` 是比 `不要写` 更常见的写法，旧词表只认后者，
# 于是「已删节点」的说明句仍被当成「引用不存在路径」报 FAIL（本轮实测踩到）。
# 只影响「该行是否跳过」，不改变下钻判据。
PATH_RE = re.compile(r'(?<![\w.])([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*){1,4})(?![\w.])')


def _drill(D, parts):
    cur = D
    for k in parts:
        if isinstance(cur, list):
            cur = cur[0] if cur else None
            if not isinstance(cur, dict):
                return False
        if not isinstance(cur, dict) or k not in cur:
            return False
        cur = cur[k]
    return True


def k6_contract(r, verbose=False):
    """prompt 里的字段路径 → 对着 data.js 下钻。

    两条降噪规则（都是踩出来的，别删）：
      ① **整行否定语境**：`严禁写 panorama.kr/jp` 这类禁令里出现的路径不是「要读的路径」；
      ② **斜杠简写**：`panorama.kr/jp` 是「kr 和 jp 两项」的口语写法，路径本身只到 `panorama.kr`。
    跳掉这两类之后，剩下的命中都是真问题（本轮据此修掉周末 21:00 的 `panorama.us`）。
    """
    path = os.path.join(HERE, 'data.js')
    if not os.path.exists(path):
        r.warn('K6', 'data.js 不存在，跳过字段契约下钻')
        return
    src = _read(path)
    m = re.search(r'window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$', src)
    if not m:
        r.fail('K6', 'data.js 里找不到 window.DASHBOARD_DATA，契约无基准可比')
        return
    try:
        D = json.loads(m.group(1))
    except Exception as e:
        r.fail('K6', 'data.js 不是合法 JSON（%s），契约无基准可比' % str(e)[:80])
        return
    top = set(D)
    try:
        con = sqlite3.connect(DB)
        rows = [(nm, pm or '') for nm, pm in con.execute(
            "select name,prompt from automations where prompt like '%run-python %' order by name")]
        con.close()
    except Exception as e:
        r.warn('K6', '读不到自动化库（%s），跳过字段契约下钻' % e)
        return

    total = bad_total = 0
    for nm, pm in rows:
        bad = []
        for mm in PATH_RE.finditer(pm):
            path_txt = mm.group(1)
            parts = path_txt.split('.')
            if parts[0] not in top:
                continue
            if pm[mm.end():mm.end() + 1] == '/':          # 斜杠简写
                continue
            ls = pm.rfind('\n', 0, mm.start()) + 1
            le = pm.find('\n', mm.end())
            line = pm[ls:(len(pm) if le < 0 else le)]
            if NEG_RE.search(line):
                continue
            total += 1
            if not _drill(D, parts):
                bad.append((path_txt, line.strip()[:60]))
        for path_txt, line in bad:
            bad_total += 1
            r.fail('K6', '%s 里引用了不存在的路径 %s —— 该行：%s' % (nm, path_txt, line))
        if verbose:
            print('      %-28s 路径 %d 条' % (nm, total))
    if not bad_total:
        r.ok('K6', '7 份 prompt 的 %d 条字段路径全部能下钻到真实数据（已过滤禁令语境与斜杠简写）' % total)


# ---------------------------------------------------------------- K7
def k7_lock(r, verbose=False):
    """K7 工序锁覆盖：会写 data.js 的自动化必须持锁。

    R100z180 实撞：21:00 与 22:00 两条都没接 pipeline_lock —— 周日 21:00 预计 55-70 分钟、
    22:00 只隔 60 分钟，两条都对 data.js 做整份读-改-写，重叠即互相覆盖（对方刚落的整段
    字段被抹掉，症状是「某模块突然变回旧值」，日志里查不出谁干的）。16:00/19:30 早在
    24.27 就接了锁，周日这两条整条漏了 —— 因为「谁该持锁」一直只写在记忆里，没有判据。

    判据（宽进严出，只看「写不写」和「有没有 acquire/release」）：
      prompt 出现 `apply_narrative` 或「写回 … data.js」→ 必须同时出现
      `pipeline_lock.py acquire` 与 `pipeline_lock.py release`。
    """
    try:
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        rows = [(x['name'], x['prompt'] or '') for x in con.execute(
            "select name,prompt from automations where cwds like '%market dashboard%'")]
        con.close()
    except Exception as e:                                   # noqa: BLE001
        r.warn('K7', '读不到自动化库（%s），跳过工序锁覆盖检查' % e)
        return
    if not rows:
        r.warn('K7', '库里没有看板自动化，跳过工序锁覆盖检查')
        return

    bad, held = [], 0
    for nm, pm in rows:
        writes = ('apply_narrative' in pm) or bool(re.search(r'写回[^\n]{0,60}data\.js', pm))
        if not writes:
            continue
        has_a = 'pipeline_lock.py acquire' in pm
        has_r = 'pipeline_lock.py release' in pm
        if has_a and has_r:
            held += 1
            if verbose:
                print('      %-34s 持锁 ✓' % nm)
        else:
            bad.append('%s（acquire=%s / release=%s）' % (nm, '有' if has_a else '缺',
                                                        '有' if has_r else '缺'))
    for x in bad:
        r.fail('K7', '%s 会写 data.js 却没有完整工序锁 —— 与相邻任务重叠时整份覆盖，'
                     '字段会莫名变回旧值' % x)
    if not bad:
        r.ok('K7', '会写 data.js 的 %d 条自动化全部持锁（acquire + release 都在 prompt 里）' % held)


# ---------------------------------------------------------------- K5
def k5_dead(r, verbose=False):
    # 1) 引用语料：7 份 prompt + deploy.sh + 所有脚本源码
    refs = {}
    try:
        con = sqlite3.connect(DB)
        for (p,) in con.execute("select prompt from automations where prompt like '%run-python %'"):
            for m in re.finditer(r'([A-Za-z0-9_]+\.py)', p or ''):
                refs.setdefault(m.group(1), set()).add('prompt')
        con.close()
    except Exception as e:
        r.warn('K5', '读不到自动化库（%s），死脚本判定降级为「只比项目内引用」' % e)
    dep = os.path.join(HERE, 'deploy.sh')
    if os.path.exists(dep):
        for m in re.finditer(r'([A-Za-z0-9_]+\.py)', _read(dep)):
            refs.setdefault(m.group(1), set()).add('deploy.sh')
    for p in _py_files():
        s = _read(p)
        stem = os.path.splitext(os.path.basename(p))[0]
        for m in re.finditer(r'(?:^|\n)\s*(?:from\s+([A-Za-z0-9_]+)\s+import|import\s+([A-Za-z0-9_]+))',
                             s):
            mod = m.group(1) or m.group(2)
            if mod != stem:
                refs.setdefault(mod + '.py', set()).add('import')

    dead = []
    for p in _py_files():
        b = os.path.basename(p)
        if b in refs or b in DEAD_OK:
            continue
        dead.append(b)
    if dead:
        for d in dead:
            r.warn('K5', '死脚本（无 prompt / deploy.sh / import 引用）：%s —— '
                         '接进链或加进 DEAD_OK 并写明用途' % d)
    else:
        r.ok('K5', '无未引用脚本（%d 个 .py，%d 个引用源）' % (len(_py_files()), len(refs)))
    if verbose:
        for k in sorted(refs):
            print('      %-32s ← %s' % (k, ','.join(sorted(refs[k]))))


# ---------------------------------------------------------------- K8
# 去噪哨兵（R100z205）。**这是唯一一条能发现「真消息被误删」的判据。**
#
# 为什么必须有（用户 2026-10-25 原话「让你优化没让你减少要闻深度」）：
#   `stock_news.NEWS_STATS_PAT` 被 `sweep_news.py` 反过来 import 去清洗「五节要闻」，
#   于是「两张表能不能合并」成了悬在要闻层头上的刀：合并当天，`sweep_news` 会静默
#   删掉 ETF 资金面、涨停潮、板块广度这类**真信号**，而 audit 的 G22 只查
#   「要闻里还有没有噪音」——删干净了它反而报 PASS。**没有任何闸门能发现少东西。**
#   同一天我还撞出第二类：把判断写宽了（`多只个股走强` 写在共用表里），
#   要闻层连「机器人概念午后拉升，多只个股走强」都吃掉。
#
# 判据是双向的，缺一半就没意义：
#   ① must_drop  个股层必须丢（40 条真实噪音标题，来自四轮清理的实测样本）
#   ② must_keep  个股层必须留（真公司消息；含四条被踩过的误杀边界）
#   ③ ashare_keep 要闻层（只过 NEWS_STATS_PAT）必须留 —— **专治「两层合并」**
# 任何一条不符 → FAIL，deploy.sh 阻断。
K8_MUST_DROP = (
    '56只股涨停 最大封单资金19.88亿元',
    '45只股上午收盘涨停(附股)',
    '10月23日A股涨跌停情况梳理',
    '汽车行业今日净流入资金9.64亿元，比亚迪获净买入',
    '主力动向：10月23日特大单净流入95.56亿元',
    '机械设备行业资金流入榜：三一重工等净流入资金居前',
    '今日盘中突破半年线个股',
    '107家公司获机构调研（附名单）',
    '北交所成交活跃股排行榜',
    'A股股票回购一览：149家公司实施回购',
    '10月14日盘前停复牌汇总',
    'A股收评：三大指数集体收涨',
    '午报｜沪指涨0.3% 创业板指走强',
    '盘前情报丨美股三大指数收涨',
    '公告避雷针：多家公司披露减持计划',
    '投资日历：下周关注这些大事',
    '10月23日盘后固定价格交易额9.15亿元',
    '45只A股筹码大换手（10月19日）',
    '晚间公告｜10月23日这些公告有看头',
    '10月22日早间新闻精选',
    '深沪北百元股数量达172只',
    '业绩潜力股名单来了',
    '筹码新动向：机构增仓这些股',
    '涨停股复盘：16股封单超亿元',
    '涨跌停统计：涨停家数创月内新高',
    '强势股追踪：5连阳个股曝光',
    '化工ETF汇添富(159093)标的指数涨超1%',
    '15只概念股低估值且大幅回调',
    '超4800只个股飘红',
    '人形机器人板块掀涨停潮',
    '昨日行情回顾：三大指数普涨',
    '两融余额缩水，杠杆资金大幅加仓165股',
    '多只个股逆市爆发',
    '券商评级观察：机构上调目标价',
    '10月22日早报｜今日值得关注的公司',
    '6只股获北向资金连续净买入',
)

K8_MUST_KEEP = (
    # ① 公司自己的涨跌停动态（不是「N只股涨停」那种市场统计）
    '慕思股份连收3个涨停板',
    '天威视讯涨停走出2连板',
    # ② 立案 + 名单（名单类只丢括注形式，不能因「名单出炉」丢利空）
    '300198，被立案调查！机构龙虎榜抢筹名单出炉',
    # ③ 公司上榜 ≠ 排行榜栏目（`股榜` 会误杀「A股榜首」）
    '长鑫科技市值重回3万亿，再登A股榜首',
    # ④ 筹码集中度是公司消息（裸 `筹码集中` 会误杀）
    '星源材质：前十大流通股东换仓后筹码集中度提升',
    # ⑤ 栏目里的公司实质动作（业绩/定增/回购/中标…）
    '杉杉股份拟向海螺集团定增募资不超40亿元',
    '东百集团：终止收购江西东百100%股权',
    '宁德时代前三季度净利润同比增长36%',
    '贵州茅台：拟回购股份用于注销',
    '比亚迪10月新能源汽车销量同比增长21%',
    '中国平安获摩根大通增持H股',
    '立讯精密：签订重大合同 金额约23亿元',
    '中际旭创：1.6T光模块已批量出货',
    '【调研快报】立讯精密接待3家机构调研',
    '紫金矿业：拟收购海外金矿资产',
    '隆基绿能：BC二代组件量产进度符合预期',
    '赛力斯：8月销量同比增长',
    '工业富联涨停 成交额超50亿元',
    '爱尔眼科：三季度门诊量恢复至疫情前',
    '中国中免：海南离岛免税销售额环比回升',
)

# 要闻层必须留（只过共用表 NEWS_STATS_PAT）。这三类正是「个股层噪音、要闻层真信号」
K8_ASHARE_KEEP = (
    '超危漏洞叠加AI新政 软件板块涨停潮',
    '四家头部基金各出2亿自购 宽基ETF净流入1900亿',
    '沪指收复4100点 两市成交额重回2万亿',
    '证监会：扩大ETF互联互通标的范围',
    '央行开展5000亿元MLF操作 中标利率持平',
    '人形机器人概念午后拉升 多只个股走强',
    '机器人概念多股涨停 产业链公司密集披露订单',
    '三大指数集体收涨 创业板指涨超2%',
    '杠杆资金加仓 两融余额重返1.9万亿',
)


def _load_stock_news():
    """导入 stock_news 取词表与判定函数（不用 import 语句：避免 check_code 自身被 K2 审视）。"""
    import importlib.util
    p = os.path.join(HERE, 'stock_news.py')
    spec = importlib.util.spec_from_file_location('_k8_stock_news', p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def k8_noise_sentinel(r, verbose=False):
    try:
        sn = _load_stock_news()
    except Exception as e:
        r.fail('K8', '无法导入 stock_news 词表（%s）—— 哨兵失效等于没有哨兵' % e)
        return

    bad = []
    for t in K8_MUST_DROP:
        if not sn.is_stats_title(t):
            bad.append(('漏网（应丢未丢）', t))
    for t in K8_MUST_KEEP:
        if sn.is_stats_title(t):
            bad.append(('误杀（应留被丢）', t))
    for t in K8_ASHARE_KEEP:
        hit = [p.pattern for p in sn.NEWS_STATS_PAT if p.search(t)]
        if hit:
            bad.append(('要闻层被共用表误杀', t + ' ← ' + hit[0]))

    if bad:
        for kind, t in bad:
            r.fail('K8', '%s：%s' % (kind, t))
        r.fail('K8', '去噪哨兵 %d/%d 条不符 —— 改词表前先读 '
                     'stock_news.py 文件头「两层词表别合并」' %
               (len(bad), len(K8_MUST_DROP) + len(K8_MUST_KEEP) + len(K8_ASHARE_KEEP)))
    else:
        n = len(K8_MUST_DROP) + len(K8_MUST_KEEP) + len(K8_ASHARE_KEEP)
        r.ok('K8', '去噪哨兵 %d 条样本全部符合（丢弃 %d / 个股保留 %d / 要闻保留 %d）' %
             (n, len(K8_MUST_DROP), len(K8_MUST_KEEP), len(K8_ASHARE_KEEP)))
    if verbose:
        print('      NEWS_STATS_PAT %d 条 / NEWS_STOCK_ONLY %d 条' %
              (len(sn.NEWS_STATS_PAT), len(sn.NEWS_STOCK_ONLY)))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--only', default='', help='只跑某一类，如 K3 / K8')
    a = ap.parse_args()

    r = R()
    only = a.only.strip().upper()
    if not only or only == 'K1':
        k1_syntax(r)
    if not only or only == 'K2':
        k2_imports(r)
        k2b_unbound(r)
    if not only or only == 'K3':
        k3_atomic(r, a.verbose)
    if not only or only == 'K4':
        k4_timeout(r)
    if not only or only == 'K5':
        k5_dead(r, a.verbose)
    if not only or only == 'K6':
        k6_contract(r, a.verbose)
    if not only or only == 'K7':
        k7_lock(r, a.verbose)
    if not only or only == 'K8':
        k8_noise_sentinel(r, a.verbose)
    return r.dump()


if __name__ == '__main__':
    sys.exit(main())

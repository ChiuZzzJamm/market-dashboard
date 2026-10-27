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
  K8 去噪哨兵     盘面统计类标题不许当个股消息（R100z204）
  K9 环比闸门     池子轮换不许把内容悄悄带走（R100z208）
  K10 消息面顺序  时间倒序 / 三段顺序 / 口径净差判定（R100z210）
                 —— 这类退步**页面看起来还在**，只是顺序乱、口径错，肉眼很难发现

★ 写规则时踩过的两个坑（别重犯）：
  1. **`s.get(...)` 不是网络调用**：`s` 在多数脚本里是 dict，`s.get('f5Tier')` 是 dict.get。
     用 `ast.unparse(func)` 精确匹配 `requests.get` 之类才不误报（首版误报 90+ 处）。
  2. **硬编码日期不能当 bug**：`cal_factor` / `common` 的节假日表、每个文件头的 changelog
     都是合法日期字面量。想查「写死今天的日期」只能查**代码路径**而非字符串，收益低于噪声，故不做。

用法:
    python3 check_code.py            # 全部检查（FAIL 时 exit 1）
    python3 check_code.py --verbose  # 附明细（谁引用了谁、每个写者的原子实现位置）
    python3 check_code.py --only K3  # 只跑某一类（K1-K11）
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
    'chen_pool.py',             # R100z218：chenPool（陈学长第二套断板反包口径；走 write_data_js）
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
    'breakout_pool.py',         # 横盘启动池（涨停锚口径；走 write_data_js）
    'refresh_stamp.py',         # updatedAt 单一主人（正则手术式改写）
    'retag_duanban.py',         # 断板重打标
    'risk_gate.py',             # 个股风险闸
    'signal_attribution.py',    # R100z160：signalPerf（指标归因台账）
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
    'backtest_indicators.py': '原子写的是 .bt_cache K线缓存 + backtest_report.md/result.json，从不碰 data.js',
    'board_alias.py': 'R100z224：原子写的是 index.html 里的 pctAlias 段（从 board_alias.json 生成），'
                      '读 data.js 只为 --add 校验目标板块是否存在',
    'goldpit_study.py': 'R100z234：黄金坑原型研究脚本，原子写的是 goldpit_study.json（--dump 时才写，'
                        '可达数百 MB、已在 .gitignore 排除），纯离线统计、从不碰 data.js、不进任何自动化',
}

# 死脚本白名单：合法的「手工跑、不进自动化」工具，写明用途才准留
DEAD_OK = {
    'goldpit_study.py': '「黄金坑」形态/外盘映射原型研究，手工触发（--dump 才落盘），不进流水线',
    'fix_dd_prefix.py': '一次性修数工具（历史 DD 前缀），手工触发',
    'track_calibration.py': '校准曲线统计，手工触发',
    'score_openoutlook.py': '开盘前瞻打分，手工/被 import',
    'stock_fundflow.py': '个股资金流对照，手工触发',
    'decode_policy.py': '政策词表纯函数，被 enrich_policy import',
    'kline_cache.py': 'K 线缓存模块，被 accumulation_score import',
    'backtest_indicators.py': '四指标 2 年滚动回测（信号归因+组合年化），纯本地、手工触发',
    'backtest_entry.py': '断板反包买点口径回测（涨停→倍量阴→缩量回均线尾盘买），纯本地、手工触发',
    'nshape_fetch.py': 'N 字形态研究：全市场日K抓取（东财/腾讯双源→.kline_hist/），纯本地、手工触发',
    'nshape_factors.py': 'N 字形态研究：因子分层+按年切分，纯本地、手工触发',
    'nshape_rules.py': 'N 字形态研究：过滤规则组合搜索（双段验证），纯本地、手工触发',
    'nshape_right.py': 'N 字形态研究：反包确认日买入+反包率，纯本地、手工触发',
    'nshape_extra.py': 'N 字形态研究：跨度交叉表/市场开关/平台可买性，纯本地、手工触发',
    'backtest_breakout.py': '横盘启动接入 backtest_indicators 同一跑道的年化对照，纯本地、手工触发',
    'breakout_study.py': '横盘启动参数研究：振幅/粘合/地量/贴沿四维分层搜索，纯本地、手工触发',
    'chen_study.py': '陈学长口径研究：卖点/持有天数网格 + 训练-样本外切分，纯本地、手工触发',
    'duanban_study.py': '断板反包口径研究：买点/卖点/止损锚分层，纯本地、手工触发',
    'duanban_tune.py': '断板反包参数寻优（量比/回撤/确认日），纯本地、手工触发',
    'duanban_extra.py': '断板反包补充维度（距MA5/板块/情绪分层），纯本地、手工触发',
    'rule_final_check.py': '陈学长口径终选：换参前后样本重算校验，纯本地、手工触发',
    'nshape_backtest.py': 'N 字形态研究：组合回测，纯本地、手工触发',
    'nshape_bt.py': 'N 字形态研究：回测口径对照，纯本地、手工触发',
    'nshape_final.py': 'N 字形态研究：最终规则定稿统计，纯本地、手工触发',
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
    # ---- R100z207：跨标的盘点稿的典型标题（这类占 448 条，靠正则穷举做不到，
    #      真正的判据是 stock_news._drop_crossdup 的「同一标题挂 ≥3 只票且不含本公司名」。
    #      这里只放几条最典型的，用于防止「有人把 crossdup 判据删了/阈值改成 99」
    #      —— 那几条一旦回流，下面的样本会立刻 FAIL（它们单靠正则是抓不住的）。----
    '人形机器人概念涨1.46%，主力资金净流入这些股',
    'ChatGPT概念涨4.45%，主力资金净流入70股',
    '300296，三分钟直线涨停！两大板块，逆市大涨！',
    '龙虎榜动向：7股获机构及北向资金集体净买入',
    '168家公司预告前三季度业绩 124家预增',
    '003021，直线涨停！5天3板',
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

    # R100z207：**跨标的去重是纯逻辑判据，正则样本测不到它** ——
    # 上面那些 must_drop 样本现在的正则也能抓到，所以把 `_drop_crossdup` 的阈值
    # 改成 99（等于关掉）它们照样 PASS（实测踩过）。这条单独构造假数据测函数本身：
    # 同一标题挂 3 只票、且不含任何一只的名字 → 必须删 3 条；
    # 标题里含自己名字的那只 → 必须留。
    try:
        fake = {
            'A': {'name': '甲公司', 'news': [{'title': '某公司签署战略合作协议'}]},
            'B': {'name': '乙公司', 'news': [{'title': '某公司签署战略合作协议'}]},
            'C': {'name': '丙公司', 'news': [{'title': '某公司签署战略合作协议'}]},
            'D': {'name': '丁公司', 'news': [{'title': '某公司签署战略合作协议'},
                                             {'title': '丁公司签署战略合作协议'}]},
        }
        n = sn._drop_crossdup(fake)
        left = sum(len(v.get('news') or []) for v in fake.values())
        # 「某公司签署战略合作协议」挂 4 只、且不含 A/B/C/D 任一名字 → 4 条全删；
        # 「丁公司签署战略合作协议」只挂 D 一只 → 留。所以是删 4 留 1。
        if n != 4 or left != 1:
            bad.append(('跨标的去重失效',
                        '期望删 4 留 1，实际删 %d 留 %d —— 检查 _drop_crossdup 的 min_stocks' %
                        (n, left)))
        if not any('丁公司签署战略合作协议' == x.get('title')
                   for x in (fake['D'].get('news') or [])):
            bad.append(('跨标的去重误杀', '标题含本公司名的条目被删了'))
    except Exception as e:
        bad.append(('跨标的去重无法自检', repr(e)))

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
def k9_depth_guard(r, verbose=False):
    """K9 环比闸门自检 —— 专治「少东西没人发现」（R100z208，用户 2026-10-25 授权）。

    要防的事故（真实发生过）：池子轮换把 36 只标的连同弹窗公告一起带走，
    标的数 533 → 530（-0.6%），人眼盯着数字看不出来，是用户点开某只票才发现的。
    数量指标对「少 30 条」不敏感，对「少 30%」才敏感 —— 所以判断交给机器。

    这条闸门本身最容易失效的三种方式，逐个设卡：
      ① 函数还在，但 main 里没人调（重构时被摘掉）→ AST 查调用点
      ② 阈值被偷偷放松（10% 改成 90%）→ 查常量 + **注入式假数据**验证判定真的用了阈值
      ③ 拦下来还照写（`return` 丢了）→ 查 if breach 块内有无 Return
    外加一条顺序约束：基线必须在 merge_depth **之前**取，否则被就地删除的旧条目
    会把基线拉低，该报的断崖报不出来（本人 2026-10-25 实际踩过这个顺序）。
    """
    p = os.path.join(HERE, 'stock_news.py')
    try:
        src = open(p, encoding='utf-8').read()
        tree = ast.parse(src)
    except Exception as e:
        r.fail('K9', '无法解析 stock_news.py（%s）—— 深度闸门自检失效' % e)
        return

    bad = []

    # ① 必须挂在 main 的写盘路径上
    main_fn = next((n for n in tree.body
                    if isinstance(n, (ast.FunctionDef,)) and n.name == 'main'), None)
    if main_fn is None:
        bad.append('找不到 main()')
    else:
        names = {n.func.id for n in ast.walk(main_fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        if 'guard_ratio' not in names:
            bad.append('main() 没调用 guard_ratio —— 闸门被摘掉了（等于没有闸门）')
        # ③ 拦下必须 return，不能继续写盘
        ret_inside = False
        for n in ast.walk(main_fn):
            if isinstance(n, ast.If):
                th = n.test
                if (isinstance(th, ast.Name) and th.id == 'breach') or \
                   (isinstance(th, ast.UnaryOp) and isinstance(th.operand, ast.Name)
                        and th.operand.id == 'breach'):
                    if any(isinstance(x, ast.Return) for x in ast.walk(n)):
                        ret_inside = True
        if not ret_inside:
            bad.append('拦下分支里没有 return —— 会「报警照写盘」，等于没拦')

    # ② 阈值不许放松
    m = re.search(r'^RATIO_MAX_DROP\s*=\s*([\d.]+)', src, re.M)
    if not m:
        bad.append('找不到 RATIO_MAX_DROP 常量')
    elif float(m.group(1)) > 0.15:
        bad.append('RATIO_MAX_DROP=%s 被放松（> 0.15 等于关闸门）' % m.group(1))

    # 顺序：基线取在 merge_depth 之前
    i_base = src.find('base_stats = _sn_stats(old)')
    i_merge = src.find('merge_depth(D[\'stockNews\'], old)')
    if i_base < 0:
        bad.append('找不到 base_stats 基线取值')
    elif i_merge > 0 and i_base > i_merge:
        bad.append('基线取在 merge_depth 之后 —— merge 会就地删旧条目，基线偏低会漏报')

    # ④ 注入式假数据：判定本身必须正确（不是只验常量）
    try:
        sn = _load_stock_news()
        cases = [
            # (旧, 新, 期望是否拦, 说明)
            ({'news': 1000}, {'news': 890}, True, '-11% 应拦'),
            ({'news': 1000}, {'news': 910}, False, '-9% 应放'),
            ({'news': 1000}, {'news': 900}, False, '恰好 -10% 应放（「超过」才拦）'),
            ({'stocks': 20, 'news': 30, 'notices': 10},
             {'stocks': 2, 'news': 3, 'notices': 1}, False, '小基数（<50）不判'),
            ({'stocks': 545, 'news': 2205, 'notices': 783},
             {'stocks': 545, 'news': 1200, 'notices': 783}, True, '单项新闻掉 45% 必须拦'),
            ({'stocks': 545, 'news': 2205, 'notices': 783},
             {'stocks': 400, 'news': 2205, 'notices': 783}, True, '池子轮换少 145 只必须拦'),
        ]
        for old_d, new_d, want, label in cases:
            b, _rows = sn.guard_ratio(new_d, old_d)
            if bool(b) != want:
                bad.append('%s：期望%s，实际%s' % (label, '拦' if want else '放',
                                                 '拦' if b else '放'))
        # allow 通道
        b, _ = sn.guard_ratio({'news': 500}, {'news': 1000}, allow=True)
        if b:
            bad.append('--allow-drop 没生效（显式豁免通道断了）')
        # 反向自检：把阈值注入成 0.90，同一组数据必须放行
        #   —— 若这行也「拦」，说明判定写死了 0.10，常量是摆设
        b, _ = sn.guard_ratio({'news': 890}, {'news': 1000}, max_drop=0.90)
        if b:
            bad.append('max_drop 注入无效 —— 判定没用传入的阈值（写死了？）')
        # 双基线：峰值高于上次时，分母取峰值
        b, _ = sn.guard_ratio({'news': 800}, {'news': 840}, peak={'news': 1000})
        if not b:
            bad.append('五日峰基线失效 —— 慢速流失（每天 -7%）会一路漏过去')
    except Exception as e:
        bad.append('假数据自检无法运行（%r）' % e)

    if bad:
        for x in bad:
            r.fail('K9', x)
        r.fail('K9', '环比闸门 %d 项不符 —— 它是唯一能自动发现「池子轮换把内容带走」的'
                     '机制，别关' % len(bad))
    else:
        r.ok('K9', '环比闸门自检通过（三项指标 / 双基线 / 拦截不写盘 / 6 组假数据 + '
                   'allow 豁免 + 阈值注入）')
    if verbose:
        print('      RATIO_MAX_DROP=%s  RATIO_MIN_BASE=%s  RATIO_PEAK_N=%s'
              % (m.group(1) if m else '?',
                 (re.search(r'^RATIO_MIN_BASE\s*=\s*(\d+)', src, re.M) or [None, '?'])[1],
                 (re.search(r'^RATIO_PEAK_N\s*=\s*(\d+)', src, re.M) or [None, '?'])[1]))


# ---------------------------------------------------------------- K10
def _extract_js_fn(html, name):
    """从 index.html 抽出一个具名函数（括号配平，跳过字符串 / 模板串 / 注释 / 正则）。

    为什么不硬编码一份副本：副本会腐化 —— 改了 index.html 忘了改副本，闸门测的是尸体。
    为什么不 eval 整块 <script>：30 万字符里全是 DOM 初始化，eval 直接炸。
    """
    m = re.search(r'\bfunction\s+' + re.escape(name) + r'\s*\(', html)
    if not m:
        return None
    i = html.find('{', m.end())
    if i < 0:
        return None
    depth, j, n, prev = 0, i, len(html), ''
    while j < n:
        c = html[j]
        if c in ('"', "'", '`'):
            q, j = c, j + 1
            while j < n:
                if html[j] == '\\':
                    j += 2
                    continue
                if html[j] == q:
                    break
                j += 1
        elif c == '/' and j + 1 < n and html[j + 1] == '/':
            j = html.find('\n', j)
            if j < 0:
                return None
        elif c == '/' and j + 1 < n and html[j + 1] == '*':
            e = html.find('*/', j + 2)
            if e < 0:
                return None
            j = e + 1
        elif c == '/' and prev in '(,=:[!&|?{};':
            j += 1
            while j < n:
                if html[j] == '\\':
                    j += 2
                    continue
                if html[j] == '[':
                    while j < n and html[j] != ']':
                        j += 1
                if html[j] == '/':
                    break
                j += 1
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return html[m.start():j + 1]
        if not c.isspace():
            prev = c
        j += 1
    return None


def _find_node():
    """找 node：PATH 优先，再兜底受管二进制（自动化环境 PATH 常常不全，deploy.sh 里同理）。"""
    cands = ['node']
    cands += sorted(glob.glob(os.path.expanduser(
        '~/.workbuddy/binaries/node/versions/*/bin/node')))
    cands += sorted(glob.glob('/Users/loccco/.nvm/versions/node/*/bin/node'))
    for c in cands:
        try:
            subprocess.run([c, '--version'], capture_output=True, timeout=8, check=True)
            return c
        except Exception:
            continue
    return None


def k10_news_order(r, verbose=False):
    """K10 消息面顺序与口径自检（R100z210，用户 2026-10-25「不要每次等我确认」授权）。

    要防的事故（R100z209 刚修完，最怕下一次重构又退回去，而且退回去**页面看起来还在**）：
      ① `stock_news.py` 的 `rank()` 是**选取**排序（方向性 bull→bear→neutral 在前），
         **不是展示排序** —— 落盘前漏了重排，弹窗就是「20:06 → 20:58 → 21:25 → 17:16」；
      ② `merge_depth()` 把补回的旧条目 **append 到末尾** —— 它负责「不少」，不负责「有序」；
      ③ 三段顺序（公告 → 新闻 → 板块）被改回平铺；
      ④ 口径判定从「净差」退回「票数多数」—— 这条最阴：绝大多数场景结果一样，
         只有「中性条目占多数」时才不同（冰山冷热 利好2/利空1/中性8 会被判成利好口径）。

    四组检查，前两组看数据、第三组看结构、第四组**用假数据跑真函数**：
      A 数据层源码 —— `merge_depth` / `fetch_stock_news` 里必须有 reverse 排序归一
      B 真数据结果 —— `data.js` 里每只标的的 news/notices 必须严格非增（查结果，不查代码）
      C 前端结构   —— `faceVerdict` 存在且被调用、三段字面量顺序、三处按时间倒序
      D 口径语义   —— node 跑 12 组假数据（node 缺失降级 WARN：查不了 ≠ 有问题，不该拦部署）

    ★ 为什么口径要抽成独立函数 `faceVerdict`：闸门没法测埋在渲染函数里的 if 链。
      抽出纯函数后可以做**故障注入**（实测：退回票数多数拦 3 组 / 净差门槛放松拦 2 组 /
      消费 tieBreak 拦 1 组 / 一律中性拦 7 组）。**没做过故障注入的闸门等于没做。**
    """
    bad = []
    n_stocks = 0        # B 组查了多少只标的（成功消息里要报出总数，否则是抽样不是全量）

    # ---- A 数据层源码：排序归一不许丢
    #   ⚠️ 判据要匹配**语义**而不是**形状**：`merge_depth` 用一层 for 遍历
    #   (('news','time'), ('notices','date')) 两个字段，一次 sorted(reverse=True) 就覆盖两处。
    #   首版数「reverse=True 出现次数 >= 2」→ 真代码只有 1 处 → 误报。
    #   正确判据：有 reverse 归一 + 两个字段名都出现在函数体里。
    #   真正防「只给 news 排、忘了 notices」的是 B 组（真数据全量倒序），这里只防「整段被删」。
    sp = os.path.join(HERE, 'stock_news.py')
    if not os.path.exists(sp):
        r.fail('K10', 'stock_news.py 不存在 —— 消息面顺序无生产者，闸门失效')
        return
    ssrc = _read(sp)
    try:
        stree = ast.parse(ssrc)
    except Exception as e:
        r.fail('K10', 'stock_news.py 语法错（%s），无法自检' % str(e)[:60])
        return
    #   fields 是「必须出现的字段名字面量」：merge_depth 里要覆盖 news 与 notices 两个键；
    #   fetch_stock_news 里排的是变量 picked（不是字典键），所以不要求字段名，只要求 reverse 归一。
    for fname, fields in (('merge_depth', ('news', 'notices')),
                          ('fetch_stock_news', ())):
        fn = next((n for n in ast.walk(stree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == fname), None)
        if fn is None:
            bad.append('%s() 不见了 —— 没有它就没人做排序归一' % fname)
            continue
        seg = ast.get_source_segment(ssrc, fn) or ''
        if not re.search(r'reverse\s*=\s*True', seg):
            bad.append('%s() 里没有 reverse=True 排序 —— 选取排序（rank）会直接进展示，'
                       '弹窗时间会乱' % fname)
            continue
        missing = [f for f in fields if ("'%s'" % f) not in seg and ('"%s"' % f) not in seg]
        if missing:
            bad.append('%s() 里没出现字段 %s —— 排序归一没覆盖它'
                       % (fname, '/'.join(missing)))

    # ---- B 真数据结果：每只标的必须严格非增
    dp = os.path.join(HERE, 'data.js')
    if not os.path.exists(dp):
        r.warn('K10', 'data.js 不存在，跳过倒序结果校验')
    else:
        m = re.search(r'window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$', _read(dp))
        if not m:
            bad.append('data.js 里找不到 window.DASHBOARD_DATA')
        else:
            try:
                D = json.loads(m.group(1))
            except Exception as e:
                bad.append('data.js 不是合法 JSON（%s）' % str(e)[:60])
                D = None
            if D is not None:
                sn = D.get('stockNews') or {}
                n_stocks = len(sn)
                n_bad, samples = 0, []
                for code, v in sn.items():
                    if not isinstance(v, dict):
                        continue
                    for field, key in (('news', 'time'), ('notices', 'date')):
                        lst = v.get(field) or []
                        ks = [str(x.get(key) or '') for x in lst if isinstance(x, dict)]
                        if ks != sorted(ks, reverse=True):
                            n_bad += 1
                            if len(samples) < 3:
                                samples.append('%s.%s %s' % (code, field, ks[:4]))
                if n_bad:
                    bad.append('data.js 里 %d 个数组没有严格倒序（共 %d 只标的）—— '
                               '例：%s；用 stock_news.py 重跑或一次性重排'
                               % (n_bad, len(sn), '；'.join(samples)))
                elif verbose:
                    print('      stockNews %d 只标的，news/notices 全部严格倒序' % len(sn))

    # ---- C 前端结构：三段顺序 + 排序调用 + 口径函数被调用
    hp = os.path.join(HERE, 'index.html')
    if not os.path.exists(hp):
        bad.append('index.html 不存在')
    else:
        html = _read(hp)
        if 'function faceVerdict(' not in html:
            bad.append('找不到 faceVerdict() —— 口径判定被内联回渲染函数，闸门再也测不到它'
                       '（K10 的 D 组依赖它独立存在）')
        body = _extract_js_fn(html, 'newsFaceBlock')
        if not body:
            bad.append('找不到 newsFaceBlock() —— 消息面渲染入口被改名或删除')
        else:
            if 'faceVerdict(nB, nE' not in body:
                bad.append('newsFaceBlock 没有调用 faceVerdict —— 口径判定被绕开了')
            if '[notices, news, sector]' not in body:
                bad.append('三段字面量 [notices, news, sector] 不在 —— '
                           '公告→新闻→板块的顺序被改（若是有意重构，同步更新 K10）')
            n_sort = body.count('.sort(snByTsDesc)')
            if n_sort < 3:
                bad.append('newsFaceBlock 里按时间倒序只有 %d 处（需 3：公告/新闻/板块各一）'
                           % n_sort)
        # ⑤ story 里的【个股消息】段**不许丢弃**（R100z210 实撞）：
        #   上一轮为了「剔除空的个股消息区块」，把 story 里同一标签的段也 return 掉了 ——
        #   全库 56 条、每段 30-80 字的真实小作文随之消失，正是用户明令禁止的「以减内容为代价」。
        #   用户说的「空」指的是 UI 里由要闻 impacts 拼出来的假区块，与 story 段是两回事。
        for m_drop in re.finditer(
                r"dbStoryParaKind\s*\(\s*p\s*\)\s*===\s*'stock'\s*\)\s*\{?\s*return", html):
            line = html[:m_drop.start()].count('\n') + 1
            bad.append('index.html:%d 把 story 的【个股消息】段丢弃了 —— '
                       '那是真实内容（全库 56 条、每段 30-80 字），'
                       '应归入「个股新闻」组（kind:news + ts:0）而不是 return' % line)
        # ⑥ 反向兜底：⑤ 只拦「写成同一条 return」；若有人改成 filter 掉、或整块删掉
        #   extra.push，⑤ 一条都拦不到（正则匹配的是形状）。所以这里改问正向语义：
        #   **两个消息面渲染入口都得真的把 story 段输出出来，且按 isStk 归到新闻组**。
        #   注意 `openDbModal` 里变量名是 isStk2 —— 判据用 `isStk` 前缀，两种命名都覆盖。
        for fn_name in ('stkNewsHtml', 'openDbModal'):
            fbody = _extract_js_fn(html, fn_name)
            if not fbody:
                bad.append('%s() 抽取失败或不存在 —— 该入口的 story 段渲染无法自检（K10 ⑥）'
                           % fn_name)
            elif 'dbStoryParaHtml(' not in fbody:
                bad.append('%s() 没调用 dbStoryParaHtml —— story 里的段落被整块删了；'
                           '「删 UI 假区块」不等于「删真实段落」（全库 56 条）' % fn_name)
            elif 'isStk' not in fbody:
                bad.append('%s() 没给 story 段分派 kind（isStk 缺失）—— '
                           '它会掉出 公告/新闻/板块 三段结构' % fn_name)

    # ---- D 口径语义：用假数据跑真函数（node 缺失只降级，不阻断）
    CASES = [
        (2, 1, '', 'neutral', '冰山冷热反例 2:1（8 条中性）→ 中性'),
        (0, 0, 'bull', 'neutral', '全中性不消费 tieBreak'),
        (0, 0, '', 'neutral', '全中性 → 中性'),
        (3, 0, '', 'bull', '净差 3 → 利好'),
        (2, 0, '', 'bull', '小样本 2:0 → 利好'),
        (1, 0, '', 'bull', '单条利好 → 利好'),
        (0, 1, '', 'bear', '单条利空 → 利空'),
        (2, 2, '', 'neutral', '贴脸 2:2 无 AI 判定 → 中性'),
        (2, 2, 'bull', 'bull', '贴脸 2:2 有 AI 判定 → 沿用'),
        (1, 1, 'bear', 'bear', '贴脸 1:1 有 AI 判定 → 沿用'),
        (4, 3, '', 'neutral', '净差 1 且 m=7 → 贴脸 → 中性'),
        (5, 3, '', 'bull', '净差 2 → 利好'),
    ]
    if os.path.exists(hp):
        fn_src = _extract_js_fn(_read(hp), 'faceVerdict')
        node = _find_node()
        if not fn_src:
            pass  # 上面 C 组已经报过，不重复
        elif not node:
            r.warn('K10', '找不到 node，口径语义测试跳过（12 组假数据未跑）—— '
                          '这不是「没问题」，是「查不了」')
        else:
            js = fn_src + '\nvar C=' + json.dumps(
                [[a, b, c, d, e] for a, b, c, d, e in CASES], ensure_ascii=False) + '''
var bad = [];
C.forEach(function (x) {
  var got = faceVerdict(x[0], x[1], x[2]);
  if (got !== x[3]) bad.push(x[4] + '：期望 ' + x[3] + '，实际 ' + got);
});
if (bad.length) { console.log('BAD'); bad.forEach(function (b) { console.log('  ' + b); }); }
else console.log('OK');
'''
            tmp = None
            try:
                fd, tmp = tempfile.mkstemp(suffix='.js', prefix='k10_')
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(js)
                pr = subprocess.run([node, tmp], capture_output=True, text=True, timeout=30)
                out = (pr.stdout or '') + (pr.stderr or '')
                if 'OK' not in out:
                    for line in [x.strip() for x in out.split('\n') if x.strip()]:
                        if line not in ('BAD',):
                            bad.append('口径语义 %s' % line)
                    if not any('口径语义' in b for b in bad):
                        bad.append('口径语义测试异常退出：%s' % out.strip()[:120])
            except Exception as e:
                bad.append('口径语义测试跑不起来（%r）' % (e,))
            finally:
                if tmp and os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass

    if bad:
        for x in bad:
            r.fail('K10', x)
        r.fail('K10', '消息面顺序/口径 %d 项不符 —— 这类退步**页面看起来还在**，'
                      '只是顺序乱、口径错，肉眼很难发现，别关这条闸门' % len(bad))
    else:
        r.ok('K10', '消息面顺序与口径自检通过（源码排序归一 / %d 只标的严格倒序 / '
                    '三段顺序+三处倒序 / 12 组口径语义）' % n_stocks)


def k11_tape_degrade(r, verbose=False):
    """K11 分时降级占位与 tape.diagnose 同构自检（R100z221）。

    要防的事故（2026-10-26 实际踩到）：`tape.diagnose_codes` 的约定是「抓不到的不进结果，
    **调用方须显式降级**」，而调用方当时只在 `if t:` 里赋值 —— 抓空时 `picks[i]` 上**没有 tape 键**。
    两个后果都不是「跑一次就看得见」的：
      - B6 台账的 `duanban.star.picks[].tape` 只下钻**首元素**，首只票抓空即 FAIL，
        报错文案却写「指向不存在的字段」，会误导成台账写错，实际是开盘半小时接口抖动；
      - AI 在 data.js 里看到一只「看不出异常」的票，会照涨跌写「盘口平稳/无明显抛压」——
        **缺键 = 鼓励编**，正是「抓不到≠没有」的反面。

    本条闸门管的是「补了键、但补得不同构」这种更隐蔽的复发：tape.py 将来加字段、
    降级对象不跟 → AI 读 `actZh` 拿到 undefined，比缺键更容易让它自己编。
    判定三条，全部静态/瞬间可判、零联网：
      ① 降级对象必须覆盖 diagnose 的全部返回键；
      ② `state/zh/act/actZh` 必须与 `tape.STATE['unknown']` 同源（prompt 已写死
         「unknown 分时抓空 → 不下结论」，脚本另造文案就会出现三种说法）；
      ③ 数值项必须是 `None`（0 是「测到 0」，None 才是「没测到」）。
    外加自律一条：`_tape_unknown` 必须在 update_star.py 里被真实调用（防函数在、无人用）。
    """
    import importlib.util
    bad = []

    def _load(fname, alias):
        spec = importlib.util.spec_from_file_location(alias, os.path.join(HERE, fname))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    try:
        tape_mod = _load('tape.py', '_k11_tape')
        us_src = open(os.path.join(HERE, 'update_star.py'), encoding='utf-8').read()
        us_mod = _load('update_star.py', '_k11_us')
    except Exception as e:                                   # noqa: BLE001
        r.fail('K11', '无法载入 tape.py / update_star.py（%s）—— 降级同构自检失效' % e)
        return

    # diagnose 的返回键集（AST 取字面量，不依赖联网）
    tree = ast.parse(open(os.path.join(HERE, 'tape.py'), encoding='utf-8').read())
    diag_keys = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == 'diagnose':
            for sub in ast.walk(n):
                if (isinstance(sub, ast.Return) and isinstance(sub.value, ast.Call)
                        and getattr(sub.value.func, 'id', '') == 'dict'):
                    diag_keys = [k.arg for k in sub.value.keywords if k.arg]
    if not diag_keys:
        r.fail('K11', 'tape.diagnose 里找不到 return dict(...) —— 闸门判定依据消失，先修闸门')
        return

    ph = us_mod._tape_unknown('K11 自检')
    miss = [k for k in diag_keys if k not in ph]
    if miss:
        bad.append('降级占位缺 %d 个 diagnose 字段（%s）—— AI 读到的会是 undefined，'
                   '比缺键更容易编；tape.py 加字段时必须同步 _tape_unknown'
                   % (len(miss), '/'.join(miss)))

    # ② 四元组同源
    exp = tuple(tape_mod.STATE['unknown'])
    got = (ph.get('zh'), ph.get('act'), ph.get('actZh'))
    if ph.get('state') != 'unknown':
        bad.append("降级占位 state=%r，必须是 'unknown'" % ph.get('state'))
    if got != exp:
        bad.append('降级占位 zh/act/actZh=%r 与 tape.STATE["unknown"]=%r 不同源'
                   '—— prompt 已按 unknown 写死口径，脚本另造文案会让页面/AI/prompt 三种说法'
                   % (got, exp))

    # ③ 数值项必须 None
    nums = [k for k in diag_keys if k not in ('state', 'zh', 'act', 'actZh', 'text')]
    filled = {k: ph.get(k) for k in nums if ph.get(k) is not None}
    if filled:
        bad.append('降级占位的「没测到」被填了值（%s）—— 0 是「测到 0」，null 才是「没测到」'
                   % ', '.join('%s=%r' % kv for kv in filled.items()))

    # 自律：函数必须在调用方真实被调用（防「函数在、无人用」）
    if us_src.count('_tape_unknown(') < 3:
        bad.append('_tape_unknown 在 update_star.py 里只有 %d 处引用（定义 1 + 调用至少 2）'
                   '—— 两条补键路径（单只抓空 / 整体异常）必须都在'
                   % us_src.count('_tape_unknown('))

    for m in bad:
        r.fail('K11', m)
    if not bad:
        r.ok('K11', '分时降级占位与 tape.diagnose 同构（%d 个字段全覆盖 / '
                    'unknown 三元组与 STATE 同源 / 数值项全 null / 两条补键路径都在）'
             % len(diag_keys))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--only', default='', help='只跑某一类，如 K3 / K10')
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
    if not only or only == 'K9':
        k9_depth_guard(r, a.verbose)
    if not only or only == 'K10':
        k10_news_order(r, a.verbose)
    if not only or only == 'K11':
        k11_tape_degrade(r, a.verbose)
    return r.dump()


if __name__ == '__main__':
    sys.exit(main())

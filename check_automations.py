#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_automations.py · 7 条自动化 prompt 的机检（R100z169）

背景（2026-10-21 用户第三轮追问「检查报告有没有办法更仔细地查 7 个自动化」）：
CHECKLIST.md 的 L1/L2/L5 早就写明「抽 .py 逐个 test -f」「按变体集匹配步骤」
「cwds 要 json.loads 断言」—— 但全是**给人看的表**，每次靠我记性去执行，
于是「仔细检查」实际上每次跑的范围都不一样。

本脚本把这三层里**可判定**的部分固化成代码，让「查过没有」变成 exit code：

  A 排程与元数据   cwds 合法 JSON + 目录存在 / rrule 与业务日历一致 / status
  B 死链          prompt + deploy.sh 引用的 .py/.sh/references 是否真实存在
                  （**按整行否定语境过滤**；references 会到 skill 目录里找）
  C 命令 flag     code block 里的 `xxx.py --flag` 是否被该脚本源码支持
  D 死步骤        「第 N 步」引用是否有定义（**带 markdown 排版变体集**）
                  + 括号步号序列是否与基线 stepOrder 一致（整步被删只有这层能抓）
  E 口径对账      prompt 给 AI 的字数下限 ≥ G16 地板、上限 ≤ check_narrative 的 ceil_of
                  —— 「prompt 让写的、闸门判 FAIL」那类分裂（PROJECT-MEMORY 24.10 第四层）
  F 时间戳契约    每条任务是否知道自己负责哪一格 updatedAt（R100z167 后）
  G 冗余          跨 prompt 逐字重复的长行（瘦身依据，仅 --verbose 详列）
  B7 执行约定     开头声明的「带 cd / 不加 cd」是否 7 份唯一、且与代码块里的命令一致
                  —— 「约定说带 cd、示例不带 cd」这类分叉只有这层能抓

★ 四个必须记住的坑（本脚本逐版踩出来的）：
  1. **否定语境要按「整行」判，不能只开 45 字窗口**：`build_patchN.py` 在 17:00 出现两次，
     一次紧跟「严禁写」，另一次在「**绝对禁止**：…㉔ 写 build_patchN.py 这类…」里，
     离「严禁」两百多字 → 窗口式过滤会把真反例报成死链。
  2. **排版变体**：步骤定义可能写成 `6)` / `- **6)** / `> 6.` / `6、`。
     只认 `^6)` → 周日 21:00 的第 6 步、08:30 的 3.4 步全被误判「无定义」。
  3. **字段名同名不同义**：`summary` 至少指三种东西——`us 五节[].summary`（地板 200）、
     `ashare 五节[].summary`（240）、`aiPrediction.verification.summary`（汇总句）。
     所以 E 类**不做「跨任务互相比较」**（那样 100% 误报），只做「各自 vs 闸门权威口径」。
  4. **区间常被 `**` 包住**：写成「写满 **200-260 字**」时字段名与数字不相邻，
     纯正则会漏采（08:30 的 logic 就被漏掉，看似「没写区间」）。必须先按字段名定位，
     再在 ±120 字窗口里找区间。

用法:
    python3 check_automations.py            # 全部检查
    python3 check_automations.py --verbose  # 附冗余明细与每份 prompt 的体量变化
    python3 check_automations.py --dump     # 把 7 份 prompt 导出到 /tmp/ap/ 便于 diff
    python3 check_automations.py --only B   # 只跑某一类

退出码: 0 = 无 FAIL（WARN 不拦）  1 = 有 FAIL  3 = 读不到数据库
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.expanduser('~/.workbuddy/workbuddy.db')
DUMP_DIR = '/tmp/ap'

# ---------------------------------------------------------------- 期望定义
# match 用子串匹配（自动化名字里的空格/全角/括号变过好几次，全等太脆）。
# rrule 只校 FREQ/BYDAY/BYHOUR/BYMINUTE，避免 BYSECOND 之类噪声。
# slots = 该任务负责写哪一格 updatedAt（G28 / refresh_stamp.py 的 SLOTS 口径）。
TARGETS = [
    dict(key='A0700', match='美股看板 A',
         rrule_want='FREQ=WEEKLY;BYDAY=TU,WE,TH,FR;BYHOUR=7;BYMINUTE=0', slots=['us']),
    dict(key='B0830', match='美股看板 B',
         rrule_want='FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=8;BYMINUTE=30', slots=[]),
    dict(key='S0945', match='开盘精选',
         rrule_want='FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=45', slots=['duanban']),
    dict(key='A1600', match='每日A股数据更新',
         rrule_want='FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=16;BYMINUTE=0', slots=['ashare']),
    # R100z196（2026-10-24 用户拍板）：叙事步由 17:00 推迟到 19:30 —— 龙虎榜 18:00 后才披露完。
    # 别按「16:00 之后一小时」的旧记忆改回来，A 股收盘到榜单发布就是 3 小时。
    dict(key='A1700', match='每日A股叙事与部署',
         rrule_want='FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=19;BYMINUTE=30', slots=[]),
    dict(key='W2100', match='周末要闻与美股研判',
         rrule_want='FREQ=WEEKLY;BYDAY=SU;BYHOUR=21;BYMINUTE=0', slots=['us', 'weekendNews']),
    dict(key='W2200', match='周末AI预测与部署',
         rrule_want='FREQ=WEEKLY;BYDAY=SU;BYHOUR=22;BYMINUTE=0', slots=[]),
]

# 否定语境标记：命中位置前后出现这些词 → 不是「要我执行」，而是历史反例
NEG_MARK = ('严禁', '禁止', '不得', '勿', '不要', '不再', '无需', '不需', '避免',
            '旧', '已删', '历史', '血泪', '废弃', '已下线', '代替', '而非', '不许')

# 步骤定义的排版变体集（这是 L1「易漏」那条的代码化）
STEP_DEF = re.compile(
    r'(?m)^[ \t>]*(?:[-*+][ \t]*)?\**\**[ ]*'          # 行首 / 列表符 / 加粗
    r'(\d+(?:\.\d+)?)[ \t]*[)）.．、]'                    # 6) 6. 6、 6）
)
STEP_REF = re.compile(r'第\s*(\d+(?:\.\d+)?)\s*步')

# 步号序列（D2 用）：只认**括号编号** `6)` `5.11)` `4a)` —— 点号 `1.` 一律当子项。
# 为什么必须区分：07:00 的 5.6 内部有 `1. 2. 3.` 三个复核子项、22:00 附录 A 有
# `1.`-`8.` 兜底子步，混进来会让「步数」凭空多出 8-11 个，任何对账都必然假报。
STEP_SEQ = re.compile(
    r'(?m)^[ \t>]*(?:[-*+][ \t]*)?\**\**[ ]*'
    r'(\d+(?:\.\d+)?|\d+[a-z](?:-\d+)?)[ \t]*[)）]'
)

FLAG_DEF = re.compile(r'[\'"](-{2}[A-Za-z][\w-]*)[\'"]')       # 脚本源码里的 flag 字面量
CMD_LINE = re.compile(r'([A-Za-z_][\w]*\.(?:py|sh))\b([^\n`|]*)')  # 脚本名 + 同行后文


class R:
    def __init__(self):
        self.rows = []          # (code, level, msg)

    def ok(self, code, msg):
        self.rows.append((code, 'PASS', msg))

    def warn(self, code, msg):
        self.rows.append((code, 'WARN', msg))

    def fail(self, code, msg):
        self.rows.append((code, 'FAIL', msg))

    def count(self, level):
        return sum(1 for _, l, _ in self.rows if l == level)

    def dump(self):
        for code, level, msg in self.rows:
            if level != 'PASS':
                print('[%-4s] %-6s %s' % (level, code, msg))
        print('-' * 78)
        for code, level, msg in self.rows:
            if level == 'PASS':
                print('[PASS] %-6s %s' % (code, msg))
        print('=' * 78)
        p, w, f = self.count('PASS'), self.count('WARN'), self.count('FAIL')
        print('自动化体检：PASS=%d WARN=%d FAIL=%d' % (p, w, f))
        return 1 if f else 0


def load():
    """读 7 条自动化；match 子串命中。返回 [{...,'prompt':...}] 与未命中的期望项。"""
    if not os.path.exists(DB):
        print('✗ 读不到自动化数据库：%s' % DB, file=sys.stderr)
        raise SystemExit(3)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = list(c.execute('select id,name,prompt,rrule,schedule_type,status,cwds from automations'))
    out, miss = [], []
    for t in TARGETS:
        hit = [r for r in rows if t['match'] in (r['name'] or '')]
        if not hit:
            miss.append(t)
            continue
        r = max(hit, key=lambda x: len(x['prompt'] or ''))     # 重名取最长的（历史副本）
        d = dict(t)
        d.update(dict(id=r['id'], name=r['name'], prompt=r['prompt'] or '',
                      rrule=r['rrule'] or '', status=r['status'] or '',
                      cwds=r['cwds'] or '', schedule_type=r['schedule_type'] or ''))
        out.append(d)
    return out, miss


# ---------------------------------------------------------------- A 排程与元数据
def chk_meta(T, r):
    for t in T:
        # cwds：必须是合法 JSON 数组，且目录真实存在（手写漏双引号会静默跑空）
        try:
            arr = json.loads(t['cwds'])
            if not isinstance(arr, list):
                raise ValueError('不是数组')
        except Exception as e:
            r.fail('A1', '%s cwds 不是合法 JSON 数组（%s）：%r —— 自动化会静默跑空'
                   % (t['key'], e, t['cwds'][:60]))
            continue
        bad = [p for p in arr if not os.path.isdir(p)]
        if bad:
            r.fail('A1', '%s cwds 目录不存在：%s' % (t['key'], bad))
        else:
            r.ok('A1', '%s cwds 合法且目录存在' % t['key'])

        # rrule：只比 FREQ/BYDAY/BYHOUR/BYMINUTE（BYSECOND 之类的噪声不管）
        if t['schedule_type'] != 'recurring':
            r.fail('A2', '%s schedule_type=%s（应为 recurring）' % (t['key'], t['schedule_type']))
        else:
            keep = ('FREQ', 'BYDAY', 'BYHOUR', 'BYMINUTE')

            def _kv(s):
                return dict(x.split('=', 1) for x in s.replace(' ', '').split(';') if '=' in x)
            got, want = _kv(t['rrule']), _kv(t['rrule_want'])
            diff = {k: (want.get(k), got.get(k)) for k in keep if want.get(k) != got.get(k)}
            if diff:
                r.fail('A2', '%s rrule 与业务日历不符：%s（期望→实得）'
                       % (t['key'], '；'.join('%s %s→%s' % (k, v[0], v[1]) for k, v in diff.items())))
            else:
                r.ok('A2', '%s rrule 正确（%s %s:%s）'
                     % (t['key'], got.get('BYDAY'), got.get('BYHOUR'), got.get('BYMINUTE')))

        if t['status'] != 'ACTIVE':
            r.fail('A3', '%s status=%s（应为 ACTIVE，否则整条不跑）' % (t['key'], t['status']))
        else:
            r.ok('A3', '%s ACTIVE' % t['key'])


# ---------------------------------------------------------------- B 死链
def _py_support(name):
    """脚本支持的 flag 集合 + 文件是否存在。"""
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        for p in glob.glob(os.path.join(HERE, '**', name), recursive=True):
            path = p
            break
    if not os.path.exists(path):
        return None, set()
    try:
        src = open(path, encoding='utf-8', errors='replace').read()
    except Exception:
        return path, set()
    return path, set(FLAG_DEF.findall(src))


def _line_of(p, pos):
    """命中位置所在的**整行**（否定语境必须按行判，不能开小窗口 —— 见文件头坑 1）。"""
    ls = p.rfind('\n', 0, pos) + 1
    le = p.find('\n', pos)
    return p[ls:len(p) if le < 0 else le]


def _code_ranges(p):
    return [(m.start(1), m.end(1)) for m in re.finditer(r'```[a-z]*\n(.*?)```', p, re.S)]


def _in_ranges(pos, rgs):
    return any(a <= pos < b for a, b in rgs)


# references 文档的解析根：项目内 → 用户级 skill → 插件缓存的 skill
_DOC_ROOTS = [os.path.expanduser('~/.workbuddy/skills'),
              os.path.expanduser('~/.workbuddy/plugins/cache')]
_DOC_CACHE = {}


def _resolve_doc(rel):
    """references/xxx.md 的真实位置：项目内没有就去找 skill 包里同名文件。"""
    if rel in _DOC_CACHE:
        return _DOC_CACHE[rel]
    hit = os.path.join(HERE, rel)
    if os.path.exists(hit):
        _DOC_CACHE[rel] = hit
        return hit
    name = os.path.basename(rel)
    for root in _DOC_ROOTS:
        if not os.path.isdir(root):
            continue
        for m in glob.glob(os.path.join(root, '**', 'references', name), recursive=True):
            _DOC_CACHE[rel] = m
            return m
    _DOC_CACHE[rel] = None
    return None


def chk_deadlink(T, r):
    refs, flags_seen = {}, []
    for t in T:
        p = t['prompt']
        rgs = _code_ranges(p)
        # 代码块内的命令：一律当「真要执行」，不做过否定过滤
        for blk in re.findall(r'```[a-z]*\n(.*?)```', p, re.S):
            for ln in blk.split('\n'):
                for m in CMD_LINE.finditer(ln):
                    nm, tail = m.group(1), m.group(2)
                    flags = re.findall(r'(--[A-Za-z][\w-]*)', tail)
                    if flags:
                        flags_seen.append((t['key'], nm, flags))

        # 全文引用：代码块内必查；正文里按整行否定语境过滤
        for m in re.finditer(r'([A-Za-z_][\w]*\.(?:py|sh))\b', p):
            nm = m.group(1)
            if not _in_ranges(m.start(), rgs) and any(k in _line_of(p, m.start()) for k in NEG_MARK):
                continue
            refs.setdefault(nm, []).append(t['key'])

    miss = []
    for nm, who in sorted(refs.items()):
        path, _ = _py_support(nm)
        if path is None:
            miss.append((nm, sorted(set(who))))
    if miss:
        for nm, who in miss:
            r.fail('B1', 'prompt 引用的脚本不存在：%s（%s）—— 死链接'
                   % (nm, '/'.join(who)))
    else:
        r.ok('B1', 'prompt 引用的 %d 个脚本全部存在（已按整行过滤历史反例）' % len(refs))

    # references/*.md —— 可能属于 skill 包（fin-strategy-engine），不能只认项目目录
    mdmiss, mdhit, mdseen = [], 0, set()
    for t in T:
        for m in re.finditer(r'(references/[\w./-]+\.md)', t['prompt']):
            rel = m.group(1)
            if (t['key'], rel) in mdseen:
                continue
            mdseen.add((t['key'], rel))
            if _resolve_doc(rel):
                mdhit += 1
            else:
                mdmiss.append((t['key'], rel))
    if mdmiss:
        for k, f in mdmiss:
            r.fail('B2', '%s 引用的文档在项目与 skill 目录都找不到：%s' % (k, f))
    else:
        r.ok('B2', 'references 文档引用全部在位（%d 处，跨项目/skill 目录解析）' % mdhit)

    # deploy.sh 的命令链（部署时真正会跑的，比 prompt 更该查）
    dp = os.path.join(HERE, 'deploy.sh')
    dmissing = []
    if os.path.exists(dp):
        for ln in open(dp, encoding='utf-8', errors='replace'):
            if ln.lstrip().startswith('#'):
                continue
            for m in re.finditer(r'([A-Za-z_][\w]*\.(?:py|sh))', ln):
                nm = m.group(1)
                if nm == 'deploy.sh':
                    continue
                if _py_support(nm)[0] is None:
                    dmissing.append(nm)
    if dmissing:
        r.fail('B4', 'deploy.sh 调用了不存在的脚本：%s —— 部署链会在该步中断'
               % sorted(set(dmissing)))
    else:
        r.ok('B4', 'deploy.sh 命令链里的脚本全部存在')

    # 命令 flag
    badflag = []
    for key, nm, flags in flags_seen:
        path, support = _py_support(nm)
        if path is None or not support:
            continue
        for fl in flags:
            if fl not in support:
                badflag.append((key, nm, fl))
    if badflag:
        for key, nm, fl in sorted(set(badflag)):
            r.fail('B3', '%s 命令用了 %s 不支持的 flag：%s' % (key, nm, fl))
    else:
        r.ok('B3', '命令行 flag 全部被对应脚本支持（%d 条命令）' % len(flags_seen))


# ---------------------------------------------------------------- B5 基线漂移
def chk_baseline(T, r):
    """B5 mustContain / forbidden 与**线上 prompt 实例**比对。

    为什么必须有这一层（R100z169 实测踩到）：`audit_pipeline` 的 G4 只校验
    `audit_expectations.json` 里「每条自动化都登记了 mustContain」，**从不拿基线去比
    线上 prompt**。于是 15 个 mustContain 短语从 2026-10-19 起就与线上措辞脱节
    （`ashare 全部字段不变` vs 实际 `ashare 全部字段未被改动`），二十多天无人发现——
    基线悄悄退化成「一份没人对账的愿望清单」，G2 承诺的「prompt 漂移可发现」实际失效。

    判据：基线短语必须**逐字**出现在对应自动化的 prompt 里。缺了就是两件事之一：
      (a) 规则真的被删/改弱 → 补 prompt；
      (b) 只是措辞变了 → 改基线（改完这里必须 PASS，否则基线继续腐烂）。
    """
    exp_path = os.path.join(HERE, 'audit_expectations.json')
    if not os.path.exists(exp_path):
        r.fail('B5', '缺少 audit_expectations.json，基线无法对账')
        return
    exp = json.load(open(exp_path, encoding='utf-8'))
    by_id = {t['id']: t for t in T}

    # 覆盖率（R100z180）：库里每一条看板自动化都必须有基线条目。
    # 21:00 曾整条漏登记 —— G2/G3/G4/B5 全都按 exp['automations'] 循环，
    # 它不在表里 = 四道闸门对它零覆盖，规则被删改也不会有人发现（全量核查时揪出）。
    exp_ids = {e.get('id') for e in exp.get('automations', [])}
    uncovered = [t['key'] for t in T if t['id'] not in exp_ids]
    if uncovered:
        r.fail('B5', '%s 在 audit_expectations.json 里没有基线条目 —— 该任务的脚本引用/参数/'
                     'mustContain 全在闸门覆盖之外；补一条 id + schedule + scripts + mustContain'
                     % ','.join(uncovered))

    checked = miss = 0
    missmsg, extramsg = [], []
    for e in exp.get('automations', []):
        t = by_id.get(e.get('id'))
        if t is None:                     # 基线里登记了、库里已无此任务
            extramsg.append('基线 id=%s（%s）在库里找不到对应自动化'
                            % (e.get('id'), (e.get('schedule') or '?')[:20]))
            continue
        for s in (e.get('mustContain') or []):
            checked += 1
            if s not in t['prompt']:
                miss += 1
                missmsg.append('%s 缺基线短语「%s」' % (t['key'], s))
        for s in (e.get('forbidden') or []):
            # `forbidden` 的语义是「禁用这两套步骤编号（圈码）当编号」，不是禁用短语：
            # 圈码在「绝对禁止」正文里合法，只有出现在**行首编号位**才算问题。
            if not isinstance(s, str) or not s:
                continue
            checked += 1
            pat = re.compile(r'(?m)^[ \t>]*(?:\*\*)?%s[ \t]*[)）.．、]' % re.escape(s))
            mm = pat.search(t['prompt'])
            if mm:
                ln = t['prompt'][:mm.start()].count('\n') + 1
                miss += 1
                missmsg.append('%s L%d 用圈码「%s」当步骤编号' % (t['key'], ln, s))
    for m in missmsg:
        r.fail('B5', m + ' —— 规则被删/改弱则补 prompt；仅措辞变了则同步改基线（二选一，不能放着）')
    for m in extramsg:
        r.warn('B5', m + ' —— 该任务已删/改名，基线应同步清理')
    if not miss and not uncovered:
        r.ok('B5', '基线 %d 条短语与线上 prompt 逐字一致（%d 条任务对账、%d 条均有基线条目）'
             % (checked, len(by_id), len(by_id) - len(uncovered)))


# ---------------------------------------------------------------- B6 字段台账
# `dataFields` / `writesFields` / `neverWrites` 三个字段从 2026-10 起就登记在
# audit_expectations.json 里，但**从来没有闸门读它们**（本脚本搜不到这三个键）——
# 和 B5 注释里「15 条 mustContain 与线上脱节二十天」是同一类病：写进基线就以为算核查过了。
# 三个字段真正的用处只有一个：AI 拿到 prompt 时，哪些 data.js 节点该读、该写、绝不能碰。
# 路径写错 = AI 去读一个不存在的节点，然后**编**（这正是 CHECKLIST L18 要求下钻验的原因）。
#
# 所以 B6 只做三件可判定的事，不做语义判断：
#   ① 台账里能解析成路径的条目，必须在 data.js 里真走得通（逐段下钻，报出断在哪一节）；
#   ② `.json` 结尾的当**文件**校验存在性（stock_pool.json 等不是 data.js 节点）；
#   ③ 同一任务的 writesFields 与 neverWrites 不许有同一字段（自相矛盾）。
#   ④ **首跑豁免**（R100z194 起）：脚本新增的产出字段，要等下一次排程真跑完才落盘，
#      在那之前台账指向的是一个「代码里已经会写、data.js 里还没有」的路径。
#      两条路都不能走：删条目 = 放弃登记（正是 B5 那种「不登记就当查过」的病）；
#      无条件放行 = 闸门失守。所以做成**带到期日**的豁免 `pendingFields`：
#        未到期   → WARN（列出来，不装看不见）
#        已到期仍缺 → FAIL（说明脚本压根没写，不是时序问题）
#        已落盘   → WARN 提醒把豁免删掉（自清理，不留永久后门）
#      到期日写**预期的下一次落盘日**，不写「+7 天」这种模糊值。
#   ⑤ **自写字段只验容器**（R100z259）：同一任务 writesFields 登记过的路径，是**本任务自己产出**的，
#      它在下一次排程前必然不存在。实证 2026-10-28 18:00：A1700（19:30）的 dataFields 里有
#      `ashare.sectorsUp[].reason` / `ashare.sectorsDown[].reason` / `ashare.lianban[].story` /
#      `ashare.fundNote` 四条全报 FAIL，而它们正是这一步要写的叙事字段——16:00 重抓把
#      sectorsUp/sectorsDown/lianban 整段重建（只有取数字段），叙事要等 19:30 才写回，
#      所以**每天 16:00~19:30 之间这四条一定不存在**。按存在性判 = 固定假 FAIL，
#      一天里有四小时闸门在报错，久之就和 G10 那条假 WARN 一样被无视。
#      但也不能无条件放行：路径若写到父节点都不存在的名字上（如 `ashare.sectorUp[].reason`），
#      那是真 bug，AI 会往空处落盘。所以自写路径**只验容器**（父级能下钻即算过），
#      内容有没有真写进去由 audit_pipeline 的 G16 字数地板兜底——写错键名时字数仍为 0，
#      照样拦在部署前，覆盖没有变薄。
#   ⑥ **自写字段的反方向检查**（R100z261，2026-10-28 用户「做吧」）：⑤ 只验容器，于是留下一个
#      它自己造的口子——容器永远在（16:00 重建的壳不会消失），所以**哪天脚本或 prompt 把字段名
#      改了**（`reason` → `why`），⑤ 照样放过，闸门一声不响。⑤ 的豁免必须配一个「它太久没写过
#      东西」的哨兵，否则就是新的静默失效点（和 G10 那条假 WARN 同一类：出了问题没人报）。
#      判据：自写路径**父级能下钻（ok）但该字段一天下来都不在**，连续 SW_WARN_DAYS 天 → WARN。
#        · 只认 st2=='ok'：父级是空数组（16:00 刚重建、元素还没填）属于「今天判不了」，记 'skip'，
#          既不计数也不断线——否则每个 16:00~19:30 的窗口都会被误算成一天。
#        · 同一天内多次运行只保留**最后一次**观测：16:00 记 miss、20:00 写完变 hit，当天就以 hit 为准。
#          换句话说，「一整天过去还是没写进去」才算一天，半天不算。
#        · 闸门不是每天都跑，所以日期有空档（周末/未部署）**不算断线**，只有记录在案的 'hit' 才断。
#        · **只在该任务「今天该跑、且已过点 60 分钟」时才记 miss**（`_sw_due`）：16:00 跑闸门时
#          19:30 才写的叙事字段必然不在、节假日该任务根本不跑——这两种都不是缺陷。不挡掉，
#          ⑥ 立刻退化成「每天固定报同一句」的噪声，跟它要防的病一样。
#      状态落在 `.cache/b6_selfwrite_state.json`（.cache/ 已在 .gitignore，属本机滚动状态不是事实源）。
# 散文条目（含中文/空格/通配 `*`）跳过并计数——不是漏洞，是台账允许写人话。
CLEAN_PATH = re.compile(r'^[A-Za-z_]\w*(?:(?:\.\w+)|(?:\[\w*\]))*$')
CJK_RE = re.compile(r'[\u4e00-\u9fff]')
PAREN_RE = re.compile(r'[（(][^）)]*[）)]')


def _field_paths(raw):
    """台账条目 → 可校验路径列表；散文/通配返回 []。

    台账是人写的，混着四种写法，必须在**解析层**归一，不能靠改文字（改文字就失去台账意义）：
      `ashare.sectorsUp[]/sectorsDown[]/allBoards[]`  并列省略前缀：第二段无点 → 继承上段前缀
      `panorama.markets[us].date`                    数组按元素 `key` 取值（markets 是数组不是字典）
      `stock_pool.json`                              文件名，不是 data.js 节点
      `duanban（merge_duanban 合并，…）`              括号里是注释，剥掉后才是路径
    """
    t = PAREN_RE.sub('', raw).replace(' / ', '/').strip()
    if not t or CJK_RE.search(t) or ' ' in t or '*' in t:
        return []
    out, prefix = [], ''
    for seg in t.split('/'):
        seg = seg.strip()
        if not seg:
            continue
        full = seg if ('.' in seg or not prefix) else prefix + seg
        if CLEAN_PATH.match(full):
            out.append(full)
            # 段落带 `.json` 是**文件**，不是命名空间：后面并列的 `policySignals`
            # 若继承 `market_bench.` 就成了幽灵路径（2026-10-24 实测误报）。
            prefix = '' if full.endswith('.json') else (full[:full.rfind('.') + 1] if '.' in full else '')
    return out


def _resolve_field(D, path):
    """在 data.js 里走一遍路径。返回 (状态, 说明)，状态 = ok / empty / bad。"""
    if path.endswith('.json'):
        if os.path.exists(os.path.join(HERE, path)):
            return 'ok', ''
        return 'bad', '%s 文件不存在（台账登记了但盘上没有）' % path
    cur, parts = D, path.split('.')
    for i, seg in enumerate(parts):
        m = re.match(r'^(\w+)((?:\[[^\]]*\])*)$', seg)
        if not m:
            return 'bad', '%s 第 %d 节写法不认识' % (path, i + 1)
        key, brs = m.group(1), m.group(2) or ''
        if cur is None:
            # R100z218：父节点是 null 不等于「字段不存在」，要按「今天没落值」处理（= empty，暂不可验）。
            # 实证（2026-10-25 周日 22:00 跑批后）：aiPrediction.date 已指向 2026-10-26，六条板块的
            # exp 全为 null（R100z11 设计：非预期型板块不落 exp）；verification / stockCheck 要等
            # 交易日 08:30 才有内容。旧写法一律判 bad → 每个周末固定 23 条假 FAIL，
            # 又把「闸门永远在报错」变回常态，与 G10 当年那条假 WARN 同一类病。
            return 'empty', '%s 当前为 null（尚未落值，属正常）' % '.'.join(parts[:i + 1])
        if not isinstance(cur, dict):
            return 'bad', '%s 的父节点不是对象（%s）' % ('.'.join(parts[:i + 1]), type(cur).__name__)
        if key not in cur:
            where = '顶层缺 %s' % key if i == 0 else '%s 下没有 %s' % ('.'.join(parts[:i]), key)
            return 'bad', '%s → %s' % (path, where)
        cur = cur[key]
        for br in re.findall(r'\[([^\]]*)\]', brs):
            if not isinstance(cur, list):
                return 'bad', '%s 不是数组，没法用 [%s] 取元素' % ('.'.join(parts[:i + 1]), br)
            if not cur:
                return 'empty', '%s 当前是空数组' % '.'.join(parts[:i + 1])
            if br == '':
                cur = cur[0]
            else:
                hit = [x for x in cur if isinstance(x, dict) and str(x.get('key', '')) == br]
                if not hit:
                    return 'bad', '%s 里没有 key=%s 的元素' % ('.'.join(parts[:i + 1]), br)
                cur = hit[0]
    return 'ok', ''


def _parent_path(p):
    """自写字段的容器路径：`ashare.sectorsUp[].reason` → `ashare.sectorsUp[]`；
    `ashare.fundNote` → `ashare`。

    只用来判「这段落盘地址挂在一棵真树上」，不判字段在不在（见 B6 规则 ⑤）。
    父级保留末尾的 `[]`——`_resolve_field` 会按首元素下钻，顺带验出「数组元素的形状变了」。
    """
    head, dot, _ = p.rpartition('.')
    return head if dot else ''


# ------------------------------------------------------- B6 ⑥ 自写字段哨兵状态
SW_STATE = os.path.join(HERE, '.cache', 'b6_selfwrite_state.json')
SW_WARN_DAYS = 3     # 连续 3 天「容器活着、字段却不在」→ WARN（用户口径）
SW_KEEP_DAYS = 14    # 每条路径只留最近 14 个观测日，够判 3 天又不让状态文件长大
SW_GRACE_MIN = 60    # 宽限 60 分钟：任务刚启动时字段自然还没落盘，不算「今天缺」
WD = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']


def _sw_due(t, now=None):
    """这条字段的产出任务「今天该跑、而且已经跑完了」吗？只有 True 才允许记 miss。

    少了这一道，⑥ 会变成新的常态噪声源（本项目的旧病：闸门永远在报同一句，久之被无视）：
      · **时点**：19:30 才写的叙事字段，16:00 跑闸门时必然不存在；A1600 自己的字段在 16:00
        刚启动时也还没写。所以 `now` 必须 ≥ 该任务 rrule 的 BYHOUR/BYMINUTE + 宽限。
      · **非交易日**：周末/节假日该任务本来不跑，字段缺不是缺陷 → 判不了，记 skip 不计数。
        否则国庆七天连休会攒出 7 天「连续缺失」，节后开工先吃一条假 WARN。
    任务记录里没有 rrule（自动化已从库里消失，A0 会单独报）→ 无从判断，按「该跑」处理。
    """
    if not isinstance(t, dict):
        return True
    now = now or datetime.now()   # 只用这一个时间源：星期与时刻必须同一天，否则跨午夜/测试会错位
    rr = str(t.get('rrule') or '')
    m = re.search(r'BYDAY=([A-Z,]+)', rr)
    if m and WD[now.weekday()] not in m.group(1).split(','):
        return False
    m = re.search(r'BYHOUR=(\d+)', rr)
    if not m:
        return True
    mi = re.search(r'BYMINUTE=(\d+)', rr)
    return (now.hour * 60 + now.minute) >= (int(m.group(1)) * 60 + int(mi.group(1) if mi else 0)
                                            + SW_GRACE_MIN)


def _sw_load():
    """{路径: {日期: 'miss'|'hit'|'skip'}}；读不到就当空开局（首次运行）。"""
    try:
        with open(SW_STATE, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _sw_save(st):
    """原子写。状态坏了不拦部署——它是提示用的哨兵，不是事实源。"""
    try:
        os.makedirs(os.path.dirname(SW_STATE), exist_ok=True)
        tmp = SW_STATE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(st, f, ensure_ascii=False, indent=0, sort_keys=True)
        os.replace(tmp, SW_STATE)
    except Exception:
        pass


def _sw_mark(st, path, today, verdict):
    """登记一天一次的观测结论（同一天重跑以最后一次为准，见 ⑥ 第 2 条）。"""
    rec = st.setdefault(path, {})
    rec[today] = verdict
    if len(rec) > SW_KEEP_DAYS:
        for d in sorted(rec)[:-SW_KEEP_DAYS]:
            rec.pop(d, None)


def _sw_streak(rec, today):
    """从今天往回数「整天都没写进去」的连续天数，返回 (天数, 最早那天)。

    只在 rec[today] == 'miss' 时才算数（今天还没判完就报警是误报）。
    'skip' 透明（无法判定，不断线也不计数）；遇到 'hit' 断线。
    日期空档（闸门没跑的那几天）不算断线——没有记录不等于写进去了。
    """
    if rec.get(today) != 'miss':
        return 0, ''
    days, first = 0, today
    for d in sorted(rec, reverse=True):
        v = rec[d]
        if v == 'miss':
            days += 1
            first = d
        elif v == 'hit':
            break
    return days, first


def chk_fields(T, r, verbose=False):
    exp_path = os.path.join(HERE, 'audit_expectations.json')
    if not os.path.exists(exp_path):
        r.fail('B6', '缺少 audit_expectations.json，字段台账无法对账')
        return
    exp = json.load(open(exp_path, encoding='utf-8'))
    dj = os.path.join(HERE, 'data.js')
    try:
        raw = open(dj, encoding='utf-8').read()
        D = json.loads(raw[raw.index('{'):raw.rindex('}') + 1])
    except Exception as e:
        r.fail('B6', '读不了 data.js（%s）—— 字段路径无从校验' % e)
        return

    by_id = {t['id']: t for t in T}
    n_ok = n_skip = n_empty = n_pend = n_own = n_stale = 0
    bad = []
    # ⑥ 的哨兵状态：{路径: {日期: miss/hit/skip}}，本轮观测完再裁剪写回
    sw = _sw_load()
    own_paths = set()
    stale_warned = set()   # 同一路径可能同时登记在两条任务下，只报一次（否则同一条 WARN 印两遍）
    # 首跑豁免：{(任务id, 路径): (到期日, 原因)}
    pend = {}
    for aid, v in (exp.get('pendingFields') or {}).items():
        for f in (v.get('fields') or []):
            pend[(aid, f)] = (str(v.get('until') or ''), str(v.get('why') or ''))
    today = date.today().isoformat()
    for e in exp.get('automations', []):
        t = by_id.get(e.get('id'))
        key = t['key'] if t else (e.get('schedule') or '?')[:16]
        led = {k: [v for v in (e.get(k) or []) if isinstance(v, str)]
               for k in ('dataFields', 'writesFields', 'neverWrites')}
        lack = [k for k, v in led.items() if not v]
        if lack:
            bad.append('%s 台账缺 %s —— 该任务的字段归属没有登记，AI 只能靠猜'
                       % (key, '/'.join(lack)))
        # ③ 自相矛盾：同一任务声称「必写」又「绝不写」
        w = {p for v in led['writesFields'] for p in _field_paths(v)}
        nw = set()
        for v in led['neverWrites']:
            nw.update(_field_paths(v))
        for f in sorted(w & nw):
            bad.append('%s 的 writesFields 与 neverWrites 同时登记了 %s —— 台账自相矛盾'
                       % (key, f))
        for kind in ('dataFields', 'writesFields', 'neverWrites'):
            for v in led[kind]:
                ps = _field_paths(v)
                if not ps:
                    n_skip += 1
                    continue
                due = _sw_due(t)   # ⑥：该任务今天该跑、且已过点（否 → 只能记 skip）
                for p in ps:
                    st, why = _resolve_field(D, p)
                    ex = pend.get((e.get('id'), p))
                    # 自写路径 = 本任务自己产出的字段（规则 ⑤/⑥ 的对象）。
                    # `.json` 是**文件**不是 data.js 节点，走上面的文件分支，不套 ⑤/⑥。
                    is_own = (p in w and not p.endswith('.json'))
                    if is_own:
                        own_paths.add(p)
                    if st == 'ok':
                        n_ok += 1
                        if is_own:
                            _sw_mark(sw, p, today, 'hit')
                    elif st == 'empty':
                        n_empty += 1
                    elif is_own:
                        # 规则 ⑤：**先按原样校验**，只有「真的不存在」才轮到自写豁免。
                        # 顺序很重要——先豁免会把 161 条本来存在的自写路径整体降成「只验容器」，
                        # 等于悄悄削掉一层校验（2026-10-28 实测：那样 n_ok 从 269 掉到 108）。
                        par = _parent_path(p)
                        st2, why2 = _resolve_field(D, par) if par else ('ok', '')
                        if st2 in ('ok', 'empty'):
                            n_own += 1
                            # 规则 ⑥：容器活着 → 不是「今天还没到写的时候」，就是「字段名早变了」。
                            # 父级是空数组时判不了（刚重建、元素还没填），记 skip 不计也不断；
                            # 父级有内容、任务今天该跑且已过点，才算真缺一天，连续 SW_WARN_DAYS 天才报警。
                            _sw_mark(sw, p, today, 'miss' if (st2 == 'ok' and due) else 'skip')
                            days, first = _sw_streak(sw[p], today)
                            if days >= SW_WARN_DAYS and p not in stale_warned:
                                stale_warned.add(p)
                                n_stale += 1
                                r.warn('B6', '%s 的 %s 已连续 %d 天「容器活着、字段却不在」'
                                              '（最早缺失日 %s，父级 %s 每天都能下钻）—— '
                                              '这不是时序问题：容器名没变，那多半是脚本或 prompt '
                                              '把字段名改了、台账没跟着改。去 audit_expectations.json '
                                              '的 writesFields 与 %s 的 prompt 里各搜一遍这个字段名，'
                                              '台账/脚本/prompt 三处必须逐字一致'
                                       % (key, p, days, first, par, key))
                        else:
                            bad.append('%s 的 %s 是自写字段，但它要挂的容器 %s 在 data.js 里不存在：%s'
                                       ' —— 写回会落到空处' % (key, p, par, why2))
                        continue
                    elif ex and ex[0] and today <= ex[0]:
                        n_pend += 1
                        r.warn('B6', '%s 的 %s 尚未落盘（首跑豁免至 %s）：%s'
                               % (key, p, ex[0], ex[1] or '脚本下一次排程后写入'))
                        continue
                    else:
                        note = ''
                        if ex and ex[0] and today > ex[0]:
                            note = '（首跑豁免 %s 已过期：脚本没写，不是时序问题）' % ex[0]
                        bad.append('%s 的 %s 指向不存在的字段：%s —— %s%s'
                                   % (key, kind, why,
                                      'AI 照着写会往空处落盘' if kind == 'writesFields'
                                      else 'AI 会读到 undefined 然后自己编', note))
                        continue
                    if ex:
                        r.warn('B6', '%s 的 %s 已能走通 —— 首跑豁免应从 pendingFields 删掉（否则豁免长期化）'
                               % (key, p))
    # ⑥ 状态收尾：只留台账里还登记着的自写路径。字段从台账删掉后状态一起清，
    # 免得文件无限长，也免得同一路径下次重新登记时带着上次的连续天数「复活」直接报 WARN。
    for p in list(sw):
        if p not in own_paths:
            sw.pop(p, None)
    _sw_save(sw)
    for m in bad:
        r.fail('B6', m)
    if not bad:
        r.ok('B6', '字段台账 %d 条路径在 data.js/%s 里走得通（空数组 %d 条暂不可验、'
                   '首跑豁免 %d 条、自写字段只验容器 %d 条、散文 %d 条跳过；'
                   '`[]` 按首元素下钻，不保证每个元素都带该键）'
                   '—— 7 条任务的读写边界与线上数据一致'
             % (n_ok, '盘上文件', n_empty, n_pend, n_own, n_skip))
        if n_stale:
            r.warn('B6', '另有 %d 条自写字段已连续 %d 天「容器在、字段不在」—— '
                         '已按规则 ⑥ 逐条 WARN 在上方，机器判不出是「还没写」还是「改名了」，'
                         '所以不 FAIL；但连续天数继续涨就说明字段名真的变了，别当噪声'
                   % (n_stale, SW_WARN_DAYS))


# ------------------------------------------------- B7 执行约定一致性
# 事故（2026-10-28 全量核查实测，用户「逐条代码仔仔细细检查」）：
#   7 份 prompt 里 5 份明文写「每条命令统一以 `cd /Users/.../market dashboard && ` 开头
#   （automation 的 shell 不保证 cwd）」，另外 2 份（周日 21:00 / 22:00）写的是相反口径
#   「本任务 cwd 即项目目录，命令直接执行、不加 `cd` 前缀」——**同一件事两个说法**。
#   更讽刺的是：这 7 份的代码块里 **69 条命令一条都没带 cd**，即「约定」与「示例」
#   全部相反；而那 2 份写「不加 cd」的，自己正文里又有 5 处 / 3 处带 cd 的命令。
#
# 为什么这不只是「文档难看」：
#   现在不会炸，因为 7 条 automation 的 cwds 都是有效目录，cwd 有保证。
#   但 cwds 是**最容易静默坏掉**的一环——历史上踩过 `["/path"]` 手抖写成 `[/path]`，
#   automation_update 渲染成 []，任务静默跑空、不报错。那一天一旦到来：
#   所有 `run-python xxx.py` 全部在错误目录执行 → 每个脚本都「找不到文件」→
#   没有任何一层闸门会报（脚本连启动都没启动，日志里连 traceback 都没有）。
#   而模型看到「约定说带 cd、示例不带 cd」，只能自己挑一种，下次改 prompt 继续飘。
#
# 判据两件事，分级不同：
#   ① 7 份之间的声明口径必须唯一 —— 分叉 = FAIL（CHECKLIST L18 明文：只留一种写法）
#   ② 单份内部「声明 vs 代码块实际」不一致 = WARN（有 cwds 兜底，属文档不一致）
_CMD_HEAD = re.compile(
    r'^(/Users/loccco/\.workbuddy/binaries/(?:run-python|run-node)'
    r'|run-python|run-node|bash|sh|python3|\./)')


def _code_block_cmds(prompt):
    """只取 ``` 围栏内的可执行命令行，返回 (带cd, 不带cd) 两个 [(行号, 文本)]。

    为什么要按围栏切：prompt 正文里大量出现 `xxx.py` 的**行内引用**（「执行
    `stock_risk_blacklist.py --write`」），那些不是要执行的命令，算进去会 10 倍虚报。
    """
    with_cd, without = [], []
    inblk = False
    for i, ln in enumerate(prompt.split('\n'), 1):
        s = ln.strip()
        if s.startswith('```'):
            inblk = not inblk
            continue
        if not inblk or not s:
            continue
        if s.startswith('cd '):
            with_cd.append((i, s))
            continue
        # 注释/说明行不是命令（A1700 曾出现「（该步内部接 stop_tracker.main()…）」）
        if s.startswith(('#', '（', '//', '—', '⚠')):
            continue
        if _CMD_HEAD.match(s):
            without.append((i, s))
    return with_cd, without


def _cd_decl(prompt):
    """识别执行约定的声明口径：'cd' / 'no-cd' / None。"""
    head = prompt[:2000]                      # 声明只在开头，避免正文里误命中
    if re.search(r'不加\s*`?cd`?\s*前缀', head):
        return 'no-cd'
    if re.search(r'(统一|一律|必须|都要?|均)\s*以\s*`?cd\b', head):
        return 'cd'
    return None


def chk_cdconv(T, r, verbose=False):
    decl = {t['key']: _cd_decl(t['prompt']) for t in T}
    stated = {k: v for k, v in decl.items() if v}

    # ① 跨份口径唯一性
    kinds = set(stated.values())
    if len(kinds) > 1:
        by = {}
        for k, v in stated.items():
            by.setdefault(v, []).append(k)
        r.fail('B7', '执行约定口径分叉：%s —— 同一件事两种写法，模型只能自己猜一种；'
                     'CHECKLIST L18 要求 7 份只留一种（本项目：一律带 cd）。'
                     '带 cd 的分支才有 cwds 失效时的兜底能力，建议把「不加 cd」改成「一律带 cd」'
               % '；'.join('%s ← %s' % (v, ','.join(ks)) for v, ks in by.items()))
    elif kinds:
        r.ok('B7', '执行约定口径统一（%s）：%d/%d 份有明文声明'
             % (list(kinds)[0], len(stated), len(T)))
    else:
        r.warn('B7', '7 份都没有明确的执行约定（cd / 不加 cd）—— 命令能否跑全看 cwds，'
                     'cwds 一坏就是全体静默跑空')

    # ② 单份内部一致
    nbad = 0
    for t in T:
        d = decl[t['key']]
        with_cd, without = _code_block_cmds(t['prompt'])
        if d == 'cd' and without:
            nbad += 1
            r.warn('B7', '%s 声明「命令统一以 cd 开头」，但代码块 %d/%d 条命令没带（首个 L%d: %s）'
                   % (t['key'], len(without), len(with_cd) + len(without),
                      without[0][0], without[0][1][:58]))
            if verbose:
                for i, c in without:
                    print('        · L%d: %s' % (i, c[:90]))
        elif d == 'no-cd' and with_cd:
            nbad += 1
            r.warn('B7', '%s 声明「不加 cd 前缀」，但代码块里有 %d 条命令带了 cd（首个 L%d）——'
                         '同一份 prompt 自相矛盾'
                   % (t['key'], len(with_cd), with_cd[0][0]))
        elif d is None and (with_cd or without):
            nbad += 1
            r.warn('B7', '%s 没有执行约定声明，却有 %d 条命令 —— 无法判定该不该带 cd'
                   % (t['key'], len(with_cd) + len(without)))
    if not nbad:
        r.ok('B7', '各份的「声明口径」与「代码块实际写法」一致')


# ---------------------------------------------------------------- D 死步骤
def chk_steps(T, r):
    bad = []
    for t in T:
        p = t['prompt']
        defined = set(STEP_DEF.findall(p))
        refs = set(STEP_REF.findall(p))
        miss = sorted(refs - defined, key=lambda x: [int(i) for i in x.split('.')])
        if miss:
            bad.append((t['key'], miss, sorted(defined, key=lambda x: [int(i) for i in x.split('.')])))
    if bad:
        for key, miss, defined in bad:
            r.fail('D1', '%s 引用了未定义步骤 %s（已定义：%s）—— AI 会被指到不存在的步骤'
                   % (key, miss, ','.join(defined)))
    else:
        r.ok('D1', '「第 N 步」引用全部有定义（变体集：N) / - **N) / N. / N、）')

    # D2 基线步号对账（R100z180）：B5 是**短语**级对账，抓不到「整步被删」——
    # 删掉一步后剩下的短语全在，B5 照样绿。stepOrder 一直是「只登记不校验」的
    # 装饰性字段（全仓没有任何闸门读它），2026-10-23 全量核查时才发现。
    # 判据：线上括号编号序列 == 基线 stepOrder（增删/改名都会露出来）。
    exp_path = os.path.join(HERE, 'audit_expectations.json')
    if not os.path.exists(exp_path):
        r.fail('D2', '缺少 audit_expectations.json，步号基线无法对账')
        return
    exp = json.load(open(exp_path, encoding='utf-8'))
    by_id = {t['id']: t for t in T}
    drift, checked = [], 0
    for e in exp.get('automations', []):
        t = by_id.get(e.get('id'))
        want = [str(x) for x in (e.get('stepOrder') or [])]
        if t is None or not want:
            continue
        checked += 1
        got = STEP_SEQ.findall(t['prompt'])
        gone = [x for x in want if x not in got]      # 基线有、线上没了 = 步骤被删/改名
        new = [x for x in got if x not in want]       # 线上有、基线没有 = 基线该补
        if gone or new:
            drift.append('%s 基线有线上无 %s；线上有基线无 %s'
                         % (t['key'], ','.join(gone) or '无', ','.join(new) or '无'))
    for m in drift:
        r.fail('D2', m + ' —— 步骤增删是**结构变更**：确认是有意为之再同步 stepOrder'
                        '（B5 只比短语，整步被删它查不出来）')
    if not drift:
        r.ok('D2', '%d 条任务的括号步号序列与基线 stepOrder 完全一致（点号子项已排除）' % checked)



# ---------------------------------------------------------------- E 口径对账
# 为什么不做「同一字段跨任务比区间」：`summary` 至少指三种字段
#   （us 五节 220-400 / ashare 五节 240-420 / verification 汇总 80-150），
# 纯按名字聚合必然假报。见文件头坑 3。
# 这里改做**覆盖检查**：G16 真会判的每个字段，负责写它的那条 prompt 必须
# 在字段名附近写明「多少字」。没写 = AI 靠猜，靠猜就会撞地板 → 静默失败。
#
# 前缀 → (负责的任务, 该字段在 prompt 里的短名（可给多个别名）, 样例 label)
# 只登记「AI 亲手写」的字段；脚本模板生成的（us.summary / accumulation.*.note）
# 不登记 —— 登记了会假报「prompt 没写字数」。
RESP = [
    ('us.sectorsUp[].reason.detail',   ('A0700', 'W2100'), 'detail',  'us.sectorsUp[0].reason.detail'),
    ('us.sectorsDown[].reason.detail', ('A0700', 'W2100'), 'detail',  'us.sectorsDown[0].reason.detail'),
    ('us.outlook',                     ('A0700', 'W2100'), 'outlook', 'us.outlook'),
    ('ashare.sectorsUp[].reason.detail',   ('A1700',),     'detail',  'ashare.sectorsUp[0].reason.detail'),
    ('ashare.sectorsDown[].reason.detail', ('A1700',),     'detail',  'ashare.sectorsDown[0].reason.detail'),
    ('ashare.outlook',                 ('A1700',),         'outlook', 'ashare.outlook'),
    ('ashare.themePicks[].logic',      ('A1700',),         'logic',   'ashare.themePicks[0].logic'),
    ('ashare.lianban[].story',         ('A1700',),         'story',   'ashare.lianban[0].story'),
    ('aiPrediction.sectors[].logic',   ('B0830', 'W2200'), 'logic',   'aiPrediction.sectors[0].logic'),
    # openOutlook 的字数区间在 prompt 里挂在子字段 content 上，别名一起找
    ('openOutlook',                    ('B0830', 'W2200'), ('openOutlook', 'content'), 'openOutlook'),
    ('aiPrediction.openOutlook',       ('B0830', 'W2200'), ('openOutlook', 'content'), 'aiPrediction.openOutlook'),
    ('duanban.confirmed[].story',      ('A1600', 'A0700', 'W2200'), 'story', 'duanban.confirmed[0].story'),
    ('duanban.watching[].story',       ('A1600', 'A0700', 'W2200'), 'story', 'duanban.watching[0].story'),
    ('duanban.star.pushText',          ('S0945',),         'pushText', 'duanban.star.pushText'),
    ('duanban.star.sentiment',         ('S0945',),         'sentiment', 'duanban.star.sentiment'),
    ('deduce#.main',                   ('A1700', 'B0830', 'W2100', 'W2200'), 'main', 'deduce#0.main'),
]
# us / ashare 五节要闻 summary 单列（字段同名，靠任务区分）
RESP_NEWS = [
    ('us',     ('A0700', 'W2100'), 'summary'),
    ('ashare', ('A1700',),         'summary'),
]

RANGE = re.compile(r'(\d{2,4})\s*[-~－—–]\s*(\d{2,4})\s*字')
WIN = 170          # 字段名 → 区间 的允许距离


def _gated_floors():
    """从真实闸门代码取地板：audit_pipeline.collect_g16_checks(当前 data.js)。"""
    try:
        import audit_pipeline as ap
        raw = open(os.path.join(HERE, 'data.js'), encoding='utf-8').read()
        D = json.loads(raw[raw.index('{'):raw.rindex('}') + 1])
        out = {}
        for label, _txt, floor in ap.collect_g16_checks(D):
            norm = re.sub(r'\[\d+\]', '[]', label)
            norm = re.sub(r'^deduce#\d+', 'deduce#', norm)
            out.setdefault(norm, floor)
        return out
    except Exception as e:
        print('  ⚠ 取不到 G16 地板（%s）→ E 类降级为只查「有没有写」' % e)
        return {}


def _nearest_range(p, name, win=WIN):
    """name 可以是单个短名，也可以是别名元组（取所有别名里最近的那个）。"""
    names = name if isinstance(name, (tuple, list)) else (name,)
    best = None
    for nm in names:
        got = _nearest_one(p, nm, win)
        if got and (best is None or got[2] < best[2]):
            best = got
    return best


def _nearest_one(p, name, win=WIN):
    """字段名 → 最近的那个「A-B 字」。返回 (lo, hi, 距离) 或 None。

    必须取**最近**而不是窗口里第一个：`pushText` 与 `sentiment` 两段紧挨着，
    取第一个会把 sentiment 的 120-200 算到 pushText 头上（→ 假 FAIL）。
    """
    best = None
    for m in re.finditer(re.escape(name), p):
        for mm in RANGE.finditer(p, max(0, m.start() - win), m.end() + win):
            lo, hi = int(mm.group(1)), int(mm.group(2))
            if lo >= hi:
                continue
            # 真实距离：区间在字段名之后就量到「名尾」，在之前就量到「名首」
            d = (mm.start() - m.end()) if mm.start() >= m.end() else (m.start() - mm.end())
            if best is None or d < best[2]:
                best = (lo, hi, d)
    return best


def chk_coverage(T, r, verbose=False):
    BY = {t['key']: t['prompt'] for t in T}
    floors = _gated_floors()
    miss, bad, soft, checked = [], [], [], 0

    for label, tasks, short, sample in RESP:
        for k in tasks:
            p = BY.get(k)
            if p is None:
                continue
            got = _nearest_range(p, short)
            checked += 1
            if got is None:
                miss.append((k, label, short))
                continue
            floor = floors.get(label)
            # 只有「紧邻」才算铁证（≤12 字）；远一点就只提示 —— 自由散文里
            # 机器无法可靠判定某句区间属于哪个字段，硬判会制造假 FAIL。
            if floor and got[0] < floor:
                (bad if got[2] <= 12 else soft).append((k, label, got, floor))

    for mkt, tasks, short in RESP_NEWS:
        for k in tasks:
            p = BY.get(k)
            if p is None:
                continue
            got = _nearest_range(p, short)
            checked += 1
            if got is None:
                miss.append((k, '%s 五节[].summary' % mkt, short))

    if bad:
        for k, label, got, floor in bad:
            r.fail('E2', '%s 的 %s 紧跟着写了 %d-%d 字，下限低于 G16 地板 %d —— '
                         'prompt 允许的字数会被闸门判 FAIL'
                   % (k, label, got[0], got[1], floor))
    else:
        r.ok('E2', '已登记的 %d 处字段口径下限均不低于 G16 地板' % checked)
    for k, label, got, floor in soft:
        r.warn('E2', '%s 的 %s 附近区间 %d-%d 字低于地板 %d（距离 %d 字，归属需人工确认）'
               % (k, label, got[0], got[1], floor, got[2]))

    if miss:
        for k, label, short in miss:
            r.warn('E1', '%s 的 %s 附近没写字数区间（找的是「%s」）—— '
                         'AI 只能凭空估字数，容易撞 G16 地板' % (k, label, short))
    else:
        r.ok('E1', 'G16 会判的字段，负责它的 prompt 全都写明了字数区间')

    if verbose and floors:
        print('  · 当前 G16 地板（从 collect_g16_checks 实测）：')
        for k, v in sorted(floors.items()):
            print('      %-46s %d' % (k, v))


# ---------------------------------------------------------------- F 时间戳契约
def chk_stamp_contract(T, r):
    bad = []
    for t in T:
        p = t['prompt']
        has_stamp_rule = ('updatedAt' in p) or ('refresh_stamp' in p)
        if not has_stamp_rule:
            bad.append((t['key'], '整份 prompt 未提 updatedAt / refresh_stamp'))
            continue
        # 写 data.js 的任务必须知道「顶层 updatedAt 禁手写」
        writes = 'data.js' in p
        if writes and 'refresh_stamp' not in p and '禁止手写' not in p and '勿手写' not in p:
            bad.append((t['key'], '会写 data.js 但没写「顶层 updatedAt 由 refresh_stamp 生成」'))
    if bad:
        for k, why in bad:
            r.fail('F1', '%s 时间戳契约缺失：%s（R100z167 后 G28 会拦部署）' % (k, why))
    else:
        r.ok('F1', '7 条任务的时间戳契约齐全（顶层 updatedAt 单一主人 = refresh_stamp.py）')


# ---------------------------------------------------------------- G 冗余
def chk_dup(T, r, verbose=False):
    """G1 冗余体检（R100z169 改写判据）。

    初版拿「跨 prompt 逐字重复」当冗余指标，实测**方向是错的**：
      ① 7 条自动化各自在**独立上下文**里运行，B 任务看不到 A 任务的 prompt，
         所以共享契约（新闻白名单/时间戳归属/简称 ST/字数纪律）**必须在每份里各存一份**，
         删了就是让某条任务失明——这是独占，不是冗余；
      ② 指标还会反向激励错误动作：把 `cd <项目> && ` 前缀去掉后命令行跨份变**一致**，
         重复率反而从 9.7% 涨到 14.4%，而总字数其实降了 3.4%。

    改为只把**单份内部**的重复当问题（同一份里同一句话说两遍才是真啰嗦），
    跨份重复只作为信息量输出（附 by-design 说明），不再报警。
    """
    total_len = sum(len(t['prompt']) for t in T)

    # ① 单份内部重复：真冗余，报警
    #    但**命令行不算**——同一条命令在不同步骤各出现一次是故意的幂等重跑
    #    （如 stock_risk_blacklist.py --write 在前置扫黑名单与写回后再扫各跑一次），
    #    删掉任意一处就等于删掉那一步的指令。
    def _is_cmd(s):
        return bool(CMD_LINE.search(s)) or s.startswith('cd ') or s.startswith('cd"')

    intra = []
    for t in T:
        seen = {}
        for ln in t['prompt'].split('\n'):
            s = ln.strip()
            if len(s) >= 40 and not _is_cmd(s):
                seen[s] = seen.get(s, 0) + 1
        for s, n in seen.items():
            if n >= 2:
                intra.append((t['key'], n, s))
    if intra:
        waste = sum((n - 1) * len(s) for _, n, s in intra)
        for k, n, s in sorted(intra, key=lambda x: -(x[1] - 1) * len(x[2]))[:8]:
            r.warn('G1', '%s 同一行在**本份内**重复 %d 次（%d 字）：%s'
                   % (k, n, len(s), s[:70]))
        r.warn('G1', '单份内部重复共 %d 处、可省约 %d 字符（同一句说两遍是真啰嗦，应删一次）'
               % (len(intra), waste))
    else:
        r.ok('G1', '单份内部无重复长行（7 份 prompt 各自无自我复读）')

    # ② 跨份重复：信息量，不报警（独立上下文必需的共用契约）
    owner = {}
    for t in T:
        for ln in t['prompt'].split('\n'):
            s = ln.strip()
            if len(s) >= 40:
                owner.setdefault(s, set()).add(t['key'])
    cross = [(s, len(w)) for s, w in owner.items() if len(w) >= 2]
    cross.sort(key=lambda x: -len(x[0]) * x[1])
    shared = sum(len(s) * n for s, n in cross)
    r.ok('G1', '跨 prompt 共用契约 %d 条 / %d 字符（占 %0.1f%%）—— 7 份独立上下文，'
               '共用块必须各存一份，属 by-design 不算冗余；总字数 %d'
         % (len(cross), shared, 100.0 * shared / max(1, total_len), total_len))
    if verbose:
        for s, n in cross[:12]:
            print('    x%d %s' % (n, s[:100]))


# ---------------------------------------------------------------- main
def main():
    ap_ = argparse.ArgumentParser(description='7 条看板自动化的机检（死链/死步骤/排程/区间冲突/冗余）')
    ap_.add_argument('--verbose', action='store_true')
    ap_.add_argument('--dump', action='store_true', help='导出 7 份 prompt 到 /tmp/ap/')
    ap_.add_argument('--only', default='', help='只跑某类：A B D E F G')
    a = ap_.parse_args()

    T, miss = load()
    r = R()
    if miss:
        for t in miss:
            r.fail('A0', '找不到自动化：%s（期望名字含「%s」）—— 被改名或删除'
                   % (t['key'], t['match']))
    else:
        r.ok('A0', '7 条看板自动化全部在库（%s）'
             % ', '.join('%s=%d字' % (t['key'], len(t['prompt'])) for t in T))

    if a.dump:
        os.makedirs(DUMP_DIR, exist_ok=True)
        for t in T:
            fn = os.path.join(DUMP_DIR, '%s_%s.md' % (t['key'], re.sub(r'[^\w]', '_', t['name'])[:30]))
            open(fn, 'w', encoding='utf-8').write(t['prompt'])
        print('已导出 %d 份到 %s' % (len(T), DUMP_DIR))

    only = a.only.upper()
    def run(tag, fn):
        if only and tag not in only:
            return
        fn()

    run('A', lambda: chk_meta(T, r))
    run('B', lambda: chk_deadlink(T, r))
    run('B', lambda: chk_baseline(T, r))
    run('B', lambda: chk_fields(T, r, a.verbose))
    run('B', lambda: chk_cdconv(T, r, a.verbose))
    run('D', lambda: chk_steps(T, r))
    run('E', lambda: chk_coverage(T, r, a.verbose))
    run('F', lambda: chk_stamp_contract(T, r))
    run('G', lambda: chk_dup(T, r, a.verbose))

    sys.exit(r.dump())


if __name__ == '__main__':
    main()

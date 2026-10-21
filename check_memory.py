#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记忆体检 M0–M8（只读，不改任何文件）

用法：python3 check_memory.py          # exit 1 = 有 FAIL
      python3 check_memory.py --quiet  # 只打 WARN/FAIL 与汇总

对应清单：.workbuddy/memory/MEMORY-CHECKLIST.md（M0–M8）。
为什么需要它：记忆坏了**不报错**，只会安静返回错答案；靠人看「有没有问题」等于没查。
每条判据都来自一次真实事故（见清单里的「实测」段）。
"""
import os, re, sys, glob, datetime, json

HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(HERE, ".workbuddy/memory")
PM = os.path.join(MEM, "PROJECT-MEMORY.md")
MM = os.path.join(MEM, "MEMORY.md")
MC = os.path.join(MEM, "MEMORY-CHECKLIST.md")
CL = os.path.join(HERE, "CHECKLIST.md")
TODAY = datetime.date.today()

rows = []
def ok(m): rows.append(("PASS", m))
def warn(m): rows.append(("WARN", m))
def fail(m): rows.append(("FAIL", m))

def rd(p):
    return open(p, encoding="utf-8").read()

# ---------------- M0
for p in (PM, MM, MC, CL):
    if not os.path.exists(p):
        fail("M0 缺文件 %s" % os.path.basename(p)); continue
    try:
        s = rd(p)
    except Exception as e:
        fail("M0 %s 非 UTF-8：%s" % (os.path.basename(p), e)); continue
    f = s.count("\n```")
    tail_ok = s.endswith("\n") or re.search(r"[。！？)\]\n]?$", s)
    if f % 2:
        fail("M0 %s 代码围栏 %d 个（奇数，未闭合）" % (os.path.basename(p), f))
    elif not tail_ok:
        fail("M0 %s 末行疑似半句：%r" % (os.path.basename(p), s[-40:]))
else:
    ok("M0 四份记忆/清单文件齐备、UTF-8 可解码、围栏平衡、末行完整")

# ---------------- M1
# R100z170：告警线维持 300B（8192 才是硬限）。真正的护栏不是「余量多少」，
# 而是 M9 的搬运台账 —— 余量再紧，只要「搬过就留痕」就不会出现「压缩即丢失」。
sz = os.path.getsize(MM)
if sz > 8192:
    fail("M1 MEMORY.md %d 字节 > 8192 硬限（会被静默截断）—— 按 24.19 协议**先搬运再删**" % sz)
elif sz > 8192 - 300:
    warn("M1 MEMORY.md %d 字节 / 8192，余量仅 %dB —— 下次改动前先把可下沉的段落搬去 PROJECT-MEMORY 并登记 24.19"
         % (sz, 8192 - sz))
else:
    ok("M1 MEMORY.md %d 字节 / 8192（余 %dB）" % (sz, 8192 - sz))

# ---------------- M2 路由可达
pm = rd(PM)
heads = set()
for m in re.finditer(r"(?m)^#{2,4}\s*(\d+\.\d+[a-z]?)(?![0-9A-Za-z])", pm):
    heads.add(m.group(1))
cnsec = set(re.findall(r"(?m)^##\s*([〇一二三四五六七八九十]+)、", pm))
mm = rd(MM)
miss21 = []
for m in re.finditer(r"\b(2[0-9]\.\d+[a-z]?)(?![0-9A-Za-z])", mm):
    k = m.group(1)
    if k not in heads:
        miss21.append(k)
miss21 = sorted(set(miss21))
if miss21:
    fail("M2 MEMORY.md 引用的 24.x 节号无对应标题：%s" % ", ".join(miss21))
else:
    ok("M2 MEMORY.md 引用的 24.x 全部命中 PROJECT-MEMORY 标题（%d 个）" % len(heads))

# 中文章节路由
misscn = [k for k in set(re.findall(r"([〇一二三四五六七八九十]+)节", mm)) if k not in cnsec]
if misscn:
    warn("M2 中文章节引用无对应标题：%s" % ", ".join(misscn))
else:
    ok("M2 中文章节引用（%d 个）全部命中" % len(cnsec))

# ---------------- M3 现行 vs 历史混排
MARK = ("已作废", "原版", "改动前", "勿再据", "已被推翻", "已废除", "已下线", "旧说法", "现行")
pmL = pm.split("\n")
no_mark = []
for i, ln in enumerate(pmL, 1):
    if re.search(r"(原版是|原来是|此前是|曾写|早期写)", ln) and not any(w in ln for w in MARK):
        no_mark.append("PROJECT-MEMORY L%d" % i)
# 已知被推翻的说法必须 0 残留（每轮迭代补一条）
DEAD = ["涨停池仍东财", "中确信（应写中等确信）"]
dead_hit = [w for w in DEAD if w in pm]
if dead_hit:
    fail("M3 已被推翻的说法仍在记忆里：%s" % dead_hit)
elif no_mark:
    for s in no_mark[:6]:
        warn("M3 %s 出现「原版/此前是」但同行无失效标记（读到时分不清哪版现行）" % s)
else:
    ok("M3 演进记录均带失效标记（%d 处标记词），已知被推翻说法 0 残留"
       % sum(pm.count(w) for w in MARK))

# ---------------- M4 假未来
stale = []
for name, txt in (("MEMORY.md", mm), ("PROJECT-MEMORY.md", pm)):
    for m in re.finditer(r"(届时|下一个|下次|待验证|等它跑完)", txt):
        w = txt[max(0, m.start() - 30): m.start()]
        for d in re.findall(r"(20\d\d-\d\d-\d\d)", w):
            if d < TODAY.isoformat():
                ln = txt[:m.start()].count("\n") + 1
                if "已过期" not in txt[m.start():m.start() + 80]:
                    stale.append("%s L%d「%s」前有已过日期 %s" % (name, ln, m.group(1), d))
if stale:
    for s in stale[:8]:
        warn("M4 %s" % s)
    warn("M4 共 %d 处过期未打标（不删原文，行尾补「⏳ 已过期」）" % len(stale))
else:
    ok("M4 无「届时/下一个」+ 已过日期 的假未来陈述")

# ---------------- M5 日志积压
logs = sorted(glob.glob(os.path.join(MEM, "20*.md")))
today_log = os.path.join(MEM, "%s.md" % TODAY.isoformat())
old = []
for f in logs:
    b = os.path.basename(f)[:-3]
    try:
        d = datetime.date.fromisoformat(b)
    except Exception:
        continue
    if (TODAY - d).days > 30:
        old.append((b, (TODAY - d).days))
if not os.path.exists(today_log):
    warn("M5 缺当日日志 %s（本轮工作需追一条）" % os.path.basename(today_log))
else:
    ok("M5 当日日志在位 %s（%d 字节）" % (os.path.basename(today_log), os.path.getsize(today_log)))
if old:
    warn("M5 >30 天日志 %d 份未蒸馏：%s" % (len(old), ", ".join("%s(%d天)" % x for x in old[:5])))
else:
    ok("M5 无 >30 天未蒸馏日志（共 %d 份，最老 %s）"
       % (len(logs), os.path.basename(logs[0])[:-3] if logs else "-"))
# 蒸馏执行者
exec_hit = []
for p in glob.glob(os.path.join(HERE, "*.py")) + glob.glob(os.path.join(HERE, "*.sh")):
    try:
        s = rd(p)
    except Exception:
        continue
    if "memory" in s and re.search(r"glob|listdir", s):
        exec_hit.append(os.path.basename(p))
if exec_hit:
    ok("M5 蒸馏规则有执行者：%s" % ", ".join(exec_hit))
else:
    warn("M5 蒸馏规则无执行者（grep 无脚本读日志目录）—— 属空转型，越线那天没人提醒")

# ---------------- M6 automation memory
am = glob.glob(os.path.join(MEM, "automations", "*", "memory.md"))
badb, stale6 = [], []
for f in am:
    try:
        s = rd(f)
    except Exception:
        continue
    if "快照" not in s:
        badb.append(os.path.dirname(f).split("/")[-1])
    m = os.path.getmtime(f)
    if (datetime.datetime.now() - datetime.datetime.fromtimestamp(m)).days > 14:
        stale6.append(os.path.basename(os.path.dirname(f)))
if badb:
    warn("M6 缺快照横幅：%s" % badb)
elif am:
    ok("M6 运行期记忆 %d 份横幅齐全、无陈旧（%d 处 >14 天）" % (len(am), len(stale6)))
else:
    warn("M6 未找到运行期记忆目录")

# ---------------- M7 归档索引第 1 列
idx = os.path.join(MEM, "归档索引.md")
if os.path.exists(idx):
    s = rd(idx)
    col1 = []
    for ln in s.split("\n"):
        if ln.strip().startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) >= 2:
                c = cells[0].strip("`").strip()
                if re.match(r"归档-[^\s]+\.md$", c):
                    col1.append(c)
    on_disk = [os.path.basename(x) for x in glob.glob(os.path.join(MEM, "归档-*.md"))]
    miss = [c for c in col1 if not os.path.exists(os.path.join(MEM, c))]
    extra = [c for c in on_disk if c not in col1]
    if miss or extra:
        fail("M7 索引第1列 ↔ 实际文件不一致：缺 %d 条、多 %d 条（%s）"
             % (len(miss), len(extra), (miss + extra)[:4]))
    else:
        ok("M7 归档索引第1列 %d 条 ↔ 实际文件 %d 条，双向 0 差异" % (len(col1), len(on_disk)))
else:
    warn("M7 缺归档索引.md")

# ---------------- M8 数字对账
import importlib.util
spec = importlib.util.spec_from_file_location("ap", os.path.join(HERE, "audit_pipeline.py"))
ap = importlib.util.module_from_spec(spec); spec.loader.exec_module(ap)
raw = rd(os.path.join(HERE, "data.js"))
D = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
checks = ap.collect_g16_checks(D)
numeric = [(l, fl) for l, _t, fl in checks if fl]
badn = []
for label, floor in numeric:
    key = re.sub(r"\[\d+\]", "[]", label)
    # 记忆里必须出现该数字（就近 200 字内有 floor 与字段关键词）
    found = False
    for base in (mm, pm):
        for m in re.finditer(re.escape(str(floor)), base):
            # 关键词可能在数字**前**（"地板 150"）也可能在**后**（"加 150 地板"）——
            # 只往前找会漏，M8 首版就因此误报过 us.summary=150。
            ctx = base[max(0, m.start() - 60): m.start()] + base[m.end(): m.end() + 30]
            if re.search(r"(地板|下限|≥|低于即)", ctx):
                found = True
                break
        if found:
            break
    if not found:
        badn.append("%s=%d" % (key, floor))
if badn:
    warn("M8 地板数字在记忆里找不到出处：%s（数字是断言，越无出处越容易过期）" % ", ".join(badn[:6]))
else:
    ok("M8 G16 地板 %d 项在记忆里都能找到数字出处" % len(numeric))

# ---------------- M9 压缩搬运台账（R100z170）
# 事故（2026-10-21，用户当场质问）：「之前记忆被截断每次你压缩都把之前有用的给压缩没了，
# 后面你不是想出办法解决了吗？怎么现在又开始截断然后压缩了？」
# M1 只保证「不超 8192」，M2 只保证「节号不悬空」——两者都拦不住「压缩时把内容删了」：
# 删掉一个短句，节号还在，M2 照样 PASS，但事实已经没了。
# 所以要求每次压缩必须在 PROJECT-MEMORY 的 24.19 台账里登记一行：
#   | 2026-10-21 | 8256 | 7888 | 搬走了什么 → 落在哪一节 |
# 判据：当前体积明显小于台账最后一条的 after = 又偷偷压缩了 → FAIL。
LEDGER = os.path.join(PM)
rows19 = []
if os.path.exists(PM):
    s19 = rd(PM)
    # 取到下一个**二级**标题为止（节内的 ### 小标题不能截断，台账表在下层小标题里）
    m19 = re.search(r"(?m)^#{2,4}\s*24\.19[^\n]*\n([\s\S]{0,6000}?)(?=\n##\s|\Z)", s19)
    if not m19:
        fail("M9 缺 24.19「MEMORY.md 压缩搬运台账」—— 压缩无记录，等同于允许静默丢内容")
    else:
        for ln in m19.group(1).split("\n"):
            c = [x.strip() for x in ln.strip().strip("|").split("|")]
            if len(c) >= 4 and re.match(r"20\d\d-\d\d-\d\d$", c[0]):
                try:
                    rows19.append((c[0], int(c[1]), int(c[2]), c[3]))
                except Exception:
                    pass
        if not rows19:
            fail("M9 台账 24.19 在，但没有任何可解析的记录行（格式：| 日期 | 前 | 后 | 搬走什么→哪节 |）")
        else:
            d0, b0, a0, _w = rows19[-1]
            sz9 = os.path.getsize(MM)
            if sz9 < a0 - 40:
                fail("M9 MEMORY.md 已缩到 %d 字节，但台账最后一条（%s）记的是 %d —— "
                     "又压缩了却没登记「搬走了什么」，先补台账再动文件" % (sz9, d0, a0))
            else:
                ok("M9 压缩搬运台账在位（%d 条，最新 %s：%d→%d），当前 %d 字节未越界"
                   % (len(rows19), d0, b0, a0, sz9))

# ---------------- M10 目标线双线（R100z171，2026-10-21）
# 用户问「记忆有什么办法解决吗」时实测：MEMORY.md 7969B / 8192B，M1 仍 PASS、只剩 223B 余量。
# 问题在于 M1 只判**硬限**——撞上就静默截断，而撞之前一直绿；等到绿变红时已经截断了。
# 历史设计目标是 6.4KB（24.11 验收记录：「19,929 → ≈6.4KB」），后来一路回涨到 7.9KB。
# 所以补一条**目标线**：>7600 = 已进危险区（FAIL，必须立刻下沉）；>6600 = 该下沉了（WARN）；否则 OK。
# 注意 M10 是「建议线」，M1 仍是「硬限」，两者判的不是同一件事，不要合并。
sz10 = os.path.getsize(MM)
if sz10 > 7600:
    fail("M10 MEMORY.md %d 字节 > 7600 危险线 —— 注入层已回涨（设计目标 6.4KB，24.11）。"
         "立刻把详情下沉到 PROJECT-MEMORY 并登记 24.19 台账，别等撞 8192 被静默截断" % sz10)
elif sz10 > 6600:
    warn("M10 MEMORY.md %d 字节 > 6600 目标线（设计值 6.4KB）—— 该下沉了，协议见 24.19/24.21"
         % sz10)
else:
    ok("M10 MEMORY.md %d 字节 ≤ 6600 目标线（到目标余 %dB / 到硬限余 %dB）"
       % (sz10, 6600 - sz10, 8192 - sz10))

# 结论：最该删哪段
print("=" * 78)
QUIET = "--quiet" in sys.argv
for lv, m in rows:
    if QUIET and lv == "PASS":
        continue
    print("[%-4s] %s" % (lv, m))
print("=" * 78)
p = sum(1 for l, _ in rows if l == "PASS")
w = sum(1 for l, _ in rows if l == "WARN")
f = sum(1 for l, _ in rows if l == "FAIL")
print("记忆体检：PASS=%d WARN=%d FAIL=%d" % (p, w, f))
sys.exit(1 if f else 0)

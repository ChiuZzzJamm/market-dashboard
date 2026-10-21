# 全链路检查清单（L0–L17）

> **这份清单的存在原因**：用户每次的指令都是「7 个自动化 + 全链路所有问题」，范围从没缩过。
> 我反复把「全链路」私自替换成「读一遍代码」，再用「仔细检查」把缩水申报成完成。
> **没有清单时，「仔细」= 随缘。有清单时，才能说「跑了 N 层、每层判据是什么、剩几条 WARN」。**
>
> 用法：从 L0 往上逐层跑，**前一层不绿不要往下走**（上层失败会让下层结论全是假的）。
> 每层跑完记一行：`L<n> PASS/WARN×m/FAIL`，不许只写「检查过了」。
> **记忆层L6 展开为独立清单**：`.workbuddy/memory/MEMORY-CHECKLIST.md`（M0–M8），记忆相关检查一律走那份。

---

## L0 语法与可加载性（30 秒，必须先跑）

| 查什么 | 命令 | 通过标准 |
|---|---|---|
| Python 全部可编译 | `python3 -m compileall -q . && echo OK` | 无 SyntaxError |
| data.js 可解析 | `node --check data.js` | OK |
| JSON 可解析 | `for f in *.json; do python3 -m json.tool "$f" >/dev/null \|\| echo "BAD $f"; done` | 无 BAD |
| 闸门自身能跑 | `python3 audit_pipeline.py; echo rc=$?` | rc=0 且 FAIL=0 |

**易漏**：`__pycache__` 里的旧 .pyc 会掩盖改名后的 ImportError；`git status` 有未提交改动时，跑的是工作区版本而非部署版本。

---

## L1 存在性（死链 / 死步骤）

| 查什么 | 怎么查 |
|---|---|
| prompt 提到的每个 `.py` 是否真实存在 | 抽出所有 `\w+\.py`，逐个 `test -f` |
| `references/*.md` 技能文档是否在位 | 逐个 `test -f` |
| 运行时临时文件（`duanban_am.json` 等） | 只需确认**生产者**在同一份 prompt 内，文件本身不存在是正常的 |
| 「第 N 步」是否有定义 | 抽出所有「第 N 步 / N)」引用，与步骤定义集比对 |
| 命令 flag 是否被脚本支持 | 只看**代码块/行内 code** 里的命令行，按 `&& \|\| ; \|` 切段，再 grep flag 字面量 |

**易漏**：正则贪婪会把相邻两行命令混成一条 → 误判「参数不支持」。排版变体（`- **3.4)`、缩进 `6)`、`> 10)`）会被误判「步骤无定义」，要按变体集匹配。

---

## L2 分叉对账（四方口径）★ 真 bug 的主产地

**同一件事在四个地方各写了一遍，只要有一处没跟上就是 bug。四方 = prompt / 代码 / 闸门 / 记忆。**

| 对账对 | 典型冲突 |
|---|---|
| prompt 写的字数区间 ↔ G16 地板 | prompt 写 220-420，地板 240 → 照写必 FAIL |
| prompt 写的选股规则 ↔ `check_stock_pool` 判据 | prompt 说「8 只照抄」，代码只约束断板槽位 |
| prompt 写的阈值 ↔ 校验脚本阈值 | prompt 说 `<40 字 FAIL`，G16 说 `<200` |
| 闸门判据 ↔ 记忆里记的判据 | 记忆记「池外 ≥2 只即拦」，实际只拦断板槽位 |
| 代码常量 ↔ 前端文案 | 枚举值改了，`quadZh()` 没加分支 |
| 板块名集 ↔ 池键集 | G26 专管（`bySector` ↔ `aiPrediction.sectors`） |

**判据：改一个阈值 = 全库 grep 该数字的所有表述形式**
`<40 字` / `40 字 FAIL` / `110-160` / `220-420` / `≥2 只` … 半角全角、空格、`-`/`~` 都要试。**代码与 prompt 必须同一批改完，只改代码不算改完。**

---

## L3 空转（规则真的会跑到吗）

对**每一条**硬约束，做三步：

1. **找执行点**：这条规则在哪个函数？`grep -n` 到具体行。
2. **查读取路径**：它读的节点路径和数据里的真实路径一致吗？（吸筹地板读错节点 → 恒不触发）
3. **查前置换算**：执行点前面有没有一条默认走通的降级分支？（「池里找不到键 → 只 WARN 点名即可」把断板约束整轮绕开）

**还有一类更隐蔽的短路**：`return` 提前返回把后面的检查整段跳过。找到每处 `return`，问一句「它今天会被走到吗」。

**判据：如果一条规则从来没有 FAIL 过，先假设它坏了，而不是假设数据质量好。**

---

## L4 未撞（让数据真流过）

静态读代码对这类 bug 检出率 0。必须换参数重跑：

| 查什么 | 怎么造 |
|---|---|
| 跨池去重 | 把 6 个板块**一次性**重键，看同一只票是否以两种身份进同一个 8 只 |
| 字段缺价 | 选一只只存在于「无价格字段」池里的票，看 stop/target 是否 `None` |
| 边界输入 | 候选 0 只 / 1 只 / 满额三种情况各跑一次 |
| 新板块名 | 造一个四池都没有候选的板块名，看是报错还是静默 0 只 |

**判据：「没撞上」≠「没有 bug」。风险 = 缺陷 × 触发概率，降概率不等于修缺陷。**

---

## L5 排程与运行元数据

| 查什么 | 怎么查 |
|---|---|
| `cwds` 是合法 JSON 数组 | `json.loads(cwds)` 断言，别看 `view` 的渲染结果 |
| `next_run_at` 是否指向正确时刻 | 毫秒时间戳换算成本地时间，逐条对 |
| `rrule` 与业务日历一致 | 07:00 美股 `BYDAY=TU,WE,TH,FR`（**不含 SA**，周五由周日 22:00 抓） |
| `status` | 全部 ACTIVE，没有意外 PAUSED |
| prompt 体量 | 记录字符数，突变说明被误改 |

**易漏**：`automation_update(mode=update)` 传 `cwds` 数组会被再包一层 → 传裸路径字符串，或走 `sqlite3` 直改。改完必须 `json.loads` 复验。

---

## L6 记忆层（不许截断）

> **详细判据已独立成 `.workbuddy/memory/MEMORY-CHECKLIST.md`（M0–M8）。**
> 原因：记忆层的失效方式与代码层完全不同——**代码坏了会报错，记忆坏了只会安静地返回错答案**，
> 然后我拿它去写代码。L6 这 5 条只是体检项，查不出「读不到」「读到过期的」这两类。
> **接「记忆检查 / 记忆优化」→ 直接开 M0–M8，别只报体积。**
> **一条命令跑完 M0–M8**：`python3 check_memory.py`（`--quiet` 只打 WARN/FAIL + 汇总；
> exit 1 = 有 FAIL）。已挂在 `deploy.sh` 审计闸门之后、**不阻断部署**——理由同 L12：
> 它查的是记忆文件，不该把当天行情部署一起拖死。

| 查什么 | 通过标准 |
|---|---|
| `MEMORY.md` 体量 | **≤8,192 字节**（G25 每次部署实测）。它是注入层，超了会被静默截断 → 「记忆里明明有、动手时想不起来」 |
| **路由指向的节真能 grep 到** | **G27 硬查**（FAIL）。`24.11` 写在路由里、标题却是「二十四·十一」→ 该节等于不存在 |
| 路由表在位 | 每一条硬约束都能找到「要动 X → 先读第 N 节」 |
| 代码围栏平衡 | `grep -c '^```'` 必须是偶数 |
| `PROJECT-MEMORY.md` 结构 | 无重复章节标题；追加后 `wc -c` 确认真的落盘 |
| **过期「届时」** | **G27 查**（WARN）。旧条目的「下一个/届时」永远停在未来时态 |
| 运行期记忆 | `.workbuddy/memory/automations/<id>/memory.md` 都有「PASS=N 是快照非基线」横幅 |

**判据：`MEMORY.md` 只放路由与硬约束，结论一律写 `PROJECT-MEMORY.md`。加一行必须删一行。**

---

## L7 前后端契约

| 查什么 | 怎么查 | 通过标准 |
|---|---|---|
| data.js 顶层节点有归属 | `PRODUCT_OWNERSHIP`（audit_pipeline.py:144）已登记 | G12 查孤儿键 |
| **节点有读取方** | 每个顶层键在 `index.html` 里的引用次数 | >0 通过；**0 次时必须查 `proof`** |
| 生产者存在 | 该键在哪个 `.py` 里被写入 | 有写方且被 prompt 点名 |

**⚠️ 「前端 0 引用」不等于 bug（2026-10-21 实测踩过两次误报）**：
`fullScan`(13.8KB) 与 `weekendNews`(0.1KB) 都是 0 引用，但 `PRODUCT_OWNERSHIP` 登记 `proof="intermediate"`——
前者读方是 `harmonic_detect`/`accumulation_score`，后者读方是 `push_notify`/`validate_stocks`，**本来就不进前端**。

正确判据：
```
前端引用 0 次
  → 查 PRODUCT_OWNERSHIP[key].proof
     ├─ "intermediate" 且 note 里能点名具体读方脚本 → PASS
     └─ 其他 / note 只写「中间产物」不点名     → FAIL（说不清谁读，就是漏渲染）
```

**反向误报也要防**：别拿 `D.xxx`/`data.xxx` 正则去比对顶层键——前端大量用的是**局部变量**（`const us = D.us` 之后的 `us.bankViews`），
会把 60 个嵌套字段误判成「data.js 缺失」。只认 `DASHBOARD_DATA.xxx` 这种明确顶层引用。

**排序改动的硬约束**：改渲染层排序**必须同步「按下标回取数据」的导航函数**（如 `openFailIdx`），否则弹窗串票。

**只出中文**：`price-in`/`beat`/`miss`/`[E]` 等一律译；确信度用「中等确信」全称。

---

## L8 数据源与降级

| 查什么 |
|---|
| 主源 / 兜底源是否与记忆一致（断板涨停池主源 = 同花顺 `limit_up_pool`，东财仅兜底） |
| 抓不到 ≠ 没有：失败必须**显式降级并打标**，不许静默写空 |
| 拿不到响应体**不写缓存**（`stockNews` 尤其） |
| 盘面统计类标题（`\d+只股`/`附股`/`连板股`）必须丢弃，不得当个股消息 |

---

## L9 展示规范（用户硬偏好）

`price-in`→已计价｜`beat`→超预期｜`in-line`→符合预期｜`miss`→不及预期｜`[E]`→预期层
涨 `#f0435c`（红）/ 跌 `#0f9d78`（绿）—— **A 股惯例，别按欧美反着来**
A 股只许 60/00 主板（禁 688/689、300/301/302、4/8/92）

---

## L10 幂等与重复执行

| 查什么 |
|---|
| 同一命令跑两次结果一致吗（`--sectors` 重键、`annotate_pool_meta`） |
| 重跑会不会重复追加（日志、`impacts`、`coverage` 数组） |
| 中途失败会不会留下半个文件（写盘一律先写临时文件再 rename） |

---

## L11 备份与回滚

| 改什么 | 先备份什么 |
|---|---|
| prompt / 自动化元数据 | `cp ~/.workbuddy/workbuddy.db /tmp/workbuddy.db.bak-$(date +%s)` |
| `stock_pool.json` | `cp stock_pool.json /tmp/stock_pool.json.bak-$(date +%H%M%S)` |
| `data.js` 大文件 | 整份读改写**必须走 Python `json.loads`**，禁 node eval（否则 25 万字符格式 churn） |
| 部署 | `./deploy.sh`（用 `.deploy_key`）；`SKIP_AUDIT=1` 仅休市分支可用 |

---

## L12 自动化体检（一条命令，别再用眼睛看）

```bash
python3 check_automations.py            # 六类 33 项，exit 1 = 有 FAIL
python3 check_automations.py --only B   # 只跑死链 + 基线漂移
python3 check_automations.py --dump     # 导出 7 份 prompt 到 /tmp/ap/ 供并排读
```

| 类 | 查什么 | 判据 |
|---|---|---|
| A | cwds 是合法 JSON 数组且目录存在 / rrule 与设计一致 / status 为 ACTIVE | `A0/A1/A2/A3` 全 PASS |
| B1 | prompt 提到的每个 `.py`/`.sh` 真实存在（**按整行**判否定语境，代码块内一律必查） | 无 FAIL |
| B2 | `references/*.md` 跨「项目 → 用户 skill → 插件缓存」三级解析后全部在位 | 无 FAIL |
| B3 | 命令里每个 `--flag` 都被对应脚本 `--help` 支持 | 无 FAIL |
| B4 | `deploy.sh` 命令链引用的脚本全部存在 | 无 FAIL |
| **B5** | **`audit_expectations.json` 的 mustContain / forbidden 与线上 prompt 逐字比对** | **无 FAIL**（这条是 G4 的盲区，见 L13） |
| D1 | 「第 N 步」引用都有定义（变体集 `N)` / `- **N)` / `N.` / `N、`） | 无 FAIL |
| E1 | G16 会判的字段，负责写它的 prompt 都写明了字数区间 | 无 FAIL |
| E2 | prompt 写的字数下限 ≥ `collect_g16_checks` 的真实地板 | 无 FAIL |
| F1 | 7 条任务的时间戳契约齐全（顶层 `updatedAt` 单一主人 = `refresh_stamp.py`） | 无 FAIL |
| G1 | 单份内部重复长行（命令行除外） | 无 WARN |

**它已挂在 `deploy.sh` 的审计闸门之后，但故意 `|| true` 不阻断**：它查的是 prompt/排程，不是 data.js 内容；哪天它自己判错，不该把当天行情部署一起拖死。要当闸门就手工跑。

---

## L13 基线漂移（G4 只保证基线「自洽」，不保证基线「是真的」）

**踩过的坑（R100z169 实测）**：`audit_expectations.json` 里 6 份基线共 207 条 `mustContain`，
G4 只校验「每条自动化都登记了短语」——**从不拿基线去比线上 prompt**。
于是 W2200 的 `ashare 全部字段不变` 与线上实际措辞 `ashare 全部字段未被改动` 脱节，
从 2026-10-19 起二十多天无人发现，基线退化成「一份没人对账的愿望清单」，
G2 承诺的「prompt 漂移可发现」实际是空话。

**判据**：改了任何 prompt 之后，`python3 check_automations.py --only B` 必须 `B5 PASS`（207 条逐字命中）。
命中不了只有两种处理，**不许放着**：
- 规则真的被删/改弱了 → 补回 prompt；
- 只是措辞变了 → 同步改基线短语。

---

## L14 指标方向（别把 by-design 当 bug，也别让它反向激励）

判断「冗余」必须先问一句：**这个重复删得掉吗？**

- 7 条自动化各自在**独立上下文**运行，B 看不到 A 的 prompt → 共用契约（新闻白名单 / 时间戳归属 /
  简称 ST / 字数纪律）**必须在每份里各存一份**，删了就是让某条任务失明。这是**独占，不是冗余**。
- 反例（指标骗人的实证）：把 `cd <项目> && ` 前缀统一去掉后，命令行跨份变**一致**，
  重复率从 9.7% 涨到 14.4% —— 而总字数其实降了 3.4%。**用「重复率」当 KPI 会奖励错误动作。**
- 正确口径：**单份内部**的同句重复才是真啰嗦（G1 只报这个，命令行除外——同一命令在不同步骤
  各跑一次是故意的幂等重跑）；跨份重复只输出数字，不报警。看体积看**总字数趋势**，不看重复率。

---

## L15 瘦身/改 prompt 后的等价性验证（四步，缺一步就是没验）

| # | 判据 | 命令 |
|---|---|---|
| 1 | 207 条 mustContain / forbidden 逐字仍在 | `python3 check_automations.py --only B` → `B5 PASS` |
| 2 | G16 叙事地板没被顺手调低 | 读 `audit_pipeline.collect_g16_checks`，确认无字段地板变小 |
| 3 | 命令在**新表述下仍可执行**（cwd / 解释器 / flag 没写坏） | `B3` 全 PASS + 抽一条命令手工跑 |
| 4 | 双闸 FAIL=0 且 `exit 0` | `python3 audit_pipeline.py; echo rc=$?` |

**改前先 `cp ~/.workbuddy/workbuddy.db /tmp/workbuddy.db.bak-<ts>`**（见 L11），
改后**必须重新从库里读出来复验**——`sqlite3` 的 `update` 报成功不等于落了你想落的内容。

---

## L16 视觉与观感（闸门看不见的那一层）

**为什么单列一层**：`audit_pipeline` 的每条 gate 都是「可编程判据」——字段在不在、class 对不对、数值合不合法。
**审美不在判据里**。R100z170 我把风险徽章三档从 ⚠️ 改成 `⚠️ / ！ / ？` 加彩底，51 条 gate 全绿，
用户一眼「好丑啊」。这不是逻辑错，是**验收维度缺失**（详见 `PROJECT-MEMORY.md` 24.21）。

| 什么时候必须走这一层 | 动作 |
|---|---|
| 改了 `index.html` 的 CSS / 渲染函数 / 徽章 / 颜色 / 字号 | ① 截图 ② 核对 DOM ③ 回答下面三问 |
| 改了文案模板（含「—」占位、单位、中文映射） | 截一张带该字段的真实卡片 |
| 只是改 Python 数据生成、不碰展示 | 可跳过，但要在汇报里写「L16 未覆盖：未改展示层」 |

**① 截图（本机 headless Chromium，无需装包）**
```bash
HS="$HOME/Library/Caches/ms-playwright/chromium_headless_shell-1246/chrome-headless-shell-mac-arm64/chrome-headless-shell"
"$HS" --disable-gpu --no-sandbox --no-zygote --allow-file-access-from-files \
      --virtual-time-budget=7000 --window-size=1400,1700 \
      --screenshot=/tmp/shot.png "file:///Users/loccco/WorkBuddy/market dashboard/index.html"
```
> ⚠️ `--allow-file-access-from-files` 不能省：`file://` 下 XHR 被 CORS 拦，
> `risk_blacklist.json` / `portfolio_curve.json` 拉不到 → 徽章和净值曲线**根本不渲染**，
> 截出来是一张「假绿」的图。

**② 核对 DOM（查「有没有」比看图更可靠）**
```bash
"$HS" --disable-gpu --no-sandbox --allow-file-access-from-files --virtual-time-budget=7000 \
      --dump-dom "file:///Users/loccco/WorkBuddy/market dashboard/index.html" > /tmp/dom.html
grep -o 'class="risk-dot sev-[a-z]*"' /tmp/dom.html | sort | uniq -c   # 三档各渲染了几处
```

**③ 三问（答不上就是没验）**
1. **小尺寸下还分得出吗？** 颜色深浅/字重/边框这类「弱信号」，在真实字号（10–11px）下会不会糊成一样？
   —— 本轮实测：10px 胶囊上 `#141414` 与 `#6e6e6e` 肉眼不可分，配色等于白做。
2. **新符号/新颜色和既有语义打架吗？** 橙黄圆点 vs A 股涨红跌绿；`！` vs 页面别处已有的感叹号徽章。
3. **我看的是真图，还是我以为的图？** 没截图 = 没看过。

**G30 已经机器化的部分**（`audit_pipeline.py`）：徽章符号统一 ⚠️、三档底色亮度单调且跨度 ≥35、
徽章字号 ≥11px、涨红跌绿常量在、图例三档齐、**`.risk-tip` 长条徽章三档 + 字色 WCAG 对比度 ≥4.5:1**。
**G30 覆盖不到、只能靠眼睛的**：留白与密度、字体层级、长标题换行、弹窗对齐、暗色语境下的可读性。

---

## L17 部署后线上复验（`deploy.sh` 成功 ≠ 上线成功）

**为什么单列一层**：R100z172（2026-10-21 23:11）`./deploy.sh` 报「✅ 推送成功」，远端 `git ls-remote` 也是新 commit，
但**线上 13 分钟仍是旧版**——GitHub Pages 没有自动重建。不 curl 就永远发现不了（刷新页面看到旧样式会以为是自己没改对）。

| 步骤 | 命令 | 判据 |
|---|---|---|
| ① 远端确实收到 | `git ls-remote origin \| head -1` | HEAD = 本地 HEAD |
| ② 线上抓到**本轮独有标记** | `curl -s "$URL/index.html?nc=$(date +%s)" \| grep -c R100z172` | ≥1 |
| ③ 超过 5 分钟还是旧 → 区分「缓存」还是「没重建」 | 换**另一个本轮也改过的文件**（如 `audit_pipeline.py`）再 grep 独有字符串 | 两个都旧 = 整站没重建；只有 html 旧 = CDN 缓存，再等 |
| ④ 确认没重建 | `git commit --allow-empty -m "chore: 触发 Pages 重建"` + `.deploy_key` push | 2 分钟内生效 |

```bash
git commit --allow-empty -m "chore: 触发 GitHub Pages 重建"
GIT_SSH_COMMAND="ssh -i .deploy_key -o StrictHostKeyChecking=no -p 443" git push origin main
```

> ⚠️ 复验必须 grep **本轮独有**的字符串（注释里的 `R100z编号` 最省事）。
> 只比对「文件大小变了」不够——data.js 每天在变，大小变化不证明 CSS 上线了。

---

## 汇报格式（强制）

不许写「检查完了」「已仔细检查」。必须写：

```
跑了 L0–L17 共 18 层 + M0–M10（记忆）共 11 层
L0 PASS / L1 PASS / L2 修了 n 处 / L3 PASS / … / L16 PASS（视觉层：贴图 + 三问）/ L17 PASS（线上独有标记已上）
自动化体检：check_automations.py → FAIL=0（33 项，报「哪类几项」不报 PASS 总数）
闸门：FAIL=0 exit=0（WARN 条数 + 逐条性质，不写 PASS 数字——WARN 随时段浮动）
未覆盖：<哪一层没跑、为什么、风险是什么>
剩余 WARN：<逐条 + 性质（真信号 / 已知噪音 / 正常态）>
```

**判据一：给不出「未覆盖」的检查报告，等同于没检查。**
**判据二：闸门没做过故障注入 = 不知道它拦不拦得住。**（G27 首版就是这么发现漏判的）

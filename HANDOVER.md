# 工作交接文件 · R100z41（2026-10-04 02:15 起草 / 02:40 更新为已核实）

> **当前 Git HEAD：`f845f38`（R100z36）；工作区有 3 处未 commit 改动（index.html / validate_stocks.py / 本文件）。**
> 本文件供新会话接续。R100z41 那轮排查的结论已直接写进本文，不必重跑。

---

## 一、本次会话已完成的改动

### 1. 断板卡片四格（对应用户 image#5/#6，上一会话已改，此处确认）
`index.html` `dbCardHtml()`：四格 = **反包概率 / 缩量天数 / 板块涨幅 / 止损**。
板块涨幅取 `e.boardPctToday`（带 `bCls` 涨跌色），foot 里重复的「板块今日…」行已删。

### 2. 谐波卡片四格 → 距PRZ / 止损 / T1 / T2（对应用户 image#1~#4）
- 旧四格 = 形态 / 距PRZ / 止损 / T1（T2 在 foot）。**交接件上一版写「线上已是正确、无需改动」是错的，已改。**
- 新四格全部为可量化指标；**形态名（蟹形/蝙蝠…）下沉到 foot**，与 `hzPatCn()`、池归属、PRZ 区间同列。

### 3. 失效归档池「没更新」（对应用户 image#7）— ✅ 修完，真因是渲染顺序
**不是数据 bug。** `failPool` n=58，entryDate 分布 `2026-09-29:25 / 2026-09-30:33`，滚动窗口正常工作。
真因：`check_duanban.py` 的 failPool 保留 ≤5 交易日旧归档 + 当日新增（数组天然多日期），
而 `dbFailHtml` 按数组原序渲染 → **旧日期整批排在最前**，第一屏全是 09-29 的条目，看着像没更新。

**改动**（index.html）：
- 新增 `failPoolSorted()` / `failPoolLatest()`，按 `entryDate`（回退 `failHz.failDay`）**倒序**渲染；
- 最新批次加 `.fail-new`（实线暖色边框 + 淡底）+ 「新」徽章；标题补「最近归档 MM-DD」；
- ⚠️ **同时改了 `openFailIdx()`**——它从 data.js 原数组按下标取条目做弹窗左右导航，
  不同步改会**串票**。两处共用 `failPoolSorted()`。
- ⚠️ **不要去改 `check_duanban.py` 的 `carried`（1382~1384）**，那是滚动窗口设计，删了丢历史。

### 4. `validate_stocks.py`：`--scope star` 的 FAIL 闸门补实（此前恒空转）
- 根因：main() 的 `scope in ('all','ai')` / `('all','ai','us')` / `('all','us')` **三道分支都不含 `star`**
  → `violations` 恒为空，`check_risk_blacklist(D, scope='star')` 也不扫任何节点 → **永远 ALL OK**。
- 改：main() → `scope in ('all','ai','us','star')`；`check_risk_blacklist` 加 `all/star → duanban.star.picks`。
  理由：star 是 9:45 自己挑的、且禁止池外补股，不过闸等于这条链路没有硬风险拦截面。

### 5. 自动化 `automation-1788494481298`（07:30 美股）rrule 加 SA
- 原 `BYDAY=TU,WE,TH,FR` 漏了**周六**。北京周二~周五早 7:30 抓周一~周四美股，
  **周六早 7:30 才是抓周五美股的唯一窗口** → 周一、周五美股一周从未入库。
  佐证：`us.updatedAt` 停在 2026-10-02 07:31，而 10-03（周六）才是周五美股收盘日。
- 现 `BYDAY=TU,WE,TH,FR,SA`。周六必然 `aShareClosed=true`，第 0 步自动跳过 2.5/5.6/5.7，只跑美股+要闻+deploy。

### 6. 08:30 / 16:00 两个自动化的 `cwds`
此前 `cwds` 被写成**单个字符串、把方括号一起塞进字符串里**（形如 `"/Users/xxx[\"/Users/xxx\"]"`），
而不是合法的数组 `["/Users/xxx"]`。已修成合法数组。

---

## 二、核对结论（不用重跑）

| 项目 | 结论 |
|---|---|
| 冗余脚本 | **无孤儿**。26 个 `.py` 里 `audit_pipeline.py` 零引用（人工审测工具），其余 24 个均在至少一份自动化 prompt 中出现 |
| 校验死链 | 五份 prompt 引用的脚本路径、`--scope/--json/--no-fix/--date` 等 flag **全部真实存在** |
| 08:30 `market_bench.py --write` | 5.10 + 「绝对禁止」第 26 条双重禁掉，盘前不会误写 |
| 09:45 开盘精选 | 无 bug；只写 `duanban.star`，`--no-fix --scope star` 用法正确，`update_star.py` 已零东财依赖 |
| 周末 22:00 | 无 bug；第 0 步已写死「10-04 跑 → 下周一 10-05 仍在休市 → `NEXT_DAY=10-08`」，且第 88 行专门写了不写 `pos` 会断链 |
| 16:00 步骤序 | 5.5→5.6→5.7→5.75→5.8→5.9→5.10→5.11 顺序正确 |
| 07:30 文案漂移 | 第 1 步「周一允许比周五早 3 天」与第 5.6 步「本任务仅周二至周五运行」跟改后调度不一致，但**零行为影响**（周一不跑、周六被第 0 步拦）。**未改**：改 prompt 要整字段重写 19K 字符，手抄出错即生产事故 |

---

## 三、日历硬事实（2026-10-04 起）

```
10-01 ~ 10-07  国庆休市（10-02 是周五但 A 股也休市）
09-30          最近交易日
10-05 / 10-06 / 10-07  休市
10-08          下一个交易日，tags=['节后首日']，posText='6-7 成'
```
- `audit_pipeline.py` G10 按此分流 pos/star。
- 周日 22:00 任务今天跑时 `NEXT_DAY` 必须写 **2026-10-08**（不是自然「下周一」10-05），且必须写 `openOutlook.pos`，否则 10-08 的 16:00 `score_openoutlook.py` 只能 `[SKIP]`。

---

## 四、记忆现状（2026-10-04 补全）

- **项目记忆目录**：`/Users/loccco/WorkBuddy/2026-09-21-09-54-09/.workbuddy/memory/`
  - `MEMORY.md`（索引，注入 8KB 上限，只放硬约束）— **已加 R100z41 指针 + 10-08 日历硬事实**
  - `MEMORY-OPS.md` — 第七节四格口径**已更新**（断板/谐波都变了）；**新增第八节「R100z41：五自动化逐行审」**
  - `MEMORY-MODULES.md`（四模块细节）
  - 按日日志 append-only，细节真相在这
- **用户级** `~/.workbuddy/MEMORY.md` — 已补：记忆路由、五自动化调度坑（rrule 缺 SA / `--scope star` 空转 / cwds 字面量 / NEXT_DAY）、前端排序改动的连带坑
- ⚠️ **会话工作区与项目工作区是分离的**：项目记忆不在本项目工作区下。
  当前会话工作区 `/Users/loccco/WorkBuddy/2026-10-04-02-15-27/.workbuddy/memory/` 已补索引 `MEMORY.md` + 当日日志。

---

## 五、快速环境信息

- 项目目录：`/Users/loccco/WorkBuddy/2026-09-04-11-53-22/market-dashboard`
- 部署：`bash deploy.sh`（SSH over 443，`.deploy_key`）
- Python：`/Users/loccco/.workbuddy/binaries/python/versions/3.13.12/bin/python3`
- Node：`/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node`
- 审测：`python3 audit_pipeline.py` → 期望 **PASS=24 / WARN=0 / FAIL=0**
- 前端语法自检：抽 `<script>` 到临时文件跑 `node --check`
- 自动化 prompt 导出（省 context）：`~/.workbuddy/workbuddy.db` → 表 `automations.prompt`

---

## 六、新会话开场白

```
接着 market-dashboard 的活儿干。先读
/Users/loccco/WorkBuddy/2026-09-04-11-53-22/market-dashboard/HANDOVER.md，
那一节 R100z41 已核实过的内容不要重跑。

当前状态：Git HEAD f845f38，工作区有 3 处未 commit（index.html 谐波四格+失效池排序、
validate_stocks.py 的 scope star 闸门、HANDOVER.md）。
audit_pipeline.py 已跑过 PASS=24/WARN=0/FAIL=0，前端 JS 语法也过了。

优先级：
1. commit 这三处改动并 bash deploy.sh 上线。
2. 跑 python3 audit_pipeline.py 复核一次（改过 validate_stocks.py，G3 组会调它）。
3. 10-08 是节后首日：08:30 那次自动化要给 openOutlook 补写 10-08 前瞻（含 pos），
   别漏；周日 22:00 任务今天跑时 NEXT_DAY=2026-10-08。

注意三件事：
- 别用 hy3-c 模型，会报 11133（请求体超重 → 网关 400），用 kimi-k2.6。
- failPool 多 entryDate 属正常（滚动窗口），别当 bug 去删 carried。
- data.js / index.html 的「日期没更新」类告警，先拿第三节日历对照。
```

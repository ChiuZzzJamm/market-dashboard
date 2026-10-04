# HANDOVER · 交接件（已并入单一事实源，本文件只是指针 · R100z49，2026-10-04）

> 本文件原先是 market-dashboard 的工作交接件（R100z41/z42/z44 三轮排查结论都在这里）。
> **全部内容（连同 strategy skill 侧 / 8 个旧工作区的记忆）已于 2026-10-04 并入 👉
> `/Users/loccco/WorkBuddy/market dashboard/.workbuddy/memory/PROJECT-MEMORY.md`（13 节 / 25.1K 唯一事实源）**
> ⚠️ **家庭网络 / 路由器诊断侧记忆已从主记忆移除**（不随看板会话加载）。原件所在处（2026-10-04 11:35 看板记忆目录整平后）：
> `归档-家庭网络-2026-10-04.md`、`归档-原件-HANDOVER-家庭网络.md`、`归档-原件-家庭网络.md`（市场看板归档层），
> 以及 router 工作区 `router/.workbuddy/memory/MEMORY.md`（**唯一事实源**）。原先的 `_原始归档-20261004/D-家庭网络/` 与 `archive/旧日志/` 已随目录删除。
> **新会话：直接读那个文件，不必读本文件。** 历史版本：`git log --oneline -- HANDOVER.md`

## 当前状态（2026-10-04）

- **项目新家**：`/Users/loccco/WorkBuddy/market dashboard/`（活着的副本）
  ⚠️ 另有旧副本 `/Users/loccco/WorkBuddy/2026-09-04-11-53-22/market-dashboard`：**10:47 已随旧工作区删除**（连同 55MB 备份层 11:15 清掉，只存废纸篓），
  **不要再照旧路径找**。别在那一处改代码（唯一活副本 = 本行上面那条）。
- Git HEAD `c7c1f7e`，工作区干净（最后两笔是 R100z45/46 的 index.html UI 统一 + 09:46 数据 update）。
- 审测基线 `python3 audit_pipeline.py` → PASS=24 / WARN=0 / FAIL=0，exit 0 才准 deploy。
- 10-04 是国庆休市期，**下一个交易日 10-08**（10-05/06/07 均休市）。
- 老话两条，别再踩：① 渲染层改排序必须同步改 `openFailIdx()` 这类按下标回取的导航函数；② `failPool` 多 `entryDate` 是滚动窗口设计，别删 `check_duanban.py` 的 `carried`。

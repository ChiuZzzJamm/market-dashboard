#!/bin/bash
# 每日市场看板自动部署脚本
# 用法: ./deploy.sh
# 说明: 更新 index.html 版本戳后，用部署专用 SSH 私钥推送到 GitHub，触发 GitHub Pages 自动部署
#       不再依赖 GitHub PAT（token 复制易截断），改用项目内 .deploy_key 私钥。

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

# 自动化环境 PATH 可能缺失 python3 / node，显式定位（与 run_push.sh 一致）
PY=$(command -v python3 2>/dev/null || ls /Users/loccco/.workbuddy/binaries/python/versions/*/bin/python3 2>/dev/null | tail -1)
NODE=$(command -v node 2>/dev/null || ls /Users/loccco/.workbuddy/binaries/node/versions/*/bin/node 2>/dev/null | tail -1)
if [ -z "$PY" ]; then echo "❌ 找不到 python3"; exit 1; fi
if [ -z "$NODE" ]; then echo "❌ 找不到 node"; exit 1; fi
echo "[info] using python3: $PY | node: $NODE"

# ---------- 配置 git 使用部署专用 SSH 私钥 ----------
DEPLOY_KEY="$REPO_DIR/.deploy_key"
if [[ ! -f "$DEPLOY_KEY" ]]; then
    echo "❌ 找不到部署私钥: $DEPLOY_KEY"
    echo "   请确认 .deploy_key 存在于项目目录（由本机生成，不可提交到仓库）。"
    exit 1
fi

# 用 GIT_SSH_COMMAND 显式指定私钥，不依赖 ssh-agent / macOS keychain，适合自动化环境
# 注意：本环境 22 端口 SSH 被协议级过滤，改用 GitHub 的 443 端口 SSH（ssh.github.com:443）
# ⚠️ 私钥路径必须用双引号包进 -i 的参数里：项目目录名含空格（"market dashboard"），
#    不加引号会被 ssh 拆成 `Identity file /Users/.../market` + 主机名 `dashboard/.deploy_key`，
#    表现为 "Could not resolve hostname dashboard/.deploy_key"（2026-10-04 实测踩坑）。
#    这里用 -F 传临时 config 避免 shell 分词，最稳妥。
SSH_CFG="$(mktemp -t gitssh.XXXXXX)"
trap 'rm -f "$SSH_CFG"' EXIT
{
    # Host 必须与 remote URL 里的主机名一致（ssh://git@ssh.github.com:443/...），
    # 否则整段配置不匹配、被 ssh 静默忽略 → 回落到默认密钥 → Permission denied (publickey)。
    echo "Host ssh.github.com"
    echo "  HostName ssh.github.com"
    echo "  Port 443"
    echo "  User git"
    echo "  IdentityFile \"$DEPLOY_KEY\""
    echo "  IdentitiesOnly yes"
    echo "  StrictHostKeyChecking no"
    echo "  BatchMode yes"
} > "$SSH_CFG"
export GIT_SSH_COMMAND="ssh -F \"$SSH_CFG\""

# remote 统一使用 SSH over 443 地址（绕过 22 端口过滤）
git remote set-url origin "ssh://git@ssh.github.com:443/ChiuZzzJamm/market-dashboard.git"

# ---------- 更新缓存版本号（防浏览器/CDN 缓存）----------
echo "📈 更新 data.js 版本戳防止浏览器/CDN缓存..."
"$PY" - <<'PY'
import re, pathlib, time
p = pathlib.Path("index.html")
t = p.read_text(encoding="utf-8")
ts = time.strftime("%Y%m%d%H%M")
t2 = re.sub(r'(data\.js\?v=)\d+', r'\g<1>' + ts, t)
p.write_text(t2, encoding="utf-8")
print("version stamp updated to", ts)
PY

# ---------- 校验 data.js 语法 ----------
echo "🔍 校验 data.js 语法..."
"$NODE" --check data.js

# ---------- R100z205：index.html 内联 JS 语法闸门（**阻断**）----------
# 背景（2026-10-25）：此前只校验 data.js。而白屏有两种坏法——data.js 语法坏（已拦）、
# index.html 的 30 万字符内联 <script> 语法坏（**从来没拦过**）。后者更隐蔽：
# 改渲染代码少一个括号、删死函数多留一个 `}`，data.js 依旧完美，audit 75 项全绿
# （它查数据契约与字符串，不做 JS 解析），check_code 的 K1 也只查 py/sh 与 data.js，
# 于是闸门全绿 → 直接推上线 → 用户打开白屏。
# 那天删 newsTag/shortDate 是我手工抽出来验过才敢推的——「靠人记得验」的环节不能留。
echo "🔍 校验 index.html 内联 JS 语法（check_inline_js.py）..."
# ⚠️ 与 check_code 一样：不能写成 `if ! "$PY" x.py | tail`（管道退出码取 tail，恒 0）。
if ! "$PY" check_inline_js.py; then
    echo "❌ index.html 内联 JS 语法未通过，已阻断部署（推上去就是白屏）。"
    echo "   处理办法：按上面给的行号修 index.html；确需紧急推送用 SKIP_AUDIT=1 ./deploy.sh"
    exit 1
fi

# ---------- R100z157：要闻信息等级 + 盘面杂音剔除（确定性脚本，零网络，绝不阻断部署）----------
# 背景：用户以为「要闻按信息等级排列」，实际组内完全按 AI 写入顺序渲染；杂音正则也只护个股层。
# sweep_news.py 给五节要闻逐条打 level（重点/关注/常规）并组内稳定排序，同时剔除
# 「N只股涨停/附股/炸板」这类盘面统计噪音。放在 enrich_policy 之前：政策解码读的是清洗后的要闻。
# 幂等、异常 exit 0，绝不阻断部署。
echo "🗞️ 要闻等级排序与杂音剔除（sweep_news.py）..."
"$PY" sweep_news.py || true

# ---------- R100z157：市场结构面（宽度/拥挤度/连板空间，确定性脚本，零网络，绝不阻断部署）----------
# 背景：A 股收盘只有「涨跌家数 + 涨停跌停」一行平铺，没有结构性判断；美股侧有 outlook 风险维度，
# A 股侧缺对称的量化小节。market_structure.py 从 ashare.breadth/lianban 确定性推导涨跌比区间、
# 涨跌停比、连板高度、高位分歧率、接力拥挤度，并用 market_structure.json 台账算 5 个交易日趋势。
# 位置在部署前、审计前：structure 参与 audit 的 data 契约检查。幂等（同 tradeDate 覆盖），异常 exit 0。
echo "📐 生成市场结构面（market_structure.py）..."
"$PY" market_structure.py || true

# ---------- R100z157：机械候选 vs 自选补位标注（确定性脚本，零网络，绝不阻断部署）----------
# 背景：08:30 的 AI 预测吃的是前一交易日 16:00 入库的 stock_pool.json，实测 6 板块候选只有
# 1/1/0/6/0/2 只，AI 大量「自选补位」在页面上与机械候选长得一模一样，用户无法核验。
# annotate_pool_meta.py 给每只标的打 poolHit、给 aiPrediction 加 poolMeta（快照日期/候选数/
# 命中数/有效期），让「哪些是机械选的、快照什么时候失效」在页面直接可见。异常 exit 0。
echo "🧷 标注机械候选与自选补位（annotate_pool_meta.py）..."
"$PY" annotate_pool_meta.py || true

# ---------- R100z111：政策信号解码前置刷新（一次接入全部自动化）----------
# 背景：此前只有 19:30 叙事步在 prompt 里手动调用 enrich_policy.py；07:00 美股收盘 /
# 周日 22:00 周末汇总同样会整体替换五节要闻，但 policySignals 不刷新 → 政策面板要滞后到
# 下一个交易日 19:30 才更新（用户 2026-10-10 反馈「包含央行的内容没进政策解码」链路缺口之一）。
# enrich_policy 零网络纯函数、任何异常兜底写空数组并 exit 0，绝不阻断部署；幂等可重复执行。
echo "📜 刷新政策信号解码（enrich_policy.py）..."
"$PY" enrich_policy.py || true

# ---------- R100z117 前瞻层 + 信号闭环（确定性脚本，兜底写空 exit 0，绝不阻断部署）----------
# macro_calendar.py：未来 45 日确定性宏观事件（FOMC/GDP/CPI/PPI/MLF/LPR/工增固投社零），零网络纯函数；
# strategy_engine.py：聚合政策/AI预测/断板反包三类信号为盘前关注清单+仓位纪律，并写入回测反馈闭环。
# 两者均读整份 data.js 仅新增顶层键、改写整文件，与 enrich_policy 同机制、互不覆盖其它键。
echo "📅 生成宏观事件日历（macro_calendar.py）..."
"$PY" macro_calendar.py || true
echo "🎯 生成盘前策略闭环（strategy_engine.py）..."
"$PY" strategy_engine.py || true

# ---------- R100z158：回填历史行业基准（零网络，读 git 历史 data.js，绝不阻断部署）----------
# 回测口径改「同源行业基准」后（量纲错配修复），历史预测日的超额要拿当日的行业中位当尺子。
# 每次部署顺手把缺的历史行业基准补进 market_bench.json[history]，保证回测台账随时可重算、
# 不依赖手工补跑。同一 tradeDate 取最新提交、只补不改、幂等，异常 exit 0 兜底不阻断。
echo "📐 回填历史行业基准（market_bench.py --backfill-bench）..."
"$PY" market_bench.py --backfill-bench || true

# ---------- R100z167：页头时间戳归一（必须在审计闸门之前）----------
# 背景：顶层 updatedAt 原先被 5 个脚本各自覆盖（谁最后跑谁赢），实测 A股 17:20 已更新、
# 页头还停在「16:01（韩股/日经）」。此脚本从各分格戳（ashare/us/panorama/四模块/stopTrack）
# 取最大时间戳，生成唯一来源的页头文案；幂等、只改一个字段、异常 exit 0 不阻断。
# 放在审计之前是为了让 G28（数据新鲜度一致性）看到归一后的结果。
echo "🕒 归一时间戳（refresh_stamp.py）..."
"$PY" refresh_stamp.py || true

# ---------- R100z168：个股简称对齐 + 硬风险 red 清出推荐位 ----------
# 背景（用户第二次问「怎么那么多遗留问题」后浮出来的两层）：
#   ① 简称层：AI 写要闻凭记忆填 name，实测 7 只已更名（韦尔股份→豪威集团、银泰黄金→
#      山金国际、郑煤机→中创智领…），其中 600079 人福医药→ST人福、600311 荣华实业→
#      *ST荣华 已戴帽却显示成正常公司，还被摆在「板块受益标的」里。sync_names.py 以
#      行情源为准校正（清洗空白/全角/除权前缀后再比，不误改「粤 传 媒」「XD工商银」）。
#   ② 风险层：stock_risk_blacklist 每天算 red，但只有 update_star 会过滤；
#      check_duanban / power_screener / accumulation_score 不读 → 603330 天洋新材
#      10-09 就公告减持，到 10-21 仍挂在「断板确认池」「量化筛选」里当推荐标的。
#      risk_gate.py 把 red 清出四个推荐池（公告日晚于交易日的「盘后新披露」保留：
#      当日盘中不可知，指标有效，次日 09:45 自动剔）。
# 两个脚本都幂等、原子写、异常 exit 0（真拦截交给 G29，避免外部源抖动阻断部署）。
echo "🏷️  对齐个股简称（sync_names.py）..."
"$PY" sync_names.py --write || true
echo "🛡️  硬风险 red 清出推荐位（risk_gate.py）..."
"$PY" risk_gate.py --write || true

# ---------- R100z53：审计闸门下沉（一次性覆盖五条自动化）----------
# 背景（子代理对账 P0-1）：此前只有 19:30 / 周末两条 automation 的 prompt 里手写了
# 「deploy 前先跑 audit_pipeline」，07:00 / 08:30 / 09:45 三条没有 → 那三条改完代码可以直接 push，
# 闸门等于没有。下沉到 deploy.sh 后，不管哪条任务调它，闸口都在同一处。
# 退出码：audit_pipeline.py 0=无 FAIL，1=有 FAIL。WARN 不拦（WARN 是历史遗留归对应任务处理）。
if [ "${SKIP_AUDIT:-0}" != "1" ]; then
    echo "🧪 运行流水线审计闸门（audit_pipeline.py，FAIL 将阻断部署）..."
    if ! "$PY" audit_pipeline.py; then
        echo "❌ 审计闸门未通过（存在 FAIL），已阻断部署，data.js / index.html 未推送。"
        echo "   处理办法：修掉对应 FAIL 后重跑 deploy.sh；"
        echo "   确需紧急推送时用：SKIP_AUDIT=1 ./deploy.sh"
        exit 1
    fi
    echo "✅ 审计闸门通过。"
    # 自动化体检（R100z169 新增，**非阻断**）：查的是 7 条自动化 prompt / 排程 / 死链 / 基线漂移，
    # 不是 data.js 内容。故意加 `|| true`——它是元层面体检，若哪天它自己判错，
    # 不应该把当天的行情部署一起拖死（那会变成新的单点故障）。
    # 需要它当闸门时手工跑：python3 check_automations.py；退出码 1=有 FAIL。
    if [ -f check_automations.py ]; then
        echo "🩺 自动化体检（check_automations.py，不阻断部署）..."
        "$PY" check_automations.py 2>&1 | tail -3 || true
    fi
    # 记忆体检（M0–M8，同样非阻断）：查 MEMORY.md 体积/路由可达/假未来陈述/归档索引对账。
    # 记忆坏了不会报错，只会安静返回错答案——这类问题只能靠固定判据定期扫。
    if [ -f check_memory.py ]; then
        echo "🧠 记忆体检（check_memory.py，不阻断部署）..."
        "$PY" check_memory.py --quiet 2>&1 | tail -6 || true
    fi
    # 脚本层体检（K1–K10，R100z179b **阻断**）：查的是「脚本怎么写盘」，不是 data.js 内容也不是 prompt。
    # 前四轮修的 bug 全在这一层（非原子写 / 非幂等回填 / 时间戳污染），而 audit 与 check_automations
    # 都看不见它 —— 于是同类问题换个脚本又冒出来。这里 FAIL 必须拦：语法错/裸写 data.js 的脚本
    # 推到线上就是白屏，没有「先上线再说」的余地。
    if [ -f check_code.py ]; then
        echo "🔧 脚本层体检（check_code.py，FAIL 将阻断部署）..."
        # ⚠️ 不能写成 `if ! "$PY" x.py | tail`：管道退出码取的是 tail 的（恒 0），
        #    闸门会变成永不触发。必须不带管道直接判。
        if ! "$PY" check_code.py; then
            echo "❌ 脚本层体检未通过（K1–K10 有 FAIL），已阻断部署。"
            echo "   处理办法：按上面提示修脚本；确需紧急推送用 SKIP_AUDIT=1 ./deploy.sh"
            exit 1
        fi
    fi
else
    echo "⚠️ SKIP_AUDIT=1 —— 已跳过审计闸门（非紧急请勿使用，失败会被追责）。"
fi

# ---------- 提交并推送 ----------
echo "📤 提交并推送到 GitHub（触发 GitHub Pages 自动部署）..."
# git add -A：纳入所有改动（含 .py 脚本），敏感文件（.deploy_key/.notify-config.json 等）已由 .gitignore 排除
# ⚠️ 本地记忆层 .workbuddy/（PROJECT-MEMORY.md + 归档-*.md，含家庭网络/路由器私有信息）已在 .gitignore 硬排除，
#    别手贱 `git add -f .workbuddy/` —— 那是公开仓库。
git add -A
if git diff --cached --quiet; then
    echo "ℹ️ 没有可提交的变更，仅确保远程最新..."
fi

# 有变更才提交
if ! git diff --cached --quiet; then
    git commit -m "update: $(date '+%Y-%m-%d %H:%M') market data"
fi

# 带重试的 push（网络波动时）
for i in 1 2 3; do
    echo "   推送尝试 $i/3..."
    if GIT_TERMINAL_PROMPT=0 git push origin main 2>&1; then
        echo "✅ 推送成功！GitHub Pages 将在 1-2 分钟内自动部署。"
        echo "   公开访问地址: https://chiuzzzjamm.github.io/market-dashboard"
        exit 0
    fi
    echo "   推送失败，${i}<3 时 5 秒后重试..."
    sleep 5
done

# 全部重试失败
echo "❌ git push 连续 3 次失败，部署未触发。"
echo "   可能原因: 公钥尚未添加到 GitHub，或网络异常。"
echo "   请确认已将 .deploy_key.pub 的内容添加到 GitHub → Settings → SSH and GPG keys。"
exit 1

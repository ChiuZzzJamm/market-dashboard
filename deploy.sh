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

# ---------- R100z111：政策信号解码前置刷新（一次接入全部自动化）----------
# 背景：此前只有 16:00 自动化在 prompt 里手动调用 enrich_policy.py；07:30 美股收盘 /
# 周日 22:00 周末汇总同样会整体替换五节要闻，但 policySignals 不刷新 → 政策面板要滞后到
# 下一个交易日 16:00 才更新（用户 2026-10-10 反馈「包含央行的内容没进政策解码」链路缺口之一）。
# enrich_policy 零网络纯函数、任何异常兜底写空数组并 exit 0，绝不阻断部署；幂等可重复执行。
echo "📜 刷新政策信号解码（enrich_policy.py）..."
"$PY" enrich_policy.py || true

# ---------- R100z53：审计闸门下沉（一次性覆盖五条自动化）----------
# 背景（子代理对账 P0-1）：此前只有 16:00 / 周末两条 automation 的 prompt 里手写了
# 「deploy 前先跑 audit_pipeline」，07:30 / 08:30 / 09:45 三条没有 → 那三条改完代码可以直接 push，
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

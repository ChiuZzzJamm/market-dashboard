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
export GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o StrictHostKeyChecking=no -o BatchMode=yes -p 443"

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

# ---------- 提交并推送 ----------
echo "📤 提交并推送到 GitHub（触发 GitHub Pages 自动部署）..."
# git add -A：纳入所有改动（含 .py 脚本），敏感文件（.deploy_key/.notify-config.json 等）已由 .gitignore 排除
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

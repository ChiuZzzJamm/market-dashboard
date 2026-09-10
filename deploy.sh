#!/bin/bash
# 每日市场看板自动部署脚本
# 用法: ./deploy.sh
# 说明: 更新 index.html 版本戳后，用部署专用 SSH 私钥推送到 GitHub，触发 GitHub Pages 自动部署
#       不再依赖 GitHub PAT（token 复制易截断），改用项目内 .deploy_key 私钥。

set -euo pipefail

REPO_DIR="/Users/loccco/WorkBuddy/2026-09-04-11-53-22/market-dashboard"
cd "$REPO_DIR"

# ---------- 配置 git 使用部署专用 SSH 私钥 ----------
DEPLOY_KEY="$REPO_DIR/.deploy_key"
if [[ ! -f "$DEPLOY_KEY" ]]; then
    echo "❌ 找不到部署私钥: $DEPLOY_KEY"
    echo "   请确认 .deploy_key 存在于项目目录（由本机生成，不可提交到仓库）。"
    exit 1
fi

# 用 GIT_SSH_COMMAND 显式指定私钥，不依赖 ssh-agent / macOS keychain，适合自动化环境
export GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o StrictHostKeyChecking=no -o BatchMode=yes"

# remote 统一使用 SSH 地址（推送走 SSH 而非 HTTPS）
git remote set-url origin "git@github.com:ChiuZzzJamm/market-dashboard.git"

# ---------- 更新缓存版本号（防浏览器/CDN 缓存）----------
echo "📈 更新 data.js 版本戳防止浏览器/CDN缓存..."
TIMESTAMP=$(date +%Y%m%d%H%M)
if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' -E "s|data\.js\?v=[^\"']*|data.js?v=$TIMESTAMP|g" index.html
else
    sed -i -E "s|data\.js\?v=[^\"']*|data.js?v=$TIMESTAMP|g" index.html
fi

# ---------- 校验 data.js 语法 ----------
echo "🔍 校验 data.js 语法..."
node --check data.js

# ---------- 提交并推送 ----------
echo "📤 提交并推送到 GitHub（触发 GitHub Pages 自动部署）..."
git add index.html data.js .gitignore deploy.sh
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

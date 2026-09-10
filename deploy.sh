#!/bin/bash
# 每日市场看板自动部署脚本
# 用法: ./deploy.sh
# 说明: 更新 index.html 版本戳后推送到 GitHub，触发 GitHub Pages 自动部署

set -euo pipefail

REPO_DIR="/Users/loccco/WorkBuddy/2026-09-04-11-53-22/market-dashboard"
cd "$REPO_DIR"

# ---------- 读取 GitHub PAT ----------
# 优先级: 1) 环境变量 GITHUB_TOKEN  2) ~/.github-token 文件
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
if [[ -z "$GITHUB_TOKEN" && -f "$HOME/.github-token" ]]; then
    GITHUB_TOKEN=$(cat "$HOME/.github-token" | tr -d '[:space:]')
fi

if [[ -z "$GITHUB_TOKEN" ]]; then
    echo "❌ 未找到 GitHub PAT"
    echo "   请生成 Personal Access Token (classic) 并写入 ~/.github-token，或设置环境变量 GITHUB_TOKEN"
    echo "   生成地址: https://github.com/settings/tokens"
    echo "   所需权限: repo (或至少 public_repo)"
    exit 1
fi

# ---------- 配置 git 使用 token 推送到 HTTPS ----------
# 这样无需依赖 macOS keychain 或 SSH agent，适合自动化环境
ORIGIN_URL="https://$GITHUB_TOKEN@github.com/ChiuZzzJamm/market-dashboard.git"
git remote set-url origin "$ORIGIN_URL"
# 禁用交互式密码提示，防止卡住
git config --local credential.helper ''

# ---------- 更新缓存版本号 ----------
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
    echo "ℹ️ 没有可提交的变更"
    exit 0
fi

git commit -m "update: $(date '+%Y-%m-%d %H:%M') market data"

# 带重试的 push（网络波动时）
for i in 1 2 3; do
    echo "   推送尝试 $i/3..."
    if GIT_TERMINAL_PROMPT=0 git push origin main 2>&1; then
        echo "✅ 推送成功！GitHub Pages 将在 1-2 分钟内自动部署。"
        echo "   公开访问地址: https://chiuzzzjamm.github.io/market-dashboard"
        # 推送成功后恢复普通 HTTPS URL（避免 token 留在 .git/config）
        git remote set-url origin "https://github.com/ChiuZzzJamm/market-dashboard.git"
        exit 0
    fi
    echo "   推送失败，5秒后重试..."
    sleep 5
done

# 全部重试失败
# 恢复普通 URL，避免 token 长期留在配置中
git remote set-url origin "https://github.com/ChiuZzzJamm/market-dashboard.git"
echo "❌ git push 连续 3 次失败，部署未触发。"
echo "   请检查 ~/.github-token 是否有效，或网络连接。"
exit 1

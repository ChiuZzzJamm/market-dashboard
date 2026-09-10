#!/bin/bash
# 推送 wrapper：定位 python3 并调用 push_notify.py，避免定时任务环境 PATH 缺失 python3 导致推送失败
set -e
cd /Users/loccco/WorkBuddy/2026-09-04-11-53-22/market-dashboard
PY=$(command -v python3 2>/dev/null || ls /Users/loccco/.workbuddy/binaries/python/versions/*/bin/python3 2>/dev/null | tail -1)
if [ -z "$PY" ]; then
  echo "python3 not found in PATH or /Users/loccco/.workbuddy/binaries/python/versions/"; exit 1
fi
echo "[info] using python3: $PY"
exec "$PY" push_notify.py "$1"

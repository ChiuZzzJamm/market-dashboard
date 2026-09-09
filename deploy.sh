#!/bin/bash
# Cloudflare Pages 自动部署脚本
# 用法: ./deploy.sh
# 环境变量:
#   CF_ACCOUNT_ID - Cloudflare Account ID
#   CF_API_TOKEN  - Cloudflare API Token

set -e

ACCOUNT_ID="${CF_ACCOUNT_ID:-00ab4b240ad76c8bb8b009e8f9e4932e}"
TOKEN="${CF_API_TOKEN:-cfut_y1PvFdVcZXF7qBBrpYk3p6urjRkfdZMws7e7jhEW726c5fec}"
PROJECT="market-dashboard"

# 计算文件 hash (SHA256)
hash_index=$(shasum -a 256 index.html | awk '{print $1}')
hash_data=$(shasum -a 256 data.js | awk '{print $1}')

# 更新时间戳，防止 CDN/浏览器缓存旧版本
echo "Updating cache-buster timestamp..."
TIMESTAMP=$(date +%Y%m%d%H%M)
perl -pi -e "s|data\.js\?v=[^\"]*|data.js?v=$TIMESTAMP|g" index.html
echo "  Timestamp: $TIMESTAMP"

echo "Deploying to Cloudflare Pages..."
echo "  index.html -> ${hash_index:0:16}..."
echo "  data.js -> ${hash_data:0:16}..."

# 创建 manifest JSON
manifest="{\"index.html\":\"$hash_index\",\"data.js\":\"$hash_data\"}"

# 部署
curl -s -X POST \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects/$PROJECT/deployments" \
  -H "Authorization: Bearer $TOKEN" \
  -F "manifest=$manifest" \
  -F "index.html=@index.html" \
  -F "data.js=@data.js" | python3 -c "
import sys, json
data = json.load(sys.stdin)
if data.get('success'):
    result = data['result']
    print(f\"\\n✅ 部署成功!\")
    print(f\"   URL: {result.get('url', 'N/A')}\")
    print(f\"   Deployment ID: {result.get('short_id', 'N/A')}\")
else:
    print(f\"\\n❌ 部署失败:\")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    sys.exit(1)
"

#!/usr/bin/env python3
"""
Cloudflare Pages Direct Upload 部署脚本（纯标准库，无需 requests）
用法: python deploy.py <project_name>
环境变量:
  CLOUDFLARE_API_TOKEN - Cloudflare API Token
  CLOUDFLARE_ACCOUNT_ID - Cloudflare Account ID
"""
import os, sys, json, hashlib, mimetypes
from pathlib import Path
import urllib.request, urllib.parse, base64

TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
PROJECT = sys.argv[1] if len(sys.argv) > 1 else "market-dashboard"
DIST = Path(__file__).parent

if not TOKEN or not ACCOUNT_ID:
    print("ERROR: 请设置环境变量 CLOUDFLARE_API_TOKEN 和 CLOUDFLARE_ACCOUNT_ID")
    sys.exit(1)

# Step 1: 获取 upload token
print("Step 1: Getting upload token...")
token_url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/pages/projects/{PROJECT}/upload-token"
req = urllib.request.Request(token_url, headers={"Authorization": f"Bearer {TOKEN}"})
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        token_resp = json.loads(resp.read().decode())
except urllib.error.HTTPError as e:
    print(f"  Upload-token endpoint returned {e.code}, trying alternative...")
    token_resp = None

if token_resp and token_resp.get("success"):
    jwt = token_resp["result"]["jwt"]
    print("  JWT token obtained")
    use_jwt = True
else:
    print("  Will use API Token directly for all requests")
    jwt = TOKEN
    use_jwt = False

# Step 2: 收集文件
print("Step 2: Collecting files...")
files_list = sorted([f for f in DIST.rglob("*") if f.is_file() and f.name not in {
    ".notify-config.json", ".wx-config.json", "deploy.py",
    "router-wol-schedule.sh", "market-dashboard.zip"
}])

manifest = {}
file_map = {}
for f in files_list:
    rel = "/" + str(f.relative_to(DIST))
    content = f.read_bytes()
    h = hashlib.sha256(content).hexdigest()
    manifest[rel] = h
    file_map[h] = (f, content)
    print(f"  {rel} -> {h[:8]}...")

# Step 3: 上传文件
print("Step 3: Uploading assets...")
if use_jwt:
    upload_url = "https://api.cloudflare.com/client/v4/pages/assets/upload"
    auth_hdr = {"Authorization": f"Bearer {jwt}"}
else:
    upload_url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/pages/projects/{PROJECT}/deployments"
    auth_hdr = {"Authorization": f"Bearer {TOKEN}"}

# 使用 multipart form-data
boundary = "----DeployBoundary7MA4YWxkTrZu0gW"
body_parts = []

# manifest 字段
manifest_json = json.dumps(manifest)
body_parts.append(f'--{boundary}\r\n'.encode())
body_parts.append(f'Content-Disposition: form-data; name="manifest"\r\n'.encode())
body_parts.append(f'Content-Type: application/json\r\n\r\n'.encode())
body_parts.append(manifest_json.encode())
body_parts.append(b'\r\n')

# 文件字段
for h, (f, content) in file_map.items():
    mime = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
    body_parts.append(f'--{boundary}\r\n'.encode())
    body_parts.append(f'Content-Disposition: form-data; name="{h}"; filename="{f.name}"\r\n'.encode())
    body_parts.append(f'Content-Type: {mime}\r\n\r\n'.encode())
    body_parts.append(content)
    body_parts.append(b'\r\n')

body_parts.append(f'--{boundary}--\r\n'.encode())
body = b''.join(body_parts)

if use_jwt:
    # 先上传 assets
    req = urllib.request.Request(upload_url, data=body, headers={
        "Authorization": f"Bearer {jwt}",
        "Content-Type": f"multipart/form-data; boundary={boundary}"
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        upload_resp = json.loads(resp.read().decode())
    print(f"  Upload result: {upload_resp.get('success', False)}")

    # upsert hashes
    print("Step 4: Upserting hashes...")
    upsert_url = "https://api.cloudflare.com/client/v4/pages/assets/upsert-hashes"
    payload = json.dumps({"hashes": list(file_map.keys())}).encode()
    req = urllib.request.Request(upsert_url, data=payload, headers={
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json"
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        upsert_resp = json.loads(resp.read().decode())
    print(f"  Upsert result: {upsert_resp.get('success', False)}")

    # 创建 deployment
    print("Step 5: Creating deployment...")
    deploy_url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/pages/projects/{PROJECT}/deployments"
    deploy_body = f'--{boundary}\r\n'.encode()
    deploy_body += f'Content-Disposition: form-data; name="manifest"\r\n'.encode()
    deploy_body += f'Content-Type: application/json\r\n\r\n'.encode()
    deploy_body += manifest_json.encode()
    deploy_body += b'\r\n'
    deploy_body += f'--{boundary}--\r\n'.encode()
    req = urllib.request.Request(deploy_url, data=deploy_body, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": f"multipart/form-data; boundary={boundary}"
    })
else:
    # 直接用 API Token 上传所有内容
    deploy_url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/pages/projects/{PROJECT}/deployments"
    req = urllib.request.Request(deploy_url, data=body, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": f"multipart/form-data; boundary={boundary}"
    })

with urllib.request.urlopen(req, timeout=120) as resp:
    deploy_resp = json.loads(resp.read().decode())

if deploy_resp.get("success"):
    result = deploy_resp.get("result", {})
    print(f"\n✅ 部署成功!")
    print(f"   URL: {result.get('url', 'N/A')}")
    print(f"   Environment: {result.get('environment', 'N/A')}")
else:
    print(f"\n❌ 部署失败:")
    print(json.dumps(deploy_resp, indent=2, ensure_ascii=False))
    sys.exit(1)

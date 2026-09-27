#!/bin/bash
# 一键部署 Open WebUI 并接入自建多模态知识库
# =============================================
#
#   ./deploy_openwebui.sh
#
# 需要先跑起来：Qdrant（6333）+ 检索服务（8088）
# 见 ../run_serve.sh
set -e
cd "$(dirname "$0")"

HOST_IP="${HOST_IP:-172.17.0.1}"       # Docker 网桥网关，容器访问宿主用
SEARCH_PORT="${SEARCH_PORT:-8088}"
OWUI_PORT="${OWUI_PORT:-3000}"
SECRET="${WEBUI_SECRET_KEY:-mmkb-local-dev-secret}"
BAILIAN_KEY="${DASHSCOPE_API_KEY:-$(cat ../.secrets/bailian.key 2>/dev/null || echo '')}"
ADMIN_EMAIL="${OWUI_ADMIN_EMAIL:-admin@mmkb.local}"
ADMIN_PASS="${OWUI_ADMIN_PASS:-Mmkb@2026}"

echo "== 1) 启动 Open WebUI 容器 =="
docker rm -f mmkb-openwebui 2>/dev/null || true
docker run -d --name mmkb-openwebui --restart unless-stopped \
  -p "${OWUI_PORT}:8080" \
  -e OPENAI_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  -e OPENAI_API_KEY="$BAILIAN_KEY" \
  -e WEBUI_SECRET_KEY="$SECRET" \
  -e MMKB_SEARCH_URL="http://${HOST_IP}:${SEARCH_PORT}/search" \
  -e MMKB_TOP_K="${MMKB_TOP_K:-5}" \
  -v mmkb-openwebui-data:/app/backend/data \
  ghcr.io/open-webui/open-webui:main >/dev/null
echo "   等待启动…"
for i in $(seq 1 30); do
  sleep 3
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${OWUI_PORT}/" || true)
  [ "$code" = "200" ] && break
done
echo "   Open WebUI: http://localhost:${OWUI_PORT}  (HTTP $code)"

echo "== 2) 创建/登录管理员并取 token =="
TOKEN=$(python3 - "$OWUI_PORT" "$ADMIN_EMAIL" "$ADMIN_PASS" <<'PY'
import json, sys, urllib.request, urllib.error
port, email, pw = sys.argv[1], sys.argv[2], sys.argv[3]
BASE = f"http://localhost:{port}"
def req(m, p, payload):
    d = json.dumps(payload).encode()
    r = urllib.request.Request(BASE + p, data=d, method=m)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
st, r = req("POST", "/api/v1/auths/signin", {"email": email, "password": pw})
if st != 200:
    st, r = req("POST", "/api/v1/auths/signup", {"name": "admin", "email": email, "password": pw})
print(r.get("token", ""))
PY
)
if [ -z "$TOKEN" ]; then echo "   ❌ 拿不到 token"; exit 1; fi
echo "   ✅ token 已获取"

echo "== 3) 安装并启用 Pipe Function =="
OWUI_TOKEN="$TOKEN" OWUI_URL="http://localhost:${OWUI_PORT}" python3 tools/install_owui_function.py 2>&1 | sed 's/^/   /'

echo
echo "完成。打开 http://localhost:${OWUI_PORT} ，在模型下拉里选「多模态知识库」。"
echo "注意：图片 URL 用的是 MMKB_PUBLIC_URL（默认 http://localhost:8088），"
echo "      若从别的机器访问，需要把 run_serve.sh 里的 MMKB_PUBLIC_URL 改成宿主可达地址。"

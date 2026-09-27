#!/usr/bin/env bash
# ============================================================
#  部署后自检
#
#  分层判定，避免"假绿"：
#    · 硬性项（服务 / 集合 / 检索链路）失败 → 退出码非 0
#    · 库为空是**正常状态**（还没入库），单独提示，不算失败
#
#  之所以要区分：早期版本只判 `curl -sf`（HTTP 200），
#  而 /search 在没有召回时照样返回 200 + 空数组，
#  于是"集合根本不存在"也会显示全绿。
# ============================================================
set -uo pipefail
cd "$(dirname "$0")"

GRN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'; DIM=$'\033[2m'; BLD=$'\033[1m'; RST=$'\033[0m'
PASS=0; FAIL=0; SKIP=0

chk()  { if eval "$2" >/dev/null 2>&1; then printf '  %s✅%s %s\n' "$GRN" "$RST" "$1"; PASS=$((PASS+1))
         else printf '  %s❌%s %s%s\n' "$RED" "$RST" "$1" "${3:+  —— $3}"; FAIL=$((FAIL+1)); fi; }
skip() { printf '  %s·%s %s\n' "$DIM" "$RST" "$1"; SKIP=$((SKIP+1)); }

[ -f .env ] && { set -a; . ./.env; set +a; }
PORT="${MMKB_PORT:-8088}"
QPORT="${QDRANT_PORT:-6333}"
COLL="${MMKB_COLLECTION:-mmkb}"

# JSON 里有引号，落临时文件再交给 python 读，避免嵌套引号地狱
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

printf '\n%s自检%s\n' "$BLD" "$RST"

# ---------------- 1) 基础设施 ----------------
chk "Qdrant 可达 (:${QPORT})" "curl -sf http://localhost:${QPORT}/healthz"
chk "集合 ${COLL} 存在" \
    "curl -sf http://localhost:${QPORT}/collections/${COLL}" \
    "集合不存在。先建： docker compose exec mmkb kb init"

# ---------------- 2) 服务 ----------------
curl -s "http://localhost:${PORT}/health" -o "$TMP/health.json" 2>/dev/null || echo '{}' > "$TMP/health.json"
HEALTH_TXT="$(cat "$TMP/health.json")"
chk "检索服务健康 (:${PORT})" \
    "python3 -c \"import json,sys; sys.exit(0 if json.load(open('$TMP/health.json')).get('status')=='ok' else 1)\"" \
    "$HEALTH_TXT"
POINTS="$(python3 -c "import json;print(json.load(open('$TMP/health.json')).get('points',0))" 2>/dev/null || echo 0)"
POINTS="${POINTS:-0}"

# ---------------- 3) 检索链路 ----------------
curl -s -X POST "http://localhost:${PORT}/search" -H 'Content-Type: application/json' \
     -d '{"query":"测试","top_k":1}' -o "$TMP/search.json" 2>/dev/null || echo '{}' > "$TMP/search.json"
chk "检索接口返回合法结构" \
    "python3 -c \"import json,sys; sys.exit(0 if 'results' in json.load(open('$TMP/search.json')) else 1)\"" \
    "没返回 results，看日志： docker compose logs mmkb"

if [ "$POINTS" -gt 0 ]; then
  chk "检索能召回内容（库里有 ${POINTS} 个点）" \
      "python3 -c \"import json,sys; sys.exit(0 if json.load(open('$TMP/search.json')).get('count',0)>0 else 1)\"" \
      "库里有数据但召回为空 —— 检查 embedding 是否正常（DASHSCOPE_API_KEY）"
  chk "GET 检索接口可用（给 Dify HTTP 节点用）" \
      "curl -sf 'http://localhost:${PORT}/search?q=%E6%B5%8B%E8%AF%95&top_k=1&format=text'"
else
  skip "库是空的（还没入库），跳过召回断言"
  skip "  GET 接口仍可用：curl 'localhost:${PORT}/search?q=x&top_k=1&format=text'"
fi

# ---------------- 4) 图片直连 ----------------
if [ "$POINTS" -gt 0 ]; then
  curl -s -X POST "http://localhost:${PORT}/search" -H 'Content-Type: application/json' \
       -d '{"query":"图","top_k":5}' -o "$TMP/images.json" 2>/dev/null || echo '{}' > "$TMP/images.json"
  IMG_URL="$(python3 -c "
import json
d = json.load(open('$TMP/images.json'))
print(next((i['url'] for r in d.get('results', []) for i in r.get('images', [])), ''))
" 2>/dev/null)"
  if [ -n "${IMG_URL:-}" ]; then
    chk "图片可直连访问" "curl -sf '$IMG_URL'" "$IMG_URL"
  else
    skip "库里没有带图的分块，跳过图片断言"
  fi
fi

# ---------------- 汇总 ----------------
printf '\n  通过 %s%d%s 项' "$GRN" "$PASS" "$RST"
[ "$SKIP" -gt 0 ] && printf '，跳过 %s%d%s 项' "$YEL" "$SKIP" "$RST"
[ "$FAIL" -gt 0 ] && printf '，%s失败 %d 项%s' "$RED" "$FAIL" "$RST"
printf '\n\n'

if [ "$FAIL" -eq 0 ]; then
  printf '  %s下一步：%s\n' "$BLD" "$RST"
  echo "    1) 入库一本 PDF（把书放到 ../data/ 下）："
  echo "       ${DIM}docker compose exec mmkb kb ingest /data/你的书.pdf --doc-id book-1${RST}"
  echo "    2) 检索："
  echo "       ${DIM}curl -X POST localhost:${PORT}/search -H 'Content-Type: application/json' \\"
  echo "            -d '{\"query\":\"你的问题\",\"top_k\":5,\"rerank\":true}'${RST}"
  echo "    3) chat 前端：${DIM}./deploy.sh --with-chat${RST}"
  echo "    4) 集成指南：${DIM}docs/chat-integration.md${RST}"
  echo
fi
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)

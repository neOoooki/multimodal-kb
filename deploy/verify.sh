#!/usr/bin/env bash
# 部署后自检：确认每个部件都真的能用（而不只是容器在跑）
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"

GRN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'; DIM=$'\033[2m'; RST=$'\033[0m'
PASS=0; FAIL=0
chk() { if eval "$2" >/dev/null 2>&1; then printf '  %s✅%s %s\n' "$GRN" "$RST" "$1"; PASS=$((PASS+1));
        else printf '  %s❌%s %s\n' "$RED" "$RST" "$1"; FAIL=$((FAIL+1)); fi; }

[ -f .env ] && { set -a; . ./.env; set +a; }
PORT="${MMKB_PORT:-8088}"
QPORT="${QDRANT_PORT:-6333}"

printf '\n%s自检%s\n' "$(printf '\033[1m')" "$RST"

chk "Qdrant 可达 (:${QPORT})"        "curl -sf http://localhost:${QPORT}/healthz"
chk "检索服务可达 (:${PORT})"         "curl -sf http://localhost:${PORT}/health"
chk "检索接口返回结果"                 "curl -sf -X POST http://localhost:${PORT}/search -H 'Content-Type: application/json' -d '{\"query\":\"测试\",\"top_k\":1}'"
chk "GET 检索接口可用（给 Dify HTTP 节点用）" "curl -sf 'http://localhost:${PORT}/search?q=%E6%B5%8B%E8%AF%95&top_k=1'"

IMGS=$(curl -s -X POST "http://localhost:${PORT}/search" -H 'Content-Type: application/json' \
       -d '{"query":"图","top_k":3}' 2>/dev/null \
       | python3 -c "import sys,json;d=json.load(sys.stdin);print(sum(len(r.get('images',[])) for r in d.get('results',[])))" 2>/dev/null || echo 0)
if [ "${IMGS:-0}" -gt 0 ]; then
  URL=$(curl -s -X POST "http://localhost:${PORT}/search" -H 'Content-Type: application/json' \
        -d '{"query":"图","top_k":3}' | python3 -c "import sys,json;d=json.load(sys.stdin);print(next((i['url'] for r in d['results'] for i in r.get('images',[])),''))")
  chk "图片可直连访问" "curl -sf '$URL'"
else
  printf '  %s·%s 库里还没有带图的分块（入库后才会检查）\n' "$DIM" "$RST"
fi

printf '\n  通过 %s%d%s 项' "$GRN" "$PASS" "$RST"
[ "$FAIL" -gt 0 ] && printf '，失败 %s%d%s 项' "$RED" "$FAIL" "$RST"
printf '\n\n'

if [ "$FAIL" -eq 0 ]; then
  printf '  %s下一步：%s\n' "$(printf '\033[1m')" "$RST"
  echo "    1) 入库一本 PDF："
  echo "       ${DIM}docker compose exec mmkb kb ingest /data/sample.pdf --doc-id sample${RST}"
  echo "       （先把 PDF 放到 ../data/ 下）"
  echo "    2) 检索："
  echo "       ${DIM}curl -X POST localhost:${PORT}/search -H 'Content-Type: application/json' \\"
  echo "            -d '{\"query\":\"你的问题\",\"top_k\":5,\"rerank\":true}'${RST}"
  echo "    3) chat 前端：${DIM}./deploy.sh --with-chat${RST}"
  echo "    4) 集成指南：${DIM}docs/chat-integration.md${RST}"
  echo
fi
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)

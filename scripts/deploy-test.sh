#!/usr/bin/env bash
# ============================================================
#  部署契约测试 · 场景 1：全新安装
#
#  为什么需要它：
#    历史上两次阻断级故障（报告一 P1、报告二 R1）都**只在干净环境**暴露 ——
#    "重复执行 deploy.sh" 是通的，因为集合已经存在。没有这个测试，
#    同类问题会一直复发（已经复发过一次，而且第二次比第一次更严重）。
#
#  本脚本做的事，等价于"一个新用户拿到仓库后第一次部署"：
#     复制一份干净的工作副本 → 写 .env → 全新 data 目录 → 一条 up --wait
#     → 断言就绪 → 断言集合已存在 → 断言幂等 → 收尾清理
#
#  隔离性：用全新的工作副本、独立的 compose 项目名/容器名/端口/数据目录，
#         绝不碰你正在跑的栈。
#
#  两个 key 是**假值**：本测试只验证"安装能否收敛"，不做任何外部 API 调用，
#  所以可以在没有密钥的 CI 上跑。真正的入库冒烟是可选步骤（--with-ingest）。
#
#  用法：
#     scripts/deploy-test.sh                 # 全新安装契约
#     scripts/deploy-test.sh --with-ingest   # 额外跑一次样例 PDF 入库（需要真 key）
#     scripts/deploy-test.sh --with-chat     # 额外验证 Open WebUI 集成（需本地已有 6.5GB 镜像）
#     WAIT_TIMEOUT=300 scripts/deploy-test.sh
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[2m'; BLD=$'\033[1m'; RST=$'\033[0m'

PROJECT="mmkb-contract-$$"
PORT="${CONTRACT_PORT:-18088}"
QPORT="${CONTRACT_QDRANT_PORT:-16333}"
OWUI_TEST_PORT="${CONTRACT_OWUI_PORT:-13000}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-240}"
WITH_INGEST=0; WITH_CHAT=0
for a in "$@"; do
  case "$a" in
    --with-ingest) WITH_INGEST=1 ;;
    --with-chat)   WITH_CHAT=1 ;;
    *) printf '未知参数：%s\n' "$a" >&2; exit 2 ;;
  esac
done

PASS=0; FAIL=0
ok()  { printf '  %s✅%s %s\n' "$GRN" "$RST" "$*"; PASS=$((PASS+1)); }
bad() { printf '  %s❌%s %s\n' "$RED" "$RST" "$*"; FAIL=$((FAIL+1)); }
inf() { printf '  %s·%s %s\n'  "$DIM" "$RST" "$*"; }
step(){ printf '\n%s%s%s\n' "$BLD" "$*" "$RST"; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/mmkb-contract-XXXXXX")"
CDIR="$WORK/deploy"                      # compose 文件所在目录
# -p 覆盖 compose 文件里的顶层 name:，让测试与真实栈完全隔离（容器名/网络/卷都按项目名生成）
CFLAGS=(-p "$PROJECT" -f docker-compose.yml)

diag() {
  printf '\n%s—— 诊断 ——%s\n' "$BLD" "$RST"
  ( cd "$CDIR" && docker compose "${CFLAGS[@]}" ps -a 2>&1 | sed 's/^/    /' ) || true
  printf '  %s--- mmkb 日志（末 40 行）---%s\n' "$DIM" "$RST"
  ( cd "$CDIR" && docker compose "${CFLAGS[@]}" logs --tail=40 mmkb 2>&1 | sed 's/^/    /' ) || true
}

cleanup() {
  # 必须带 --profile chat：否则 down 不会移除 chat profile 的容器，
  # 会把 Open WebUI 连端口一起留在机器上（实测踩到过）。
  ( cd "$CDIR" 2>/dev/null && docker compose "${CFLAGS[@]}" --profile chat down -v --remove-orphans >/dev/null 2>&1 ) || true
  rm -rf "$WORK" 2>/dev/null || true
  if [ -d "$WORK" ]; then
    # Qdrant 官方镜像以 root 运行，会在副本的 data/qdrant 里留下宿主删不掉的
    # root 属主文件（P3 的同一个病根）。这里借一次性容器清掉。
    # S2 换成 qdrant/qdrant:*-unprivileged 之后，这个兜底就不再需要。
    docker run --rm -v "$WORK:/w" alpine:3.20 sh -c 'rm -rf /w/* /w/.[!.]*' >/dev/null 2>&1 || true
    rm -rf "$WORK" 2>/dev/null || true
  fi
  [ -d "$WORK" ] && printf '  %s·%s 临时目录未能完全清除：%s\n' "$DIM" "$RST" "$WORK"
}
trap cleanup EXIT

printf '\n%s部署契约测试：全新安装%s  （项目=%s 端口=%s/%s）\n' "$BLD" "$RST" "$PROJECT" "$PORT" "$QPORT"

# ---------------------------------------------------------------
step "0/5 准备一份干净的工作副本"

command -v docker >/dev/null 2>&1 || { bad "未安装 docker"; exit 1; }
docker info >/dev/null 2>&1 || { bad "docker 未运行"; exit 1; }
docker compose version >/dev/null 2>&1 || { bad "未找到 docker compose"; exit 1; }
inf "docker compose $(docker compose version --short 2>/dev/null || echo '?')"

# 复制工作副本：排除 .git、data/ 和 80MB 的示例书，
# 这样 compose 里的 `../data` 落在副本内部，与真实 data/ 完全隔离。
tar -C "$ROOT" --exclude=.git --exclude=./data --exclude=./test-book.pdf \
    -cf - . 2>/dev/null | tar -C "$WORK" -xf - || { bad "复制工作副本失败"; exit 1; }

mkdir -p "$WORK/data/qdrant" "$WORK/data/work" "$WORK/data/parsed"

# 假 key：本测试不调用任何外部 API
cat > "$CDIR/.env" <<EOF
DASHSCOPE_API_KEY=sk-contract-dummy-not-a-real-key
MINERU_TOKEN=sk-contract-dummy-not-a-real-key
MINERU_MODE=api
QDRANT_URL=http://localhost:${QPORT}
MMKB_COLLECTION=mmkb
MMKB_PORT=${PORT}
QDRANT_PORT=${QPORT}
MMKB_PUBLIC_URL=http://localhost:${PORT}
MMKB_UID=$(id -u)
MMKB_GID=$(id -g)
EOF
ok "工作副本就绪（$(du -sh "$WORK" 2>/dev/null | cut -f1)）"

# ---------------------------------------------------------------
step "1/5 构建并启动（一条命令，无 shell 轮询）"

cd "$CDIR" || { bad "无法进入副本的 deploy/"; exit 1; }
export MMKB_UID="$(id -u)" MMKB_GID="$(id -g)"

UP_LOG="$WORK/_up.log"
if docker compose "${CFLAGS[@]}" up -d --build --wait --wait-timeout "$WAIT_TIMEOUT" >"$UP_LOG" 2>&1; then
  ok "up -d --build --wait 成功收敛（全新 data 目录、无预建集合）"
else
  bad "up --wait 未能收敛 —— 全新安装失败"
  printf '%s\n' "$(tail -6 "$UP_LOG" | sed 's/^/      /')"
  diag
  printf '\n  通过 %s%d%s 项，%s失败 %d 项%s\n\n' "$GRN" "$PASS" "$RST" "$RED" "$FAIL" "$RST"
  exit 1
fi

# ---------------------------------------------------------------
step "2/5 就绪断言"

READY="$WORK/_ready.json"
ENDPOINT=""
for ep in /readyz /health; do
  if curl -sf "http://localhost:${PORT}${ep}" -o "$READY" 2>/dev/null; then
    ENDPOINT="$ep"
    break
  fi
done

if [ -z "$ENDPOINT" ]; then
  bad "就绪端点无响应（试过 /readyz 与 /health）"
  diag
else
  [ "$ENDPOINT" = "/health" ] && inf "注意：/readyz 尚未实现，回退到 /health（S1 待办）"
  ok "就绪端点 ${ENDPOINT} 返回 200"

  if python3 -c "
import json,sys
sys.exit(0 if json.load(open('$READY')).get('collection_exists') is True else 1)
" 2>/dev/null; then
    ok "集合已创建（collection_exists=true）"
  else
    bad "集合未创建：$(head -c 200 "$READY")"
  fi
fi

# ---------------------------------------------------------------
step "3/5 健康契约：存活与就绪必须分开"

# 存活：不碰依赖，永远 200
if curl -sf "http://localhost:${PORT}/livez" >/dev/null 2>&1; then
  ok "/livez 返回 200（不碰依赖）"
else
  bad "/livez 不可用 —— 存活探针缺失"
fi

# 就绪：依赖不可用时必须 503，而**存活仍是 200**。
# 这是 C 根因的回归测试：把依赖状态混进存活探针，
# 会让"依赖抖动"变成"启动失败"（R1 的死锁机制）。
BADPORT=$((PORT + 1000))
docker rm -f "${PROJECT}-degraded" >/dev/null 2>&1 || true
if docker run --rm -d --name "${PROJECT}-degraded" -p "${BADPORT}:8088" \
     -e QDRANT_URL=http://definitely-not-a-qdrant:6333 \
     -e DASHSCOPE_API_KEY=sk-contract-dummy \
     mmkb/service:latest >/dev/null 2>&1; then
  sleep 4
  LIVE=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${BADPORT}/livez" 2>/dev/null || echo 000)
  READY=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${BADPORT}/readyz" 2>/dev/null || echo 000)
  [ "$LIVE" = "200" ] && ok "Qdrant 不可达时 /livez 仍 200（存活=进程活着）" \
                      || bad "Qdrant 不可达时 /livez 返回 ${LIVE}（应为 200，存活探针不应依赖外部服务）"
  [ "$READY" = "503" ] && ok "Qdrant 不可达时 /readyz 返回 503（就绪=能服务）" \
                       || bad "Qdrant 不可达时 /readyz 返回 ${READY}（应为 503）"
else
  inf "降级探针容器启动失败，跳过该项"
fi
docker rm -f "${PROJECT}-degraded" >/dev/null 2>&1 || true

# ---------------------------------------------------------------
step "4/5 幂等性：再跑一次 up"

if docker compose "${CFLAGS[@]}" up -d --wait --wait-timeout "$WAIT_TIMEOUT" >"$WORK/_up2.log" 2>&1; then
  ok "重复执行仍然收敛（幂等）"
else
  bad "重复执行未能收敛"
  printf '%s\n' "$(tail -6 "$WORK/_up2.log" | sed 's/^/      /')"
fi

# ---------------------------------------------------------------
if [ "$WITH_INGEST" = "1" ]; then
  step "5/5 样例 PDF 入库冒烟（需要真实 key）"
  if [ -z "${DASHSCOPE_API_KEY:-}" ] || [ -z "${MINERU_TOKEN:-}" ]; then
    inf "未提供真实 DASHSCOPE_API_KEY / MINERU_TOKEN，跳过"
  else
    python3 "$WORK/tests/fixtures/make_sample_pdf.py" "$WORK/data/sample-2p.pdf" >/dev/null 2>&1 || true
    if docker compose "${CFLAGS[@]}" run --rm --no-deps \
         -e DASHSCOPE_API_KEY -e MINERU_TOKEN mmkb \
         kb ingest /data/sample-2p.pdf --doc-id contract-sample >"$WORK/_ingest.log" 2>&1; then
      ok "样例 PDF 入库成功"
      if curl -s "http://localhost:${PORT}/search?q=earthing&top_k=3&format=text" | grep -qi "earth"; then
        ok "入库后可检索到内容"
      else
        bad "入库后检索为空"
      fi
    else
      bad "样例 PDF 入库失败"
      tail -8 "$WORK/_ingest.log" | sed 's/^/      /'
    fi
  fi
else
  step "5/5 入库冒烟"
  inf "未启用（加 --with-ingest 并设置真实 key 可跑）"
fi

# ---------------------------------------------------------------
# Open WebUI 集成（可选：镜像 6.5GB，默认不进常规 CI）
if [ "$WITH_CHAT" = "1" ]; then
  step "5b Open WebUI 集成（--with-chat）"

  printf 'OWUI_PORT=%s\n' "$OWUI_TEST_PORT" >> "$CDIR/.env"

  if docker compose "${CFLAGS[@]}" --profile chat up -d --wait --wait-timeout "$WAIT_TIMEOUT" \
       >"$WORK/_chat.log" 2>&1; then
    ok "chat profile 启动成功（Open WebUI :${OWUI_TEST_PORT}）"
  else
    bad "chat profile 启动失败"
    tail -6 "$WORK/_chat.log" | sed 's/^/      /'
  fi

  if OWUI_URL="http://localhost:${OWUI_TEST_PORT}" \
     python3 "$WORK/integrations/openwebui/install_owui_function.py" \
       >"$WORK/_owui_install.log" 2>&1; then
    ok "Pipe Function 安装成功（含注册/启用/回读验证）"
  else
    bad "Pipe Function 安装失败"
    tail -10 "$WORK/_owui_install.log" | sed 's/^/      /'
  fi

  if OWUI_URL="http://localhost:${OWUI_TEST_PORT}" MMKB_URL="http://localhost:${PORT}" \
     python3 "$WORK/integrations/openwebui/verify_integration.py" \
       >"$WORK/_owui_verify.log" 2>&1; then
    ok "集成链路端到端通过（回答非空 / 无 <img> / 图片可直连）"
  else
    bad "集成链路验证失败"
    sed -n '1,40p' "$WORK/_owui_verify.log" | sed 's/^/      /'
  fi
fi

# ---------------------------------------------------------------
printf '\n  通过 %s%d%s 项' "$GRN" "$PASS" "$RST"
[ "$FAIL" -gt 0 ] && printf '，%s失败 %d 项%s' "$RED" "$FAIL" "$RST"
printf '\n\n'

if [ "$FAIL" -eq 0 ]; then
  printf '  %s全新安装契约满足 ✅%s\n\n' "$GRN" "$RST"
  exit 0
fi
printf '  %s全新安装契约未满足 —— 干净环境部署是坏的%s\n\n' "$RED" "$RST"
exit 1

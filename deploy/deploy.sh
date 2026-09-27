#!/usr/bin/env bash
# ============================================================
#  一键部署：Qdrant + 检索服务（可选 Open WebUI）
#
#    ./deploy.sh                 # 只部署存储 + 检索服务
#    ./deploy.sh --with-chat     # 同时起 Open WebUI
#    ./deploy.sh --down          # 停止
#    ./deploy.sh --verify        # 只做部署后自检
#
#  脚本是**幂等**的，重复执行不会丢数据。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[2m'; BLD=$'\033[1m'; RST=$'\033[0m'
say()  { printf '%s\n' "$*"; }
ok()   { printf '  %s✅%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '  %s⚠️ %s%s\n' "$YEL" "$*" "$RST"; }
die()  { printf '  %s❌ %s%s\n' "$RED" "$*" "$RST" >&2; exit 1; }
step() { printf '\n%s%s%s\n' "$BLD" "$*" "$RST"; }

WITH_CHAT=0
ACTION=up
for a in "$@"; do
  case "$a" in
    --with-chat) WITH_CHAT=1 ;;
    --down)      ACTION=down ;;
    --verify)    ACTION=verify ;;
    -h|--help)   sed -n '2,12p' "$0"; exit 0 ;;
    *) die "未知参数：$a" ;;
  esac
done

# ---------------------------------------------------------------
step "1/6 检查运行环境"

command -v docker >/dev/null 2>&1 || die "未安装 docker"
docker info >/dev/null 2>&1 || die "docker 未运行（或当前用户无权限：试试 sudo usermod -aG docker \$USER 后重新登录）"
ok "docker 可用（$(docker --version | cut -d, -f1)）"

if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  die "未找到 docker compose（v2 插件或 v1 独立版都行）"
fi
ok "compose 可用（$DC）"

# compose 里的 `user:` 要用宿主 uid/gid，否则容器会以 root 在挂载目录留文件。
# UID/GID 是 shell 内建变量，默认不导出，必须显式 export。
export MMKB_UID="$(id -u)"
export MMKB_GID="$(id -g)"
ok "容器将以 $(id -u):$(id -g) 运行（避免遗留 root 属主文件）"

if [ "$ACTION" = "down" ]; then
  step "停止服务"
  $DC --profile chat down
  ok "已停止（数据保留在 ../data）"
  exit 0
fi

# ---------------------------------------------------------------
if [ "$ACTION" = "up" ]; then
step "2/6 准备配置"

if [ ! -f .env ]; then
  cp .env.example .env
  warn "已生成 .env（从 .env.example）—— 请填入密钥后重新执行"
  say ""
  say "    ${DIM}vim .env${RST}    # 至少填 DASHSCOPE_API_KEY 和 MINERU_TOKEN"
  say ""
  exit 1
fi
ok ".env 已存在"

# shellcheck disable=SC1091
set -a; . ./.env; set +a

[ -n "${DASHSCOPE_API_KEY:-}" ] || die ".env 里没有 DASHSCOPE_API_KEY（向量化必需）"
case "$DASHSCOPE_API_KEY" in sk-xxxx*) die "DASHSCOPE_API_KEY 还是占位值，请填真实 Key" ;; esac
ok "DASHSCOPE_API_KEY 已设置"

if [ -z "${MINERU_TOKEN:-}" ] && [ "${MINERU_MODE:-api}" = "api" ]; then
  warn "MINERU_MODE=api 但没有 MINERU_TOKEN —— 入库会失败"
  warn "  去 https://mineru.net/apiManage/token 取一个（免费 1000 页/天）"
  warn "  或改成 MINERU_MODE=local 走本地解析"
fi

# ---------------------------------------------------------------
step "3/6 准备数据目录"

# 这里必须**由宿主机用户**先建好、再交给 Docker，
# 否则 Docker 会以 root 建出 data/，之后宿主机就写不进去了（踩过）。
mkdir -p ../data/qdrant ../data/work ../data/parsed
ok "已创建 ../data/{qdrant,work,parsed}"

# 若之前被 root 建过，用一次性容器把属主改回来
if [ ! -w ../data/work ]; then
  warn "../data 属主不对，尝试用容器修正…"
  docker run --rm -v "$ROOT/data:/d" alpine \
    sh -c "chown -R $(id -u):$(id -g) /d" && ok "属主已修正" || die "无法修正 ../data 属主"
fi
ok "数据目录可写"

# ---------------------------------------------------------------
step "4/6 构建并启动（收敛由 compose 声明决定）"

PROFILE_ARG=""
[ "$WITH_CHAT" = "1" ] && PROFILE_ARG="--profile chat"

# 版本门槛：`up --wait` 与"一次性依赖"的兼容是 docker/compose#9572 才修好的，
# 更早的版本会在一次性服务上永久等待。
DC_VERSION="$($DC version --short 2>/dev/null || echo '?')"
say "  ${DIM}compose ${DC_VERSION}${RST}"

# 顺序（qdrant 健康 → mmkb-init 建集合 → mmkb 启动 → openwebui）全部写在
# docker-compose.yml 的 depends_on 里，这里不再有任何 sleep/轮询编排。
# 以前这里先轮询 /health 等 200、之后才 `kb init` —— 而集合不存在时
# /health 返回 503，于是全新安装死锁（报告二 R1）。
WAIT_TIMEOUT="${MMKB_WAIT_TIMEOUT:-300}"
if ! $DC $PROFILE_ARG up -d --build --wait --wait-timeout "$WAIT_TIMEOUT"; then
  say ""
  $DC ps -a 2>&1 | sed 's/^/    /' || true
  say ""
  $DC logs --tail=60 mmkb-init 2>&1 | sed 's/^/    /' || true
  $DC logs --tail=60 mmkb 2>&1 | sed 's/^/    /' || true
  die "服务未能在 ${WAIT_TIMEOUT}s 内收敛（看上面的 ps/logs）"
fi
ok "容器已启动且健康"

# ---------------------------------------------------------------
step "5/6 就绪断言（业务语义：集合可用）"

PORT="${MMKB_PORT:-8088}"
READY_URL="http://localhost:${PORT}/readyz"
ready_ok=0
for i in $(seq 1 30); do
  if curl -sf "$READY_URL" -o /tmp/.mmkb_ready.$$ 2>/dev/null; then ready_ok=1; break; fi
  sleep 1
done
if [ "$ready_ok" = "1" ]; then
  ok "就绪（${READY_URL}）：$(cat /tmp/.mmkb_ready.$$)"
else
  warn "就绪端点未通过：${READY_URL}"
fi
rm -f /tmp/.mmkb_ready.$$
# 注意：这里判的是 /readyz（依赖 + 集合），不是 /livez。
# compose 的 healthcheck 故意只探 /livez —— 两者职责不同，见 docker-compose.yml 注释。

# ---------------------------------------------------------------
# 带了 chat 前端时，顺手把 Pipe Function 装进去。
# Open WebUI 的 in-process Function 存在它自己的数据库里，只能通过它的 API 注册 ——
# 不装的话"模型下拉里选多模态知识库"这一步根本不存在。
if [ "$WITH_CHAT" = "1" ]; then
  step "5b/6 接入 Open WebUI（安装 Pipe Function）"
  OWUI_URL="http://localhost:${OWUI_PORT:-3000}" \
    python3 "$ROOT/integrations/openwebui/install_owui_function.py" 2>&1 | sed 's/^/  /' \
    || warn "自动安装失败，可手动重试：
      OWUI_URL=http://localhost:${OWUI_PORT:-3000} python3 integrations/openwebui/install_owui_function.py"
fi
fi

# ---------------------------------------------------------------
step "6/6 部署后自检"
exec "$(dirname "$0")/verify.sh"

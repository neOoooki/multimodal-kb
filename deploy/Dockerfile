# ============================================================
# 检索服务镜像
#
# 多阶段构建：先把包装进虚拟环境，再拷进干净的运行镜像。
# 基础镜像用 slim；核心零第三方依赖，所以镜像很小。
# ============================================================
FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY multimodal_kb ./multimodal_kb
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir .


FROM python:3.12-slim

# 时区与中文显示
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

COPY --from=builder /opt/venv /opt/venv

# 第三方适配器（Dify 等）。它们不属于核心包，放在镜像的 /app/integrations 下，
# 由 MMKB_ENABLE_DIFY_ADAPTER=1 决定是否挂载。
COPY integrations /app/integrations
# 兜底：源码权限若被带成 600，非 root 运行时会读不到（踩过）
RUN chmod -R a+rX /app/integrations

# 运行期数据（缓存 + 解析产物 + 图片）都挂到这里
ENV MMKB_WORK_DIR=/data/work \
    MMKB_PARSED_DIR=/data/parsed
VOLUME ["/data"]

EXPOSE 8088

# 容器内用服务名访问 Qdrant；宿主机端口映射后由 MMKB_PUBLIC_URL 决定浏览器看到的地址
CMD ["kb", "serve", "--host", "0.0.0.0", "--port", "8088"]

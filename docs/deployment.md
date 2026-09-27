# 从零部署指南

> 本文假设你**什么都没有**：一台装了 Docker 的干净机器。
> 全程 10 分钟，含自动与手动两条路。部署完会有自检脚本确认每个部件真的能用。

---

## 0. 你会得到什么

| 部件 | 端口 | 作用 |
| --- | --- | --- |
| Qdrant | 6333 | 向量库（三路向量：融合 / 文本 / 图片） |
| 检索服务 | 8088 | HTTP 接口：检索、图片直连、Dify 适配 |
| Open WebUI（可选） | 3000 | chat 前端，图片/公式能正常渲染 |

---

## 1. 前置条件

### 1.1 硬件与系统

| 项 | 最低 | 说明 |
| --- | --- | --- |
| CPU | 2 核 | 只跑服务，不做本地解析时要求很低 |
| 内存 | 4 GB | 服务本身占用很小；**本地解析才需要 16 GB** |
| 磁盘 | 20 GB | 向量库与图片；文档越多越大 |
| 系统 | Linux / macOS | Windows 用 WSL2 |
| Docker | 20.10+ | 需要 `docker compose`（v2 插件或 v1 独立版都行） |

> **不需要 GPU。**

### 1.2 两个 API Key（必须）

**① 阿里云百炼（DASHSCOPE_API_KEY）** —— 用于向量化和图片语义标注

1. 打开 <https://bailian.console.aliyun.com/>
2. 右上角「API-KEY」→ 创建
3. 形如 `sk-xxxxxxxx`

> 成本参考：100 页教材约 **0.1 元**（图片描述用最便宜的 `qwen3-vl-flash`）。

**② MinerU（MINERU_TOKEN）** —— 用于 PDF 解析

1. 打开 <https://mineru.net/apiManage/token>
2. 注册后创建 Token
3. 形如 `sk-xxxxxxxx`

> **免费额度 1000 页/天**。100 页 PDF 约 40 秒。
> 不想用官方 API 可以走本地解析（见 [1.3](#13-可选本地解析零-api-成本)）。

### 1.3 （可选）本地解析：零 API 成本

适合不想把文档传到云上、或想省额度。**约 0.62 秒/页**（i5-13500HX 实测，100 页 62 秒）。

要求：
- **CPU 必须支持 AVX2**（2013 年后的 Intel/AMD 基本都有）
- 内存 **≥16 GB**（实测 100 页峰值 6 GB，但官方建议 16 GB 起）
- **容器必须给足 `/dev/shm`**（Docker 默认 64 MB 会导致 `multiprocessing` 报 `PermissionError`）

```bash
# 在宿主机装（不是容器里）
pip install "mineru==4.0.7"
mineru-models-download --tier basic --small-backend onnx    # 约 600 MB
```

安装后设置 `MINERU_MODE=local`。

> **电子版 PDF（有文字层）用 `basic` 档就够** —— 实测与云端 VLM 档输出完全一致。
> 扫描件才需要 VLM 档。

---

## 2. 自动部署（推荐）

```bash
git clone <仓库地址> multimodal-kb
cd multimodal-kb/deploy

./deploy.sh
```

第一次运行会生成 `.env` 并让你去填 key：

```bash
vim .env          # 至少填 DASHSCOPE_API_KEY 和 MINERU_TOKEN
./deploy.sh       # 再跑一次
```

脚本做了这些事（**幂等，可重复执行**）：

1. 检查 Docker / compose 是否可用
2. 检查 `.env` 与必要密钥
3. 建好 `data/{qdrant,work,parsed}` 并**确保属主正确**
4. `docker compose up -d --build`
5. 轮询 `/health` 等就绪
6. 跑部署后自检

成功输出：

```
自检
  ✅ Qdrant 可达 (:6333)
  ✅ 检索服务可达 (:8088)
  ✅ 检索接口返回结果
  ✅ GET 检索接口可用（给 Dify HTTP 节点用）

  通过 4 项
```

### 带 chat 前端

```bash
./deploy.sh --with-chat        # 额外起 Open WebUI（:3000）
```

### 其它命令

```bash
./deploy.sh --down       # 停止（数据保留）
./verify.sh              # 只做自检
docker compose logs -f mmkb   # 看日志
```

---

## 3. 手动部署（逐步）

想自己控制每一步，或自动脚本出问题时用这条。

### 3.1 建目录（**顺序很重要**）

```bash
cd multimodal-kb
mkdir -p data/qdrant data/work data/parsed
```

> ⚠️ **必须先建 `data/` 再让 Docker 挂载它。**
> 否则 Docker 会以 root 建这个目录，之后你的宿主机用户就写不进 `data/work` 了。
> 已经踩到了就这么修：
> ```bash
> docker run --rm -v "$PWD/data:/d" alpine chown -R $(id -u):$(id -g) /d
> ```

### 3.2 起 Qdrant

```bash
docker run -d --name mmkb-qdrant --restart unless-stopped \
  -p 6333:6333 \
  -v "$PWD/data/qdrant:/qdrant/storage" \
  qdrant/qdrant:latest

curl -s localhost:6333/healthz      # 应返回 healthz check passed
```

### 3.3 配置

```bash
cp .env.example .env
vim .env
```

必填两项：

```bash
DASHSCOPE_API_KEY=sk-xxxx
MINERU_TOKEN=sk-xxxx
```

### 3.4 起检索服务

**方式 A：Docker（推荐）**

```bash
docker build -f deploy/Dockerfile -t mmkb/service:latest .
docker run -d --name mmkb-service --restart unless-stopped \
  -p 8088:8088 \
  --env-file .env \
  -e QDRANT_URL=http://host.docker.internal:6333 \
  -e MMKB_PUBLIC_URL=http://localhost:8088 \
  -e MMKB_WORK_DIR=/data/work \
  -e MMKB_PARSED_DIR=/data/parsed \
  -v "$PWD/data:/data" \
  --add-host host.docker.internal:host-gateway \
  mmkb/service:latest
```

> Linux 上 `host.docker.internal` 需要 `--add-host`；也可以直接用宿主 IP 或把 Qdrant 也放进同一个 compose 网络。

**方式 B：直接跑（无 Docker）**

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .            # 核心零依赖，这一步很快
set -a; . ./.env; set +a
kb serve                    # 或 python3 -m multimodal_kb serve
```

### 3.5 验证

```bash
curl -s localhost:8088/health
# {"status":"ok","points":0,"collection":"mmkb"}

curl -s -X POST localhost:8088/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"测试","top_k":3}'
# {"query":"测试","count":0,"results":[]}   ← 库还是空的，正常
```

---

## 4. 入库第一份文档

服务起好后，向量库还是空的。入库一本 PDF：

```bash
# 把 PDF 放进 data/ 让容器能看到
cp 你的教材.pdf data/

# 在容器里入库
docker compose -f deploy/docker-compose.yml exec mmkb \
  kb ingest /data/你的教材.pdf --doc-id book-1
```

**不用 Docker 时**：

```bash
./kb ingest 你的教材.pdf --doc-id book-1
# 或
python3 scripts/ingest.py 你的教材.pdf --doc-id book-1
```

过程与耗时（100 页教材实测）：

```
① 解析：1496 个块                    15–40 秒（API）
② 分块：336 父块 / 431 子块 / 156 图片
③ 标注：156 张图
④ 向量化 …                            约 4 分钟（933 次调用，之后有缓存）
⑤ 入库：431 个点
```

> **`--doc-id` 建议固定**：重复入库会**幂等更新**而不是产生重复点。

### 验证入库成功

```bash
./kb docs          # 应该看到你的文档与分块数
./kb eval          # 跑内置召回测试
./kb search "你的问题"
```

---

## 5. 部署后自检清单

```bash
cd deploy && ./verify.sh
```

手工逐项：

```bash
# 1) 容器都在跑
docker compose ps

# 2) Qdrant 有数据
curl -s -X POST localhost:6333/collections/mmkb/points/count \
  -H 'Content-Type: application/json' -d '{"exact":true}'

# 3) 检索服务健康
curl -s localhost:8088/health

# 4) 能检索出东西
./kb search "任意关键词" -k 3

# 5) 图片能直连（浏览器打开这个 URL 应该看到图）
./kb search "任意关键词" -k 1     # 输出里的 🖼 那行就是 URL
```

---

## 6. 常见问题

### 6.1 目录权限：`Permission denied: .../data/work`

**两种成因，都要处理：**

**① `data/` 被 Docker 以 root 预建**（挂 Qdrant 卷时）。
`deploy.sh` 已经会先用你的用户建好目录，手动部署请照做。已经踩到了就修：

```bash
docker run --rm -v "$PWD/data:/d" alpine chown -R $(id -u):$(id -g) /d
```

**② 容器以 root 运行，在挂载目录写下 root 属主的文件。**
这会让宿主机侧 `kb ingest` 和 `make distclean` 失败。

已在 `docker-compose.yml` 里给检索服务设了非 root 用户：

```yaml
user: "${MMKB_UID:-1000}:${MMKB_GID:-1000}"
```

`deploy.sh` 会 `export MMKB_UID=$(id -u) MMKB_GID=$(id -g)`。
**手动用 compose 时要自己导出**，否则会退化成 1000:1000：

```bash
export MMKB_UID=$(id -u) MMKB_GID=$(id -g)
docker compose up -d
```

> **Qdrant 官方镜像无法以非 root 启动**（会 panic），所以它的
> `data/qdrant` 仍是 root 属主。`make distclean` 已经用一次性容器来清理，
> 手动删的话也要绕一下：
> ```bash
> docker run --rm -v "$PWD/data:/d" alpine rm -rf /d/qdrant/*
> ```

### 6.1b 宿主机 `kb` 命令提示缺少 API Key

`kb` 启动器**会自动读 `./.env` 和 `./deploy/.env`**（已存在的环境变量不覆盖）。
如果还报缺少 key，说明两个文件都没有：

```bash
# 确认一下
./kb doctor | head -20
# 或手动加载
set -a; . deploy/.env; set +a
```

### 6.2 服务的 `/health` 一直不通

```bash
docker compose -f deploy/docker-compose.yml logs mmkb
```

常见原因：
- `DASHSCOPE_API_KEY` 没填 → 检索时无法做 query 向量化
- `QDRANT_URL` 指向了 `localhost` → **容器里 localhost 是它自己**，要用服务名或宿主 IP

### 6.3 检索返回空，但 `points` 不为 0

- 库里确实没数据 → `./kb docs` 看看
- 用的是 Dify → 见 [chat-integration.md](chat-integration.md#dify-的两个必踩坑)

### 6.4 图片显示不出来（返回 404 或裂图）

`MMKB_PUBLIC_URL` 是**浏览器视角**的地址：
- 浏览器和服务在同一台机器 → `http://localhost:8088` ✅
- 从别的机器访问 → 必须改成 `http://<宿主IP>:8088`

```bash
# 改了要重启服务
vim .env
docker compose -f deploy/docker-compose.yml up -d mmkb
```

### 6.5 入库时报 `MINERU_TOKEN` 相关错误

`MINERU_MODE=api` 必须有 token。没有就用本地：

```bash
MINERU_MODE=local ./kb ingest 书.pdf
```

### 6.6 本地解析报 `PermissionError`（multiprocessing）

`/dev/shm` 太小。Docker 里跑要加：

```bash
docker run --shm-size=2g ...
```

compose 里：

```yaml
    shm_size: "2gb"
```

### 6.7 本地解析报 `Illegal instruction`

CPU 不支持 AVX2。只能改用官方 API。

---

## 7. 升级与运维

```bash
# 更新代码
git pull
cd deploy && ./deploy.sh          # 会重新 build 并滚动重启

# 备份（向量库 + 配置）
tar czf mmkb-backup-$(date +%F).tar.gz data/qdrant deploy/.env

# 恢复
tar xzf mmkb-backup-*.tar.gz

# 清空所有数据重来
make distclean                    # 或手动 rm -rf data/{qdrant,work,parsed}/*
```

---

## 8. 下一步

- **把知识库接进 chat 应用** → [chat-integration.md](chat-integration.md)
- **了解架构与三路向量** → [architecture.md](architecture.md)
- **看接口定义** → [api.md](api.md)
- **遇到怪问题** → [troubleshooting.md](troubleshooting.md)

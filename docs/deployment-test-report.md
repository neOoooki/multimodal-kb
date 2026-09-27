# 部署测试报告

> 测试范围：**仅核心服务**（Qdrant + 检索服务），走文档里的推荐路径 `deploy/deploy.sh`。
> 不含 Open WebUI / Dify 集成。
> 结论：**部署成功，可用**；发现 1 个会影响"下一步入库"的真 bug + 若干中低优先级问题。

---

## 1. 环境与最终状态

| 项 | 值 |
| --- | --- |
| 主机 | Linux，Docker 29.8.1 / Compose v5.5.1 |
| Python | 3.12.3 |
| 部署方式 | `cd deploy && ./deploy.sh`（**文档原始命令，未改动脚本**） |
| 端口 | Qdrant `6333`、检索服务 `8088`（默认端口） |
| 密钥 | 复用 `my_knowledge_base/.secrets/` 的 bailian / mineru key，写入 `deploy/.env`（权限 600，已被 `.gitignore` 忽略） |
| 向量库 | 全新空库 |

最终状态：两个容器 `Up (healthy)`；集合 `mmkb` 存在且为 0 点；
`fusion`(2560, dense) / `text`(1024, dense) / `image`(2560, multivector max_sim) 三个具名向量齐全。

```
NAME           STATUS                    PORTS
mmkb-qdrant    Up (healthy)              0.0.0.0:6333->6333/tcp
mmkb-service   Up (healthy)              0.0.0.0:8088->8088/tcp

$ curl -s localhost:8088/health
{"status": "ok", "points": 0, "collection": "mmkb"}
```

---

## 2. 验证通过的部分

| 验证项 | 结果 |
| --- | --- |
| `./deploy.sh` 首次部署 | ✅ 建目录 → 构建 → 起容器 → 等健康 → 自检，全绿 |
| `./deploy.sh` 重复执行（幂等） | ✅ 重建 service 容器，Qdrant 数据保留 |
| `./deploy.sh --down` → 再 `up` | ✅ 容器与网络正确移除 / 重建，数据保留 |
| DashScope `text-embedding-v4` | ✅ dim = 1024 |
| DashScope `qwen3-vl-embedding`（文/图） | ✅ dim = 2560 |
| DashScope `qwen3-rerank` | ✅ 返回相关性分数，排序正确 |
| MinerU token | ✅ 通过鉴权（伪造 batch id → `code:-60012 task not found`） |
| 真实书籍入库（`--limit 2` 试跑，复用已有解析产物） | ✅ 1496 块 → 336 父块 / 431 子块 / 156 图；标注、三路向量化、入库 |
| HTTP 检索（含 rerank） | ✅ 三路召回 + RRF + rerank，`scores_breakdown` 完整 |
| 图片直连 `/images/{doc}/{name}` | ✅ HTTP 200 `image/jpeg`；不存在的图 → 404 |
| `kb docs` / `kb drop` | ✅ 文档清单与按 `doc_id` 删除正常 |
| `git status` | ✅ 干净（`data/`、`.env` 均未入库） |

---

## 3. 发现的问题

### 🔴 P1 —— `kb ingest` 在新部署上必然失败在最后一步

**现象**（就是 README / deployment.md 里的那条命令）：

```
$ docker compose exec mmkb kb ingest /data/book.pdf --doc-id book-1
① 解析：1496 个块
② 分块：336 父块 / 431 子块 / 156 图片
③ 标注：1 张图待处理
④ 向量化 …（缓存命中 0 / 新算 5）
出错：RuntimeError: Qdrant PUT /collections/mmkb/points?wait=true
      -> HTTP 404: {"status":{"error":"Not found: Collection `mmkb` doesn't exist!"}}
```

**根因**：`kb init` 之外没有任何地方建集合。

- `multimodal_kb/cli/main.py:437` `cmd_ingest()` **没有**调用 `pipe.init_store()`
- 对比：`scripts/ingest.py:78` **有**调用 `pipe.init_store()` —— 说明"入库自动建集合"才是本意
- `deploy.sh` / `verify.sh` 也都不建集合

**影响**：引擎跑完解析 + 图片标注 + 向量化（**花钱、花时间**）之后才在最后一步挂掉，
对"拿具体书籍入库"的第一体验非常不友好。

**复现与规避**（已实测）：

```bash
docker compose exec mmkb kb init          # 手动建集合
docker compose exec mmkb kb ingest /data/book.pdf --doc-id book-1   # 再跑，⑤ 入库：2 个点 ✅
```

**建议修复**：`cmd_ingest` 里加一行 `pipe.init_store()`（幂等，已存在时只打印"集合已存在"）；
或在 `deploy.sh` 健康检查后直接 `kb init`。两条都做更稳。

---

### 🟠 P2 —— 部署自检会"假绿"：集合不存在也报 4/4 通过

首次部署时 `verify.sh` 输出 **4 项全过**，但服务日志同时在刷：

```
[warn] fusion 路失败: Qdrant POST /collections/mmkb/points/query -> HTTP 404: ... doesn't exist!
[warn] text 路失败:   Qdrant POST /collections/mmkb/points/query -> HTTP 404: ... doesn't exist!
```

原因有三层，叠起来让"健康"完全失真：

1. `search/hybrid.py:71-93` 每一路异常都被 `except` 吞掉，只把该路置空；
2. `service/app.py` 的 `/search` 在没有召回时照样返回 **HTTP 200 + `results: []`**；
3. `verify.sh` 的"检索接口返回结果"只判 `curl -sf`（即 HTTP 200），不判结果条数。

`/health` 同样不可靠：`points` 取自 `store.count()`，异常被 `except: pass` 吞掉 → 即使
Qdrant 挂了或集合不存在也返回 `{"status":"ok","points":0}`。

**影响**：部署"看起来成功"，但检索其实一路都没通；排查时要先翻容器日志才发现。

**建议修复**：
- `deploy.sh` 健康检查通过后自动 `kb init`；
- `verify.sh` 增加一条"集合存在"断言（`curl -sf localhost:6333/collections/mmkb`），
  并把"检索有返回"与"库是空的"区分开；
- `/health` 增加 `qdrant_reachable` / `collection_exists` 字段。

---

### 🟠 P3 —— 容器以 root 写挂载目录，宿主侧 `kb ingest` 与 `make distclean` 都会失败

检索服务容器是 **uid 0**；Qdrant 容器也是。

```
$ docker compose exec mmkb id
uid=0(root) gid=0(root)

$ ls -l data/work/
-rw-r--r-- 1 root root 217280 embed_cache.json
-rw-r--r-- 1 root root    633 image_desc_cache.json
```

`deploy.sh` 只处理了**反方向**的坑（`data/` 被 root 先建出来），没处理容器运行后产生的 root 文件。
实测两个后果：

1. **宿主侧 `./kb ingest`（文档 §3.4「方式 B：直接跑」）失败**：

   ```
   ④ 向量化 …（缓存命中 4 / 新算 1）
   出错：PermissionError: [Errno 13] Permission denied:
         '/home/neooooki/multimodal-kb/data/work/embed_cache.json'
   ```

2. **`make distclean`（或手动 `rm -rf data/qdrant/*`）失败**：

   ```
   rm: cannot remove 'data/qdrant/collections/mmkb/config.json': Permission denied
   ...
   ```

**建议修复**：compose 里给两个服务加 `user: "${UID:-1000}:${GID:-1000}"`；
或把已有的 chown 一行命令补进 deployment.md（现在只有 data/ 被 root 预建的那一种场景），
并让 `distclean` 先 `--down` 再用一次性容器 chown。

---

### 🟡 P4 —— 宿主机 `kb` 命令不会读 `deploy/.env`

README「检索」一节紧接 Docker 部署给出 `./kb search "..."` / `./kb eval` / `./kb demo`，
但这些命令只读环境变量，不读 `deploy/.env`：

```
$ ./kb search "焊接"
缺少 DASHSCOPE_API_KEY（检索需要对 query 做向量化）
  设置： export DASHSCOPE_API_KEY=sk-xxx
```

必须自己 `set -a; . deploy/.env; set +a`。错误提示本身很清楚，属于文档缺口。

**顺带**：向量缓存的 fusion key 里带了图片**绝对路径**（`pipeline.py:187`），
所以容器内入库、宿主侧再入库时缓存**必然不命中**（实测多算了 1 次）。
跨环境切换会让"有缓存"的省钱效果打折。

---

### 🟡 P5 —— Docker 部署下 Dify External Knowledge 适配器不可用（信息级）

镜像只 `COPY multimodal_kb`，容器内 `PROJECT_ROOT` 解析为
`/opt/venv/lib/python3.12/site-packages`，`integrations/dify/external_kb.py` 根本不在镜像里：

```
$ docker run --rm --entrypoint sh mmkb/service:latest -c 'find / -name external_kb.py'
（无结果）
$ curl -X POST localhost:8088/v1/retrieval ...   → HTTP 404
```

而且 compose 的启动命令也没有 `--enable-dify-adapter`。
`docs/api.md:132` 只说"启动时加 `--enable-dify-adapter` 才挂载"，没提 Docker 下不可行。

**缓解**：`docs/chat-integration.md` 本来就推荐用 **Dify HTTP 请求节点**（`GET /search?...&format=text`），
那条路在容器部署下是通的（本次已验证 GET 接口 200）。若确实要用适配器，
需在 Dockerfile 里带上 `integrations/` 并在 compose 里覆盖 command。

---

### ⚪ P6 —— 小瑕疵

`deploy/.env.example` 里 `MMKB_PORT` 定义了两次（第 26 行、第 43 行），值相同，无害但容易看糊。

---

## 4. 本次测试中与项目无关的环境因素

- 部署前 `:8088` 上有一个**残留的宿主机进程**
  （`python3 -m multimodal_kb serve --port 8088 --enable-dify-adapter --adapter-token mmkb-local`，
  它连不上 Qdrant，检索恒为空）。已按约定停掉，未影响本次测试结论。
- 首次执行 `./deploy.sh` 时，`docker compose up --build` 因 buildx 需要写
  `~/.docker/buildx/` 而被文件沙箱拦下：

  ```
  failed to update builder last activity time: open .../buildx/activity/.tmp-... : permission denied
  ```

  这是**测试沙箱**的限制，不是项目问题（放开权限后同一命令一次通过）。
  等价绕行：`DOCKER_BUILDKIT=0` 走 legacy builder，同样构建成功。

---

## 5. 给"下一步入库具体书籍"的建议顺序

```bash
# 0) 宿主机侧要用 kb 命令，先加载环境
set -a; . deploy/.env; set +a

# 1) 确认集合存在（本次已代为创建，空库）
docker compose -f deploy/docker-compose.yml exec mmkb kb init

# 2) 放书并入库（doc-id 固定 → 重复入库幂等）
cp 你的教材.pdf data/
docker compose -f deploy/docker-compose.yml exec mmkb \
  kb ingest /data/你的教材.pdf --doc-id book-1

# 3) 验证
curl -s localhost:8088/health
docker compose -f deploy/docker-compose.yml exec mmkb kb docs
curl -s -X POST localhost:8088/search -H 'Content-Type: application/json' \
  -d '{"query":"你的问题","top_k":5,"rerank":true}'
```

> 目前 `data/work/` 是空的（无缓存）；第一次正式入库会真实调用 embedding / VLM，
> 100 页教材大致 4 分钟、成本约 0.1 元（与文档一致）。

---

# 附：验证与修复记录

> 由项目作者逐条复现并修复。**报告的 6 条全部属实**，另外在修复过程中又发现 4 个新问题。

## 报告各条的核实结果

| 编号 | 是否属实 | 核实方式 | 修复 |
| --- | --- | --- | --- |
| P1 🔴 `kb ingest` 不建集合 | ✅ 属实 | 对比 `cmd_ingest`（无）与 `scripts/ingest.py:74`（有） | `cmd_ingest` 加 `pipe.init_store()`；`deploy.sh` 健康检查后再兜一次 `kb init` |
| P2 🟠 自检假绿 | ✅ 属实 | `verify.sh` 只判 `curl -sf`；`/health` 里 `except: pass` | `/health` 改为如实上报（集合缺失返回 **503** + `collection_exists:false`）；`verify.sh` 重写为分层判定，区分"链路通"与"库为空" |
| P3 🟠 容器以 root 写挂载目录 | ✅ 属实 | `docker exec ... id` → `uid=0` | compose 加 `user: "${MMKB_UID}:${MMKB_GID}"`，`deploy.sh` 负责 export；`distclean` 改用一次性容器清理 |
| P4 🟡 宿主 `kb` 不读 `deploy/.env` | ✅ 属实 | 读 `kb` 启动器源码 | 启动器改为自动加载 `./.env` 与 `./deploy/.env`（已存在的变量不覆盖） |
| P4b 🟡 缓存 key 含绝对路径 | ✅ 属实 | `pipeline.py:187` 传的是 `str(first)` | 改为 **相对路径 + 图片内容哈希** |
| P5 🟡 Docker 下 Dify 适配器不可用 | ✅ 属实 | 镜像里 `find / -name external_kb.py` 无结果 | Dockerfile 带上 `integrations/`；新增 `MMKB_ENABLE_DIFY_ADAPTER` 环境变量开关 |
| P6 ⚪ `MMKB_PORT` 重复定义 | ✅ 属实 | `deploy/.env.example` 第 26/43 行 | 去重 |

## 修复过程中新发现的 4 个问题

| 编号 | 问题 | 影响 | 修复 |
| --- | --- | --- | --- |
| **N1** 🔴 | **镜像里没有 `curl`**，而 MinerU 上传用了 `curl` | Docker 部署下**首次入库必然在解析阶段失败**（报告因复用了解析产物而未触发） | 用标准库 `http.client` 裸发请求（本来就需要精确控制请求头，顺带去掉了外部命令依赖） |
| **N2** 🔴 | 融合向量失败时**回退成 1024 维文本向量** | 塞进 2560 维的 `fusion` 槽位 → Qdrant 报 `Vector dimension error`，**把真实失败原因掩盖了** | 失败就跳过该路，不回退；错误信息不再截断 |
| **N3** 🟠 | 传给 `fusion_one()` 的是 **`ImageRef` 对象而不是路径** | `str(ImageRef)` 变成 dataclass repr → DashScope 报 `Image URL or Base64 is invalid` | 传 `root / first.path`；无图时传 `None` |
| **N4** 🟠 | 复用解析产物前**不校验完整性** | 上次失败留下的空目录会被当成有效产物，报 `找不到 content_list*.json`，误导排查 | 复用前检查目录里确实有 `*_content_list*.json` |
| **N5** 🟠 | **46 个源码文件权限是 600** | 镜像里非 root 用户读不到（N1 之后的第二道坎）；本地构建受影响 | 全仓库归一化（目录 755 / 文件 644 / 脚本 755），Dockerfile 加 `chmod -R a+rX` 兜底 |

> N1/N3/N5 都只在**真正干净的部署 + 完整入库**路径上才会暴露 ——
> 报告的测试因为复用了解析产物，恰好绕过了 N1 和 N3。
> 修复后已用「全新 data 目录 + 全新 Qdrant + 完整入库」重跑验证通过。

## 修复后的验证

| 验证项 | 结果 |
| --- | --- |
| 全新部署 → `/health` | 集合不存在时 **503** + `collection_exists:false`（不再假绿） |
| 全新部署 → `kb ingest` | **自动建集合**，解析→标注→向量化→入库全程无错 |
| 三路向量落库 | `fusion` 2560 dense / `text` 1024 dense / `image` 1×2560 multivector |
| 容器用户 | `uid=1000`（非 root） |
| 容器内 `curl` | 不存在，但已不依赖 |
| Dify 适配器（容器内） | `MMKB_ENABLE_DIFY_ADAPTER=1` → `/v1/retrieval` 返回 **200** |
| `verify.sh` | 集合存在 **7/7 通过、退出码 0**；集合缺失 **2 项失败、退出码 1** |
| 冒烟测试 | **23/23 通过**（适配器未挂载时优雅跳过而非失败） |

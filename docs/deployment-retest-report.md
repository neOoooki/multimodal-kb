# 部署测试报告 · 第二轮（修复后复测）

> 对象：commit `2e5f889`（"修复部署测试报告发现的 6 个问题 + 4 个连带问题"）
> 方式：**清空环境 + 全新 `git clone`** → 代码审计 → 按文档 `./deploy.sh` 重新部署
> 结论：**修复方向都对，但 `./deploy.sh` 在"真正全新"的环境下起不来** —— 两个修复互相锁死了。

---

## 0. 本次做了什么

| 步骤 | 说明 |
| --- | --- |
| 清空 | 停掉残留 `kb serve` 进程；`compose down`；删 `mmkb-service` / `mmkb-qdrant` 容器与 `mmkb/service:*` 镜像 |
| 重拉 | `git clone git@github.com:neOoooki/multimodal-kb.git` 到全新目录，替换整个工作区（旧的用一次性容器清理，因为里面有 root 属主文件） |
| 审计 | 逐文件读 `838c49b..2e5f889` 的 diff + 关键文件的最终状态 |
| 部署 | 全新 `data/`（只有我切好的 50 页 PDF），密钥写入 `deploy/.env`，执行 `./deploy.sh` |
| 切页 | `硬件电路设计与电子工艺基础（第2版）.pdf` 317 页 → 切前 50 页 → `data/test-book-50p.pdf`（13 MB） |

---

## 1. 🔴 R1 —— `./deploy.sh` 在全新环境必然失败（本次部署的实测结果）

```
4/6 构建并启动
  ✅ 容器已启动
5/6 等待健康检查
  ❌ 检索服务 120 秒内没起来，查看日志：docker compose logs mmkb
EXIT=1
```

**根因是两个修复撞车：**

| 位置 | 行为 |
| --- | --- |
| `multimodal_kb/service/app.py:174-196` | 集合不存在时 `/health` **返回 503**（P2 的修复，本身是对的） |
| `deploy/deploy.sh:120-125` | 轮询 `/health` 等 **HTTP 200**，40×3 秒后 `die` |
| `deploy/deploy.sh:129` | 建集合的 `kb init` **被放在这个轮询之后** |

于是形成死锁：**集合要 `kb init` 才存在 → `kb init` 要等轮询通过 → 轮询要集合存在才给 200。**
全新环境下永远走不到第 129 行。

实测证据：

```
$ curl -s -i localhost:8088/health | head -1
HTTP/1.0 503 Service Unavailable
$ curl -s localhost:8088/health
{"collection":"mmkb","qdrant_reachable":true,"status":"degraded","points":0,
 "collection_exists":false,"error":"集合 mmkb 不存在或不可读：... HTTP 404 ..."}

$ docker compose ps
mmkb-qdrant    Up 2 minutes (healthy)
mmkb-service   Up 2 minutes (unhealthy)     ← compose 健康检查同样要求 200
```

**连带影响**：`--with-chat` 时 Open WebUI 有 `depends_on: mmkb: service_healthy`，
在全新环境下**永远起不来**。

**为什么上一轮作者复测没发现**：只有在"库里还没有集合"时才触发。
重复执行 `deploy.sh`、或 `data/qdrant` 里已有集合时，`/health` 直接 200，整条路是通的 ——
所以"全新部署"和"再跑一次"必须分开测。

**建议修复**（任选，建议前两条都做）：

```bash
# ① 把建集合挪到 up -d 之后、健康轮询之前（容器刚起，exec 可能要重试几次）
$DC up -d --build
for i in $(seq 1 20); do $DC exec -T mmkb kb init >/dev/null 2>&1 && break; sleep 2; done
# ② 轮询把 503 也当作"服务已就绪但需要初始化"来容忍
#    code=503 时继续（等待 ① 建好集合），只有连不上才重试
```

顺带：`docs/deployment.md:89-107` 的步骤清单和"成功输出（通过 4 项）"也还是旧的，
没有提"自动 kb init"这一步。

---

## 2. 🟠 R2 —— `make distclean` 的修复无效，而且变成了「删一半还报成功」

`Makefile:65` 的判定：

```make
@if [ -d data/qdrant ] && [ ! -w data/qdrant ]; then     # ← 判错了对象
  docker run --rm ... alpine sh -c "rm -rf /d/qdrant/* ..."
else
  rm -rf data/qdrant/* 2>/dev/null || true               # ← 实际走这里
fi
```

`data/qdrant` **目录本身一直是宿主机用户的**（Qdrant 只是往里写文件），所以 `-w` 为真，
永远走 `else` 的直接 `rm`。而 root 属主的是**里面的子目录**：

```
$ ls -ln data/qdrant/
drwxr-xr-x 2 0 0 aliases        ← root
drwxr-xr-x 3 0 0 collections    ← root
-rw-r--r-- 1 0 0 raft_state.json
```

`rm -rf` 的删除权限看**父目录**是否可写：`data/qdrant` 可写 → 能删掉 `raft_state.json`；
但 `collections/`、`aliases/` 是 root 且 755 → 删不动，静默失败（`2>/dev/null || true`）。

实测（用 `make` 不可用，逐行照抄 recipe 执行）：

```
  (else 分支：直接 rm)
已清空运行时数据

raft_state.json survived? NO - deleted
aliases/data.json survived? yes
collections/mmkb survived? yes
```

**结果比"删不掉"更糟**：Qdrant 的存储被删掉一部分（`raft_state.json` 没了，集合目录还在），
脚本却报"已清空运行时数据"。这正是最容易让人误判的状态。

`docs/deployment.md:314` 还写着"`make distclean` 已经用一次性容器来清理" —— 与实现不符。

**建议修复**：判"树里有没有非当前用户的文件"，或者干脆总是走容器：

```make
@if find data/qdrant ! -user "$$(id -u)" -print -quit 2>/dev/null | grep -q . ; then
  docker run --rm -v "$$PWD/data:/d" alpine sh -c "rm -rf /d/qdrant/* /d/qdrant/.[!.]*"
else
  rm -rf data/qdrant/*
fi
```

---

## 3. 🟡 R3 —— MinerU 上传/下载**不再走代理**，而 `_download_direct` 的"退回走代理"是空操作

N1 把 `curl` 换成了 `http.client`（`parse/mineru.py:31-95`）。但
**`http.client` 完全不读 `http_proxy`/`https_proxy`** —— 代理支持在 `urllib` 里。
所以：

- `_raw_request("PUT", ...)` 上传 PDF：即使环境里配了代理，也是**直连**；
- `_download_direct`（`mineru.py:69-95`）先摘掉 `*_proxy` 环境变量"直连"、
  失败后再"退回走代理" —— 第二次调用和第一次**完全等价**（都不走代理），
  摘/恢复环境变量没有任何效果；
- 同一个 `MinerUAPI._req` 里的 API 调用仍然用 `urllib`（走代理）。
  于是表现为"申请上传链接成功（走代理），PUT 上传失败（绕代理）"，症状很迷惑。

旧实现用 `curl`（`curl` 认 `http_proxy`；`--noproxy '*'` 才是显式直连），
所以这是一处**能力回退**：在必须走代理出网的环境里，上传/下载会挂。

**建议**：保留 `http.client` 精确控制请求头的好处，但显式支持代理
（`HTTPSConnection(..., set_tunnel=proxy_host)`，或给 `urllib` 装一个自定义
`ProxyHandler` + 手动去掉 `Content-Type`）；下载也要真的区分"直连 / 走代理"两条路。

---

## 4. 🟡 R4 —— `/health` 每次探测打 3 次 Qdrant，含一次 exact count，而健康检查超时只有 5 秒

修复后的 `/health`（`service/app.py:174-196`）依次调用：
`store.alive()`（GET `/`）→ `store.info()`（GET `/collections/{c}`）→ `store.count()`（`exact: true`）。
compose 健康检查 **每 15 秒一次、超时 5 秒**（`deploy/docker-compose.yml:57-61`，且不可用 `.env` 覆盖）。

空集合实测约 **4 ms**，完全没问题；但 `exact: true` 的计数与两次额外往返会随库增长，
大库（几万点以上）如果超过 5 秒，容器会被判 **unhealthy** —— 而 `--with-chat` 又拿它当门槛。

**建议**：用 `info()` 里已有的 `points_count`（近似值）代替 `exact: true`；
或给健康检查单独一个轻量端点（如 `/livez`）只判进程存活。

---

## 5. 🟡 R5 —— 显式传 `--parsed-dir` 会绕过 N4 新加的"产物完整性校验"

N4 的校验只在 `cmd_ingest` 的**自动探测**分支里（`cli/main.py:451-455`）。
`kb ingest --parsed-dir <dir>`（以及 `scripts/ingest.py --parsed-dir`）走的是显式分支，
直接把目录丢给 `find_content_list`，仍然报那个误导性的
`找不到 content_list*.json`。

本次探针实测：

```
$ docker compose exec mmkb kb ingest /data/test-book-50p.pdf --parsed-dir /data/parsed/__nonexistent__
已创建集合: mmkb
出错：FileNotFoundError: /data/parsed/__nonexistent__ 下找不到 content_list*.json
```

（这条命令顺带验证了 P1 的自动建集合 —— 见第 6 节。）
**建议**：把校验下沉到 `ingest_pdf()` / `parse()` 里解析目录的地方，覆盖两条分支。

---

## 6. 🟡 R6 —— 大文件现在整块读进内存

- 上传：`_raw_request("PUT", url, body=pdf.read_bytes(), ...)` —— 80 MB 的书 → 80 MB 常驻，
  加上 `json.dumps` 的副本；
- 下载：`_download_direct` 先把整个结果 zip 读进 `data` 再 `write_bytes`。

旧实现用 `curl --data-binary @file` / `-o file`，是流式的。
扫描版大书（几百 MB）时内存峰值值得关注（容器没设 memory limit，但宿主 15 GB）。
**建议**：分块 `conn.send()` / 流式写盘。

---

## 7. ✅ 已核实修好的部分

| 原编号 | 修复内容 | 我的核实方式与结果 |
| --- | --- | --- |
| **P1** 🔴 | `kb ingest` 自动建集合 | 删掉集合后跑 `kb ingest`（用不存在的 `--parsed-dir`，不触发 MinerU/向量化）：先打印 `已创建集合: mmkb`，再报 `content_list` 找不到 → **建集合确实发生在解析之前** ✅ |
| **P2** 🟠 | `/health` 如实上报 + 分层自检 | 集合缺失：`/health` → 503 且 `collection_exists:false`；`verify.sh` → **2 项失败、退出码 1**（不再假绿）✅ 集合存在：**4 通过 / 2 跳过、退出码 0** ✅ |
| **P3** 🟠 | 容器非 root | `docker exec mmkb id` → `uid=1000 gid=1000`；容器写出的文件在宿主机是 `1000:1000` ✅ |
| **P4** 🟡 | `kb` 启动器自动读 env | 清空环境变量后 `./kb status` → `DASHSCOPE_API_KEY 已设置` ✅ |
| **P5** 🟡 | 镜像带 `integrations/` + 环境变量开关 | 一次性容器 `MMKB_ENABLE_DIFY_ADAPTER=1` → 日志 `已挂载 Dify 适配器`，`POST /v1/retrieval` 带对 token **200**、错 token **403** ✅ |
| **N1** 🔴 | 去掉 curl 依赖 | 镜像内 `command -v curl` → 无；上传改用 `http.client`（**但见 R3**）⚠️ |
| **N2** 🔴 | 融合向量失败不再回退成 1024 维 | 代码：`c.vector_fusion = None`，`upsert` 跳过缺失向量 ✅（代码级） |
| **N3** 🟠 | `fusion_one()` 收到路径而非 `ImageRef` | 代码：`img_path = (root / first.path) if first else None` ✅（代码级） |
| **N5** 🟠 | 源码权限归一化 | 全新 clone：**279 个 644 + 7 个 755，0 个不可读文件**（唯一的 600 是我自己 `chmod` 的 `deploy/.env`）✅ |
| **P6** ⚪ | `MMKB_PORT` 重复定义 | `deploy/.env` 中只出现 **1 次** ✅ |

> 未独立验证：作者声称的 `verify.sh 7/7`、`smoke_test 23/23` 需要**库里有数据**，
> 而你要求本轮不入库，故留待你入库后复测（`tests/smoke_test.py` 是只读的，可以随时跑）。

---

## 8. 当前环境状态（已给你留好，可直接手动入库）

```
mmkb-qdrant    Up (healthy)   0.0.0.0:6333->6333/tcp
mmkb-service   Up (healthy)   0.0.0.0:8088->8088/tcp     RestartCount=0

$ curl -s localhost:8088/health
{"collection":"mmkb","qdrant_reachable":true,"collection_exists":true,"points":0,"status":"ok"}
```

- 集合 `mmkb` **已建好**（空的，0 点），三个具名向量齐全
- `data/test-book-50p.pdf` —— 你那本 317 页的书切的**前 50 页**（13 MB），已放在容器可见的 `/data/` 下
- `deploy/.env` 已填好两个 key（600 权限，gitignore 覆盖）
- 我没有入库，`data/work/`、`data/parsed/` 都是空的

你要手动执行的那条：

```bash
cd /home/neooooki/multimodal-kb
docker compose -f deploy/docker-compose.yml exec mmkb \
  kb ingest /data/test-book-50p.pdf --doc-id book-1
```

入完后：

```bash
curl -s localhost:8088/health
docker compose -f deploy/docker-compose.yml exec mmkb kb docs
curl -s -X POST localhost:8088/search -H 'Content-Type: application/json' \
  -d '{"query":"你的问题","top_k":5,"rerank":true}'
cd deploy && ./verify.sh          # 此时应 7/7 全过
```

---

## 9. 建议的修复优先级

| 优先级 | 项 | 一句话 |
| --- | --- | --- |
| **P0** | **R1** | `kb init` 挪到健康轮询**之前**；否则全新机器装不上 |
| **P1** | **R2** | `distclean` 判定改成"树里有没有别人的文件"，否则删一半报成功 |
| P2 | R3 | MinerU 上传/下载恢复代理支持（`http.client` 不认 `*_proxy`） |
| P3 | R4 | `/health` 别用 `exact: true`，或放宽 5 秒健康检查超时 |
| P4 | R5 | 完整性校验下沉，覆盖显式 `--parsed-dir` |
| P5 | R6 | 大文件流式上传/下载 |

---

## 10. 本轮的环境备注（与项目无关）

- 本机**没有 `make`**（`make: command not found`），所以 `make distclean` 我是**逐行照抄 recipe**执行的，
  结论等价；README/docs 里的 `make xxx` 在这台机器上暂时跑不了。
- 本机没有 `qpdf`/`pdftk`/`poppler`；切页是用 `pip install --target /tmp/pdflib pypdf` 做的，未污染仓库。
- 上一轮的 buildx 写 `~/.docker` 受限问题本轮未复现（会话已是 full-access），`./deploy.sh` 的构建一次通过。

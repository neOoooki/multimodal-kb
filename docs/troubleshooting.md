# 排错手册

先跑这个，它会逐项体检并给出可复制的修复命令：

```bash
./kb doctor
```

---

## 症状索引

| 症状 | 跳到 |
| --- | --- |
| 服务起不来 / `/health` 不通 | [A](#a-服务起不来) |
| 检索返回空，但库里有数据 | [B](#b-检索返回空) |
| 图片裂图 / 404 | [C](#c-图片显示不出来) |
| 入库失败 | [D](#d-入库失败) |
| 检索结果不相关 | [E](#e-检索结果不相关) |
| 目录权限 `Permission denied` | [F](#f-目录权限问题) |
| 框架（Dify/Open WebUI）连不上服务 | [G](#g-框架连不上检索服务) |

---

## A. 服务起不来

```bash
docker compose -f deploy/docker-compose.yml logs mmkb
# 或不用 docker 时看前台输出
```

| 报错 | 原因 | 修 |
| --- | --- | --- |
| `缺少 DASHSCOPE_API_KEY` | 没配 | `.env` 填 `DASHSCOPE_API_KEY` |
| `Qdrant 不可达` | 地址错 | 容器里**不能用 localhost**，要用服务名（`http://qdrant:6333`）或宿主 IP |
| `Address already in use` | 端口占用 | `ss -lptn 'sport = :8088'` 找占用进程，或改 `MMKB_PORT` |
| `Permission denied: .../data/work` | 目录被 Docker 以 root 建 | 见 [F](#f-目录权限问题) |

---

## B. 检索返回空

按顺序排查：

```bash
# 1) 库里到底有没有数据
curl -s -X POST localhost:6333/collections/mmkb/points/count \
  -H 'Content-Type: application/json' -d '{"exact":true}'
# {"result":{"count":431}}

# 2) 有哪些文档
./kb docs

# 3) 换几个关键词试
./kb search "文档里肯定有的词" -k 3
```

如果 `count` 是 0 → 还没入库，见 [D](#d-入库失败)。

如果 `count > 0` 但一直空：

- **分数阈值太高**：检查调用方有没有传 `score_threshold`，
  我们的分数范围是 0~1，阈值超过 0.9 会滤掉大部分
- **query 是空的**：服务对空 query 直接返回空（这是刻意的，见 [api.md](api.md)）
- **用 Dify 外部知识库**：见 [chat-integration.md](chat-integration.md#22-备选外部知识库有坑不建议)

---

## C. 图片显示不出来

### C1. HTTP 404

```bash
# 直接试这个 URL
curl -I "http://localhost:8088/images/<doc_id>/<file>.jpg"
```

404 说明 `MMKB_PARSED_DIR` 指错了，或解析产物被删了：

```bash
ls data/parsed/*/images/ | head
# 应该有 189 张图
```

没有就重新入库（解析产物会重新生成）。

### C2. 浏览器裂图，但 curl 是 200

**`MMKB_PUBLIC_URL` 用的是 `localhost`，而你从别的机器访问。**

```bash
# 在 .env 里改成宿主可达地址
MMKB_PUBLIC_URL=http://192.168.1.100:8088
```

改完重启服务。

### C3. 框架里不显示（但直接 curl 正常）

- **Open WebUI**：它只渲染 `![]()`，**不渲染原始 `<img>`**。
  本平台输出的就是 Markdown 语法，如果还不显示，检查框架有没有把 Markdown 转义了
- **Dify**：图片要过 LLM 复述。检查提示词里有没有要求"原样保留图片 Markdown"，
  见 [chat-integration.md](chat-integration.md#21-推荐做法http-请求节点)
- **CSP / 图片白名单**：有些前端默认只允许同源图片

---

## D. 入库失败

### D1. 解析失败

| 报错 | 原因 | 修 |
| --- | --- | --- |
| `MINERU_MODE=api 需要 MINERU_TOKEN` | 没配 token | 填 token，或 `MINERU_MODE=local` |
| `failed to connect to the endpoint` | 网络到不了 mineru.net | 检查代理/防火墙 |
| `Local parse-server is disabled` | 本地 server 没起 | `mineru config set parse_server.local.mode managed` 后**重启 server** |
| `Is a directory` | `--output` 给了目录 | 要给**文件**路径（模块里已处理） |
| `PermissionError` (multiprocessing) | `/dev/shm` 太小 | 容器加 `--shm-size=2g` |
| `Illegal instruction` | CPU 无 AVX2 | 只能用官方 API |

### D2. 向量化失败

| 报错 | 原因 | 修 |
| --- | --- | --- |
| `HTTP 400` + 批量相关 | `text-embedding-v4` 单次最多 10 条 | 代码已自动分批；若自己调要分批 |
| `Throttling.RateQuota` | 超出配额 | 放慢或分批；本平台按块调用并带重试 |
| `Arrearage` | 账户欠费 | 充值 |

### D3. 入库很慢

正常耗时（100 页）：

| 阶段 | 耗时 |
| --- | --- |
| 解析（API） | 15–40 秒 |
| 图片标注（156 张） | 约 5 分钟 |
| 向量化（933 次调用） | 约 4 分钟 |

**第二次跑同一份文档几乎是瞬时的**（向量缓存命中，零 API 调用）。

---

## E. 检索结果不相关

### E1. 先确认 rerank 开着

这是最常见的原因。同一个问题：

```
不开 rerank：  1. 0.0328  1. 手工焊接              ← 泛泛的章节
开 rerank：    1. 0.9033  （5） 焊接的步骤          ← 真正讲步骤的
```

```bash
./kb compare "你的问题"      # 直接看对比
```

### E2. 看分路情况

```bash
./kb explain "你的问题"
```

如果 `fusion` 路和 `text` 路结果差异很大，说明图片语义和正文语义分离得比较开
（通常是正常的）。

### E3. 跑评测看整体

```bash
./kb eval                    # 内置 10 题
./kb eval -f 你的题集.json    # 自己的题集
```

题集格式：

```json
{"questions": [
  {"q": "手工焊接的步骤是什么？", "expect": ["三步操作法"]}
]}
```

`expect` 里的关键词**全部出现**在 top-k 召回内容中才算命中。

### E4. 真的是分块问题

如果某个知识点总是召不到，检查它有没有成为独立子块：

```bash
./kb search "那个知识点的原话" -k 5 --width 400
```

看到的分块如果被截断在小节中间，说明 `MMKB_MAX_CHUNK_CHARS` 偏小或标题识别有问题。

---

## F. 目录权限问题

**症状**：`Permission denied: .../data/work`

**原因**：Docker 以 root 建了 `data/`，宿主机用户写不进去。

**修**：

```bash
docker run --rm -v "$PWD/data:/d" alpine chown -R $(id -u):$(id -g) /d
```

**预防**：先用宿主用户建好 `data/{qdrant,work,parsed}` 再让 Docker 挂载
（`deploy.sh` 已经这么做了）。

---

## G. 框架连不上检索服务

**第一步永远是**：从框架容器里试

```bash
docker exec <框架容器> curl -s http://172.17.0.1:8088/health
```

失败的话，逐个排查：

| 原因 | 检查 | 修 |
| --- | --- | --- |
| 用了 `localhost` | 容器里的 localhost 是容器自己 | 用宿主 IP 或 compose 服务名 |
| 宿主 IP 不对 | `ip route \| grep default` | Linux 通常 `172.17.0.1`；compose 网络里用服务名 |
| 防火墙 | `sudo ufw status` | 放行 8088 |
| 服务只监听 127.0.0.1 | `ss -lptn 'sport = :8088'` | 启动时 `--host 0.0.0.0` |
| Dify 的 SSRF 拦截 | 注册时报 `failed to connect` | `.env` 加 `SSRF_PROXY_ALLOW_PRIVATE_IPS=172.17.0.0/16` |

---

## H. 收集诊断信息

报 issue 时带上这些：

```bash
./kb doctor 2>&1
./kb status 2>&1
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs --tail=100 mmkb
python3 -c "import multimodal_kb; print(multimodal_kb.__version__)"
docker --version; docker compose version
```

> **注意**：贴日志前检查有没有把 API Key 贴出去。

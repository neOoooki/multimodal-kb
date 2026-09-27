# HTTP 接口

服务默认监听 `0.0.0.0:8088`，**零第三方依赖**（Python 标准库 `http.server`）。

启动：`./kb serve` 或 `docker compose -f deploy/docker-compose.yml up -d mmkb`

---

## `GET /health`

健康检查。**会如实反映 Qdrant 与集合的状态**，不再"假绿"。

```bash
curl localhost:8088/health
```

集合正常时 —— **HTTP 200**：

```json
{
  "status": "ok",
  "collection": "mmkb",
  "qdrant_reachable": true,
  "collection_exists": true,
  "points": 431
}
```

Qdrant 挂了或集合不存在时 —— **HTTP 503**（便于健康检查/自检脚本直接判失败）：

```json
{
  "status": "degraded",
  "collection": "mmkb",
  "qdrant_reachable": true,
  "collection_exists": false,
  "points": 0,
  "error": "集合 mmkb 不存在或不可读：Qdrant GET /collections/mmkb -> HTTP 404: ..."
}
```

> 早期版本这里用 `try/except: pass` 吞掉异常，Qdrant 挂了也返回 `status: ok`，
> 导致部署自检**假绿**。现在状态与 HTTP 码都如实反映。

---

## `POST /search`

主检索接口。

**请求体**

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `query` | string | 必填 | 查询文本 |
| `top_k` | int | 5 | 返回条数 |
| `rerank` | bool | true | 是否重排（**强烈建议开**，实测能把正确结果从第 5 位提到第 1 位） |
| `query_image` | string | — | 以图搜图时的图片路径（服务端可读） |
| `format` | string | `json` | `json` 或 `text` |

```bash
curl -X POST localhost:8088/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"接地保护示意图","top_k":3,"rerank":true}'
```

**响应**

```json
{
  "query": "接地保护示意图",
  "count": 3,
  "results": [
    {
      "chunk_id": "224087dc-e690-5d2a-a738-069639a25004",
      "content": "【电子工艺实训基础 > 1.5 用电安全技术 > 1. 接地】\n正文…\n![图 接地保护示意图](http://localhost:8088/images/dianzi-100/931c….jpg)",
      "score": 0.9261,
      "section_path": ["电子工艺实训基础", "1.5 用电安全技术", "1. 5.1 接地和接零保护", "1. 接地"],
      "page": 18,
      "doc_id": "dianzi-100",
      "doc_name": "电子工艺实训基础",
      "images": [
        {
          "url": "http://localhost:8088/images/dianzi-100/931c….jpg",
          "path": "images/931c….jpg",
          "caption": "图 接地保护示意图",
          "description": "【图类型】示意图\n【图中文字】…",
          "page": 19
        }
      ],
      "scores_breakdown": {"fusion_rank": 1, "text_rank": 1, "rerank": 0.9261}
    }
  ]
}
```

**字段说明**

- `content` 里的图片已是**绝对 URL 的 Markdown 语法**，直接渲染
- `images[]` 是结构化图片列表，方便做图集
- `scores_breakdown` 用于调试：`fusion_rank`/`text_rank` 是各路排名，`rerank` 是重排分

---

## `GET /search`

给**不支持 JSON body 编排**的工具用（比如 Dify 的 HTTP 请求节点）。

| 参数 | 说明 |
| --- | --- |
| `q` | 查询文本（URL 编码） |
| `top_k` | 返回条数，默认 5 |
| `rerank` | `true`/`false`，默认 true |
| `format` | `json`（默认）或 `text` |

```bash
curl "localhost:8088/search?q=%E6%8E%A5%E5%9C%B0&top_k=3&format=text"
```

`format=text` 返回**纯文本**，图片已内联为 Markdown：

```
[1] 电子工艺实训基础 > 1.5 用电安全技术 > 1. 接地（第 18 页）
【电子工艺实训基础 > … > 1. 接地】
在中性点不接地的配电系统中…

---

[2] …
![图 接地保护示意图](http://localhost:8088/images/dianzi-100/931c….jpg)
```

> **为什么要有 GET**：Dify 的 JSON body 是把变量**原样**塞进 JSON 字符串模板，
> 用户 query 里出现引号或换行就会破坏 JSON。URL 参数由客户端负责编码，稳得多。

---

## `GET /images/{doc_id}/{filename}`

图片直连。前端渲染 `![](URL)` 时实际请求的就是这里。

```bash
curl -I localhost:8088/images/dianzi-100/931c….jpg
# HTTP/1.1 200 OK
# Content-Type: image/jpeg
# Cache-Control: public, max-age=86400
```

> ⚠️ 返回的图片 URL 前缀由 `MMKB_PUBLIC_URL` 决定，那是**浏览器视角**的地址。
> 从别的机器访问要把它改成宿主 IP 或域名。

---

## `POST /v1/retrieval`（可选）

Dify 外部知识库契约。启动时加 `--enable-dify-adapter` 才挂载。

**请求**（Dify 发的）

```json
{
  "knowledge_id": "mmkb",
  "query": "接地保护",
  "retrieval_setting": {"top_k": 5, "score_threshold": 0.0},
  "metadata_condition": null
}
```

**响应**

```json
{
  "records": [
    {
      "content": "…含 Markdown 图片…",
      "score": 0.9522,
      "title": "电子工艺实训基础 > 1.5 用电安全技术 > 1. 接地",
      "metadata": {
        "doc_id": "dianzi-100",
        "page": 18,
        "section_path": ["…"],
        "images": ["http://localhost:8088/images/dianzi-100/931c….jpg"]
      }
    }
  ]
}
```

**行为细节**

- **空 query 直接返回 `{"records": []}`** —— Dify 注册时会发空 query 探测，
  不识别的话会对空串跑一次完整检索
- 设了 `--adapter-token` 时校验 `Authorization: Bearer <token>`，不匹配返回 403
- 推荐改用 HTTP 请求节点而不是这个接口，原因见
  [chat-integration.md](chat-integration.md#2-dify)

---

## 错误码

| 状态码 | 场景 |
| --- | --- |
| 400 | `query`/`q` 为空；图片路径格式错 |
| 403 | Dify 适配器 token 不匹配 |
| 404 | 路径不存在；图片文件找不到 |
| 500 | 检索内部错误（响应体里有 `error` 字段） |

---

## Python 客户端

不想走 HTTP 可以直接用包：

```python
from multimodal_kb import KBConfig, MultimodalKBPipeline, SearchConfig

cfg = KBConfig.from_env()
pipe = MultimodalKBPipeline(cfg)

hits = pipe.searcher(SearchConfig(top_k=5, rerank=True)).search("接地保护示意图")
for h in hits:
    print(h.score, h.section_path, h.images)
```

> 直连模式不会把图片路径改写成绝对 URL —— 那是服务层做的。
> 演示/前端用途建议走 HTTP。

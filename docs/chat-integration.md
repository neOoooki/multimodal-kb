# Chat 应用集成指南

把本平台的检索能力接进 chat 应用。核心只有一句话：

> **你的检索服务是独立的一层，chat 框架只是调用方。**
> 换框架不用动检索，换检索也不用动前端。

---

## 0. 先理解一个关键区别

调研过 Dify / MaxKB / FastGPT / RAGFlow 之后的结论：

| 路线 | 链路 | 问题 |
| --- | --- | --- |
| **传统整栈 RAG 框架** | 检索片段 → 塞进 prompt → **指望 LLM 把图片 markdown 复述出来** → 才能显示 | LLM 可能改写、漏掉、转义图片语法。**链路天然脆弱** |
| **本平台推荐** | 检索服务返回**已含 Markdown 图片的正文** → 代码**确定性地**放进 UI | 图片一定会出现，不依赖 LLM |

MaxKB 官方文档甚至教用户在提示词里补一句"对于已知信息中的图片，必须要在答案中进行输出"
—— 这恰好证明了第一条路有多不可靠。

**所以选框架的标准只有一个：能不能让你把检索产物直接写进 UI。**

推荐顺序：

1. **Open WebUI + Pipe Function** —— ✅ 确定性注入，已验证
2. **Dify + HTTP 请求节点** —— ⚠️ 可行，但图片要过 LLM，见第 2 节
3. **自己的前端** —— 直接调 `/search`，最自由

---

## 1. Open WebUI（推荐）

### 1.1 起前端

```bash
cd deploy
./deploy.sh --with-chat
# Open WebUI 在 http://localhost:3000
```

`--with-chat` 会把 **Pipe Function 一起装好**（1.2 那一步不用手动做）：
起 compose 的 `chat` profile → 建/登录管理员 → 注册并启用 Function → 自检里多两条断言。

或手动（注意 `MMKB_SEARCH_URL` 用宿主 IP 或服务名，**不能用 localhost**）：

```bash
docker run -d --name mmkb-openwebui-manual --restart unless-stopped \
  -p 3000:8080 \
  -e OPENAI_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  -e OPENAI_API_KEY=sk-你的百炼Key \
  -e WEBUI_SECRET_KEY=随便一个固定字符串 \
  -e MMKB_SEARCH_URL=http://172.17.0.1:8088/search \
  -e MMKB_TOP_K=5 \
  -v mmkb-openwebui-data:/app/backend/data \
  ghcr.io/open-webui/open-webui:main
```

> `MMKB_SEARCH_URL` 是**容器视角**的地址。容器里的 `localhost` 是它自己，
> 所以要用宿主 IP（Linux 下通常是 `172.17.0.1`）或者 compose 服务名。

### 1.2 装 Pipe Function

```bash
# 在仓库根目录跑（compose 部署时 OWUI_URL 默认就是 localhost:3000）
python3 integrations/openwebui/install_owui_function.py
```

脚本会：
1. 登录管理员；没有账号就注册（**Open WebUI 的第一个用户自动是 admin**）
2. 把 `pipe.py` 注册成 Function（**已存在则更新内容**，重复执行安全）
3. 确保它是启用状态，并回读一次验证（含"模型下拉里能否看到"）

可覆盖的环境变量：`OWUI_URL`、`OWUI_ADMIN_EMAIL`（默认 `admin@mmkb.local`）、
`OWUI_ADMIN_PASS`（默认 `Mmkb@2026`）、`OWUI_FUNCTION_ID`（默认 `mmkb`）。
已有别的管理员账号时，用它来指定即可：

```bash
OWUI_ADMIN_EMAIL=you@example.com OWUI_ADMIN_PASS='你的密码' \
  python3 integrations/openwebui/install_owui_function.py
```

手动装：Open WebUI → 右上角头像 → **管理员设置 → 函数 → 新建函数**，
把 `integrations/openwebui/pipe.py` 的内容粘进去，保存并启用。

> Open WebUI 的 in-process Function 存在**它自己的数据库**里，只能通过它的 API/管理界面注册 ——
> 把文件丢进容器的 `data/functions/` 目录是不生效的。

### 1.3 用

打开 <http://localhost:3000>，模型下拉里选 **「多模态知识库」**，直接提问。

### 1.4 它做了什么

`pipe.py` 里的 `pipe()` 是异步生成器，逐段 `yield` 文本，Open WebUI 按 Markdown 渲染：

```python
async def pipe(self, body, __user__=None, __event_emitter__=None):
    query = 取最后一条 user 消息
    hits = self._search(query)            # 调我们的 /search
    
    yield "**检索到的相关内容**\n\n"
    yield self.build_context(hits)         # 正文里已含 ![](图片URL)
    
    img_block = self.build_image_block(hits)   # 补上正文没覆盖到的图
    if img_block:
        yield img_block                    # ★ 由代码追加，不经过 LLM
```

**关键点**：图片是**代码写进流的**，不是 LLM 生成的。这是与 Dify 路线的本质区别。

### 1.5 两个必须知道的坑

**坑 1：Open WebUI 只渲染 `![]()`，不渲染原始 `<img>`**

源码里 `HTMLToken.svelte` 只特判 video/audio/iframe/status/file，其余按**纯文本**输出。
本平台的流水线**已经统一输出 Markdown 图片语法**，所以不用改。

**坑 2：不要用它的 "External Knowledge" 连接**

`EXTERNAL_KNOWLEDGE_PROVIDERS` 只支持 `qdrant` / `milvus` / `pgvector`
**直连向量库**，不是任意 HTTP 检索 API。要用 Pipe Function。

### 1.6 验证

仓库里带了端到端验证脚本，**验的不是"容器起来了"，而是这条链路真的通**：

```bash
python3 integrations/openwebui/verify_integration.py
# 默认问「万用表怎么用？」，可用 Q="..." 覆盖
```

它做四类断言：

1. Open WebUI 在跑、Function 已装且已启用
2. 通过 `/api/chat/completions`（`model=mmkb`）走一遍 pipe，拿到非空回答
3. 回答里**不含**原始 `<img>`（Open WebUI 只渲染 `![]()`）
4. 检索服务为该问题命中的**每一张图**都出现在回答里，且 URL 真能取到（HTTP 200）

第 4 条就是"确定性注入"的核心契约。实测输出：

```
1/4 检索服务       ✅ /search 返回 5 条   ✅ 该问题命中 1 张图
2/4 Open WebUI     ✅ 已登录   ✅ Function「多模态知识库」已安装且已启用
3/4 走 pipe        ✅ 拿到回答（1285 字）  ✅ 回答里没有原始 <img>
4/4 确定性图片注入  ✅ 命中的 1 张图全部出现在回答里
                   ✅ 回答里 1 个图片 URL 全部可直连（HTTP 200）
  通过 8 项 —— Open WebUI 集成链路通 ✅
```

> 这条链路曾用无头 Chromium 做过浏览器侧实测（图片确实渲染、无 Markdown 字面量残留），
> 截图见 [evidence/openwebui_rendered.png](evidence/openwebui_rendered.png)
> —— 电路图、LaTeX 公式、章节路径引用都正常。
> 上面的脚本是它的**可重复版本**（不依赖浏览器，CI 友好）。

---

## 2. Dify

Dify 可以用，但有坑。**推荐用 HTTP 请求节点，不要用它的「外部知识库」功能。**

### 2.1 推荐做法：HTTP 请求节点

```
开始 → HTTP 请求（GET /search?q=...&format=text）→ LLM → 直接回复
```

为什么要用 **GET + `format=text`**：

- Dify 的 JSON body 会把变量**原样塞进 JSON 字符串模板**，
  用户 query 里出现引号或换行就会破坏 JSON
- 用 URL 参数由 httpx 负责编码，稳得多
- `format=text` 让服务直接返回干净文本（图片已内联为 Markdown）

节点配置（可直接抄）：

```json
{
  "type": "http-request",
  "method": "get",
  "url": "http://172.17.0.1:8088/search",
  "authorization": {"type": "no-auth", "config": null},
  "params": "q:{{#sys.query#}}\ntop_k:5\nrerank:true\nformat:text",
  "body": {"type": "none", "data": []},
  "ssl_verify": true
}
```

LLM 节点的 `context` 指向 HTTP 节点的 `body`：

```json
{"enabled": true, "variable_selector": ["<http节点id>", "body"]}
```

提示词（**第 2 条很重要**）：

```
你是一位电子工艺实训课程的助教。请严格依据下面检索到的资料回答问题。

资料：
{{#context#}}

要求：
1. 只使用资料中的信息作答；资料没有提到的内容，明确回答「资料未提及」。
2. **资料里的 Markdown 图片语法 ![图注](URL) 必须原样保留在回答里**，
   不要改写、不要省略、不要转义 —— 前端要靠它显示插图。
3. 如果资料中包含表格（HTML），尽量保留其结构。
```

自动化脚本：`integrations/dify/dify_http_node_chatflow.py`

```bash
export DIFY_EMAIL=你的账号 DIFY_PASSWORD=你的密码
python3 integrations/dify/dify_http_node_chatflow.py \
    --app-id <应用ID> \
    --search-url http://172.17.0.1:8088/search
```

### 2.1b Docker 部署下启用适配器（可选）

镜像里已带 `integrations/`，用环境变量开关：

```bash
# deploy/.env
MMKB_ENABLE_DIFY_ADAPTER=1
MMKB_ADAPTER_TOKEN=change-me      # 建议设一个，Dify 注册时会带上
```

然后 `docker compose up -d mmkb`。

验证：

```bash
curl -X POST localhost:8088/v1/retrieval \
  -H 'Authorization: Bearer change-me' -H 'Content-Type: application/json' \
  -d '{"knowledge_id":"mmkb","query":"测试","retrieval_setting":{"top_k":1,"score_threshold":0}}'
```

> 不过**仍然推荐用 HTTP 请求节点**（2.1 节）：
> 它的返回结构更丰富，而且不受 Dify 外部数据集那套隐式约束影响。

### 2.2 备选：外部知识库（有坑，不建议）

如果你坚持用 Dify 的「外部知识库」，至少要知道这些：

**必做一次**：Dify 的 SSRF 防护会拦截本机/私有 IP，在 `dify/docker/.env` 里加：

```bash
SSRF_PROXY_ALLOW_PRIVATE_IPS=172.17.0.0/16
```

然后重启 `ssrf_proxy` 和 `api`。

**必做一次**：创建外部数据集后，`retrieval_model` 是 **NULL**，
会导致 workflow 检索节点**静默返回空**（节点状态还是 succeeded，极难排查）。

```bash
python3 integrations/dify/fix_dify_external_dataset.py \
    --dataset-name "你的外部知识库名"
```

**原因链**：Dify 的 `POST /datasets/external` 不写 `retrieval_model`；
workflow 检索节点把它传给外部服务时是 `None`，
`fetch_external_knowledge_retrieval` 里 `None.get(...)` 抛 `AttributeError`，
异常被 `skip_on_error=True` 吞掉 → 节点"成功"但结果为空。
而**召回测试**显式传了 `external_retrieval_model`，所以它是好的。

**其它坑**（详见 [dify-external-kb-pitfalls.md](dify-external-kb-pitfalls.md)）：

| 坑 | 说明 |
| --- | --- |
| 节点里的检索参数对外部库**无效** | Dify 传的是数据集自己的 `retrieval_model` |
| Dify 可能**二次 rerank** | 选了多个数据集且开了 rerank 时，会对我们排好序的结果再排一次 |
| 注册时发空 query 探测 | 适配器已处理（空 query 直接返回空 records） |

### 2.3 实测结果

改用 HTTP 节点后，Dify 同样能正常渲染：

```
指向检索服务的 <img>: 3，全部加载成功
公式正常排版（KaTeX）
回答里无 '[' 未渲染标记
```

截图：[evidence/dify_rendered.png](evidence/dify_rendered.png)

> **澄清一个常见误解**：Dify 的图片"显示不出来"通常**不是渲染能力问题**，
> 而是检索路径根本没把图片 URL 放进 LLM 上下文。

---

## 3. 自己写前端 / 接任意应用

直接用 HTTP 接口即可，没有任何框架依赖。

### 3.1 检索

```bash
# POST（推荐，支持图片查询等高级参数）
curl -X POST http://localhost:8088/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"接地保护示意图","top_k":5,"rerank":true}'

# GET（给不支持 JSON body 的编排工具用）
curl "http://localhost:8088/search?q=接地保护&top_k=5&rerank=true&format=text"
```

返回（`format=json`，默认）：

```json
{
  "query": "接地保护示意图",
  "count": 5,
  "results": [
    {
      "chunk_id": "224087dc-...",
      "content": "【章节路径】\n正文…\n![图 接地保护示意图](http://localhost:8088/images/doc/xxx.jpg)",
      "score": 0.9261,
      "section_path": ["电子工艺实训基础", "1.5 用电安全技术", "1. 接地"],
      "page": 18,
      "doc_id": "dianzi-100",
      "doc_name": "电子工艺实训基础",
      "images": [
        {"url": "http://localhost:8088/images/dianzi-100/xxx.jpg",
         "caption": "图 接地保护示意图", "page": 19}
      ],
      "scores_breakdown": {"fusion_rank": 1, "text_rank": 1, "rerank": 0.9261}
    }
  ]
}
```

**要点**：
- `content` 里的图片已经是**绝对 URL 的 Markdown 语法**，直接渲染即可
- `images[]` 单独给了结构化图片列表（前端可以做成图集）
- `scores_breakdown` 便于调试和展示

### 3.2 前端渲染建议

| 内容 | 建议 |
| --- | --- |
| 正文 | 标准 Markdown 渲染（`react-markdown` + `remark-gfm`） |
| 公式 | `remark-math` + `rehype-katex` |
| 表格 | `remark-gfm` 直接支持；`content` 里同时有 HTML 表格可兜底 |
| **图片** | `![]()` 语法，**渲染库要允许外部 URL**（CSP/白名单） |
| 溯源 | 用 `section_path` + `page` 显示来源，比只给文档名有用得多 |

### 3.3 最小前端示例（原生 JS）

```html
<div id="out"></div>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script>
async function ask(q) {
  const r = await fetch('http://localhost:8088/search', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({query: q, top_k: 5, rerank: true}),
  });
  const {results} = await r.json();
  document.getElementById('out').innerHTML = results.map(h => `
    <article>
      <header>${h.section_path.join(' > ')}　<small>第 ${h.page} 页</small></header>
      <div>${marked.parse(h.content)}</div>
    </article>`).join('');
}
ask('接地保护示意图说明了什么？');
</script>
```

---

## 4. 以图搜图

用户上传一张图，找相关章节。

```bash
curl -X POST http://localhost:8088/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"","query_image":"/path/to/uploaded.jpg","top_k":5}'
```

> **注意**：`query_image` 目前需要是**服务端可读的路径**。
> 如果前端要传二进制，需要在上层加一个 `/search-by-image` 的 multipart 接口
> （核心能力已就绪，只差 HTTP 层包装）。

命令行：

```bash
./kb image-search 某张图.jpg -k 5
```

实测：拿库里的图搜它自己，**Top-1 命中且分数 1.0000**。

---

## 5. 集成检查清单

接任何框架前后，用这几条确认：

```bash
# 1) 服务活着
curl -s localhost:8088/health

# 2) 检索有结果
curl -s -X POST localhost:8088/search -H 'Content-Type: application/json' \
     -d '{"query":"测试","top_k":1}' | python3 -m json.tool | head

# 3) 图片能直连（拿上一步结果里的 url 试）
curl -sI "http://localhost:8088/images/<doc_id>/<file>.jpg"

# 4) 从**框架容器内**也能访问（这一步最容易挂）
docker exec <框架容器> curl -s http://172.17.0.1:8088/health
```

第 4 步失败 = 框架到不了检索服务，通常是：
- 用了 `localhost`（容器里的 localhost 是容器自己）
- 宿主防火墙挡了 8088
- docker 网络不通（改用同一 compose 网络，用服务名访问）

---

## 6. 还没做的

- `/search-by-image` 的 multipart 上传接口（核心能力已有，只差 HTTP 包装）
- 流式检索（当前是整体返回，检索本身只需 700 ms 左右，暂不需要）
- 多轮对话的检索改写（把上下文并入 query）

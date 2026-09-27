# Dify 多模态知识流水线（DSL 模板）

面向 **Dify 1.17.x** Knowledge Pipeline（知识流水线）的基础模板，导入即用。

多模态的核心不是"多接一个模型节点"，而是让**文档里的图片和文本进入同一个向量空间**。
本模板已按 Dify 1.17 的真实实现把这条链路接好。

---

## 1. 文件说明

| 文件 | 用途 |
| --- | --- |
| `build_multimodal_kb.py` | ⭐ **建库入口**。走**传统索引路径**批量建多模态知识库：拆页 + 控速 + 限流重试，**图片能真正入向量库**。 |
| `chatflow-multimodal-kb.yml` | ⭐ **问答入口**。Chatflow 模板：开始 → 知识库检索(vision) → LLM(视觉模型) → 回复，可图文问答。 |
| `multimodal-file-basic.yml` | 知识流水线模板。文件 → 多模态提取 → 分块 → 视觉向量索引。 |
| `multimodal-file-mineru.yml` | 流水线 OCR 变体。提取器换成 MinerU，适合扫描件 / 图片型 PDF。**需先配置 MinerU 服务**。 |
| `validate_templates.py` | 流水线 DSL 离线校验脚本，导入前自检连线与变量引用。 |
| `_libs/` | 脚本依赖（pypdf），已随项目提供，无需安装。 |

> ⚠️ **先看第 2 节**：Dify 有两套索引实现，本项目的两个 `.yml` 走"知识流水线"，
> 而脚本走"传统索引路径"。**当前版本只有后者能让图片入向量库**，选错路径会白忙一场。

流水线结构（两个 `.yml` 一致，仅提取器不同）：

```
文件上传 ──► 多模态提取 ──► 通用分块 ──► 知识库索引
datasource   dify_extractor  general_chunker   knowledge-index
             (文本+图片)      (文本块)          (视觉 embedding)
```

---

## 2. 两条索引路径：选对路径比调模板更重要

Dify 里其实有**两套索引实现**。它们写同一个 `datasets` 记录、同一套表、同一个向量库，
区别只在**谁负责提取和分块**：

| | 传统索引路径 | 知识流水线 |
| --- | --- | --- |
| `datasets.runtime_mode` | `general` | `rag_pipeline` |
| 触发方式 | `POST /datasets/{id}/documents` | `POST /rag/pipelines/{id}/workflows/published/run` |
| 执行器 | `IndexingRunner.run()`（**写死在 API 里**） | `GraphWorker` 跑**你画的那张图** |
| 提取 | `core/rag/extractor/*`（Dify 内置） | 图里的节点 |
| 分块 | `IndexProcessor.transform()`，参数来自 `process_rule` | 图里的 chunker 节点 |
| 可定制性 | ❌ 只能调分块参数 | ✅ 任意节点组合（MinerU / 父子分块 / 摘要索引…） |
| 界面上对应 | 普通「知识库」 | 「知识流水线」 |
| **图片能否入向量库** | ✅ **可以** | ❌ **不行**（见下） |

**流水线拿不到图片向量**，这一点从两个方向都验证过：

| 提取器 | 能否抽图 | 图片 URL 格式 | 是否需 `current_user` |
| --- | --- | --- | --- |
| 内置 `document-extractor` 节点 | ❌ 1240 行代码无任何图片处理 | — | — |
| 插件 `dify_extractor` | ✅ | `/files/tools/<id>.<ext>` | **需要** |
| 传统路径 `core/rag/extractor/*` | ✅ | `/files/<id>/file-preview` | 不需要 |

而流水线的 `paragraph_index_processor.index()`（第 207 行）调用
`_get_content_files(doc, session=session)` 时**没有传 `current_user`**，
`/files/tools/` 分支被 `if current_user:` 直接跳过。
对比传统路径的 `load()`（第 243 行）会先 `AccountService.load_user(...)` 再传进去。

**结论：要做多模态知识库，请用传统索引路径（本项目的 `build_multimodal_kb.py`）。**
流水线适合不需要图片的场景（纯文本 + 自定义解析链路）。

---

## 3. 导入步骤（知识流水线）

1. 确认插件已安装（本机已装齐，若换环境会自动提示安装）：

   - `langgenius/dify_extractor` `0.1.1`
   - `langgenius/general_chunker` `0.0.14`
   - `langgenius/tongyi` `0.2.22`（提供多模态 embedding 模型）
   - `langgenius/mineru` `0.5.0`（仅 OCR 变体需要）

2. 在 Dify 中新建知识库 → 选择 **知识流水线 / 从 DSL 导入** → 上传 `.yml` 文件。
3. 导入后检查 **知识库索引** 节点的 embedding 模型是否为 `multimodal-embedding-v1`（带 vision 特性）。
4. 确认通义（DashScope）API Key 已在「设置 → 模型供应商 → 通义」中配置，
   并在该供应商下确保 `multimodal-embedding-v1` 可用（Key 从阿里云百炼获取）。
5. ⚠️ **必须点击「发布」**（不是只保存草稿）——见下方说明。
6. 在运行面板调整分块参数 → 上传文件试跑。

> 导入前可先本地自检：`python3 validate_templates.py`

### ⚠️ 导入后必须「发布」，否则多模态不会生效

这是实测才暴露的坑。`is_multimodal` 只在**发布流水线**时写入：

- `publish_workflow()` → `update_rag_pipeline_dataset_settings(has_published=pipeline.is_published)`
- 而保存草稿走的 `sync_draft_workflow()` **完全不会碰** `is_multimodal`

实测对比（同一个库）：

| 操作 | `datasets.is_multimodal` |
| --- | --- |
| 导入 DSL 后 | `false` |
| 保存草稿后 | `false` ← 仍是 false |
| **点击发布后** | **`true`** ✅ |

若停在 `false`，图片会被抽出来、也会存成附件，但**不会写入向量库**，
检索时只能用文本，多模态等于白配。导入后请确认数据库/界面里该库已是多模态状态。

---

## 4. 多模态是怎么生效的（关键机制）

理解这一点，才能正确改模板。链路共 3 步，**缺任何一步都会退化成普通文本知识库**：

**① 提取器把文档里的图片抽出来，并以 Markdown 图片链接写回正文**

`dify_extractor:0.1.1` 在解析 **PDF / PPTX / DOCX / Markdown** 时会把内嵌图片上传到 Dify 存储，
并在正文中插入 `![image](.../files/<id>/file-preview)`。其源码实际输出为：

```python
if result.img_list:
    yield self.create_variable_message("images", result.img_list)
yield self.create_text_message(result.md_content)      # ← 含图片链接的 Markdown
```

**② 分块时图片链接自然留在对应的块里**

`general_chunker` 对正文切片，图片链接跟着所在段落走，因此每个块都知道自己关联了哪些图。

**③ 索引节点用「带 vision 特性的 embedding 模型」把图片也编码成向量**

Dify 在 `paragraph_index_processor` 中会扫描块内的 Markdown 图片链接（`_get_content_files`），
把图片挂成 `AttachmentDocument`；当 embedding 模型带 `vision` 特性时，
`dataset.is_multimodal` 被置为 `True`，并调用 `vector.create_multimodal()` 写入同一向量空间。

> **重点**：`is_multimodal` 由**模型特性自动推导**，DSL 里没有这个开关。
> 换句话说，**换掉 embedding 模型就决定了这条流水线是不是多模态**。

### 本模板使用的视觉 embedding 模型（通义）

默认用 **`langgenius/tongyi/tongyi` + `multimodal-embedding-v1`**，已在本机实例验证 `VISION=True`。

| 模型 | vision 特性 | 说明 |
| --- | --- | --- |
| `multimodal-embedding-v1` | ✅ **本模板默认** | 通义多模态向量，`context_size=8192`，`max_chunks=10` |
| `text-embedding-v1/v2/v3/v4` | ❌ | 纯文本，会静默退化为普通知识库 |

**通义模型的三个实测约束（重要）**：

1. **只接受 `jpeg` / `png` / `bmp` 三种图片格式**，其它格式（gif / tiff / svg / webp）会直接
   `raise ValueError("Unsupported image format")`，导致该文档索引失败。
   所幸 PDF 抽图时 `dify_extractor` 统一转成 PNG，所以 **PDF 最稳**；
   DOCX / PPTX 里的 GIF、TIFF、SVG、EMF 图片存在触发风险。
2. `max_chunks=10`，每批最多 10 个向量。Dify 的 `cached_embedding` 会读取该值分批，
   所以**不会报错，只是图片多时索引稍慢**。
3. **限流是索引大文档的最大瓶颈**（实测踩到）。官方标称 `multimodal-embedding-v1`
   在华北2（北京）为 **RPM 120 / TPM 1,000,000**，但实测约 **39 RPM 就返回 `Throttling.RateQuota`**，
   说明账号实际配额低于标称值。

   根因是**请求数 = 分块数**：通义该接口**单条文本一次请求**，插件在 `embed_documents()` 里
   `for text in texts` 逐条调用，Dify 的批处理帮不上忙。实测数据：

   | 文档 | 分块数 | 结果 |
   | --- | --- | --- |
   | 12 页 PDF | 16 | ✅ 全部成功 |
   | 100 页 PDF | 146 | ❌ 嵌入 12 条后 429，插件仅 `sleep(10)` 重试一次即放弃 |

   缓解办法（按推荐度）：
   - **拆分文件**分批入库（每批控制在几十个分块），这是最省事的做法
   - **调大 `max_chunk_length`**（如 3000~5000）减少分块数，代价是检索粒度变粗
   - 在阿里云百炼控制台**申请提高配额**
   - 换用限流更宽松的向量模型（需具备 `vision` 特性才能保持多模态）

> 若之后要换成其它视觉模型：Jina 系的 `jina-embeddings-v4` / `jina-clip-v2` 同样带 `vision`，
> 改 `embedding_model` / `embedding_model_provider` 与 `dependencies` 即可（需国外支付方式）。

---

## 5. 可调参数

**共享变量**（运行面板统一调整，节点通过 `{{#rag.shared.*#}}` 引用）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `delimiter` | `\n\n` | 块分隔符。写成转义序列是有意的——分块器会用 `codecs.decode(sep, "unicode_escape")` 把它还原成真实换行，**请保持原样**。 |
| `max_chunk_length` | `1000` | 单块最大字符数。 |
| `chunk_overlap` | `200` | 块间重叠字符数。 |
| `replace_consecutive_spaces` | `true` | 合并连续空格/换行/制表符。 |
| `delete_urls_email` | `false` | 删除 URL 与邮箱。 |

> 说明：后两个开关虽然在插件清单中声明，但 `general_chunker 0.0.14` 的实现里**并未读取**它们
> （只有 `input_variable` / `delimiter` / `max_chunk_length` / `chunk_overlap_length` 生效）。
> 保留它们是为了和插件声明的参数表保持一致，改动不会影响分块结果，也不会动到图片链接。

**知识库索引节点**：

- `chunk_structure`：`text_model`（通用分块）或 `hierarchical_model`（父子分块）
- `indexing_technique`：`high_quality`（向量检索，多模态必须）或 `economy`（关键词，**不支持多模态**）
- `retrieval_model`：`top_k` 默认 3、`score_threshold` 默认 0.5

**数据源 `fileExtensions`**：已按提取器**实际支持**的格式对齐，避免"能上传但解析失败"。
基础版不含 `png/jpg`——`dify_extractor` 不能直接解析图片文件，图片是从 PDF/PPTX 内部抽取的；
要直接传图片请用 MinerU 变体。

---

## 6. 前置条件

- **基础版**：插件 + 通义（DashScope）API Key 即可，无需额外服务。
- **MinerU 变体**：还需在「设置 → 工具」中配置 MinerU：
  - `server_type`：`local`（自建服务）或 `remote`（官方 API）
  - `base_url` / `token`
  - 未配置时流水线**可导入但运行会失败**。

### ⚠️ 部署必须配置 FILES_URL（否则整条流水线不可用）

知识流水线的提取器是**插件**，它需要**自己下载**文件，因此 Dify 必须给出**绝对 URL**。
若 `.env` 中 `FILES_URL` 为空，API 会下发相对路径 `/files/...`，插件直接报错：

```
ValueError: Invalid file URL '/files/<id>/file-preview?timestamp=...&sign=...':
Request URL is missing an 'http://' or 'https://' protocol.
```

**注意一个隐蔽点**：`INTERNAL_FILES_URL` 的文档语义是「未设置时回退到 `SERVER_CONSOLE_API_URL`」，
但如果 `.env` 里写了 `INTERNAL_FILES_URL=`（**存在但为空**），pydantic 取到第一个别名的空值
就**不会**再回退。所以请显式填写：

```bash
# ~/dify/docker/.env
FILES_URL=http://localhost          # 外部/浏览器访问地址
INTERNAL_FILES_URL=http://api:5001  # 容器内互访（插件下载文件走这个）
```

改完需重启 `api`、`worker`、`plugin_daemon`。**这个问题与多模态无关，所有基于文件的流水线都会被它挡住。**

---

## 7. 验证情况

模板已用本机运行的 **Dify 1.17.1 真实代码**做过无副作用校验（未写入数据库）：

- ✅ 4 个节点全部通过 Dify 对应的 pydantic 模型校验
  （`DatasourceNodeData` / `ToolNodeData` / `KnowledgeConfiguration` / `KnowledgeIndexNodeData`）
- ✅ 依赖项解析为 `Marketplace` 变体，标识与本机已装插件完全一致
- ✅ `provider_id` / `tool_name` 与插件声明的 identity 一致
- ✅ `tool_parameters`（`mixed` / `variable` / `constant`）类型解析正确
- ✅ 图连线完整，无孤立节点，变量引用均可解析
- ✅ `validate_templates.py` 离线校验通过
- ✅ **在本机租户中实际解析 `langgenius/tongyi/tongyi` + `multimodal-embedding-v1`：
  `features=[ModelFeature.VISION]`，即该库建成后 `is_multimodal` 会为 `True`**
  （对照：`text-embedding-v4` 为 `features=None`）
- ✅ DSL 版本兼容性检查返回 `completed`（无需版本迁移流程）

### 端到端实测（真机跑通）

正确入口是 `POST /rag/pipelines/{id}/workflows/published/run`（前端 `useRunPublishedPipeline` 用它）。
注意 `POST /datasets/{id}/documents` 走的是**传统索引路径**，**不会**执行流水线图。

**① 12 页 PDF 子集（1.1 MB）—— 完整成功**

| 环节 | 实测结果 |
| --- | --- |
| 流水线图执行 | 日志可见 `dify_extractor` / `general_chunker` 的 `dispatch/tool/invoke` |
| 提取 + 分块 | `start embedding 16 texts` → 17 个 segment |
| 文本向量化 | ✅ 向量库 17 个文本对象，文档 `indexing_status=completed` |
| 检索 | ✅ hit-testing 命中，score 0.56 ~ 0.72 |
| 图片 Markdown | ✅ 正文含 `![image](http://localhost/files/tools/<id>.jpg?...)` |
| **图片向量化** | ❌ **未生成**（见下方「已确认的 Dify 缺陷」） |

**② 100 页原 PDF（3.4 MB）—— 提取成功，向量化被限流阻断**

- ✅ 提取/分块正常：146 个 segment
- ❌ 索引阶段 429：`Throttling.RateQuota`（只成功嵌入 12 个分块即被限流，插件等 10s 重试一次仍失败）

### ⚠️ 已确认的 Dify 1.17.1 缺陷：流水线路径下插件图片不会入向量库

对照两条代码路径（`paragraph_index_processor.py`）：

| 路径 | 调用方式 | `/files/tools/<id>.<ext>` 图片 |
| --- | --- | --- |
| 传统 `load()` | `_get_content_files(doc, current_user=account, ...)`（第 243 行，会先 `AccountService.load_user`） | ✅ 能解析 |
| 流水线 `index()` | `_get_content_files(doc, session=session)`（第 207 行，**未传 `current_user`**） | ❌ 被 `if current_user:` 跳过 |

而 `dify_extractor` 插件抽出的图片正是 `http://<FILES_URL>/files/tools/<id>.<ext>` 这种**工具文件 URL**，
必须传入 `current_user` 才能通过 `_download_tool_file()` 转成 `UploadFile`。
流水线路径没传，于是：图片**被正常抽取、Markdown 链接也在**，但 `segment_attachment_bindings` 为空、
向量库里没有 `doc_type='image'` 对象。

实测数据佐证：

| 文档 | 索引路径 | 分块数 | 含图分块 | 附件绑定 | 图片向量 |
| --- | --- | --- | --- | --- | --- |
| `test-multimodal.docx` | 传统路径 | 1 | 1 | **1** | ✅ |
| `电子工艺实训基础-前12页.pdf` | **流水线** | 17 | 2 | **0** | ❌ |
| `电子工艺实训基础_p1-10.pdf` | **传统路径** | 20 | 2 | **2** | ✅ **2** |

最后一行是关键对照：**同一份 PDF**，传统路径建出来的库里向量库有
`20 个文本 + 2 个 image` 共 22 个对象，检索 score 0.65~0.77；流水线建出来的库里
图片向量为 0。

### 完整跑通：100 页 PDF 全量入库

用 `build_multimodal_kb.py`（每批 10 页，批间隔 60s）把整份 100 页 PDF 建库完成，
**全程零限流**：

| 指标 | 结果 |
| --- | --- |
| 批次 | 10/10 全部成功 |
| 文档 | 10 个 |
| 文本分块 | **189** |
| 含图分块 | 55 |
| 附件绑定 | **101** |
| 向量库 | **290 个对象 = 189 文本 + 101 图片** |

也就是说，PDF 里 **101 张图片全部被抽取并写入了向量库**。
知识库名为 `电子工艺实训-多模态`，可在界面上直接查看。

**实践建议**：做多模态知识库请直接用 `build_multimodal_kb.py`（传统索引路径）。
若坚持流水线，可关注 Dify 后续版本是否在 `index()` 中补齐 `current_user`。

---

## 8. 用脚本批量建库（推荐路径）

```bash
cd dify-multimodal-pipeline

export DIFY_BASE_URL=http://localhost
export DIFY_EMAIL='you@example.com'
export DIFY_PASSWORD='...'

# 先看拆分计划（不调用 Dify）
python3 build_multimodal_kb.py 你的文件.pdf --kb-name "多模态库" --pages-per-batch 10 --dry-run

# 正式建库：每批 10 页，批间隔 60s，遇 429 自动退避重试
python3 build_multimodal_kb.py 你的文件.pdf --kb-name "多模态库" --pages-per-batch 10 --delay 60
```

脚本做的事：

1. 登录 → 创建（或复用）**普通知识库**，并把向量模型设为 `multimodal-embedding-v1`
   → Dify 随即算出 `is_multimodal=True`（脚本会打印确认）
2. 按 `--pages-per-batch` 把 PDF 拆成小文件
3. 逐个上传 + 索引，**批次之间 sleep**，避免触发通义限流
4. 遇 `Throttling.RateQuota` 时**指数退避重试**（删掉半成品重新索引）
5. 结束后打印汇总，并给出验证多模态是否生效的 SQL

常用参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--pages-per-batch` | 10 | 每批页数。限流严重就调小 |
| `--delay` | 60 | 批间隔秒数。限流严重就调大 |
| `--chunk-size` / `--chunk-overlap` | 800 / 100 | 分块参数 |
| `--max-retries` | 3 | 限流重试次数（退避为 delay×2ⁿ） |
| `--max-batches` | 0（全部） | 本次只跑前 N 批；**重跑会自动跳过已完成部分**，适合分几次慢慢建 |
| `--dry-run` | — | 只拆页不调 API |

> 断点续跑：脚本按文档名判断是否已索引，因此中断后重新执行会跳过已完成的批次。
> 若想重建某批，先在界面上删掉同名文档即可。

---

## 9. Chatflow 问答模板（图文问答）

`chatflow-multimodal-kb.yml` 是一个 4 节点的 Chatflow：

```
开始 ──► 知识库检索 ──► LLM ──► 直接回复
        (vision: true)  (vision: true)
```

导入方式：**工作室 → 创建应用 → 导入 DSL 文件**。

### ⚠️ 两个必须注意的开关，错一个多模态就静默失效

**① 知识库检索节点 `vision.enabled` 必须为 `true`**
否则检索结果不带出 segment 关联的图片。

**② LLM 节点 `vision.enabled` 必须为 `true`**（最容易漏）
`graphon` 的 `_append_file_prompts()` 里是：

```python
if not vision_enabled or not files:
    return
```

即使检索节点已经通过 `#context_files#` 把图片交给了 LLM 节点，
**只要 LLM 自己的 `vision.enabled=false`，图片就会被静默丢弃**，
模型只能看到正文里的 `![image](...)` 文本链接，于是会回答「我无法访问该图片」。
本模板已把两处都设为 `true`。

### 另一个易错点：变量引用要用「节点 ID」

Answer 节点写 `{{#llm.text#}}` 是**错的**（`llm` 是标题不是 ID），
Dify 会原样输出字符串 `llm.text`。必须写节点 ID，例如 `{{#1764000000003.text#}}`。
本模板已修正。

### 实测结果（针对 100 页知识库）

| 提问 | 传给 LLM 的图片 | 回答表现 |
| --- | --- | --- |
| 手工焊接的步骤是什么？ | 0 | 引用资料作答，并如实说明"资料中未提及具体步骤" |
| 安全用电需要注意哪些事项？ | 0 | 归纳出电流频率/作用时间/电容器等内容，附 3 条引用 |
| **接地保护示意图** | **3 张** | — |
| **接地保护示意图里画的是什么？请按图描述接线方式。** | **1 张** | **准确描述了图中 PCB1~PCB N 地线并联汇集到公共接地点的接线方式** |

最后一行是关键验证：模型**真正看到了图片**并描述了图中内容，
而不是复述正文文字——说明「传统路径建库 → 检索带图 → LLM 视觉理解」这条链路完整可用。

### 模型选择

模板默认 `langgenius/deepseek/deepseek` + `deepseek-flash`（带 vision）。
本机可用的视觉模型还有：

| 供应商 | 模型 |
| --- | --- |
| `langgenius/deepseek/deepseek` | `deepseek-flash`、`deepseek-v4-flash-vision-exp` |
| `langgenius/tongyi/tongyi` | `qwen3-vl-plus`、`qwen-vl-max`、`qwen3.6-plus`、`qwen3.8-flash` 等 27 个 |

换模型只需改 LLM 节点的 `model.provider` / `model.name`。
> `deepseek-flash` 是推理模型，回答里会带 `<think>` 思考段；
> 若不想展示，把 `model.name` 换成 `qwen3-vl-plus` 之类的非推理视觉模型即可。

### 关于 dataset_ids

模板里 `dataset_ids` 写的是你本机的知识库 ID（`2bd89482-...`）。
Dify 的 `decrypt_dataset_id()` 对**明文 UUID 有回退**，所以能直接导入使用；
但换到别的 Dify 实例时该 ID 不存在，需在界面上重新选择知识库。

---

## 10. 后续可扩展方向

1. **解决限流**：`--pages-per-batch` 调小、`--delay` 调大；或在阿里云百炼控制台提配额。
   根因是通义该接口**单条文本一次请求**，请求数 = 分块数，Dify 的批处理帮不上忙。
2. **父子分块**（传统路径同样支持）：建库/加文档时把 `doc_form` 设为 `hierarchical_model`，
   并给出 `subchunk_segmentation`，检索精度通常更好。目前脚本固定用 `text_model`，
   如需可改 `create_document()` 的 `doc_form`。
3. **图片摘要索引**：`summary_index_setting` 可用**视觉 LLM** 为含图块生成摘要。
   > 需要 LLM 带 vision 特性；本机 `deepseek`、`tongyi` 都有视觉模型可选。
4. **提高图片召回**：目前能否看到图取决于**检索是否命中带图的 segment**。
   可把知识点检索节点的 `top_k` 调大（模板默认 4），提高命中带图块的概率。
5. **扫描件 / 复杂版式**：传统路径用的是 Dify 内置提取器，OCR 能力有限；
   这类文档可考虑走流水线 + MinerU，但需接受"图片不入向量库"的现状，
   或等 Dify 修复后切回流水线。


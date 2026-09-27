# AI Chat / Agent 框架选型：替代 / 补充 Dify

> 调研时间：**2026-09-26**（所有 star 数、依赖、版本均为此日快照）
> 背景：多模态教材知识库（MinerU → 两级分块 → 视觉模型图注 → 融合向量入库 → 自建检索服务），现用 Dify 1.17.1
> 核心痛点：① 图片/表格在回答中不渲染 ② 检索层被框架绑死 ③ 需要富文本渲染 + 流式 + 多轮 + API + 轻量 Docker（32GB/无 GPU）+ 宽松许可
> 证据分级：**[源码]** = 我直接读了仓库依赖/源码；**[报告]** = 有人实测并提了 issue/discussion；**[文档]** = 官方文档声称；**[未验证]** = 未能确认

---

## 0. 结论速览

**首选：Open WebUI + 自建检索服务（通过 Pipe Function 接入）。**
它是唯一同时满足「Markdown 图片必渲染 + 后端可用 Python 完全自定义 + 单容器轻量部署」的开源方案。渲染栈是 `marked` + 自研 KaTeX 扩展（支持 `$$`/`$`/`\(\)`/`\[\]`/`\begin{equation}`/`\pu{}`/`\ce{}` 七种定界符，对教材公式最友好）；`![](...)` 走 `<Image>` 组件且允许任意外部 http(s) 地址 **[源码]**。

**必须先纠正一个认知（最关键的架构结论）：**
你现在的痛点**不是"框架渲染能力差"，而是"检索结果被当成了 LLM 的上下文，而不是 UI 的内容"**。Dify / MaxKB / FastGPT 这类整栈框架的链路是：检索片段 → 塞进 prompt → **指望 LLM 把图片 markdown 复述出来** → 才能渲染。这个链路天然脆弱：模型可能删掉、改写、漏掉 URL，且每轮都烧 token。MaxKB 官方知识库指南亲口承认了这一点——它教用户"在提示词里补一句『对于已知信息中的图片，必须要在答案中进行输出』" **[文档]**。所以选型的真正标准是：**框架能不能让你把检索产物直接、确定性地写进 UI**。

**因此推荐路线：自建检索服务 + 轻量 chat 前端，长期解耦。** 不要再用整栈 RAG 框架。

---

## 1. 对比总表

「图片渲染」一列区分两种输入：`![]()` = Markdown 语法；`<img>` = 原始 HTML。

| 框架 | 图片渲染 | 表格 | 公式 | 外部知识库接入 | 许可 | 部署 |
|---|---|---|---|---|---|---|
| **Open WebUI** | `![]()` ✅；`<img>` ❌（按纯文本输出）**[源码]** | ✅ | ✅ 七种定界符 | ✅ 三条路：Pipe Function（全自定义）/ External Document Loader / External Knowledge（**仅 qdrant·milvus·pgvector**）**[源码]** | 自定义，非 OSI；v0.6.6+ 品牌条款，≤50 用户可去品牌 | 单容器（内存占用未实测） |
| **Lobe Chat** | 两者皆 ✅（`rehype-raw`+`rehype-sanitize`）**[源码]** | ✅ | ✅ | ⚠️ 内置知识库 + MCP/插件工具；无"外部检索 API"契约 | LobeHub Community License（Apache-2.0 **+ 衍生作品需商业授权**） | Next.js，中等 |
| **LibreChat** | `![]()` ✅；`<img>` ❌（无 `rehype-raw`）；**MCP 图片有 bug** **[报告]** | ✅ | ✅ | ⚠️ RAG API 是独立 FastAPI（`RAG_API_URL`）**理论可替换**，需自实现其契约 | **MIT** | Docker Compose，中等 |
| **Chainlit** | 两者皆 ✅（`rehype-raw`）**[源码]** | ✅ | ✅ | ✅ 纯 Python，后端 100% 自定义 | **Apache-2.0** | 单 Python 进程，轻 |
| **assistant-ui + streamdown** | ✅ `remend.images` 专治流式未闭合图片 **[文档]** | ✅ | ✅ KaTeX 插件 | ✅ 纯前端库，后端随你 | **MIT** | 无后端 |
| **RAGFlow** | 两者皆 ✅（`rehype-raw`）**[源码]**，但**图片不可检索** | ✅ | ✅ | ❌ 检索层自带，不可替换 | Apache-2.0 | Elasticsearch/Infinity，**≥16GB RAM** |
| **FastGPT** | `![]()` ✅；`<img>` ❌ | ✅ | ✅ | ⚠️ 第三方知识库接口面向"**文件库**"（4 个文件类接口），非检索接口 | Apache-2.0 + 禁多租户 SaaS、禁去 LOGO | Docker Compose，中等 |
| **AnythingLLM** | 基本 ✅（`markdown-it`+DOMPurify） | ✅ | ✅ | ⚠️ 内置 RAG + 可换向量库 + API upsert | **MIT** | 单容器，轻 |
| **MaxKB** | ⚠️ 需靠提示词让模型回显图片 **[文档]** | ✅ | ✅ | ⚠️ 内置 | **GPL-3.0** | Docker，中等 |
| **Dify**（现状） | ⚠️ 有 1.8.0 图片不渲染报告 **[报告]** | ⚠️ 你实测不渲染 | ✅ | ✅ External Knowledge API，契约最标准，但`records` **无图片字段** **[文档]** | 自定义（Apache-2.0 + 附加条款） | 重 |
| NextChat | `![]()` ✅ | ✅ | ✅ | ❌ 无外部检索 | MIT | 轻，但**两月未更新** |
| Cherry Studio / Chatbox | ⚠️ 未验证 | ✅ | ✅ | ❌ 桌面端 | AGPL-3.0 / GPL-3.0 | 桌面 |
| Onyx / Khoj / Verba | ✅ / ✅ / [未验证] | ✅ | ✅ | ❌ 自带连接器与索引 | MIT+ee / AGPL-3.0 / MIT | 重 / 重 / **停滞** |
| Gradio / Streamlit | ⚠️ 需自己塞 image 组件 | ✅ | ⚠️ | ✅ 纯 Python | Apache-2.0 | 轻，但产品化弱 |

**Dify External Knowledge API 的关键局限 [文档]**：你的服务实现 `POST /retrieval`，返回 `records[{content, score, title, metadata}]`。**没有图片字段**，且 `content` 的定位是"作为上下文传给 LLM"。也就是说：即便你接上自建检索，图片能否显示仍取决于 LLM 是否复述——同一个坑。

---

## 2. 逐个展开要点

### 2.1 Open WebUI（首选）
- **渲染**：`marked@9` + `src/lib/utils/marked/katex-extension.ts`（七种数学定界符）+ `DOMPurify` + `highlight.js`/`shiki`。表格由 marked 原生支持。**[源码]**
- **图片**：`MarkdownInlineTokens.svelte` 中 `token.type === 'image'` → `<Image src={token.href} allowExternal={true} />`；`safeImageUrl` 明确放行 `data:`、相对路径与 `allowExternal` 下的任意 `http(s)`。**所以你的图注 URL 只要能浏览器直连就能显示。**
- **但 `<img>` 不渲染**：`HTMLToken.svelte` 先 DOMPurify，然后只特判 `<video>`/`<audio>`/YouTube iframe/通用 iframe/`<status>`/`<file type="html">`/`<br>`，其余落到 `{token.text}` 兜底按**纯文本**输出。**[源码]** → **你的 MinerU 流水线必须把 HTML 图片统一转成 Markdown 图片语法。**（源码级结论，未做运行时实测）
- **引用/溯源**：`Citations/CitationModal.svelte` **import 了 `Markdown.svelte`**，即来源预览弹窗会完整渲染 markdown → 命中片段里的 `![]()` 会出图 **[源码]**。来源列表本身只显示名称 + favicon。
- **接自建检索的推荐姿势**：写一个 **Pipe Function**（Python，`async def pipe(self, body)`，可返回生成器做流式）。它像一个自定义模型，你可以在里面调自建检索、调 LLM，**并确定性地把 `![图注](url)` 追加进流**——不依赖 LLM 复述。注意官方警告：**Pipelines 已是 legacy**，新部署用 in-process Function **[文档]**。
- **上传走自己的解析**：`ExternalDocumentLoader` 会把文件 PUT 到 `{你的url}/process`（可带 Bearer），期望返回 `page_content` + metadata **[源码]** —— 正是你要的"上传走自建流水线"。
- **外部知识连接**：`EXTERNAL_KNOWLEDGE_PROVIDERS = {'qdrant','milvus','pgvector'}`，**只支持直连向量库，不是任意 HTTP 检索 API** **[源码]**，所以别指望它替代 Pipe Function。
- **坑**：① 工具/MCP 返回的图片前端不显示，维护者 tjbck 回复"Image should be returned as a raw string"，但 2026-06 仍有用户称"middleware swallows image data" **[报告]**；② 自定义 RAG 的 citations 元数据传递一直很弱 **[报告]**；③ 许可非 OSI（≤50 用户可去品牌；v0.6.5 及以前纯 BSD-3）**[文档]**。

### 2.2 Chainlit（次选，最省心的"纯后端自定义"）
`react-markdown@9` + `remark-gfm` + `remark-math` + `rehype-katex` + **`rehype-raw`** + `remark-directive` **[源码]** → **Markdown 图片和原始 HTML 图片都能渲染**，这点比 Open WebUI 宽松。Apache-2.0。12,478 star，最近提交 2026-09-18，活跃。缺点是图片以"element"形式挂在消息上，**消息中间插图**曾有问题 **[报告]**；引用/溯源 UI 要自己写；产品化程度弱。

### 2.3 LibreChat / Lobe Chat（可用但都有硬伤）
- **LibreChat**：MIT，渲染栈最"教科书"（`react-markdown` + `remark-gfm` + `remark-math` + `rehype-katex` + `rehype-highlight` + `remark-directive` + `dompurify`），但**没有 `rehype-raw`** → HTML 图片不渲染；且 **MCP 工具返回的图片被渲染成一大串 base64 文本**（issue #9960，官方随后锁了该 issue 的讨论）**[报告]**。RAG API 是独立服务、`RAG_API_URL` 可指向别处，理论可替换为自实现，但契约要自己对齐。44,974 star，非常活跃。
- **Lobe Chat**：`@lobehub/ui` 用 `react-markdown@10` + `remark-gfm` + `remark-math` + **`rehype-raw`** + `rehype-sanitize` + `katex@0.18` + `shiki` **[源码]**，渲染最强之一。但知识库是内置的，没有"Dify External Knowledge"那种外部检索契约；且许可是 **LobeHub Community License**——用作前后端服务可以，**开发并分发衍生作品必须另外拿商业授权** **[源码]**。

### 2.4 RAGFlow（明确不匹配）
渲染栈其实很好（含 `rehype-raw`），但检索层不可替换，且 **issue #8750 明确：图片只做 OCR / VLM 转文本，原始图像不作为检索模态，没有任何配置或插件能开启图文检索** **[报告]**。再加上 Elasticsearch 依赖与 **≥16GB RAM** 的资源要求，与你的融合向量方案和 32GB 单机定位冲突。

### 2.5 自己写前端：assistant-ui + streamdown（值得认真考虑）
`streamdown` 是 Vercel 系为**流式 markdown** 专门做的渲染器：`@streamdown/math`（KaTeX）、`@streamdown/code`（Shiki）、`@streamdown/mermaid`、`@streamdown/cjk`。两个细节对这个场景极有价值：`remend.images` 专门处理**流式中途未闭合的图片语法**；`security.allowedImagePrefixes` / `allowDataImages` 精确控制图片白名单 **[文档]**。MIT。Vercel AI SDK 官方 `ai-chatbot` 模板已切到 `streamdown@2.3` **[源码]**。若你愿意自己写前端，这条路渲染上限最高。

### 2.6 不推荐
NextChat（88k star 但 2026-08-11 后无提交、无外部检索）、Verba（7.7k，停滞于 2026-06）、Chatbox/Cherry Studio（桌面端、GPL/AGPL）、Gradio/Streamlit（要自己塞 image 组件，产品化弱）、MaxKB（GPL-3.0 + 图片靠提示词）、FastGPT（许可禁多租户、第三方知识库面向文件库而非检索接口）。

---

## 3. 明确推荐路线

**首选：Open WebUI（保持 v0.6.x）+ 自建检索服务，用 Pipe Function 对接。**
理由：渲染覆盖你最痛的"图片 + 表格 + 公式"三项且全部有源码/文档依据；后端可 100% 自控；单容器符合 32GB/无 GPU；153k star、当日仍在提交。**落地三件事**：① 流水线把 HTML 图片统一转成 `![图注](url)`；② 图片 URL 用浏览器可直连的地址（别带只能服务端鉴权的 header）；③ 答案流里由你**确定性追加**图片区块，不依赖 LLM 复述。
**代价**：许可非 OSI（你这规模 ≤50 用户可去品牌，合规无碍）；`<img>` 不渲染；自定义 citations 要自己造。

**备选 1：Chainlit**（要 Python 生态、要 HTML 图片也能渲染、要 Apache-2.0 最干净）。
**备选 2：assistant-ui + streamdown 自写前端**（要最高渲染上限与完全掌控，愿意投入前端工时）。
**备选 3：LibreChat**（MIT 且要完整产品形态），但需接受 HTML 图片不渲染 + 自实现 RAG API 契约 + MCP 图片 bug。

**不推荐**：继续在 Dify 上打补丁；改用 RAGFlow / FastGPT / MaxKB。
**共同理由**：它们的架构把"检索结果"定义为 LLM 上下文而非 UI 内容，你的融合向量检索层又必须自控——两边都被绑死。**判断一个框架是否合适，只问一句：检索命中的图片，是"框架帮你渲染"还是"求 LLM 复述"？** 只有前者可用。

---

## 4. 来源与版本说明

- 依赖与源码结论：[Open WebUI main](https://github.com/open-webui/open-webui)、[MarkdownInlineTokens.svelte](https://github.com/open-webui/open-webui/blob/main/src/lib/components/chat/Messages/Markdown/MarkdownInlineTokens.svelte)、[HTMLToken.svelte](https://github.com/open-webui/open-webui/blob/main/src/lib/components/chat/Messages/Markdown/HTMLToken.svelte)、[katex-extension.ts](https://github.com/open-webui/open-webui/blob/main/src/lib/utils/marked/katex-extension.ts)、[safeImageUrl.ts](https://github.com/open-webui/open-webui/blob/main/src/lib/utils/safeImageUrl.ts)、[routers/knowledge.py](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/routers/knowledge.py)、[loaders/external_document.py](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/retrieval/loaders/external_document.py)、[Citations/CitationModal.svelte](https://github.com/open-webui/open-webui/blob/main/src/lib/components/chat/Messages/Citations/CitationModal.svelte)
- Open WebUI 许可：[官方 License 页](https://docs.openwebui.com/license/)
- Open WebUI Pipe/Function：[Pipe Function 文档](https://docs.openwebui.com/features/extensibility/plugin/functions/pipe)、[Pipes（已 legacy）](https://docs.openwebui.com/features/extensibility/pipelines/pipes/)
- 图片渲染实测报告：[Open WebUI #14732（MCP 图片不显示）](https://github.com/open-webui/open-webui/discussions/14732)、[Open WebUI #18059（工具出图）](https://github.com/open-webui/open-webui/discussions/18059)、[自建 RAG citations 局限 #156](https://github.com/open-webui/pipelines/discussions/156)
- Dify：[External Knowledge API](https://docs.dify.ai/en/self-host/use-dify/knowledge/external-knowledge-api)、[#27333 图片不渲染（1.8.0）](https://github.com/langgenius/dify/issues/27333)、[#9120 Image Display Issues（0.9.1，closed as not planned）](https://github.com/langgenius/dify/issues/9120)
- LibreChat：[RAG API](https://www.librechat.ai/docs/configuration/rag_api)、[#9960 MCP 图片不渲染](https://github.com/LibreChat-AI/LibreChat/issues/9960)、[MIT LICENSE](https://github.com/danny-avila/LibreChat/blob/main/LICENSE)
- RAGFlow：[#8750 图文检索不支持](https://github.com/infiniflow/ragflow/issues/8750)、[Apache-2.0](https://github.com/infiniflow/ragflow/blob/main/LICENSE)；内存要求为第三方整理，**未从官方文档逐字核实**
- MaxKB：[官方《知识库图片输出指南》](https://kb.fit2cloud.com/?p=30ba995e-114c-41ee-aa6b-9eb5ac197e4e)、GPL-3.0
- FastGPT：[第三方知识库开发](https://doc.fastgpt.io/zh-CN/guide/dataset/third-party/third_dataset)、[对话框与 HTML 渲染](https://doc.fastgpt.io/zh-CN/guide/chat/htmlRendering)、[LICENSE](https://github.com/labring/FastGPT/blob/main/LICENSE)
- assistant-ui / streamdown：[Streamdown 渲染器文档](https://www.assistant-ui.com/docs/guides/streamdown)、[vercel/streamdown](https://github.com/vercel/streamdown)、[MIT](https://github.com/assistant-ui/assistant-ui/blob/main/LICENSE)
- 许可与活跃度：各仓库 `LICENSE` 原文（2026-09-26 读取）+ star/最近提交来自 [ungh.cc](https://ungh.cc) GitHub 代理，2026-09-26 快照

**未验证项（明确标注）**：各框架图片渲染的**运行时表现**均未实际部署测试，结论基于源码依赖与 issue 报告；Cherry Studio / Chatbox / Onyx / Verba 的图片渲染未逐项核实；RAGFlow 16GB 内存要求仅见第三方整理；各框架内存占用未实测。

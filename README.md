<div align="center">

# multimodal-kb

**可迁移的多模态知识库平台**

把 PDF（教材 / 元器件手册 / 技术文档）变成**可文搜图、可图搜图**的结构化知识库

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](pyproject.toml)
[![Smoke tests](https://img.shields.io/badge/smoke%20tests-23%2F23-brightgreen)](tests/smoke_test.py)

</div>

---

## 它解决什么问题

普通 RAG 平台处理带插图的教材/手册时，有三个硬伤：

| 问题 | 实测数据 |
| --- | --- |
| **切分把知识切碎** | Dify 内置提取器：图注与图同块率仅 **27/55 = 49%**；83/189 个分块内夹带小节号 |
| **跨模态检索基本无效** | 主流多模态 embedding 文→图 Top-1 只有 **40%**；换融合向量能到 **95%** |
| **图片/表格在回答里显示不出来** | 检索结果只当 LLM 上下文，图片靠 LLM 复述，链路天然脆弱 |

本平台的处理方式：

**① 结构化解析 + 两级分块** —— MinerU 拿到带 bbox、图注、表格 HTML、阅读顺序的结构化数据，
自研切分器重建章节树，切成「父块 = 小节 / 子块 = 语义单元」。图注配对率 **100%**。

**② 三路向量并存** —— 一个 chunk 带三个具名向量，其中 `fusion` 是
**正文 + 图融合成的单个向量**（实测跨模态 Top-1 95%）。这是普通 RAG 平台
**存不下**的能力（它们的索引结构固定为「一个文本向量 + 一个图片向量」）。

**③ 检索产物由代码确定性注入 UI** —— 不依赖 LLM 复述图片。

### 实际效果

同一份教材、同一个 LLM、同一套提示词，**只换知识库**：

| 问题 | 传统方案 | 本平台 |
| --- | --- | --- |
| 手工焊接的步骤是什么？ | ❌ 资料中未提及 | ✅ 完整答出 |
| 接地保护示意图说明了什么？ | ❌ 资料中未提及 | ✅ 正确解释 |
| 胸外按压的操作要领？ | ⚠️ 部分未提及 | ✅ 完整答出 |
| 设备通电前做哪三查？ | ❌ 资料中未提及 | ✅ 准确列出 |

召回测试：**10/10 命中**（`./kb eval`）

---

## 快速开始

只需要 **Docker + 两个 API Key**（都有免费额度）：

```bash
git clone <仓库地址> multimodal-kb
cd multimodal-kb/deploy

./deploy.sh                 # 建目录、起 Qdrant + 检索服务、自检
```

第一次运行会生成 `.env` 让你填 Key：

```bash
vim .env                    # 填 DASHSCOPE_API_KEY 和 MINERU_TOKEN
./deploy.sh                 # 再跑一次
```

> - **DASHSCOPE_API_KEY** —— [百炼控制台](https://bailian.console.aliyun.com/)获取，
>   用于向量化 + 图片标注（100 页教材约 0.1 元）
> - **MINERU_TOKEN** —— [mineru.net](https://mineru.net/apiManage/token)获取，
>   用于 PDF 解析（免费 1000 页/天）

### 入库第一份文档

```bash
cp 你的教材.pdf data/
docker compose -f deploy/docker-compose.yml exec mmkb \
  kb ingest /data/你的教材.pdf --doc-id book-1
```

### 检索

```bash
./kb search "接地保护示意图"       # 格式化输出，带图片 URL
./kb eval                        # 召回测试
./kb demo                        # 交互式演示
```

### 接 chat 前端

```bash
cd deploy && ./deploy.sh --with-chat     # 额外起 Open WebUI（:3000）
```

打开 <http://localhost:3000>，模型选「多模态知识库」。

---

## 文档

| 文档 | 内容 |
| --- | --- |
| [**部署指南**](docs/deployment.md) | 从零部署（自动 + 手动逐步）、前置条件、常见问题 |
| [**Chat 集成指南**](docs/chat-integration.md) | Open WebUI / Dify / 自写前端，含提示词与配置 |
| [架构说明](docs/architecture.md) | 为什么这么设计、三路向量、实测数据支撑 |
| [HTTP 接口](docs/api.md) | 接口定义、字段说明、Python 客户端 |
| [排错手册](docs/troubleshooting.md) | 按症状索引的排查流程 |
| [Dify 踩坑](docs/dify-external-kb-pitfalls.md) | 外部知识库的 6 个坑与根因 |
| [调研与实测报告](docs/research/) | 9 份报告：解析器对比、embedding 实测、框架选型…（设计决策的证据来源） |

---

## 命令行工具

```bash
./kb doctor          # 全链路体检，逐项给修复建议 ← 先跑这个
./kb status          # 配置 / 依赖 / 存储 / 文档
./kb docs            # 列出已入库文档

./kb ingest book.pdf --doc-id bk1    # 入库
./kb drop bk1                        # 删除文档

./kb search "问题"                    # 检索
./kb explain "问题"                   # 分路详解：各路召回 + RRF 贡献
./kb image-search 图片.jpg            # 以图搜图
./kb compare "问题"                   # 多配置对比（看 rerank 的作用）
./kb eval                            # 批量召回测试
./kb demo                            # 交互式演示
./kb serve                           # 启动检索服务
```

`kb compare` 最能说明 rerank 的价值：

```
  融合+文本 (RRF)                          融合+文本 +rerank
    1. ██████████ 0.0328  1. 手工焊接        1. █████████░ 0.9033  （5） 焊接的步骤  ← 提到首位
    2. ██████████ 0.0320  3. 3. 4 焊接要领    2. █████████░ 0.8550  1. 手工浸焊
```

---

## 架构

```
  PDF ──► MinerU 解析 ──► 结构重建 + 两级分块 ──► 图片语义标注
                                                      │
                          ┌───────────────────────────┘
                          ▼
         ┌────────────────┬────────────────┬────────────────┐
         │ fusion 2560    │ text 1024      │ image 2560×N   │
         │ 正文+图→1向量  │ 正文+图片描述  │ 每张图各自成向量 │
         └────────────────┴────────────────┴────────────────┘
                          │
                          ▼
             Qdrant ──► 混合检索 + RRF + rerank ──► HTTP 服务
                                                         │
                              ┌──────────────────────────┼──────────────┐
                              ▼                          ▼              ▼
                    integrations/openwebui      integrations/dify   你的前端
```

**核心 `multimodal_kb/` 是独立的 Python 包，不知道任何 chat 框架的存在。**
换框架只写新适配器，核心一行不改。

---

## 目录结构

```
multimodal_kb/          ★ 核心包（零第三方依赖，可 pip install）
├── config.py           所有配置，一律走环境变量
├── models.py           领域模型（完整保留 bbox / 章节树 / 图片对象）
├── pipeline.py         编排：解析 → 分块 → 标注 → 向量化 → 入库
├── parse/              MinerU（官方 API + 本地 CPU）
├── chunk/              结构重建 + 两级分块
├── annotate/           图片语义标注
├── embed/              三路向量 + rerank
├── store/              Qdrant（原生 HTTP）
├── search/             混合检索 + RRF
├── service/            HTTP 服务（stdlib，零依赖）
└── cli/                kb 命令行

integrations/           第三方适配（与核心解耦）
├── dify/               External Knowledge API + 运维工具 + 原始 DSL 模板
└── openwebui/          Pipe Function + 部署脚本

deploy/                 Dockerfile / compose / 一键部署 / 自检
scripts/                运维脚本
tests/                  冒烟测试 + 召回题集
docs/                   文档 + 实测截图证据
experiments/            支撑设计决策的研究实验
```

---

## 环境要求

| 项 | 要求 |
| --- | --- |
| Python | 3.10+（**核心零第三方依赖**，只用标准库） |
| Docker | 20.10+（含 compose） |
| 内存 | 4 GB 起（**本地解析才需要 16 GB**） |
| GPU | **不需要** |

| 组件 | 作用 | 成本 |
| --- | --- | --- |
| Qdrant | 向量库 | 自托管免费 |
| 百炼 `qwen3-vl-embedding` | 融合向量 / 图片向量 | 按量，很低 |
| 百炼 `text-embedding-v4` | 文本向量 | 按量，很低 |
| 百炼 `qwen3-vl-flash` | 图片语义标注 | 100 张图约 0.1 元 |
| 百炼 `qwen3-rerank` | 重排序 | 按量，很低 |
| MinerU | PDF 解析 | 免费 1000 页/天，或本地零成本 |

---

## 已知限制

- **以图搜图目前需要服务端可读的图片路径**，还不支持前端直接传二进制
  （核心能力已就绪，只差一个 multipart 接口）
- **检索是整体返回，不是流式**（检索本身约 700 ms，暂不需要）
- **`kb explain` 里的 RRF 分数**在 0.01~0.03 量级，进度条按相对刻度归一化，不是绝对相关性
- **表格 OCR 质量取决于解析器**，扫描件建议用 MinerU 的 vlm 档

---

## 开发

```bash
pip install -e .             # 可编辑安装，获得全局 kb 命令
make help                    # 查看所有命令
python3 tests/smoke_test.py  # 冒烟测试（需要 Qdrant 与服务在跑）
make test-deploy             # 部署契约测试：干净副本 + 全新安装（不需要 key）
```

> `make test-deploy` 是防"部署回归"的护栏：它在一个独立副本上做一次**全新安装**
> （独立端口与项目名，不碰你正在跑的栈），断言能否收敛、集合是否被自动创建、
> 存活/就绪是否分开、重复执行是否幂等。CI 里也跑它。
> 历史上两次阻断级故障都只在干净环境暴露 —— 所以这条测试是必须的。

核心零第三方依赖是**刻意保持**的：只用 `urllib` / `http.server` / `argparse`，
换环境、进容器、被别的项目引用都不需要装任何东西。

---

## License

[MIT](LICENSE)

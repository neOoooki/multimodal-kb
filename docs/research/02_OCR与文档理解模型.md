# OCR / 文档理解模型技术选型调研报告

**调研时间：2026-09-26　　面向场景：100 页中文电子工艺教材 PDF（电路图 / 示意图 / 表格 / 公式）→ 多模态 RAG**

> 说明：所有评分引自 OmniDocBench **v1.7** 官方榜单（2026-09-11 更新），Overall = ((1−Text Edit)×100 + Table TEDS + Formula CDM) / 3。表中"未验证"指未找到官方或可核查来源，不做推测。

## 一、对比总表

| 模型 | 版式/阅读顺序 | 表格 | 公式 | 元素 bbox | 输出格式 | 中文 | OmniDocBench v1.7 (Overall) | 参数量 | 许可 |
|---|---|---|---|---|---|---|---|---|---|
| **PaddleOCR-VL-1.6** | ✅ 强（PP-DocLayoutV2 定序） | ✅ TEDS 94.76 | ✅ CDM 97.53 | ✅ block_bbox + 多边形(poly)异形框 | MD + 结构化 JSON | 优秀（109 语种） | **96.34**，Text Edit 0.0326，ReadOrder 0.1278 | 0.9B | Apache-2.0 |
| PP-StructureV3 (Pipeline) | ✅ | ✅ | ✅ | ✅ **含单元格级 overall_ocr_res** | MD + JSON | 优秀 | 榜单未单列 | 多模型流水线 | Apache-2.0 |
| PP-OCRv5 / v6 | ❌ 仅检测+识别 | ❌ | ❌ | ✅ 单字坐标 | 文本 + 坐标 | 5 种文字（简繁英日拼） | 不参与（纯 OCR） | 1.5M–34.5M | Apache-2.0 |
| **MinerU2.5-Pro** | ✅ | ✅ 93.42 | ✅ 97.45 | ✅ 稳定的 page/block 定位 | MD + JSON | 优秀 | **95.75**，ReadOrder 0.120 | 1.2B | AGPL-3.0（模型） |
| **dots.mocr**（原 dots.ocr-1.5） | ✅ JSON 逐元素 | ✅ TEDS 87.18¹ | ✅ CDM 89.95¹ | ✅ `[x1,y1,x2,y2]` 每元素 | **单 JSON 对象**（bbox+类别+文本）或 MD | 强 | 90.77¹（dots.ocr） | 1.7B LLM / 3B | MIT |
| **DeepSeek-OCR 2** | ⚠️ grounding 模式 | ⚠️ TEDS 83.89 | ✅ CDM 91.84 | ✅ `<\|det\|>` 坐标标签 | MD / grounding 标签 | 强 | **90.25**，ReadOrder 0.144 | 3B(MoE-A570M) | Apache-2.0 |
| Qwen3-VL-235B | ✅ 2D/3D grounding | ⚠️ 83.07 | ✅ 92.55 | ✅ | MD / 自由文本 | 强（32 语种 OCR） | **89.78** | 2B–235B | Apache-2.0 |
| Qwen-OCR（qwen3.5-ocr / qwen-vl-ocr） | ✅ 含 text localization | ✅ | ✅ | ✅ 坐标在 `ocr_result` | JSON | 强 | 未收录 | 闭源 API | 商用 API |
| Nanonets-OCR2-3B | ❌ 无语义版面 | ✅ HTML+MD | ✅ LaTeX | ❌ **无坐标** | **带语义标签的 MD** | 中等 | 83.61 | 3B | ⚠️ 未声明 |
| GOT-OCR2.0 | ❌ | ⚠️ | ⚠️ | ❌ | MD / LaTeX | 中等 | 已退出榜单 | 580M | 代码 Apache-2.0 / 数据 CC BY-NC 4.0 |
| InternVL3.5-241B | ⚠️ | ❌ 74.35 | ✅ 89.95 | ✅ | MD | 强 | 83.76 | 241B | Apache-2.0 |
| olmOCR-2-7B | ⚠️ ReadOrder 0.216 | ✅ 83.00 | ✅ 88.10 | ❌ 无坐标 | MD | 弱（英文导向） | 85.74 | 7B | Apache-2.0 |

¹ dots.ocr 行取自 OmniDocBench v1.7（3B）；dots.mocr 仅公开 Elo 分，未进入 v1.7 表格。

## 二、逐个展开

### PaddleOCR（v3.7.x 系列）
**PP-OCRv5 / v6 vs PP-StructureV3 vs PaddleOCR-VL 三者边界**：PP-OCRv5（及现行 PP-OCRv6，50 语种）**只是文字检测+识别模型**，单模型支持简中/繁中/英/日/拼音五种文字，精度较上代 +13pp，支持手写体与单字坐标，**不做版面分析**。PP-StructureV3 是**多模型 Pipeline**，把复杂 PDF 转成保留原始结构的 Markdown+JSON，其 `overall_ocr_res` 保留表格单元格级文本与坐标。PaddleOCR-VL 是**两阶段 VLM 方案**：PP-DocLayoutV2 先做版面检测+阅读顺序排序+元素子图裁剪，再由 0.9B VLM（NaViT 动态分辨率编码器 + ERNIE-4.5-0.3B）逐元素识别后按序合并——**必须跑完整流程，单独调 VLM 会严重幻觉**（官方明确警告）。
**版本**：v1 2025-10-16；**v1.5 2026-01-29**（OmniDocBench v1.5 达 94.5%，新增异形框定位、印章识别）；**v1.6 2026-05-28**（v1.6 达 96.3%，结构与 1.5 完全一致可零成本迁移）。
**部署**：官方 Docker 镜像（vLLM / SGLang / FastDeploy 后端）；vLLM 要求 Compute Capability ≥ 8.0、CUDA ≥ 12.6，**T4/V100（CC 7.x）不推荐**（OOM/超时）。**已知坑**：PaddleOCR-VL 的 JSON 不再提供 PP-StructureV3 那样的单元格级坐标；且 bbox 与 PDF 裁剪坐标存在映射换算问题。
来源：[PaddleOCR 文档](http://www.paddleocr.ai/v3.3.2/index.html)、[PaddleOCR-VL 教程](https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.md)、[PP-OCRv6 博客](https://huggingface.co/blog/paddlepaddle/pp-ocrv6)、[issue #17709](https://github.com/paddlepaddle/paddleocr/issues/17709)、[坐标映射讨论](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5/discussions/8)

### DeepSeek-OCR
核心卖点是**上下文光学压缩**：DeepEncoder + DeepSeek3B-MoE-A570M 解码器；压缩比 <10× 时 OCR 精度 97%，20× 时约 60%。仅用 **100 vision tokens** 即超过 GOT-OCR2.0（256 tokens/页），用 **<800 tokens** 超过 MinerU2.0（6000+ tokens/页）；单张 A100-40G 每天可产 20 万+页训练数据。分辨率档位：Tiny 512²/64 tokens、Small 640²/100、Base 1024²/256、Large 1280²/400、Gundam 动态。提示词 `Free OCR.` / `<|grounding|>Convert the document to markdown.` / `Parse the figure.`，**grounding 模式输出带 bbox 的版面标签**。上游 vLLM 自 2025-10-23 官方支持。显存：官方仅给 A100-40G 吞吐，**bf16 约 6–7GB 为社区说法（未验证）**。**DeepSeek-OCR 2 于 2026-01-27 发布**（arXiv:2601.20552），榜单 90.25。
来源：[GitHub](https://github.com/deepseek-ai/DeepSeek-OCR)、[arXiv:2510.18234](https://arxiv.org/abs/2510.18234)、[DeepSeek-OCR-2](https://github.com/deepseek-ai/DeepSeek-OCR-2)

### Qwen 系列
**有专门的 OCR 模型，但是闭源 API**：阿里云百炼的 Qwen-OCR 提供 `qwen3.5-ocr`（Qwen3.5 架构，文档解析、文本定位、证件抽取、PDF 解析，Response API 单次最多 50 页）与 `qwen-vl-ocr` 系列（`qwen-vl-ocr-latest`、`-2025-11-20` 等），内置 `ocr_options` 任务，坐标结果在 `ocr_result` 字段。开源侧 **Qwen3-VL**（2025-09~11 陆续发布 2B/4B/8B/30B-A3B/32B/235B-A22B，Apache-2.0）OCR 语种从 10 扩到 32，原生 256K 上下文可扩至 1M，具备强 2D grounding，但**文档解析并非其专项**——235B 才到 89.78，表格 TEDS 83.07 明显弱于专用模型。
来源：[Qwen3-VL GitHub](https://github.com/QwenLM/Qwen3-VL)、[Qwen-OCR 文档](https://www.alibabacloud.com/help/en/model-studio/qwen-vl-ocr)

### dots.ocr / dots.mocr
基于 1.7B LLM 的多语种文档解析模型（2025-07-30 首发），2026-03-19 更名为 **dots.mocr**（arXiv:2603.13032），另有 dots.mocr-svg 专攻图表转 SVG。**输出最贴合"图片锚定"需求**：`prompt_layout_all_en` 让模型直接吐出一个 JSON 对象，每个元素含 `[x1,y1,x2,y2]` bbox、类别与文本，Picture 类只给框不给文本；也有 `prompt_layout_only_en` 只做检测。vLLM 自 0.11.0 起官方集成（官方推荐 vLLM，transformers 更慢）。**MIT 许可**，商用最友好。
来源：[dots.ocr GitHub](https://github.com/rednote-hilab/dots.ocr)、[dots.mocr 权重](https://huggingface.co/rednote-hilab/dots.mocr)

### Nanonets-OCR2
确有其模型：Nanonets-OCR2-3B（基于 Qwen2.5-VL-3B-Instruct）、1.5B-exp、Plus（API）。特色是**语义标签化 Markdown**——`<img>` 图片描述、`<signature>`、`<watermark>`、`<table>`（同时给 MD 与 HTML）、mermaid 流程图、LaTeX 公式。**但完全不输出坐标**，对"图片与段落锚定"几乎无帮助，且 HF 模型卡未声明许可证（**商用需自行确认，未验证**）。olmOCR-Bench 仅 69.5±1.1，页眉页脚项 32.1 分极低。
来源：[Nanonets-OCR2-3B](https://huggingface.co/nanonets/Nanonets-OCR2-3B)、[官方介绍](https://nanonets.com/research/nanonets-ocr-2/)

### GOT-OCR2.0
580M 统一端到端模型（2024-09），主要输出 Markdown/LaTeX，**无版面分析、无 bbox、无阅读顺序**，已退出 OmniDocBench v1.7 榜单。**许可存在风险**：代码 Apache-2.0，但数据标注 CC BY-NC 4.0（非商用），商用需谨慎。目前仅适合做轻量纯文本 OCR 基线的对照。
来源：[GOT-OCR2.0 GitHub](https://github.com/Ucas-HaoranWei/GOT-OCR2.0)

### 其它值得关注
**InternVL3.5-241B**：文档综合 83.76，表格 TEDS 仅 74.35，性价比低，不建议作为主力。榜单头部还有两个 2026 年新模型值得跟踪：**TeleOCR（1.2B, 96.91）** 与 **OvisOCR2（0.8B, 96.47）**，阅读顺序编辑距离分别为 0.1184 / 0.1120（优于 PaddleOCR-VL-1.6 的 0.1278），但生态与文档成熟度尚未验证。

### 解析工具与底层模型的关系
- **MinerU 4.0**：已升级为四档解析（Flash/Basic/Standard/Advanced），VLM 组件为 **MinerU2.5-2509-1.2B / MinerU2.5-Pro-2605-1.2B**，小模型走 ONNX/Torch，VLM 可选 **llama.cpp、vLLM、LMDeploy**。**默认安装即为 ONNX CPU + llama.cpp Vulkan 模式**，这是少见的可无 NVIDIA GPU 运行的生产级方案。许可证为 MinerU Open Source License（Apache 2.0 + 附加条件），模型为 AGPL-3.0。
- **olmOCR v0.4.0**（2025-10-21）：底层是 **Qwen2.5-VL-7B-Instruct** 微调的 olmOCR-2-7B-1025-FP8，Apache-2.0，vLLM 推理流水线 + 官方 Docker，成本 <$200/百万页，**必须 GPU**；阅读顺序得分是头部模型中最差的（0.216），中文场景不建议。
- **PP-StructureV3** 本身即是工具层，被 RAGFlow、Umi-OCR、OmniParser 等项目引用。
来源：[MinerU GitHub](https://github.com/opendatalab/MinerU)、[olmOCR GitHub](https://github.com/allenai/olmocr)、[olmOCR-2 模型卡](https://huggingface.co/allenai/olmOCR-2-7B-1025-FP8)

## 三、结论与推荐选型

**针对三大目标的最优解**

| 目标 | 首选 | 理由 |
|---|---|---|
| 版面结构 + 阅读顺序 | **PaddleOCR-VL-1.6**（ReadOrder 0.1278）、MinerU2.5-Pro（0.120） | 榜单第一梯队且工程封装完整；TeleOCR/OvisOCR2 分数更高但生态未验证 |
| 图片与段落正确锚定 | **dots.mocr**（逐元素 JSON bbox）或 **PaddleOCR-VL / PP-StructureV3**（block_bbox + 阅读顺序 + 元素子图） | 只有这两类能同时给出"框 + 类别 + 顺序"，可据此把 Picture 块绑定到前一个正文块/最近标题 |
| 表格 + 公式还原 | **PaddleOCR-VL-1.6**（CDM 97.53 / TEDS 94.76）、MinerU2.5-Pro（97.45 / 93.42） | 双项均衡领先；TeleOCR 表格最高（96.82）但公式略低 |

**本地 Docker 部署推荐（含显存建议）**

1. **主方案（有 GPU）**：**PaddleOCR-VL-1.6（0.9B, Apache-2.0）+ vLLM**，官方 Docker 镜像直用。单卡 **≥12GB 显存（RTX 3060 12G / 4070Ti 以上）可跑，16–24GB 更稳**；要求 CC ≥ 8.0、CUDA ≥ 12.6，**避开 T4/V100**。用 `save_to_json()` 拿 block_bbox + 阅读顺序，据此完成图片锚定；需要**表格单元格级坐标**时，额外用 PP-StructureV3 的 `overall_ocr_res` 补齐。
2. **备选/交叉验证**：**dots.mocr（MIT, 1.7B）**，vLLM 0.11.0+ 一键 serve，逐元素 JSON 最省事；**MinerU 4.0** 作为一站式流水线，`mineru[full]>=4.0` 走 GPU，且自带稳定 page/block locator。
3. **无 GPU 方案**：**MinerU 默认安装**（ONNX CPU 小模型 + llama.cpp Vulkan VLM）可零 GPU 跑通；或纯 CPU 用 **PP-OCRv6 / PP-StructureV3** 的 ONNX Runtime 后端（速度慢但精度可接受）。100 页教材量级下，CPU 方案一次性跑完是现实可行的。
4. **RAG 落地建议**：解析产物按 `标题层级 → 正文块 → 图片块（bbox 裁图另存） → 表格（HTML） → 公式（LaTeX）` 结构化，图片向量与"图注文本 + 所属小节标题 + 前后段落"拼接后入库，不要只嵌图片本身——这是解决"图片归属错乱"的关键。

**风险提示**：Nanonets-OCR2 无坐标且许可未声明；GOT-OCR2.0 数据许可 CC BY-NC 4.0 限制商用；MinerU 模型 AGPL-3.0 对闭源分发有传染性。以上均需在选型时二次确认。

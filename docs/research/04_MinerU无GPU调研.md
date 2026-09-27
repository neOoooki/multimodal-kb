# MinerU 纯 CPU（无 NVIDIA GPU）实际效果与速度调研报告

**查询时间**：2026-09-26（UTC）
**涉及版本**：MinerU 4.0.7（最新，2026-09-23 发布）、4.0.0（2026-09-16）、3.4.5（2026-08-14）、2.5.4（2025-09-19）；对照 PaddleOCR 3.7.0 / PaddleOCR-VL 系列

---

## 一、结论速览

1. **能用，且是官方一等公民路径。** MinerU 4.x 的**基础包（base package）默认就是"ONNX Runtime（CPU 小模型）+ llama.cpp（VLM）"**，无需 GPU 即可跑 `flash`/`basic` 档；官方原文："基础包开箱即用：小模型走 ONNX CPU，VLM 使用 llama.cpp Vulkan 模式"（[4.0.0 Release Notes](https://github.com/opendatalab/MinerU/releases/tag/mineru-4.0.0-released)）。你听到的"ONNX + llama.cpp"说法**属实，且专指 4.x**；2.x/3.x 的时代口径是"只有 `pipeline` 后端支持纯 CPU，`vlm-*` 后端必须 GPU"。
2. **多快**：小模型 pipeline 在纯 CPU 上属"可用但慢"。官方/社区可核实的点：16 核 CPU 虚拟机上 **22 页 PDF ≈ 30 秒（≈1.4 s/页）**（[PR #5097](https://github.com/opendatalab/MinerU/pull/5097)）；公式密集页在 x86 CPU 上 **≈16 s/页**，ARM 鲲鹏 920 上 **>300 s/页**（[Issue #2067](https://github.com/opendatalab/MinerU/issues/2067)）。**4.x 用 llama.cpp 在纯 CPU 上跑 VLM（standard 档）的速度未找到可靠实测数据**，按 1.2B VLM 在 CPU 上的常识推断会显著慢于 pipeline，但无实测数字，不编造。
3. **精度**：4.x 的 CPU（ONNX）与 GPU（Torch）走**同一套模型的两种格式**，官方模型包为 `MinerU-4_models_onnx` / `MinerU-4_models_torch`，模型清单一致（PP-DocLayoutV2、PP-OCRv6 Tiny Det + Small Rec、PP-FormulaNet plus-M）。**未找到官方 ONNX vs Torch 的精度对比实测**；官方给出的差异是**后端档位**差异：pipeline ≈86.47 / 86.2，VLM ≈95.30（OmniDocBench）。
4. **内存**：官方要求**最低 16GB、推荐 32GB+**（3.x/4.x 一致）。另有跨文档 RSS 增长缺陷与 `MINERU_MALLOC_TRIM` 修复。
5. **PaddleOCR-VL 在纯 CPU 下"官方支持但明确不建议生产"**：官方矩阵显示 x64 CPU 仅支持 `PaddlePaddle`/`Transformers`/`llama.cpp` 三种推理方式，**vLLM/SGLang/FastDeploy 在 CPU 上为 ❌**；官方文档直言该路径"推理速度可能较慢""未必能满足生产环境要求"。

---

## 二、CPU 可运行性与启用方式（核实结果）

| 版本 | 纯 CPU 支持 | 启用方式 | 来源 |
|---|---|---|---|
| **4.x** | ✅ 基础包默认 ONNX+llama.cpp | 无需参数；`model.small_backend=onnx`、`model.vlm.engine=llama-cpp`；`mineru-kit models download --tier standard --small-backend onnx --vlm-engine llama-cpp` | [tiers.md](https://opendatalab.github.io/MinerU/usage/tiers/)、[extension_modules.md](https://github.com/opendatalab/MinerU/blob/master/docs/en/quick_start/extension_modules.md) |
| **3.x** | ✅ 仅 `pipeline` | `mineru -p in.pdf -o out -b pipeline`，`MINERU_DEVICE_MODE=cpu` | [3.4.5 quick_start](https://github.com/opendatalab/MinerU/blob/mineru-3.4.5-released/docs/en/quick_start/index.md) |
| **2.x** | ✅ 仅 `pipeline` | 同上 | [2.5.4 README](https://github.com/opendatalab/MinerU/blob/mineru-2.5.4-released/README.md) |

**关键坑（3.x，社区实测）**：3.4 的 `DEFAULT_BACKEND` 是 `hybrid-engine`（需 GPU），**CPU 容器上任何不显式传 `backend=pipeline` 的请求都会失败**，且它是模块常量、无法用环境变量覆盖（[mineru-railway](https://github.com/bon5co/mineru-railway)）。**4.x 改为档位制后此问题消失**（`basic` 档明确"可在 CPU 运行"）。

**CPU 指令集要求**：官方 FAQ 明确——报 `Illegal instruction (core dumped)` / `非法指令(核心已转储)` 的原因是"**CPU 不支持 AVX/AVX2 指令集，或支持但被运维禁用**"（[MinerU 2.0.0 FAQ 第 7 条](https://github.com/opendatalab/MinerU/blob/mineru-2.0.0-released/docs/FAQ_zh_cn.md)，由 [PR #867](https://github.com/opendatalab/MinerU/pull/867) 加入）。即 **AVX2 是事实基线**。另有 [Issue #1773](https://github.com/opendatalab/MinerU/issues/1773)：AMD EPYC 上因 PaddlePaddle CPU 版不支持该平台而失败（2.x 时代的 paddle 依赖；4.x 小模型转向 ONNX 后该限制是否解除**未找到官方说明**）。

---

## 三、实测/官方数据表

**A. MinerU 官方精度与硬件要求**

| 项目 | pipeline | VLM | 来源（官方） |
|---|---|---|---|
| 纯 CPU 支持（3.x） | ✅ | ❌ | [3.4.5 文档](https://github.com/opendatalab/MinerU/blob/mineru-3.4.5-released/docs/en/quick_start/index.md) |
| OmniDocBench 精度 | 86.47（v1.6）/ 86.2（v1.5） | 95.30 / 95.39 | 同上 + [3.0.0 Release](https://github.com/opendatalab/MinerU/releases/tag/mineru-3.0.0-released) |
| 最低 RAM | ≥16GB，推荐 32GB+ | 同 | 同上 |
| GPU 要求 | Turing+ / 6GB 显存 | Turing+ / 8GB | 同上 |

**B. MinerU 纯 CPU 速度（社区/官方，均为个别案例，非系统基准）**

| 环境 | 结果 | 换算 | 来源 | 性质 |
|---|---|---|---|---|
| 16 核 CPU VM，Ubuntu 22.04，3.2.3 | 22 页 ≈ 30 s | ≈1.4 s/页，≈0.73 页/s | [PR #5097](https://github.com/opendatalab/MinerU/pull/5097) | 社区 PR 自测 |
| x86_64 CPU（1.0.x，公式密集页） | ≈16 s/页 | — | [Issue #2067](https://github.com/opendatalab/MinerU/issues/2067) | 社区 issue |
| 鲲鹏 920 ARM，纯 CPU | >300 s/页（MFR 阶段） | 比 x86 慢十几倍 | 同上 | 社区 issue |
| 8 核 / 16 核 / 服务器级 对比 | **未找到可靠数据** | — | — | — |

**C. 分阶段开销（MinerU 无官方分阶段 CPU 数据；下表用 PaddleOCR 官方同族模型的 CPU/GPU 单次推理耗时做量级参照，来源：[PaddleOCR 模型列表（CPU/GPU）](https://www.paddleocr.ai/latest/version3.x/model_list.html)，官方）**

| 阶段（对应模型） | GPU 耗时 | **CPU 耗时** | CPU/GPU 倍数 |
|---|---|---|---|
| 版面检测 PP-DocLayout_plus-L | 53.0 ms | **378–635 ms** | ≈7–12× |
| OCR 检测 PP-OCRv5_server_det | 89.6 ms | **383 ms** | ≈4× |
| OCR 识别 PP-OCRv5_server_rec（每行） | 8.5 ms | **31 ms** | ≈4× |
| 公式识别 PP-FormulaNet_plus-M（每 crop） | 1040 ms | **1616 ms** | ≈1.6× |
| 表格结构 SLANet_plus | 23.4 ms | **41.8 ms** | ≈1.8× |
| 表格单元格 RT-DETR-L | 33.5 ms | **402 ms** | ≈12× |

社区侧另有定性证据：公式识别（MFR）是纯 CPU 最大瓶颈，且**多核利用率极低**（32 核机器上公式步骤单核利用率 <25%，[Issue #2241](https://github.com/opendatalab/MinerU/issues/2241)）；表格模型开关会带来十几倍差异（[Issue #926](https://github.com/opendatalab/MinerU/issues/926)）。→ **结论：MinerU 纯 CPU 的耗时主体是公式识别 + 表格/版面检测，且难以靠加核数线性加速。**

**D. 参照：GPU 上的速度（说明 CPU 差距的基线，官方论文）**

| 方案 | 硬件 | 吞吐 | 来源 |
|---|---|---|---|
| MinerU2.5 1.2B（vLLM） | A100 80G | 2.12 页/s（优化后）；0.95 页/s（默认） | [MinerU2.5 论文 Table 3](https://arxiv.org/html/2509.22186v2)（官方） |
| MinerU2.5 | RTX 4090 | 1.70 页/s | 同上 |
| PaddleOCR-VL-0.9B | H800 FastDeploy | 2.225 页/s | [PaddleOCR-VL 论文 Table A2](https://arxiv.org/html/2510.14528v4)（官方） |

**E. 内存（官方 + 社区实测）**

- 官方：**最低 16GB，推荐 32GB+**（[tiers.md](https://opendatalab.github.io/MinerU/usage/tiers/)）。
- 3.0 起引入**滑窗机制**，"显著降低长文档场景的峰值内存，数万页文档不再需要手工切分"（[3.0.0 Release](https://github.com/opendatalab/MinerU/releases/tag/mineru-3.0.0-released)，官方）。
- 社区实测缺陷：长驻 API 跨文档 RSS 单调增长至 OOM；实测 **+0.47 GiB/篇（48 页扫描件）**，设 `MALLOC_TRIM_THRESHOLD_=-1` 或 `malloc_trim` 可压到 **+0.03 GiB/篇**；另有 libgomp 线程阶梯增长（96 核主机曾达约 3000 线程）（[Issue #5313](https://github.com/opendatalab/MinerU/issues/5313)、[PR #5354](https://github.com/opendatalab/MinerU/pull/5354)、[PR #5477](https://github.com/opendatalab/MinerU/pull/5477)）。4.x 已提供 `MINERU_MALLOC_TRIM` 与 `MINERU_INTRA_OP_NUM_THREADS` / `MINERU_INTER_OP_NUM_THREADS`。
- **100 页 PDF 的峰值内存：未找到官方实测数字。** 建议按 ≥16GB 规划，32GB 更稳。

**F. PaddleOCR-VL 无 GPU 情况**

| 项目 | 结论 | 来源 |
|---|---|---|
| x64 CPU 支持 | ✅（PaddlePaddle / Transformers / llama.cpp 三种方式） | [PaddleOCR-VL 文档支持矩阵](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.md)（官方） |
| CPU 上 vLLM / SGLang / FastDeploy | ❌ | 同上 |
| 官方对 CPU 的评价 | "默认模型较大，推理速度可能较慢"；本地直推"未必能满足生产环境要求" | 同上 |
| 高性能服务化（Triton+vLLM） | **仅支持 NVIDIA GPU** | [HPS 部署文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/deploy/paddleocr_vl_docker/hps/README_en.md)（官方） |
| CPU/llama.cpp 具体速度 | 仅见第三方 Jetson 平台 llama.cpp 数据（f16 94 tok/s、Q8_0 176.8 tok/s、Q4_K_M 199.7 tok/s，**未标注硬件、非 x86 纯 CPU**） | [懒猫算力舱 benchmark](https://developer.lazycat.cloud/aipod/benchmark/paddleocr-vl-1.5.html)（第三方，谨慎参考） |
| **PP-StructureV3 + ONNX Runtime 纯 CPU 的端到端页/秒** | **未找到可靠数据**（仅能拿到上表 C 的分模块 CPU ms） | — |

---

## 四、替代/折中方案（免 GPU）

1. **MinerU 官方在线 API**：Flash（轻量）模式**免登录、秒级**，但限 ≤10MB / ≤20 页；Precision（精准）模式支持 `pipeline`/`vlm`/`html`，**≤200MB / ≤600 页**，官网"每日免费 2000 页高优先级额度"（[MinerU 训练营第 01/03 课](https://github.com/opendatalab/mineru-tutorials)，OpenDataLab 官方材料）。对 100 页教材最省事的路径。
2. **本地 CPU 跑 pipeline/basic（小模型）**：4.x 基础包直接可用，精度 ≈86。
3. **本地 CPU 只做 `flash` 档**：PDF 有文本层时**完全不用推理模型**，速度极快，只缺公式/表格的 AI 还原（[4.0.0 Release](https://github.com/opendatalab/MinerU/releases/tag/mineru-4.0.0-released)）。
4. **混合：本地 CPU 做版面/OCR，VLM 走远端**：3.x `vlm-http-client` / `hybrid-http-client` 对接任意 OpenAI 兼容服务（含云上 VLM），这是"没有显卡但要最高精度"的标准做法（[3.4.5 文档](https://github.com/opendatalab/MinerU/blob/mineru-3.4.5-released/docs/en/quick_start/index.md)、[Deployment 课程](https://github.com/opendatalab/mineru-tutorials)）。
5. **阿里云百炼**：本次未查到其文档解析 API 的公开页页级价格与精度评测，**未找到可靠数据**，不做推荐性结论。

---

## 五、针对「100 页中文教材、无 GPU」的可行性判断与建议

**判断：可行，但要走对档位，且必须做小规模实测标定。**

- 该教材含**电路图、示意图、表格、公式**，恰好踩在纯 CPU 最贵的三个阶段（版面、公式、表格）。若用 4.x `basic` 档（ONNX 小模型，无 VLM），按 1.4 s/页的理想值，100 页 ≈ **2.5 分钟**；按公式密集页 16 s/页的外推，可能到 **20–30 分钟**。真实值必须在你的 CPU 上实测——**这是本报告最不确定的一项**。
- **精度代价是真实存在的**：pipeline ≈86 vs VLM ≈95（OmniDocBench）。对教材的**公式 LaTeX 与表格结构化**，pipeline 的落差最明显；而 RAG 恰恰比较依赖公式/表格切块质量。
- **推荐三步走（1–2 天可完成）**：
  1. **用官方 API Flash 模式 + Precision 模式各跑一遍这 100 页**（Flash 限 20 页，可先取 20 页样本），作为"精度天花板/地板"参照，成本近零。
  2. **本地装 MinerU 4.x 基础包**（`uv pip install -U "mineru>=4.0,<5"`），跑 `flash`、`basic` 两档，记录**每页耗时、峰值 RSS、公式/表格正确率**；用 `mineru-kit models show` 确认实际生效后端。同时用 `--pages "1-10"` 做快速标定。
  3. **若精度不够**：优先选"本地 CPU pipeline + 远端 VLM"的 http-client 混合方案，而不是硬扛 CPU VLM。同时用 [OmniDocBench](https://github.com/opendatalab/OmniDocBench) + [Dingo](https://github.com/opendatalab/Dingo) 对两套方案打分（训练营第 07/08 课有方法论）。
- **CPU 侧硬门槛**：必须有 **AVX2**（否则直接 SIGILL）；多核利用率差（MFR 阶段尤其），**不要指望 32 核 = 8 核的 4 倍**；容器内务必手动设 `OMP_NUM_THREADS`（torch 会按宿主机核数而非 cgroup 配额起线程，社区实测严重超订）。
- **PaddleOCR-VL 的取舍**：官方 CPU 路径存在但**官方自己不推荐用于生产**，且高性能服务化只支持 NVIDIA。若坚持无 GPU，**MinerU pipeline（成熟 ONNX 小模型链路）比 PaddleOCR-VL（VLM 在 CPU 上跑）更现实**；PaddleOCR 的价值主要体现在其分模块 CPU 耗时数据更透明、生态更可控。

**待补的空白（明确未找到可靠数据）**：4.x 纯 CPU 端到端页/秒官方基准；ONNX vs Torch 精度差；8/16/服务器级 CPU 横评；100 页 PDF 峰值内存；PP-StructureV3 纯 CPU 端到端页/秒；阿里云百炼文档解析的页级价格与精度。

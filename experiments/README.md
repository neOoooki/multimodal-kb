# 研究实验留档

这里是为**设计决策提供证据**的实验脚本，不是产品代码。

保留它们是为了让 README 和 `docs/architecture.md` 里的每个数字都能被复现。

| 脚本 | 验证了什么 | 结论 |
| --- | --- | --- |
| `align_test.py` | 三种 embedding 的**图→文**对齐能力 | `multimodal-embedding-v1` 40%、`tongyi-embedding-vision-plus` 40%、`qwen3-vl-embedding` 75% |
| `align_test2.py` | **文→图**方向 + 融合向量 | 独立向量 80%、**融合向量 95%** |
| `rerank_test.py` | rerank 能否补救弱检索 | **40% → 90%** |
| `textify_test.py` / `textify_test2.py` | 「图片语义文本化」是否可行 | 图注+描述合并后与融合向量同一水平 |
| `mineru_api.py` / `mineru_api2.py` | MinerU 官方 API 调用流程 | 100 页 15–40 秒 |

## 运行

这些脚本会**真的调 API 产生费用**（很少，几分钱），按需运行：

```bash
export DASHSCOPE_API_KEY=sk-xxx
export MINERU_TOKEN=sk-xxx
python3 experiments/align_test2.py
```

> **注意**：部分脚本假设存在 `data/parsed/<doc>/` 解析产物（先用 `./kb ingest` 生成），
> 以及本机 Qdrant 里已有数据。

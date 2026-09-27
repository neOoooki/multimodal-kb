# 测试

## 冒烟测试

一次性验证整条链路的关键假设（**23 项**）：

```bash
export DASHSCOPE_API_KEY=sk-xxx
python3 tests/smoke_test.py
```

覆盖：

| 分组 | 检查项 |
| --- | --- |
| 核心包导入 | 零第三方依赖下可导入 |
| Qdrant | 可达 / `fusion`+`text` 是 dense / `image` 是 multivector |
| 分块 | 确定性 id（同 `doc_key` 幂等、不同 `doc_key` 区分、无重复） |
| 三路检索 | `fusion` / `text` 各返回结果 |
| 以图搜图 | 拿库里的图搜它自己，Top-1 命中且分数 >0.99 |
| 服务端点 | `/health`、`/search`、图片直连下载 |
| Dify 适配器 | 空 query 探测返回空 records / 错误 token 403 / 契约结构完整 |
| Open WebUI 适配器 | 能拼出图片区块，用 Markdown 而非 `<img>`，不重复正文图片 |

**前置条件**：Qdrant 在跑 + 服务已启动 + 库里有数据。

## 召回测试题集

`evalsets/dianzi.json` 是随仓库带的示例题集。

```bash
./kb eval                        # 用内置题集
./kb eval -f tests/evalsets/自己的题集.json
./kb eval --llm                  # 同时跑 LLM 生成回答
```

**题集格式**：

```json
{
  "name": "题集名",
  "questions": [
    {"q": "手工焊接的步骤是什么？", "expect": ["三步操作法"]}
  ]
}
```

命中判定：`expect` 里的关键词**全部出现**在 top-k 召回内容中才算命中。

> 设计题集时，`expect` 选**该知识点独有**的词（比如"三步操作法"），
> 不要选"焊接"这种到处都有的词，否则测不出区分度。

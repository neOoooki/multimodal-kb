# 测试素材

## `make_sample_pdf.py` / `sample-2p.pdf`

生成一个**极小、带文字层**的样例 PDF（2 页，约 1.6 KB），供**部署契约测试**做入库冒烟。

```bash
python3 tests/fixtures/make_sample_pdf.py                 # → tests/fixtures/sample-2p.pdf
python3 tests/fixtures/make_sample_pdf.py /tmp/other.pdf  # 指定输出
```

为什么是"生成"而不是"提交一个 PDF"：

- 真实教材有版权，且动辄几十 MB，不适合进版本库、更不适合 CI 每次跑；
- `sample-2p.pdf` 是产物，已写进 `.gitignore`，CI 里现算现用；
- 生成器只用标准库，和核心包"零第三方依赖"的原则一致。

内容刻意用**英文**：PDF 标准 14 字体（Helvetica）不含 CJK 字形，内嵌中文字体需要字体子集化，
会把一个 20 行的生成器变成一条字体工具链。这个 fixture 只需要"有标题、有正文、能切出块"。

> 注意：`scripts/deploy-test.sh --with-ingest` 会用**真实** MinerU + 百炼 API 跑一次入库，
> 因此需要真实密钥，并会消耗少量额度（2 页）。默认不启用。

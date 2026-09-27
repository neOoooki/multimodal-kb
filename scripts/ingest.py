#!/usr/bin/env python3
"""
入库脚本：把一个 PDF 变成可检索的知识库
==========================================

这是 `kb ingest` 的**可编程版本**，适合放进批处理/CI 流程。
路径全部走配置与环境变量，**仓库里没有任何机器相关的绝对路径**。

用法：
    export DASHSCOPE_API_KEY=sk-xxx      # 阿里云百炼
    export MINERU_TOKEN=sk-xxx           # MinerU 官方 API（本地模式可省）
    python3 scripts/ingest.py book.pdf --doc-id book-1

    # 只用本地 MinerU（零 API 成本，约 0.6 秒/页）
    MINERU_MODE=local python3 scripts/ingest.py book.pdf

    # 已有解析产物时直接复用，跳过解析
    python3 scripts/ingest.py book.pdf --parsed-dir data/parsed/book
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本在**未安装**的情况下也能跑：把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multimodal_kb import KBConfig, MultimodalKBPipeline  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="把 PDF 入库到多模态知识库")
    ap.add_argument("pdf", help="PDF 路径")
    ap.add_argument("--doc-id", default=None,
                    help="文档 ID。**固定它可以让重复入库幂等**（默认按文件名取）")
    ap.add_argument("--name", default=None, help="文档显示名（默认取文件名）")
    ap.add_argument("--parsed-dir", default=None,
                    help="复用已有的 MinerU 解析产物目录（跳过解析）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个子块（试跑）")
    ap.add_argument("--no-images", action="store_true", help="不生成图片向量（省成本）")
    ap.add_argument("--recreate", action="store_true", help="入库前重建集合（会清空数据）")
    ap.add_argument("--mineru-mode", choices=["api", "local"], default=None,
                    help="解析方式，覆盖 MINERU_MODE")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"❌ PDF 不存在：{pdf}", file=sys.stderr)
        return 1

    cfg = KBConfig.from_env()
    if args.mineru_mode:
        cfg.mineru_mode = args.mineru_mode
    cfg.embed_images = not args.no_images
    cfg.ensure_dirs()

    if not cfg.dashscope_api_key:
        print("❌ 需要 DASHSCOPE_API_KEY（向量化用）", file=sys.stderr)
        return 1
    if cfg.mineru_mode == "api" and not cfg.mineru_token:
        print("❌ MINERU_MODE=api 需要 MINERU_TOKEN；或改用 MINERU_MODE=local",
              file=sys.stderr)
        return 1

    print(f"解析方式   {cfg.mineru_mode}"
          + (f"（tier={cfg.mineru_tier}）" if cfg.mineru_mode == "local" else ""))
    print(f"缓存目录   {cfg.work_dir}")
    print(f"解析目录   {cfg.parsed_dir}")
    print(f"Qdrant     {cfg.qdrant_url} / {cfg.collection}\n")

    pipe = MultimodalKBPipeline(cfg)
    pipe.init_store(recreate=args.recreate)

    stats = pipe.ingest_pdf(
        pdf, doc_name=args.name, doc_id=args.doc_id or pdf.stem,
        parsed_dir=args.parsed_dir, limit_chunks=args.limit,
    )

    print("\n" + "=" * 50)
    for k, v in stats.items():
        print(f"  {k:<12} {v}")
    print("=" * 50)
    print('\n完成。检索试试：\n    ./kb search "你的问题"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

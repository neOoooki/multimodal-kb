"""
multimodal-kb —— 可迁移的多模态知识库平台
============================================

把 PDF（教材 / 元器件手册）变成**可文搜图、可图搜图**的结构化知识库。

设计要点
--------
1. **核心不依赖任何 chat 框架**。Dify / Open WebUI 只是 `integrations/` 里的适配层，
   换框架不改核心。
2. **三路向量并存**：`fusion`（正文+图融合成一个向量）、`text`（正文+图片描述）、
   `image`（每张图各自成向量，以图搜图）。这是普通 RAG 平台做不到的。
3. **零第三方依赖**：内核只用 Python 标准库。

快速开始
--------
    from multimodal_kb import KBConfig, MultimodalKBPipeline, HybridSearcher, SearchConfig

    cfg = KBConfig.from_env()          # 读环境变量
    pipe = MultimodalKBPipeline(cfg)
    pipe.init_store()                  # 建集合
    pipe.ingest_pdf("book.pdf")        # 解析→分块→标注→向量化→入库

    hits = pipe.searcher(SearchConfig(top_k=5, rerank=True)).search("你的问题")
"""

from .config import KBConfig
from .models import Block, BlockType, Chunk, Document, ImageRef, RetrievedChunk, Section
from .pipeline import MultimodalKBPipeline
from .search.hybrid import HybridSearcher, SearchConfig
from .store.qdrant_store import QdrantStore

__version__ = "0.1.0"

__all__ = [
    "KBConfig",
    "MultimodalKBPipeline",
    "HybridSearcher",
    "SearchConfig",
    "QdrantStore",
    "Block",
    "BlockType",
    "Chunk",
    "Document",
    "ImageRef",
    "RetrievedChunk",
    "Section",
    "__version__",
]

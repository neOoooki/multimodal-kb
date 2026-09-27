"""
领域模型：多模态知识库的**完整**数据结构
==========================================

设计原则
--------
**核心模型不为任何外部框架妥协。**

Dify 的索引结构是「每 segment 一个文本向量 + 每附件一个图片向量」，
存不下"正文+图融合成一个向量"，也不保存 bbox / 表格 HTML / 章节树。

如果我们按 Dify 的能力来设计模型，就等于把它的缺陷固化进核心。
所以这里的模型**完整保留**所有信息，由 `adapters/` 里的适配器在需要时做**降级映射**
（丢什么、怎么丢，是适配器的责任，不是核心的责任）。

将来换任何框架，只写一个新适配器，核心一行不改。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import StrEnum
from typing import Any


class BlockType(StrEnum):
    """块类型（对齐 MinerU 的 content_list 类型）。"""
    TITLE = "title"
    PARAGRAPH = "paragraph"
    IMAGE = "image"
    CHART = "chart"
    TABLE = "table"
    EQUATION = "equation_interline"
    PAGE_NUMBER = "page_number"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"


# 这些是噪声，默认丢弃
NOISE_TYPES = {BlockType.PAGE_NUMBER, BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER}


@dataclass
class Block:
    """解析后的最小单元。"""
    idx: int
    page: int
    type: BlockType
    text: str = ""
    level: int | None = None                 # title 的层级（解析器给的）
    image_path: str | None = None            # 相对路径
    caption: str = ""                        # 图注 / 表注
    footnote: str = ""
    html: str | None = None                  # 表格的 HTML 结构
    latex: str | None = None                 # 公式的 LaTeX
    bbox: list[float] | None = None          # 版面坐标 [x0,y0,x1,y1]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_title(self) -> bool:
        return self.type == BlockType.TITLE

    @property
    def is_figure(self) -> bool:
        return self.type in (BlockType.IMAGE, BlockType.CHART)

    @property
    def is_table(self) -> bool:
        return self.type == BlockType.TABLE

    @property
    def is_equation(self) -> bool:
        return self.type == BlockType.EQUATION

    @property
    def is_noise(self) -> bool:
        return self.type in NOISE_TYPES


@dataclass
class ImageRef:
    """图片引用：路径 + 语义 + 位置。"""
    path: str                                # 存储内路径
    caption: str = ""
    footnote: str = ""
    bbox: list[float] | None = None
    page: int | None = None
    description: str = ""                    # VLM 生成的详细语义描述
    description_model: str = ""
    ocr_text: str = ""                       # 图中文字（若单独抽取）

    @property
    def searchable_text(self) -> str:
        """用于文本检索的合并文本：图注 + 描述。"""
        parts = [p for p in (self.caption, self.description) if p]
        return "。".join(parts)


@dataclass
class Section:
    """一级结构：一个小节（父块）。"""
    id: str
    path: list[str]                          # 标题路径，如 ["第1章","1.1 概述"]
    part: int = 1                            # 超长小节拆分时的序号
    page_start: int | None = None
    page_end: int | None = None
    blocks: list[Block] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def path_text(self) -> str:
        p = " > ".join(self.path)
        if self.part > 1:
            p = f"{p}（续{self.part - 1}）" if p else f"（续{self.part - 1}）"
        return p


@dataclass
class Chunk:
    """
    二级结构：一个语义单元（子块）。

    这是**入库与检索的基本单位**。携带的信息远超 Dify 能存的 —— 这是有意的。
    """
    id: str
    section_id: str
    content: str                             # 正文（已含章节路径前缀）
    section_path: list[str] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    page: int | None = None
    bbox: list[float] | None = None
    order: int = 0                           # 在文档内的顺序
    content_type: str = "text"               # text | table | figure | equation
    meta: dict[str, Any] = field(default_factory=dict)

    # ---- 向量（由 embed 层填充；三路并存，这是 Dify 做不到的）----
    vector_fusion: list[float] | None = None  # 正文+图 融合向量
    vector_text: list[float] | None = None    # 纯文本向量
    vector_images: list[list[float]] = field(default_factory=list)  # 每张图的向量

    @property
    def image_paths(self) -> list[str]:
        return [i.path for i in self.images]

    @property
    def searchable_text(self) -> str:
        """
        用于**文本向量**的内容：正文 + 各图的可检索文本。

        这一路保证"图片语义可被文字检索到"——不依赖跨模态对齐。
        """
        parts = [self.content]
        for im in self.images:
            t = im.searchable_text
            if t:
                parts.append(f"[图片] {t}")
        return "\n".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Document:
    """一份解析完成的文档。"""
    id: str
    name: str
    source_path: str = ""
    page_count: int = 0
    sections: list[Section] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def stats(self) -> dict[str, Any]:
        return {
            "doc_id": self.id,
            "name": self.name,
            "pages": self.page_count,
            "sections": len(self.sections),
            "chunks": len(self.chunks),
            "images": sum(len(c.images) for c in self.chunks),
            "tables": sum(1 for c in self.chunks if c.content_type == "table"),
        }


@dataclass
class RetrievedChunk:
    """检索结果——**框架无关**的返回结构。

    适配器负责把它翻译成目标框架要的样子。
    """
    chunk_id: str
    content: str
    score: float
    section_path: list[str] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    page: int | None = None
    doc_id: str = ""
    doc_name: str = ""
    scores_breakdown: dict[str, float] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

"""
编排层：一条命令跑完「解析 → 分块 → 标注 → 向量化 → 入库」
==============================================================

这一层**完全不依赖 Dify 或任何 chat 框架** —— 它只产出「带三路向量的 chunk 集合」
放进 Qdrant。要让某个框架用上，去 `adapters/` 里写对应适配器。

用法
----
    cfg = KBConfig(qdrant_url="http://localhost:6333",
                   dashscope_api_key="sk-...",
                   mineru_token="sk-...")
    pipe = MultimodalKBPipeline(cfg)
    pipe.init_store()
    stats = pipe.ingest_pdf("book.pdf", doc_name="电子工艺实训")
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .annotate.vlm import VisionAnnotator
from .config import KBConfig
from .chunk.structure import rebuild
from .embed.dashscope import DashScopeEmbedder, Reranker
from .models import Document
from .parse.mineru import MinerUAPI, find_content_list, load_content_list
from .search.hybrid import HybridSearcher, SearchConfig
from .store.qdrant_store import QdrantStore


class MultimodalKBPipeline:
    def __init__(self, cfg: KBConfig):
        self.cfg = cfg
        self.work = Path(cfg.work_dir)
        self.work.mkdir(parents=True, exist_ok=True)
        # 解析产物目录：可用 MMKB_PARSED_DIR 覆盖（容器里通常挂到 /data/parsed）
        self.parsed_dir = Path(cfg.parsed_dir)
        self.parsed_dir.mkdir(parents=True, exist_ok=True)
        self.store = QdrantStore(cfg.qdrant_url, cfg.collection)
        self.embedder = DashScopeEmbedder(
            cfg.dashscope_api_key, cfg.fusion_model, cfg.text_model) if cfg.dashscope_api_key else None
        self.reranker = Reranker(cfg.dashscope_api_key, cfg.rerank_model) \
            if cfg.dashscope_api_key else None
        self.annotator = VisionAnnotator(
            cfg.dashscope_api_key, cfg.vision_model,
            cache_path=self.work / "image_desc_cache.json") if cfg.dashscope_api_key else None
        # 向量缓存：重跑入库时不重复调用 embedding API（省钱也省时间）
        self._vec_cache_path = self.work / "embed_cache.json"
        self._vec_cache: dict[str, list] = {}
        if self._vec_cache_path.exists():
            try:
                self._vec_cache = json.loads(self._vec_cache_path.read_text(encoding="utf-8"))
            except Exception:
                self._vec_cache = {}
        self._vec_dirty = 0

    def _cache_save(self, force: bool = False):
        self._vec_dirty += 1
        if force or self._vec_dirty % 20 == 0:
            self._vec_cache_path.write_text(
                json.dumps(self._vec_cache, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _vkey(kind: str, data: str) -> str:
        import hashlib
        return kind + ":" + hashlib.sha256(data.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _file_hash(path: Path) -> str:
        """图片内容哈希（短）。用于让缓存 key 与绝对路径无关。"""
        import hashlib
        try:
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
        except Exception:
            return "?"

    # -- 存储 -------------------------------------------------------------
    def init_store(self, recreate: bool = False) -> None:
        if not self.store.alive():
            raise RuntimeError(f"Qdrant 不可达: {self.cfg.qdrant_url}")
        try:
            self.store.info()
            if not recreate:
                print(f"集合已存在: {self.cfg.collection}（{self.store.count()} 点）")
                return
        except Exception:
            pass
        self.store.create_collection(recreate=recreate)
        print(f"已创建集合: {self.cfg.collection}")

    # -- 解析 -------------------------------------------------------------
    def parse(self, pdf: str | Path, out_dir: str | Path | None = None) -> Path:
        pdf = Path(pdf)
        out = Path(out_dir) if out_dir else self.parsed_dir / pdf.stem
        if self.cfg.mineru_mode == "api":
            if not self.cfg.mineru_token:
                raise RuntimeError("mineru_mode=api 需要 mineru_token")
            return MinerUAPI(self.cfg.mineru_token).parse(
                pdf, out, model_version=self.cfg.mineru_model_version)
        from .parse.mineru import parse_local
        md = parse_local(pdf, out / "result.md", tier=self.cfg.mineru_tier)
        return md.parent

    # -- 主流程 -----------------------------------------------------------
    def ingest_pdf(self, pdf: str | Path, doc_name: str | None = None,
                   parsed_dir: str | Path | None = None,
                   doc_id: str | None = None, annotate: bool = True,
                   limit_chunks: int = 0) -> dict:
        pdf = Path(pdf)
        doc_id = doc_id or str(uuid4())
        name = doc_name or pdf.stem

        # ① 解析
        root = Path(parsed_dir) if parsed_dir else self.parse(pdf)
        cl = find_content_list(root)
        blocks = load_content_list(cl)
        print(f"① 解析：{len(blocks)} 个块（{cl.name}）")

        # ② 两级分块
        sections, chunks = rebuild(blocks, self.cfg.max_section_chars,
                                   self.cfg.target_chunk_chars, self.cfg.max_chunk_chars,
                                   doc_key=doc_id)   # 确定性 id → 重复入库幂等
        n_img = sum(len(c.images) for c in chunks)
        print(f"② 分块：{len(sections)} 父块 / {len(chunks)} 子块 / {n_img} 图片")
        if limit_chunks:
            keep = {c.section_id for c in chunks[:limit_chunks]}
            chunks = chunks[:limit_chunks]
            sections = [s for s in sections if s.id in keep]
            print(f"   （试跑模式：只取前 {len(chunks)} 个子块）")

        # ③ 图片语义标注
        if annotate and self.annotator:
            self._annotate(chunks, root)

        # ④ 向量化
        if self.embedder:
            self._embed(chunks, root)

        # ⑤ 入库
        n = self.store.upsert(chunks, doc_id, name)
        print(f"⑤ 入库：{n} 个点")

        doc = Document(id=doc_id, name=name, source_path=str(pdf),
                       sections=sections, chunks=chunks,
                       meta={"parsed_dir": str(root)})
        return doc.stats()

    def _annotate(self, chunks, root: Path) -> None:
        todo = {}
        for c in chunks:
            for im in c.images:
                p = root / im.path
                if p.exists() and im.path not in todo and not im.description:
                    todo[im.path] = p
        print(f"③ 标注：{len(todo)} 张图待处理（有缓存会自动跳过）")
        done = 0
        for i, (rel, p) in enumerate(todo.items(), 1):
            try:
                desc = self.annotator.describe(p)
            except Exception as e:
                print(f"   [{i}/{len(todo)}] 失败 {rel}: {str(e)[:100]}")
                continue
            for c in chunks:
                for im in c.images:
                    if im.path == rel:
                        im.description = desc
                        im.description_model = self.cfg.vision_model
            done += 1
            if i % 10 == 0 or i == len(todo):
                print(f"   进度 {i}/{len(todo)}")

    def _embed(self, chunks, root: Path) -> None:
        print("④ 向量化 …（有缓存会自动跳过）")
        hit = miss = 0
        for i, c in enumerate(chunks, 1):
            text = c.searchable_text

            # 文本向量：正文 + 图片描述（图片语义文本化的主力）
            k = self._vkey("text", text)
            if k in self._vec_cache:
                c.vector_text = self._vec_cache[k]; hit += 1
            else:
                try:
                    c.vector_text = self.embedder.text_one(text)
                    self._vec_cache[k] = c.vector_text; self._cache_save(); miss += 1
                except Exception as e:
                    print(f"   文本向量失败 chunk {i}: {str(e)[:200]}")

            # 融合向量：正文 + 首张图（多图时其余走 image 路）
            #
            # 缓存 key 用 **图片相对路径 + 内容哈希**，不用绝对路径 ——
            # 否则同一份数据换台机器/换个挂载点（容器内 vs 宿主机）就必然不命中，
            # "有缓存"省钱的意图就白费了。
            first = next((im for im in c.images if (root / im.path).exists()), None)
            if first:
                img_key = f"{first.path}@{self._file_hash(root / first.path)}"
            else:
                img_key = ""
            fk = self._vkey("fusion", text + "||" + img_key)
            if fk in self._vec_cache:
                c.vector_fusion = self._vec_cache[fk]; hit += 1
            else:
                try:
                    # 注意传**路径**，不是 ImageRef 对象；没有图时传 None
                    # （此时 fusion 等价于纯文本向量，仍是同一维度的 2560）
                    img_path = (root / first.path) if first else None
                    c.vector_fusion = self.embedder.fusion_one(text, img_path)
                    self._vec_cache[fk] = c.vector_fusion; self._cache_save(); miss += 1
                except Exception as e:
                    # 不回退成文本向量 —— 两者维度不同（fusion 2560 / text 1024），
                    # 塞进 fusion 槽位会在 Qdrant 报 "Vector dimension error"，
                    # 把真正的失败原因掩盖掉。宁可这条 chunk 少一路向量。
                    print(f"   融合向量失败 chunk {i}（跳过该路，不影响 text 路）: {str(e)[:200]}")
                    c.vector_fusion = None

            # 图片向量（每张图）
            if self.cfg.embed_images:
                for im in c.images:
                    p = root / im.path
                    if not p.exists():
                        continue
                    ik = self._vkey("image", f"{im.path}@{self._file_hash(p)}")
                    if ik in self._vec_cache:
                        c.vector_images.append(self._vec_cache[ik]); hit += 1
                        continue
                    try:
                        v = self.embedder.image_one(p)
                        c.vector_images.append(v)
                        self._vec_cache[ik] = v; self._cache_save(); miss += 1
                    except Exception as e:
                        print(f"   图片向量失败 {im.path}: {str(e)[:100]}")
            if i % 40 == 0 or i == len(chunks):
                print(f"   进度 {i}/{len(chunks)}（缓存命中 {hit} / 新算 {miss}）")
        self._cache_save(force=True)

    # -- 检索 -------------------------------------------------------------
    def searcher(self, config: SearchConfig | None = None) -> HybridSearcher:
        return HybridSearcher(self.store, self.embedder, self.reranker, config)

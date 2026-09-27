"""
Qdrant 存储：三路向量并存 + 混合检索
======================================

Dify 的索引结构是「每 segment 一个文本向量 + 每附件一个图片向量」，
**没有地方存"正文+图融合成的那一个向量"**。这里不存在这个限制：

    一个 chunk 对应一个 point，带三个**具名向量**：

      fusion : 2560 维  正文 + 图 → 融合成一个向量（实测跨模态 Top-1 95%）
      text   : 1024 维  正文 + 图片描述 → 纯文本向量（不依赖跨模态对齐）
      image  : 2560 维 × N（multivector, max_sim）  每张图各自的向量

检索时三路并行，用 RRF 融合，再交给 rerank 精排。

用原生 HTTP 直连 Qdrant REST API，**不引入任何第三方依赖**（便于迁移）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..models import Chunk, ImageRef, RetrievedChunk


class QdrantStore:
    """Qdrant 集合管理 + 写入 + 检索（纯 stdlib 实现）。"""

    def __init__(self, url: str = "http://localhost:6333", collection: str = "mmkb",
                 timeout: int = 120):
        self.url = url.rstrip("/")
        self.collection = collection
        self.timeout = timeout

    # -- 基础设施 ---------------------------------------------------------
    def _req(self, method: str, path: str, payload=None, timeout: int | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(self.url + path, data=data, method=method)
        if data:
            r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=timeout or self.timeout) as resp:
                body = resp.read().decode()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode()[:400]
            raise RuntimeError(f"Qdrant {method} {path} -> HTTP {e.code}: {raw}")

    def alive(self) -> bool:
        try:
            return "version" in self._req("GET", "/", timeout=10)
        except Exception:
            return False

    # -- 集合 -------------------------------------------------------------
    def create_collection(self, dim_fusion: int = 2560, dim_text: int = 1024,
                          dim_image: int = 2560, distance: str = "Cosine",
                          recreate: bool = False) -> None:
        """
        三个具名向量：
          fusion / text 是单向量（dense）
          image 是 multivector（comparator=max_sim），因为一个 chunk 可能有多张图

        注意：multivector 必须写在**同一次** `PUT /collections/{name}` 的
        `multivector_config` 里（不是单独的 /vectors/{name} 端点）。
        """
        if recreate:
            self._req("DELETE", f"/collections/{self.collection}")
        self._req("PUT", f"/collections/{self.collection}", {
            "vectors": {
                "fusion": {"size": dim_fusion, "distance": distance},
                "text": {"size": dim_text, "distance": distance},
                "image": {"size": dim_image, "distance": distance,
                          "multivector_config": {"comparator": "max_sim"}},
            },
        })

    def info(self) -> dict:
        return self._req("GET", f"/collections/{self.collection}")

    def count(self) -> int:
        return self._req("POST", f"/collections/{self.collection}/points/count",
                         {"exact": True}).get("result", {}).get("count", 0)

    def delete_by_doc(self, doc_id: str) -> None:
        self._req("POST", f"/collections/{self.collection}/points/delete?wait=true",
                  {"filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]}})

    # -- 浏览 -------------------------------------------------------------
    def scroll_all(self, with_payload: bool = True, limit: int = 10000) -> list[dict]:
        """翻页取回所有点（用于统计、列文档、导出）。"""
        out, offset = [], None
        while len(out) < limit:
            body: dict = {"limit": 256, "with_payload": with_payload, "with_vector": False}
            if offset:
                body["offset"] = offset
            r = self._req("POST", f"/collections/{self.collection}/points/scroll", body)
            res = r.get("result") or {}
            pts = res.get("points") or []
            out += pts
            offset = res.get("next_page_offset")
            if not offset or not pts:
                break
        return out[:limit]

    def list_documents(self) -> list[dict]:
        """
        按 doc_id 聚合出文档清单。

        注意：Qdrant 没有"文档表"，文档信息只存在于每个点的 payload 里，
        所以只能遍历聚合。数据量大时这里会变慢（届时应另建一张元数据表）。
        """
        agg: dict[str, dict] = {}
        for p in self.scroll_all():
            pl = p.get("payload") or {}
            did = pl.get("doc_id") or "?"
            a = agg.setdefault(did, {
                "doc_id": did, "doc_name": pl.get("doc_name", ""),
                "chunks": 0, "images": 0, "tables": 0, "figures": 0,
                "pages": set(),
            })
            a["chunks"] += 1
            a["images"] += len(pl.get("images") or [])
            ct = pl.get("content_type")
            if ct == "table":
                a["tables"] += 1
            elif ct == "figure":
                a["figures"] += 1
            if pl.get("page"):
                a["pages"].add(pl["page"])
        for a in agg.values():
            a["pages"] = len(a["pages"])
        return sorted(agg.values(), key=lambda x: -x["chunks"])

    # -- 写入 -------------------------------------------------------------
    def upsert(self, chunks: list[Chunk], doc_id: str, doc_name: str) -> int:
        """把带向量的 chunk 写入。缺少向量时会跳过对应具名向量。"""
        points = []
        for c in chunks:
            vectors: dict[str, Any] = {}
            if c.vector_fusion:
                vectors["fusion"] = c.vector_fusion
            if c.vector_text:
                vectors["text"] = c.vector_text
            if c.vector_images:
                vectors["image"] = c.vector_images
            if not vectors:
                continue
            points.append({
                "id": c.id,
                "vector": vectors,
                "payload": {
                    "chunk_id": c.id,
                    "doc_id": doc_id,
                    "doc_name": doc_name,
                    "content": c.content,
                    "section_path": c.section_path,
                    "page": c.page,
                    "order": c.order,
                    "content_type": c.content_type,
                    "images": [
                        {"path": i.path, "caption": i.caption, "footnote": i.footnote,
                         "description": i.description, "page": i.page, "bbox": i.bbox}
                        for i in c.images
                    ],
                    **(c.meta or {}),
                },
            })
        if not points:
            return 0
        total = 0
        for i in range(0, len(points), 64):
            batch = points[i:i + 64]
            self._req("PUT", f"/collections/{self.collection}/points?wait=true",
                      {"points": batch})
            total += len(batch)
        return total

    # -- 检索 -------------------------------------------------------------
    @staticmethod
    def _to_results(hits: list[dict]) -> list[RetrievedChunk]:
        out = []
        for h in hits:
            p = h.get("payload") or {}
            imgs = [ImageRef(path=x.get("path", ""), caption=x.get("caption", ""),
                             footnote=x.get("footnote", ""), description=x.get("description", ""),
                             page=x.get("page"), bbox=x.get("bbox"))
                    for x in (p.get("images") or [])]
            out.append(RetrievedChunk(
                chunk_id=p.get("chunk_id", str(h.get("id"))),
                content=p.get("content", ""), score=float(h.get("score", 0.0)),
                section_path=p.get("section_path") or [], images=imgs,
                page=p.get("page"), doc_id=p.get("doc_id", ""), doc_name=p.get("doc_name", ""),
                meta={"content_type": p.get("content_type")},
            ))
        return out

    def search_named(self, vector_name: str, vector: Any, limit: int = 20,
                     query_filter: dict | None = None) -> list[RetrievedChunk]:
        """
        具名向量检索。

        注意：必须用 **`/points/query`**，不能用旧的 `/points/search` ——
        Qdrant 1.19 下 `/points/search` 对 multivector 字段会报
        `data did not match any variant of untagged enum NamedVectorStruct`。
        `/points/query` 对 dense 和 multivector 都适用。
        """
        body: dict = {"query": vector, "using": vector_name,
                      "limit": limit, "with_payload": True}
        if query_filter:
            body["filter"] = query_filter
        res = self._req("POST", f"/collections/{self.collection}/points/query", body)
        result = res.get("result") or {}
        points = result.get("points") if isinstance(result, dict) else result
        return self._to_results(points or [])


    def search_fusion(self, text: str | None, image: Path | str | None,
                      embedder, limit: int = 20,
                      query_filter: dict | None = None) -> list[RetrievedChunk]:
        """融合向量检索：query 也可以"文+图"融合，与库里同构。"""
        vec = embedder.fusion_one(text, image)
        return self.search_named("fusion", vec, limit, query_filter)

    def search_text(self, text: str, embedder, limit: int = 20,
                    query_filter: dict | None = None) -> list[RetrievedChunk]:
        return self.search_named("text", embedder.text_one(text), limit, query_filter)

    def search_image(self, image: Path | str, embedder, limit: int = 20,
                     query_filter: dict | None = None) -> list[RetrievedChunk]:
        """以图搜图。库里 image 是 multivector，Qdrant 用 max_sim 比较。"""
        vec = embedder.image_one(image)
        return self.search_named("image", [vec], limit, query_filter)

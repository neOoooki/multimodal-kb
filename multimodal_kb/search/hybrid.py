"""
混合检索：多路召回 → RRF 融合 → rerank 精排
==============================================

为什么要多路
------------
单一向量检索在面对"同一本书相邻小节、相似电路图"这类高度相似内容时很容易混淆。
实测：当前 embedding 文→图 Top-1 只有 40%；换融合向量到 95%；
再加 rerank 可把弱检索从 40% 拉到 90%。

三路各有所长
------------
  fusion : 跨模态最强（图文融合在同一向量空间）
  text   : 不依赖跨模态对齐，图片语义已文本化，可用最强文本模型
  image  : 以图搜图（用户上传一张图找相关内容）

RRF（Reciprocal Rank Fusion）
-----------------------------
    分数 = Σ 1 / (k + 该路排名)      k 默认 60
只用**排名**不用原始分数，因此不同路的分数量纲不一致也能融合 —— 这是它最大的优点。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import RetrievedChunk
from ..store.qdrant_store import QdrantStore

RRF_K = 60


@dataclass
class SearchConfig:
    top_k: int = 5               # 最终返回
    recall_per_route: int = 20   # 每路召回数
    use_fusion: bool = True
    use_text: bool = True
    use_image: bool = False      # 仅当 query 带图片时打开
    rerank: bool = False
    rrf_k: int = RRF_K


class HybridSearcher:
    def __init__(self, store: QdrantStore, embedder=None, reranker=None,
                 config: SearchConfig | None = None):
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self.config = config or SearchConfig()

    # -- RRF --------------------------------------------------------------
    @staticmethod
    def rrf(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
        """输入多路有序 id 列表，输出 {id: 融合分}。"""
        scores: dict[str, float] = {}
        for ranking in rankings:
            for rank, cid in enumerate(ranking, start=1):
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        return scores

    # -- 主流程 -----------------------------------------------------------
    def search(self, query: str, query_image: Path | str | None = None,
               config: SearchConfig | None = None,
               query_filter: dict | None = None) -> list[RetrievedChunk]:
        cfg = config or self.config
        routes: dict[str, list[RetrievedChunk]] = {}

        if cfg.use_fusion and self.embedder:
            try:
                routes["fusion"] = self.store.search_fusion(
                    query, query_image, self.embedder, cfg.recall_per_route, query_filter)
            except Exception as e:
                routes["fusion"] = []
                print(f"  [warn] fusion 路失败: {str(e)[:120]}")

        if cfg.use_text and self.embedder:
            try:
                routes["text"] = self.store.search_text(
                    query, self.embedder, cfg.recall_per_route, query_filter)
            except Exception as e:
                routes["text"] = []
                print(f"  [warn] text 路失败: {str(e)[:120]}")

        if (cfg.use_image or query_image) and self.embedder and query_image:
            try:
                routes["image"] = self.store.search_image(
                    query_image, self.embedder, cfg.recall_per_route, query_filter)
            except Exception as e:
                routes["image"] = []
                print(f"  [warn] image 路失败: {str(e)[:120]}")

        # 合并候选池
        pool: dict[str, RetrievedChunk] = {}
        breakdown: dict[str, dict[str, float]] = {}
        for name, hits in routes.items():
            for rank, h in enumerate(hits, start=1):
                pool.setdefault(h.chunk_id, h)
                breakdown.setdefault(h.chunk_id, {})[f"{name}_rank"] = rank
                breakdown[h.chunk_id][f"{name}_score"] = h.score

        if not pool:
            return []

        # RRF 融合
        fused = self.rrf([[h.chunk_id for h in hits] for hits in routes.values() if hits],
                         cfg.rrf_k)
        for cid, sc in fused.items():
            if cid in pool:
                pool[cid].scores_breakdown = {**breakdown.get(cid, {}), "rrf": sc}
                pool[cid].score = sc
        ordered = sorted(pool.values(), key=lambda x: -x.score)

        # rerank 精排
        if cfg.rerank and self.reranker and len(ordered) > 1:
            try:
                pairs = self.reranker.rerank(
                    query, [c.content for c in ordered], top_n=len(ordered))
                reranked = []
                for idx, rscore in pairs:
                    c = ordered[idx]
                    c.scores_breakdown["rerank"] = rscore
                    c.score = float(rscore)
                    reranked.append(c)
                ordered = reranked
            except Exception as e:
                print(f"  [warn] rerank 失败，退回 RRF 顺序: {str(e)[:120]}")

        return ordered[: cfg.top_k]

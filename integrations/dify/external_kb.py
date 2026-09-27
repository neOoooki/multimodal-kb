"""
Dify 适配器（**薄适配**，不是核心）
=====================================

定位
----
核心层（解析 / 分块 / 三路向量 / 混合检索）完全独立于 Dify。
本文件只做一件事：把核心的 `RetrievedChunk` **翻译**成 Dify 外部知识库契约。

契约（`POST {endpoint}`）
------------------------
    请求: {"knowledge_id": "...", "query": "...",
           "retrieval_setting": {"top_k": 5, "score_threshold": 0.5},
           "metadata_condition": {...}}
    响应: {"records": [{"content": "...", "score": 0.9,
                        "title": "...", "metadata": {...}}]}

**为什么是"薄"适配 —— 以及它的固有损失**
------------------------------------------
Dify 的 `records` 只有 `content / score / title / metadata`，**没有图片字段**，
且 `content` 的定位是"作为上下文传给 LLM"。也就是说：

    检索到图片 → 塞进 prompt → 指望 LLM 把图片 markdown **复述**出来 → 才能显示

这条链路本质脆弱（LLM 可能改写、丢弃或转义图片语法），
这是 **Dify 的架构限制，不是我们能修的**。

所以我们**不为了适配 Dify 而改变核心**：
  · 核心 chunk 里保留完整信息（图片对象、bbox、章节树、表格 HTML）
  · 适配器把图片**尽力**塞进 `content`（Markdown 语法 + metadata 里给直连 URL）
  · 是否显示由 Dify/LLM 决定 —— 要在 UI 里**确定性**展示图片，
    应该用能直接控制 UI 的框架（见 `adapters/openwebui/`）
"""

from __future__ import annotations

from typing import Any

from multimodal_kb.service.app import KBSearchService


def dify_retrieval(svc: KBSearchService, payload: dict[str, Any]) -> dict[str, Any]:
    """把一次 Dify 外部知识库检索请求翻译成核心检索，再翻译回去。"""
    query = (payload.get("query") or "").strip()

    # Dify 注册外部知识库时会发一个**空 query 的连通性探测**
    # （`{"knowledge_id":"","query":"","retrieval_setting":{"top_k":1,...}}`）。
    # 必须识别并直接返回空结果：否则空 query 会触发一次无意义的全量检索，
    # 既浪费算力又可能让 embedding 接口报错，导致注册失败。
    if not query:
        return {"records": []}

    setting = payload.get("retrieval_setting") or {}
    top_k = int(setting.get("top_k") or 5)
    threshold = setting.get("score_threshold")

    hits = svc.search(query, top_k=top_k, rerank=True)

    records = []
    for h in hits:
        if threshold is not None and h["score"] < float(threshold):
            continue
        # content 里已经带了 Markdown 图片语法（绝对 URL），LLM 若照抄即可显示
        records.append({
            "content": h["content"],
            "score": float(h["score"]),
            "title": " > ".join(h.get("section_path") or []) or h.get("doc_name", ""),
            "metadata": {
                "doc_id": h.get("doc_id", ""),
                "doc_name": h.get("doc_name", ""),
                "page": h.get("page"),
                "section_path": h.get("section_path") or [],
                "chunk_id": h.get("chunk_id", ""),
                # 图片直连地址：即使 LLM 吞了正文里的图片，前端/提示词也能取用
                "images": [i["url"] for i in (h.get("images") or [])],
            },
        })
    return {"records": records}


def install(svc: KBSearchService, prefix: str = "/v1/retrieval",
            bearer_token: str | None = None):
    """
    返回可挂到 `service.app.serve(extra_routes=...)` 的路由表。

    `bearer_token` 非空时校验 `Authorization: Bearer <token>`
    （Dify 会用注册时填的 api_key 发这个头）。
    """
    def handler(handler_self, payload, _path_tail):
        if bearer_token:
            auth = handler_self.headers.get("Authorization") or ""
            if auth != f"Bearer {bearer_token}":
                return 403, {"error": "forbidden"}
        return 200, dify_retrieval(svc, payload or {})

    return {("POST", prefix): handler}

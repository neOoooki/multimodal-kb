#!/usr/bin/env python3
"""
报告指标采集
==============

跑一遍真实环境，把报告要用到的数据落成 JSON（`docs/report/metrics.json`），
让图表脚本可以**离线重跑**，不必每次都打接口。

采集三类：
  1. 知识库统计        —— 向量库全量回读 + 解析产物
  2. 检索耗时分解      —— 查询向量化 / 向量检索 / 重排，各占多少
  3. 多路召回贡献      —— 每一路各自找到了什么，互补性如何

用法：
    python3 scripts/report_metrics.py

依赖：检索服务与向量库在跑；需要有效的向量化/重排 API Key（走项目 `.env`）。
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MMKB = "http://localhost:8088"
QDRANT = "http://localhost:6333"
COLLECTION = "mmkb"

# 取自教材内容的真实问题，覆盖概念、操作、图形、参数四类
QUERIES = [
    "万用表怎么用？",
    "手工焊接的步骤是什么？",
    "电阻的串联有什么特点？",
    "安全用电要注意什么？",
    "电烙铁的结构是怎样的？",
    "色环电阻怎么读数？",
    "电容的作用是什么？",
    "接地保护示意图说明了什么？",
    "常用电子元器件有哪些？",
    "怎样用万用表测量电压？",
]


def _post(url: str, payload: dict, timeout: int = 120):
    r = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        body = resp.read().decode()
        return json.loads(body) if body.strip() else {}


def _get(url: str, timeout: int = 60):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def scroll_all() -> list[dict]:
    out, offset = [], None
    while True:
        body = {"limit": 256, "with_payload": True, "with_vector": False}
        if offset:
            body["offset"] = offset
        res = _post(f"{QDRANT}/collections/{COLLECTION}/points/scroll", body)["result"]
        out += res.get("points") or []
        offset = res.get("next_page_offset")
        if not offset:
            break
    return out


# ---------------------------------------------------------------------------
def collect_kb(points: list[dict]) -> dict:
    imgs = sum(len(p["payload"].get("images") or []) for p in points)
    imgs_with_desc = sum(
        1 for p in points for i in (p["payload"].get("images") or [])
        if i.get("description"))
    pages = {p["payload"].get("page") for p in points if p["payload"].get("page")}
    secs = {tuple(p["payload"].get("section_path") or []) for p in points}
    types = Counter(p["payload"].get("content_type") or "text" for p in points)
    chars = [len(p["payload"].get("content") or "") for p in points]
    imgs_per = [len(p["payload"].get("images") or []) for p in points]
    depths = [len(p["payload"].get("section_path") or []) for p in points]

    # 解析产物统计（若还在本地）
    blocks = Counter()
    for f in (ROOT / "data" / "parsed").glob("*/*content_list_v2.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for page in data:
                if isinstance(page, list):
                    for b in page:
                        blocks[b.get("type") or "?"] += 1
        except Exception:
            pass

    # 分块长度直方图（按 200 字分桶）
    hist = Counter(min(int(c // 200) * 200, 1200) for c in chars)

    return {
        "points": len(points),
        "images": imgs,
        "images_with_desc": imgs_with_desc,
        "pages": len(pages),
        "sections": len(secs),
        "chars_total": sum(chars),
        "chars_median": int(statistics.median(chars)) if chars else 0,
        "chars_min": min(chars) if chars else 0,
        "chars_max": max(chars) if chars else 0,
        "content_types": dict(types),
        "images_per_chunk": dict(Counter(imgs_per)),
        "section_depth": dict(Counter(depths)),
        "chunk_len_hist": dict(sorted(hist.items())),
        "parse_blocks": dict(blocks),
        "parse_blocks_total": sum(blocks.values()),
    }


def collect_latency(cfg) -> dict:
    """检索耗时分解：查询向量化 / 向量检索 / 重排。"""
    from multimodal_kb.embed.dashscope import DashScopeEmbedder, Reranker
    from multimodal_kb.store.qdrant_store import QdrantStore

    em = DashScopeEmbedder(cfg.dashscope_api_key, cfg.fusion_model, cfg.text_model)
    rk = Reranker(cfg.dashscope_api_key, cfg.rerank_model)
    store = QdrantStore(cfg.qdrant_url, cfg.collection)

    q = "手工焊接的工艺要点是什么？"
    t0 = time.perf_counter(); v_text = em.text_one(q); t_text = (time.perf_counter()-t0)*1000
    t0 = time.perf_counter(); v_fus = em.fusion_one(q, None); t_fus = (time.perf_counter()-t0)*1000
    t0 = time.perf_counter(); hits_f = store.search_named("fusion", v_fus, 20); t_qf = (time.perf_counter()-t0)*1000
    t0 = time.perf_counter(); hits_t = store.search_named("text", v_text, 20); t_qt = (time.perf_counter()-t0)*1000

    docs = [h.content for h in (hits_f + hits_t)[:20]]
    t0 = time.perf_counter(); rk.rerank(q, docs, top_n=len(docs)); t_rk = (time.perf_counter()-t0)*1000

    # 端到端（HTTP，含重排），多测几次取中位数
    e2e = []
    for qq in QUERIES[:5]:
        t0 = time.perf_counter()
        _post(f"{MMKB}/search", {"query": qq, "top_k": 5, "rerank": True})
        e2e.append((time.perf_counter()-t0)*1000)
    norank = []
    for qq in QUERIES[:5]:
        t0 = time.perf_counter()
        _post(f"{MMKB}/search", {"query": qq, "top_k": 5, "rerank": False})
        norank.append((time.perf_counter()-t0)*1000)

    return {
        "embed_text_ms": round(t_text),
        "embed_fusion_ms": round(t_fus),
        "qdrant_fusion_ms": round(t_qf),
        "qdrant_text_ms": round(t_qt),
        "rerank_ms": round(t_rk),
        "e2e_with_rerank_ms": round(statistics.median(e2e)),
        "e2e_without_rerank_ms": round(statistics.median(norank)),
        "candidates_reranked": len(docs),
    }


def collect_routes() -> dict:
    """多路召回贡献：最终 top-5 来自哪一路；两路在更深排名上的一致性。"""
    per_query, both = [], Counter()
    overlaps, top1_same = [], 0
    for q in QUERIES:
        d = _post(f"{MMKB}/search", {"query": q, "top_k": 5, "rerank": False})
        res = d.get("results", [])
        only_f = only_t = in_both = 0
        for r in res:
            b = r.get("scores_breakdown") or {}
            f = "fusion_rank" in b
            t = "text_rank" in b
            if f and t:
                in_both += 1
            elif f:
                only_f += 1
            elif t:
                only_t += 1
        per_query.append({"q": q, "n": len(res), "only_fusion": only_f,
                          "only_text": only_t, "both": in_both})

        # 更深排名（每路各召回 20 条）看两路到底有多一致
        ex = _post(f"{MMKB}/search", {"query": q, "top_k": 20, "rerank": False,
                                      "routes": ["fusion", "text"]})
        cands = ex.get("results", [])
        fus = [c["chunk_id"] for c in cands if "fusion_rank" in (c.get("scores_breakdown") or {})]
        txt = [c["chunk_id"] for c in cands if "text_rank" in (c.get("scores_breakdown") or {})]
        if fus and txt:
            overlaps.append(len(set(fus) & set(txt)) / max(len(set(fus) | set(txt)), 1))
            if cands and (cands[0].get("scores_breakdown") or {}).get("fusion_rank") == 1 \
               and (cands[0].get("scores_breakdown") or {}).get("text_rank") == 1:
                top1_same += 1

    both["only_fusion"] = sum(p["only_fusion"] for p in per_query)
    both["only_text"] = sum(p["only_text"] for p in per_query)
    both["both"] = sum(p["both"] for p in per_query)
    return {
        "per_query": per_query,
        "summary": dict(both),
        "n_queries": len(QUERIES),
        "overlap_jaccard_median": round(statistics.median(overlaps), 3) if overlaps else None,
        "top1_same_queries": top1_same,
    }


def main() -> int:
    from multimodal_kb.config import KBConfig
    cfg = KBConfig.from_env()

    print("== 采集知识库统计 ==")
    points = scroll_all()
    kb = collect_kb(points)
    print(f"   点数 {kb['points']}｜图片 {kb['images']}｜章节 {kb['sections']}｜"
          f"解析块 {kb['parse_blocks_total']}")

    print("== 采集检索耗时分解 ==")
    lat = collect_latency(cfg)
    print(f"   {lat}")

    print("== 采集多路召回贡献 ==")
    routes = collect_routes()
    print(f"   汇总 {routes['summary']}")

    out = {"kb": kb, "latency": lat, "routes": routes}
    dest = ROOT / "docs" / "report" / "metrics.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 已写入 {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

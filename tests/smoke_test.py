#!/usr/bin/env python3
"""
冒烟测试：一次性验证整条链路的关键假设
==========================================

    export DASHSCOPE_API_KEY=sk-xxx
    python3 smoke_test.py

覆盖的检查项（都是曾经出过错或从未验证过的地方）：
  1. 依赖：核心包能否在零第三方依赖下导入
  2. Qdrant：连通性 + 集合三路向量配置（fusion/text 是 dense，image 是 multivector）
  3. 分块：确定性 id（同 doc_key 幂等、不同 doc_key 区分）
  4. 三路检索：fusion / text / image 都能返回结果
  5. 以图搜图：拿库里已有的图去搜，应命中它自己所在的 chunk（分数≈1.0）
  6. 检索服务：/health、/search、/images 三个端点
  7. Dify 适配器：空 query 探测返回空 records；错误 token 返回 403
  8. Open WebUI 适配器：确定性图片区块能被拼出来
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]     # 项目根
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail else ""))


def http(url: str, payload=None, headers=None, method=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    if data:
        r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body.decode() or "{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                # 非 JSON（如图片等二进制）——原样返回字节
                return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200]
    except Exception as e:
        return 0, str(e)[:200]


def main() -> int:
    print("=" * 70)
    print("1) 核心包导入（应零第三方依赖）")
    print("=" * 70)
    try:
        from multimodal_kb.chunk.structure import rebuild
        from multimodal_kb.embed.dashscope import DashScopeEmbedder, Reranker
        from multimodal_kb.parse.mineru import load_content_list, find_content_list
        from multimodal_kb.pipeline import KBConfig, MultimodalKBPipeline
        from multimodal_kb.search.hybrid import HybridSearcher, SearchConfig
        from multimodal_kb.store.qdrant_store import QdrantStore
        check("核心模块导入", True)
    except Exception as e:
        check("核心模块导入", False, str(e)[:120])
        return 1

    cfg = KBConfig.from_env()
    key = cfg.dashscope_api_key or os.environ.get("DASHSCOPE_API_KEY", "")

    print("\n" + "=" * 70)
    print("2) Qdrant 连通性与集合配置")
    print("=" * 70)
    store = QdrantStore(cfg.qdrant_url, cfg.collection)
    check("Qdrant 可达", store.alive(), cfg.qdrant_url)
    if not store.alive():
        return 1
    try:
        info = store.info()["result"]["config"]["params"]["vectors"]
        check("集合有 fusion 向量", "fusion" in info, f"dim={info.get('fusion',{}).get('size')}")
        check("集合有 text 向量", "text" in info, f"dim={info.get('text',{}).get('size')}")
        check("image 是 multivector", "multivector_config" in info.get("image", {}),
              f"comparator={info.get('image',{}).get('multivector_config',{}).get('comparator')}")
    except Exception as e:
        check("集合配置读取", False, str(e)[:120])
    n = store.count()
    check("库中有数据", n > 0, f"{n} 个点")

    print("\n" + "=" * 70)
    print("3) 分块：确定性 id（幂等）")
    print("=" * 70)
    from multimodal_kb.config import KBConfig
    parsed_root = Path(KBConfig.from_env().parsed_dir)
    blocks = None
    for cand in ([parsed_root] + sorted(p for p in parsed_root.glob("*") if p.is_dir())
                 if parsed_root.exists() else []):
        try:
            blocks = load_content_list(find_content_list(cand))
            break
        except Exception:
            continue
    if blocks:
        s1, c1 = rebuild(blocks, doc_key="smoke")
        s2, c2 = rebuild(blocks, doc_key="smoke")
        s3, c3 = rebuild(blocks, doc_key="other")
        check("同 doc_key 产出相同 id", [x.id for x in c1] == [x.id for x in c2], f"{len(c1)} 个块")
        check("不同 doc_key 产出不同 id", [x.id for x in c1] != [x.id for x in c3])
        check("id 无重复", len({x.id for x in c1}) == len(c1))
    else:
        check("载入解析结果", False, "找不到 content_list.json（设 MMKB_PARSED_ROOT）")

    if not key:
        print("\n⚠️  未设 DASHSCOPE_API_KEY，跳过 4~8 项")
        return 0 if not FAIL else 1

    pipe = MultimodalKBPipeline(cfg)
    print("\n" + "=" * 70)
    print("4) 三路检索连通性")
    print("=" * 70)
    q = "接地保护"
    for name, fn in [
        ("fusion 路", lambda: store.search_fusion(q, None, pipe.embedder, 3)),
        ("text 路", lambda: store.search_text(q, pipe.embedder, 3)),
    ]:
        try:
            hits = fn()
            check(name, len(hits) > 0 and hits[0].score > 0, f"{len(hits)} 条，首条 {hits[0].score:.3f}")
        except Exception as e:
            check(name, False, str(e)[:120])

    print("\n" + "=" * 70)
    print("5) 以图搜图（拿库里的图搜它自己）")
    print("=" * 70)
    # 若 parsed_root 下是"每个文档一个子目录"，取第一个存在的
    if blocks and not (parsed_root / "images").exists():
        for sub in sorted(p for p in parsed_root.glob("*") if p.is_dir()):
            if (sub / "images").exists():
                parsed_root = sub
                break
    try:
        secs, chunks = rebuild(blocks, doc_key="dianzi-100") if blocks else ([], [])
        picks = [(im.caption, im.path, c.id) for c in chunks for im in c.images
                 if im.caption.startswith("图")][:4]
        ok = 0
        for cap, path, cid in picks:
            f = parsed_root / path
            if not f.exists():
                continue
            hits = store.search_image(f, pipe.embedder, limit=5)
            if hits and hits[0].chunk_id == cid and hits[0].score > 0.99:
                ok += 1
        check("以图搜图命中自身", ok == len(picks) and ok > 0, f"{ok}/{len(picks)} Top-1，分数>0.99")
    except Exception as e:
        check("以图搜图", False, str(e)[:140])

    print("\n" + "=" * 70)
    print("6) 检索服务端点")
    print("=" * 70)
    base = os.environ.get("MMKB_PUBLIC_URL", "http://localhost:8088")
    st, body = http(f"{base}/health")
    check("GET /health", st == 200 and body.get("status") == "ok", str(body)[:80])
    st, body = http(f"{base}/search", {"query": "接地保护示意图", "top_k": 2, "rerank": True})
    ok = st == 200 and body.get("count", 0) > 0
    check("POST /search", ok, f"返回 {body.get('count') if isinstance(body, dict) else '?'} 条")
    if ok:
        img_urls = [i["url"] for r in body["results"] for i in r.get("images", [])]
        check("结果含绝对图片 URL", all(u.startswith("http") for u in img_urls) if img_urls else True,
              f"{len(img_urls)} 个")
        if img_urls:
            st2, _ = http(img_urls[0])
            check("图片可直连下载", st2 == 200, img_urls[0].split("/")[-1][:24])

    print("\n" + "=" * 70)
    print("7) Dify 适配器")
    print("=" * 70)
    token = os.environ.get("MMKB_ADAPTER_TOKEN", "mmkb-local")
    st, body = http(f"{base}/v1/retrieval",
                    {"knowledge_id": "", "query": "",
                     "retrieval_setting": {"top_k": 1, "score_threshold": 0.0}},
                    {"Authorization": f"Bearer {token}"})
    # 适配器是**可选**的：没挂载时是 404，这时跳过而不是判失败
    if st == 404:
        print("  ·  Dify 适配器未挂载（服务启动时没加 --enable-dify-adapter "
              "或 MMKB_ENABLE_DIFY_ADAPTER=1），跳过这 3 项")
    else:
        check("空 query 探测返回空 records",
              st == 200 and isinstance(body, dict) and body.get("records") == [], f"HTTP {st}")
        st, _ = http(f"{base}/v1/retrieval", {"query": "x"},
                     {"Authorization": "Bearer WRONG-TOKEN"})
        check("错误 token 返回 403", st == 403, f"HTTP {st}")
        st, body = http(f"{base}/v1/retrieval",
                        {"knowledge_id": "mmkb", "query": "接地保护",
                         "retrieval_setting": {"top_k": 1, "score_threshold": 0.0}},
                        {"Authorization": f"Bearer {token}"})
        rec = (body or {}).get("records") or []
        check("正常查询返回契约结构", st == 200 and rec and
              all(k in rec[0] for k in ("content", "score", "title", "metadata")),
              f"{len(rec)} 条")

    print("\n" + "=" * 70)
    print("8) Open WebUI 适配器（确定性图片区块）")
    print("=" * 70)
    try:
        pipe_file = ROOT / "integrations" / "openwebui" / "pipe.py"
        sys.path.insert(0, str(pipe_file.parent))
        import importlib.util
        spec = importlib.util.spec_from_file_location("owui_pipe", pipe_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        p = mod.Pipe()
        hits = p._search("接地保护示意图")
        ctx = p.build_context(hits)
        blk = p.build_image_block(hits)

        # 图片有两处来源：chunk 正文自带（检索服务给的，含唯一 URL），
        # 以及 build_image_block 补的"正文里没有的图"。
        # 关键要求是：**图片以 Markdown 语法出现在返回内容里**，且不重复、不用 <img>。
        all_text = ctx + blk
        import re as _re
        urls = _re.findall(r"!\[[^\]]*\]\(([^)]+)\)", all_text)
        check("返回内容含 Markdown 图片", len(urls) > 0, f"{len(urls)} 张")
        check("图片 URL 不重复", len(urls) == len(set(urls)),
              f"唯一 {len(set(urls))}/{len(urls)}")
        check("用的是 Markdown 而非 <img>", "<img" not in all_text)
        # 正文已含全部图时，补图区块应为空（这是去重的正确表现）
        extra = _re.findall(r"!\[[^\]]*\]\(([^)]+)\)", blk)
        check("补图区块不重复正文里的图",
              all(u not in ctx for u in extra),
              f"补了 {len(extra)} 张正文没有的图")
    except Exception as e:
        check("Open WebUI 适配器", False, str(e)[:140])

    print("\n" + "=" * 70)
    print(f"结果：{len(PASS)} 项通过，{len(FAIL)} 项失败")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

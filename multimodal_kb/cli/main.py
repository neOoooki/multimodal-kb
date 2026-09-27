#!/usr/bin/env python3
"""
kb — 多模态知识库命令行工具
================================

管理 + 召回测试 + 演示，一个入口。**零第三方依赖**（只用 argparse + 标准库）。

    ./kb status                     系统状态总览
    ./kb doctor                     全链路诊断（逐项检查并给出修复建议）
    ./kb docs                       列出已入库文档
    ./kb init --recreate            初始化/重建集合
    ./kb ingest <pdf>               入库（解析→分块→标注→向量化→存储）
    ./kb drop <doc_id>              删除某个文档的所有分块
    ./kb search "问题"               检索（带分路得分与图片）
    ./kb explain "问题"              分路详解：fusion / text 各自召回与 RRF 贡献
    ./kb image-search <图片>         以图搜图
    ./kb eval                       批量召回测试（默认用内置题集）
    ./kb compare "问题"              多配置对比：rerank 开/关、单路 vs 多路
    ./kb demo                       交互式演示（连续提问，实时看命中）
    ./kb serve                       启动检索服务

环境变量（全部可选，见 multimodal_kb/config.py）：
    DASHSCOPE_API_KEY   百炼 Key（检索/入库必需）
    MINERU_TOKEN        MinerU 官方 API Token（入库时用）
    QDRANT_URL          默认 http://localhost:6333
    MMKB_COLLECTION     默认 mmkb
    MMKB_WORK_DIR       缓存目录，默认 <项目>/data/work
    MMKB_PARSED_DIR     解析产物目录，默认 <项目>/data/parsed
    MMKB_PUBLIC_URL     图片直连前缀，默认 http://localhost:8088
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# 包内运行：项目根 = cli/main.py 的上上级
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# 输出工具（无依赖的 ANSI 着色 + 简单排版）
# ---------------------------------------------------------------------------
_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _COLOR else s


def bold(s):   return _c("1", s)
def dim(s):    return _c("2", s)
def red(s):    return _c("31", s)
def green(s):  return _c("32", s)
def yellow(s): return _c("33", s)
def blue(s):   return _c("34", s)
def cyan(s):   return _c("36", s)


def hr(ch: str = "─", n: int = 74):
    print(dim(ch * n))


def title(s: str):
    print()
    print(bold(s))
    hr("═")


def ok(s):   print(f"  {green('✅')} {s}")
def bad(s):  print(f"  {red('❌')} {s}")
def warn(s): print(f"  {yellow('⚠️ ')} {s}")
def info(s): print(f"  {dim('·')} {s}")


def dwidth(s: str) -> int:
    """显示宽度：CJK 字符占 2 列，否则对齐会歪。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def pad(s: str, width: int, align: str = "left") -> str:
    """按显示宽度补齐/截断。"""
    s = str(s)
    while dwidth(s) > width:
        s = s[:-1]
    gap = width - dwidth(s)
    return (s + " " * gap) if align == "left" else (" " * gap + s)


def score_bar(score: float, width: int = 18, scale: float = 1.0) -> str:
    """
    把分数画成可视化条。

    `scale` 是参考最大值 —— RRF 融合分只有 0.01~0.03 量级，
    直接用原值画会全是空的，所以要按本次结果的最大值归一化。
    """
    if scale <= 0:
        scale = 1.0
    ratio = score / scale if scale != 1.0 else score
    filled = max(1 if score > 0 else 0, min(width, int(round(ratio * width))))
    color = green if ratio >= 0.8 else (yellow if ratio >= 0.5 else dim)
    return color("█" * filled) + dim("░" * (width - filled))


# ---------------------------------------------------------------------------
# 惰性加载（只读命令不需要 API Key）
# ---------------------------------------------------------------------------
def load_cfg(**over):
    from multimodal_kb.pipeline import KBConfig
    return KBConfig.from_env(**over)


def load_pipe(cfg=None, need_key: bool = True):
    from multimodal_kb.pipeline import MultimodalKBPipeline
    cfg = cfg or load_cfg()
    if need_key and not cfg.dashscope_api_key:
        print(red("缺少 DASHSCOPE_API_KEY（检索需要对 query 做向量化）"))
        print("  设置： export DASHSCOPE_API_KEY=sk-xxx")
        raise SystemExit(2)
    return MultimodalKBPipeline(cfg)


def parsed_root() -> Path:
    """解析产物目录（由 MMKB_PARSED_DIR 决定，默认 <项目>/data/parsed）。"""
    from multimodal_kb.config import KBConfig
    return Path(KBConfig.from_env().parsed_dir)


def load_svc(pipe=None):
    from multimodal_kb.service.app import KBSearchService
    cfg = load_cfg()
    pipe = pipe or load_pipe(cfg)
    pub = os.environ.get("MMKB_PUBLIC_URL", "http://localhost:8088")
    return KBSearchService(pipe, public_base_url=pub, parsed_root=parsed_root())


# ---------------------------------------------------------------------------
# 归一化：把 RetrievedChunk 转成与 HTTP 服务一致的 dict（带绝对图片 URL）
# ---------------------------------------------------------------------------
def _norm(h, public_base_url: str | None = None) -> dict:
    """接受 dict（服务层输出）或 RetrievedChunk（核心层输出），统一成一种形状。"""
    pub = public_base_url or os.environ.get("MMKB_PUBLIC_URL", "http://localhost:8088")
    if isinstance(h, dict):
        return h
    doc_id = getattr(h, "doc_id", "") or ""
    imgs = []
    for im in getattr(h, "images", []) or []:
        name = Path(getattr(im, "path", "") or "").name
        imgs.append({
            "url": f"{pub}/images/{doc_id}/{name}" if name else "",
            "path": getattr(im, "path", ""),
            "caption": getattr(im, "caption", ""),
            "description": getattr(im, "description", ""),
            "page": getattr(im, "page", None),
        })
    return {
        "chunk_id": getattr(h, "chunk_id", ""),
        "content": getattr(h, "content", ""),
        "score": getattr(h, "score", 0.0),
        "section_path": getattr(h, "section_path", []) or [],
        "page": getattr(h, "page", None),
        "doc_id": doc_id,
        "doc_name": getattr(h, "doc_name", ""),
        "images": imgs,
        "scores_breakdown": getattr(h, "scores_breakdown", {}) or {},
    }


def do_search(query: str, top_k: int = 5, rerank: bool = True,
              routes=None, recall: int = 20, query_image=None):
    """
    统一检索入口，返回 (结果 dict 列表, 耗时 ms)。

    走 service 层而不是直接调 store：service 会把图片路径改写成**绝对 URL**，
    演示/评测时能直接点开看，与 HTTP 接口的输出也完全一致。
    """
    from multimodal_kb.search.hybrid import SearchConfig
    pipe = load_pipe()
    cfg = SearchConfig(
        top_k=top_k, rerank=rerank, recall_per_route=recall,
        use_fusion=(routes is None or "fusion" in routes),
        use_text=(routes is None or "text" in routes),
        use_image=bool(query_image),
    )
    t0 = time.time()
    hits = pipe.searcher(cfg).search(query, query_image=query_image)
    dt = (time.time() - t0) * 1000
    pub = os.environ.get("MMKB_PUBLIC_URL", "http://localhost:8088")
    return [_norm(h, pub) for h in hits], dt


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def render_hit(i: int, h: dict, show_content: int = 200, show_images: bool = True):
    path = " > ".join(h.get("section_path") or [])
    head = f"[{i}] {bold(path)}" if path else f"[{i}] {dim('（无标题）')}"
    if h.get("page"):
        head += dim(f"  第 {h['page']} 页")
    print(f"\n{head}")
    hsc = h.get("score", 0.0)
    print(f"    {score_bar(hsc)}  {bold(f'{hsc:.4f}')}  {dim(h.get('chunk_id','')[:12])}")
    bd = h.get("scores_breakdown") or {}
    if bd:
        bits = []
        for k in ("fusion_rank", "text_rank", "image_rank"):
            if k in bd:
                bits.append(f"{k.split('_')[0]}={bd[k]}")
        if "rerank" in bd:
            bits.append(f"rerank={bd['rerank']:.3f}")
        elif "rrf" in bd:
            bits.append(f"rrf={bd['rrf']:.4f}")
        if bits:
            print(f"    {dim('、'.join(bits))}")
    content = (h.get("content") or "").replace("\n", " ")
    if len(content) > show_content:
        content = content[:show_content] + "…"
    print(f"    {content}")
    if show_images and h.get("images"):
        for im in h["images"]:
            cap = im.get("caption") or "插图"
            print(f"    {cyan('🖼')} {cap}  {dim(im.get('url',''))}")


def render_results(results: list[dict], show_content: int = 200, show_images: bool = True):
    if not results:
        warn("没有召回任何结果")
        return
    for i, h in enumerate(results, 1):
        render_hit(i, h, show_content, show_images)


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------
def cmd_status(args) -> int:
    cfg = load_cfg()
    title("系统状态")

    print(f"  {bold('配置')}")
    info(f"Qdrant        {cfg.qdrant_url}")
    info(f"集合          {cfg.collection}")
    info(f"工作目录      {cfg.work_dir}")
    info(f"融合模型      {cfg.fusion_model}")
    info(f"文本模型      {cfg.text_model}")
    info(f"视觉模型      {cfg.vision_model}")
    info(f"重排模型      {cfg.rerank_model}")
    info(f"解析方式      {cfg.mineru_mode}")

    print(f"\n  {bold('依赖')}")
    info(f"DASHSCOPE_API_KEY  {'已设置' if cfg.dashscope_api_key else red('未设置')}")
    info(f"MINERU_TOKEN       {'已设置' if cfg.mineru_token else dim('未设置（仅本地解析时需要）')}")

    print(f"\n  {bold('存储')}")
    from multimodal_kb.store.qdrant_store import QdrantStore
    st = QdrantStore(cfg.qdrant_url, cfg.collection)
    if not st.alive():
        bad(f"Qdrant 不可达：{cfg.qdrant_url}")
        info("启动： docker run -d --name mmkb-qdrant -p 6333:6333 \\")
        info("         -v $PWD/data/qdrant:/qdrant/storage qdrant/qdrant:latest")
        return 1
    ok(f"Qdrant 在线（{st.alive() and cfg.qdrant_url}）")
    try:
        info(f"集合 {cfg.collection}：{bold(str(st.count()))} 个点")
        vecs = st.info()["result"]["config"]["params"]["vectors"]
        for name, v in vecs.items():
            kind = "multivector(max_sim)" if "multivector_config" in v else "dense"
            info(f"  {name:8s} dim={v.get('size'):<6} {kind}")
    except Exception as e:
        warn(f"集合不存在或不可读：{str(e)[:80]}")

    print(f"\n  {bold('文档')}")
    try:
        docs = st.list_documents()
        if not docs:
            info("（空）")
        for d in docs:
            info(f"{d['doc_name'] or d['doc_id']}  "
                 f"分块 {d['chunks']}  图 {d['images']}  表 {d['tables']}  页 {d['pages']}")
    except Exception as e:
        warn(str(e)[:100])
    return 0


def cmd_doctor(args) -> int:
    """逐项体检，给出可执行的修复建议。"""
    title("全链路诊断")
    problems = []

    cfg = load_cfg()
    from multimodal_kb.store.qdrant_store import QdrantStore
    st = QdrantStore(cfg.qdrant_url, cfg.collection)

    # 1) Qdrant
    if st.alive():
        ok("Qdrant 可达")
    else:
        bad("Qdrant 不可达")
        problems.append("启动 Qdrant：docker run -d --name mmkb-qdrant -p 6333:6333 "
                        "-v $PWD/data/qdrant:/qdrant/storage qdrant/qdrant:latest")
        return _doctor_report(problems)

    # 2) 集合与向量配置
    try:
        vecs = st.info()["result"]["config"]["params"]["vectors"]
        need = {"fusion": False, "text": False, "image": True}
        for name, want_mv in need.items():
            v = vecs.get(name)
            if not v:
                bad(f"集合缺少 {name} 向量")
                problems.append(f"重建集合： ./kb init --recreate")
            elif want_mv and "multivector_config" not in v:
                bad(f"{name} 应为 multivector，实际是 dense")
                problems.append("重建集合： ./kb init --recreate")
            else:
                ok(f"{name} 向量配置正确（dim={v.get('size')}）")
    except Exception:
        bad("集合不存在")
        problems.append("初始化集合： ./kb init")
        return _doctor_report(problems)

    # 3) 数据量
    n = st.count()
    if n > 0:
        ok(f"库中有 {n} 个点")
    else:
        bad("库是空的")
        problems.append("入库： ./kb ingest <pdf>")

    # 4) 解析产物（图片能否直连）
    parsed = parsed_root()
    if parsed.exists():
        imgs = list(parsed.rglob("images/*.jpg")) + list(parsed.rglob("images/*.png"))
        ok(f"解析产物存在：{parsed}（{len(imgs)} 张图）")
    else:
        bad(f"解析产物目录不存在：{parsed}")
        problems.append(f"设置 MMKB_PARSED_DIR 或重新入库（当前 {parsed}）")

    # 5) API Key
    if cfg.dashscope_api_key:
        ok("DASHSCOPE_API_KEY 已设置")
        try:
            p = load_pipe(cfg)
            v = p.embedder.text_one("健康检查")
            ok(f"embedding 可用（返回 {len(v)} 维）")
        except Exception as e:
            bad(f"embedding 调用失败：{str(e)[:120]}")
            problems.append("检查 DASHSCOPE_API_KEY 是否有效/有额度")
    else:
        bad("DASHSCOPE_API_KEY 未设置")
        problems.append("export DASHSCOPE_API_KEY=sk-xxx  （阿里云百炼控制台获取）")

    # 6) 检索服务
    import urllib.error
    import urllib.request
    pub = os.environ.get("MMKB_PUBLIC_URL", "http://localhost:8088")
    try:
        with urllib.request.urlopen(pub + "/health", timeout=5) as r:
            d = json.loads(r.read().decode())
        ok(f"检索服务在线（{d.get('points')} 点）")
        # 7) 图片直连
        if n > 0:
            target = None
            for pt in st.scroll_all(limit=200):
                pl = pt.get("payload") or {}
                for im in pl.get("images") or []:
                    if im.get("path"):
                        target = (pl.get("doc_id"), Path(im["path"]).name)
                        break
                if target:
                    break
            if target:
                u = f"{pub}/images/{target[0]}/{target[1]}"
                try:
                    with urllib.request.urlopen(u, timeout=5) as r:
                        ok(f"图片可直连（HTTP {r.status}, {len(r.read())} bytes）")
                except Exception as e:
                    bad(f"图片不可直连：{str(e)[:80]}")
                    problems.append("检查 MMKB_PARSED_DIR 是否指向正确的解析目录")
            else:
                warn("库里没有带图的分块，跳过图片直连检查")
    except Exception:
        warn(f"检索服务未运行（{pub}）")
        info("启动： ./kb serve      （仅本地检索不需要，CLI 会直接连 Qdrant）")

    return _doctor_report(problems)


def _doctor_report(problems: list[str]) -> int:
    title("结论")
    if not problems:
        ok(bold("全部通过，可以使用"))
        return 0
    print(f"  {yellow(f'发现 {len(problems)} 个待处理项：')}")
    for i, p in enumerate(problems, 1):
        print(f"    {i}. {p}")
    return 1


def cmd_docs(args) -> int:
    cfg = load_cfg()
    from multimodal_kb.store.qdrant_store import QdrantStore
    st = QdrantStore(cfg.qdrant_url, cfg.collection)
    title("已入库文档")
    docs = st.list_documents()
    if not docs:
        warn("库是空的。先入库： ./kb ingest <pdf>")
        return 1
    w = max((dwidth(d["doc_name"] or d["doc_id"]) for d in docs), default=10)
    w = max(w, 4)
    print(f"  {pad('文档', w)}  {pad('分块', 6, 'right')} {pad('图', 5, 'right')} "
          f"{pad('表', 5, 'right')} {pad('页', 5, 'right')}   文档 ID")
    hr()
    for d in docs:
        print(f"  {pad(d['doc_name'] or d['doc_id'], w)}  {pad(d['chunks'], 6, 'right')} "
              f"{pad(d['images'], 5, 'right')} {pad(d['tables'], 5, 'right')} "
              f"{pad(d['pages'], 5, 'right')}   {dim(d['doc_id'])}")
    print()
    info(f"合计 {len(docs)} 个文档，{sum(d['chunks'] for d in docs)} 个分块")
    return 0


def cmd_init(args) -> int:
    pipe = load_pipe(need_key=False)
    title("初始化存储")
    pipe.init_store(recreate=args.recreate)
    ok("完成")
    return 0


def cmd_ingest(args) -> int:
    cfg = load_cfg()
    pipe = load_pipe(cfg)
    title(f"入库：{Path(args.pdf).name}")
    parsed = args.parsed_dir
    if not parsed:
        cand = parsed_root() / Path(args.pdf).stem
        if cand.exists():
            parsed = str(cand)
            info(f"复用已有解析产物：{parsed}")
    stats = pipe.ingest_pdf(args.pdf, doc_name=args.name, parsed_dir=parsed,
                            doc_id=args.doc_id, limit_chunks=args.limit)
    title("入库完成")
    for k, v in stats.items():
        info(f"{k:10s} {v}")
    return 0


def cmd_drop(args) -> int:
    pipe = load_pipe(need_key=False)
    title(f"删除文档：{args.doc_id}")
    if not args.yes:
        print(f"  将删除 doc_id={bold(args.doc_id)} 的所有分块。")
        if input("  确认？(yes/N) ").strip().lower() != "yes":
            print("  已取消")
            return 0
    before = pipe.store.count()
    pipe.store.delete_by_doc(args.doc_id)
    after = pipe.store.count()
    ok(f"已删除 {before - after} 个点（剩余 {after}）")
    return 0


def _do_search(args):
    """按命令行参数执行一次检索。"""
    return do_search(
        args.query,
        top_k=args.top_k,
        rerank=not getattr(args, "no_rerank", False),
        routes=getattr(args, "routes", None),
        recall=getattr(args, "recall", 20),
    )


def cmd_search(args) -> int:
    title(f"检索：{args.query}")
    hits, dt = _do_search(args)
    r = args.routes or ["fusion", "text"]
    print(f"  {dim(f'路由 {{{{+}}}}{chr(32).join(r)}  rerank={not args.no_rerank}  '
                    f'召回/路={args.recall}')}".replace("{{+}}", "+"))
    print(f"  {green(f'{len(hits)} 条命中')}  {dim(f'{dt:.0f} ms')}")
    render_results(hits, show_content=args.width)
    _print_image_hint(hits)
    return 0 if hits else 1


def _print_image_hint(hits):
    urls = []
    for h in hits:
        for im in h.get("images") or []:
            u = im.get("url") or im.get("path")
            if u and u not in urls:
                urls.append(u)
    if urls:
        print()
        info(f"本次共 {len(urls)} 张图；若走 HTTP 服务，URL 为 "
             f"{os.environ.get('MMKB_PUBLIC_URL','http://localhost:8088')}/images/...")


def cmd_explain(args) -> int:
    """分路详解：每条路由各自召回了什么、RRF 怎么融合的。"""
    from multimodal_kb.search.hybrid import SearchConfig
    pipe = load_pipe()
    title(f"分路详解：{args.query}")

    routes = {}
    for name, fn in [
        ("fusion", lambda: pipe.store.search_fusion(args.query, None, pipe.embedder, args.recall)),
        ("text",   lambda: pipe.store.search_text(args.query, pipe.embedder, args.recall)),
    ]:
        t0 = time.time()
        try:
            routes[name] = fn()
            print(f"  {bold(name):<20} 召回 {len(routes[name])} 条  {dim(f'{(time.time()-t0)*1000:.0f} ms')}")
        except Exception as e:
            routes[name] = []
            bad(f"{name} 失败：{str(e)[:100]}")

    for name, hits in routes.items():
        print(f"\n  {bold(name + ' 路 Top5')}")
        hr()
        for i, h in enumerate(hits[:5], 1):
            path = " > ".join(h.section_path[-1:]) or "（无标题）"
            print(f"    {i}. {score_bar(h.score, 12)} {h.score:.4f}  {path[:38]}")
            print(f"       {dim(h.content[:70].replace(chr(10),' '))}")

    # RRF 融合
    fused = pipe.searcher(SearchConfig(top_k=args.top_k, recall_per_route=args.recall)).rrf(
        [[h.chunk_id for h in hits] for hits in routes.values() if hits])
    print(f"\n  {bold('RRF 融合结果')}")
    hr()
    by_id = {h.chunk_id: h for hits in routes.values() for h in hits}
    for rank, (cid, sc) in enumerate(sorted(fused.items(), key=lambda x: -x[1])[:args.top_k], 1):
        h = by_id.get(cid)
        contrib = []
        for name, hits in routes.items():
            for i, hh in enumerate(hits, 1):
                if hh.chunk_id == cid:
                    contrib.append(f"{name}#{i}")
        print(f"    {rank}. {score_bar(sc * 30, 12)} {sc:.5f}  {dim('+'.join(contrib))}")
        if h:
            print(f"       {h.content[:70].replace(chr(10),' ')}")
    print()
    info("RRF 只用**排名**不用原始分数，所以不同量纲的多路可以安全融合")
    return 0


def cmd_image_search(args) -> int:
    pipe = load_pipe()
    p = Path(args.image)
    if not p.exists():
        bad(f"文件不存在：{p}")
        return 1
    title(f"以图搜图：{p.name}")
    t0 = time.time()
    hits = pipe.store.search_image(p, pipe.embedder, args.top_k)
    print(f"  {green(f'{len(hits)} 条命中')}  {dim(f'{(time.time()-t0)*1000:.0f} ms')}")
    for i, h in enumerate(hits, 1):
        path = " > ".join(h.section_path)
        print(f"\n  [{i}] {bold(path)}")
        print(f"      {score_bar(h.score)} {h.score:.4f}")
        print(f"      {h.content[:120].replace(chr(10),' ')}")
        if h.images:
            print(f"      {cyan('🖼')} 命中图：{dim(h.images[0].path)}")
    return 0


DEFAULT_EVALSET = ROOT / "tests" / "evalsets" / "dianzi.json"


def _load_evalset(path: str | None) -> list[dict]:
    p = Path(path) if path else DEFAULT_EVALSET
    if not p.exists():
        return []
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("questions") if isinstance(d, dict) else d


def cmd_eval(args) -> int:
    from multimodal_kb.search.hybrid import SearchConfig
    pipe = load_pipe()
    qs = _load_evalset(args.file)
    if not qs:
        bad(f"题集不存在：{args.file or DEFAULT_EVALSET}")
        return 1

    title(f"召回测试（{len(qs)} 题）")
    print(f"  {dim(f'top_k={args.top_k}  rerank={not args.no_rerank}')}\n")

    hits_ok = 0
    rows = []
    for q in qs:
        query = q["q"] if isinstance(q, dict) else str(q)
        expect = (q.get("expect") or []) if isinstance(q, dict) else []
        sc = SearchConfig(top_k=args.top_k, rerank=not args.no_rerank,
                          recall_per_route=args.recall)
        t0 = time.time()
        hits = pipe.searcher(sc).search(query)
        dt = (time.time() - t0) * 1000
        joined = " ".join(h.content for h in hits)
        # 命中判定：期望关键词是否出现在召回内容里
        hit = all(k in joined for k in expect) if expect else bool(hits)
        hits_ok += hit
        rows.append((query, hit, hits[0].score if hits else 0.0, len(hits), dt))

    print(f"  {pad('问题', 36)} {pad('命中', 6)} {pad('首条分', 8, 'right')} "
          f"{pad('条数', 6, 'right')} {pad('耗时', 9, 'right')}")
    hr()
    for query, hit, sc_, n, dt in rows:
        mark = green("✅") if hit else red("❌")
        print(f"  {pad(query, 36)} {pad(mark, 6)} {pad(f'{sc_:.4f}', 8, 'right')} "
              f"{pad(n, 6, 'right')} {pad(f'{dt:.0f}ms', 9, 'right')}")
    print()
    rate = hits_ok / len(qs) * 100
    color = green if rate >= 80 else (yellow if rate >= 50 else red)
    print(f"  {bold('命中率')}：{color(f'{hits_ok}/{len(qs)} = {rate:.0f}%')}")
    if args.llm:
        _answer_all(pipe, [r[0] for r in rows])
    return 0 if hits_ok == len(qs) else 0


def _answer_all(pipe, queries):
    import urllib.request
    cfg = pipe.cfg
    print()
    title("LLM 回答（基于召回内容）")
    for q in queries:
        from multimodal_kb.search.hybrid import SearchConfig
        hits = pipe.searcher(SearchConfig(top_k=4, rerank=True)).search(q)
        ctx = "\n\n".join(h.content for h in hits)
        body = {"model": "qwen3-vl-flash", "messages": [
            {"role": "system", "content": "只依据资料回答；资料没有的，回答「资料中未提及」。"},
            {"role": "user", "content": f"资料：\n{ctx}\n\n问题：{q}"}],
            "max_tokens": 300, "temperature": 0.2}
        r = urllib.request.Request(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            data=json.dumps(body).encode(), method="POST")
        r.add_header("Authorization", "Bearer " + cfg.dashscope_api_key)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=120) as resp:
                ans = json.loads(resp.read().decode())["choices"][0]["message"]["content"]
        except Exception as e:
            ans = f"（调用失败：{str(e)[:80]}）"
        print(f"\n  {bold(q)}")
        print(f"    {ans.strip()[:300]}")


def cmd_compare(args) -> int:
    """多配置对比：直观展示每个开关的作用。"""
    from multimodal_kb.search.hybrid import SearchConfig
    pipe = load_pipe()
    title(f"配置对比：{args.query}")

    configs = [
        ("仅文本向量",        SearchConfig(top_k=args.top_k, use_fusion=False, use_text=True,  rerank=False)),
        ("仅融合向量",        SearchConfig(top_k=args.top_k, use_fusion=True,  use_text=False, rerank=False)),
        ("融合+文本 (RRF)",   SearchConfig(top_k=args.top_k, use_fusion=True,  use_text=True,  rerank=False)),
        ("融合+文本 +rerank", SearchConfig(top_k=args.top_k, use_fusion=True,  use_text=True,  rerank=True)),
    ]
    for name, sc in configs:
        t0 = time.time()
        hits = [_norm(h) for h in pipe.searcher(sc).search(args.query)]
        dt = (time.time() - t0) * 1000
        print(f"\n  {bold(name)}  {dim(f'{dt:.0f} ms')}")
        hr()
        mx = max((h["score"] for h in hits), default=1.0)
        # RRF 分很小 → 用相对刻度；rerank 分本身可读 → 用绝对刻度
        scale = mx if mx < 0.2 else 1.0
        for i, h in enumerate(hits, 1):
            path = h.get("section_path") or []
            last = path[-1] if path else "（无标题）"
            print(f"    {i}. {score_bar(h['score'], 10, scale)} {h['score']:.4f}  {last[:44]}")
            print(f"       {dim(h['content'][:66].replace(chr(10),' '))}")
    print()
    info("看首条命中的差异最直观：rerank 常把真正相关的那条从后面提到第一位")
    return 0


def cmd_demo(args) -> int:
    """交互式演示：连续提问，实时展示检索 → 图片 → 回答。"""
    pipe = load_pipe()
    title("交互式演示")
    print(f"  输入问题回车检索；{dim('直接回车退出')}；{dim('输入 :img <路径> 以图搜图')}")
    print(f"  当前配置：top_k={args.top_k}  rerank={not args.no_rerank}\n")

    while True:
        try:
            q = input(bold("问题> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            break
        if q.startswith(":img "):
            args.image = q[5:].strip()
            cmd_image_search(args)
            continue
        try:
            hits, dt = do_search(q, top_k=args.top_k, rerank=not args.no_rerank)
            print(f"\n  {green(f'{len(hits)} 条')} {dim(f'{dt:.0f} ms')}")
            render_results(hits, show_content=args.width)
            if args.llm:
                _answer_all(pipe, [q])
            print()
        except Exception as e:
            bad(str(e)[:200])
    print("再见")
    return 0


def cmd_serve(args) -> int:
    """启动检索服务（进程内，不依赖任何额外脚本）。"""
    from multimodal_kb.service.app import KBSearchService, serve
    cfg = load_cfg()
    cfg.service_port = args.port
    cfg.service_host = args.host
    if args.enable_dify_adapter:
        cfg.enable_dify_adapter = True   # type: ignore[attr-defined]
    pipe = load_pipe(cfg)
    svc = KBSearchService(pipe, public_base_url=cfg.public_base_url,
                          parsed_root=Path(cfg.parsed_dir))

    routes = {}
    if args.enable_dify_adapter:
        # 适配器在 integrations/ 下，**不属于核心包**，所以这里做运行时可选加载：
        # 从源码运行时能找到；pip 安装后 integrations/ 不在包里，会给出明确提示。
        adapter_dir = ROOT / "integrations" / "dify"
        if not (adapter_dir / "external_kb.py").exists():
            print(yellow("未找到 integrations/dify/external_kb.py"))
            print(dim("  （适配器不在核心包里；请从源码目录运行，或直接用 HTTP 请求节点"
                      "——见 docs/chat-integration.md）"))
        else:
            sys.path.insert(0, str(adapter_dir))
            try:
                # 动态导入：让"这是可选的外部适配器、不是核心依赖"在代码里显式可见
                import importlib
                install_dify = importlib.import_module("external_kb").install
                routes.update(install_dify(svc, bearer_token=args.adapter_token))
                print(f"已挂载 Dify 适配器 /v1/retrieval"
                      f"{'（已启用 Bearer 校验）' if args.adapter_token else '（未启用鉴权）'}")
            except Exception as e:
                print(yellow(f"挂载 Dify 适配器失败：{e}"))

    serve(svc, host=cfg.service_host, port=cfg.service_port, extra_routes=routes)
    return 0


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="kb", description="多模态知识库命令行工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""常用示例：
  ./kb doctor                          体检
  ./kb search "接地保护示意图"           检索
  ./kb compare "手工焊接的步骤"          看 rerank 的作用
  ./kb eval --llm                       批量召回测试 + LLM 回答
  ./kb demo                             交互式演示
""")
    sub = ap.add_subparsers(dest="cmd", metavar="<命令>")

    sub.add_parser("status", help="系统状态总览").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="全链路诊断并给出修复建议").set_defaults(func=cmd_doctor)
    sub.add_parser("docs", help="列出已入库文档").set_defaults(func=cmd_docs)

    p = sub.add_parser("init", help="初始化集合")
    p.add_argument("--recreate", action="store_true", help="删除并重建（会清空数据）")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("ingest", help="入库 PDF")
    p.add_argument("pdf")
    p.add_argument("--name", help="文档名（默认取文件名）")
    p.add_argument("--doc-id", help="文档 ID（默认随机；固定它可让重复入库幂等）")
    p.add_argument("--parsed-dir", help="复用已有的 MinerU 解析产物目录")
    p.add_argument("--limit", type=int, default=0, help="只入前 N 个子块（试跑）")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("drop", help="删除某个文档的所有分块")
    p.add_argument("doc_id")
    p.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    p.set_defaults(func=cmd_drop)

    def add_search_args(p):
        p.add_argument("query")
        p.add_argument("-k", "--top-k", type=int, default=5)
        p.add_argument("--recall", type=int, default=20, help="每路召回条数")
        p.add_argument("--no-rerank", action="store_true")
        p.add_argument("--routes", nargs="+", choices=["fusion", "text"], help="只用指定路由")
        p.add_argument("--width", type=int, default=200, help="正文显示字符数")

    p = sub.add_parser("search", help="检索")
    add_search_args(p)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("explain", help="分路详解（各路召回 + RRF 贡献）")
    p.add_argument("query")
    p.add_argument("-k", "--top-k", type=int, default=5)
    p.add_argument("--recall", type=int, default=10)
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("image-search", help="以图搜图")
    p.add_argument("image")
    p.add_argument("-k", "--top-k", type=int, default=5)
    p.set_defaults(func=cmd_image_search)

    p = sub.add_parser("eval", help="批量召回测试")
    p.add_argument("-f", "--file", help="题集 JSON（默认内置）")
    p.add_argument("-k", "--top-k", type=int, default=4)
    p.add_argument("--recall", type=int, default=20)
    p.add_argument("--no-rerank", action="store_true")
    p.add_argument("--llm", action="store_true", help="同时跑 LLM 生成回答")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("compare", help="多配置对比（直观展示各开关的作用）")
    p.add_argument("query")
    p.add_argument("-k", "--top-k", type=int, default=3)
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("demo", help="交互式演示")
    p.add_argument("-k", "--top-k", type=int, default=5)
    p.add_argument("--no-rerank", action="store_true")
    p.add_argument("--llm", action="store_true", help="每问都调 LLM 生成回答")
    p.add_argument("--width", type=int, default=160)
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("serve", help="启动检索服务")
    p.add_argument("-p", "--port", type=int, default=8088)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--enable-dify-adapter", action="store_true",
                   help="同时挂载 Dify 外部知识库接口 /v1/retrieval")
    p.add_argument("--adapter-token", default=os.environ.get("MMKB_ADAPTER_TOKEN"),
                   help="Dify 适配器的 Bearer token（不填则不鉴权）")
    p.set_defaults(func=cmd_serve)

    return ap


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130
    except SystemExit:
        raise
    except Exception as e:
        print(red(f"\n出错：{type(e).__name__}: {e}"))
        if os.environ.get("KB_DEBUG"):
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

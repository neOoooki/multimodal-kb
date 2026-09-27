"""
检索服务（零依赖 HTTP 服务）
==============================

**只用 Python 标准库**，不引入 FastAPI/uvicorn —— 便于迁移到任何环境。
如果将来要换 ASGI，只需替换本文件的 HTTP 层，下面的 `KBSearchService` 不用动。

提供的接口
----------
  GET  /health                     健康检查
  POST /search                     框架无关的检索接口（核心）
  GET  /images/{doc_id}/{name}     图片直连（前端渲染用）
  POST /v1/retrieval               Dify External Knowledge API 契约（适配层挂载）

关于图片
--------
chunk 正文里的图片是**相对路径**（`images/xxx.jpg`），
返回前必须改写成**前端可直连的绝对 URL**，否则渲染不出来。
"""

from __future__ import annotations

import json
import mimetypes
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from multimodal_kb.models import RetrievedChunk

IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\((?!https?://|data:)([^)]+)\)")


class KBSearchService:
    """搜索 + 文档服务的业务逻辑（不依赖 HTTP 框架）。"""

    def __init__(self, pipeline, public_base_url: str = "http://localhost:8088",
                 parsed_root: str | Path | None = None):
        self.pipe = pipeline
        self.public_base_url = public_base_url.rstrip("/")
        self.parsed_root = Path(parsed_root) if parsed_root else pipeline.work / "parsed"

    # -- 图片路径改写 -----------------------------------------------------
    def resolve_images(self, content: str, doc_id: str) -> str:
        """
        把 `![](images/x.jpg)` 改写成 `![](http://host/images/{doc_id}/x.jpg)`。

        必须做这一步：前端拿到的相对路径无从解析。
        """
        def sub(m):
            alt, path = m.group(1), m.group(2)
            name = Path(path).name
            return f"![{alt}]({self.public_base_url}/images/{doc_id}/{name})"
        return IMG_MD_RE.sub(sub, content)

    def _decorate(self, hits: list[RetrievedChunk]) -> list[dict]:
        out = []
        for h in hits:
            content = self.resolve_images(h.content, h.doc_id)
            out.append({
                "chunk_id": h.chunk_id,
                "content": content,
                "score": h.score,
                "section_path": h.section_path,
                "page": h.page,
                "doc_id": h.doc_id,
                "doc_name": h.doc_name,
                "images": [
                    {"url": f"{self.public_base_url}/images/{h.doc_id}/{Path(i.path).name}",
                     "path": i.path, "caption": i.caption, "description": i.description,
                     "page": i.page}
                    for i in h.images
                ],
                "scores_breakdown": h.scores_breakdown,
            })
        return out

    # -- 检索 -------------------------------------------------------------
    @staticmethod
    def format_as_text(results: list[dict]) -> str:
        """
        把检索结果渲染成**可直接喂给 LLM 的纯文本**。

        图片以 Markdown 语法内联（`![图注](绝对URL)`），所以只要下游把这段文本
        原样保留，图片就会出现在最终回答里 —— 这是给 Dify 的 HTTP 请求节点用的，
        因为 Dify 的 answer 节点只能输出文本，没有别的确定性注入途径。
        """
        blocks = []
        for i, r in enumerate(results, 1):
            path = " > ".join(r.get("section_path") or [])
            head = f"[{i}] {path}" if path else f"[{i}]"
            if r.get("page"):
                head += f"（第 {r['page']} 页）"
            blocks.append(f"{head}\n{r.get('content', '')}")
        return "\n\n---\n\n".join(blocks)

    def search(self, query: str, top_k: int = 5, rerank: bool = True,
               query_image: str | None = None,
               routes: list[str] | None = None) -> list[dict]:
        from multimodal_kb.search.hybrid import SearchConfig
        cfg = SearchConfig(
            top_k=top_k, rerank=rerank,
            use_fusion=("fusion" in routes) if routes else True,
            use_text=("text" in routes) if routes else True,
            use_image=bool(query_image),
        )
        hits = self.pipe.searcher(cfg).search(query, query_image=query_image)
        return self._decorate(hits)

    # -- 图片文件 ---------------------------------------------------------
    def find_image(self, doc_id: str, name: str) -> Path | None:
        """在所有已解析目录里找这张图。"""
        name = Path(name).name
        for candidate in self.parsed_root.rglob(name):
            if candidate.is_file():
                return candidate
        return None


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------
def make_handler(svc: KBSearchService, extra_routes: dict | None = None):
    """
    extra_routes: {(method, path_prefix): handler}
    handler 签名: (handler_self, payload_or_None, path_params) -> (status, body_dict)
    适配器用这个挂载自己的契约（如 Dify 的 /v1/retrieval）。
    """
    extra_routes = extra_routes or {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "mmkb/1.0"

        def log_message(self, fmt, *args):
            # 打访问日志：排查"框架说检索为空"时，第一件事是确认请求有没有到服务端
            sys.stderr.write("[access] %s - %s\n" % (self.address_string(), fmt % args))
            sys.stderr.flush()

        # -- 工具 --
        def _send(self, status: int, body: dict | bytes, ctype="application/json",
                  extra_headers=None):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode() or "{}")
            except Exception:
                return {}

        def do_OPTIONS(self):
            self._send(204, b"")

        # -- 路由 --
        def do_GET(self):
            parsed = urlparse(self.path)
            path = unquote(parsed.path)

            if path == "/health":
                # 如实上报：Qdrant 可达性、集合是否存在都不能被吞掉。
                # 曾经这里 `except: pass` 把 Qdrant 挂了也报 status=ok，
                # 导致部署自检"假绿"。
                store = svc.pipe.store
                info: dict = {"collection": store.collection}
                qdrant_ok = store.alive()
                info["qdrant_reachable"] = qdrant_ok
                if not qdrant_ok:
                    info.update(status="degraded", points=0, collection_exists=False,
                                error=f"Qdrant 不可达：{store.url}")
                    # 用 503 让调用方（健康检查/自检脚本）能直接判失败
                    return self._send(503, info)
                try:
                    store.info()
                    info["collection_exists"] = True
                    info["points"] = store.count()
                    info["status"] = "ok"
                    return self._send(200, info)
                except Exception as e:
                    info.update(status="degraded", points=0, collection_exists=False,
                                error=f"集合 {store.collection} 不存在或不可读：{str(e)[:160]}")
                    return self._send(503, info)

            # GET 版检索：给 Dify 的 HTTP 请求节点用。
            # 为什么需要它：Dify 的 JSON body 是把变量**原样**塞进 JSON 字符串模板，
            # 用户 query 里只要有引号或换行就会破坏 JSON。改用 URL 参数则由 httpx 负责编码。
            if path == "/search":
                from urllib.parse import parse_qs
                qs = parse_qs(parsed.query)
                q = (qs.get("q") or qs.get("query") or [""])[0].strip()
                if not q:
                    return self._send(400, {"error": "q is required"})
                try:
                    top_k = int((qs.get("top_k") or ["5"])[0])
                except ValueError:
                    top_k = 5
                rerank = (qs.get("rerank") or ["true"])[0].lower() not in ("0", "false", "no")
                fmt = (qs.get("format") or ["json"])[0].lower()
                try:
                    hits = svc.search(q, top_k=top_k, rerank=rerank)
                except Exception as e:
                    return self._send(500, {"error": str(e)[:300]})
                if fmt == "text":
                    # 纯文本：直接给 LLM 当上下文用（Dify HTTP 节点走这条）
                    return self._send(200, svc.format_as_text(hits).encode("utf-8"),
                                      "text/plain; charset=utf-8")
                return self._send(200, {"query": q, "count": len(hits), "results": hits})

            if path.startswith("/images/"):
                parts = path[len("/images/"):].split("/", 1)
                if len(parts) != 2:
                    return self._send(400, {"error": "bad image path"})
                doc_id, name = parts
                f = svc.find_image(doc_id, name)
                if not f:
                    return self._send(404, {"error": "image not found", "name": name})
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                return self._send(200, f.read_bytes(), ctype,
                                  {"Cache-Control": "public, max-age=86400"})

            for (method, prefix), fn in extra_routes.items():
                if method == "GET" and path.startswith(prefix):
                    status, body = fn(self, None, path[len(prefix):])
                    return self._send(status, body)

            return self._send(404, {"error": "not found", "path": path})

        def do_POST(self):
            path = unquote(urlparse(self.path).path)

            if path == "/search":
                p = self._body()
                q = (p.get("query") or "").strip()
                if not q:
                    return self._send(400, {"error": "query is required"})
                try:
                    hits = svc.search(
                        q, top_k=int(p.get("top_k", 5)),
                        rerank=bool(p.get("rerank", True)),
                        query_image=p.get("query_image"),
                        routes=p.get("routes"),
                    )
                except Exception as e:
                    return self._send(500, {"error": str(e)[:300]})
                if str(p.get("format", "json")).lower() == "text":
                    return self._send(200, svc.format_as_text(hits).encode("utf-8"),
                                      "text/plain; charset=utf-8")
                return self._send(200, {"query": q, "count": len(hits), "results": hits})

            for (method, prefix), fn in extra_routes.items():
                if method == "POST" and path.startswith(prefix):
                    status, body = fn(self, self._body(), path[len(prefix):])
                    return self._send(status, body)

            return self._send(404, {"error": "not found", "path": path})

    return Handler


def serve(svc: KBSearchService, host: str = "0.0.0.0", port: int = 8088,
          extra_routes: dict | None = None):
    httpd = ThreadingHTTPServer((host, port), make_handler(svc, extra_routes))
    print(f"检索服务已启动：http://{host}:{port}")
    print(f"  健康检查  GET  /health")
    print(f"  检索      POST /search   {{\"query\":\"...\",\"top_k\":5,\"rerank\":true}}")
    print(f"  图片      GET  /images/{{doc_id}}/{{name}}")
    for (m, prefix) in (extra_routes or {}):
        print(f"  适配器    {m:4s} {prefix}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()

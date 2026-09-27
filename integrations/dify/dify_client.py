#!/usr/bin/env python3
"""
写入 Dify：父块（小节）+ 子块（语义单元）+ 图片精确绑定
==========================================================

为什么自己写而不让 Dify 切分
----------------------------
Dify 内置 PDF 提取器把每页文本用 `\\n\\n` 拼接、并把该页所有图片堆到页尾，
再叠加固定长度切分 —— 实测导致：
  · 图注与图分离：55 个带图块中 28 个（51%）图注不在同块
  · 小节被从中间切开：83/189 块内夹带小节号
  · 无标题层级，无法做整节召回

因此改为：MinerU 解析 → 自研两级切分 → 通过 Dify 的手动接口直接写入。

用到的 Dify 接口（均已实测可用）
--------------------------------
  POST /datasets/{ds}/documents/{doc}/segment
       body: {content, attachment_ids:[file_id], ...}      → 建父块并精确绑图
  POST /datasets/{ds}/documents/{doc}/segments/{seg}/child_chunks
       body: {content}                                      → 建子块（自动向量化）
  POST /files/upload                                        → 上传图片拿 file_id

用法
----
    export DIFY_BASE_URL=http://localhost DIFY_EMAIL=... DIFY_PASSWORD=...
    python3 ingest_dify.py <described_chunks.json> \
        --kb-name "电子工艺实训-结构化" \
        --images-root <MinerU 解压目录> \
        --embedding-model text-embedding-v4 \
        --dry-run            # 先看计划，不写库
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
class DifyClient:
    """最小 Dify 控制台客户端（登录用 base64 密码；写操作需同时带 csrf cookie + header）。"""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.api = f"{self.base}/console/api"
        self.token = self.csrf = self.cookie = ""

    # -- 基础设施 ---------------------------------------------------------
    def _open(self, req, timeout):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode() or "{}"), r.headers
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                return e.code, json.loads(raw or "{}"), e.headers
            except json.JSONDecodeError:
                return e.code, {"raw": raw[:500]}, e.headers

    def _headers(self, extra=None):
        h = {"Authorization": f"Bearer {self.token}", "X-CSRF-Token": self.csrf, "Cookie": self.cookie}
        if extra:
            h.update(extra)
        return h

    def req(self, method, path, payload=None, timeout=120):
        data = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(self.api + path, data=data, method=method)
        for k, v in self._headers({"Content-Type": "application/json"}).items():
            r.add_header(k, v)
        st, body, _ = self._open(r, timeout)
        return st, body

    # -- 登录 -------------------------------------------------------------
    def login(self, email: str, password: str) -> None:
        pw = base64.b64encode(password.encode()).decode()
        body = json.dumps({"email": email, "password": pw, "language": "zh-Hans",
                           "remember_me": True}).encode()
        r = urllib.request.Request(self.api + "/login", data=body, method="POST")
        r.add_header("Content-Type", "application/json")
        st, resp, headers = self._open(r, 60)
        if st != 200:
            raise RuntimeError(f"登录失败 HTTP {st}: {json.dumps(resp, ensure_ascii=False)[:200]}")
        jar = {}
        for c in headers.get_all("Set-Cookie") or []:
            k, v = c.split(";", 1)[0].split("=", 1)
            jar[k.strip()] = v.strip()
        if "access_token" not in jar:
            raise RuntimeError("登录未拿到 access_token")
        self.token, self.csrf = jar["access_token"], jar.get("csrf_token", "")
        self.cookie = "; ".join(f"{k}={v}" for k, v in jar.items())

    # -- 上传 -------------------------------------------------------------
    def upload(self, path: Path, timeout=300) -> str:
        content = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        b = uuid.uuid4().hex
        body = b"".join([
            f"--{b}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            content, b"\r\n", f"--{b}--\r\n".encode(),
        ])
        r = urllib.request.Request(self.api + "/files/upload", data=body, method="POST")
        for k, v in self._headers({"Content-Type": f"multipart/form-data; boundary={b}"}).items():
            r.add_header(k, v)
        st, resp, _ = self._open(r, timeout)
        if st != 201:
            raise RuntimeError(f"上传失败 HTTP {st}: {json.dumps(resp, ensure_ascii=False)[:200]}")
        return resp["id"]

    # -- 知识库 -----------------------------------------------------------
    def find_dataset(self, name: str):
        st, body = self.req("GET", "/datasets?page=1&limit=100")
        for d in (body.get("data") or []):
            if d.get("name") == name:
                return d
        return None

    def create_dataset(self, name: str, description: str = ""):
        st, body = self.req("POST", "/datasets", {
            "name": name, "description": description,
            "indexing_technique": "high_quality", "permission": "only_me", "provider": "vendor"})
        if st not in (200, 201):
            raise RuntimeError(f"建库失败 HTTP {st}: {json.dumps(body, ensure_ascii=False)[:200]}")
        return body

    def configure_retrieval(self, ds: str, provider: str, model: str,
                            hierarchical: bool = True):
        """设置向量模型 + 检索参数。hierarchical=True 时启用父子结构。"""
        st, body = self.req("PATCH", f"/datasets/{ds}", {
            "indexing_technique": "high_quality",
            "embedding_model": model,
            "embedding_model_provider": provider,
            "retrieval_model": {
                "search_method": "semantic_search",
                "reranking_enable": False, "reranking_mode": None,
                "reranking_model": {"reranking_provider_name": "", "reranking_model_name": ""},
                "weights": None, "top_k": 4,
                "score_threshold_enabled": False, "score_threshold": 0.5,
            },
        })
        if st != 200:
            raise RuntimeError(f"配置知识库失败 HTTP {st}: {json.dumps(body, ensure_ascii=False)[:300]}")
        return body

    def create_document(self, ds: str, file_id: str, name: str,
                        doc_form: str = "hierarchical_model",
                        process_rule: dict | None = None):
        """建一个"载体文档"——段落实体挂在它下面。

        doc_form=hierarchical_model 时**必须**给出 subchunk_segmentation，
        否则手动建 segment 时 Dify 自动切子块会抛
        `ValueError: No subchunk segmentation found in rules.`，
        该异常被吞掉但段落会被标记为 error/disabled —— 静默失败，很坑。
        """
        st, body = self.req("POST", f"/datasets/{ds}/documents", {
            "indexing_technique": "high_quality",
            "doc_form": doc_form,
            "doc_language": "Chinese",
            "is_multimodal": True,
            "data_source": {"info_list": {"data_source_type": "upload_file",
                                          "file_info_list": {"file_ids": [file_id]}}},
            "process_rule": process_rule or {"mode": "automatic"},
        }, timeout=300)
        if st != 200:
            raise RuntimeError(f"建文档失败 HTTP {st}: {json.dumps(body, ensure_ascii=False)[:300]}")
        docs = body.get("documents") or []
        if not docs:
            raise RuntimeError("建文档成功但未返回 document_id")
        return docs[0]["id"]

    def wait_document(self, ds: str, doc: str, timeout: float = 300) -> str:
        """
        等载体文档索引完成。

        必须等：`create_segment` 里有 `assert document.word_count is not None`，
        而 word_count 是**索引过程**写入的。若文档还没索引完（或索引失败），
        手动建 segment 会直接 500。这是踩过的坑。
        """
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            st, body = self.req("GET", f"/datasets/{ds}/documents/{doc}/indexing-status", timeout=60)
            s = body.get("indexing_status") if st == 200 else f"http{st}"
            if s != last:
                print(f"      载体文档状态: {s}")
                last = s
            if s == "completed":
                return s
            if s in ("error", "stopped"):
                raise RuntimeError(f"载体文档索引失败: {body.get('error')}")
            time.sleep(4)
        raise RuntimeError("等待载体文档索引超时")

    def list_segments(self, ds: str, doc: str, limit: int = 100):
        st, body = self.req("GET", f"/datasets/{ds}/documents/{doc}/segments?page=1&limit={limit}")
        return (body.get("data") or []) if st == 200 else []

    def delete_segment(self, ds: str, doc: str, seg_id: str):
        st, _ = self.req("DELETE",
                         f"/datasets/{ds}/documents/{doc}/segments/{seg_id}", timeout=120)
        return st

    def add_segment(self, ds: str, doc: str, content: str, attachment_ids=None,
                    keywords=None, timeout=180):
        # 注意：Dify 里是 `if args["attachment_ids"]` 直接取键，
        # 省略该键会 KeyError -> 500。必须始终传（可为空列表）。
        payload = {"content": content, "attachment_ids": attachment_ids or []}
        if keywords:
            payload["keywords"] = keywords
        st, body = self.req("POST", f"/datasets/{ds}/documents/{doc}/segment", payload, timeout)
        if st != 200:
            raise RuntimeError(f"建 segment 失败 HTTP {st}: {json.dumps(body, ensure_ascii=False)[:300]}")
        return (body.get("data") or {}).get("id")

    def add_child(self, ds: str, doc: str, seg: str, content: str, timeout=120):
        st, body = self.req("POST",
                            f"/datasets/{ds}/documents/{doc}/segments/{seg}/child_chunks",
                            {"content": content}, timeout)
        if st != 200:
            raise RuntimeError(f"建子块失败 HTTP {st}: {json.dumps(body, ensure_ascii=False)[:200]}")
        return (body.get("data") or {}).get("id")

    def doc_count(self, ds: str) -> int:
        st, body = self.req("GET", f"/datasets/{ds}/documents?page=1&limit=1")
        return body.get("total", 0) if st == 200 else -1


# ---------------------------------------------------------------------------
# 父块内部分隔符：Dify 会用同一串作为 subchunk_segmentation.separator，
# 于是它自动切出的子块 == 我们的子块，同时保留"命中子块→返回整节"的原生行为。
CHUNK_SEP = "<<<KBCHUNK>>>"


def child_text(c: dict, desc_in_child: bool = True) -> str:
    """单个子块的最终文本：正文 + 图注 + 图片描述。"""
    content = (c.get("content") or "").strip()
    if desc_in_child and c.get("image_descriptions"):
        extra = [f"[图片内容] {d}" for d in c["image_descriptions"].values() if d]
        if extra:
            content = content + "\n" + "\n".join(extra)
    return content


def build_parent_text(section_path: str, children: list[dict],
                      desc_in_child: bool = True) -> str:
    """
    父块正文 = 标题路径前缀 + 各子块（子块之间用 CHUNK_SEP 分隔）。

    注意：标题路径必须作为**前缀**拼进正文，不能自成一个 part ——
    否则它会和第一个子块之间产生一个多余的分隔符，
    导致 Dify 把一个只有 1 个子块的父块切成 2 块。
    """
    seen = set()
    parts = []
    # 子块正文自带【章节路径】前缀（子块检索时需要）。
    # 父块已经有路径前缀了，这里要把子块的重复前缀剥掉，否则父块里
    # 每个子块都重复一遍路径，浪费 token 且稀释向量。
    dup = f"【{section_path}】"
    for c in children:
        t = child_text(c, desc_in_child)
        if section_path and t.startswith(dup):
            t = t[len(dup):].lstrip("\n")
        if t and t not in seen:
            seen.add(t)
            parts.append(t)
    body = f"\n\n{CHUNK_SEP}\n\n".join(parts)
    return (f"【{section_path}】\n{body}" if section_path else body)


def ingest(chunks_path: str, images_root: str, kb_name: str,
           embed_provider: str, embed_model: str, base_url: str,
           email: str, password: str, dry_run: bool = False,
           limit_parents: int = 0, sleep: float = 0.3,
           desc_in_child: bool = True) -> dict:
    data = json.loads(Path(chunks_path).read_text(encoding="utf-8"))
    children: list[dict] = data["children"]
    root = Path(images_root)

    # 按父块分组
    groups: dict[int, list[dict]] = {}
    for c in children:
        groups.setdefault(c["parent_index"], []).append(c)

    # 父块标题路径：从 sections 取
    secs = data.get("sections") or []
    def path_of(i: int) -> str:
        if i < len(secs):
            p = secs[i].get("path") or []
            return " > ".join(p)
        return ""

    plan = []
    for i in sorted(groups):
        kids = groups[i]
        imgs = sorted({p for k in kids for p in (k.get("images") or [])})
        plan.append({"index": i, "path": path_of(i), "children": kids, "images": imgs})

    total_children = sum(len(p["children"]) for p in plan)
    total_images = len({p for pl in plan for p in pl["images"]})
    print(f"父块 {len(plan)} 个 | 子块 {total_children} 个 | 去重图片 {total_images} 张")

    if dry_run:
        for pl in plan[:8]:
            print(f"\n[父块 {pl['index']}] {pl['path'] or '（无标题）'}")
            print(f"   子块 {len(pl['children'])} 个，图片 {len(pl['images'])} 张")
            for k in pl["children"][:2]:
                print("     ·", (k.get("content") or "").replace("\n", " ")[:100])
        print("\n--dry-run：未写入 Dify")
        return {"parents": len(plan), "children": total_children, "images": total_images}

    cli = DifyClient(base_url)
    cli.login(email, password)
    print("✅ 登录成功")

    ds = cli.find_dataset(kb_name)
    if ds:
        ds_id = ds["id"]
        print(f"复用知识库：{kb_name} ({ds_id[:8]})")
    else:
        ds_id = cli.create_dataset(kb_name, "由 MinerU 结构化解析 + 两级分块构建")["id"]
        print(f"已建知识库：{kb_name} ({ds_id[:8]})")
    cli.configure_retrieval(ds_id, embed_provider, embed_model)
    print(f"向量模型：{embed_model} @ {embed_provider}")

    # 载体文档：必须用**文本文档**（不是图片）——否则 word_count 为 None，
    # 后续 create_segment 的 `assert document.word_count is not None` 会 500。
    carrier = root / "_carrier.md"
    if not carrier.exists():
        carrier.write_text(
            "# 载体文档\n\n"
            "本文件的段落仅用于承载由 MinerU 结构化解析生成的两级分块。\n"
            "正文内容请以手动写入的 segment 为准。\n",
            encoding="utf-8")
    # 关键：subchunk_segmentation.separator 用与父块拼接相同的串，
    # 这样 Dify 自动切出的子块 == 我们的子块（无需再手动建子块）
    proc_rule = {
        "mode": "hierarchical",
        "rules": {
            # remove_urls_emails 必须 false：多模态依赖正文里的图片链接
            "pre_processing_rules": [
                {"id": "remove_extra_spaces", "enabled": True},
                {"id": "remove_urls_emails", "enabled": False},
            ],
            "parent_mode": "paragraph",
            # segmentation 是 Dify 校验必填项（作用于载体文档自身，之后会被清理）
            "segmentation": {"separator": "\n\n", "max_tokens": 1000, "chunk_overlap": 0},
            "subchunk_segmentation": {
                "separator": CHUNK_SEP,
                "max_tokens": 4000,
                "chunk_overlap": 0,
            }
        },
    }
    doc_id = cli.create_document(ds_id, cli.upload(carrier),
                                 "MinerU 结构化载体",
                                 doc_form="hierarchical_model",
                                 process_rule=proc_rule)
    print(f"载体文档：{doc_id[:8]}，等待索引 …")
    cli.wait_document(ds_id, doc_id)

    # 清掉载体自身产生的段落，避免污染检索（只留我们结构化写入的）
    for seg in cli.list_segments(ds_id, doc_id):
        if seg.get("id"):
            cli.delete_segment(ds_id, doc_id, seg["id"])
    print("      已清理载体自带段落")

    # 图片上传缓存：路径 -> file_id
    img_cache: dict[str, str] = {}
    stats = {"parents_ok": 0, "children_ok": 0, "images_uploaded": 0, "errors": []}
    expected_children = 0

    todo = plan[:limit_parents] if limit_parents else plan
    for n, pl in enumerate(todo, 1):
        # 上传本父块的图片
        file_ids = []
        for p in pl["images"]:
            if p in img_cache:
                file_ids.append(img_cache[p]); continue
            f = root / p
            if not f.exists():
                stats["errors"].append(f"图片缺失 {p}"); continue
            try:
                fid = cli.upload(f)
                img_cache[p] = fid; file_ids.append(fid); stats["images_uploaded"] += 1
            except Exception as e:
                stats["errors"].append(f"上传失败 {p}: {str(e)[:80]}")
            time.sleep(sleep)

        parent_text = build_parent_text(pl["path"], pl["children"], desc_in_child)
        try:
            seg_id = cli.add_segment(ds_id, doc_id, parent_text, file_ids)
        except Exception as e:
            stats["errors"].append(f"父块 {pl['index']} 失败: {str(e)[:120]}")
            continue
        stats["parents_ok"] += 1
        # 子块由 Dify 按 CHUNK_SEP 自动切出；这里只统计期望值
        stats["children_ok"] += len(pl["children"])
        expected_children += len(pl["children"])

        if n % 20 == 0 or n == len(todo):
            print(f"  [{n}/{len(todo)}] 父块 {pl['index']} 完成 "
                  f"（累计父块 {stats['parents_ok']} 子块 {stats['children_ok']}）")

    print("\n" + "=" * 60)
    for k, v in stats.items():
        if k != "errors":
            print(f"  {k:<18} {v}")
    if stats["errors"]:
        print(f"  错误 {len(stats['errors'])} 条，前 5 条：")
        for e in stats["errors"][:5]:
            print("    -", e)
    # 校验：Dify 自动切出的子块数是否与预期一致
    try:
        segs = cli.list_segments(ds_id, doc_id, limit=100)
        n_child = 0
        for sg in segs:
            st2, body2 = cli.req(
                "GET", f"/datasets/{ds_id}/documents/{doc_id}/segments/{sg['id']}/child_chunks"
                       f"?page=1&limit=100")
            if st2 == 200:
                n_child += body2.get("total", len(body2.get("data") or []))
        stats["实际子块数"] = n_child
        ok = "✅ 一致" if n_child == expected_children else "⚠️ 不一致"
        print(f"\n校验：本次预期子块 {expected_children}，Dify 实际切出 {n_child}  {ok}")
    except Exception as e:
        stats["errors"].append(f"子块校验失败: {str(e)[:100]}")

    print(f"\n知识库 ID: {ds_id}")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="把两级分块结果写入 Dify")
    ap.add_argument("chunks_json", help="describe_images.py 的输出（或 structure.py 的 --json-out）")
    ap.add_argument("--images-root", required=True)
    ap.add_argument("--kb-name", default="结构化多模态知识库")
    ap.add_argument("--embedding-provider", default="langgenius/tongyi/tongyi")
    ap.add_argument("--embedding-model", default="text-embedding-v4",
                    help="图片语义已文本化，建议直接用最强文本模型")
    ap.add_argument("--base-url", default=os.environ.get("DIFY_BASE_URL", "http://localhost"))
    ap.add_argument("--email", default=os.environ.get("DIFY_EMAIL"))
    ap.add_argument("--password", default=os.environ.get("DIFY_PASSWORD"))
    ap.add_argument("--limit-parents", type=int, default=0, help="只写前 N 个父块（试跑）")
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--no-desc-in-child", action="store_true",
                    help="不把图片描述追加进子块（默认会追加）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.email or not args.password:
        raise SystemExit("请提供 DIFY_EMAIL / DIFY_PASSWORD")
    ingest(args.chunks_json, args.images_root, args.kb_name,
           args.embedding_provider, args.embedding_model, args.base_url,
           args.email, args.password, args.dry_run, args.limit_parents,
           args.sleep, desc_in_child=not args.no_desc_in_child)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

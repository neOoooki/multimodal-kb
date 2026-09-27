#!/usr/bin/env python3
"""
把 Dify chatflow 的知识库检索节点换成 HTTP 请求节点（直连自建检索服务）
========================================================================

为什么要换（详见 `docs/dify_external_kb_pitfalls.md`）：

1. Dify 的外部数据集 `retrieval_model` 默认为 NULL，会让 workflow 检索节点
   **静默返回空**（节点状态还是 succeeded），极难排查。
2. 节点里配的 top_k / reranking **对外部知识库无效** —— Dify 传的是数据集自己的
   `retrieval_model`，检索参数不受控。
3. Dify 可能对已排好序的结果**再做一次 rerank**。
4. 外部数据集的 records 只有 `content/score/title/metadata`，信息量比我们自己的
   返回结构（章节路径、图片直连 URL、分数明细）小得多。

换成 HTTP 请求节点后，检索完全由我们控制，返回结构也是我们自己的。

工作流变成：
    开始 → HTTP 请求（GET /search?q=...&format=text）→ LLM → 直接回复

用法：
    python3 tools/dify_http_node_chatflow.py --app-id <id> --search-url http://172.17.0.1:8088/search
    python3 tools/dify_http_node_chatflow.py --app-id <id> --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# 直接复用 pipeline 里的 Dify 客户端
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dify_client import DifyClient  # noqa: E402

HTTP_NODE_ID = "1764000000002"
LLM_NODE_ID = "1764000000003"
ANSWER_NODE_ID = "1764000000004"
START_NODE_ID = "1764000000001"

SYSTEM_PROMPT = """你是一位电子工艺实训课程的助教。请严格依据下面检索到的资料回答问题。

资料：
{{#context#}}

要求：
1. 只使用资料中的信息作答；资料没有提到的内容，明确回答「资料中未提及」。
2. **资料里的 Markdown 图片语法 `![图注](URL)` 必须原样保留在回答里**，不要改写、
   不要省略、不要转义 —— 前端要靠它显示插图。
3. 如果资料中包含表格（HTML），尽量保留其结构。
4. 回答用中文，条理清晰；涉及操作步骤时按顺序列出。"""


def http_node(search_url: str) -> dict:
    return {
        "id": HTTP_NODE_ID,
        "type": "custom",
        "position": {"x": 400, "y": 280},
        "positionAbsolute": {"x": 400, "y": 280},
        "width": 244,
        "height": 100,
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "data": {
            "type": "http-request",
            "title": "知识库检索（自建服务）",
            "desc": "直连自建多模态检索服务，绕开 Dify 外部数据集的限制",
            "variables": [],
            "method": "get",
            "url": search_url,
            "authorization": {"type": "no-auth", "config": None},
            "headers": "",
            # 注意：params 是 **冒号分隔** 的 key:value，每行一个。
            # 用 URL 参数而不是 JSON body，是因为 Dify 的 JSON body 会把变量原样塞进
            # JSON 字符串模板，query 里出现引号/换行就会破坏 JSON。
            "params": "q:{{#sys.query#}}\ntop_k:5\nrerank:true\nformat:text",
            "body": {"type": "none", "data": []},
            "ssl_verify": True,
            "timeout": {"max_connect_timeout": 0, "max_read_timeout": 0, "max_write_timeout": 0},
            "retry_config": {"retry_enabled": True, "max_retries": 2, "retry_interval": 200},
        },
    }


def build_graph(wf: dict, search_url: str) -> dict:
    """在现有草稿基础上，把 knowledge-retrieval 换成 http-request。"""
    g = wf.setdefault("graph", {})
    nodes = g.get("nodes", [])

    # 先把所有检索类节点（含 id 写错的残留）清掉，再插入唯一一个 HTTP 节点
    new_nodes, found_http = [], False
    for n in nodes:
        t = (n.get("data") or {}).get("type")
        if t in ("knowledge-retrieval", "http-request"):
            if not found_http:
                new_nodes.append(http_node(search_url))
                found_http = True
            continue
        if t == "llm" and n.get("id") == LLM_NODE_ID:
            d = n["data"]
            for m in d.get("prompt_template") or []:
                if m.get("role") == "system":
                    m["text"] = SYSTEM_PROMPT
            # LLM 节点的 context 是**必填**，指向 HTTP 节点的 body。
            # 提示词里用 {{#context#}}，由 Dify 负责代入。
            # （删掉整个字段会报 `context Field required`，踩过。）
            d["context"] = {"enabled": True,
                            "variable_selector": [HTTP_NODE_ID, "body"]}
        new_nodes.append(n)

    if not found_http:
        new_nodes.insert(1, http_node(search_url))

    g["nodes"] = new_nodes
    # 连线保持 start → http → llm → answer（id 未变，边不用改）
    g["edges"] = [e for e in g.get("edges", [])
                  if not ((e.get("source") == START_NODE_ID and e.get("target") == HTTP_NODE_ID)
                          or (e.get("source") == HTTP_NODE_ID and e.get("target") == LLM_NODE_ID))]
    g["edges"] = [
        {"id": f"{START_NODE_ID}-{HTTP_NODE_ID}", "source": START_NODE_ID,
         "target": HTTP_NODE_ID, "sourceHandle": "source", "targetHandle": "target",
         "type": "custom", "data": {"isInIteration": False, "sourceType": "start",
                                    "targetType": "http-request"}},
        {"id": f"{HTTP_NODE_ID}-{LLM_NODE_ID}", "source": HTTP_NODE_ID,
         "target": LLM_NODE_ID, "sourceHandle": "source", "targetHandle": "target",
         "type": "custom", "data": {"isInIteration": False, "sourceType": "http-request",
                                    "targetType": "llm"}},
        {"id": f"{LLM_NODE_ID}-{ANSWER_NODE_ID}", "source": LLM_NODE_ID,
         "target": ANSWER_NODE_ID, "sourceHandle": "source", "targetHandle": "target",
         "type": "custom", "data": {"isInIteration": False, "sourceType": "llm",
                                    "targetType": "answer"}},
    ]
    return wf


def main() -> int:
    ap = argparse.ArgumentParser(description="把 chatflow 换成 HTTP 请求节点直连自建检索服务")
    ap.add_argument("--app-id", required=True)
    ap.add_argument("--search-url", default="http://172.17.0.1:8088/search",
                    help="自建检索服务地址（**容器视角**，不是浏览器视角）")
    ap.add_argument("--base-url", default="http://localhost")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if DifyClient is None:
        print("找不到 DifyClient（需要 tools/dify_client.py）",
              file=sys.stderr)
        return 1

    import os
    email, password = os.environ.get("DIFY_EMAIL"), os.environ.get("DIFY_PASSWORD")
    if not email or not password:
        print("需要 DIFY_EMAIL / DIFY_PASSWORD 环境变量", file=sys.stderr)
        return 1

    c = DifyClient(args.base_url)
    c.login(email, password)
    st, wf = c.req("GET", f"/apps/{args.app_id}/workflows/draft")
    if st != 200:
        print(f"取草稿失败 HTTP {st}: {json.dumps(wf, ensure_ascii=False)[:200]}", file=sys.stderr)
        return 1

    before = [(n["data"].get("type"), n["id"]) for n in wf["graph"]["nodes"]]
    print("改造前节点:", before)

    new_wf = build_graph(wf, args.search_url)
    after = [(n["data"].get("type"), n["id"]) for n in new_wf["graph"]["nodes"]]
    print("改造后节点:", after)

    if args.dry_run:
        Path("/tmp/_wf_new.json").write_text(json.dumps(new_wf, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
        print("--dry-run：已把新图写到 /tmp/_wf_new.json，未提交")
        return 0

    # 同步草稿（草稿是增量式的，先清空再写入）
    # SyncDraftWorkflowPayload 是 extra="forbid"，只能传这几个字段
    # Dify 用乐观锁：必须带上 GET 到的 hash，否则 409 draft_workflow_not_sync
    payload = {"graph": new_wf["graph"],
               "features": new_wf.get("features", {}) or {},
               "conversation_variables": new_wf.get("conversation_variables", []) or []}
    if wf.get("hash"):
        payload["hash"] = wf["hash"]
    st, r = c.req("POST", f"/apps/{args.app_id}/workflows/draft", payload)
    print("同步草稿:", st, json.dumps(r, ensure_ascii=False)[:200])
    if st != 200:
        return 1

    st, r = c.req("POST", f"/apps/{args.app_id}/workflows/publish")
    print("发布:", st, json.dumps(r, ensure_ascii=False)[:160] if st != 200 else "ok")
    return 0 if st == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())

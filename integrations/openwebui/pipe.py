"""
Open WebUI 适配器（Pipe Function）
====================================

为什么选这条路
--------------
调研结论（详见 `调研报告/08_框架选型_替代Dify方案.md`）指出一个关键认知：

    Dify / MaxKB / FastGPT 的链路是
        检索片段 → 塞进 prompt → **指望 LLM 把图片 markdown 复述出来** → 才能渲染

这条链路天然脆弱 —— LLM 可能改写、丢弃或转义图片语法。
而 Open WebUI 的 **Pipe Function** 是 Python 代码，
可以**确定性地把检索产物写进 UI**，不依赖 LLM 复述。

两个必须注意的坑（源码级结论）
------------------------------
1. **Open WebUI 只渲染 `![]()`，不渲染原始 `<img>`**
   （`HTMLToken.svelte` 只特判 video/audio/iframe/status/file，其余按纯文本输出）
   → 我们的流水线已经统一输出 Markdown 图片语法。
2. **Open WebUI 的 "External Knowledge" 连接只支持 qdrant/milvus/pgvector 直连向量库**，
   不是任意 HTTP 检索 API → 所以要用 Pipe Function，而不是它的外部知识库配置。

另外官方已声明 **Pipelines 为 legacy**，新部署请用 in-process Function
（把本文件放进 `data/functions/` 即可）。
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, AsyncGenerator, Generator

SEARCH_URL = os.environ.get("MMKB_SEARCH_URL", "http://localhost:8088/search")


class Pipe:
    """
    Open WebUI Function（in-process）。

    安装：把本文件放到 Open WebUI 的 `data/functions/` 下，在管理面板启用。
    环境变量：
        MMKB_SEARCH_URL   检索服务地址（默认 http://localhost:8088/search）
        MMKB_TOP_K        召回条数（默认 5）
    """

    class Valves:
        def __init__(self):
            self.search_url = SEARCH_URL
            self.top_k = int(os.environ.get("MMKB_TOP_K", "5"))
            self.rerank = True
            # 确定性图片区块的开关与上限
            self.append_image_block = True
            self.max_images = 6

    def __init__(self):
        self.type = "pipe"
        self.id = "mmkb"
        self.name = "多模态知识库"
        self.valves = self.Valves()

    # -- 检索 -------------------------------------------------------------
    def _search(self, query: str) -> list[dict]:
        body = json.dumps({"query": query, "top_k": self.valves.top_k,
                           "rerank": self.valves.rerank}).encode()
        r = urllib.request.Request(self.valves.search_url, data=body, method="POST")
        r.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(r, timeout=120) as resp:
            return (json.loads(resp.read().decode()) or {}).get("results", [])

    # -- 拼上下文 + 确定性图片区块 ------------------------------------------
    @staticmethod
    def build_context(hits: list[dict]) -> str:
        blocks = []
        for i, h in enumerate(hits, 1):
            path = " > ".join(h.get("section_path") or [])
            head = f"[{i}] {path}" if path else f"[{i}]"
            if h.get("page"):
                head += f"（第 {h['page']} 页）"
            blocks.append(f"{head}\n{h.get('content', '')}")
        return "\n\n---\n\n".join(blocks)

    def build_image_block(self, hits: list[dict]) -> str:
        """
        **确定性**收集本次命中的所有图片，拼成 Markdown 区块。

        这是与 Dify 路线的本质区别：不经过 LLM，图片一定会出现在回答里。
        用 `![]()` 语法（Open WebUI 只认这个），URL 是服务端给的绝对地址。

        会跳过**正文里已经出现过**的图片（chunk 内容本身带了 `![]()`），
        避免同一张图在回答里出现两遍。
        """
        if not self.valves.append_image_block:
            return ""
        body_text = self.build_context(hits)
        seen, items = set(), []
        for h in hits:
            for im in (h.get("images") or []):
                url = im.get("url")
                if not url or url in seen:
                    continue
                if url in body_text:          # 正文已含这张图，不再重复
                    seen.add(url)
                    continue
                seen.add(url)
                cap = im.get("caption") or "插图"
                if im.get("page"):
                    cap += f"（第 {im['page']} 页）"
                items.append(f"![{cap}]({url})")
                if len(items) >= self.valves.max_images:
                    break
            if len(items) >= self.valves.max_images:
                break
        if not items:
            return ""
        return "\n\n---\n\n**相关插图**\n\n" + "\n\n".join(items)

    # -- Open WebUI 入口 ---------------------------------------------------
    async def pipe(self, body: dict, __user__: dict | None = None,
                   __event_emitter__=None) -> AsyncGenerator[str, None]:
        """
        流式返回。Open WebUI 会把 yield 的文本当 Markdown 渲染。

        这里走"检索 + 直接拼接"的模式（不调用 LLM），
        保证图片与原文都**原样**出现在回答里。
        若要接 LLM，把 build_context 的结果作为 prompt 调你的模型即可 ——
        但**图片区块仍然由代码追加**，不要交给 LLM 复述。
        """
        messages = body.get("messages") or []
        query = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                query = m.get("content") or ""
                break
        if not query:
            yield "（未收到问题）"
            return

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "检索多模态知识库…"}})

        try:
            hits = self._search(query)
        except Exception as e:
            yield f"检索失败：{str(e)[:200]}"
            return

        if not hits:
            yield "知识库中没有找到相关内容。"
            return

        yield "**检索到的相关内容**\n\n"
        yield self.build_context(hits)

        # 图片由代码追加，不依赖 LLM
        img_block = self.build_image_block(hits)
        if img_block:
            yield img_block


# ---------------------------------------------------------------------------
def main():
    """本地调试：直接跑一次检索并打印渲染结果。"""
    import asyncio

    p = Pipe()
    query = os.environ.get("Q", "接地保护示意图")
    print(f"问题：{query}\n" + "=" * 70)

    async def run():
        async for chunk in p.pipe({"messages": [{"role": "user", "content": query}]}):
            print(chunk, end="")
        print()

    asyncio.run(run())


if __name__ == "__main__":
    main()

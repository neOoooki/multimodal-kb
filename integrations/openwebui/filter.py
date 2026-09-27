"""
Open WebUI 适配器（Filter Function · 检索增强）
================================================

为什么是 Filter，而不是 Pipe
----------------------------
Open WebUI 的两类 Function 语义完全不同：

    Pipe    —— 在模型下拉里**冒充一个模型**，`pipe()` 的返回值就是最终回答。
               **完全绕过 LLM**：能保证图片原样出现，但用户看到的是检索原文，
               没有总结、没有提炼，本质上是"检索结果展示器"。
    Filter  —— 挂在真实模型上，**在 LLM 调用前后各插一脚**：
               `inlet()`  在请求发出前改 `body`（把检索结果注入成 system 提示）
               `outlet()` 在回答生成后改 `body`（把图片确定性追加到回答末尾）

本项目要的是"LLM 总结提炼 + 图片一定出现"，所以必须用 Filter：

    用户提问
      └─ inlet()   调检索服务 → 把资料（**剥掉图片语法**）注入 system 提示
      └─ LLM       基于资料总结成通顺回答（图片语法不在上下文里，模型无从丢弃）
      └─ outlet()  取出本次命中的图片，用 Markdown 追加到回答末尾

这样两个目标同时成立：**文字由模型组织**，**图片由代码保证**。
（Pipe 路线的教训：让模型"复述"它没被给到的东西，或者干脆不调模型，都不对。）

inlet 与 outlet 之间怎么传数据
------------------------------
两者是同一请求里的两次独立调用，不共享局部变量。Open WebUI 会把**同一个
`metadata` 字典**分别作为 `__metadata__` 传给它们（见 `utils/middleware.py`
里两处 `extra_params`），所以把本次命中的图片列表写进 `metadata` 即可 ——
比用模块级全局变量可靠（插件模块是按请求缓存的）。

两个源码级结论
--------------
1. Open WebUI 只渲染 `![]()`，不渲染原始 `<img>`（`HTMLToken.svelte` 只特判
   video/audio/iframe/status/file）→ 流水线统一输出 Markdown 图片语法。
2. 它的 "External Knowledge" 连接只支持 qdrant/milvus/pgvector 直连向量库，
   不是任意 HTTP 检索 API → 所以要走 Function，而不是外部知识库配置。

安装：`python3 integrations/openwebui/install_owui_function.py`（幂等）。

环境变量：
    MMKB_SEARCH_URL   检索服务地址（默认 http://localhost:8088/search）
    MMKB_TOP_K        召回条数（默认 5）
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

SEARCH_URL = os.environ.get("MMKB_SEARCH_URL", "http://localhost:8088/search")

# metadata 里存放本次命中图片的键
META_KEY = "mmkb_images"

SYSTEM_TEMPLATE = """你是一位电子工艺实训课程的助教。请严格依据下面检索到的资料回答用户的问题。

【资料开始】
{context}
【资料结束】

回答要求：
1. 只使用资料中的信息作答；资料没有提到的内容，明确回答「资料中未提及」，不要用常识补充。
2. 用中文回答，条理清晰；涉及操作步骤时按顺序列出，涉及参数时保留原始数值与单位。
3. 如果资料里出现表格，尽量保留其结构。
4. 回答末尾另起一行标注用到的资料来源编号，例如：参考：[1][3]
"""

_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


class Filter:
    """Open WebUI in-process Filter：检索增强 + 图片确定性注入。"""

    class Valves:
        def __init__(self):
            self.search_url = SEARCH_URL
            self.top_k = int(os.environ.get("MMKB_TOP_K", "5"))
            self.rerank = True
            # 把资料注入 system 提示，让 LLM 基于资料作答
            self.inject_context = True
            # 把命中图片追加到回答末尾（不经过 LLM）
            self.append_image_block = True
            self.max_images = 6
            # 注入前剥掉资料里的 ![]() 图片语法：
            # 图片由 outlet 统一追加，避免同一张图出现两遍，也免得模型拿它当正文
            self.strip_images_from_context = True
            # 检索阶段在界面上显示状态
            self.show_status = True

    class UserValves:
        def __init__(self):
            self.enabled = True

    def __init__(self):
        self.type = "filter"
        self.id = "mmkb_rag"
        self.name = "多模态知识库（检索增强）"
        self.valves = self.Valves()

    # -- 检索 ---------------------------------------------------------------
    def _search(self, query: str) -> list[dict]:
        body = json.dumps({
            "query": query,
            "top_k": self.valves.top_k,
            "rerank": self.valves.rerank,
        }).encode()
        req = urllib.request.Request(self.valves.search_url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=120) as resp:
            return (json.loads(resp.read().decode()) or {}).get("results", [])

    # -- 上下文拼装 ---------------------------------------------------------
    def build_context(self, hits: list[dict]) -> str:
        blocks = []
        for i, h in enumerate(hits, 1):
            path = " > ".join(h.get("section_path") or [])
            head = f"[{i}] {path}" if path else f"[{i}]"
            if h.get("page"):
                head += f"（第 {h['page']} 页）"
            content = h.get("content", "") or ""
            if self.valves.strip_images_from_context:
                content = _IMG_RE.sub("", content)
                content = re.sub(r"\n{3,}", "\n\n", content).strip()
            blocks.append(f"{head}\n{content}")
        return "\n\n---\n\n".join(blocks)

    def collect_images(self, hits: list[dict]) -> list[dict]:
        """把本次命中的图片压平成列表（去重、限量），供 metadata 传递。"""
        if not self.valves.append_image_block:
            return []
        seen, items = set(), []
        for h in hits:
            for im in (h.get("images") or []):
                url = im.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                items.append({"url": url, "caption": im.get("caption"),
                              "page": im.get("page")})
                if len(items) >= self.valves.max_images:
                    return items
        return items

    @staticmethod
    def build_image_block(images: list[dict]) -> str:
        """把图片列表拼成 Markdown 区块（**确定性**，不经过 LLM）。"""
        if not images:
            return ""
        items = []
        for im in images:
            cap = im.get("caption") or "插图"
            if im.get("page"):
                cap += f"（第 {im['page']} 页）"
            items.append(f"![{cap}]({im['url']})")
        return "\n\n---\n\n**相关插图**\n\n" + "\n\n".join(items)

    # -- Open WebUI 入口 ----------------------------------------------------
    async def inlet(self, body: dict, __user__: dict | None = None,
                    __event_emitter__=None, __metadata__: dict | None = None) -> dict:
        """请求发出前：检索，把资料注入 system 提示，并把图片列表存进 metadata。"""
        valves = (__user__.get("valves") if __user__ else None)
        if valves is not None and not getattr(valves, "enabled", True):
            return body

        messages = body.get("messages") or []
        query = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                query = m.get("content") or ""
                break
        if not query:
            return body

        if self.valves.show_status and __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "正在检索多模态知识库…", "done": False},
            })

        try:
            hits = self._search(query)
        except Exception as e:
            # 检索失败不该打断对话，如实告知即可
            if __event_emitter__:
                await __event_emitter__({
                    "type": "status",
                    "data": {"description": f"检索服务不可用：{str(e)[:80]}", "done": True},
                })
            return body

        # 交给 outlet 用
        if isinstance(__metadata__, dict):
            __metadata__[META_KEY] = self.collect_images(hits)

        if self.valves.show_status and __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": f"检索到 {len(hits)} 条资料", "done": True},
            })

        if not self.valves.inject_context or not hits:
            return body

        sys_text = SYSTEM_TEMPLATE.format(context=self.build_context(hits))
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = (messages[0].get("content") or "") + "\n\n" + sys_text
        else:
            messages.insert(0, {"role": "system", "content": sys_text})
        body["messages"] = messages
        return body

    async def outlet(self, body: dict, __user__: dict | None = None,
                     __metadata__: dict | None = None) -> dict:
        """
        回答生成后：把本次命中的图片确定性追加到回答末尾。

        必须同时改两个地方 —— Open WebUI 0.11.x 的 assistant 消息**既有 `content`
        （纯文本）又有 `output`（结构化 parts），而**前端优先渲染 `output`**：
        只改 `content` 的话，数据库里有图、界面上却看不到。
        """
        if not self.valves.append_image_block:
            return body
        images = (__metadata__ or {}).get(META_KEY) if isinstance(__metadata__, dict) else None
        block = self.build_image_block(images or [])
        if not block:
            return body

        for m in reversed(body.get("messages") or []):
            if m.get("role") != "assistant":
                continue
            self._append_to_message(m, block)
            break
        return body

    @staticmethod
    def _append_to_message(m: dict, block: str) -> None:
        """把 block 追加到消息的 content 与 output 两处（各自去重）。"""
        content = m.get("content")
        if isinstance(content, str) and "**相关插图**" not in content:
            m["content"] = content + block

        out = m.get("output")
        if not isinstance(out, list):
            return
        for part in out:
            if not isinstance(part, dict):
                continue
            inner = part.get("content")
            if isinstance(inner, list):
                last = inner[-1] if inner else None
                if isinstance(last, dict) and last.get("type") == "text":
                    text = last.get("text") or ""
                    if "**相关插图**" not in text:
                        last["text"] = text + block
                else:
                    inner.append({"type": "text", "text": block})
            elif isinstance(part.get("text"), str):
                if "**相关插图**" not in part["text"]:
                    part["text"] = part["text"] + block


# ---------------------------------------------------------------------------
def main():
    """本地调试：跑一次检索，打印注入的提示与图片区块。"""
    f = Filter()
    query = os.environ.get("Q", "色环电阻怎么读数？")
    hits = f._search(query)
    print(f"问题：{query}\n检索到 {len(hits)} 条\n" + "=" * 70)
    print(f.build_context(hits)[:900])
    print("\n" + "=" * 70 + "\n图片区块：")
    print(f.build_image_block(f.collect_images(hits)) or "（无）")


if __name__ == "__main__":
    main()

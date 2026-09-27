"""
图片语义标注（视觉模型）
==========================

为什么需要
----------
Dify 存不下融合向量，跨模态检索又不可靠（当前模型 Top-1 仅 40%）。
把**图片语义转成文本**，检索就完全走文本侧 —— 可用最强的文本 embedding + rerank，
且不受跨模态对齐能力拖累。

描述粒度
--------
针对**元器件数据手册**场景，强制模型输出图中**所有可见文字/型号/数值/单位**，
这样"某个型号的封装尺寸"这类问题也能命中。费用极低：
`qwen3-vl-flash` 0.0005 元/千token，100 张图约 0.1 元。
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path

CHAT_API = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

DEFAULT_MODEL = "qwen3-vl-flash"     # 专用 VL，最便宜档
JSON_MODEL = "qwen3.8-flash"         # 带 structured-output，需要严格 JSON 时用

FIGURE_PROMPT = """请为这张来自中文技术教材的插图生成**可检索的详细描述**。

严格按以下结构输出纯文本（不要 Markdown 代码块，不要额外解释）：

【图类型】电路图 / 方框图 / 示意图 / 曲线图 / 实物照片 / 表格截图 / 封装图 / 其他（选一个并补充说明）
【图中文字】逐条列出图中**所有可见的文字**，包括：标题、标注、型号、数值、单位、引脚名、元件标号。原文照抄，不要翻译、不要概括。
【数值与参数】列出所有数值及其单位/条件（如 "R=4Ω"、"U=220V"、"40~100Hz"）。没有则写"无"。
【符号与连接】描述元件符号、连线关系、箭头方向、层级结构。
【图注原文】如果图中或随图带有图注（形如"图 1-3 xxx"），原文照抄；没有则写"无"。
【一句话概括】用一句话概括这张图在讲什么。

要求：
- 只描述你实际看到的内容，不要推测、不要补充图中没有的知识。
- 图中文字若有模糊无法辨认的，标注[模糊]。
- 中文输出。"""


class VisionAnnotator:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 cache_path: str | Path | None = None, timeout: int = 120):
        self.key = api_key
        self.model = model
        self.timeout = timeout
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache: dict[str, str] = {}
        if self.cache_path and self.cache_path.exists():
            try:
                self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}
        self.calls = 0

    def _save_cache(self):
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2),
                                       encoding="utf-8")

    @staticmethod
    def _hash(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]

    def describe(self, image: Path | str, prompt: str = FIGURE_PROMPT,
                 use_cache: bool = True) -> str:
        p = Path(image)
        if not p.exists():
            return ""
        h = self._hash(p)
        if use_cache and h in self.cache:
            return self.cache[h]

        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        uri = f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
        body = {"model": self.model,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": uri}},
                    {"type": "text", "text": prompt}]}],
                "max_tokens": 1200, "temperature": 0.1}

        for a in range(4):
            r = urllib.request.Request(CHAT_API, data=json.dumps(body).encode(), method="POST")
            r.add_header("Authorization", "Bearer " + self.key)
            r.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(r, timeout=self.timeout) as resp:
                    d = json.loads(resp.read().decode())
                text = (d["choices"][0]["message"].get("content") or "").strip()
                self.calls += 1
                if use_cache:
                    self.cache[h] = text
                    self._save_cache()
                return text
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(5 * (a + 1)); continue
                raise RuntimeError(f"标注失败 HTTP {e.code}: {e.read().decode()[:200]}")
            except Exception:
                if a == 3:
                    raise
                time.sleep(3)
        return ""

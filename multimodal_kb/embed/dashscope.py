"""
向量化（阿里云百炼 / DashScope）
================================

为什么不用 Dify 自带的多模态 embedding
--------------------------------------
实测（20 组有标准答案的图文对，文→图 Top-1）：

    multimodal-embedding-v1（Dify 当前用的）  40%
    tongyi-embedding-vision-plus              40%     ← 同代换型没用
    qwen3-vl-embedding（独立向量）             80%
    qwen3-vl-embedding（**融合向量**）         95%     ← 最优
    qwen3-vl-rerank（重排）                    90%

**融合向量**（`enable_fusion=true`）把 text + image 编进**同一个 2560 维向量**，
图文天然同位置 —— 这是跨模态检索的关键。

本模块提供三路向量，落库时可并存（Dify 存不下，这里是核心能力）：
    · fusion（融合）  ：正文+图 → 1 个向量
    · text（文本）    ：正文+图片描述 → 1 个向量（不依赖跨模态对齐）
    · image（图片）   ：每张图 → 1 个向量
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path

MM_EMBED = ("https://dashscope.aliyuncs.com/api/v1/services/"
            "embeddings/multimodal-embedding/multimodal-embedding")
TEXT_EMBED = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"

# text-embedding-v4 单次最多 10 条，超过返回 400
TEXT_BATCH_LIMIT = 10

FUSION_MODEL = "qwen3-vl-embedding"       # 融合 + 独立，2560 维
TEXT_MODEL = "text-embedding-v4"


class DashScopeEmbedder:
    def __init__(self, api_key: str, fusion_model: str = FUSION_MODEL,
                 text_model: str = TEXT_MODEL, timeout: int = 120):
        self.key = api_key
        self.fusion_model = fusion_model
        self.text_model = text_model
        self.timeout = timeout
        self.calls = 0
        self.tokens = 0

    # -- 基础设施 ---------------------------------------------------------
    def _post(self, url: str, body: dict, retries: int = 4):
        for a in range(retries):
            r = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
            r.add_header("Authorization", "Bearer " + self.key)
            r.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(r, timeout=self.timeout) as resp:
                    self.calls += 1
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                msg = e.read().decode()[:300]
                if e.code == 429:
                    time.sleep(5 * (a + 1)); continue
                raise RuntimeError(f"HTTP {e.code}: {msg}")
            except Exception:
                if a == retries - 1:
                    raise
                time.sleep(3)
        raise RuntimeError("重试耗尽")

    @staticmethod
    def _data_uri(path: Path) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()

    # -- 融合向量 ---------------------------------------------------------
    def fusion(self, contents: list[dict], dimension: int | None = None) -> list[list[float]]:
        """
        融合向量。`contents` 每项形如 `{"text":..., "image":...}`，
        或 `{"text":...}` / `{"image":...}`。

        `enable_fusion=true` 时同一项的 text+image 会融合成**一个**向量；
        不传该参数则 text / image 各自出向量。
        """
        params: dict = {"enable_fusion": True}
        if dimension:
            params["dimension"] = dimension
        d = self._post(MM_EMBED, {"model": self.fusion_model,
                                  "input": {"contents": contents},
                                  "parameters": params})
        return [e["embedding"] for e in d["output"]["embeddings"]]

    def fusion_one(self, text: str | None, image: Path | str | None,
                   dimension: int | None = None) -> list[float]:
        """单条融合向量（正文 + 图 → 1 个向量）。"""
        item: dict = {}
        if text:
            item["text"] = text
        if image:
            item["image"] = self._data_uri(Path(image)) if isinstance(image, (str, Path)) \
                and Path(image).exists() else str(image)
        return self.fusion([item], dimension)[0]

    # -- 文本 / 图片向量（独立）--------------------------------------------
    def text(self, texts: list[str], dimension: int | None = None) -> list[list[float]]:
        """独立文本向量。自动按 10 条分批（v4 的硬限制）。"""
        out: list[list[float]] = []
        for i in range(0, len(texts), TEXT_BATCH_LIMIT):
            body: dict = {"model": self.text_model, "input": texts[i:i + TEXT_BATCH_LIMIT]}
            if dimension:
                body["dimensions"] = dimension
            d = self._post(TEXT_EMBED, body)
            out += [x["embedding"] for x in d["data"]]
        return out

    def text_one(self, text: str, dimension: int | None = None) -> list[float]:
        return self.text([text], dimension)[0]

    def image(self, images: list[Path | str], dimension: int | None = None) -> list[list[float]]:
        """独立图片向量（不融合）。"""
        contents = []
        for p in images:
            pp = Path(p)
            contents.append({"image": self._data_uri(pp) if pp.exists() else str(p)})
        params = {"enable_fusion": False}
        if dimension:
            params["dimension"] = dimension
        d = self._post(MM_EMBED, {"model": self.fusion_model,
                                  "input": {"contents": contents},
                                  "parameters": params})
        return [e["embedding"] for e in d["output"]["embeddings"]]

    def image_one(self, image: Path | str, dimension: int | None = None) -> list[float]:
        return self.image([image], dimension)[0]

    # -- 探测维度 ---------------------------------------------------------
    def probe_dims(self) -> dict[str, int]:
        return {
            "fusion": len(self.fusion_one("维度探测", None)),
            "text": len(self.text_one("维度探测")),
            "image": len(self.fusion_one(None, "https://dashscope.oss-cn-beijing.aliyuncs.com/images/256_1.png")),
        }


class Reranker:
    """重排序（交叉编码器）。

    Embedding 是"双塔"：query 与 document 各自独立编码、从不"见面"，
    只比整体语义；rerank 把 `[query + document]` 拼在一起送模型，
    能看到词级交互，排序准得多 —— 代价是无法预计算，只能对少量候选跑。

    实测：当前 embedding 单独 40% Top-1，rerank 能拉到 90%。
    """

    URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"

    def __init__(self, api_key: str, model: str = "qwen3-rerank",
                 vl_model: str = "qwen3-vl-rerank", timeout: int = 120):
        self.key = api_key
        self.model = model
        self.vl_model = vl_model
        self.timeout = timeout

    def rerank(self, query: str, documents: list[str | dict],
               top_n: int | None = None, multimodal: bool = False) -> list[tuple[int, float]]:
        """返回 [(原始下标, 分数)]，按分数降序。"""
        body = {"model": self.vl_model if multimodal else self.model,
                "input": {"query": query, "documents": documents}}
        if top_n:
            body["parameters"] = {"top_n": top_n}
        for a in range(4):
            r = urllib.request.Request(self.URL, data=json.dumps(body).encode(), method="POST")
            r.add_header("Authorization", "Bearer " + self.key)
            r.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(r, timeout=self.timeout) as resp:
                    d = json.loads(resp.read().decode())
                return [(x["index"], x["relevance_score"])
                        for x in sorted(d["output"]["results"],
                                        key=lambda x: -x["relevance_score"])]
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(5 * (a + 1)); continue
                raise RuntimeError(f"rerank HTTP {e.code}: {e.read().decode()[:200]}")
        raise RuntimeError("rerank 重试耗尽")

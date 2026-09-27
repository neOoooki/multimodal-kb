"""
配置
======

**所有可调项集中在这里，且一律通过环境变量覆盖** —— 仓库里不出现任何机器相关的绝对路径，
克隆下来就能跑，也便于容器化。

    from multimodal_kb import KBConfig, MultimodalKBPipeline
    cfg = KBConfig.from_env()
    pipe = MultimodalKBPipeline(cfg)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# 目录约定
# ---------------------------------------------------------------------------
# 项目根 = 本文件的上上级（multimodal_kb/config.py → multimodal_kb/ → 项目根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 默认数据目录。**不要**把它们放进版本库（见 .gitignore）。
DEFAULT_WORK_DIR = PROJECT_ROOT / "data" / "work"          # 缓存：图片描述、向量、日志
DEFAULT_PARSED_DIR = PROJECT_ROOT / "data" / "parsed"      # MinerU 解析产物


def _env_path(key: str, default: Path) -> Path:
    """读路径型环境变量；相对路径按项目根解析。"""
    v = os.environ.get(key)
    if not v:
        return default
    p = Path(v).expanduser()
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "") or default)
    except ValueError:
        return default


@dataclass
class KBConfig:
    # -- 存储 -------------------------------------------------------------
    qdrant_url: str = "http://localhost:6333"
    collection: str = "mmkb"

    # -- 模型 -------------------------------------------------------------
    dashscope_api_key: str = ""
    fusion_model: str = "qwen3-vl-embedding"      # 融合向量（正文+图 → 1 个向量）
    text_model: str = "text-embedding-v4"         # 独立文本向量
    vision_model: str = "qwen3-vl-flash"          # 图片语义标注
    rerank_model: str = "qwen3-rerank"            # 文本重排
    rerank_vl_model: str = "qwen3-vl-rerank"      # 多模态重排

    # -- 解析 -------------------------------------------------------------
    mineru_token: str = ""                        # MinerU 官方 API
    mineru_mode: str = "api"                      # api | local
    mineru_model_version: str = "vlm"             # vlm | pipeline | MinerU-HTML
    mineru_tier: str = "basic"                    # 本地模式的档位

    # -- 分块 -------------------------------------------------------------
    max_section_chars: int = 3000                 # 父块（小节）超长阈值
    target_chunk_chars: int = 400                 # 子块目标长度
    max_chunk_chars: int = 900                    # 子块硬上限

    # -- 向量化 -----------------------------------------------------------
    embed_images: bool = True                     # 是否生成图片向量（以图搜图需要）

    # -- 目录 -------------------------------------------------------------
    work_dir: Path = field(default_factory=lambda: DEFAULT_WORK_DIR)
    parsed_dir: Path = field(default_factory=lambda: DEFAULT_PARSED_DIR)

    # -- 服务 -------------------------------------------------------------
    public_base_url: str = "http://localhost:8088"   # 图片直连前缀（前端视角）
    service_host: str = "0.0.0.0"
    service_port: int = 8088

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, **overrides) -> "KBConfig":
        """
        从环境变量构造。所有字段都可以用 `MMKB_<字段名大写>` 覆盖环境变量，
        也接受显式 `overrides`（优先级最高）。
        """
        cfg = cls(
            qdrant_url=os.environ.get("QDRANT_URL", cls.qdrant_url),
            collection=os.environ.get("MMKB_COLLECTION", cls.collection),
            dashscope_api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
            fusion_model=os.environ.get("MMKB_FUSION_MODEL", cls.fusion_model),
            text_model=os.environ.get("MMKB_TEXT_MODEL", cls.text_model),
            vision_model=os.environ.get("MMKB_VISION_MODEL", cls.vision_model),
            rerank_model=os.environ.get("MMKB_RERANK_MODEL", cls.rerank_model),
            rerank_vl_model=os.environ.get("MMKB_RERANK_VL_MODEL", cls.rerank_vl_model),
            mineru_token=os.environ.get("MINERU_TOKEN", ""),
            mineru_mode=os.environ.get("MINERU_MODE", cls.mineru_mode),
            mineru_model_version=os.environ.get("MINERU_MODEL_VERSION", cls.mineru_model_version),
            mineru_tier=os.environ.get("MINERU_TIER", cls.mineru_tier),
            max_section_chars=_env_int("MMKB_MAX_SECTION_CHARS", cls.max_section_chars),
            target_chunk_chars=_env_int("MMKB_TARGET_CHUNK_CHARS", cls.target_chunk_chars),
            max_chunk_chars=_env_int("MMKB_MAX_CHUNK_CHARS", cls.max_chunk_chars),
            embed_images=_env_bool("MMKB_EMBED_IMAGES", cls.embed_images),
            work_dir=_env_path("MMKB_WORK_DIR", DEFAULT_WORK_DIR),
            parsed_dir=_env_path("MMKB_PARSED_DIR", DEFAULT_PARSED_DIR),
            public_base_url=os.environ.get("MMKB_PUBLIC_URL", cls.public_base_url),
            service_host=os.environ.get("MMKB_HOST", cls.service_host),
            service_port=_env_int("MMKB_PORT", cls.service_port),
        )
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
        return cfg

    def ensure_dirs(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.parsed_dir.mkdir(parents=True, exist_ok=True)

    def describe(self) -> dict:
        """用于 `kb status` 的可读摘要（不泄露 key）。"""
        return {
            "qdrant_url": self.qdrant_url,
            "collection": self.collection,
            "fusion_model": self.fusion_model,
            "text_model": self.text_model,
            "vision_model": self.vision_model,
            "rerank_model": self.rerank_model,
            "mineru_mode": self.mineru_mode,
            "work_dir": str(self.work_dir),
            "parsed_dir": str(self.parsed_dir),
            "public_base_url": self.public_base_url,
            "dashscope_api_key": "已设置" if self.dashscope_api_key else "未设置",
            "mineru_token": "已设置" if self.mineru_token else "未设置",
        }

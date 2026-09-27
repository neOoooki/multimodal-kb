"""
MinerU 解析适配
================

MinerU 有两条调用路径，本模块统一成一个接口，产出核心的 `list[Block]`：

  · **官方 API**（推荐）：100 页约 40 秒，免费额度 1000 页/天
  · **本地 CPU**：100 页约 62 秒，峰值内存约 6 GB，零成本

实测（i5-13500HX / 无 GPU / 32GB）：两条路都能跑；
对**带文字层的电子版 PDF**，本地 basic 档与 VLM 档输出**完全一致**，
扫描件才需要 VLM 档。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from ..models import Block, BlockType

MINERU_API = "https://mineru.net/api/v4"


# ---------------------------------------------------------------------------
# content_list_v2.json → list[Block]
# ---------------------------------------------------------------------------
def _join(items) -> str:
    out = []
    for it in items or []:
        if isinstance(it, dict):
            out.append(it.get("content", "") or "")
        elif isinstance(it, str):
            out.append(it)
    return " ".join(x for x in out if x).strip()


_TYPE_MAP = {
    "title": BlockType.TITLE,
    "paragraph": BlockType.PARAGRAPH,
    "image": BlockType.IMAGE,
    "chart": BlockType.CHART,
    "table": BlockType.TABLE,
    "equation_interline": BlockType.EQUATION,
    "page_number": BlockType.PAGE_NUMBER,
    "page_header": BlockType.PAGE_HEADER,
    "page_footer": BlockType.PAGE_FOOTER,
}


def load_content_list(path: str | Path) -> list[Block]:
    """读取 `*_content_list_v2.json`（按页的嵌套数组）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    blocks: list[Block] = []
    idx = 0
    for pi, page in enumerate(data):
        if not isinstance(page, list):
            continue
        for raw in page:
            t = _TYPE_MAP.get(raw.get("type"))
            if t is None:
                continue
            c = raw.get("content") or {}
            b = Block(idx=idx, page=pi + 1, type=t, bbox=raw.get("bbox"))
            idx += 1

            if t == BlockType.TITLE:
                b.text = _join(c.get("title_content"))
                b.level = c.get("level")
            elif t == BlockType.PARAGRAPH:
                b.text = _join(c.get("paragraph_content"))
            elif t in (BlockType.IMAGE, BlockType.CHART):
                b.image_path = (c.get("image_source") or {}).get("path")
                b.caption = _join(c.get("image_caption")) or _join(c.get("chart_caption"))
                b.footnote = _join(c.get("image_footnote")) or _join(c.get("chart_footnote"))
                b.text = c.get("content") or ""
            elif t == BlockType.TABLE:
                b.image_path = (c.get("image_source") or {}).get("path")
                b.html = c.get("html")
                b.caption = _join(c.get("table_caption"))
                b.footnote = _join(c.get("table_footnote"))
                b.meta["table_type"] = c.get("table_type")
            elif t == BlockType.EQUATION:
                b.latex = c.get("math_content")
                b.image_path = (c.get("image_source") or {}).get("path")
            else:  # 页眉/页脚/页码
                b.text = _join(c.get(f"{raw.get('type')}_content"))
            blocks.append(b)
    return blocks


# ---------------------------------------------------------------------------
# 官方 API
# ---------------------------------------------------------------------------
class MinerUAPI:
    """MinerU 官方 API 客户端（Standard API / api/v4）。

    注意两个坑：
      · 申请到的 OSS 上传链接 PUT 时**不能带 Content-Type**，
        `urllib` 会自动加 `application/x-www-form-urlencoded` 导致 403 —— 这里用 curl。
      · 结果 zip 在 `cdn-mineru.openxlab.org.cn`，某些代理会 TLS 握手失败，
        需要直连（`--noproxy`）。
    """

    def __init__(self, token: str):
        self.token = token

    def _req(self, method: str, path: str, payload=None, timeout=120):
        data = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(MINERU_API + path, data=data, method=method)
        r.add_header("Authorization", "Bearer " + self.token)
        if data:
            r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return {"http": e.code, "body": e.read().decode()[:300]}

    def parse(self, pdf: str | Path, out_dir: str | Path,
              model_version: str = "vlm", page_ranges: str | None = None,
              poll_interval: int = 10, timeout: int = 1800) -> Path:
        """上传并解析，返回解压目录。"""
        pdf = Path(pdf)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        body = {"files": [{"name": pdf.name, "data_id": pdf.stem}],
                "model_version": model_version}
        if page_ranges:
            body["page_ranges"] = page_ranges
        st = self._req("POST", "/file-urls/batch", body)
        if st.get("code") != 0:
            raise RuntimeError(f"申请上传失败: {json.dumps(st, ensure_ascii=False)[:300]}")
        batch_id, url = st["data"]["batch_id"], st["data"]["file_urls"][0]

        cp = subprocess.run(
            ["curl", "-s", "-X", "PUT", "-H", "Content-Type:",
             "--data-binary", f"@{pdf}", "-o", "/dev/null", "-w", "%{http_code}", url],
            capture_output=True, text=True)
        if cp.stdout.strip() != "200":
            raise RuntimeError(f"上传失败 HTTP {cp.stdout.strip()}")

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(poll_interval)
            res = self._req("GET", f"/extract-results/batch/{batch_id}")
            items = (res.get("data") or {}).get("extract_result") or []
            if not items:
                continue
            it = items[0]
            if it.get("state") == "done":
                return self._download(it["full_zip_url"], out)
            if it.get("state") == "failed":
                raise RuntimeError(f"解析失败: {it.get('err_msg')}")
        raise RuntimeError("解析超时")

    @staticmethod
    def _download(zip_url: str, out: Path) -> Path:
        zpath = out / "_result.zip"
        # 部分代理会对该 CDN 造成 TLS 握手失败，优先直连
        for extra in (["--noproxy", "*"], []):
            cp = subprocess.run(["curl", "-s", "--http1.1", *extra, "-o", str(zpath), zip_url],
                                capture_output=True, text=True)
            if zpath.exists() and zpath.stat().st_size > 0:
                break
        if not zpath.exists() or zpath.stat().st_size == 0:
            raise RuntimeError("结果包下载失败（可能是代理问题，试 --noproxy）")
        with zipfile.ZipFile(zpath) as z:
            z.extractall(out)
        zpath.unlink(missing_ok=True)
        return out


# ---------------------------------------------------------------------------
# 本地调用
# ---------------------------------------------------------------------------
def parse_local(pdf: str | Path, out_file: str | Path, tier: str = "basic",
                pages: str = "all", mineru_home: str | None = None,
                timeout: int = 3600, env: dict | None = None) -> Path:
    """
    调本地 mineru CLI。

    踩过的坑：
      · `--output` 要给**文件**路径，给目录会 `Is a directory`
      · CLI 默认只等 60 秒，100 页会提示 "still in progress" 后退出 → 用 `--wait`
      · 必须先 `mineru config set parse_server.local.mode managed` 并**重启 server**
      · 容器里跑要给足 `/dev/shm`，否则 multiprocessing 报 PermissionError
    """
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["mineru", "parse", str(pdf), "--pages", pages, "--tier", tier,
           "--output", str(out_file), "--force", "--wait", str(timeout)]
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 120,
                        env=env or os.environ.copy())
    if cp.returncode != 0 or not out_file.exists():
        raise RuntimeError(f"本地解析失败: {cp.stdout[-400:]} {cp.stderr[-400:]}")
    return out_file


def find_content_list(directory: str | Path) -> Path:
    """在解压目录里找 content_list_v2.json（其次 v1）。"""
    d = Path(directory)
    for pat in ("*_content_list_v2.json", "*_content_list.json"):
        hits = sorted(d.glob(pat))
        if hits:
            return hits[0]
    raise FileNotFoundError(f"{directory} 下找不到 content_list*.json")

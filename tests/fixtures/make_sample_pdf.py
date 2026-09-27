#!/usr/bin/env python3
"""
生成极小的样例 PDF（纯标准库，无第三方依赖）
================================================

用途：给**部署契约测试**提供一个体积极小、可随时重新生成、无版权负担的入库素材。

为什么不用真实教材：
  · 80 MB 的 PDF 不适合进版本库，也不适合 CI 每次跑；
  · 真实教材有版权；
  · 而且本文件是**生成**出来的 —— CI 里现算现用，仓库里连二进制都不用存。

生成的 PDF 带**文字层**（不是扫描件），所以 MinerU 用 basic 档就能解析。

用法：
    python3 tests/fixtures/make_sample_pdf.py [输出路径]
    # 默认写到 tests/fixtures/sample-2p.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

# 两页英文内容。**刻意用英文**：PDF 标准 14 字体（Helvetica）不含 CJK 字形，
# 内嵌中文字体需要字体子集化，会把一个 20 行的生成器变成一个字体工具链。
# 这个 fixture 只需要"有标题、有正文、能切出块"，语言不影响验证目标。
PAGES: list[tuple[str, list[str]]] = [
    (
        "Manual Arc Welding",
        [
            "The three-step operation method is the core of manual arc welding.",
            "Step 1: strike the arc by touching the workpiece, then lift to 2-4 mm.",
            "Step 2: feed the electrode steadily so the arc length stays constant.",
            "Step 3: close the arc by pulling back and filling the crater.",
            "Figure 1 shows the electrode angle for a butt joint.",
        ],
    ),
    (
        "Protective Earthing",
        [
            "Protective earthing connects the equipment enclosure to the earth.",
            "The earth resistance must not exceed 4 ohms for a training bench.",
            "Figure 2 shows the earthing schematic of the welding machine.",
            "Check the earthing before powering the equipment for the first time.",
        ],
    ),
]


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(title: str, lines: list[str]) -> bytes:
    parts = ["BT", "/F1 16 Tf", "1 0 0 1 60 780 Tm", f"({_escape(title)}) Tj"]
    parts += ["/F1 12 Tf", "0 -28 Td"]
    for line in lines:
        parts.append(f"({_escape(line)}) Tj")
        parts.append("0 -18 Td")
    parts.append("ET")
    return ("\n".join(parts) + "\n").encode("latin-1")


def build_pdf(dest: Path) -> Path:
    n_pages = len(PAGES)
    # 对象编号固定分配：1 catalog / 2 pages / 3 font / 之后每页两个对象
    first_page_obj = 4
    kids = " ".join(f"{first_page_obj + 2 * i} 0 R" for i in range(n_pages))

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("latin-1"),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for i, (title, lines) in enumerate(PAGES):
        page_obj = first_page_obj + 2 * i
        content_obj = page_obj + 1
        objects[page_obj] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_obj} 0 R >>"
        ).encode("latin-1")
        body = _content_stream(title, lines)
        objects[content_obj] = (
            f"<< /Length {len(body)} >>\nstream\n".encode("latin-1") + body + b"endstream"
        )

    # 组装 + 记录每个对象的字节偏移（xref 表需要）
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode("latin-1") + objects[num] + b"\nendobj\n"

    max_obj = max(objects)
    xref_pos = len(out)
    out += f"xref\n0 {max_obj + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for num in range(1, max_obj + 1):
        out += f"{offsets.get(num, 0):010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {max_obj + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    ).encode("latin-1")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(bytes(out))
    return dest


def main() -> int:
    default = Path(__file__).resolve().parent / "sample-2p.pdf"
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    build_pdf(dest)
    print(f"已生成 {dest}（{len(PAGES)} 页，{dest.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

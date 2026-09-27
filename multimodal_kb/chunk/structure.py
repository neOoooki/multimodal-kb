"""
结构重建 + 两级分块
====================

把解析器输出的**扁平块序列**变成**两级结构**：
    父级 = 小节（Section）　子级 = 语义单元（Chunk）

三条关键设计（都是踩坑后定下来的）
----------------------------------
1. **不依赖严格正确的章节树**。层级推断三级降级：
     ① 编号推导（`1.4` → `1.4.1` 判为更深）→ ② 解析器给的 level → ③ 单级兜底
   无标题文档自动切"滑动分组"，完整上下文由父块提供。

2. **列表项会被解析器误标为标题**。MinerU 把 `1. 安全措施` / `2. 安全操作`
   也标成 title；若不特殊处理，`2. 安全操作` 会被判成 1 级并**清空标题栈**，
   污染后续全部路径。规则：单段编号 = 列表项 → 与上一个标题同级。

3. **图片按"图注所在位置"归属**。图注定义了图的语义归属；当图注被标题隔开时，
   把图移到图注旁边，保证图与图注落在同一个 Chunk 里。
"""

from __future__ import annotations

import hashlib
import re
from uuid import UUID, uuid5

from ..models import BlockType, Block, Chunk, ImageRef, Section

# 确定性 id 的命名空间。
# 为什么不用 uuid4：随机 id 会让"同一个文档重跑入库"产生**重复点**而不是覆盖更新
# （实测踩过：一次入库写了两遍，Qdrant 里 431 个 chunk 变成 862 个点）。
# 改成内容寻址后，重跑入库天然幂等。
_NS = UUID("6f1a1e2c-8b7d-4c3a-9e5f-2d4b6c8a0e13")


def _sid(doc_key: str, path: list[str], part: int) -> str:
    return str(uuid5(_NS, f"{doc_key}|S|{' > '.join(path)}|{part}"))


def _cid(doc_key: str, sid: str, order: int, content: str) -> str:
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return str(uuid5(_NS, f"{doc_key}|C|{sid}|{order}|{h}"))

# ---- 可调参数 ----
MIN_TITLES_FOR_STRUCTURE = 3      # 标题少于这个数 → 视为无结构文档
DEFAULT_MAX_SECTION_CHARS = 3000  # 父块超长阈值
DEFAULT_TARGET_CHUNK_CHARS = 400  # 子块目标长度（相邻短段落合并）
DEFAULT_MAX_CHUNK_CHARS = 900     # 子块硬上限

CAPTION_RE = re.compile(r"^\s*(图|表|Figure|Table|Fig\.?)\s*[\d\w]", re.IGNORECASE)

_NUM_PATTERNS = [
    re.compile(r"^\s*(\d+(?:\s*[.．]\s*\d+)*)\s*[.．、]?\s"),        # 1.4.1 / 1. 4. 1 / 1.4
    re.compile(r"^\s*第\s*(\d+)\s*[章节篇]"),                          # 第 3 章
    re.compile(r"^\s*(?:Chapter|CHAPTER)\s+(\d+)", re.IGNORECASE),    # Chapter 3
]
_CN_PATTERN = re.compile(r"^\s*([一二三四五六七八九十]+)\s*[、.．]")
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


# ---------------------------------------------------------------------------
# 编号与层级
# ---------------------------------------------------------------------------
def parse_numbering(text: str) -> tuple[int, ...] | None:
    """解析标题编号，返回段数元组；解析不出返回 None。"""
    t = (text or "").strip()
    for pat in _NUM_PATTERNS:
        m = pat.match(t)
        if m:
            parts = [p for p in re.split(r"\s*[.．]\s*", m.group(1)) if p]
            if parts:
                try:
                    return tuple(int(p) for p in parts)
                except ValueError:
                    return None
    m = _CN_PATTERN.match(t)
    if m:
        return (_CN_NUM.get(m.group(1)[0], 1),)
    return None


def _is_prefix(a: tuple, b: tuple) -> bool:
    return len(a) < len(b) and b[: len(a)] == a


def infer_level(text: str, parser_level: int | None,
                stack: list[tuple[int, str, tuple | None]]) -> int:
    """
    推断标题层级（1 起）。三级降级，见模块 docstring。
    """
    stack_num = stack[-1][2] if stack else None
    num = parse_numbering(text)

    if num:
        if stack_num and _is_prefix(stack_num, num):
            return len(num)                       # 更深
        if stack_num and _is_prefix(num, stack_num):
            return len(stack_num) + 1             # 同节内的子项
        if len(num) == 1:
            # 单段编号 = 列表项，与上一个标题同级；
            # 但不允许顶掉"文档级根标题"（1 级且无编号，如书名）
            if not stack:
                return 1
            top_lvl, _, top_num = stack[-1]
            if top_num is None and top_lvl == 1:
                return 2
            return top_lvl
        return len(num)

    if parser_level:
        return int(parser_level)
    return stack[-1][0] if stack else 1


# ---------------------------------------------------------------------------
# 图片归属
# ---------------------------------------------------------------------------
def pair_figures(blocks: list[Block]) -> None:
    """
    给缺图注的图向后找形如「图 X」的短段落作为图注，
    并标记该图应移动到那个段落之后（让图与图注同块）。
    """
    for i, b in enumerate(blocks):
        if not b.is_figure or b.caption:
            continue
        for j in range(i + 1, min(i + 3, len(blocks))):
            nb = blocks[j]
            if nb.is_title or nb.is_figure:
                break
            if nb.type == BlockType.PARAGRAPH and CAPTION_RE.match(nb.text) and len(nb.text) <= 60:
                b.caption = nb.text
                b.meta["_attach_after_idx"] = nb.idx
                break


def reorder_figures(blocks: list[Block]) -> list[Block]:
    """按 `_attach_after_idx` 把图移到对应图注之后。"""
    moved = [b for b in blocks if b.meta.get("_attach_after_idx") is not None]
    if not moved:
        return blocks
    moved_ids = {b.idx for b in moved}
    base = [b for b in blocks if b.idx not in moved_ids]
    for m in moved:
        target = m.meta["_attach_after_idx"]
        pos = next((k for k, b in enumerate(base) if b.idx == target), None)
        if pos is None:
            base.append(m)
        else:
            base.insert(pos + 1, m)
    return base


# ---------------------------------------------------------------------------
# 分块
# ---------------------------------------------------------------------------
def _block_text(b: Block) -> str:
    if b.is_title:
        return b.text
    if b.is_table:
        return (b.caption + " " + (b.html or ""))
    if b.is_figure:
        return b.caption or ""
    if b.is_equation:
        return b.latex or ""
    return b.text or ""


def _render(blocks: list[Block]) -> tuple[str, list[ImageRef], str]:
    """渲染一组块 → (正文（含章节路径前缀，由调用方加）, 图片引用, 内容类型)"""
    parts: list[str] = []
    images: list[ImageRef] = []
    ctype = "text"
    for b in blocks:
        if b.is_figure:
            cap = b.caption or "（无图注）"
            # 输出 **Markdown 图片语法**，不要 HTML <img>：
            # Open WebUI 只渲染 ![]()，原始 <img> 会被当纯文本输出。
            # 相对路径由适配层/服务层补成可直连的绝对 URL。
            if b.image_path:
                parts.append(f"![{cap}]({b.image_path})")
            parts.append(f"[图片] {cap}")
            if b.footnote:
                parts.append(f"[图片说明] {b.footnote}")
            images.append(ImageRef(path=b.image_path or "", caption=b.caption,
                                   footnote=b.footnote, bbox=b.bbox, page=b.page))
            if ctype == "text":
                ctype = "figure"
        elif b.is_table:
            if b.caption:
                parts.append(f"[表格] {b.caption}")
            # 表格同时给 HTML（能渲染 HTML 的前端更好看）和**截图 markdown**
            if b.html:
                parts.append(b.html)
            if b.image_path:
                tcap = b.caption or "表格"
                parts.append(f"![{tcap}]({b.image_path})")
            if b.footnote:
                parts.append(f"[表注] {b.footnote}")
            if b.image_path:
                images.append(ImageRef(path=b.image_path, caption=b.caption,
                                       footnote=b.footnote, bbox=b.bbox, page=b.page))
            ctype = "table"
        elif b.is_equation:
            if b.latex:
                parts.append(f"$$ {b.latex} $$")
            if ctype == "text":
                ctype = "equation"
        else:
            if b.text:
                parts.append(b.text)
    # 过滤掉 "images/" 这类只有目录的伪路径（MinerU 偶尔会给出）
    clean = [i for i in images if i.path and not i.path.rstrip("/").endswith("images")]
    return "\n".join(parts).strip(), clean, ctype


def _window_sections(blocks: list[Block], max_chars: int, doc_key: str = "") -> list[Section]:
    """无结构文档的降级：按字符数滑动分组，仍保留最近标题作为 path。"""
    out: list[Section] = []
    last_title = ""
    acc: list[Block] = []
    n = 0
    for b in blocks:
        if b.is_title:
            last_title = b.text
        acc.append(b)
        n += len(_block_text(b))
        if n >= max_chars:
            pth = [last_title] if last_title else []
            out.append(Section(id=_sid(doc_key, pth, 1), path=pth, blocks=acc))
            acc, n = [], 0
    if acc:
        pth = [last_title] if last_title else []
        out.append(Section(id=_sid(doc_key, pth, 1), path=pth, blocks=acc))
    return out


def build_sections(blocks: list[Block], max_chars: int = DEFAULT_MAX_SECTION_CHARS,
                   doc_key: str = "") -> list[Section]:
    blocks = [b for b in blocks if not b.is_noise]
    titles = [b for b in blocks if b.is_title]
    if len(titles) < MIN_TITLES_FOR_STRUCTURE:
        return _window_sections(blocks, max_chars, doc_key)

    raw: list[Section] = []
    stack: list[tuple[int, str, tuple | None]] = []
    cur = Section(id="", path=[])

    def _mk(path):
        return Section(id=_sid(doc_key, path, 1), path=path)

    def flush():
        nonlocal cur
        if cur.blocks:
            raw.append(cur)
        cur = _mk([t for _, t, _ in stack])

    for b in blocks:
        if b.is_title:
            flush()
            lvl = infer_level(b.text, b.level, stack)
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, b.text, parse_numbering(b.text)))
            cur = _mk([t for _, t, _ in stack])
            continue
        cur.blocks.append(b)
    flush()

    # 超长小节拆成多个父块（共享 path，用 part 区分）
    out: list[Section] = []
    for s in raw:
        total = sum(len(_block_text(b)) for b in s.blocks)
        if total <= max_chars or not s.blocks:
            out.append(s)
            continue
        part, acc, n = 1, [], 0
        for b in s.blocks:
            k = len(_block_text(b))
            if acc and n + k > max_chars:
                out.append(Section(id=_sid(doc_key, s.path, part), path=s.path,
                                   part=part, blocks=acc))
                part += 1
                acc, n = [], 0
            acc.append(b)
            n += k
        if acc:
            out.append(Section(id=_sid(doc_key, s.path, part), path=s.path,
                               part=part, blocks=acc))

    for s in out:
        pages = [b.page for b in s.blocks if b.page]
        s.page_start, s.page_end = (min(pages), max(pages)) if pages else (None, None)
    return out


def build_chunks(sections: list[Section],
                 target_chars: int = DEFAULT_TARGET_CHUNK_CHARS,
                 max_chars: int = DEFAULT_MAX_CHUNK_CHARS,
                 doc_key: str = "") -> list[Chunk]:
    """
    切子块。规则：
      · 表格自成一个子块
      · 图片与其图注、紧跟的说明合并
      · 相邻短段落合并到接近 target_chars，超过 max_chars 强制断开
    """
    chunks: list[Chunk] = []
    order = 0

    def emit(s: Section, blocks: list[Block]):
        nonlocal order
        if not blocks:
            return
        body, images, ctype = _render(blocks)
        if not body and not images:
            return
        prefix = f"【{s.path_text}】\n" if s.path_text else ""
        content = prefix + body
        chunks.append(Chunk(
            id=_cid(doc_key, s.id, order, content), section_id=s.id, content=content,
            section_path=list(s.path), images=images,
            page=blocks[0].page, bbox=blocks[0].bbox, order=order,
            content_type=ctype,
        ))
        order += 1

    for s in sections:
        pending: list[Block] = []
        n = 0
        for b in s.blocks:
            if b.is_table:
                emit(s, pending); pending, n = [], 0
                emit(s, [b])
                continue
            if b.is_figure:
                pending.append(b); n += len(_block_text(b))
                if n >= target_chars:
                    emit(s, pending); pending, n = [], 0
                continue
            k = len(_block_text(b))
            if pending and n + k > max_chars:
                emit(s, pending); pending, n = [], 0
            pending.append(b); n += k
            if n >= target_chars:
                emit(s, pending); pending, n = [], 0
        emit(s, pending)
    return chunks


# ---------------------------------------------------------------------------
def rebuild(blocks: list[Block], max_section_chars: int = DEFAULT_MAX_SECTION_CHARS,
            target_chunk_chars: int = DEFAULT_TARGET_CHUNK_CHARS,
            max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
            doc_key: str = "") -> tuple[list[Section], list[Chunk]]:
    """
    完整流程：块序列 → (小节列表, 子块列表)。

    `doc_key` 用于生成**确定性 id**：同一文档 + 同样内容 → 同样的 id，
    因此重复入库是幂等的（覆盖更新）而不是产生重复点。强烈建议传入文档标识。
    """
    blocks = [b for b in blocks if not b.is_noise]
    pair_figures(blocks)
    blocks = reorder_figures(blocks)
    sections = build_sections(blocks, max_section_chars, doc_key)
    chunks = build_chunks(sections, target_chunk_chars, max_chunk_chars, doc_key)
    return sections, chunks

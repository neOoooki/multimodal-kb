#!/usr/bin/env python3
"""
项目报告图表生成（matplotlib）
================================

输出到 `docs/report/figures/`：PNG（200 dpi，供 Markdown 阅读）+ PDF（矢量，供 LaTeX 定稿）。

数据来源：
  · 静态数据（跨模态准确率、解析质量、问答效果）—— 项目实测记录，见报告附录
  · 动态数据 —— `scripts/report_metrics.py` 采集的 `docs/report/metrics.json`
    （知识库规模、检索耗时分解等）

用法：
    python3 scripts/report_metrics.py      # 先采集动态数据（需要服务在跑）
    python3 scripts/report_charts.py       # 再生成图表
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "report" / "figures"
DPI = 200

# ---------------------------------------------------------------------------
# 中文字体（按可用性依次尝试；找不到就退回默认字体并提示）
# ---------------------------------------------------------------------------
# 同一字族往往分 Regular / Bold 两个文件，两个都要注册，否则粗体请求会退回常规体
FONT_CANDIDATES = [
    ("/mnt/c/Windows/Fonts/Deng.ttf", "/mnt/c/Windows/Fonts/Dengb.ttf"),      # 等线
    ("/mnt/c/Windows/Fonts/msyh.ttc", "/mnt/c/Windows/Fonts/msyhbd.ttc"),    # 微软雅黑
    ("/mnt/c/Windows/Fonts/simhei.ttf", None),                               # 黑体（仅一款字重）
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", None),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", None),
    ("/System/Library/Fonts/PingFang.ttc", None),
]

C_INK, C_MUTED, C_GRID = "#1f2933", "#7b8794", "#e4e7eb"
C_BLUE, C_GREEN, C_AMBER, C_RED = "#2b6cb0", "#2f855a", "#b7791f", "#c53030"
C_GRAY, C_LIGHT = "#a0aec0", "#f0f4f8"


def setup_font() -> str:
    for regular, bold in FONT_CANDIDATES:
        if not Path(regular).is_file():
            continue
        try:
            fm.fontManager.addfont(regular)
            if bold and Path(bold).is_file():
                fm.fontManager.addfont(bold)
            name = fm.FontProperties(fname=regular).get_name()
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
            print(f"  中文字体: {name}（{regular}）")
            return name
        except Exception:
            continue
    print("  ⚠️ 未找到中文字体，图中中文可能显示为方框")
    return "sans-serif"


def style_axes(ax, grid_axis="y"):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(C_GRID)
    ax.tick_params(colors=C_MUTED, labelsize=10, length=0)
    if grid_axis:
        ax.grid(axis=grid_axis, color=C_GRID, linewidth=0.8, alpha=0.9)
        ax.set_axisbelow(True)


def save(fig, name: str):
    OUT.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"{name}.{ext}", dpi=DPI, bbox_inches="tight",
                    facecolor="white")
    plt.close(fig)
    print(f"  ✅ {name}.png / .pdf")


def load_metrics() -> dict:
    f = ROOT / "docs" / "report" / "metrics.json"
    if not f.is_file():
        raise SystemExit("缺少 metrics.json，请先运行 scripts/report_metrics.py")
    return json.loads(f.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 图 1　架构总览
# ---------------------------------------------------------------------------
def fig_architecture():
    fig, ax = plt.subplots(figsize=(11.2, 6.6))
    ax.set_xlim(0, 100); ax.set_ylim(-10, 102); ax.axis("off")
    ax.text(50, 100, "多模态知识库平台 · 工作流程", ha="center", va="top",
            fontsize=16.5, color=C_INK, fontweight="bold")

    def box(x, y, w, h, title, sub="", fc=C_LIGHT, ec=C_GRAY, tc=C_INK, fs=11.5,
            subfs=9.2):
        ax.add_patch(FancyBboxPatch((x, y), w, h,
                                    boxstyle="round,pad=0.6,rounding_size=1.2",
                                    linewidth=1.2, edgecolor=ec, facecolor=fc))
        if sub:
            ax.text(x + w/2, y + h*0.63, title, ha="center", va="center",
                    fontsize=fs, color=tc, fontweight="bold")
            ax.text(x + w/2, y + h*0.26, sub, ha="center", va="center",
                    fontsize=subfs, color=C_MUTED)
        else:
            ax.text(x + w/2, y + h/2, title, ha="center", va="center",
                    fontsize=fs, color=tc, fontweight="bold")

    def arrow(x1, y1, x2, y2, color=C_MUTED):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                     mutation_scale=13, linewidth=1.5,
                                     color=color, shrinkA=0, shrinkB=0))

    box(2.5, 76, 21, 12, "PDF 教材 / 论文", "插图 · 表格 · 公式", fc="#eef2ff", ec=C_BLUE)
    ax.text(13, 91, "原始资料", ha="center", fontsize=10.5, color=C_BLUE,
            fontweight="bold")

    LX, LW = 30, 32
    ax.text(LX + LW/2, 91, "① 知识入库（自动）", ha="center", fontsize=10.5,
            color=C_BLUE, fontweight="bold")
    box(LX, 76, LW, 12, "结构化解析", "还原版面、图注、表格")
    box(LX, 60, LW, 12, "章节重建与分块", "小节为父、语义单元为子")
    box(LX, 44, LW, 12, "图片语义标注", "用视觉模型把图转成文字")
    arrow(LX + LW/2, 76, LX + LW/2, 72.6)
    arrow(LX + LW/2, 60, LX + LW/2, 56.6)
    arrow(LX + LW/2, 44, LX + LW/2, 40.6)
    arrow(23.5, 82, LX - 1, 82, color=C_BLUE)

    ax.text(LX, 38.5, "② 三路向量", ha="left", fontsize=10.5, color=C_GREEN,
            fontweight="bold")
    vs = [("融合向量", "正文+图 融合", C_GREEN, "#f0fff4"),
          ("文本向量", "正文+图描述", C_BLUE, "#ebf8ff"),
          ("图像向量", "每图各自编码", C_AMBER, "#fffaf0")]
    vw, gap = 9.6, 1.6
    vx0 = LX + (LW - (vw*3 + gap*2)) / 2
    for i, (t, d, c, bg) in enumerate(vs):
        x = vx0 + i*(vw+gap)
        box(x, 25, vw, 11.5, t, d, fc=bg, ec=c, tc=c, fs=10, subfs=8)
    arrow(LX + LW/2, 44, LX + LW/2, 37)

    box(LX, 11, LW, 11, "向量数据库", "一条知识 = 三种向量表示", fc="#f7fafc")
    box(LX, -5, LW, 11, "混合检索与重排", "多路查找 → 综合排序", fc="#f7fafc")
    arrow(LX + LW/2, 11, LX + LW/2, 6.5)

    RX, RW = 70, 27
    ax.add_patch(FancyBboxPatch((RX, -5), RW, 56.5,
                                boxstyle="round,pad=0.7,rounding_size=1.4",
                                linewidth=1.3, edgecolor=C_AMBER, facecolor="#fffaf0"))
    ax.text(RX + RW/2, 48.5, "③ 使用方（可替换）", ha="center", fontsize=10.5,
            color=C_AMBER, fontweight="bold")
    for i, (t, d) in enumerate([("智能问答助手", "回答中直接呈现图文"),
                                ("教学 / 学习平台", "按知识点检索"),
                                ("自研页面", "直接调用检索接口")]):
        y = 39 - i*15
        ax.text(RX + 1.8, y + 1.4, "·", fontsize=13, color=C_AMBER, fontweight="bold")
        ax.text(RX + 3.6, y + 1.6, t, fontsize=10.5, color=C_INK, fontweight="bold")
        ax.text(RX + 3.6, y - 1.8, d, fontsize=9, color=C_MUTED)
    arrow(LX + LW, 0.5, RX - 1, 0.5, color=C_GREEN)
    ax.text((LX + LW + RX)/2, 2.6, "检索结果", ha="center", fontsize=9, color=C_MUTED)

    ax.text(2.5, -8.5, "核心处理流程不依赖任何具体问答平台；更换前端只需替换最外层接口。",
            fontsize=9.5, color=C_MUTED)
    save(fig, "fig1_architecture")


# ---------------------------------------------------------------------------
# 图 2　两个硬伤的改善
# ---------------------------------------------------------------------------
def fig_problem_effect():
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.1))
    data = [
        (axes[0], "图注与插图被分在同一块的比例",
         ["传统方案", "本项目"], [49, 81],
         "传统方案中近一半插图与图注被切散，\n检索到图注却拿不到图"),
        (axes[1], "用文字检索插图的准确率（首位命中）",
         ["传统方案", "本项目"], [40, 95],
         "跨模态检索从「基本不可用」\n提升到「基本可用」"),
    ]
    for ax, title, labels, vals, note in data:
        colors = [C_GRAY, C_GREEN]
        bars = ax.bar(labels, vals, color=colors, width=0.5)
        for b, v in zip(bars, vals):
            ax.text(b.get_x()+b.get_width()/2, v+3, f"{v}%", ha="center",
                    fontsize=14, fontweight="bold", color=b.get_facecolor())
        ax.set_title(title, fontsize=12, color=C_INK, pad=12)
        ax.set_ylim(0, 112); ax.set_yticks([0, 50, 100])
        ax.set_yticklabels(["0%", "50%", "100%"])
        style_axes(ax)
        ax.text(0, -0.26, note, transform=ax.transAxes, fontsize=9.2,
                color=C_MUTED, va="top")

    fig.suptitle("解决什么问题：两个关键指标的改善", fontsize=15,
                 fontweight="bold", color=C_INK, y=1.05, x=0.02, ha="left")
    fig.tight_layout()
    save(fig, "fig2_problem_effect")


# ---------------------------------------------------------------------------
# 图 3　跨模态检索方案对比
# ---------------------------------------------------------------------------
def fig_retrieval_top1():
    rows = [
        ("通用多模态向量模型（主流方案常用）", 40, C_GRAY),
        ("更换同代多模态向量模型", 40, C_GRAY),
        ("本项目：文字与插图分开编码", 80, C_BLUE),
        ("本项目：文字与插图融合编码", 95, C_GREEN),
        ("重排模型（可与上述叠加）", 90, C_AMBER),
    ]
    fig, ax = plt.subplots(figsize=(10.4, 4.5))
    ys = list(range(len(rows)))
    for y, (label, v, c) in zip(ys, rows):
        ax.barh(y, v, color=c, height=0.58, alpha=0.92)
        ax.text(v + 1.5, y, f"{v}%", va="center", fontsize=13,
                fontweight="bold", color=c)
    ax.set_yticks(ys); ax.set_yticklabels([r[0] for r in rows], fontsize=11)
    ax.invert_yaxis()
    ax.set_xlim(0, 108); ax.set_xticks([0, 20, 40, 60, 80, 100])
    ax.set_xticklabels(["0%", "20%", "40%", "60%", "80%", "100%"])
    ax.set_xlabel("用一句文字描述去找对应插图的准确率（首位命中）",
                  fontsize=10.5, color=C_MUTED)
    style_axes(ax, grid_axis="x")
    ax.set_title("关键发现：瓶颈不在「换更强的模型」，而在「向量怎么构造」",
                 fontsize=14.5, fontweight="bold", color=C_INK, pad=14)
    ax.text(0, -0.26, "评测方式：20 组有标准答案的图文对，直接测量「文字 → 插图」首位命中率。",
            transform=ax.transAxes, fontsize=9.2, color=C_MUTED)
    fig.tight_layout()
    save(fig, "fig3_retrieval_top1")


# ---------------------------------------------------------------------------
# 图 4　入库规模与构成
# ---------------------------------------------------------------------------
def fig_kb_scale(m: dict):
    kb = m["kb"]
    fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.5),
                             gridspec_kw={"width_ratios": [1.15, 1]})

    ax = axes[0]
    stages = ["解析出的\n结构化块", "重建出的\n章节路径", "切分后的\n知识块", "绑定入库的\n图像对象"]
    vals = [kb["parse_blocks_total"], kb["sections"], kb["points"], kb["images"]]
    colors = [C_GRAY, C_BLUE, C_GREEN, C_AMBER]
    bars = ax.bar(stages, vals, color=colors, width=0.6, alpha=0.92)
    for b, v in zip(bars, vals):
        ax.text(b.get_x()+b.get_width()/2, v+14, f"{v}", ha="center",
                fontsize=13, fontweight="bold", color=b.get_facecolor())
    ax.set_ylim(0, max(vals)*1.22)
    ax.set_ylabel("数量", fontsize=10.5, color=C_MUTED)
    style_axes(ax)
    ax.set_title(f"一本 {kb['pages']} 页教材的入库结果", fontsize=12.5,
                 color=C_INK, pad=10)
    ax.text(0, -0.24, f"图像对象零丢失：{kb['images_with_desc']}/{kb['images']} "
                      f"均附有语义描述", transform=ax.transAxes, fontsize=9.2,
            color=C_MUTED)

    ax = axes[1]
    name_map = {"text": "文字块", "figure": "图示块", "table": "表格块"}
    items = sorted(kb["content_types"].items(), key=lambda x: -x[1])
    labels = [name_map.get(k, k) for k, _ in items]
    sizes = [v for _, v in items]
    wedges, _ = ax.pie(
        sizes, labels=None, autopct=None,
        colors=[C_BLUE, C_GREEN, C_AMBER][:len(sizes)],
        startangle=90, counterclock=False,
        wedgeprops=dict(width=0.40, edgecolor="white", linewidth=2))
    ax.legend(wedges, [f"{l}　{s} 块" for l, s in zip(labels, sizes)],
              loc="center", frameon=False, fontsize=10.5, labelcolor=C_INK,
              handlelength=1.0, handletextpad=0.6)
    ax.set_title("知识块构成", fontsize=12.5, color=C_INK, pad=10)
    ax.text(0, -1.22, f"平均每块 {kb['chars_median']} 字", ha="center",
            fontsize=9.2, color=C_MUTED)

    fig.suptitle("知识入库的实际产出", fontsize=15, fontweight="bold",
                 color=C_INK, y=1.02, x=0.02, ha="left")
    fig.tight_layout()
    save(fig, "fig4_kb_scale")


# ---------------------------------------------------------------------------
# 图 5　端到端问答效果
# ---------------------------------------------------------------------------
def fig_qa_effect():
    rows = [("手工焊接的步骤是什么？", 0, 2),
            ("接地保护示意图说明了什么？", 0, 2),
            ("胸外按压的操作要领？", 1, 2),
            ("设备通电前做哪三查？", 0, 2)]
    # 用文字标签而不是 ✓/✕ —— 中文字体常缺这些符号字形，会渲染成方框
    label = {0: ("未答出", C_RED, 0.12), 1: ("部分答出", C_AMBER, 0.16),
             2: ("完整答出", C_GREEN, 0.13)}

    fig, ax = plt.subplots(figsize=(10.6, 4.1))
    ax.set_xlim(0, 10); ax.set_ylim(-0.8, len(rows)+0.85); ax.axis("off")

    ax.text(4.15, len(rows)+0.40, "传统方案", ha="center", fontsize=11.5,
            color=C_MUTED, fontweight="bold")
    ax.text(6.55, len(rows)+0.40, "本项目", ha="center", fontsize=11.5,
            color=C_GREEN, fontweight="bold")

    for i, (q, a, b) in enumerate(rows):
        y = len(rows) - i - 0.5
        ax.axhline(y + 0.5, color=C_GRID, linewidth=0.8)
        ax.text(0.1, y, q, va="center", fontsize=11.5, color=C_INK)
        for j, v in enumerate((a, b)):
            txt, col, alpha = label[v]
            cx = 4.15 + j*2.4
            ax.add_patch(FancyBboxPatch((cx-0.92, y-0.30), 1.84, 0.60,
                                        boxstyle="round,pad=0.02,rounding_size=0.16",
                                        facecolor=col, alpha=alpha, edgecolor="none"))
            ax.text(cx, y, txt, ha="center", va="center", fontsize=10.5,
                    color=col, fontweight="bold")

    ax.text(0.1, -0.55, "同一份教材、同一个大模型、同一套提示词，只更换知识库。"
                        "「接地保护示意图」一类问题完全依赖插图内容。",
            fontsize=9.5, color=C_MUTED)
    ax.set_title("端到端问答：只换知识库带来的差别", fontsize=15,
                 fontweight="bold", color=C_INK, loc="left", pad=16)
    fig.tight_layout()
    save(fig, "fig6_qa_effect")


# ---------------------------------------------------------------------------
# 图 6　检索响应耗时分解
# ---------------------------------------------------------------------------
def fig_latency(m: dict):
    lat = m["latency"]
    embed = lat["embed_text_ms"] + lat["embed_fusion_ms"]
    search = lat["qdrant_fusion_ms"] + lat["qdrant_text_ms"]
    rerank = lat["rerank_ms"]
    total = lat["e2e_with_rerank_ms"]

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2),
                             gridspec_kw={"width_ratios": [1.25, 1]})

    # 左：三个环节各自耗时（分开画，比堆叠更易读）
    ax = axes[0]
    labels = ["理解问题\n（调用向量模型）", "结果重新排序\n（调用重排模型）",
              "在知识库中查找"]
    vals = [embed, rerank, search]
    colors = [C_BLUE, C_AMBER, C_GREEN]
    ys = list(range(len(labels)))
    ax.barh(ys, vals, color=colors, height=0.55, alpha=0.92)
    for y, v in zip(ys, vals):
        ax.text(v + total*0.015, y, f"{v} ms", va="center", fontsize=12,
                fontweight="bold", color=colors[y])
    ax.set_yticks(ys); ax.set_yticklabels(labels, fontsize=10.5)
    ax.invert_yaxis()
    ax.set_xlim(0, total*0.62)
    ax.set_xlabel("毫秒", fontsize=10.5, color=C_MUTED)
    style_axes(ax, grid_axis="x")
    ax.set_title(f"三个环节：一次检索共约 {total} 毫秒", fontsize=12.5,
                 color=C_INK, pad=10)
    ax.text(0, -0.30, "真正用于「查找」的只有几毫秒，其余几乎都是调用模型的网络等待。",
            transform=ax.transAxes, fontsize=9.2, color=C_MUTED)

    # 右：开 / 不开重排
    ax = axes[1]
    labs = ["不开启重排", "开启重排"]
    vals = [lat["e2e_without_rerank_ms"], lat["e2e_with_rerank_ms"]]
    bars = ax.bar(labs, vals, color=[C_GRAY, C_GREEN], width=0.45, alpha=0.92)
    for b, v in zip(bars, vals):
        ax.text(b.get_x()+b.get_width()/2, v + max(vals)*0.04, f"{v} ms",
                ha="center", fontsize=12, fontweight="bold",
                color=b.get_facecolor())
    ax.set_ylim(0, max(vals)*1.28)
    ax.set_ylabel("毫秒", fontsize=10.5, color=C_MUTED)
    ax.tick_params(axis="x", labelsize=10.5)
    style_axes(ax)
    ax.set_title("重排以约 0.25 秒换取更准的排序", fontsize=12.5,
                 color=C_INK, pad=10)

    fig.suptitle("检索有多快：耗时花在哪里", fontsize=15, fontweight="bold",
                 color=C_INK, y=1.02, x=0.02, ha="left")
    fig.tight_layout()
    save(fig, "fig5_latency")


def main() -> int:
    print("生成图表（matplotlib）→ docs/report/figures/")
    setup_font()
    m = load_metrics()
    fig_architecture()
    fig_problem_effect()
    fig_retrieval_top1()
    fig_kb_scale(m)
    fig_qa_effect()
    fig_latency(m)
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

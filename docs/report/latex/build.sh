#!/usr/bin/env bash
# ============================================================
#  编译结题报告（LaTeX）
#
#    ./build.sh            # 编译并生成 结题报告.pdf
#    ./build.sh --clean    # 清理中间文件
#
#  为什么用 XeLaTeX：中文排版需要 xeCJK；字体用 TeX Live 自带的 Fandol，
#  不依赖系统安装中文字体。
#
#  依赖（若无 xelatex，按下面任一方式安装）：
#    仅当前用户安装（无需 root，推荐）：
#      wget -qO- "https://yihui.org/tinytex/install-bin-unix.sh" | sh
#      ~/.local/bin/tlmgr install ctex fandol booktabs caption float \
#                                enumitem fancyhdr xcolor geometry hyperref
#    或系统级安装：
#      sudo apt install texlive-xetex texlive-lang-chinese texlive-latex-extra
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

TEX="结题报告.tex"
PDF="结题报告.pdf"

if [ "${1:-}" = "--clean" ]; then
  rm -f ./*.aux ./*.log ./*.out ./*.toc ./*.synctex.gz
  echo "已清理中间文件（保留 $PDF）"
  exit 0
fi

command -v xelatex >/dev/null 2>&1 || {
  echo "❌ 找不到 xelatex。安装方式见本脚本文件头部注释。" >&2
  exit 1
}

echo "== 编译（需跑两遍以生成目录与交叉引用）=="
for i in 1 2; do
  echo "  第 $i 遍…"
  xelatex -interaction=nonstopmode -halt-on-error "$TEX" > "pass$i.log" 2>&1 || {
    echo "❌ 编译失败，错误摘要：" >&2
    grep -A4 -E '^!' "pass$i.log" | head -40 >&2
    exit 1
  }
done
rm -f pass1.log pass2.log

pages=$(grep -oE 'Output written on .*\(([0-9]+) pages' "${TEX%.tex}.log" | grep -oE '[0-9]+ pages' || echo "?")
echo "✅ 完成：$PDF（$pages）"

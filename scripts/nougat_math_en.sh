#!/usr/bin/env bash
# Nougat(隔离 .nougat-venv)把英文数学 PDF 转成带 LaTeX 的 MD → books/MD/英文数学/.
# LaTeX(\(...\)/\[...\])过数学 R6 命门. 用法: bash scripts/nougat_math_en.sh <pdf1> <pdf2> ...
set -uo pipefail
cd /home/soffy/projects/AII
OUT=/home/soffy/books/MD/英文数学
TMP=/tmp/nougat_out
mkdir -p "$TMP" "$OUT"
echo "════ Nougat 英文数学转换 $(date '+%m-%d %H:%M') ════"
for pdf in "$@"; do
  [ -f "$pdf" ] || { echo "  ⚠ 不存在: $pdf"; continue; }
  stem=$(basename "$pdf" .pdf)
  clean=$(echo "$stem" | sed -E 's/ ?\(z-lib[^)]*\)//g; s/ ?\(z-library[^)]*\)//g; s/ ?\(1lib[^)]*\)//g')
  echo "▶ $clean  $(date '+%H:%M')"
  rm -f "$TMP/$stem.mmd" 2>/dev/null
  .nougat-venv/bin/nougat "$pdf" -o "$TMP" --batchsize 2 --no-skipping >/dev/null 2>&1 || { echo "  ✗ Nougat失败"; continue; }
  if [ -f "$TMP/$stem.mmd" ]; then
    mv "$TMP/$stem.mmd" "$OUT/$clean.md"
    lx=$(grep -oE '\\\(|\\\[' "$OUT/$clean.md" 2>/dev/null | wc -l)
    echo "  ✓ → $clean.md ($(($(wc -c <"$OUT/$clean.md")/1024))KB, LaTeX $lx)"
  else echo "  ✗ 无 mmd 输出"; fi
done
echo "════ 完成 $(date '+%H:%M') ════"

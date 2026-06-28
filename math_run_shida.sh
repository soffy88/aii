#!/usr/bin/env bash
# 数分 数学管道顺序驱动(绕过有语法错误的 math_batch_run.sh)
# 逐章 math_pipeline.py(NIM key2, 串行=限流安全) → 质量门 → register
set -uo pipefail
cd /home/soffy/projects/AII
PY=.venv/bin/python

export NVIDIA_NIM_API_KEY="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"
export AII_MD_FILE="/home/soffy/shared/stratum-to-aii/数学分析(第5版) 上 (华东师范大学数学系).md"
export DATABASE_URL="postgresql://aii:aii_safe_pass@localhost:5435/aii_kg"
SUB="shida_mathanalysis_v5_vol1"
TITLE="数学分析(第5版)上·华东师大"
export MATH_OUTDIR="math_pipeline/staging/$SUB"
mkdir -p "$MATH_OUTDIR"

echo "════ 数分 数学管道(NIM key2, 顺序) $(date '+%H:%M') ════"
# 逐章(数分上册 ~10 章; math_pipeline 内部检测, 缺章 SystemExit→跳过)
for ch in $(seq 1 12); do
  echo "── 第 $ch 章 ──"
  $PY scripts/math_pipeline.py "$ch" 2>&1 | grep -vE "INFO|WARNING|huggingface|tokenizer" | tail -4 || true
done

echo "════ 质量门 ════"
$PY scripts/math_quality_gate.py "$SUB" --staging "$MATH_OUTDIR" --json "math_pipeline/qual_${SUB}.json" 2>&1 | tail -5 || true

echo "════ 完成 $(date '+%H:%M') ════"

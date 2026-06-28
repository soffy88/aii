#!/usr/bin/env bash
# 英文数学书专用通道: MATH_LANG=en(英文 should_have: Definition/Theorem/Rule) + NIM.
# 与 econ(key1)/中文数学(key2)用不同 NIM key 即可三路同时跑(各自 40/min 独立).
#
# 必需 env:
#   NVIDIA_NIM_API_KEY   英文数学通道的 NIM key(与其它两路不同 → 可同时启用)
#   AII_MD_FILE          英文数学书 MD 绝对路径
#   SUBSTRATE            DB 唯一键
# 可选: MATH_TITLE / MATH_NCH(最多章数, 默认12) / NIM_MODEL / NIM_RPM
#
# 用法:
#   NVIDIA_NIM_API_KEY=nvapi-... \
#   AII_MD_FILE="/home/soffy/shared/stratum-to-aii/Calculus Volume 1.md" \
#   SUBSTRATE=openstax_calculus_v1 MATH_TITLE="Calculus Volume 1 (OpenStax)" \
#   bash math_run_en.sh
set -uo pipefail
cd /home/soffy/projects/AII
PY=.venv/bin/python

: "${NVIDIA_NIM_API_KEY:?需设 NVIDIA_NIM_API_KEY(英文数学通道 NIM key; 与 econ/中文数学不同 key 可同时跑)}"
: "${AII_MD_FILE:?需设 AII_MD_FILE(英文数学书 MD 路径)}"
: "${SUBSTRATE:?需设 SUBSTRATE(DB唯一键)}"
export NVIDIA_NIM_API_KEY AII_MD_FILE SUBSTRATE
export MATH_LANG=en                                  # ★英文通道开关
export DATABASE_URL="${DATABASE_URL:-postgresql://aii:aii_safe_pass@localhost:5435/aii_kg}"
export MATH_OUTDIR="math_pipeline/staging/$SUBSTRATE"
mkdir -p "$MATH_OUTDIR"
NCH="${MATH_NCH:-12}"

echo "════ 英文数学通道 [MATH_LANG=en] $SUBSTRATE  $(date '+%H:%M') ════"
echo "  MD=$AII_MD_FILE  模型=${NIM_MODEL:-meta/llama-3.3-70b-instruct}  限流=${NIM_RPM:-36}/min"
for ch in $(seq 1 "$NCH"); do
  echo "── 第 $ch 章 ──"
  $PY scripts/math_pipeline.py "$ch" 2>&1 | grep -vE "INFO|WARNING|huggingface|tokenizer" | tail -3 || true
done
echo "════ 完成 $(date '+%H:%M') — 暂存在 $MATH_OUTDIR ════"

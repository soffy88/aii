#!/usr/bin/env bash
# 持续飞轮(单通道): 监控对应文件夹的 *.md → 逐本处理+入库 → 文件夹无新书则 sleep 后重扫 →
# 永不退出(panel stop 才停). 免费 NIM key → 24/7 不停. 放 MD 进文件夹即自动处理.
# 文件夹: /home/soffy/books/MD/{经济学,中文数学,英文数学}
# 用法: bash flywheel_channel.sh <econ|math_zh|math_en>
set -uo pipefail
cd /home/soffy/projects/AII
PY=.venv/bin/python
DOMAIN="${1:?需指定通道: econ|math_zh|math_en}"
export DATABASE_URL="${DATABASE_URL:-postgresql://aii:aii_safe_pass@localhost:5435/aii_kg}"
export NVIDIA_NIM_API_KEY="$($PY -c "import json;print(json.load(open('.pipeline_keys.json')).get('$DOMAIN',''))")"
# ★飞轮用 NIM 云端 LLM, 不需 GPU; BGE-M3 嵌入强制跑 CPU, 把 GPU 让给 aii-api(否则抢 VRAM
#   导致 aii-api BGE-M3 加载卡住不绑端口). 嵌入在 CPU 稍慢但不阻塞.
export CUDA_VISIBLE_DEVICES=""
SLEEP="${FLYWHEEL_SLEEP:-600}"

case "$DOMAIN" in
  econ)    SUBDIR="经济学" ;;
  math_zh) SUBDIR="中文数学" ;;
  math_en) SUBDIR="英文数学" ;;
  *) echo "未知通道 $DOMAIN"; exit 1 ;;
esac
DIR="/home/soffy/books/MD/$SUBDIR"
mkdir -p "$DIR" flywheel_queue
DONE="flywheel_queue/${DOMAIN}.done"; touch "$DONE"

process_book() {  # $1=md $2=substrate $3=title
  local MD="$1" SUB="$2" TITLE="$3"
  case "$DOMAIN" in
    econ)
      SUBSTRATE="$SUB" AII_MD_FILE="$MD" ECON_TITLE="$TITLE" \
        PIPELINE_CKPT_DIR="econ_pipeline/ckpts" bash scripts/econ_pipeline.sh ;;
    math_zh|math_en)
      local OUT="math_pipeline/staging/$SUB"; mkdir -p "$OUT"
      [ "$DOMAIN" = math_en ] && export MATH_LANG=en || export MATH_LANG=zh
      for ch in $(seq 1 30); do
        SUBSTRATE="$SUB" AII_MD_FILE="$MD" MATH_OUTDIR="$OUT" \
          $PY scripts/math_pipeline.py "$ch" >/dev/null 2>&1 || true
      done
      $PY scripts/math_register.py "$SUB" "$TITLE" --staging "$OUT" --subject 数学 2>/dev/null || true ;;
  esac
}

echo "════ 飞轮 [$DOMAIN] 监控 $DIR (sleep ${SLEEP}s/轮) $(date '+%m-%d %H:%M') ════"
while true; do
  shopt -s nullglob
  for MD in "$DIR"/*.md; do
    SUB="$($PY scripts/flywheel_resolve.py "$DOMAIN" "$MD")"
    grep -qxF "$SUB" "$DONE" && continue
    TITLE="$(basename "$MD" .md)"
    echo "▶ [$DOMAIN] 处理: $TITLE ($SUB) $(date '+%H:%M')"
    process_book "$MD" "$SUB" "$TITLE"
    echo "$SUB" >> "$DONE"
    echo "✓ [$DOMAIN] 完成: $TITLE"
  done
  shopt -u nullglob
  echo "── [$DOMAIN] 文件夹无新书, sleep ${SLEEP}s 后重扫… $(date '+%H:%M') ──"
  sleep "$SLEEP"
done

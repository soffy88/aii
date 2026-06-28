#!/usr/bin/env bash
# 持续飞轮(单通道): 处理书队列 → 入库 → 队列空则 sleep 后重查新书 → 永不退出(panel stop 才停).
# 免费 NIM key → 可 24/7 不停运转. 用法: bash flywheel_channel.sh <econ|math_zh|math_en>
set -uo pipefail
cd /home/soffy/projects/AII
PY=.venv/bin/python
DOMAIN="${1:?需指定通道: econ|math_zh|math_en}"
export DATABASE_URL="${DATABASE_URL:-postgresql://aii:aii_safe_pass@localhost:5435/aii_kg}"
export NVIDIA_NIM_API_KEY="$($PY -c "import json;print(json.load(open('.pipeline_keys.json')).get('$DOMAIN',''))")"
SLEEP="${FLYWHEEL_SLEEP:-600}"
QDIR="flywheel_queue"; mkdir -p "$QDIR"
QUEUE="$QDIR/${DOMAIN}.txt"; DONE="$QDIR/${DOMAIN}.done"
touch "$QUEUE" "$DONE"

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
      # 质量门 + 入库
      $PY scripts/math_register.py "$SUB" "$TITLE" --staging "$OUT" --subject 数学 2>/dev/null || true ;;
  esac
}

echo "════ 飞轮 [$DOMAIN] 持续运转 (sleep ${SLEEP}s/轮) $(date '+%m-%d %H:%M') ════"
while true; do
  while IFS='|' read -r MD SUB TITLE || [ -n "$MD" ]; do
    [ -z "$MD" ] || [[ "$MD" == \#* ]] && continue
    grep -qxF "$SUB" "$DONE" && continue
    echo "▶ [$DOMAIN] 处理: $TITLE ($SUB) $(date '+%H:%M')"
    process_book "$MD" "$SUB" "$TITLE"
    echo "$SUB" >> "$DONE"
    echo "✓ [$DOMAIN] 完成: $SUB"
  done < "$QUEUE"
  echo "── [$DOMAIN] 队列已处理完, sleep ${SLEEP}s 监视新书… $(date '+%H:%M') ──"
  sleep "$SLEEP"
done

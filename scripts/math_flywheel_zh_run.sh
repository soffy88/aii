#!/usr/bin/env bash
# ★程序化中文数学飞轮 — 连续运行(不停, 非定时)
# 处理完一本(math_batch_run 自带 预检R1+R6公式 + 逐章抽取(定义/定理/公式) + ★质量门抽查确认
# → 入A仓/隔离)→ 下一本; 全部完→sleep→再发现→继续. 永不退出.
#
# 用法:  nohup bash scripts/math_flywheel_zh_run.sh >> math_pipeline/flywheel_zh.log 2>&1 &
# 可选:  MATH_IDLE_SLEEP=600(无书时sleep秒, 默认600)
set -uo pipefail
cd "$(dirname "$0")/.."
IDLE="${MATH_IDLE_SLEEP:-600}"
BOOKLIST="math_pipeline/flywheel_zh_booklist.txt"

echo "════════════════════════════════════════════════════"
echo "★ 程序化中文数学飞轮 — 连续运行启动 $(date '+%Y-%m-%d %H:%M')"
echo "  模式: 不停(处理完一本质量门确认→下一本; 无书sleep ${IDLE}s 再查)"
echo "════════════════════════════════════════════════════"

while true; do
    bash scripts/math_flywheel_zh.sh || echo "  ⚠️ 本轮飞轮异常(继续循环) $(date '+%H:%M')"
    if [ ! -s "$BOOKLIST" ]; then
        echo "── [连续] 无新中文数学书, sleep ${IDLE}s 后再发现… $(date '+%H:%M') ──"
        sleep "$IDLE"
    fi
done

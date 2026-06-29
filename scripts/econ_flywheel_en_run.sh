#!/usr/bin/env bash
# ★程序化英文经济学飞轮 — 连续运行(不停, 非定时)
# 处理完一本(econ_batch_run 自带 R1-R9预检 + A仓5步 + ★质量门抽查确认质量 → 入A仓/隔离)→ 下一本;
# 全部处理完(无新书)→ sleep 后再发现 → 继续. 永不退出(Ctrl-C / kill 才停).
#
#   每本"完成上一本自己抽查确认质量后就下一个" = econ_batch_run 逐本: book→管道→质量门→register→next.
#   连续 = 本脚本外层 while: 一批跑完→无书sleep→再查→跑, 不停.
#
# 用法:  nohup bash scripts/econ_flywheel_en_run.sh >> econ_pipeline/flywheel_en.log 2>&1 &
# 可选:  ECON_IDLE_SLEEP=600(无书时sleep秒, 默认600)
set -uo pipefail
cd "$(dirname "$0")/.."
IDLE="${ECON_IDLE_SLEEP:-600}"
BOOKLIST="econ_pipeline/flywheel_en_booklist.txt"

echo "════════════════════════════════════════════════════"
echo "★ 程序化英文经济学飞轮 — 连续运行启动 $(date '+%Y-%m-%d %H:%M')"
echo "  模式: 不停(处理完一本质量门确认→下一本; 无书sleep ${IDLE}s 再查)"
echo "════════════════════════════════════════════════════"

while true; do
    bash scripts/econ_flywheel_en.sh || echo "  ⚠️ 本轮飞轮异常(继续循环) $(date '+%H:%M')"
    # 本轮没发现新书 → sleep 后再查; 有书已在上面逐本处理完(每本质量门确认)
    if [ ! -s "$BOOKLIST" ]; then
        echo "── [连续] 无新英文经济书, sleep ${IDLE}s 后再发现… $(date '+%H:%M') ──"
        sleep "$IDLE"
    fi
done

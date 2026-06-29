#!/usr/bin/env bash
# ★经济学专门管道 — 单本全自动(固化完整标准)
# 固化标准来源: 第一本微观经济学(microecon_en_full_v2)验证过的流程
# 标准内容:
#   - 逐章讲透(六类概念/原理/方法 + 三面: WHAT/WHY/HOW + 双语中英)
#   - 完整性校验(黑体术语 should-have 清单逐章核查 + 自动补漏)
#   - 打磨规则(去脚手架/markdown/空壳KU/残留字符)
#   - 概念抽取 + 共现联结(书内)
#   - 有向关系读出(讲透KU读出,非N²judge,85%+精度)
#   - 按章KC(中文主题名 + 双语簇摘要)
#   - BU七项书级理解(灵魂/定位/根本问题/知识骨架/思维方式/适合谁/诚实边界)
#   - 质量门: complete/残留/空壳/双语/有向/KU密度/讲浅/章密度
#
# Required env vars:
#   SUBSTRATE     DB唯一键 (如 mankiw_macro_zh_v7)
#   AII_MD_FILE   书的 MD 文件绝对路径
# Optional:
#   ECON_TITLE    显示名(默认=SUBSTRATE)
#   QUAL_DIR      质量报告输出目录(默认 econ_pipeline/qual/)
#   PIPELINE_CKPT_DIR  断点续跑checkpoint目录(默认 econ_pipeline/ckpts/)
#
# Exit: 0=质量门通过, 1=质量门报警, 2=管道步骤失败
#
# Usage:
#   SUBSTRATE=mankiw_macro AII_MD_FILE=/path/to/mankiw.md bash scripts/econ_pipeline.sh
set -euo pipefail
cd "$(dirname "$0")/.."

: "${SUBSTRATE:?必须设置 SUBSTRATE (DB唯一键)}"
: "${AII_MD_FILE:?必须设置 AII_MD_FILE (md文件绝对路径)}"

ECON_TITLE="${ECON_TITLE:-$SUBSTRATE}"
QUAL_DIR="${QUAL_DIR:-econ_pipeline/qual}"
PIPELINE_CKPT_DIR="${PIPELINE_CKPT_DIR:-econ_pipeline/ckpts}"

mkdir -p "$QUAL_DIR" "$PIPELINE_CKPT_DIR"
QUAL_JSON="$QUAL_DIR/${SUBSTRATE}.json"

# ★★★ 固化标识: 经济学管道标准版本 ★★★
# v1.1 (2026-06-28): 在 v1.0(microecon_en_full_v2 验证)基础上, 固化"程序端WHAT升级":
#   A 定位 _find_pos 优先真定义框 + 全程跳过目录条目(治本防误落目录)
#   B 边界 _clean_window 清脚注/URL引文/孤立页码(治本防污染窗口)
#   C/D 已成熟: 程序抓 定义框/例子/公式/图表标题/表格数据 + 例子去重+代表性排序
#   分流: 程序抓 WHAT 骨架, gemma4(本地全GPU) 只补 WHY/HOW → 减 LLM 负担、降成本
ECON_PIPELINE_VERSION="econ-std-v1.1"
export SUBSTRATE AII_MD_FILE PIPELINE_CKPT_DIR ECON_PIPELINE_VERSION

PY=.venv/bin/python

echo "════════════════════════════════════════════"
echo "★ 经济学管道 [$ECON_PIPELINE_VERSION]: $ECON_TITLE"
echo "  SUBSTRATE=$SUBSTRATE"
echo "  AII_MD_FILE=$AII_MD_FILE"
echo "  LLM=${ECON_LLM_PROVIDER:-deepseek}${OLLAMA_MODEL:+ ($OLLAMA_MODEL)}"
echo "  QUAL=$QUAL_JSON"
echo "════════════════════════════════════════════"

# ★步骤失败直接退出(set -e), 不静默通过
echo ""
echo "[1/8] 逐章讲透 KU + 完整性校验(黑体术语should-have) + 打磨(去脚手架/空壳/残留)"
$PY scripts/synthesize_book.py || { echo "❌ [1/8] 失败"; exit 2; }

echo ""
echo "[2/8] 概念抽取 + 共现联结(纯计算, 书内)"
$PY scripts/materialize_links.py || { echo "❌ [2/8] 失败"; exit 2; }

echo ""
echo "[3/8] 有向关系读出(讲透KU读出, 非N²judge)"
$PY scripts/readout_all.py || { echo "❌ [3/8] 失败"; exit 2; }

echo ""
echo "[4/8] 节点归一: 概念级有向→图 / KU内部逻辑→留KU"
$PY scripts/normalize_readout.py || { echo "❌ [4/8] 失败"; exit 2; }

echo ""
echo "[5/8] KU内部逻辑结构化(因果链+分解树) + 节点归一"
$PY scripts/structure_logic.py && $PY scripts/normalize_ku_logic_nodes.py || { echo "❌ [5/8] 失败"; exit 2; }

echo ""
echo "[6/8] 按章KC(中文主题名) + 双语簇摘要"
$PY scripts/persist_chapter_kc.py && $PY scripts/fix_kc_labels_summaries.py || { echo "❌ [6/8] 失败"; exit 2; }

echo ""
echo "[7/8] BU 书级理解(七项+忠实校验) 入库"
$PY scripts/generate_bu.py && $PY scripts/persist_bu.py || { echo "❌ [7/8] 失败"; exit 2; }

echo ""
echo "[8/8] ★质量门全检(complete/残留/空壳/双语/有向/KU密度/讲浅/章密度) → $QUAL_JSON"
$PY scripts/econ_quality_gate.py "$SUBSTRATE" --json "$QUAL_JSON"
GATE_EXIT=$?

echo ""
if [ $GATE_EXIT -eq 0 ]; then
    echo "✅ 质量门通过 → 待自动入库 (econ_register.py)"
else
    echo "🚨 质量门报警($GATE_EXIT) → 隔离等人工. 详见 $QUAL_JSON"
fi

exit $GATE_EXIT

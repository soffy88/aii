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

# ★★★ 固化标识: 经济学 A仓 KU 抽取标准(已验证, 全自动)★★★
# econ-A仓-v1.3 (2026-06-29): 双仓架构 A仓标准 = 只忠实抽原始KU给人读(全/不漏/中文).
#   验证: microecon ch9弹性(两约束守住) + Mankiw 生产跑通(persist修复确认).
#   固化内容:
#   ① 程序WHAT骨架 + LLM补WHY/HOW(v1.1基础: _find_pos优先真定义框+跳目录, _clean_window清脚注/页码)
#   ② plan 14K小块(v1.2: 密度大的章granular不被摘要漏)
#   ③ 六类抽取 + 主动抽why(每概念配rationale) + 准入闸门 + 两约束(rationale不编因果/positional不附会)
#   ④ A仓瘦身5步: 讲透+完整性严 / 概念抽取(不共现) / 按章KC / BU(单本枢纽) / KU质量门(含六分类rationale≠0)
#   ⑤ 卸B仓: 有向/归一/谱社区/超边(explains链留provenance)/本性; persist不插生成列is_positional
ECON_PIPELINE_VERSION="econ-A仓-v1.3"
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
# ★★ A仓瘦身(双仓架构): A仓只忠实抽原始KU给人读(全/不漏/中文). 卸到B仓的:
#    有向关系readout / 节点归一normalize / KU内部逻辑structure / 共现 / 谱社区 / 概念归一 / 超边 / 本性.
echo ""
echo "[1/5] 逐章讲透 KU + ★完整性校验严(应有清单, A仓命门:不漏) + 打磨(去脚手架/空壳/残留)"
$PY scripts/synthesize_book.py || { echo "❌ [1/5] 失败"; exit 2; }

echo ""
echo "[2/5] 概念抽取(单本KU涉及哪些概念; ★只概念不共现, 共现=B仓)"
$PY scripts/materialize_links.py || { echo "❌ [2/5] 失败"; exit 2; }

echo ""
echo "[3/5] 按章KC(书内结构, 给人按书读) + 双语簇摘要"
$PY scripts/persist_chapter_kc.py && $PY scripts/fix_kc_labels_summaries.py || { echo "❌ [3/5] 失败"; exit 2; }

echo ""
echo "[4/5] BU 书级理解(七项; ★单本枢纽=ku_concept度数+按章KC, 不碰B仓概念图) 入库"
$PY scripts/generate_bu.py && $PY scripts/persist_bu.py || { echo "❌ [4/5] 失败"; exit 2; }

echo ""
echo "[5/5] ★KU质量门(complete严/残留/空壳/双语/讲浅/密度/章/★六分类rationale≠0; 去有向边/explains=B仓) → $QUAL_JSON"
$PY scripts/econ_quality_gate.py "$SUBSTRATE" --json "$QUAL_JSON"
GATE_EXIT=$?

echo ""
if [ $GATE_EXIT -eq 0 ]; then
    echo "✅ 质量门通过 → 待自动入库 (econ_register.py)"
else
    echo "🚨 质量门报警($GATE_EXIT) → 隔离等人工. 详见 $QUAL_JSON"
fi

exit $GATE_EXIT

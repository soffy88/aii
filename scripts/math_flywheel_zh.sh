#!/usr/bin/env bash
# ★程序化中文数学飞轮 — 自动循环: 发现中文数学书→预检(R1+R6公式)→跑管道→质量门→入库/隔离
# ★程序化 = WHAT 部分由【程序】抽取(math_should_have.py 确定性抽 定义/定理/公式 应有清单),
#   不是 LLM 抽 WHAT; LLM 只负责把每个知识点【讲透】(条件/结论/证明思路/适用)。
#   数学比经济更彻底: 连"抽哪些知识点"(WHAT 清单)都由程序定, 无 LLM 规划。这是本飞轮的根本标识。
# (复制自 math_flywheel.sh，待升级为英文数学专用；原始 math_flywheel.sh 不动)
#
# 流程:
#   math_discover.py 找到未处理的数学书 →
#   math_batch_run.sh 逐本: 预检(R1+R6公式严格) → math_pipeline.py → 质量门 → 入库/隔离 →
#   更新 flywheel_state.json → 生成飞轮报告
#
# Optional env vars:
#   MATH_LIMIT=N        每次最多处理N本(默认10; 0=无限)
#   MATH_DRY_RUN=1      仅预检, 不跑管道
#   MATH_FORCE=1        已入库的书也重跑
#
# Usage:
#   bash scripts/math_flywheel.sh                           # 正常跑
#   MATH_DRY_RUN=1 MATH_LIMIT=3 bash scripts/math_flywheel.sh  # 小批干跑
#
# Cron (每天凌晨3点, 错开经济飞轮的2点):
#   0 3 * * * cd /home/soffy/projects/AII && bash scripts/math_flywheel.sh >> math_pipeline/flywheel.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
# ★中文数学飞轮: 独立 _zh 状态/书单/报告(与英文/原始 math_flywheel 不冲突)
FLYWHEEL_STATE="math_pipeline/flywheel_zh_state.json"
FLYWHEEL_BOOK_LIST="math_pipeline/flywheel_zh_booklist.txt"
FLYWHEEL_REPORT="math_pipeline/flywheel_zh_report.json"
FLYWHEEL_LOG="math_pipeline/flywheel_zh.log"
MATH_LIMIT="${MATH_LIMIT:-10}"

# ★中文应有清单(默认 math_should_have: 定义/定理, 不设MATH_LANG) + NIM key(math_zh 专属,
#   4飞轮各自独立: 英文econ=econ/中文econ=econ_zh/英文math=math_en/中文math=math_zh) + BGE-M3跑CPU
export NVIDIA_NIM_API_KEY="$($PY -c "import json;print(json.load(open('.pipeline_keys.json')).get('math_zh',''))" 2>/dev/null)"
export DATABASE_URL="${DATABASE_URL:-postgresql://aii:aii_safe_pass@localhost:5435/aii_kg}"
export CUDA_VISIBLE_DEVICES=""

mkdir -p math_pipeline

# ── 初始化 state ──
if [ ! -f "$FLYWHEEL_STATE" ]; then
    echo '{"processed":{}}' > "$FLYWHEEL_STATE"
fi

echo "════════════════════════════════════════════════════"
echo "★ 数学知识飞轮 $(date '+%Y-%m-%d %H:%M')"
echo "  LIMIT=$MATH_LIMIT  DRY_RUN=${MATH_DRY_RUN:-0}"
echo "════════════════════════════════════════════════════"
echo ""

# ── Step 1: 发现未处理的【中文】数学书 ──
echo "[1/4] 发现未处理的【中文】数学书(books/MD/中文数学)..."
LIMIT_ARG=""
[ "$MATH_LIMIT" -gt 0 ] 2>/dev/null && LIMIT_ARG="--limit $MATH_LIMIT"
$PY scripts/math_discover_zh.py \
    --out "$FLYWHEEL_BOOK_LIST" \
    --state "$FLYWHEEL_STATE" \
    --verbose \
    $LIMIT_ARG

if [ ! -s "$FLYWHEEL_BOOK_LIST" ]; then
    echo "  ✅ 没有新书需要处理(全部已处理或未发现数学书)"
    echo "════════════════════════════════════════════════════"
    echo "飞轮完成: 无新书"
    exit 0
fi

BOOK_COUNT=$(wc -l < "$FLYWHEEL_BOOK_LIST" | tr -d ' ')
echo "  发现 $BOOK_COUNT 本待处理书 → $FLYWHEEL_BOOK_LIST"
echo ""

# ── Step 2: 批量预检+管道 ──
echo "[2/4] 批量预检 + 管道..."
MATH_MD_LIST="$FLYWHEEL_BOOK_LIST" \
MATH_DRY_RUN="${MATH_DRY_RUN:-0}" \
MATH_FORCE="${MATH_FORCE:-0}" \
bash scripts/math_batch_run.sh 2>&1 | tee -a "$FLYWHEEL_LOG"

echo ""

# ── Step 3: 更新 flywheel_state.json ──
echo "[3/4] 更新飞轮状态..."
$PY - << PYEOF
import json, datetime
from pathlib import Path

state_path = Path('$FLYWHEEL_STATE')
state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {"processed": {}}

batch_report = Path('math_pipeline/batch_report.json')
if not batch_report.exists():
    print("  批量报告不存在, 跳过状态更新")
    exit(0)

quarantine_file = Path('math_pipeline/quarantine.json')
quarantine = json.loads(quarantine_file.read_text(encoding='utf-8')) if quarantine_file.exists() else {"quarantined": []}
ts = datetime.datetime.now(datetime.timezone.utc).isoformat()

for item in quarantine.get('quarantined', []):
    sid = item.get('substrate_id', '')
    reason_type = item.get('reason_type', '')
    if sid:
        state['processed'][sid] = {
            'status': 'precheck_fail' if reason_type == 'precheck' else 'quarantine',
            'reason': item.get('reason_detail', '')[:200],
            'ts': item.get('ts', ts),
        }

# 从 run_log.jsonl 更新 ingested 状态
run_log = Path('math_pipeline/run_log.jsonl')
if run_log.exists():
    for line in run_log.read_text(encoding='utf-8').splitlines():
        try:
            r = json.loads(line)
            if r.get('action') == 'registered':
                sid = r['substrate_id']
                state['processed'][sid] = {
                    'status': 'ingested',
                    'ku_count': r.get('ku_count', 0),
                    'ts': r.get('ts', ts),
                }
        except Exception:
            pass

state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"  飞轮状态已更新: {len(state['processed'])} 条记录")
PYEOF

echo ""

# ── Step 4: 飞轮报告 ──
echo "[4/4] 生成飞轮报告..."
$PY - << PYEOF
import json, datetime
from pathlib import Path

report = {
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "flywheel_run": True,
    "dry_run": "${MATH_DRY_RUN:-0}" == "1",
    "limit": $MATH_LIMIT,
}

batch = Path('math_pipeline/batch_report.json')
if batch.exists():
    report["batch_summary"] = json.loads(batch.read_text(encoding='utf-8')).get("summary", {})

state_file = Path('$FLYWHEEL_STATE')
if state_file.exists():
    s = json.loads(state_file.read_text(encoding='utf-8'))
    by_status = {}
    for v in s.get('processed', {}).values():
        st = v.get('status', '?')
        by_status[st] = by_status.get(st, 0) + 1
    report["flywheel_state"] = by_status

quarantine_file = Path('math_pipeline/quarantine.json')
if quarantine_file.exists():
    q = json.loads(quarantine_file.read_text(encoding='utf-8'))
    report["quarantine_count"] = len(q.get('quarantined', []))

Path('$FLYWHEEL_REPORT').write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

print(f"  飞轮报告 → {Path('$FLYWHEEL_REPORT')}")
print()
print("  批量摘要:")
for k, v in report.get("batch_summary", {}).items():
    print(f"    {k}: {v}")
print()
print("  飞轮状态:")
for k, v in report.get("flywheel_state", {}).items():
    print(f"    {k}: {v}")
if "quarantine_count" in report:
    print(f"  隔离区: {report['quarantine_count']} 本 → math_pipeline/quarantine.json")
PYEOF

echo ""
echo "════════════════════════════════════════════════════"
echo "★ 飞轮完成 $(date '+%Y-%m-%d %H:%M')"
echo "  书单  → $FLYWHEEL_BOOK_LIST"
echo "  状态  → $FLYWHEEL_STATE"
echo "  报告  → $FLYWHEEL_REPORT"
echo "  隔离区 → math_pipeline/quarantine.json"
echo "════════════════════════════════════════════════════"

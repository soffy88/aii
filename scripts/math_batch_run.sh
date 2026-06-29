#!/usr/bin/env bash
# ★数学批量管道 — 逐本: MD预检→逐章抽取→质量门→入库/隔离
#
# 流程(每本书):
#   1. 预检: 中文章节结构(≥3章) + R6公式严格(数学命门)
#   2. 检测MD中章节数, 逐章运行 math_pipeline.py (4并行)
#   3. 质量门: math_quality_gate.py → PASS(exit 0) / ALARM(exit 1) / FAIL(exit 2)
#   4. PASS → math_register.py 自动入库
#      ALARM/FAIL → 隔离到 math_pipeline/quarantine.json
#
# Required:
#   MATH_MD_LIST  文件路径, 每行: "<md绝对路径>|<substrate_id>|<title>"
# Optional:
#   MATH_QUAL_DIR       质量报告目录(默认 math_pipeline/qual/)
#   MATH_STAGING_BASE   暂存根目录(默认 math_pipeline/staging/)
#   MATH_DRY_RUN=1      只做预检, 不跑管道
#   MATH_FORCE=1        已入库的也重跑
#   MATH_CH_PARALLEL=4  每本书并行章数(默认4)
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
MATH_QUAL_DIR="${MATH_QUAL_DIR:-math_pipeline/qual}"
MATH_STAGING_BASE="${MATH_STAGING_BASE:-math_pipeline/staging}"
QUARANTINE_JSON="math_pipeline/quarantine.json"
BATCH_REPORT="math_pipeline/batch_report.json"
DRY_RUN="${MATH_DRY_RUN:-0}"
FORCE="${MATH_FORCE:-0}"
CH_PARALLEL="${MATH_CH_PARALLEL:-4}"

: "${MATH_MD_LIST:?必须设置 MATH_MD_LIST (书单文件路径)}"
if [ ! -f "$MATH_MD_LIST" ]; then
    echo "❌ 书单文件不存在: $MATH_MD_LIST"; exit 1
fi

mkdir -p "$MATH_QUAL_DIR" "$MATH_STAGING_BASE" "math_pipeline"

echo "════════════════════════════════════════════════════"
echo "★ 数学批量管道 $(date '+%Y-%m-%d %H:%M')"
echo "  书单: $MATH_MD_LIST"
echo "  DRY_RUN=$DRY_RUN  FORCE=$FORCE  CH_PARALLEL=$CH_PARALLEL"
echo "════════════════════════════════════════════════════"
echo ""

# ── 辅助: 写 quarantine ──
_quarantine_add() {
    local sid="$1" title="$2" md="$3" reason_type="$4" reason_detail="$5"
    $PY - <<PYEOF 2>/dev/null || true
import json, datetime
from pathlib import Path
qf = Path('$QUARANTINE_JSON')
d = json.loads(qf.read_text(encoding='utf-8')) if qf.exists() else {"quarantined": []}
d["quarantined"] = [x for x in d["quarantined"] if x.get("substrate_id") != "$sid"]
d["quarantined"].append({
    "substrate_id": "$sid",
    "title": "$title",
    "md_path": "$md",
    "reason_type": "$reason_type",
    "reason_detail": "$reason_detail",
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "qual_json": "math_pipeline/qual/$sid.json",
    "status": "pending_review",
})
qf.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
PYEOF
}

# ── 初始化计数 ──
N_TOTAL=0; N_SKIP=0; N_PRECHECK_FAIL=0; N_PIPELINE_FAIL=0; N_ALARM=0; N_PASS=0

if [ ! -f "$QUARANTINE_JSON" ]; then
    echo '{"quarantined":[]}' > "$QUARANTINE_JSON"
fi

# ── 逐本处理 ──
while IFS='|' read -r MD_PATH SUBSTRATE MATH_TITLE || [ -n "$MD_PATH" ]; do
    MD_PATH="${MD_PATH//[$'\r\n']/}"
    [ -z "$MD_PATH" ] || [[ "$MD_PATH" == \#* ]] && continue

    N_TOTAL=$((N_TOTAL + 1))

    # 从 sidecar 补全缺失字段
    if [ -z "$SUBSTRATE" ]; then
        SIDECAR="${MD_PATH%.md}.json"
        [ -f "$SIDECAR" ] && SUBSTRATE=$($PY -c "import json; print(json.load(open('$SIDECAR')).get('id',''))" 2>/dev/null || true)
        [ -z "$SUBSTRATE" ] && SUBSTRATE=$(basename "$MD_PATH" .md)
    fi
    MATH_TITLE="${MATH_TITLE:-$SUBSTRATE}"

    echo "──────────────────────────────────────────"
    echo "[$N_TOTAL] $MATH_TITLE"
    echo "  SUBSTRATE=$SUBSTRATE"
    echo "  MD=$MD_PATH"

    if [ ! -f "$MD_PATH" ]; then
        echo "  ⚠️ MD文件不存在, 跳过"
        N_SKIP=$((N_SKIP + 1)); continue
    fi

    # ── 检查是否已入库 ──
    if [ "$FORCE" != "1" ]; then
        ALREADY=$($PY -c "
import asyncio, asyncpg, os
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('aii/.env'))
async def chk():
    c = await asyncpg.connect(os.getenv('DATABASE_URL'))
    r = await c.fetchrow(
        'SELECT ku_count FROM aii.ingested_substrate WHERE substrate_id=\$1 AND ku_count>30', '$SUBSTRATE')
    await c.close()
    print('yes' if r else 'no')
asyncio.run(chk())
" 2>/dev/null || echo "no")
        if [ "$ALREADY" = "yes" ]; then
            echo "  ✅ 已完整入库(ingested_substrate KU>30), 跳过(FORCE=0)"
            N_SKIP=$((N_SKIP + 1)); continue
        fi
    fi

    # ── 预检: 中文章节结构 + R6公式(数学命门) ──
    echo "  [预检] 章节结构 + R6公式..."
    export _MATH_MD="$MD_PATH"
    PRECHECK=$($PY - <<'PYEOF' 2>/dev/null || echo "FAIL:预检脚本异常"
import re, sys, os
md = os.environ.get('_MATH_MD','')
try:
    text = open(md, encoding='utf-8', errors='replace').read()
except Exception as e:
    print(f'FAIL:无法读取MD({e})'); sys.exit(0)
_CN = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
def _cn2int(s):
    if s in _CN: return _CN[s]
    if s.startswith('十'): return 10 + _CN.get(s[1:], 0)
    if '十' in s:
        a,_,b = s.partition('十'); return _CN[a]*10+(_CN.get(b,0) if b else 0)
    return _CN.get(s, 0)
chapters = set()
for m in re.finditer(r'(?m)^#{0,4}\s*第([一二三四五六七八九十]+)章', text):   # 允许 markdown 前导 #
    line = text[m.start(): m.start()+80]
    if '…' in line or re.search(r'\s\d+\s*$', line): continue
    n = _cn2int(m.group(1))
    if n: chapters.add(n)
for m in re.finditer(r'(?m)^#\s+Chapter\s+(\d+):?\s*$', text):   # ★英文章节(冒号可选)
    chapters.add(int(m.group(1)))
n_ch = len(chapters)
signals = len(re.findall(r'[=Σ∑∫∂√±≤≥≠αβγδεθλμπρσφω∞·×÷]|\bpercentage\b', text))
latexes = len(re.findall(r'\$[^$\n]+\$|\\\[|\\\(', text))
garble = len(re.findall(r'(?:\b[A-Za-z] ){5,}', text))   # ★OCR间隔字母乱码 'C H A P T E R'(born-digital无)
issues = []
if n_ch < 3:
    issues.append(f'R1:章节数({n_ch})<3')
# ★R6 放宽(born-digital aware): 只在 OCR 乱码(公式被毁)时硬拒; born-digital 干净 unicode 公式
#   (无/少 LaTeX 但 garble 低)→ 放行(A仓给人读够用; 保真低于LaTeX但非乱码).
if signals > 30 and latexes == 0 and garble > 20:
    issues.append(f'R6_HARD:数学信号{signals}无LaTeX且OCR乱码(garble={garble}) → 公式被毁')
if issues:
    print('FAIL:' + '; '.join(issues))
else:
    ch_list = ','.join(str(c) for c in sorted(chapters))
    print(f'PASS:{n_ch}章({ch_list})')
PYEOF
)

    if [[ "$PRECHECK" == FAIL* ]]; then
        echo "  ❌ 预检失败: $PRECHECK → 不进管道"
        N_PRECHECK_FAIL=$((N_PRECHECK_FAIL + 1))
        _quarantine_add "$SUBSTRATE" "$MATH_TITLE" "$MD_PATH" "precheck" "$PRECHECK"
        continue
    fi
    echo "  ✅ 预检通过: $PRECHECK"

    if [ "$DRY_RUN" = "1" ]; then
        echo "  [DRY_RUN] 跳过管道执行"
        continue
    fi

    # ── 检测章节列表 ──
    CH_LIST=$(_MATH_MD="$MD_PATH" $PY - <<'PYEOF' 2>/dev/null || echo ""
import re, os
_CN = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
def _cn2int(s):
    if s in _CN: return _CN[s]
    if s.startswith('十'): return 10 + _CN.get(s[1:], 0)
    if '十' in s:
        a,_,b = s.partition('十'); return _CN[a]*10+(_CN.get(b,0) if b else 0)
    return _CN.get(s, 0)
text = open(os.environ['_MATH_MD'], encoding='utf-8', errors='replace').read()
chapters = {}
for m in re.finditer(r'(?m)^第([一二三四五六七八九十]+)章', text):
    line = text[m.start(): m.start()+80]
    if '…' in line or re.search(r'\s\d+\s*$', line): continue
    n = _cn2int(m.group(1))
    if n: chapters[n] = m.start()
print(' '.join(str(c) for c in sorted(chapters.keys())))
PYEOF
)
    if [ -z "$CH_LIST" ]; then
        echo "  ❌ 无法检测章节列表 → 隔离"
        N_PIPELINE_FAIL=$((N_PIPELINE_FAIL + 1))
        _quarantine_add "$SUBSTRATE" "$MATH_TITLE" "$MD_PATH" "pipeline_error" "无法检测章节"
        continue
    fi
    echo "  [管道] 章节: $CH_LIST"

    # ── 暂存目录 ──
    STAGING_DIR="$MATH_STAGING_BASE/$SUBSTRATE"
    mkdir -p "$STAGING_DIR"

    # ── 逐章运行 math_pipeline.py (并行, $CH_PARALLEL 章) ──
    PIPE_FAIL=0
    PIDS=()
    COUNT=0
    for CH in $CH_LIST; do
        # 已存在则跳过(checkpoint)
        if [ -f "$STAGING_DIR/ch${CH}.json" ]; then
            N_KU=$($PY -c "import json; d=json.load(open('$STAGING_DIR/ch${CH}.json')); print(len(d))" 2>/dev/null || echo 0)
            echo "    Ch${CH}: ✓ 已有(${N_KU}条), 跳过"
            continue
        fi
        echo "    Ch${CH}: 开始..."
        AII_MD_FILE="$MD_PATH" MATH_OUTDIR="$STAGING_DIR" \
            $PY scripts/math_pipeline.py "$CH" \
            >> "math_pipeline/${SUBSTRATE}_run.log" 2>&1 &
        PIDS+=($!)
        COUNT=$((COUNT + 1))
        if [ "$COUNT" -ge "$CH_PARALLEL" ]; then
            for PID in "${PIDS[@]}"; do
                wait "$PID" || PIPE_FAIL=$((PIPE_FAIL + 1))
            done
            PIDS=(); COUNT=0
        fi
    done
    # 等待剩余
    for PID in "${PIDS[@]}"; do
        wait "$PID" || PIPE_FAIL=$((PIPE_FAIL + 1))
    done

    if [ "$PIPE_FAIL" -gt 0 ]; then
        echo "  ❌ 管道章节失败($PIPE_FAIL 章) → 隔离"
        N_PIPELINE_FAIL=$((N_PIPELINE_FAIL + 1))
        _quarantine_add "$SUBSTRATE" "$MATH_TITLE" "$MD_PATH" "pipeline_error" "${PIPE_FAIL}章管道失败"
        continue
    fi

    # ── 质量门 ──
    echo "  [质量门] math_quality_gate.py..."
    QUAL_JSON="$MATH_QUAL_DIR/${SUBSTRATE}.json"
    GATE_EXIT=0
    $PY scripts/math_quality_gate.py "$SUBSTRATE" \
        --staging "$STAGING_DIR" \
        --md "$MD_PATH" \
        --json "$QUAL_JSON" \
        2>&1 | tee -a "math_pipeline/${SUBSTRATE}_run.log" || GATE_EXIT=$?

    if [ "$GATE_EXIT" -eq 0 ]; then
        # ── PASS → 入库 ──
        echo "  ✅ 质量门通过 → 自动入库..."
        $PY scripts/math_register.py "$SUBSTRATE" "$MATH_TITLE" \
            --staging "$STAGING_DIR" --subject 数学
        N_PASS=$((N_PASS + 1))
        echo "  ✅ 已入正式库: $SUBSTRATE"
    else
        # ── ALARM/FAIL → 隔离 ──
        ALARM_REASONS="see $QUAL_JSON"
        if [ -f "$QUAL_JSON" ]; then
            ALARM_REASONS=$($PY -c "
import json; d=json.load(open('$QUAL_JSON'))
alarms = d.get('alarms',[])
print(' | '.join(alarms[:3]) if alarms else 'see $QUAL_JSON')
" 2>/dev/null || echo "see $QUAL_JSON")
        fi
        _quarantine_add "$SUBSTRATE" "$MATH_TITLE" "$MD_PATH" "quality_gate" "$ALARM_REASONS"
        N_ALARM=$((N_ALARM + 1))
        echo "  🚨 质量门报警(exit=$GATE_EXIT) → 隔离: $ALARM_REASONS"
    fi

done < "$MATH_MD_LIST"

# ── 批量报告 ──
$PY - <<PYEOF 2>/dev/null
import json, datetime
from pathlib import Path
report = {
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "book_list": "$MATH_MD_LIST",
    "dry_run": "$DRY_RUN" == "1",
    "summary": {
        "total": $N_TOTAL,
        "skipped(已入库)": $N_SKIP,
        "precheck_fail": $N_PRECHECK_FAIL,
        "pipeline_fail": $N_PIPELINE_FAIL,
        "alarm_quarantined": $N_ALARM,
        "pass_registered": $N_PASS,
    },
    "quarantine_file": "$QUARANTINE_JSON",
    "qual_dir": "$MATH_QUAL_DIR",
}
Path("$BATCH_REPORT").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
PYEOF

echo ""
echo "════════════════════════════════════════════════════"
echo "★ 批量完成 $(date '+%Y-%m-%d %H:%M')"
echo "  总计=$N_TOTAL | 跳过=$N_SKIP | 预检失败=$N_PRECHECK_FAIL"
echo "  管道失败=$N_PIPELINE_FAIL | 质量拦截=$N_ALARM | 成功入库=$N_PASS"
echo ""
echo "  批量报告 → $BATCH_REPORT"
echo "  隔离区   → $QUARANTINE_JSON"
echo "════════════════════════════════════════════════════"

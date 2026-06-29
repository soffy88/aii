"""单章全链路: 修复版md按章切 → 打磨版HQ抽KU(双语) → 打chapter_id → 归一 → 章内建边.
验证 抽KU(双语)+章内建边 完整机制. Usage: chapter_ingest.py <chapter_n>"""
import asyncio, json, os, re, sys
from pathlib import Path
from collections import Counter
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "aii" / ".env", override=True)
import asyncpg
from aii.api._provider import register_providers
from aii.service import onto_prompts as P
from aii.service import onto_vocab as V
# persist_ontology_result 只在 ingest_chapter() 里用,懒加载避免 omodul 依赖在 import 时触发
from aii.service.concept_onto_ops import vectorize_and_normalize
from aii.service.cross_chunk_link import gen_candidates, judge_and_link
from aii.storage.pg_backend import PgBackend
from obase import ProviderRegistry

# ★md 文件可 env 覆盖(给第二本书=数学书用), 默认微观经济学
SM = Path(os.getenv("AII_MD_FILE",
    "/home/soffy/shared/stratum-to-aii/Principles_of_Microeconomics_The_Way_We__01KVAJCX.md"))

_CN = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}


def _cn2int(s):
    # 一..九 / 十 / 十一..十九 / 二十..
    if s in _CN:
        return _CN[s]
    if s.startswith('十'):
        return 10 + (_CN.get(s[1:], 0))
    if '十' in s:  # 二十/二十一..
        a, _, b = s.partition('十')
        return _CN[a] * 10 + (_CN.get(b, 0) if b else 0)
    return _CN.get(s, 0)


def _zh_chapter_starts(text):
    """中文教材 第N章: 跳 TOC(含…… 或 末尾页码), 每章取首个正文出现(页眉重复取第一个)."""
    starts = {}
    for m in re.finditer(r'(?m)^#{0,4}\s*第([一二三四五六七八九十]+)章', text):   # 允许 markdown 前导 #
        line = text[m.start(): text.find('\n', m.start()) if text.find('\n', m.start()) > 0 else m.start() + 40]
        if '…' in line or re.search(r'\s\d+\s*$', line):   # TOC 条目(…… 页码)→ 跳
            continue
        num = _cn2int(m.group(1))
        if num and num not in starts:                       # 每章首个正文出现(后续页眉重复忽略)
            starts[num] = m.start()
    return starts


def chapter_starts(text):
    """章起始位置(英文 # Chapter N: 或中文 第N章). 自动判格式."""
    starts = {int(m.group(1)): m.start() for m in re.finditer(r'(?m)^#\s+Chapter\s+(\d+):', text)}
    if not starts:
        starts = _zh_chapter_starts(text)
    return starts


def chapter_numbers(text):
    return sorted(chapter_starts(text).keys())


def slice_chapter(text, n):
    starts = chapter_starts(text)
    if n not in starts:
        raise SystemExit(f"chapter {n} not found; have {sorted(starts)}")
    s = starts[n]
    e = starts.get(n + 1, len(text))
    chap = text[s:e]
    # ★末章截掉书末 back-matter(GLOSSARY/INDEX, 可能间隔字母 'G L O S S A R Y')—— 防全书术语表误抽进末章
    bm = re.search(r'(?im)^#{1,3}\s*\**\s*(?:g\s*l\s*o\s*s\s*s\s*a\s*r\s*y|i\s*n\s*d\s*e\s*x)\b', chap)
    if bm:
        chap = chap[:bm.start()]
    return chap


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    sub = f"microecon_en_ch{n}"
    register_providers()
    backend = PgBackend(); backend.dsn = os.getenv("DATABASE_URL")
    llm = ProviderRegistry.get().llm("default")
    from oskill import ontology_extract

    text = SM.read_text(encoding="utf-8", errors="replace")
    chap = slice_chapter(text, n)
    title = chap.splitlines()[0].lstrip("# ").strip()
    print(f"[{sub}] chapter {n}: '{title[:60]}'  chars={len(chap)} (~{len(chap)//2000} chunks)", flush=True)

    # 1. 抽取 — 打磨版 HQ + 双语
    r = await ontology_extract(
        source_text=chap, llm=llm, chunk_size=2000, doc_type="textbook", source_credibility="high",
        pass1_chunk_tmpl=P.PASS1_CHUNK_TMPL, pass1_chunk_system=P.PASS1_CHUNK_SYSTEM,
        pass1_outline_tmpl=P.PASS1_OUTLINE_TMPL, pass1_outline_system=P.PASS1_OUTLINE_SYSTEM,
        pass2_chunk_tmpl=P.PASS2_CHUNK_TMPL_HQ, pass2_system=P.PASS2_SYSTEM_HQ,
        valid_knowledge_types=V.VALID_KNOWLEDGE_TYPES, valid_sub_types=V.VALID_SUB_TYPES,
        valid_relation_types=V.VALID_RELATION_TYPES)
    print(f"  extracted {len(r.ku_candidates)} KU candidates", flush=True)

    # 2. 持久化
    from aii.service.onto_persist import persist_ontology_result  # 懒加载(omodul依赖在运行时才需要)
    trail = Path("/tmp/onto_trails"); trail.mkdir(parents=True, exist_ok=True)
    ps = await persist_ontology_result(dsn=backend.dsn, substrate_id=sub, result=r, trail_dir=trail, backend=backend)
    print(f"  persisted registered={ps.get('registered')} rejected={ps.get('rejected')} rej_struct={ps.get('rejected_structural',0)}", flush=True)

    conn = await asyncpg.connect(backend.dsn)
    from pgvector.asyncpg import register_vector
    await register_vector(conn)
    # 3. 打 chapter_id (provenance)
    await conn.execute(
        "UPDATE aii.ku_onto SET provenance = provenance || jsonb_build_object('chapter',$2::int) WHERE substrate_id=$1",
        sub, n)
    # 4. 概念归一 (章内, 供共现预筛)
    norm = await vectorize_and_normalize(conn, llm, substrate_id=sub, discipline="经济学")
    print(f"  normalize: {norm['before']}→{norm['after']} concepts", flush=True)
    # 5. 章内建边: 共现预筛候选 → LLM 判真关系
    cands = await gen_candidates(conn, substrate_id=sub, sem_threshold=0.80)
    print(f"  chapter-internal candidates={len(cands)}", flush=True)
    estats = await judge_and_link(conn, llm, cands, substrate_id=sub)
    print(f"  edges built: linked={estats['linked']}/{estats['candidates']} by_relation={estats['by_relation']}", flush=True)

    n_ku = await conn.fetchval("SELECT count(*) FROM aii.ku_onto WHERE substrate_id=$1", sub)
    n_zh = await conn.fetchval("SELECT count(*) FROM aii.ku_onto WHERE substrate_id=$1 AND natural_text_zh<>''", sub)
    await conn.close()
    print(f"\nDONE {sub}: KU={n_ku} zh={n_zh} edges_linked={estats['linked']} chapter_id={n}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

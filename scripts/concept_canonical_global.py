"""★M0 全局/跨书概念语义归一驱动. 复用 concept_onto_ops.vectorize_and_normalize_global.
用法:
  concept_canonical_global.py --filter '%opportunity cost%'   # 一组试(验证防错合)
  concept_canonical_global.py                                  # 全库
"""
import asyncio, os, sys
from pathlib import Path
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "aii" / ".env", override=True)
sys.path.insert(0, str(ROOT))
import asyncpg
from pgvector.asyncpg import register_vector
from aii.api._provider import register_providers
from obase import ProviderRegistry
from aii.service.concept_onto_ops import vectorize_and_normalize_global


async def main():
    name_filter = None
    if "--filter" in sys.argv:
        name_filter = sys.argv[sys.argv.index("--filter") + 1]
    dry_run = "--dry-run" in sys.argv
    register_providers()
    llm = ProviderRegistry.get().llm("default")
    conn = await asyncpg.connect(os.getenv("DATABASE_URL"))
    await register_vector(conn)
    print(f"★全局归一 filter={name_filter or '(全库)'} dry_run={dry_run}", flush=True)
    r = await vectorize_and_normalize_global(conn, llm, name_filter=name_filter, dry_run=dry_run)
    mk = "would_merge" if dry_run else "merged"
    print(f"\n前 {r['before']} → {'(dry,不落库)' if dry_run else '后 '+str(r['after'])} "
          f"({mk} {r.get(mk)}, 候选对 {r['candidates']}, ★硬闸挡 {r['hardgate_blocked']})", flush=True)
    print("=== 会合并的组(canonical ← 被并入的)===", flush=True)
    for g in r["groups"]:
        print(f"  ✓ {g['canonical']}  ←  {g['merged']}", flush=True)
    if not r["groups"]:
        print("  (无合并)", flush=True)
    await conn.close()


asyncio.run(main())

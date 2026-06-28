"""回滚 M0 测试时的 2 组错合(判别词矛盾的): 把被错合并入的概念从 canonical 拆回独立.
对每个 (canonical, split_name):
  ① 若 split_name 概念已存在(不同casing存活)→ 只从 canonical.aliases 移除.
  ② 否则重建概念(name/discipline/vector)+ 把标题==split_name 的KU链接从 canonical 移到新概念 + 移除 alias.
正确的合并(price elasticity 的 case 变体等)不动——只回滚判别词矛盾的.
"""
import asyncio, os, sys, json
from pathlib import Path
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "aii" / ".env", override=True)
sys.path.insert(0, str(ROOT))
import asyncpg
from pgvector.asyncpg import register_vector
from aii.api._provider import register_providers
from aii.service.concept_onto_ops import _encode, _forced_different

# 只回滚【判别词矛盾】的真错合(income≠price / unitary-price≠unitary-income / increasing-OC≠OC).
# "opportunity cost of production / of investment" 判别词不矛盾 = 短↔全称(同 price elasticity of
# demand↔price elasticity), 属正确合并, 不回滚(_forced_different=False 会自动 skip).
ROLLBACK = [
    ("price-inelastic goods",     ["income-inelastic goods"]),
    ("Unitary income elasticity", ["unitary price elastic demand"]),
    ("Opportunity cost",          ["increasing opportunity cost"]),
]


async def main():
    register_providers()   # ★必须: 让 _encode 走 BGE-M3 1024 维(否则 fallback 128 维)
    conn = await asyncpg.connect(os.getenv("DATABASE_URL"))
    await register_vector(conn)
    for can_name, splits in ROLLBACK:
        can = await conn.fetchrow("SELECT concept_id, discipline, aliases FROM aii.concept_onto WHERE name=$1", can_name)
        if not can:
            print(f"  ⚠ canonical 不存在: {can_name}"); continue
        can_id = can["concept_id"]
        for sp in splits:
            if not _forced_different(can_name, sp):   # 判别词不矛盾 = 正确合并, 不回滚
                print(f"  – skip(判别词不矛盾,属正确合并): {sp}"); continue
            exist = await conn.fetchrow("SELECT concept_id FROM aii.concept_onto WHERE name=$1", sp)
            async with conn.transaction():
                if exist:
                    new_id = exist["concept_id"]
                    note = "已存在(存活)"
                else:
                    vec = _encode([sp])[0].tolist()
                    new_id = await conn.fetchval(
                        "INSERT INTO aii.concept_onto(name, discipline, vector) VALUES($1,$2,$3) RETURNING concept_id",
                        sp, can["discipline"], vec)
                    note = "重建"
                # 标题==split_name 的 KU: 当前挂 canonical → 移到新概念
                moved = 0
                kus = await conn.fetch(
                    "SELECT DISTINCT k.ku_id FROM aii.ku_onto k WHERE lower(k.title)=lower($1)", sp)
                for r in kus:
                    ku = r["ku_id"]
                    has = await conn.fetchval(
                        "SELECT 1 FROM aii.ku_concept_onto WHERE ku_id=$1 AND concept_id=$2", ku, can_id)
                    if has:
                        await conn.execute("INSERT INTO aii.ku_concept_onto(ku_id,concept_id) VALUES($1,$2) ON CONFLICT DO NOTHING", ku, new_id)
                        await conn.execute("DELETE FROM aii.ku_concept_onto WHERE ku_id=$1 AND concept_id=$2", ku, can_id)
                        moved += 1
                # 从 canonical.aliases 移除该名
                await conn.execute(
                    "UPDATE aii.concept_onto SET aliases = (SELECT jsonb_agg(x) FROM jsonb_array_elements(aliases) x WHERE x <> $1::jsonb) WHERE concept_id=$2",
                    json.dumps(sp), can_id)
                print(f"  ✓ 拆回 [{note}] {sp} ← from {can_name} (移回KU链接 {moved})")
    await conn.close()
    print("回滚完成")


asyncio.run(main())

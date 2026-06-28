import asyncio, asyncpg, os, json, httpx
from pathlib import Path
from dotenv import load_dotenv; load_dotenv("/home/soffy/projects/AII/aii/.env", override=True)
SUB=os.getenv('SUBSTRATE','microecon_en_full_v2'); KEY=os.getenv('DEEPSEEK_API_KEY')
async def go():
    bu=json.loads(Path(f"econ_pipeline/bu_{SUB}.json").read_text())
    # 边界用生成版(忠实校验在 generate 阶段做; 不再硬编码 microecon 边界)
    zh={k:bu[k] for k in ['soul','positioning','question','skeleton','thinking','for_whom','boundary']}
    SYS="Translate each JSON value to concise English. Output JSON same keys, English values only."
    # ★走 ProviderRegistry: ECON_LLM_PROVIDER=ollama → gemma4(本地); 否则 DeepSeek. call_sync=JSON mode.
    from aii.api._provider import register_providers
    from obase import ProviderRegistry
    register_providers()
    llm = ProviderRegistry.get().llm("default")
    en = json.loads(llm.call_sync(SYS + "\n\n" + json.dumps(zh, ensure_ascii=False)))
    c=await asyncpg.connect(os.getenv('DATABASE_URL'))
    await c.execute("ALTER TABLE aii.bu_onto ADD COLUMN IF NOT EXISTS facets_zh jsonb")
    await c.execute("ALTER TABLE aii.bu_onto ADD COLUMN IF NOT EXISTS facets_en jsonb")
    await c.execute("DELETE FROM aii.bu_onto WHERE substrate_id=$1",SUB)
    nkc=await c.fetchval("SELECT count(*) FROM aii.kc_onto WHERE substrate_id=$1",SUB)
    await c.execute("""INSERT INTO aii.bu_onto(substrate_id,doc_type,overview_oneline,problem_statement,learning_thread,
        facets_zh,facets_en,synthesis_marker) VALUES($1,'textbook',$2,$3,$4,$5,$6,'AII综合-书级理解,非原文断言')""",
        SUB, zh['soul'], zh['question'], zh['thinking'], json.dumps(zh,ensure_ascii=False), json.dumps(en,ensure_ascii=False))
    print(f"BU入库(校验版) + 双语 ✓ (关联 {nkc} KC)")
    print("boundary_zh:", zh['boundary'][:60])
    print("soul_en:", en.get('soul','')[:80])
    await c.close()
asyncio.run(go())

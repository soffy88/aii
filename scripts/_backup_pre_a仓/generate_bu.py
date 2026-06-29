import asyncio, asyncpg, os, json, httpx
from dotenv import load_dotenv; load_dotenv('aii/.env',override=True)
SUB=os.getenv('SUBSTRATE','microecon_en_full_v2'); KEY=os.getenv('DEEPSEEK_API_KEY')
async def go():
    c=await asyncpg.connect(os.getenv('DATABASE_URL'))
    kcs=await c.fetch("SELECT community_label FROM aii.kc_onto WHERE substrate_id=$1 AND synthesis_marker='AII章节KC' ORDER BY level",SUB)
    hubs=await c.fetch("""SELECT cc.name, count(*) d FROM aii.ku_concept_onto kc JOIN aii.concept_onto cc ON kc.concept_id=cc.concept_id
        JOIN aii.ku_onto k ON kc.ku_id=k.ku_id WHERE k.substrate_id=$1 GROUP BY 1 ORDER BY 2 DESC LIMIT 12""",SUB)
    # sample KU essences (titles span breadth)
    booktitle=await c.fetchval("SELECT title FROM aii.ingested_substrate WHERE substrate_id=$1",SUB) or SUB
    samp=await c.fetch(f"SELECT title FROM aii.ku_onto WHERE substrate_id='{SUB}' ORDER BY random() LIMIT 30")
    await c.close()
    topics="\n".join("- "+k['community_label'] for k in kcs)
    hubtxt=", ".join(f"{h['name']}({h['d']})" for h in hubs)
    samptxt=", ".join(s['title'] for s in samp)
    SYS=("You synthesize a BOOK UNDERSTANDING (BU) — the highest-level grasp of a book — by ABSTRACTING from its "
         "knowledge structure (NOT retelling/summarizing chapters). ★STRICT: every claim must be derivable from the "
         "given topics/concepts/KUs. Do NOT fabricate. For 思维方式(way of thinking) and 诚实边界(honest boundaries) "
         "— the most over-reach-prone — assert ONLY what the structure actually shows; if unsure, hedge. "
         "知识骨架 MUST use the data-computed hub concepts given. Output JSON with 7 fields (简体中文): "
         '{"soul":"一句话灵魂","positioning":"背景定位","question":"根本问题","skeleton":"知识骨架(用枢纽概念)",'
         '"thinking":"思维方式","for_whom":"适合谁能干什么","boundary":"诚实边界(不讲什么)"}.')
    body=(f"Book: {booktitle}\n\n"
          f"主题KC(知识结构):\n{topics}\n\n"
          f"★数据算出的枢纽概念(度中心, 括号=涉及KU数)= 知识骨架支柱:\n{hubtxt}\n\n"
          f"KU样本(覆盖广度): {samptxt}")
    # ★走 ProviderRegistry: ECON_LLM_PROVIDER=ollama → gemma4(本地); 否则 DeepSeek. call_sync=JSON mode.
    from aii.api._provider import register_providers
    from obase import ProviderRegistry
    register_providers()
    llm = ProviderRegistry.get().llm("default")
    j = json.loads(llm.call_sync(SYS + "\n\n" + body))
    import pathlib; pathlib.Path("econ_pipeline").mkdir(exist_ok=True); pathlib.Path(f"econ_pipeline/bu_{SUB}.json").write_text(json.dumps(j,ensure_ascii=False,indent=2))
    labels=[("①一句话灵魂","soul"),("②背景定位","positioning"),("③根本问题","question"),("④知识骨架","skeleton"),("⑤思维方式","thinking"),("⑥适合谁/能干什么","for_whom"),("⑦诚实边界","boundary")]
    for lab,k in labels: print(f"\n【{lab}】\n{j.get(k,'(缺)')}")
asyncio.run(go())

import asyncio, asyncpg, os, re
from dotenv import load_dotenv; load_dotenv('aii/.env',override=True)
SUB=os.getenv('SUBSTRATE','microecon_en_full_v2')
def norm(t):
    t=re.sub(r'\([^)]*\)','',t).lower().strip()           # 去 (Concept)/(PPF)
    t=re.sub(r'[^a-z0-9一-鿿\s/&-]','',t); t=re.sub(r'\s+',' ',t).strip()  # 保留中文
    return re.sub(r's\b','',t)                              # 单复数归一
async def go():
    c=await asyncpg.connect(os.getenv('DATABASE_URL'))
    from pgvector.asyncpg import register_vector; await register_vector(c)
    kus=await c.fetch(f"SELECT ku_id,title,natural_text,(provenance->>'chapter')::int ch FROM aii.ku_onto WHERE substrate_id='{SUB}'")
    # 1. 概念词表 = 去重归一标题; canonical = 首次出现的清洗标题
    canon={}; 
    for k in kus:
        n=norm(k['title'])
        if n and n not in canon: canon[n]=re.sub(r'\s*\([^)]*\)','',k['title']).strip()
    print(f"unique concepts (from titles): {len(canon)}", flush=True)
    # 2. 确定性概念抽取: 每KU涉及 = 自身概念 ∪ 正文里出现的其他概念(短语≥6字, 词边界)
    # 概念短语匹配: 英文≥6字符; 中文≥3字(中文概念短, 映射/导数/极限)
    phrases=[(n,re.compile(r'(?<![a-z])'+re.escape(n)+r'(?:e?s)?(?![a-z])',re.I)) for n in canon
             if len(n)>=6 or (re.search(r'[一-鿿]',n) and len(n)>=3)]
    ku_concepts={}
    for k in kus:
        body=(k['natural_text'] or '').lower(); own=norm(k['title'])
        inv={own} if own in canon else set()
        for n,rx in phrases:
            if n!=own and rx.search(body): inv.add(n)
        ku_concepts[k['ku_id']]=inv
    # 3. 物化概念 + ku_concept 链接 (concept_onto 全局, ON CONFLICT name 复用; discipline 标 SUBSTRATE 可识别)
    await c.execute("DELETE FROM aii.ku_concept_onto WHERE ku_id LIKE $1", SUB+'::%')
    cid={}
    for n,name in canon.items():
        row=await c.fetchrow("INSERT INTO aii.concept_onto(name,discipline) VALUES($1,$2) "
                             "ON CONFLICT(name) DO UPDATE SET name=EXCLUDED.name RETURNING concept_id", name, SUB)
        cid[n]=row['concept_id']
    links=0
    for kid,inv in ku_concepts.items():
        for n in inv:
            if n in cid:
                await c.execute("INSERT INTO aii.ku_concept_onto(ku_id,concept_id) VALUES($1,$2) ON CONFLICT DO NOTHING",kid,cid[n]); links+=1
    print(f"ku_concept links: {links}", flush=True)
    # 4. 共现 (纯SQL, 0 LLM): 共享概念对 + 语义余弦 + 强度
    await c.execute("DELETE FROM aii.ku_cooccurrence WHERE substrate_id=$1",SUB)
    await c.execute(f"""
      INSERT INTO aii.ku_cooccurrence(substrate_id,ku_a,ku_b,shared_concept_count,semantic_sim,strength)
      SELECT $1, a.ku_id, b.ku_id, count(*) shared,
             (1-(ka.embedding<=>kb.embedding))::real sim,
             CASE WHEN count(*)>=2 AND (1-(ka.embedding<=>kb.embedding))>=0.80 THEN 'strong'
                  WHEN count(*)>=2 THEN 'medium' ELSE 'weak' END
      FROM aii.ku_concept_onto a JOIN aii.ku_concept_onto b ON a.concept_id=b.concept_id AND a.ku_id<b.ku_id
      JOIN aii.ku_onto ka ON a.ku_id=ka.ku_id AND ka.substrate_id=$1
      JOIN aii.ku_onto kb ON b.ku_id=kb.ku_id AND kb.substrate_id=$1
      GROUP BY a.ku_id,b.ku_id,ka.embedding,kb.embedding""", SUB)
    n=await c.fetchval("SELECT count(*) FROM aii.ku_cooccurrence WHERE substrate_id=$1",SUB)
    print(f"co-occurrence links: {n}", flush=True)
    await c.close()
asyncio.run(go())

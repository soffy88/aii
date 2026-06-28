import asyncio, asyncpg, os, json, httpx, re
from dotenv import load_dotenv; load_dotenv('aii/.env',override=True)
SUB=os.getenv('SUBSTRATE','microecon_en_full_v2'); KEY=os.getenv('DEEPSEEK_API_KEY')
SYS=("Read out ONLY the directed relations this knowledge unit's text EXPLICITLY expresses "
     "(text directly describes them; exclude quoted/inferred). Types: "
     "prerequisite (A must be understood/exist before B), subsumes (A includes B as part/type), "
     "derives (A leads to/produces/implies B), "
     "explains (A is the why/mechanism behind B — A explains why B holds). "
     "★explains ONLY when the text gives a real causal mechanism, never invent one. src/dst named in text. "
     'Output JSON {"rels":[{"src":"..","dst":"..","type":".."}]}. None->{"rels":[]}. '
     '★ Name src/dst concepts in the SAME language as the Concept title (中文 if title is Chinese).')
async def call(cli, k, tries=3):
    # ★喂与标题同语言的正文(中文书喂中文)→ 读出节点与概念canon同语言, 能匹配
    body = (k['natural_text_zh'] if re.search(r'[一-鿿]', k['title'] or '') else k['natural_text']) or k['natural_text'] or ''
    prompt = "Concept: %s\n\n%s" % (k['title'], body[:2500])
    payload = {"model":"deepseek-v4-flash","response_format":{"type":"json_object"},
               "messages":[{"role":"system","content":SYS},{"role":"user","content":prompt}]}
    for _ in range(tries):
        try:
            r = await cli.post("https://api.deepseek.com/chat/completions",
                               headers={"Authorization":"Bearer "+KEY}, json=payload)
            j = json.loads(r.json()["choices"][0]["message"]["content"])
            return [x for x in j.get('rels',[]) if x.get('src') and x.get('dst')
                    and x.get('type') in ('prerequisite','subsumes','derives','explains') and x['src']!=x['dst']]
        except Exception:
            await asyncio.sleep(2)
    return None
async def go():
    c=await asyncpg.connect(os.getenv('DATABASE_URL'))
    # ★增量: 只读尚未读出的 KU(已在 concept_readout_edge 的跳过), 省 LLM. 全删用 AII_READOUT_FULL=1
    if os.getenv("AII_READOUT_FULL")=="1":
        await c.execute("DELETE FROM aii.concept_readout_edge WHERE substrate_id=$1",SUB)
    kus=await c.fetch(f"""SELECT ku_id,title,natural_text,natural_text_zh FROM aii.ku_onto WHERE substrate_id='{SUB}'
        AND ku_id NOT IN (SELECT DISTINCT ku_id FROM aii.concept_readout_edge WHERE substrate_id='{SUB}')""")
    sem=asyncio.Semaphore(10); tot=[0]; fail=[]; done=[0]; lock=asyncio.Lock()
    async with httpx.AsyncClient(trust_env=False, timeout=55) as cli:
        async def rd(k):
            async with sem:
                rels=await call(cli,k)
                async with lock:
                    if rels is None: fail.append(k['ku_id'])
                    else:
                        for x in rels:
                            await c.execute("INSERT INTO aii.concept_readout_edge VALUES($1,$2,$3,$4,$5)",
                                            SUB,k['ku_id'],x['src'][:120],x['dst'][:120],x['type']); tot[0]+=1
                    done[0]+=1
                    if done[0]%80==0: print(f"  {done[0]}/{len(kus)}, {tot[0]} edges, {len(fail)} fail",flush=True)
        await asyncio.gather(*(rd(k) for k in kus))
    print(f"DONE: {tot[0]} edges, {len(kus)} KUs, {len(fail)} fails {fail[:5]}",flush=True)
    await c.close()
asyncio.run(go())

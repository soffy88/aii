"""Display endpoints — read-only KU/Graph/KC/BU views, on _onto tables (onto pipeline).

迁移自旧 aii.ku/edge/concept (uuid + synthesis 行) → _onto 表:
  ku_onto(ku_id TEXT) / edge_onto / concept_onto / ku_concept_onto / kc_onto / bu_onto.
ku_id 现在是 TEXT(命名空间化 substrate::ku_cN), 不再是 uuid — 移除所有 UUID 校验/转换.
kc/bu 从独立表 kc_onto/bu_onto 读(原是 ku 里的 synthesis/book_understanding 行).
"""
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Query
from oprim import vector_encode

from aii.api._dependencies import backend
from aii.api._envelope import success_response, error_response

router = APIRouter()

_SHARED_DIR = Path(os.getenv("FLYWHEEL_SHARED_DIR", "/home/soffy/shared/stratum-to-aii"))


# ── helpers ────────────────────────────────────────────────────────────────

def _str(v) -> str | None:
    return str(v) if v is not None else None


def _jsonb(v):
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


# ── KU list / detail ────────────────────────────────────────────────────────

@router.get("/ku/list")
async def ku_list(
    grade: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    substrate: Optional[str] = Query(None),
    merged_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    size: int = Query(0),           # legacy alias
):
    """Paginated KU list (ku_onto)."""
    try:
        effective_size = page_size if page_size != 20 or size == 0 else size
        conditions: list[str] = ["TRUE"]
        params: list = []

        if grade:
            params.append(grade)
            conditions.append(f"k.grade = ${len(params)}")
        if type:
            params.append(type)
            conditions.append(f"k.knowledge_type = ${len(params)}")
        if substrate:
            params.append(substrate)
            conditions.append(f"k.substrate_id = ${len(params)}")
        if merged_only:
            conditions.append("k.merge_count > 1")

        where = " AND ".join(conditions)
        offset = (page - 1) * effective_size
        filter_params = params[:]
        params += [effective_size, offset]
        limit_p = len(params) - 1
        offset_p = len(params)

        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT count(*) FROM aii.ku_onto k WHERE {where}", *filter_params
            )
            rows = await conn.fetch(
                f"""
                SELECT k.ku_id, left(k.natural_text, 300) AS natural_text,
                       left(k.natural_text_zh, 400) AS natural_text_zh,
                       k.knowledge_type, k.grade, k.substrate_id,
                       s.title AS substrate_title, s.subject AS subject,
                       k.merge_count, k.created_at
                FROM aii.ku_onto k
                LEFT JOIN aii.ingested_substrate s ON k.substrate_id = s.substrate_id
                WHERE {where}
                ORDER BY k.created_at DESC
                LIMIT ${limit_p} OFFSET ${offset_p}
                """,
                *params,
            )

        items = [
            {
                "id": _str(r["ku_id"]),
                "natural_text": r["natural_text"],
                "natural_text_zh": r["natural_text_zh"],
                "knowledge_type": r["knowledge_type"],
                "grade": r["grade"],
                "substrate_id": r["substrate_id"],
                "substrate_title": r["substrate_title"],
                "subject": r["subject"],
                "merge_count": r["merge_count"],
                "defeater_count": 0,
            }
            for r in rows
        ]
        return success_response({
            "total": total,
            "page": page,
            "page_size": effective_size,
            "items": items,
        })
    except Exception as e:
        return error_response("KU_LIST_ERROR", str(e))


@router.get("/ku/{ku_id}")
async def ku_detail(ku_id: str):
    """KU detail: all fields + sources + edges with target text. ku_id 是 TEXT."""
    try:
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT k.ku_id, k.natural_text, k.natural_text_zh, k.knowledge_type, k.grade,
                       k.substrate_id, s.title AS substrate_title,
                       k.merge_count, k.sources, k.created_at
                FROM aii.ku_onto k
                LEFT JOIN aii.ingested_substrate s ON k.substrate_id = s.substrate_id
                WHERE k.ku_id = $1
                """,
                ku_id,
            )
            if not row:
                return error_response("NOT_FOUND", f"KU {ku_id} not found")

            edge_rows = await conn.fetch(
                """
                SELECT e.src_id, e.dst_id, e.relation_type,
                       e.grade, e.extraction_method,
                       CASE WHEN e.src_id = $1 THEN e.dst_id ELSE e.src_id END AS target_ku_id,
                       kt.natural_text AS target_text
                FROM aii.edge_onto e
                LEFT JOIN aii.ku_onto kt ON kt.ku_id =
                     CASE WHEN e.src_id = $1 THEN e.dst_id ELSE e.src_id END
                WHERE e.src_id = $1 OR e.dst_id = $1
                LIMIT 100
                """,
                ku_id,
            )

        raw_sources = _jsonb(row["sources"]) or []
        sources = [
            {
                "substrate_id": s.get("substrate_id", ""),
                "substrate_title": s.get("substrate_id", ""),
                "text": s.get("natural_text", ""),
            }
            for s in (raw_sources if isinstance(raw_sources, list) else [])
        ]

        edges = [
            {
                "target_id": _str(r["target_ku_id"]),
                "target_text": (r["target_text"] or "")[:120],
                "relation_type": r["relation_type"],
                "extraction_method": r["extraction_method"] or "llm",
                "grade": r["grade"],
            }
            for r in edge_rows
        ]

        return success_response({
            "id": _str(row["ku_id"]),
            "natural_text": row["natural_text"],
            "natural_text_zh": row["natural_text_zh"],
            "knowledge_type": row["knowledge_type"],
            "grade": row["grade"],
            "substrate_id": row["substrate_id"],
            "substrate_title": row["substrate_title"],
            "merge_count": row["merge_count"],
            "defeater_count": 0,
            "sources": sources,
            "defeaters": [],
            "edges": edges,
        })
    except Exception as e:
        return error_response("KU_DETAIL_ERROR", str(e))


# ── Graph ────────────────────────────────────────────────────────────────────

@router.get("/graph/subgraph")
async def graph_subgraph(
    ku_id: str = Query(...),
    hops: int = Query(2, ge=1, le=4),
    limit: int = Query(50, ge=1, le=200),
):
    """BFS subgraph on edge_onto (ku_id TEXT)."""
    try:
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            visited: set[str] = {ku_id}
            frontier: list[str] = [ku_id]
            all_edges: list[dict] = []
            seen_edge_keys: set[tuple] = set()

            for _ in range(hops):
                if not frontier or len(visited) >= limit:
                    break
                edge_rows = await conn.fetch(
                    """
                    SELECT src_id, dst_id, relation_type, grade, extraction_method
                    FROM aii.edge_onto
                    WHERE src_id = ANY($1::text[]) OR dst_id = ANY($1::text[])
                    """,
                    frontier,
                )
                new_frontier: list[str] = []
                for r in edge_rows:
                    s, d = r["src_id"], r["dst_id"]
                    key = (s, d, r["relation_type"])
                    if key not in seen_edge_keys:
                        seen_edge_keys.add(key)
                        all_edges.append({
                            "id": f"{s[:8]}-{d[:8]}-{r['relation_type']}",
                            "source": s,
                            "target": d,
                            "relation_type": r["relation_type"],
                            "grade": r["grade"],
                            "extraction_method": r["extraction_method"] or "llm",
                        })
                    for nid in (s, d):
                        if nid not in visited and len(visited) < limit:
                            visited.add(nid)
                            new_frontier.append(nid)
                frontier = new_frontier

            degree: dict[str, int] = {}
            for e in all_edges:
                degree[e["source"]] = degree.get(e["source"], 0) + 1
                degree[e["target"]] = degree.get(e["target"], 0) + 1

            node_rows = await conn.fetch(
                """
                SELECT ku_id, left(natural_text, 120) AS label, grade, knowledge_type
                FROM aii.ku_onto WHERE ku_id = ANY($1::text[])
                """,
                list(visited),
            )

        nodes = [
            {
                "id": r["ku_id"],
                "label": r["label"],
                "grade": r["grade"],
                "knowledge_type": r["knowledge_type"],
                "degree": degree.get(r["ku_id"], 0),
            }
            for r in node_rows
        ]
        return success_response({
            "nodes": nodes,
            "edges": all_edges,
            "center_id": ku_id,
            "truncated": len(visited) >= limit,
        })
    except Exception as e:
        return error_response("SUBGRAPH_ERROR", str(e))


@router.get("/graph/search")
async def graph_search(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=50)):
    """Vector search over ku_onto embeddings. Returns {matches:[{id,label,grade}]}."""
    try:
        qv = vector_encode(texts=[q], provider="default")[0]
        vec = [float(x) for x in qv]
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            from pgvector.asyncpg import register_vector
            await register_vector(conn)
            rows = await conn.fetch(
                """
                SELECT ku_id, left(natural_text, 120) AS label, grade
                FROM aii.ku_onto WHERE embedding IS NOT NULL
                ORDER BY embedding <=> $1 LIMIT $2
                """,
                vec, limit,
            )
        matches = [{"id": r["ku_id"], "label": r["label"], "grade": r["grade"]} for r in rows]
        return success_response({"matches": matches})
    except Exception as e:
        return error_response("SEARCH_ERROR", str(e))


# ── KC (Knowledge Cluster / kc_onto) ─────────────────────────────────────────

@router.get("/kc/list")
async def kc_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    view: str = Query("chapter"),  # ★双视图: chapter(按章) | spectral(谱社区)
    substrate: str = Query("microecon_en_full_v2"),  # ★按书过滤
):
    try:
        offset = (page - 1) * page_size
        marker = "AII谱社区KC" if view == "spectral" else "AII章节KC"
        order = "level NULLS LAST, kc_id" if view == "chapter" else "community_size DESC, kc_id"
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT count(*) FROM aii.kc_onto WHERE synthesis_marker=$1 AND substrate_id=$2",
                marker, substrate)
            rows = await conn.fetch(
                f"""
                SELECT kc_id, community_label, left(summary, 300) AS summary, grade,
                       jsonb_array_length(COALESCE(member_ku_ids, '[]'::jsonb)) AS community_size
                FROM aii.kc_onto WHERE synthesis_marker=$3 AND substrate_id=$4
                ORDER BY {order}
                LIMIT $1 OFFSET $2
                """,
                page_size, offset, marker, substrate,
            )

        items = [
            {
                "id": _str(r["kc_id"]),
                "community_label": r["community_label"] or "",
                "summary": r["summary"] or "",
                "grade": r["grade"],
                "community_size": r["community_size"] or 0,
            }
            for r in rows
        ]
        return success_response({"total": total, "page": page, "page_size": page_size, "items": items})
    except Exception as e:
        return error_response("KC_LIST_ERROR", str(e))


@router.get("/kc/{kc_id}")
async def kc_detail(kc_id: str):
    try:
        try:
            kc_id_int = int(kc_id)
        except (TypeError, ValueError):
            return error_response("BAD_REQUEST", f"invalid kc_id: {kc_id}")
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT kc_id, community_label, summary, summary_en, grade, member_ku_ids, synthesis_marker "
                "FROM aii.kc_onto WHERE kc_id = $1",
                kc_id_int,
            )
            if not row:
                return error_response("NOT_FOUND", f"KC {kc_id} not found")

            source_ids = _jsonb(row["member_ku_ids"]) or []
            member_rows = []
            if source_ids:
                # ★成员带来源书(为跨书谱社区准备: 以后别的书同主题KU加入会标各自来源)
                member_rows = await conn.fetch(
                    "SELECT k.ku_id, k.title, k.natural_text_zh, k.natural_text, k.grade, "
                    "       k.substrate_id, s.title AS source_book "
                    "FROM aii.ku_onto k LEFT JOIN aii.ingested_substrate s ON k.substrate_id = s.substrate_id "
                    "WHERE k.ku_id = ANY($1::text[])",
                    [str(s) for s in source_ids[:200]],
                )

        # 区分两种 KC: 按章(书内固定) vs 谱社区(跨书增长)
        kind = "spectral" if row["synthesis_marker"] == "AII谱社区KC" else "chapter"
        members = [
            {
                "id": r["ku_id"],
                "title": r["title"],
                "natural_text": (r["natural_text_zh"] or r["natural_text"] or "")[:140],
                "natural_text_zh": r["natural_text_zh"] or "",
                "natural_text_en": r["natural_text"] or "",
                "grade": r["grade"],
                "source_book": r["source_book"] or r["substrate_id"],
            }
            for r in member_rows
        ]
        return success_response({
            "id": _str(row["kc_id"]),
            "community_label": row["community_label"] or "",
            "summary": row["summary"] or "",
            "summary_en": row["summary_en"] or "",
            "grade": row["grade"],
            "kind": kind,
            "community_size": len(source_ids),
            "source_ku_ids": source_ids,
            "members": members,
        })
    except Exception as e:
        return error_response("KC_DETAIL_ERROR", str(e))


# ── BU (Book Understanding / bu_onto) ─────────────────────────────────────────

@router.get("/books")
async def books_list():
    """有 BU 的书清单(供前端选书)。"""
    try:
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT s.substrate_id, s.title, s.subject,
                       (SELECT count(*) FROM aii.ku_onto k WHERE k.substrate_id=s.substrate_id) AS ku_count
                FROM aii.ingested_substrate s
                WHERE EXISTS (SELECT 1 FROM aii.bu_onto b WHERE b.substrate_id=s.substrate_id)
                ORDER BY ku_count DESC
                """)
        return success_response({"items": [
            {"substrate_id": r["substrate_id"], "title": r["title"] or r["substrate_id"],
             "subject": r["subject"], "ku_count": r["ku_count"]} for r in rows]})
    except Exception as e:
        return error_response("BOOKS_ERROR", str(e))


@router.get("/book/{substrate_id}/bu")
async def book_bu(substrate_id: str):
    """书级理解 BU 七项(双语)+ 该书 KC/KU 计数。书的入口: 看 BU 决定要不要深入读。"""
    try:
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT facets_zh, facets_en, grade, synthesis_marker FROM aii.bu_onto WHERE substrate_id=$1",
                substrate_id)
            nku = await conn.fetchval("SELECT count(*) FROM aii.ku_onto WHERE substrate_id=$1", substrate_id)
            nkc_ch = await conn.fetchval(
                "SELECT count(*) FROM aii.kc_onto WHERE substrate_id=$1 AND synthesis_marker='AII章节KC'", substrate_id)
            nkc_sp = await conn.fetchval(
                "SELECT count(*) FROM aii.kc_onto WHERE substrate_id=$1 AND synthesis_marker='AII谱社区KC'", substrate_id)
        if not row or not row["facets_zh"]:
            return error_response("NOT_FOUND", f"BU for {substrate_id} not found")
        return success_response({
            "substrate_id": substrate_id,
            "facets_zh": _jsonb(row["facets_zh"]),
            "facets_en": _jsonb(row["facets_en"]),
            "grade": row["grade"],
            "synthesis_marker": row["synthesis_marker"],
            "n_ku": nku, "n_kc_chapter": nkc_ch, "n_kc_spectral": nkc_sp,
        })
    except Exception as e:
        return error_response("BU_ERROR", str(e))


@router.get("/bu/list")
async def bu_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    try:
        offset = (page - 1) * page_size
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT count(*) FROM aii.bu_onto")
            rows = await conn.fetch(
                """
                SELECT b.bu_id, b.substrate_id, b.doc_type, b.grade,
                       left(b.overview_oneline, 300) AS summary,
                       s.title AS book_title, s.subject AS subject,
                       jsonb_array_length(COALESCE(b.main_claims, '[]'::jsonb)) AS claim_count
                FROM aii.bu_onto b
                LEFT JOIN aii.ingested_substrate s ON s.substrate_id = b.substrate_id
                ORDER BY b.created_at DESC
                LIMIT $1 OFFSET $2
                """,
                page_size, offset,
            )

        items = [
            {
                "id": _str(r["bu_id"]),
                "substrate_id": r["substrate_id"] or "",
                "book_title": r["book_title"] or "",
                "summary": r["summary"] or "",
                "grade": r["grade"],
                "subject": r["subject"],
                "main_claim_count": r["claim_count"] or 0,
            }
            for r in rows
        ]
        return success_response({"total": total, "page": page, "page_size": page_size, "items": items})
    except Exception as e:
        return error_response("BU_LIST_ERROR", str(e))


@router.get("/bu/{bu_id}")
async def bu_detail(bu_id: str):
    try:
        try:
            bu_id_int = int(bu_id)
        except (TypeError, ValueError):
            return error_response("BAD_REQUEST", f"invalid bu_id: {bu_id}")
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT b.*, s.title AS book_title
                FROM aii.bu_onto b
                LEFT JOIN aii.ingested_substrate s ON s.substrate_id = b.substrate_id
                WHERE b.bu_id = $1
                """,
                bu_id_int,
            )
        if not row:
            return error_response("NOT_FOUND", f"BU {bu_id} not found")

        raw_claims = _jsonb(row["main_claims"]) or []
        main_claims = [
            {
                "id": f"claim-{i}",
                "text": c.get("claim", "") if isinstance(c, dict) else str(c),
                "stance": c.get("stance", "作者观点") if isinstance(c, dict) else "作者观点",
                "stance_marker": c.get("stance_marker", "") if isinstance(c, dict) else "",
                "claim_grade": c.get("claim_grade", "unverified") if isinstance(c, dict) else "unverified",
            }
            for i, c in enumerate(raw_claims)
        ]

        raw_args = _jsonb(row["argument_structure"]) or []
        argument_structure = [
            {
                "id": f"arg-{i}",
                "thesis": a.get("point", "") if isinstance(a, dict) else str(a),
                "thesis_grade": "unverified",
                "boundary": a.get("boundary", "") if isinstance(a, dict) else "",
                "evidence": [
                    {"text": e.get("text", ""), "grade": e.get("grade", "unverified")}
                    for e in (a.get("evidence") or [])
                ] if isinstance(a, dict) else [],
            }
            for i, a in enumerate(raw_args)
        ]

        key_ku_ids = _jsonb(row["key_concept_ku_ids"]) or []
        key_concepts = []
        if key_ku_ids:
            pool2 = await backend._ensure_pool()
            async with pool2.acquire() as conn2:
                kc_rows = await conn2.fetch(
                    "SELECT ku_id, left(natural_text,60) AS label, grade "
                    "FROM aii.ku_onto WHERE ku_id = ANY($1::text[])",
                    [str(k) for k in key_ku_ids[:10]],
                )
                key_concepts = [
                    {"ku_id": r["ku_id"], "label": r["label"], "grade": r["grade"]}
                    for r in kc_rows
                ]

        structure_raw = _jsonb(row["structure"]) or []
        if isinstance(structure_raw, str):
            structure_raw = [{"title": "全书结构", "summary": structure_raw, "children": []}] if structure_raw else []

        return success_response({
            "id": _str(row["bu_id"]),
            "substrate_id": row["substrate_id"] or "",
            "book_title": row["book_title"] or "",
            "summary": row["overview_oneline"] or "",
            "grade": row["grade"],
            "main_claim_count": len(main_claims),
            "source_credibility": row["source_credibility"] or "",
            "problem_statement": row["problem_statement"] or "",
            "overview_oneline": row["overview_oneline"] or "",
            "learning_thread": row["learning_thread"] or "",
            "knowledge_categories": {},
            "applicability": _jsonb(row["applicability"]) or "",
            "core_takeaways": _jsonb(row["core_takeaways"]) or [],
            "main_claims": main_claims,
            "argument_structure": argument_structure,
            "structure": structure_raw,
            "key_concepts": key_concepts,
        })
    except Exception as e:
        return error_response("BU_DETAIL_ERROR", str(e))


# ── Concept (concept_onto; ku_count 实时算) ───────────────────────────────────

@router.get("/concepts")
async def concept_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    min_ku_count: int = Query(1, ge=0),
    q: Optional[str] = Query(None),
):
    """List concepts sorted by ku_count DESC (ku_count 由 ku_concept_onto 实时聚合)."""
    try:
        offset = (page - 1) * page_size
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            conditions = ["cnt.ku_count >= $1"]
            params: list = [min_ku_count]
            if q:
                params.append(f"%{q.lower()}%")
                conditions.append(f"c.name LIKE ${len(params)}")
            where = " AND ".join(conditions)
            base = f"""
                FROM aii.concept_onto c
                JOIN LATERAL (SELECT count(*) AS ku_count FROM aii.ku_concept_onto kc
                              WHERE kc.concept_id = c.concept_id) cnt ON TRUE
                WHERE {where}
            """
            total = await conn.fetchval(f"SELECT count(*) {base}", *params)
            params += [page_size, offset]
            rows = await conn.fetch(
                f"""SELECT c.concept_id, c.name, cnt.ku_count {base}
                    ORDER BY cnt.ku_count DESC, c.name
                    LIMIT ${len(params)-1} OFFSET ${len(params)}""",
                *params,
            )
        items = [
            {"id": _str(r["concept_id"]), "name": r["name"], "ku_count": r["ku_count"]}
            for r in rows
        ]
        return success_response({
            "total": total, "page": page, "page_size": page_size, "items": items,
        })
    except Exception as e:
        return error_response("CONCEPT_LIST_ERROR", str(e))


@router.get("/concepts/{name}/kus")
async def concept_kus(
    name: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """KUs that involve this concept (by exact name, case-insensitive)."""
    try:
        offset = (page - 1) * page_size
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            c_row = await conn.fetchrow(
                "SELECT concept_id FROM aii.concept_onto WHERE name = $1",
                name.strip().lower(),
            )
            if not c_row:
                return error_response("NOT_FOUND", f"Concept '{name}' not found")

            total = await conn.fetchval(
                "SELECT count(*) FROM aii.ku_concept_onto WHERE concept_id = $1", c_row["concept_id"]
            )
            rows = await conn.fetch(
                """
                SELECT k.ku_id, left(k.natural_text, 200) AS natural_text,
                       k.knowledge_type, k.grade, k.substrate_id
                FROM aii.ku_concept_onto kc
                JOIN aii.ku_onto k ON k.ku_id = kc.ku_id
                WHERE kc.concept_id = $1
                ORDER BY k.created_at DESC
                LIMIT $2 OFFSET $3
                """,
                c_row["concept_id"], page_size, offset,
            )
        items = [
            {
                "id": r["ku_id"],
                "natural_text": r["natural_text"],
                "knowledge_type": r["knowledge_type"],
                "grade": r["grade"],
                "substrate_id": r["substrate_id"],
            }
            for r in rows
        ]
        return success_response({
            "concept": name.strip().lower(),
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
        })
    except Exception as e:
        return error_response("CONCEPT_KUS_ERROR", str(e))


# ── Stratum 反向共享端点 (只读) ─────────────────────────────────────────────────

@router.get("/graph/edges")
async def graph_edges(
    relation_type: Optional[str] = Query(None),
    grade: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=2000),
):
    """Paginated full edge export (edge_onto) for Stratum reverse-sharing."""
    try:
        offset = (page - 1) * page_size
        conditions = ["relation_type IS NOT NULL", "relation_type != ''"]
        params: list = []

        if relation_type:
            params.append(relation_type)
            conditions.append(f"relation_type = ${len(params)}")
        if grade:
            params.append(grade)
            conditions.append(f"grade = ${len(params)}")

        where = " AND ".join(conditions)
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT count(*) FROM aii.edge_onto WHERE {where}", *params
            )
            params += [page_size, offset]
            rows = await conn.fetch(
                f"""
                SELECT src_id, dst_id, relation_type, grade, extraction_method
                FROM aii.edge_onto
                WHERE {where}
                ORDER BY src_id, relation_type
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
                """,
                *params,
            )
        items = [
            {
                "src_id": r["src_id"],
                "dst_id": r["dst_id"],
                "relation_type": r["relation_type"],
                "grade": r["grade"],
                "extraction_method": r["extraction_method"] or "llm",
            }
            for r in rows
        ]
        return success_response({
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
        })
    except Exception as e:
        return error_response("GRAPH_EDGES_ERROR", str(e))


@router.post("/ku/batch")
async def ku_batch(
    ids: list[str] = Body(..., embed=True, max_length=200),
):
    """Batch KU lookup by ID list (≤200, ku_id TEXT). Returns grade/type/concepts per KU."""
    try:
        if not ids:
            return success_response({"items": []})
        if len(ids) > 200:
            return error_response("TOO_MANY_IDS", "ids list exceeds 200 limit")

        id_list = [str(x) for x in ids]
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            ku_rows = await conn.fetch(
                """
                SELECT ku_id, left(natural_text, 200) AS natural_text,
                       grade, knowledge_type, substrate_id
                FROM aii.ku_onto
                WHERE ku_id = ANY($1::text[])
                """,
                id_list,
            )
            if ku_rows:
                ku_ids = [r["ku_id"] for r in ku_rows]
                concept_rows = await conn.fetch(
                    """
                    SELECT kc.ku_id, c.name
                    FROM aii.ku_concept_onto kc
                    JOIN aii.concept_onto c USING (concept_id)
                    WHERE kc.ku_id = ANY($1::text[])
                    ORDER BY kc.ku_id, c.name
                    """,
                    ku_ids,
                )
            else:
                concept_rows = []

        ku_concepts: dict[str, list[str]] = {}
        for cr in concept_rows:
            ku_concepts.setdefault(cr["ku_id"], []).append(cr["name"])

        items = [
            {
                "id": r["ku_id"],
                "natural_text": r["natural_text"],
                "grade": r["grade"],
                "knowledge_type": r["knowledge_type"],
                "is_synthesis": False,
                "substrate_id": r["substrate_id"],
                "concepts": ku_concepts.get(r["ku_id"], []),
            }
            for r in ku_rows
        ]
        return success_response({"items": items})
    except Exception as e:
        return error_response("KU_BATCH_ERROR", str(e))


# ── KU 溯源 ────────────────────────────────────────────────────────────────

@router.get("/ku/{ku_id}/trace")
async def ku_trace(ku_id: str):
    """KU source provenance: which substrate(s) this KU was extracted from (ku_id TEXT)."""
    try:
        pool = await backend._ensure_pool()
        async with pool.acquire() as conn:
            ku_row = await conn.fetchrow(
                "SELECT ku_id, natural_text, substrate_id, sources FROM aii.ku_onto WHERE ku_id = $1",
                ku_id,
            )
            if not ku_row:
                return error_response("NOT_FOUND", f"KU {ku_id} not found")

            raw_sources = _jsonb(ku_row["sources"]) or []
            source_ids: list[str] = [s["substrate_id"] for s in raw_sources if isinstance(s, dict) and s.get("substrate_id")]
            if not source_ids and ku_row["substrate_id"]:
                source_ids = [str(ku_row["substrate_id"])]

            substrate_rows = await conn.fetch(
                "SELECT substrate_id, title, medium FROM aii.ingested_substrate WHERE substrate_id = ANY($1)",
                source_ids,
            ) if source_ids else []

        substrate_map = {str(r["substrate_id"]): {"title": r["title"], "medium": r["medium"]} for r in substrate_rows}
        positions = [
            {
                "source_id": sid,
                "chunk_idx": i,
                "substrate_title": substrate_map.get(sid, {}).get("title"),
                "medium": substrate_map.get(sid, {}).get("medium"),
            }
            for i, sid in enumerate(source_ids)
        ]
        return success_response({
            "ku_id": ku_id,
            "source_ids": source_ids,
            "trace_depth": len(source_ids),
            "positions": positions,
        })
    except Exception as e:
        return error_response("KU_TRACE_ERROR", str(e))

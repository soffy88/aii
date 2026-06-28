"""Chunk-level concurrent ontology extraction wrapper (for flywheel-scale speedup).

★ Does NOT modify the shared oskill element. Re-implements the SAME two-pass orchestration
  (split → pass1-per-chunk → outline → pass2-per-chunk → validate/id-sync) but runs the
  per-chunk pass1 and pass2 calls with asyncio.gather under a Semaphore instead of serial
  for+await. Per-chunk extraction logic, prompts, max_tokens, and structural validation are
  byte-for-byte the same as oskill.ontology_extract — concurrency only parallelizes WHEN calls
  run, never WHAT each call does. Returns the same OntologyExtractResult type persist consumes.

Why a wrapper (not editing oskill): oskill is a shared platform element; AII owns this speedup.
Concurrency cap (Semaphore) keeps in-flight calls within DeepSeek's comfortable range.

Concurrency-safe ids: each chunk's KUs get ku_c{chunk_idx}_{i} (i = index within that chunk).
chunk_idx is unique per chunk, so ids are unique and parseable regardless of completion order
(serial oskill uses a global running counter; both yield the same KU set + the ku_cN_ format that
cross_chunk_link depends on).
"""
from __future__ import annotations

import asyncio
import json
import re

from dataclasses import dataclass
from aii.service.onto_vocab import (
    VALID_RELATION_TYPES, VALID_KNOWLEDGE_TYPES, VALID_SUB_TYPES)


# OntologyExtractResult was dropped from oprim (main-lib version drift); define the
# same shape locally so this extractor stays self-contained. Fields match what
# persist_ontology_result consumes.
@dataclass
class OntologyExtractResult:
    outline: dict
    ku_candidates: list
    edge_candidates: list
    concept_candidates: list
    stats: dict


def _split_chunks(text: str, chunk_size: int) -> list[str]:
    """Identical to oskill._split_chunks (replicated, not imported, to avoid private coupling)."""
    if len(text) <= chunk_size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            b = text.rfind("。", start, end)
            if b == -1:
                b = text.rfind(". ", start, end)
            if b != -1 and b > start:
                end = b + 1
        chunks.append(text[start:end].strip())
        start = end
    return [c for c in chunks if c]


def _parse_json(resp: dict) -> dict | None:
    text = ""
    for block in resp.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            text = block["text"].strip()
            break
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _process_chunk_kus(chunk_idx: int, data: dict, vkt, vst, vrt):
    """Replicate oskill pass2 per-chunk validation/id-sync. Returns (kus, edges, concepts).
    ids local to this chunk → ku_c{chunk_idx}_{i} (concurrency-safe; oskill uses global counter)."""
    kus, edges, concepts = [], [], []
    chunk_id_map: dict[str, str] = {}
    for ku in data.get("ku_candidates", []):
        if not isinstance(ku, dict):
            continue
        ku["grade"] = "unverified"
        if ku.get("knowledge_type") not in vkt:
            ku["knowledge_type"] = "factual"
        if ku.get("knowledge_type") == "positional" and not ku.get("stance_holder"):
            continue
        raw_sub = ku.get("sub_type")
        if raw_sub and raw_sub not in vst:
            ku["sub_type"] = None
        temp_id = ku.get("id", "")
        new_id = f"ku_c{chunk_idx}_{len(kus)}"
        chunk_id_map[temp_id] = new_id
        ku["id"] = new_id
        kus.append(ku)
    for edge in data.get("edge_candidates", []):
        if not isinstance(edge, dict):
            continue
        if edge.get("relation_type") not in vrt:
            continue
        edge["source"] = chunk_id_map.get(edge.get("source", ""), edge.get("source", ""))
        edge["target"] = chunk_id_map.get(edge.get("target", ""), edge.get("target", ""))
        edges.append(edge)
    for c in data.get("concept_candidates", []):
        if isinstance(c, str):
            concepts.append(c)
    return kus, edges, concepts


async def ontology_extract_concurrent(
    *, source_text: str, llm,
    pass1_chunk_tmpl: str, pass1_chunk_system: str,
    pass1_outline_tmpl: str, pass1_outline_system: str,
    pass2_chunk_tmpl: str, pass2_system: str,
    chunk_size: int = 2000, doc_type: str = "textbook", source_credibility: str = "medium",
    valid_knowledge_types=None, valid_sub_types=None, valid_relation_types=None,
    chunk_concurrency: int = 10,
) -> OntologyExtractResult:
    vkt = valid_knowledge_types or VALID_KNOWLEDGE_TYPES
    vst = valid_sub_types or VALID_SUB_TYPES
    vrt = valid_relation_types or VALID_RELATION_TYPES

    if not source_text.strip():
        return OntologyExtractResult(outline={}, ku_candidates=[], edge_candidates=[],
                                     concept_candidates=[], stats={"total": 0, "by_type": {}, "explains_count": 0})

    chunks = _split_chunks(source_text, chunk_size)
    sem = asyncio.Semaphore(chunk_concurrency)

    # Pass 1 — per-chunk analysis, CONCURRENT (order preserved by gather)
    async def p1(chunk):
        async with sem:
            resp = await llm(messages=[{"role": "user", "content": pass1_chunk_tmpl.format(chunk_text=chunk)}],
                             system=pass1_chunk_system, max_tokens=512)
            return _parse_json(resp) or {"concepts": [], "topics": [], "chapter": ""}

    chunk_analyses = await asyncio.gather(*(p1(c) for c in chunks))

    # Outline — single call (same as serial)
    outline_resp = await llm(
        messages=[{"role": "user", "content": pass1_outline_tmpl.format(
            doc_type=doc_type, source_credibility=source_credibility,
            chunk_analyses=json.dumps(list(chunk_analyses), ensure_ascii=False, indent=2))}],
        system=pass1_outline_system, max_tokens=1024)
    outline = _parse_json(outline_resp) or {
        "chapters": [], "core_concepts": [], "main_thread": "",
        "stance": "", "doc_type": doc_type, "source_credibility": source_credibility}
    outline_str = json.dumps(outline, ensure_ascii=False, indent=2)

    # Pass 2 — per-chunk KU extraction, CONCURRENT, each chunk independent
    async def p2(chunk_idx, chunk):
        async with sem:
            resp = await llm(messages=[{"role": "user", "content": pass2_chunk_tmpl.format(
                outline=outline_str, chunk_text=chunk)}], system=pass2_system, max_tokens=2048)
            return _process_chunk_kus(chunk_idx, _parse_json(resp) or {}, vkt, vst, vrt)

    per_chunk = await asyncio.gather(*(p2(i, c) for i, c in enumerate(chunks)))

    all_ku, all_edge, all_concept = [], [], []
    for kus, edges, concepts in per_chunk:
        all_ku.extend(kus)
        all_edge.extend(edges)
        for c in concepts:
            if c not in all_concept:
                all_concept.append(c)

    by_type: dict[str, int] = {}
    for ku in all_ku:
        by_type[ku.get("knowledge_type", "unknown")] = by_type.get(ku.get("knowledge_type", "unknown"), 0) + 1
    stats = {"total": len(all_ku), "by_type": by_type,
             "explains_count": sum(1 for e in all_edge if e.get("relation_type") == "explains")}
    return OntologyExtractResult(outline=outline, ku_candidates=all_ku, edge_candidates=all_edge,
                                 concept_candidates=all_concept, stats=stats)

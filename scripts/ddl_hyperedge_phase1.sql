-- ★Phase 1 — explains 有向超边 数据模型(对齐 AII-HYPEREDGE-EXPLAINS-001 §3.2)
-- 纯 DDL,只新增 2 表,现有表(ku_onto/concept_onto/ku_concept_onto/edge_onto/directed_edge_v2)一律不动。
-- 二部结构对齐 HyperGraphRAG: hyperedge(超边节点,带 NL 描述+向量) + hyperedge_member(超边↔概念关联)。

-- ── 1. 有向超边 = 一条 explains 事实(head: rationale → {被解释概念集}) ──
CREATE TABLE IF NOT EXISTS aii.hyperedge (
  hyperedge_id      bigserial PRIMARY KEY,
  substrate_id      text NOT NULL,
  relation_type     text NOT NULL DEFAULT 'explains',      -- 受控,现仅 explains(预留未来其它 n元有向关系)
  head_ku_id        text NOT NULL REFERENCES aii.ku_onto(ku_id) ON DELETE CASCADE,  -- 解释者: rationale KU
  nl_description    text NOT NULL,                         -- 机制 NL 描述(HyperGraphRAG 风格,向量检索用)
  embedding         vector(1024),                          -- nl_description 向量(BGE-M3,对齐 ku_onto;后续 hyperedge_vdb)
  grade             text NOT NULL DEFAULT 'unverified',    -- grade 铁律: LLM 一律 unverified
  extraction_method text DEFAULT 'llm',
  evidence          jsonb,                                 -- 来源/原文片段
  created_at        timestamptz DEFAULT now(),
  updated_at        timestamptz DEFAULT now()
);

-- ── 2. 超边成员 = 被解释概念集(二部 incidence;★动态生长就改这张表) ──
CREATE TABLE IF NOT EXISTS aii.hyperedge_member (
  hyperedge_id  bigint  NOT NULL REFERENCES aii.hyperedge(hyperedge_id) ON DELETE CASCADE,
  concept_id    bigint  NOT NULL REFERENCES aii.concept_onto(concept_id) ON DELETE CASCADE,  -- 被解释概念=实体
  status        text    NOT NULL DEFAULT 'confirmed'
                   CHECK (status IN ('confirmed','candidate')),   -- ★宁缺毋附会: confirmed进主网/candidate进候选池
  evidence      jsonb,                                            -- ★这条成员的原文依据(加成员必须有,不附会)
  cross_disc    boolean DEFAULT false,                            -- ★跨学科扩入标记(跨学科尤其严,先 candidate)
  source_ku_id  text,                                             -- 哪条 KU surface 了这条成员(溯源)
  added_at      timestamptz DEFAULT now(),
  PRIMARY KEY (hyperedge_id, concept_id)
);

-- ── 3. 索引 ──
CREATE INDEX IF NOT EXISTS idx_hyperedge_head   ON aii.hyperedge(head_ku_id);
CREATE INDEX IF NOT EXISTS idx_hyperedge_rel    ON aii.hyperedge(relation_type);
CREATE INDEX IF NOT EXISTS idx_hyperedge_sub    ON aii.hyperedge(substrate_id);
CREATE INDEX IF NOT EXISTS idx_hemember_concept ON aii.hyperedge_member(concept_id);
CREATE INDEX IF NOT EXISTS idx_hemember_status  ON aii.hyperedge_member(status);

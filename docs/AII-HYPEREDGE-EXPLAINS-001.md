# AII explains 超边设计(对齐 HyperGraphRAG,n元/动态生长)

> **Doc ID:** AII-HYPEREDGE-EXPLAINS-001
> **依据:** HyperGraphRAG(arXiv 2503.21322, NeurIPS 2025)+ AII-KNOWLEDGE-ONTOLOGY-002(rationale=深度)+ AII-DATA-MODEL-001(edge/concept)+ 现有有向关系实证(directed_edge_v2 / edge_onto / ku_concept_onto)。
> **决策(已定):** explains 用**超边(n元、动态生长)**表示——rationale → {它联合解释的概念集合},**不拆成多条二元边**。理由:这是知识本来的样子(一个机制常联合解释多个概念),也是有机体生长的载体(机制不变,被解释概念集随新书摄入而扩)。
> **状态:** 设计供审,**先不改代码**。Wiki/经理人审过 + 全量前定对,再动手。

---

## 一、HyperGraphRAG 机制总结(深读 repo 实证)

> repo: github.com/LHRLAB/HyperGraphRAG。读了 prompt.py / operate.py / storage.py。

### 1.1 数据模型(二部图 + 双向量库)
- **超边(hyperedge)= 一条 NL 知识段**:`("hyper-relation", <knowledge_segment 文本>, completeness_score 0-10)`。超边节点名 = `"<hyperedge>"+知识段文本`。**超边就是"一条事实"**,自带自然语言描述。
- **实体(entity)= 段内成员**:`(name, type, description, key_score 0-100)`。实体是 canonical 节点,跨知识段**合并**。
- **二部图存储(NetworkXStorage)**:实体和超边**都是节点**;边 = "超边↔它的成员实体"的关联(incidence)。`role="hyperedge"` 标超边节点。
- **双向量库(NanoVectorDB)**:`hyperedge_vdb`(超边描述向量)+ `entity_vdb`(实体向量),同一嵌入空间。

### 1.2 抽取(LLM 端到端 n元)
1. LLM 把文本切成若干**完整知识段**(每段→一条超边 + completeness_score)。
2. 对每段抽出其中**所有实体**(name/type/description/key_score)。
3. 超边连接它段内抽出的全部实体 = 一条 n元事实。

### 1.3 动态生长(核心机制)
- `_merge_nodes_then_upsert`:实体复现时**合并**——entity_type 取众数、description 取并集(<SEP>连接后 LLM 摘要)、source_id 取并集。**实体节点随更多事实提及而累积、生长**。
- 新事实(超边)提及已有实体 → 二部图加一条关联边 → 该实体所在的超边邻域**自然扩大**。
- 即:**实体 canonical 不动,超边不断挂上来,n元关系网生长**。

### 1.4 检索(双路 + 双向扩展)
- 实体检索:问题抽实体 → `entity_vdb` 找相似实体(`sim ⊙ score`)。
- 超边检索:问题 → `hyperedge_vdb` 找相似超边。
- 双向扩展:实体→它所在超边;超边→它的实体。并集 = n元事实集 → 喂生成。

### 1.5 关键洞察
> HyperGraphRAG 的超图层**只有无向的"超边-实体"关联**,**没有超边之间的有向关系**。它证明:n元事实拆成二元边会**有损**(信息论:二元 `H(X|φ)>0`,超图 `=0`)。

---

## 二、AII 现状对照:**AII 本就是 HyperGraphRAG 式超图**

| HyperGraphRAG | AII 现有 | 说明 |
|---|---|---|
| 超边(n元事实)| **KU**(`ku_onto`)| 一条讲透的知识 |
| 实体 | **概念**(`concept_onto`)| |
| 二部图关联(超边↔实体)| **`ku_concept_onto`**(KU↔多概念)| ✅ 已是超图的 incidence |
| 实体 canonical 合并 | 概念语义归一(规划中)| |

AII 还**多了 HyperGraphRAG 没有的层**:
- `directed_edge_v2`(概念→概念:derives/subsumes/prerequisite)= 概念结构图。
- `edge_onto`(KU→KU 二元:explains/causes/special_case_of…)= 事实间有向关系(主本体管道在用)。

**结论:AII = HyperGraphRAG 超图(KU=超边/概念=实体/ku_concept=关联)+ 有向关系增强层。** explains 属于"有向关系",但 **explains 常常 n元**(一个机制联合解释多个概念)→ 用**有向超边**最忠于知识本相。

---

## 三、★AII explains 超边设计

### 3.1 本相:explains 是"有向 n元超边"
一个 rationale(机制)联合解释一组概念:

```
explains 超边 H = ( head: rationale, 机制NL描述, { 被解释概念集合 } )
   例: "可替代品的多寡决定需求对价格的敏感度"
       → explains → { 需求价格弹性, 供给价格弹性, 奢侈品vs必需品的弹性差异 }
```

- **head(解释者)** = rationale KU(机制本身)。
- **members(被解释者)** = 概念集合(n元,可 1 可多)。
- **NL 描述** = 机制的自然语言陈述(对齐 HyperGraphRAG,供向量检索)。

### 3.2 数据模型(新 2 表,二部结构,**不动现有表**)

```sql
-- 有向超边(= 一条 explains 事实;通用化预留 relation_type 供未来其它 n元有向关系)
aii.hyperedge (
  hyperedge_id    bigserial PK,
  substrate_id    text  NOT NULL,
  relation_type   text  NOT NULL DEFAULT 'explains',   -- 受控; 现仅 explains
  head_ku_id      text  NOT NULL,        -- 解释者: rationale KU(FK ku_onto)
  nl_description  text  NOT NULL,         -- 机制NL描述(向量检索用, HyperGraphRAG风格)
  embedding       vector,                 -- nl_description 的向量(进 hyperedge_vdb)
  grade           text  DEFAULT 'unverified',          -- grade铁律
  extraction_method text DEFAULT 'llm',
  evidence        jsonb,                  -- 来源/原文片段
  created_at / updated_at timestamptz
)

-- 超边成员(被解释概念集合; 二部图的 incidence; 动态生长就改这张表)
aii.hyperedge_member (
  hyperedge_id  bigint NOT NULL,          -- FK hyperedge
  concept_id    bigint NOT NULL,          -- FK concept_onto(被解释的概念=实体)
  status        text  NOT NULL DEFAULT 'confirmed',  -- ★confirmed(内核同机制+原文依据) | candidate(相似存疑,待确认)
  evidence      jsonb,                     -- ★原文依据(原文真说"这机制解释这概念"的出处)
  cross_disc    bool  DEFAULT false,       -- ★是否跨学科扩入(跨学科判据更严,先 candidate)
  source_ku_id  text,                     -- 哪条KU surface了这条成员(溯源)
  added_at      timestamptz DEFAULT now(),
  PRIMARY KEY (hyperedge_id, concept_id)
)
```
> `status` 是"宁缺毋附会"的落地:**确信同机制+原文依据 → confirmed(进网络);存疑 → candidate(进候选池,不污染主网,等确证)。** 跨学科扩入一律先 candidate(§3.4 ⑤)。

> 完全是 HyperGraphRAG 的二部结构:`hyperedge` = 超边节点(带 NL 描述 + 向量),`hyperedge_member` = 超边↔概念实体的关联。**新增表,现有 ku_onto/concept_onto/ku_concept_onto/edge_onto/directed_edge_v2 一律不动。**

### 3.3 n=1 与 n>1 统一(n元天然兼容 n=1)
- rationale 只解释一个概念 → `hyperedge_member` 一行(n=1)。
- rationale 联合解释多个 → 多行(n>1)。
- **同一结构,同一查询路径**。不需要"单概念走二元边、多概念走超边"的分叉——n元就是统一形态。

### 3.4 ★动态生长 = 超边的本性(不暂缓,Phase 2 核心)

**explains 超边的灵魂 = 机制不变、被解释概念集随知识摄入而生长。** 一条不会生长的超边 = 静态 n元边,失去有机体意义。所以动态生长**不是 Phase 3 增量,是超边的本性,Phase 2 必须一起做**。

新书/新章发现**同一机制**还解释别的概念 → 给已有超边**加成员**(机制 canonical,成员集生长),而非新建。

**★但"何时判同一机制"的生长判据,要像本性判据一样严**(本性不是表面相似算数,要内核结构同、经得起变换):

```
生长判据(给已有超边扩成员前, 必须全过):
 ① ★不是 nl_description 相似就合并 —— 表面相似 ≠ 同一机制
 ② 要"内核是同一个机制 / 同一推理结构" —— 不只表述像, 是机制本身同
    (同样的因果结构、同样的"之所以如此", 不是名字/措辞撞)
 ③ 加的成员必须有原文依据(source_ku_id + evidence: 原文真说"这机制解释这概念")
 ④ ★存疑的(相似但不确定同机制)→ 不自动合并, 标 status='candidate',
    进候选池等人工确认 / 更多证据 —— 宁缺毋附会
 ⑤ ★跨学科扩成员尤其严: 经济的"可替代性"和生物的"可替代性"真是同一机制吗?
    要内核真同(同一不变结构), 不是名字/表述像就连; 跨学科一律先 candidate
```

> 对齐 HyperGraphRAG 的 `_merge_nodes`(实体 canonical 合并)——但 AII 把"合并判据"提到**本性判据的严度**:**生长是本性(积极扩),不附会是命门(严判据)——靠严判据统一,不靠暂缓。** 真同机制就长,存疑的进 candidate 池等确证。

### 3.4b ★宁缺毋附会机制(对齐本性的"宁标未发现")
像本性"抽不到留空、绝不造假"一样,生长也"宁缺毋附会":

- **确信同一机制**(内核同 + 原文依据)→ status=confirmed,扩成员(生长)。
- **存疑**(相似但不确定同机制)→ status=candidate,**不自动并入主网**,等人工确认 / 积累更多证据再定。
- **★假联结(附会)比漏联结危险**:跨学科涌现"看起来惊艳"最易诱人附会——但必须真。漏一条联结只是暂时没连上(以后还能补);连一条假联结会污染整张本性网,且"看起来对"最难被发现。
- candidate 池是缓冲:积极发现候选,但**只有经得起严判据的才进 confirmed 主网**。

### 3.5 和现有三套的协调(不分裂)
| 结构 | 语义 | explains 超边的关系 |
|---|---|---|
| `ku_concept_onto`(隐式超边)| **无向**:KU 涉及哪些概念 | explains 超边是其**有向、带类型的精选**:rationale 涉及的概念里,哪些是"被它解释的" |
| `directed_edge_v2`(概念→概念)| 概念间**结构**(derives/subsumes/prerequisite)| 正交:那是概念结构图,explains 是"机制→被解释概念" |
| `edge_onto`(KU→KU 二元)| 事实间二元关系(causes/special_case_of/…)| explains **移出** edge_onto 的二元,改走超边;edge_onto 留其余二元关系。数学管道已写的 115 条 explains 二元边 → Phase 迁移(见 §四) |

> 一句话:**超边层(n元 explains)= ku_concept 的有向类型化升格;二元层(edge_onto)留非 n元的二元关系;概念结构层(directed_edge_v2)不动。三层各司其职,不重复不冲突。**

### 3.6 检索 / 前端(HyperGraphRAG 式)
- **检索**:问某概念的"为什么" → ① 在 `hyperedge_vdb`(nl_description 向量)检索相关机制超边;② 从该概念(实体)经 `hyperedge_member` 反查"哪些机制解释我";③ 双向扩展(机制→它解释的全部概念)。→ 给出"这个概念被哪些机制解释、每个机制还联合解释了什么"。
- **前端**:概念页展示"解释它的机制(超边)";机制页展示"它联合解释的概念集合"——深度(why)从藏在文本里变成**可导航的机制网**。

### 3.7 ★忠于原文(命门,延续约束1)
- **成员只标原文真表达的**:一个机制"解释哪些概念",只收**原文真的说它解释**的概念,**不附会**(不把机制硬扩到原文没关联的概念)。
- 动态生长加成员时,**新成员也要有原文依据**(source_ku_id + evidence),不靠"看起来相关"硬连。
- grade 一律 unverified(机制是否真成立、解释是否真对,留确证机制),LLM 不标可信度。

### 3.8 ★本性浮现路径(AII 独有,HyperGraphRAG 没有)
有向超边的动态生长,是 AII 通向"本性(invariant)"的路径——这是 HyperGraphRAG 不具备的延伸:

```
有向超边动态生长
  → 一条机制超边的成员跨多学科积累(经济弹性 / 生态位竞争 / 信息冗余…)
  → 当成员形式各异、内核却是同一个机制(同一不变结构)
  → 逼近一个【本性 invariant】(穿透各学科表象的不变内核)
```

- **这解了"本性为时尚早"的死结**:之前说本性"要跨学科数据才能提",而**超边生长正是积累"跨学科同机制"证据的载体**——每条机制超边跨学科扩一个 confirmed 成员,就多一份"这是同一不变结构"的证据。
- **与本性体系接通**(CONCEPT-STORAGE/NATURE-EXTRACT):一条机制超边的成员若跨 ≥2 学科且内核同(经严判据 confirmed)→ 该机制的 invariant 浮现 → 凝结 invariant_concept,各学科概念经"本性同源"相连。
- **★HyperGraphRAG 没有这个**:它的超边无向、静态、无本性追求(只为 RAG 检索)。AII 把超边做成**有向 + 动态生长 + 严判据**,才把它变成本性浮现的载体——这是 AII 区别于一切现成 RAG 的地方。
- **本性提取仍审慎**:够多跨学科同机制证据(confirmed,非 candidate)+ 严判据,才提 invariant;不够就留在超边层继续长。**路径由超边生长打通,门槛由严判据把守。**

---

## 四、工程量 + 分步路径

### 4.1 评估:**增量,非大改**
新增 2 表 + 1 向量库,**现有表/管道全不动**。抽取/质量门/前端是"加一条新路径",不是重写。

| 改动 | 范围 | 量 |
|---|---|---|
| 数据模型 | 新建 hyperedge + hyperedge_member(+ hyperedge_vdb)| 小(DDL)|
| 抽取 | `chapter_synthesize._plan`:explains 字段 单概念→**概念列表**;`synthesize_book.persist`:写 hyperedge+members(替掉我 v1.3 误写的 concept_readout_edge)| 中 |
| 质量门 | `econ_quality_gate`:explains 边数 → **explains 超边数 + 成员数 + 平均元数** | 小 |
| 动态生长 | merge 步(严判据:内核同机制→confirmed扩 / 存疑→candidate)| 中(**Phase 2 核心,不延后**)|
| 检索/前端 | HyperGraphRAG 式超边检索 + 概念页机制网 + candidate 确认界面 | 中(Phase 3)|

### 4.2 分步路径
1. **Phase 1 数据模型**:建 hyperedge + hyperedge_member(含 status/evidence/cross_disc + 向量列)。DDL by CC。
2. **Phase 2 抽取落超边 + ★动态生长(含严判据)**:
   - _plan 的 explains→**概念列表**;persist 写超边 + 成员(n元)。
   - ★动态生长(本性,不延后):跨章/跨书同机制 → 严判据(内核同 + 原文依据)→ confirmed 扩成员;存疑 → candidate。
   - 撤掉 v1.3 误写 concept_readout_edge 的 explains。
3. **Phase 3 检索 + 前端**:超边向量检索(HyperGraphRAG 式)+ 概念页机制网 + candidate 池人工确认界面。
4. **Phase 4 本性接通**:跨学科 confirmed 成员够多 → invariant 浮现 → 接 converge_invariants / invariant_concept(对齐 NATURE-EXTRACT)。
5. **迁移**:数学管道 edge_onto 里已有的 explains 二元边 → 转成超边(每条二元 = n=1 超边),统一到超边层。

> **全量前定对**:Phase 1+2 在 Mankiw/微观一本验证——① n元超边抽出、成员忠于原文(单本内);② ★动态生长跨章扩成员**判据严否**(真同机制才 confirmed,存疑 candidate);③ 抽查扩的成员:**原文真支持否(没附会假联结)**。三项过 → 再上飞轮全量。

---

## 五、命门
1. explains = **有向 n元超边**(rationale→{概念集合}),不拆二元——知识本相 + 生长载体。
2. **二部结构对齐 HyperGraphRAG**:hyperedge(带NL描述+向量)+ hyperedge_member(关联),新增不动现有。
3. **n元统一 n=1**:同结构同查询,不分叉。
4. **★动态生长是超边的本性(不暂缓,Phase 2 核心)**:机制 canonical,被解释概念集随摄入扩;不会长的超边失去有机体意义。
5. **★生长判据像本性判据一样严**:内核同机制才扩(非表面相似)、要原文依据、存疑标 candidate 不自动并入、跨学科尤其严——**生长积极(本性)+ 不附会(命门),靠严判据统一,不靠暂缓**。
6. **★宁缺毋附会**:假联结比漏联结危险(污染本性网且难发现);confirmed 才进主网,存疑进 candidate 池等确证。
7. **★本性浮现路径(AII 独有)**:超边跨学科生长 → 积累"跨学科同机制"证据 → 本性浮现;HyperGraphRAG 无此(它无向/静态/无本性追求)。
8. **三层协调**:超边层(n元 explains)/ 二元层(edge_onto 其余)/ 概念结构层(directed_edge_v2),不重复不分裂。
9. **忠于原文**:成员只标原文真表达的解释关系,不附会;grade unverified。
10. **增量路径**:Phase 1-2(含动态生长+严判据)先跑通验证,3-4 增量;先出本设计供审,**不急改代码**。

---

*依据:HyperGraphRAG(n元超边/二部图/双向量库/动态合并)+ AII 知识本体(rationale=深度,know-why 的在场)+ AII 现有超图实证(ku_onto/concept_onto/ku_concept_onto = 已是超图)。*

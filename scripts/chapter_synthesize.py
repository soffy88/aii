"""新范式章节合成(讲透 + 防漏): 按章切 → 规划知识点 → 每点讲透合成(双语+溯源) → 完整性校验防漏 → 补漏.
★规划喂全章(不截断), 合成改定向窗口+程序骨架混合:
  - 程序骨架(多策略): 格式定义框→is/means句式→首次出现句; 例子: EXAMPLE块+数字句+for example
  - 窗口按章节边界截断(治本防污染): 跳过近距离同主题小节, 在第一个≥4000chars的节边界或章末截断
  - LLM只看WHY窗口; 节省~49%/call
  - 无骨架: 标准定向窗口 intro[:1000]+section[pos-WIN_PRE:pos+WIN_POST]
★防漏: 用 planning_completeness(确定性)对照"应有黑体术语/小节"查漏, 漏的补抽.
Usage: chapter_synthesize.py <chapter_n>
"""
import asyncio, json, re, sys
from pathlib import Path
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "aii" / ".env", override=True)
sys.path.insert(0, str(ROOT / "scripts"))
from chapter_ingest import slice_chapter, SM
from aii.api._provider import register_providers
from aii.service.planning_completeness import check_completeness
from obase import ProviderRegistry

# _plan 仍用全章分块喂(规划不截断, 不影响 token).
_CTX = 130000
# ★定向窗口参数 — 每个 KU 合成只喂知识点所在小节, 不喂整章.
_WIN_PRE        = 500    # 知识点 pos 前的 chars(标准路径)
# ★★ 固化标识: 经济学管道程序端标准版本(随 econ_pipeline.sh econ-std-v1.1) ★★
# v1.1: A定位优先真定义框+跳目录 / B窗口清脚注·URL·页码 / C-D WHAT骨架+例子去重(成熟)
PIPELINE_VERSION = "econ-A仓-v1.3"  # 固化: A仓标准(六类+主动抽why+准入+两约束; plan14K小块; 瘦身5步)

_WIN_POST       = 20000  # 知识点 pos 后的 chars(标准路径)
_WIN_FALLBACK   = 40000  # pos 未找到时 fallback
# ★混合路径参数 — 程序提取了WHAT骨架后, LLM看WHY窗口
# 注意: 不跳过定义区(避免引入段被截断); 节省来自省去 intro[:1000]+pre-pos 500 + 缩短 20K→9K
_WIN_SKEL_DEF   = 0      # 从 pos 开始读(不跳过定义区, 防止"Let bygones"等引入段丢失)
_WIN_POST_HYBRID = 9000  # 从 pos 起读 9K chars(比标准 20K 更小)

PLAN_SYS = ("You identify the knowledge points a textbook chapter DIRECTLY AND SUBSTANTIVELY teaches, "
            "classified by ontological type (conceptual / rationale / procedural / positional / factual). "
            "★FAITHFUL TO THE TEXT (命门): for rationale, give ONLY the causal mechanism the text actually "
            "states — never invent causation the text doesn't say. For positional, mark ONLY genuine disputes "
            "the text presents — never turn a consensus principle into a 'dispute'. "
            "Types reflect what the book really is — never force a type that isn't there. Output valid JSON only.")
SYN_SYS = ("You synthesize ONE thorough KU by INTEGRATING the chapter's material. Use ONLY the chapter text. "
           "Cite [Ch{n}]. Write ONLY what IS substantively covered — skip any facet absent from this chapter "
           "(do NOT write placeholder text like 'not covered'). Integration not creation. Bilingual EN+中文. "
           "★ The Chinese MUST be Simplified Chinese (简体中文) only — NEVER Traditional characters (禁止繁体字).")


# ── 章节边界检测(模块级编译, 治本防污染) ──
# 章末/习题/小结/案例栏等区块: 这些都是污染源(混进WHY窗口), 命中即硬截断.
_HARD_BOUNDARY_RE = re.compile(
    r'(?mi)^(?:#{1,4}\s+)?(?:\*\*)?'
    r'(?:KEY\s+TERMS?|WHAT\s+YOU\s+SHOULD|QUESTIONS?\s+AND\s+PROBLEMS?'
    r'|REVIEW\s+QUESTIONS?|EXERCISES?|APPENDIX|FURTHER\s+READING'
    r'|(?:CHAPTER\s+)?SUMMARY|CONCLUSIONS?|(?:CHAPTER\s+)?REVIEW\b'
    r'|习题|练习题|本章小结|本章要点|思考题|复习题|关键术语|延伸阅读|阅读材料|案例分析|专栏)'
    r'(?:\*\*)?\b'
)
_SOFT_BOUNDARY_RE = re.compile(r'(?m)^#{1,4}\s+')

# ── 窗口噪音清洗(页内污染源: 图片占位/脚注/引文/页码/URL) ──
# OCR管道留下的图片占位符(每章15~31个)纯噪音; 喂给LLM前清掉, 让WHY窗口更干净.
_PIC_NOISE_RE  = re.compile(r'\*\*\s*==>.*?omitted.*?<==\s*\*\*', re.I | re.S)
_IMG_TAG_RE    = re.compile(r'!\[[^\]]*\]\([^)]*\)')   # markdown 图片标签(alt文本非定义, 易误当def)
# 脚注行: OCR 常把脚注号与作者/引文粘连成行首("23Stephen Coate, ..."); 数字紧跟大写/引号(无空格)= 脚注.
# (真列表项是 "23. " 或 "23 " 带分隔符, 不会被误伤)
_FOOTNOTE_RE   = re.compile(r'(?m)^\s*>?\s*\d{1,3}(?=[A-Z“‘"\'])[^\n]{0,240}$')
_URL_LINE_RE   = re.compile(r'(?m)^[^\n]*(?:https?://|www\.)\S[^\n]*$')   # URL/引文出处行
_PAGENUM_RE    = re.compile(r'(?m)^\s*\d{1,4}\s*$')                        # 孤立页码行(OCR 页眉页脚)
_BLANKS_RE     = re.compile(r'\n{3,}')


def _clean_window(s: str) -> str:
    """清洗喂给LLM的窗口: 去图片占位/图片标签/脚注/URL引文/孤立页码等页内污染, 收敛多余空行.
    ★边界全覆盖: 这些混进窗口会污染定义抽取与 WHY 窗口, 治本在喂 LLM 前清掉."""
    s = _PIC_NOISE_RE.sub("", s)
    s = _IMG_TAG_RE.sub("", s)
    s = _FOOTNOTE_RE.sub("", s)
    s = _URL_LINE_RE.sub("", s)
    s = _PAGENUM_RE.sub("", s)
    return _BLANKS_RE.sub("\n\n", s).strip()


def _section_end(text: str, after: int, min_dist: int = 4000) -> int:
    """after 之后的实质性节边界(用于截断WHY窗口).
    ★策略: 跳过 <min_dist 的近距离节头(同主题小节); 找第一个 ≥min_dist 的## 或硬边界.
    硬边界(习题/小结等)无视距离直接截断."""
    # 硬边界: 章末/习题区, 无论距离
    mh = _HARD_BOUNDARY_RE.search(text, after + 50)
    hard = mh.start() if mh else len(text)
    # 软边界: 第一个距离 ≥ min_dist 的 ## 标题
    search_from = after + 100
    soft = len(text)
    while True:
        ms = _SOFT_BOUNDARY_RE.search(text, search_from)
        if not ms:
            break
        if ms.start() - after >= min_dist:
            soft = ms.start()
            break
        search_from = ms.end()
    return min(hard, soft)


def _name_variants(name: str) -> list:
    """生成知识点名称的匹配变体: 大写/Title/原始 × 单/复数形式."""
    u, t = name.upper(), name.title()
    out = []
    for base in [u, t, name]:
        out.append(base)
        if not base.rstrip("sS").endswith("s"):  # 如果原始不以s结尾则加复数
            pass
        # 单复数互补
        if base.endswith("S") or base.endswith("s"):
            out.append(base[:-1])  # Cost ← Costs
        else:
            out.append(base + "s")  # Costs ← Cost
    return list(dict.fromkeys(out))  # 去重保序


def _extract_skeleton(text: str, name: str, pos: int):
    """★升级版程序骨架(多策略):
    定义: A.格式定义框(**TERM** ...) → B.is/means/we mean句式 → C.首次出现句(fallback)
    例子: A.**EXAMPLE**块 → B.含数字计算句 → C.for example/e.g./比如句 (去重+按代表性排序)
    其它WHAT: 公式(X = ...)、图表标题(Table/Figure N.N) — 程序能抓的WHAT就抓, 减LLM负担.
    窗口: pos-200:pos+3500 (不依赖边界, 搜定义用小窗已够)
    返回: (def_str, [example_str, ...], [extra_str, ...]) — 均为原文片段."""
    win = _clean_window(text[max(0, pos - 200): min(len(text), pos + 3500)])
    name_lo = name.lower()
    variants = _name_variants(name)

    # ── 定义抽取 ──
    bold_def = ""

    # A: 格式定义框 **TERM** sentence (原有,最可信)
    for term in variants:
        m = re.search(rf'\*\*{re.escape(term)}\*\*\s+[^\n*]{{5,150}}', win, re.I)
        if m:
            bold_def = m.group(0).strip()
            break

    # B: "TERM is/are/means/refers to/we mean/is defined as" 句式
    if not bold_def:
        for term in variants[:4]:
            m = re.search(
                rf'\b{re.escape(term)}\b[^.!?\n]{{0,80}}'
                rf'\b(?:is|are|means?|refers?\s+to|we\s+mean|is\s+defined\s+as|represent)\b'
                rf'[^.!?\n]{{10,200}}[.!?]',
                win, re.I)
            if m:
                sent = m.group(0).strip()
                if 25 < len(sent) < 420:
                    bold_def = sent
                    break

    # C: 位置策略 — 含术语的首个非标题短句(fallback)
    if not bold_def:
        for s in re.split(r'(?<=[.!?])\s+|\n\n', win):
            s = s.strip()
            if (name_lo in s.lower()
                    and not s.startswith('#')
                    and 20 < len(s) < 450):
                bold_def = s
                break

    # ── 例子抽取 (多策略 + 去重 + 按代表性排序) ──
    seen, examples = set(), []

    def _norm_key(s: str) -> str:
        # 归一: 去**标注 → 小写 → 收敛空白 → 取前50字符 (跨策略命中同一例子时可去重)
        s = re.sub(r'\*\*\w[\w\s]*\*\*\s*', '', s)
        return re.sub(r'\s+', ' ', s).strip().lower()[:50]

    def _add(frag: str, pri: int):
        frag = re.sub(r'^\*\*[A-Z]+\*\*\s*', '', frag).strip()  # 去掉 **EXAMPLE** 前缀
        key = _norm_key(frag)
        if key not in seen and len(frag) > 20:
            examples.append((pri, frag[:280]))
            seen.add(key)

    # A: **EXAMPLE** 块(最可信)
    for m in re.finditer(r'\*\*EXAMPLE\*\*\s*([^\n*]{20,280})', win):
        _add(m.group(1), 0)

    # B: 含百分比/货币/小数的计算句(抓原文数字例子, 如 0.27/-1.32)
    # ★ (?<=[.!?\n ]) 保证从句首开始, 避免匹配 **EXAMPLE** 块内的 [A-Z]
    for m in re.finditer(
        r'(?:(?<=[.!?\n])\s*)([A-Z][^.!?\n*]{8,}'
        r'(?:\d[\d,]*\.?\d*\s*(?:%|percent)|'
        r'\$\s*[\d,]+(?:\.\d+)?|\bε[a-z]?\s*=\s*[\d.]+|equals?\s+[\d.]+)'
        r'[^.!?\n*]{5,150}[.!?])',
        win, re.M):
        frag = m.group(1)
        if re.search(r'\b[a-zA-Z]{4,}\b', frag):  # 过滤纯数字/公式噪音
            _add(frag, 1)

    # C: for example / for instance / 比如 / 例如 (不含 e.g. — 常嵌在公式中产生噪音)
    for m in re.finditer(
        r'(?:for example|for instance|比如|例如)[,\s：]+([A-Z가-힣][^.!?\n]{25,250}[.!?])',
        win, re.I):
        frag = m.group(1)
        if re.search(r'\b[a-zA-Z]{4,}\b', frag):
            _add(frag, 2)

    # ★代表性排序: 来源可信(EXAMPLE块>数字句>for example) + 含数字(具体) + 含术语词(对题); 取前4
    last_word = name_lo.split()[-1] if name_lo.split() else name_lo
    def _rep(pe):
        pri, frag = pe; f = frag.lower()
        return -pri * 10 + (3 if re.search(r'\d', frag) else 0) + (2 if last_word in f else 0)
    ordered = [e for _, e in sorted(examples, key=_rep, reverse=True)][:4]

    # ── 其它WHAT骨架: 图表标题 + 关键公式 (程序能抓的WHAT就抓, 减LLM负担) ──
    extras, eseen = [], set()
    def _add_extra(frag: str):
        frag = re.sub(r'\s+', ' ', frag).strip().rstrip('*').strip()
        k = frag.lower()[:50]
        if frag and k not in eseen and 6 < len(frag) < 180:
            extras.append(frag); eseen.add(k)
    # 图表标题(告诉LLM本节有哪些数据/图表): **Table 11.1 ...** / Figure 9.2 ...
    for m in re.finditer(r'(?:Table|Figure|Exhibit)\s+\d+[.\d]*\s+([A-Z][^\n*|]{6,90})', win):
        _add_extra(f"{m.group(0).split()[0]} {m.group(0).split()[1]}: {m.group(1)}")
    # 关键公式: 文本中的命名等式 "<名称> = <表达式>"(图片公式抓不到→不抓, 不造噪音)
    for m in re.finditer(r'(?m)^\s*([A-Za-z][A-Za-z %()/_-]{4,40}=\s*[^=\n]{2,45})\s*$', win):
        frag = m.group(1).strip()
        if re.search(r'[\d%ε]', frag):
            _add_extra("Formula: " + frag)
    # ★表格数据(不只标题): 从 pipe 表抽"标签=数值"数据行(经济书数据表的实质内容)
    # OCR 表多空格列/分隔/标题行; 严格留"文字标签 + 真数值"行, 跳过 Table/Figure 标题行
    tbl = []
    for line in win.split("\n"):
        if line.count("|") < 2:
            continue
        cells = [c.strip(" *") for c in line.strip().strip("|").split("|")]
        cells = [c for c in cells if c and not re.fullmatch(r'-{2,}', c)]
        if len(cells) < 2 or any(re.search(r'\b(Table|Figure|Exhibit)\b', c, re.I) for c in cells):
            continue
        labels = [c for c in cells if re.search(r'[A-Za-z一-鿿]{3,}', c)]
        nums = [c for c in cells if re.fullmatch(r'[-+]?\$?\d[\d,]*\.?\d*\s*%?', c.strip())]
        if labels and nums:
            tbl.append(f"{labels[0]} = {nums[0]}")
    for row in list(dict.fromkeys(tbl))[:4]:
        _add_extra("Data: " + row)
    return bold_def, ordered, extras[:5]


def _facets(typ):
    return {
        # ── 六类各自的"面" ──
        "conceptual": "WHAT(内涵essence/外延boundary/用处use), WHY(why true/important), HOW(how applied)",
        "rationale":  "the MECHANISM / causal chain: WHY-it-is-so and HOW the causation works. "
                      "★ONLY the causation the text EXPLICITLY states — never invent causation the text doesn't say.",
        "procedural": "WHAT, WHEN(applicable conditions), HOW(steps), WHY",
        "positional": "the CLAIM, its ARGUMENT, the OPPOSING stance(s). "
                      "★ONLY a genuine dispute the text presents — never present a consensus principle as a dispute.",
        "factual":    "the 5W: when / where / who / what happened / significance",
        # ── 旧类型 back-compat(防历史调用) ──
        "concept": "WHAT(内涵essence/外延boundary/用处use), WHY(why true/important), HOW(how applied)",
        "principle": "WHAT(meaning), WHY(rationale/evidence), IMPLICATION(what follows)",
        "method": "WHAT, WHEN, HOW(steps), WHY",
    }.get(typ, "WHAT, WHY, HOW")


def _not_toc(tl: str, start: int) -> bool:
    """该位置所在行是否*不是*目录条目(目录行尾是裸页码, 如 '… Demand 263**')."""
    le = tl.find("\n", start)
    ls = tl.rfind("\n", 0, start) + 1
    line = tl[ls: le if le >= 0 else len(tl)]
    return not re.search(r'\d{2,4}\s*\*{0,2}\s*$', line)


def _find_pos(text: str, name: str) -> int:
    """★定位知识点的*定义性*出现(不是顺带提一句的那处). 未找到返回 -1.
    优先级(治本: 定位准→骨架窗口/WHY窗口/边界都准):
      1. 全大写定义框 **TERM** (教材定义框约定, 如 **EXPLICIT COST**) — 最权威
      2. 任意加粗 **term**
      3. 含该词的小节标题 (## ... TERM ...)
      4. 'TERM is/are/means/refers to/defined as' 定义句式
      5. 首次出现(原行为, 词边界兜底)
    ★全程用 \\b 词边界, 杜绝 'elastic' 误命中 'inelastic' 这类子串错位."""
    variants = _name_variants(name)
    upper_vs = list(dict.fromkeys(x.upper() for x in variants))
    # 1a. 全大写定义框 + 后接定义句(The/A/An/"大写词 is/are/refers/means") — 教材定义框最权威,
    #     优先于"小节内顺带加粗"或"列表里点名"的那处(治本: 定位到真讲它的地方).
    for v in upper_vs:
        m = re.search(
            rf'\*\*\s*{re.escape(v)}\s*\*\*\s+(?:The\b|An?\b|[A-Z][a-z]+\s+(?:is|are|means?|refers?))',
            text)
        if m:
            return m.start()
    # 1b. 任意全大写定义框 **TERM**(兜底): **EXPLICIT COST** 优先于运行文里的 **explicit cost**
    for v in upper_vs:
        m = re.search(rf'\*\*\s*{re.escape(v)}\s*\*\*', text)
        if m:
            return m.start()
    tl = text.lower()
    # 2. 任意加粗 **term** (**...** 定界天然防子串错位)
    for v in dict.fromkeys(x.lower() for x in variants):
        m = re.search(rf'\*\*\s*{re.escape(v)}\s*\*\*', tl)
        if m:
            return m.start()
    # 3. 含该词的小节标题(跳过章标题 "# Chapter N:" — 那不是定义节)
    for v in dict.fromkeys(x.lower() for x in variants):
        for m in re.finditer(rf'(?m)^#{{1,4}}\s+[^\n]*\b{re.escape(v)}\b', tl):
            line = tl[m.start(): tl.find("\n", m.start())]
            if re.match(r'#\s+chapter\s+\d', line):
                continue
            if re.search(r'\d{2,4}\s*\*{0,2}\s*$', line):  # 目录条目: 行尾带页码 → 跳过(非真定义节)
                continue
            return m.start()
    # 4. 'TERM is/are/means/refers to/defined as' 定义句(跳过目录条目)
    for v in list(dict.fromkeys(x.lower() for x in variants))[:4]:
        for m in re.finditer(
            rf'\b{re.escape(v)}\b[^.\n]{{0,40}}\b(?:is|are|means?|refers?\s+to|is\s+defined\s+as|measures?|calculated)\b', tl):
            if _not_toc(tl, m.start()):
                return m.start()
    # 5. 兜底: 首次出现(词边界, 跳过目录)— 全名前30字符, 再退最长有意义词
    for m in re.finditer(rf'\b{re.escape(name.lower()[:30])}', tl):
        if _not_toc(tl, m.start()):
            return m.start()
    for word in sorted(name.split(), key=len, reverse=True):
        if len(word) > 4:
            for mm in re.finditer(rf'\b{re.escape(word.lower())}', tl):
                if _not_toc(tl, mm.start()):
                    return mm.start()
            break
    return -1


def _parse_points(t: str) -> list:
    """从LLM返回稳健提取知识点列表.
    标准: {"points":[{"name","type"}]} (DeepSeek遵守). 兜底: 本地模型(gemma)常自拟schema
    (如 {"analysis":{"key_concepts":[{"concept":..}]}}) → 递归找含 name/concept/term 的对象."""
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return []
    pts = data.get("points") if isinstance(data, dict) else None
    if pts:  # 标准 schema, 原样返回(保持 DeepSeek 路径行为不变)
        return pts
    # 兜底: 递归扫描自拟schema, 取 name/concept/term 字段
    out = []
    def _walk(o):
        if isinstance(o, dict):
            nm = (o.get("name") or o.get("concept") or o.get("term")
                  or o.get("point") or o.get("title") or o.get("topic"))
            if isinstance(nm, str) and nm.strip():
                out.append({"name": nm.strip(), "type": o.get("type", "concept")})
            for v in o.values():
                _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)
    _walk(data)
    return out


async def _plan(llm, text, n):
    """规划分块喂(块大小适配 LLM prompt 上限); 额外为每个知识点定位 pos 供 _synth 用.
    ★块大小=min(_CTX, OLLAMA_PROMPT_CHARS-余量): 本地模型(Ollama)按 OLLAMA_PROMPT_CHARS 截断,
      若块>上限会被截断、且指令在文本后→指令被切掉→空规划. 故块要 fit 上限, 指令放最前."""
    import os
    _lim = int(os.getenv("OLLAMA_PROMPT_CHARS", str(_CTX)))
    # ★plan 用较小块(默认14K): 密度大的章若整章塞1次调用, LLM会"摘要"成~10个主概念而漏抽;
    #   小块→每块都granular抽概念→合并后覆盖更全. Ollama 上限更小则取更小.
    _ck = min(_CTX, max(8000, _lim - 2500), int(os.getenv("PLAN_CHUNK_CHARS", "14000")))
    chunks = [text] if len(text) <= _ck else [text[i:i + _ck] for i in range(0, len(text), _ck)]
    pts = []
    for ck in chunks:
        r = await llm(messages=[{"role": "user", "content":
            # ★指令在前(防 Ollama 截断切掉指令), 章节文本在后
            "List the knowledge points this chapter text DIRECTLY AND SUBSTANTIVELY teaches "
            "(each with its own definition or ≥2 paragraphs), classified by TYPE:\n"
            "• conceptual: a concept / principle / law (what X is, what holds) — e.g. price elasticity, law of demand\n"
            "• rationale: a WHY / mechanism the text EXPLICITLY explains (why something holds, the causal chain). "
            "★Only the causation the text actually states — DO NOT invent causation the text doesn't say.\n"
            "• procedural: a method / how-to (steps to do/compute something)\n"
            "• positional: a CONTESTED claim with no settled truth (schools disagree) — give the holder. "
            "★Only genuine disputes the text presents — DO NOT turn a consensus principle into a 'dispute'.\n"
            "• factual: a specific verifiable fact (only if the text really states one)\n\n"
            "★MAIN WHY (深度): for EACH conceptual point, IF the text explains why it holds / its mechanism, "
            'ALSO add a rationale point with "explains":<that concept name>.\n'
            "★ADMISSION GATE: cases / experiments / stories / examples are NOT points (they SUPPORT a point) — exclude them.\n"
            "★TYPES REFLECT THE BOOK: a normal econ textbook is mostly conceptual/rationale/procedural; "
            "positional/factual may be few or NONE — that is correct, NEVER force them.\n\n"
            'Output JSON: {"points":[{"name":"..","type":"conceptual|rationale|procedural|positional|factual",'
            '"explains":"<concept name — ONLY if type=rationale>",'
            '"stance_holder":"<who holds it — ONLY if positional>","opposing":"<opposing stance — ONLY if positional>"}]}\n\n'
            f"Chapter {n} text:\n\n{ck}"}],
            system=PLAN_SYS, max_tokens=1200)
        t = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
        pts += _parse_points(t)
    # dedup by normalized name (单复数/大小写归一)
    seen, out = set(), []
    for p in pts:
        k = re.sub(r"s\b", "", re.sub(r"\s+", " ", p.get("name", "").strip().lower()))[:40]
        if k and k not in seen:
            seen.add(k); out.append(p)
    # ★为每个知识点定位 pos (供 _synth 定向窗口用) — 定位到定义性出现
    for p in out:
        found = _find_pos(text, p.get("name", ""))
        p["pos"] = found if found >= 0 else 0
    return out


async def _synth(llm, text, n, name, typ, pos: int = 0):
    """★定向窗口合成 + 程序骨架混合(hybrid):
    pos>0 且程序能提取定义框/例子 → hybrid: 程序WHAT骨架 + LLM只看WHY后段(小窗口)
    pos>0 无骨架 → 标准定向窗口
    pos=0 → fallback 章首40K"""
    bold_def, examples, extras = _extract_skeleton(text, name, pos) if pos > 0 else ("", [], [])
    has_skeleton = bool(bold_def or examples or extras)

    if pos > 0 and has_skeleton:
        # ── HYBRID路径: 程序已提取WHAT, LLM只看WHY后段 ──
        intro = text[:500]                                          # 短intro(约束/记号)
        # ★ WHY窗口按章节边界截断(治本防污染): 不超过下一个实质性节边界
        boundary  = _section_end(text, pos)
        why_start = min(len(text), pos + _WIN_SKEL_DEF)
        why_end   = min(len(text), why_start + _WIN_POST_HYBRID, boundary)
        why_section = _clean_window(text[why_start:why_end])        # 去图片占位等页内噪音
        # 骨架提示(直接放进 prompt, 不多余)
        skel = ""
        if bold_def:  skel += f"Extracted definition: {bold_def}\n"
        if examples:  skel += "Extracted examples:\n" + "\n".join(f"• {e[:200]}" for e in examples) + "\n"
        if extras:    skel += "Extracted facts (formulas/figures/tables):\n" + "\n".join(f"• {x}" for x in extras)
        context = (f"Chapter {n} opening:\n\n{intro}\n\n"
                   f"Programmatic skeleton for \"{name}\":\n{skel}\n\n"
                   f"Section text (after definition):\n\n{why_section}")
        r = await llm(messages=[{"role": "user", "content":
            f"{context}\n\n"
            f"Synthesize ONE thorough KU for: \"{name}\" (type={typ}). "
            f"Include the WHAT from the skeleton above. "
            f"Focus on WHY (importance/rationale — integrate from section, "
            f"SKIP unrelated paragraphs and end-of-chapter questions). "
            f"Facets: {_facets(typ)}. Each [Ch{n}]-cited; skip absent facets. English then 中文."}],
            system=SYN_SYS.format(n=n), max_tokens=1100)

    elif pos > 0:
        # ── 标准定向窗口 (无骨架) ──
        intro   = text[:1000]
        section = _clean_window(text[max(0, pos - _WIN_PRE): pos + _WIN_POST])
        context = f"Chapter {n} opening (notation/context):\n\n{intro}\n\nRelevant section:\n\n{section}"
        r = await llm(messages=[{"role": "user", "content":
            f"{context}\n\nSynthesize ONE thorough KU for: \"{name}\" (type={typ}). "
            f"Facets: {_facets(typ)}. Each [Ch{n}]-cited; skip absent facets. English then 中文."}],
            system=SYN_SYS.format(n=n), max_tokens=1100)

    else:
        # ── Fallback: pos未找到, 章首40K ──
        context = f"Chapter {n} text (opening):\n\n{_clean_window(text[:_WIN_FALLBACK])}"
        r = await llm(messages=[{"role": "user", "content":
            f"{context}\n\nSynthesize ONE thorough KU for: \"{name}\" (type={typ}). "
            f"Facets: {_facets(typ)}. Each [Ch{n}]-cited; skip absent facets. English then 中文."}],
            system=SYN_SYS.format(n=n), max_tokens=1100)

    return name, "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    register_providers(); llm = ProviderRegistry.get().llm("default")
    text = slice_chapter(SM.read_text(encoding="utf-8", errors="replace"), n)
    print(f"chapter {n}: {len(text)} chars", flush=True)
    points = await _plan(llm, text, n)
    print(f"planned {len(points)} knowledge points", flush=True)
    for p in points:
        print(f"  {p['name']}: pos={p.get('pos', 0)}", flush=True)
    sem = asyncio.Semaphore(8)

    async def s(p):
        async with sem:
            return await _synth(llm, text, n, p["name"], p.get("type", "concept"), p.get("pos", 0))
    kus = await asyncio.gather(*(s(p) for p in points))
    names = [k for k, _ in kus]
    # ★防漏: 完整性校验
    comp = check_completeness(text, names)
    print(f"completeness: {comp['covered_terms']}/{comp['total_terms']} terms; "
          f"complete={comp['complete']} missing={comp['missing_bold_terms']}", flush=True)
    # 补漏: 搜 pos 后再调 _synth
    if comp["missing_bold_terms"]:
        fill_pts = []
        for t in comp["missing_bold_terms"]:
            pos = _find_pos(text, t)
            fill_pts.append({"name": t.title(), "type": "concept", "pos": max(0, pos) if pos >= 0 else 0})
        fill = await asyncio.gather(*(s(p) for p in fill_pts))
        kus = list(kus) + list(fill)
        print(f"backfilled {len(fill)} missing → total {len(kus)} KUs", flush=True)
    import os
    Path(os.getenv("PIPELINE_CKPT_DIR", "/tmp") + f"/ch{n}_synth.md").write_text(
        "\n\n".join(f"### {nm}\n{body}" for nm, body in kus), encoding="utf-8")
    print(f"DONE: {len(kus)} thorough KUs (complete after backfill)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

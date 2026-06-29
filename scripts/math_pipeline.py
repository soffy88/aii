"""数学专门管道: 应有清单(定义/定理/概念)驱动讲透→数学打磨→完整性自检→质量门. 不入正式库, 出实物.
★关键: 讲透由确定性应有清单驱动(每知识点一KU), 不靠LLM规划(那会漏). 公式完整=命门.
★★固化标识: 数学 A仓 KU 抽取标准 math-A仓-v1(2026-06-29) — 双仓A仓=纯抽取(should_have确定性
   定义/定理/公式驱动→逐章讲透→质量门content_match/公式≥80%/facet). 本就无readout/KC/BU(已A仓).
   MATH_LANG=en→英文应有清单. slice_chapter支持 第N章 与 # Chapter N(冒号可选)."""
MATH_PIPELINE_VERSION = "math-A仓-v1"
import asyncio, os, json, re, sys, httpx
from pathlib import Path

# ★内联章节切割(避免从chapter_ingest传递导入aii重链路依赖)
ROOT = Path(__file__).resolve().parents[1]
# 加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "aii" / ".env", override=True)
except ImportError:
    pass
sys.path.insert(0, str(ROOT / "scripts"))

_CN = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
def _cn2int(s):
    if s in _CN: return _CN[s]
    if s.startswith('十'): return 10 + (_CN.get(s[1:], 0))
    if '十' in s:
        a, _, b = s.partition('十'); return _CN[a] * 10 + (_CN.get(b, 0) if b else 0)
    return _CN.get(s, 0)

def slice_chapter(text, n):
    starts = {}
    for m in re.finditer(r'(?m)^第([一二三四五六七八九十]+)章', text):
        line = text[m.start(): text.find('\n', m.start()) if text.find('\n', m.start()) > 0 else m.start()+40]
        if '…' in line or re.search(r'\s\d+\s*$', line): continue
        num = _cn2int(m.group(1))
        if num and num not in starts: starts[num] = m.start()
    # 英文格式 fallback(冒号可选: '# Chapter 1:' 或 '# Chapter 1')
    if not starts:
        for m in re.finditer(r'(?m)^#\s+Chapter\s+(\d+):?\s*$', text):
            starts[int(m.group(1))] = m.start()
    if n not in starts:
        raise SystemExit(f"chapter {n} not found; have {sorted(starts)}")
    s = starts[n]; e = starts.get(n+1, len(text))
    return text[s:e]

# 默认MD文件(可被AII_MD_FILE env覆盖)
SM = Path(os.getenv("AII_MD_FILE",
    "/home/soffy/shared/stratum-to-aii/Principles_of_Microeconomics_The_Way_We__01KVAJCX.md"))

# ★语言通道: MATH_LANG=en → 英文数学书专用应有清单(Definition/Theorem 等); 否则中文版.
if os.getenv('MATH_LANG', '').lower() == 'en':
    from math_should_have_en import extract
else:
    from math_should_have import extract
KEY = os.getenv('DEEPSEEK_API_KEY')

# ★LLM 端点: 设 NVIDIA_NIM_API_KEY → 用 NIM(云端OpenAI兼容, 快+可并发); 否则 DeepSeek.
import time as _time
_NIM_KEY = os.getenv('NVIDIA_NIM_API_KEY')
if _NIM_KEY:
    _LLM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
    _LLM_KEY = _NIM_KEY
    _LLM_MODEL = os.getenv('NIM_MODEL', 'meta/llama-3.3-70b-instruct')
    _LLM_RPM = float(os.getenv('NIM_RPM', '36'))   # NIM 免费层 40/min, 留余量
else:
    _LLM_URL = "https://api.deepseek.com/chat/completions"
    _LLM_KEY = KEY
    _LLM_MODEL = "deepseek-v4-flash"
    _LLM_RPM = 0
# ★全局 async 限流(时间槽节流, 防爆 NIM 40/min)
_RL = {"next": 0.0}
_RL_LOCK = asyncio.Lock()
async def _throttle():
    if not _LLM_RPM:
        return
    async with _RL_LOCK:
        start = max(_time.monotonic(), _RL["next"])
        _RL["next"] = start + 60.0 / _LLM_RPM
    w = start - _time.monotonic()
    if w > 0:
        await asyncio.sleep(w)
# 数学杂乱词(打磨): 含这些的句删
_MATHNONCOV = re.compile(r"未涉及|未覆盖|未出现|未给出|未提及|未定义|未讨论|需查阅|其他资料|建议参考|不在本(章|节)|超出本(章|节)")
def clean_math(t):
    if not t: return ""
    t = re.sub(r"\s*[\[【]\s*第?\s*\d+[-–]?\d*\s*页\s*[\]】]", "", t)  # 去页码溯源
    t = re.sub(r"^\s*(KU|知识单元|类型|概念)\s*[:：].*$", "", t, flags=re.M)  # 去元标记行
    t = t.replace("**", "").replace("##", "")
    kept = [s for s in re.split(r"(?<=[。.！？\n])", t) if s.strip() and not _MATHNONCOV.search(s)]
    return re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()
def facets(typ):
    return {'定义': "定义内容 / 直观含义 / 与相关概念的区别 / 用途",
            '定理': "条件 / 结论 / 证明思路 / 适用场景 / 例子",
            '概念': "是什么 / 核心公式或方法 / 适用条件 / 应用",
            '小节': "该小节核心知识点概述"}.get(typ, "是什么 / 为什么 / 怎么用")


def facet_check(point_name, zh):
    """★面齐校验(防空洞): 含key_terms还不够, 该有的面要齐. 定理/法则/公式型须有证明+例子."""
    issues = []
    # 公式汇总/表型(基本导数公式/积分表): 汇总他处已证的公式, 不需单独证明/例题, 只需公式
    is_summary = bool(re.search(r'基本.*公式|基本.*法则|导数公式|微分公式|积分表|公式表|汇总', point_name))
    is_thm = bool(re.search(r'法则|定理|公式', point_name)) and not is_summary
    has_proof = bool(re.search(r'证明|推导|为什么|∵|证\s*[:(（]|证\s*\d', zh)) or \
                bool(re.search(r'^\s*证\s', zh, re.MULTILINE))
    has_example = bool(re.search(r'例\s*\d|例如|例题|例\s*[:：]|例子', zh))
    # 宽: LaTeX命令/定界符 + ★Unicode数学符号(有时LLM输出 a·b=|a||b|cosθ / ∫_a^x 而非LaTeX)
    has_formula = len(zh) > 0 and bool(re.search(
        r'\\[a-zA-Z]{2,}|\$.+|\\\[|\\\(|[∫∬∭∮∑∏√∂∇∈∉⊂⊆≤≥≠≈≡∞±·×÷→πθλμφψΦΔΩΣαβγ]|[a-zA-Z]\^|\|[a-zA-Z]\|', zh))
    if is_thm:
        if not has_proof:
            issues.append('缺证明/推导')
        if not has_example:
            issues.append('缺例子')
    if len(zh) < 350:
        issues.append('过短(讲浅)')
    if not has_formula:
        issues.append('无公式(数学命门)')
    return issues
SYS=("你为数学教材合成一个讲透知识单元(KU), 严格只用本章内容、整合非创作。"
     "★数学公式必须完整保留(LaTeX原样, 如 \\frac \\lim), 公式残缺=不合格。"
     "中文主显(简体), 之后附English。书没讲的面直接不写(不要写'未涉及')。"
     'Output JSON {"zh":"<中文讲透,含完整LaTeX公式>","en":"<English>"}.')
async def synth(cli, chapter, item):
    # ★分块: 喂该知识点所在小节(由pos定位), 不喂整章[:75000]→长章(Ch11/12 12万/18万字)章尾知识点不再被截断成空
    intro = chapter[:1000]  # 章首记号/约定上下文
    p = item.get('pos', 0)
    section = chapter[max(0, p - 300): p + 13000]
    prompt=f"本章开头(记号约定):\n{intro}\n\n该知识点所在小节:\n{section}\n\n为知识点「{item['label'] or item['id']}」({item['type']}型)合成讲透KU。\n面: {facets(item['type'])}\n中文讲透(公式完整)+English。"
    for _ in range(3):
        try:
            await _throttle()
            r=await cli.post(_LLM_URL,headers={"Authorization":"Bearer "+_LLM_KEY},
                json={"model":_LLM_MODEL,"response_format":{"type":"json_object"},
                      "messages":[{"role":"system","content":SYS},{"role":"user","content":prompt}]})
            j=json.loads(r.json()["choices"][0]["message"]["content"]); return j
        except Exception: await asyncio.sleep(2)
    return None
OUTDIR = Path(os.getenv("MATH_OUTDIR", str(ROOT / "scripts" / "_staging" / "math_full")))
# 支持 AII_MD_FILE env 覆盖(华东师大数分: 上册/下册)
_MD = Path(os.getenv("AII_MD_FILE", str(SM)))
async def main():
    ch_n = int(sys.argv[1]) if len(sys.argv)>1 else 2
    OUTDIR.mkdir(parents=True, exist_ok=True)
    outp = OUTDIR / f"ch{ch_n}.json"
    # ★断点续: 已完成且非空则跳过
    if outp.exists() and len(json.loads(outp.read_text())) > 0:
        print(f"第{ch_n}章 已存在({len(json.loads(outp.read_text()))} KU), 跳过", flush=True); return
    chapter = slice_chapter(_MD.read_text(encoding='utf-8',errors='replace'), ch_n)
    sh = extract(chapter)
    print(f"第{ch_n}章 应有清单: {len(sh)} 知识点", flush=True)
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient(trust_env=False, timeout=60) as cli:
        async def one(item):
            async with sem:
                j = await synth(cli, chapter, item)
                if not j: return None
                zh = clean_math(j.get('zh','')); en = clean_math(j.get('en',''))
                has_latex = bool(re.search(r'\\(frac|lim|int|sqrt|prime|partial)|\$', zh))
                # ★内容层校验: KU内容真含该知识点的辨识词? (堵'占位骗校验')
                content_ok = any(kt in zh for kt in item['key_terms'])
                # ★面齐校验(防空洞): 该有的面齐不齐
                fissues = facet_check(item['id'], zh)
                return {'point': item['id'], 'type': item['type'], 'label': item['label'],
                        'key_terms': item['key_terms'], 'zh': zh, 'en': en,
                        'has_formula': has_latex, 'zh_len': len(zh),
                        'content_match': content_ok, 'facet_issues': fissues}
        kus = [k for k in await asyncio.gather(*(one(it) for it in sh)) if k]
    for k in kus:
        k['chapter'] = ch_n
    outp.write_text(json.dumps(kus, ensure_ascii=False, indent=1))
    # ★完整性校验(内容层): 不是'槽存在', 是'内容真讲了这个知识点'
    miss = [k['point'] for k in kus if not k['content_match']]
    shallow = [(k['point'], k['facet_issues']) for k in kus if k['facet_issues']]
    print(f"讲透 {len(kus)} KU (应有{len(sh)})", flush=True)
    print(f"★内容层完整性: 内容真覆盖 {len(kus)-len(miss)}/{len(kus)}; 占位(内容不匹配)= {miss}", flush=True)
    print(f"★面齐校验(防空洞): 讲浅/缺面 {len(shallow)} 条: {shallow}", flush=True)


if __name__ == '__main__':
    asyncio.run(main())

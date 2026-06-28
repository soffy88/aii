import os, re, glob, sys, shutil
from pathlib import Path

SRC = "/home/soffy/shared/stratum-to-aii"
DST = "/home/soffy/books/MD"
DO = "--do" in sys.argv  # 默认 dry-run

ECON = ['经济','市场','需求','供给','价格','成本','货币','资本','利润','通货','宏观','微观','贸易',
        '金融','投资','边际','弹性','需求曲线','gdp','demand','supply','market','price','cost','profit',
        'capital','inflation','macroeconom','microeconom','marginal','elasticity','fiscal','monetary','economy','economic']
MATH = ['定理','定义','引理','推论','证明','极限','导数','积分','微分','函数','矩阵','向量','集合','拓扑',
        '微积分','数学分析','方程','级数','收敛','theorem','lemma','corollary','calculus','derivative',
        'integral','differential','matrix','manifold','topology','algebra','geometry','convergence']

_GARBAGE = re.compile(r'^(下载|Return to top|GLOBAL EDITION|LONDON|.*版权声明|.*声明$|目录|封面|扉页|致谢|参考文献)',
                      re.I)
_PAPER = re.compile(r'\(\d{4}\)|\bWP\b|\bAER\b|\bJEL\b|\bJOEG\b|\bAEJ\b| et al')  # 学术论文名

def classify(path):
    name = Path(path).stem
    try:
        text = open(path, encoding='utf-8', errors='replace').read()
    except Exception:
        return ('SKIP', '读取失败', False)
    n = len(text)
    samp = text[:300000]; low = samp.lower()
    fffd = text.count('�')
    zh = len(re.findall(r'[一-鿿]', samp)); en = len(re.findall(r'[A-Za-z]', samp))
    headings = len(re.findall(r'(?m)^#{1,4}\s', text))
    chaps = len(re.findall(r'(?m)^#\s+Chapter\s+\d|^第[一二三四五六七八九十百\d]+章', text))
    # ── 品质门 ──
    if _GARBAGE.match(name) or len(name) < 4: return ('低质', '垃圾/碎片名', False)
    if _PAPER.search(name):     return ('低质', '学术论文(非教材)', False)
    if n < 60000:               return ('低质', f'太小({n//1024}KB)', False)
    if fffd / max(n,1) > 0.008: return ('低质', f'OCR乱码({100*fffd//n}%)', False)
    # ── 领域(域文件夹要求章节结构, 管道才能逐章处理)──
    econ = sum(low.count(k) for k in ECON)
    math = sum(low.count(k) for k in MATH)
    # ★带编号的定理/定义(教材特征, 科普没有): 定理6.1 / 定义2 / Theorem 3.1
    thm_num = len(re.findall(r'(定理|定义|引理|推论)\s*[\d一二三四五六七八九十]', samp)) \
            + len(re.findall(r'\b(theorem|definition|lemma|corollary)\s+\d', low))
    is_zh = zh > 1000  # 有大量中文即判中文书(中文数学教材公式里英文多, 不能用 zh>en)
    dens_e = econ / (n/1000); dens_m = math / (n/1000)
    has_ch = chaps >= 3
    # 经济学教材: econ密度高 + 有章节
    if dens_e >= 2.0 and dens_e >= dens_m and has_ch:
        return ('经济学', f'econ{dens_e:.1f} 章{chaps}', True)
    # 数学教材: math密度高 + 带编号定理多(非科普) + 有章节
    if dens_m >= 1.5 and dens_m > dens_e and thm_num >= 10 and has_ch:
        return ('中文数学' if is_zh else '英文数学', f'math{dens_m:.1f} 编号定理{thm_num} 章{chaps} {"中" if is_zh else "英"}', True)
    return ('其它', f'e{dens_e:.1f}/m{dens_m:.1f} 章{chaps} 编号定理{thm_num}', True)

cats = {}
for f in sorted(glob.glob(f"{SRC}/*.md")):
    cat, reason, ok = classify(f)
    cats.setdefault(cat, []).append((Path(f).name, reason))

for cat in ['经济学','中文数学','英文数学','其它','低质','SKIP']:
    items = cats.get(cat, [])
    print(f"\n=== {cat} ({len(items)}) ===")
    for name, reason in items[:60]:
        print(f"  [{reason}] {name[:50]}")

if DO:
    print("\n=== 执行移动(放入文件夹; 已存在则跳过避免误删; 低质留在源)===")
    moved = skipped = 0
    for cat, items in cats.items():
        if cat in ('低质','SKIP'): continue
        tgt = DST if cat == '其它' else f"{DST}/{cat}"
        os.makedirs(tgt, exist_ok=True)
        for name, _ in items:
            src = f"{SRC}/{name}"; dst = f"{tgt}/{name}"
            if os.path.exists(dst):
                skipped += 1
            else:
                shutil.move(src, dst); moved += 1
    print(f"✓ 移动 {moved} 个, 跳过(已存在){skipped} 个. 低质留在 {SRC}")

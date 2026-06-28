"""数学源文件(PDF/EPUB)→ MD: 找未转的, 检查可转性, 转换+入文件夹.
用法: math_convert.py          # 分析报告(不转)
      math_convert.py --do     # 转换可转的(文字层+章节)并入文件夹
"""
import os, re, sys, glob, unicodedata
import fitz  # pymupdf
from pathlib import Path

SRC = "/home/soffy/books/数学"
MD_ZH = "/home/soffy/books/MD/中文数学"
MD_EN = "/home/soffy/books/MD/英文数学"
DO = "--do" in sys.argv

def norm(s):  # 归一标题(去 z-lib/作者括号/空格标点)用于匹配
    s = re.sub(r'\(z-lib[^)]*\)|\(z-library[^)]*\)|\([^)]*1lib[^)]*\)', '', s, flags=re.I)
    s = re.sub(r'\.(pdf|epub)$', '', s, flags=re.I)
    s = re.sub(r'[\s_\-（）()【】\[\]·,，.。、:：;；]+', '', s)
    return s.lower()

# 已有 MD 的归一标题
existing = set()
for d in (MD_ZH, MD_EN, "/home/soffy/books/MD"):
    for f in glob.glob(f"{d}/*.md"):
        existing.add(norm(Path(f).stem))

def matched(stem):
    ns = norm(stem)
    for e in existing:
        if len(e) >= 6 and (e in ns or ns in e):
            return True
    return False

def chapters(text):
    return len(re.findall(r'(?m)^#\s+Chapter\s+\d|^第[一二三四五六七八九十百\d]+章|^Chapter\s+\d', text))

def analyze(path):
    stem = Path(path).stem
    if matched(stem):
        return ('已转', stem, None)
    try:
        d = fitz.open(path)
    except Exception as e:
        return ('打不开', stem, None)
    npg = d.page_count
    # 抽样查文字层
    txt = ""
    for p in range(0, min(npg, 60), 6):
        txt += d[p].get_text()
    txt_ratio = len(txt) / max(min(npg, 60) // 6, 1)
    # 全文(估章节)
    full = "".join(d[p].get_text() for p in range(npg))
    nch = chapters(full)
    if txt_ratio < 200:
        return ('需OCR(无文字层)', stem, npg)
    if nch < 3:
        return ('无章节结构', stem, npg)
    return ('可转', stem, npg)

from collections import Counter

def convert(path):
    """PDF/EPUB → 清洗后的 MD 文本(去页眉页脚/页码, 章节行提升为 # 标题)."""
    d = fitz.open(path)
    pages = [d[p].get_text() for p in range(d.page_count)]
    cnt = Counter()
    for pg in pages:
        ls = [l.strip() for l in pg.splitlines() if l.strip()]
        if ls:
            cnt[ls[0]] += 1; cnt[ls[-1]] += 1
    headers = {l for l, c in cnt.items() if c > len(pages) * 0.12 and len(l) < 80}
    out = []
    for pg in pages:
        for l in pg.splitlines():
            s = l.strip()
            if not s or s in headers or re.fullmatch(r'\d{1,4}', s):
                continue
            if re.match(r'^(Chapter\s+\d+|第[一二三四五六七八九十百\d]+章|CHAPTER\s+\d+)\b', s):
                out.append(f"\n# {s}\n")
            else:
                out.append(s)
    return "\n".join(out)

def math_textbook(text):
    """判定: 是否数学教材(带编号定理+章节+数学密度) → (是否, 中/英)."""
    n = len(text); low = text[:300000].lower()
    math = sum(low.count(k) for k in ('定理','定义','证明','函数','极限','导数','积分','微分','矩阵',
        '向量','拓扑','收敛','theorem','lemma','proof','calculus','derivative','integral','matrix','topology'))
    thm = len(re.findall(r'(定理|定义|引理|推论)\s*[\d一二三四五六七八九十]', text[:300000])) \
        + len(re.findall(r'\b(theorem|definition|lemma|corollary)\s+\d', low))
    nch = chapters(text)
    zh = len(re.findall(r'[一-鿿]', text[:300000]))
    dens = math / max(n/1000, 1)
    # 管道需 第N章/Chapter 结构(章≥3); 数学性: 大量编号定理 或 高数学密度
    ok = nch >= 3 and (thm >= 30 or dens >= 1.2)
    return ok, ('中文数学' if zh > 1000 else '英文数学'), f'dens{dens:.1f} 定理{thm} 章{nch}'

results = {}
for path in sorted(glob.glob(f"{SRC}/*.pdf") + glob.glob(f"{SRC}/*.epub")):
    cat, stem, npg = analyze(path)
    results.setdefault(cat, []).append((stem, npg, path))

for cat in ['可转','需OCR(无文字层)','无章节结构','已转','打不开']:
    items = results.get(cat, [])
    print(f"\n=== {cat} ({len(items)}) ===")
    for stem, npg, _ in items[:40]:
        print(f"  {'%4d页 '%npg if npg else ''}{stem[:60]}")

if DO:
    print("\n=== 转换「可转」+ 分类入文件夹 ===")
    for stem, npg, path in results.get('可转', []):
        try:
            text = convert(path)
        except Exception as e:
            print(f"  ✗ 转换失败 {stem[:40]}: {e}"); continue
        ok, lang, m = math_textbook(text)
        if not ok:
            print(f"  – 跳过[{m}]: {stem[:42]}"); continue
        tgt = (MD_ZH if lang == '中文数学' else MD_EN)
        # 清理文件名里的 z-lib 噪音
        clean = re.sub(r'\s*\(z-lib[^)]*\)|\s*\(z-library[^)]*\)', '', stem).strip()
        dst = f"{tgt}/{clean}.md"
        if os.path.exists(dst):
            print(f"  – 已存在, 跳过: {clean[:40]}"); continue
        open(dst, 'w', encoding='utf-8').write(text)
        print(f"  ✓ [{lang}] {clean[:45]} ({len(text)//1024}KB)")
    print("✓ 完成. 需OCR/无章节 的未处理(见上).")

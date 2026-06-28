"""批量 OCR 扫描版数学教材(RapidOCR 本地)→ MD → 分类入文件夹.
逐本断点(MD已存在则跳过). 后台跑. 用法: ocr_batch.py
"""
import os, re, sys, glob, time
import fitz
import numpy as np
from collections import Counter
import easyocr

SRC = "/home/soffy/books/数学"
MD_ZH = "/home/soffy/books/MD/中文数学"
MD_EN = "/home/soffy/books/MD/英文数学"
OTHER = "/home/soffy/books/MD"
DPI = 170
LOG = "/home/soffy/projects/AII/ocr_batch.log"

def log(m):
    line = f"[{time.strftime('%m-%d %H:%M:%S')}] {m}"
    print(line, flush=True)
    open(LOG, "a", encoding="utf-8").write(line + "\n")

def needs_ocr(path):
    try:
        d = fitz.open(path)
    except Exception:
        return False
    npg = d.page_count
    txt = "".join(d[p].get_text() for p in range(0, min(npg, 60), 6))
    return len(txt) / max(min(npg, 60) // 6, 1) < 200

def chapters(text):
    return len(re.findall(r'(?m)^#\s+Chapter\s+\d|第[一二三四五六七八九十百\d]+章|^Chapter\s+\d', text))

reader = easyocr.Reader(['ch_sim', 'en'], gpu=True, verbose=False)

def ocr_pdf(path):
    d = fitz.open(path)
    pages = []
    n = d.page_count
    for p in range(n):
        try:
            pix = d[p].get_pixmap(dpi=DPI)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
            if pix.n == 4:
                img = img[:, :, :3]
            res = reader.readtext(img, detail=0, paragraph=True)
            pages.append("\n".join(res) if res else "")
        except Exception:
            pages.append("")
        if (p + 1) % 50 == 0:
            log(f"    …第 {p+1}/{n} 页")
    return pages

def clean(pages):
    cnt = Counter()
    for pg in pages:
        ls = [l.strip() for l in pg.splitlines() if l.strip()]
        if ls:
            cnt[ls[0]] += 1; cnt[ls[-1]] += 1
    headers = {l for l, c in cnt.items() if c > len(pages) * 0.12 and len(l) < 60}
    out = []
    for pg in pages:
        for l in pg.splitlines():
            s = l.strip()
            if not s or s in headers or re.fullmatch(r'\d{1,4}', s):
                continue
            if re.match(r'^(第[一二三四五六七八九十百\d]+章|Chapter\s+\d+|CHAPTER\s+\d+)\b', s):
                out.append(f"\n# {s}\n")
            else:
                out.append(s)
    return "\n".join(out)

def place(text, stem):
    zh = len(re.findall(r'[一-鿿]', text[:300000]))
    lang_zh = zh > 1000
    low = text[:300000].lower()
    math = sum(low.count(k) for k in ('定理','定义','证明','函数','极限','导数','积分','微分','矩阵',
        '向量','拓扑','收敛','theorem','lemma','proof','calculus','integral','matrix','topology'))
    thm = len(re.findall(r'(定理|定义|引理|推论)\s*[\d一二三四五六七八九十]', text[:300000])) \
        + len(re.findall(r'\b(theorem|definition|lemma|corollary)\s+\d', low))
    nch = chapters(text)
    is_math = nch >= 3 and (thm >= 20 or math / max(len(text)/1000, 1) >= 1.2)
    clean_name = re.sub(r'\s*\(z-lib[^)]*\)|\s*\(z-library[^)]*\)', '', stem).strip()
    tgt = (MD_ZH if lang_zh else MD_EN) if is_math else OTHER
    return f"{tgt}/{clean_name}.md", is_math, f'章{nch} 定理{thm}'

def main():
    targets = []
    for path in sorted(glob.glob(f"{SRC}/*.pdf")):
        if needs_ocr(path):
            targets.append(path)
    log(f"═══ 需OCR {len(targets)} 本, 开始 ═══")
    for i, path in enumerate(targets, 1):
        stem = os.path.splitext(os.path.basename(path))[0]
        clean_name = re.sub(r'\s*\(z-lib[^)]*\)|\s*\(z-library[^)]*\)', '', stem).strip()
        # 断点: 任一目标位置已有同名 MD 则跳过
        if any(os.path.exists(f"{d}/{clean_name}.md") for d in (MD_ZH, MD_EN, OTHER)):
            log(f"[{i}/{len(targets)}] 跳过(已有): {clean_name[:40]}")
            continue
        t0 = time.time()
        log(f"[{i}/{len(targets)}] OCR: {clean_name[:45]} …")
        try:
            pages = ocr_pdf(path)
            text = clean(pages)
            dst, is_math, m = place(text, stem)
            open(dst, "w", encoding="utf-8").write(text)
            tag = "数学教材→" + os.path.dirname(dst).split("/")[-1] if is_math else "非教材→其它"
            log(f"[{i}/{len(targets)}] ✓ {clean_name[:35]} [{m}] {tag} ({len(text)//1024}KB, {int(time.time()-t0)}s)")
        except Exception as e:
            log(f"[{i}/{len(targets)}] ✗ 失败 {clean_name[:35]}: {e}")
    log("═══ 全部完成 ═══")

if __name__ == "__main__":
    main()

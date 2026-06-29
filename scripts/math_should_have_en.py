"""英文数学书"应有清单"抽取(专门通道) — 镜像 math_should_have 的程序驱动思路,
但针对英文教材标记: **Definition** / **Theorem** / **Rule** / **Lemma** / **Corollary**
(OpenStax 等). 每个带标记的块 = 一个 KU(确定性, 不靠 LLM 规划).
返回与中文版同格式: {type, id, label, key_terms, pos}.
"""
import re

_KIND = {
    'Definition': 'definition', 'Theorem': 'theorem', 'Rule': 'theorem',
    'Lemma': 'theorem', 'Corollary': 'theorem', 'Proposition': 'theorem',
    'Property': 'definition', 'Law': 'theorem',
}
_STOP = {'the', 'and', 'for', 'with', 'that', 'this', 'let', 'where', 'function',
         'value', 'values', 'number', 'real', 'given', 'over', 'form'}


def _key_terms(label):
    words = [w for w in re.split(r'[\s\-/]+', label.lower()) if len(w) >= 3 and w not in _STOP]
    return (words[:3] or [label.lower()[:6]])


def _stmt_terms(stmt):
    """从定理/定义语句里取实质辨识词(content_match 用)."""
    s = re.sub(r'\$[^$]*\$|\([^)]*\)|[^A-Za-z\s]', ' ', stmt[:200])
    words = [w for w in re.split(r'\s+', s) if len(w) >= 4 and w.lower() not in _STOP]
    return (words[:3] or ['theorem'])


def extract(chapter_text):
    """两类标记 → 知识点列表(去重, 按出现序):
       A. OpenStax 黑体 **Definition**/**Theorem** + 块内黑体术语;
       B. ★编号 'Theorem 14.5' / 'Lemma 4.14' / 'Proposition 7.1' / 'Definition 1.1'(行首, 避开引用)."""
    items, seen = [], set()
    # ── A. 黑体标记(OpenStax) ──
    for m in re.finditer(r'\*\*(Definition|Theorem|Rule|Lemma|Corollary|Proposition|Property|Law)s?\*\*',
                         chapter_text):
        kind = _KIND[m.group(1)]
        stmt = chapter_text[m.end(): m.end() + 500]
        label = ''
        for bm in re.finditer(r'\*\*([^*\n]{2,45}?)\*\*', stmt):
            cand = bm.group(1).strip()
            if re.match(r'^\(?\d', cand) or len(cand) < 3:
                continue
            if re.search(r'openstax|access for free|figure|chapter', cand, re.I):
                continue
            label = cand
            break
        if not label or re.match(r'^(Let|If|Suppose|For all|We |Given|Then|Access|Figure)\b', label, re.I):
            continue
        key = re.sub(r'\s+', ' ', label.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        items.append({'type': kind, 'id': f"{m.group(1).lower()}_{len(items)}",
                      'label': label, 'key_terms': _key_terms(label), 'pos': m.start()})
    # ── B. ★编号标记(行首, 避开 'by Theorem X.X' 式引用) ──
    for m in re.finditer(
            r'(?m)^\s*\*{0,2}(Definition|Theorem|Lemma|Corollary|Proposition|Rule|Law)\*{0,2}\s+(\d+(?:\.\d+)?)\b',
            chapter_text):
        kw, num = m.group(1), m.group(2)
        kind = _KIND.get(kw, 'theorem')
        key = f"{kw.lower()} {num}"
        if key in seen:
            continue
        seen.add(key)
        stmt = chapter_text[m.end(): m.end() + 400]
        title_m = re.match(r'\s*[\(\.：:]\s*\*{0,2}([A-Z][^)*\n.]{3,40})', stmt)   # 'Theorem 14.5 (Title)'
        title = title_m.group(1).strip() if title_m else ''
        label = f"{kw} {num}" + (f": {title}" if title else "")
        items.append({'type': kind, 'id': f"{kw.lower()}_{num}",
                      'label': label, 'key_terms': (_key_terms(title) if title else _stmt_terms(stmt)),
                      'pos': m.start()})
    items.sort(key=lambda x: x['pos'])
    return items


if __name__ == '__main__':
    import sys
    t = open(sys.argv[1], encoding='utf-8', errors='replace').read()
    sh = extract(t)
    print(f"{len(sh)} 知识点")
    for s in sh[:15]:
        print(' -', s['label'], f"[{s['type']}]")

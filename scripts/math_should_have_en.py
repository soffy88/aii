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


def extract(chapter_text):
    """带标记的 定义/定理/法则块 → 知识点列表(去重, 按出现序)."""
    items, seen = [], set()
    for m in re.finditer(r'\*\*(Definition|Theorem|Rule|Lemma|Corollary|Proposition|Property|Law)s?\*\*',
                         chapter_text):
        kind = _KIND[m.group(1)]
        stmt = chapter_text[m.end(): m.end() + 500]
        # 标签 = 该块语句里第一个"实质"加粗术语(跳过公式编号 **(2.1)** / 单字母 / 页脚噪音)
        label = ''
        for bm in re.finditer(r'\*\*([^*\n]{2,45}?)\*\*', stmt):
            cand = bm.group(1).strip()
            if re.match(r'^\(?\d', cand) or len(cand) < 3:
                continue
            if re.search(r'openstax|access for free|figure|chapter', cand, re.I):
                continue
            label = cand
            break
        # 无实质加粗术语(纯陈述式定义/定理)→ 跳过, 不造噪音(不回退句子片段)
        if not label or re.match(r'^(Let|If|Suppose|For all|We |Given|Then|Access|Figure)\b', label, re.I):
            continue
        key = re.sub(r'\s+', ' ', label.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        items.append({
            'type': kind,
            'id': f"{m.group(1).lower()}_{len(items)}",
            'label': label,
            'key_terms': _key_terms(label),
            'pos': m.start(),
        })
    return items


if __name__ == '__main__':
    import sys
    t = open(sys.argv[1], encoding='utf-8', errors='replace').read()
    sh = extract(t)
    print(f"{len(sh)} 知识点")
    for s in sh[:15]:
        print(' -', s['label'], f"[{s['type']}]")

"""KU 呈现清洗(机械, 不改知识): 去脚手架结构标签/元标记/markdown 标记/正文来源标注/(未涉及)面.
来源命门: [Ch...] 标注从正文剥离 → 收集到 citations(存 provenance.citations), 底层保留可溯源.
clean(text) -> (clean_text, citations:list[str]); is_empty_shell(clean_text) -> bool.
"""
from __future__ import annotations
import re

# 脚手架关键词
_SCAFFOLD = (r"knowledge unit|综合阐述|知识单元|english|中文|concept|概念|"
             r"what|why|how|when|implication|meaning|essence|boundary|use|rationale|evidence|mechanism|"
             r"是什么|为什么|如何应用|如何|何时|推论|含义|内涵|外延|用处|用途|本质|边界|机制|"
             r"为何成立|为何正确|为何重要|为何真实|原理依据|理由|证据|应用方式|定义|类型|ku")
# 整行纯脚手架(可带 # / 数字编号 / (括号注解) / 冒号, 行内无实质内容)→ 删整行
_HDR = re.compile(rf"^\s*(?:#{{1,6}}\s*)?(?:\d+[.、]\s*)?\**\s*(?:{_SCAFFOLD})\s*"
                  rf"(?:[（(][^）)\n]*[)）])?\s*\**\s*[:：]?\s*$", re.I)
# 行首脚手架面标签(后接同行内容): 带冒号 或 带(括号注解) → 仅剥标签留内容
_LABEL = re.compile(rf"^\s*[-*]?\s*(?:\d+[.、]\s*)?\**\s*(?:{_SCAFFOLD})\s*"
                    rf"(?:(?:[（(][^）)\n]*[)）])\**\s*[:：]?|\**\s*[:：])\s*", re.I)
# 元前言/分隔符整行 → 删
_PREAMBLE = re.compile(
    r"(here is (?:a|an|the|one)\b.{0,70}knowledge unit|here'?s?\b.{0,70}\bku\b|"
    r"^\s*synthesized knowledge unit\b.{0,60}[:：]|^\s*synthesized ku\b.{0,60}[:：]|"
    r"based (?:strictly )?on the\b.{0,40}(?:text|chapter)|"
    r"这是(?:基于|针对|根据|为)\b.{0,40}(?:知识单元|阐述|KU)|以下是.{0,30}(?:知识单元|阐述|KU)|"
    r"针对.{0,20}合成的知识单元|^\s*KU\s*[:：]|^\s*知识单元\s*[:：]|"
    r"英文.{0,4}简体中文如上|简体中文如上|英文\+简体|english\s*\+\s*(?:simplified\s*)?chinese)", re.I)
_SEP = re.compile(r"^\s*[-=*_]{3,}\s*$")
# 句中/行中残留脚手架标签 "。WHY（为何正确/重要） " "。如何应用：" → 句首/句末位置剥除
_INLINE = re.compile(rf"(?:(?<=[。.！？!?；;\n])|^)\s*\**\s*(?:\d+[.、]\s*)?(?:{_SCAFFOLD})\s*"
                     rf"(?:[（(][^）)\n]*[)）])?\**\s*[:：]?\s*", re.I)
# 正文来源标注 [Ch3, ...] / [Ch9: "..."] / 【Ch3，第3.2节】(可含引文)
_CITE = re.compile(r"\s*[\[【]\s*Ch\s*\d+[^\]】]*[\]】]")
# 非覆盖语句(标记 + 解释"书没讲"的整句)→ 删: 既删"(not covered)"标记, 也删"第N章未定义X…"解释句
_NONCOV = re.compile(
    r"(未涉及|未提及|未定义|未讨论|未阐述|未提供|未明确|未被提及|未解释|未具体|未单独|不涉及|"
    r"未覆盖|未涵盖|未出现|未包含|未给出|未介绍|未描述|"
    r"未得到实质性涵盖|未得到实质性覆盖|未实质性涵盖|未实质性覆盖|未实质涵盖|未实质覆盖|"
    r"没有.{0,12}(?:讨论|涉及|定义|提及|阐述|覆盖|涵盖|介绍)|"
    r"并未.{0,20}进行(?:定义|解释|涵盖|讨论|阐述|运用)|"
    r"无法.{0,40}(?:综合出|合成).{0,20}(?:知识单元|KU)|"
    r"无法从.{0,30}(?:材料|文本|内容).{0,30}(?:综合|合成)|"
    r"不进行.{0,8}编造|此处不提供.{0,6}KU|都将是创造(?:，而非整合)?|"
    r"not covered|does not (?:define|discuss|mention|provide|cover|"
    r"address|elaborate|specify|explicitly)|is not (?:covered|discussed|defined|mentioned|addressed))", re.I)


def clean(text: str) -> tuple[str, list[str]]:
    if not text:
        return "", []
    cites: list[str] = []
    # 收集并剥离来源标注
    for m in _CITE.finditer(text):
        cites.append(m.group(0).strip())
    text = _CITE.sub("", text)
    out = []
    for line in text.split("\n"):
        if _HDR.match(line) or _SEP.match(line) or _PREAMBLE.search(line):  # 脚手架/分隔/元前言整行删
            continue
        line = _LABEL.sub("", line)               # 剥面标签留内容(带冒号或括号注解)
        line = re.sub(r"^\s*[-*]\s+", "", line)   # 去开头无序列表符
        line = line.replace("**", "").replace("__", "")  # 去 markdown 粗体标记
        line = re.sub(r"^\s*#{1,6}\s*", "", line)  # 去残留 # 标题符
        line = re.sub(r"[ \t]+", " ", line).rstrip()
        out.append(line)
    txt = "\n".join(out)
    # 句级删非覆盖语句(标记 + "书没讲X"解释句): 按句/行切, 含非覆盖语的句丢弃
    kept = []
    for seg in re.split(r"(?<=[。！？\n])", txt):
        s = seg.strip()
        if s and not _NONCOV.search(s):
            kept.append(seg)
    txt = "".join(kept)
    txt = _INLINE.sub(lambda m: m.group(0)[0] if m.group(0)[:1] in "。.！？!?；;\n" else "", txt)  # 句中残留脚手架
    txt = re.sub(r"[ \t]*\n[ \t]*", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt, cites


# 实质内容判据: 去掉空白/标点/数字编号后还剩中文或英文字母 ≥ 一定量
_SUBSTANCE = re.compile(r"[一-鿿A-Za-z]")


def is_empty_shell(clean_text: str) -> bool:
    """清洗后无实质内容(全是 未涉及 被删空, 或仅剩编号/标点)→ 空壳."""
    core = re.sub(r"[\s\d.、，,。;:：；()（）\-—…•]", "", clean_text)
    return len(_SUBSTANCE.findall(core)) < 8

"""★程序化英文经济学飞轮 — 发现未处理的【英文】经济书.
扫 /home/soffy/books/MD/经济学/*.md → 筛英文(中文占比<15% + 有 # Chapter 章节)→
排除已入库(ingested_substrate ku>100) → 排除 flywheel 终态 → 输出书单.

输出: md路径|substrate_id|书名   (substrate_id: 已知书保留旧id, 新书派生 econ_en_<md5前10>)
用法: python scripts/econ_discover_en.py [--out list.txt] [--limit N] [--state state.json]
"""
import asyncio, asyncpg, json, os, re, sys, hashlib, glob
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "aii" / ".env", override=True)
ECON_DIR = "/home/soffy/books/MD/经济学"

# 已有 substrate_id 的英文经济书 → 保留旧 id(避免重处理/孤儿)
KNOWN = {
    "Principles of Economics 10e": "mankiw_principles_econ_10e",
    "Principles of Microeconomics The Way We": "microecon_en_full_v2",
    "Principles of Microeconomics The Way We Live": "microecon_en_full_v2",
}


def _sid(stem: str) -> str:
    return KNOWN.get(stem) or f"econ_en_{hashlib.md5(stem.encode('utf-8')).hexdigest()[:10]}"


def _is_english_book(text: str) -> tuple[bool, int]:
    """英文书判定: 中文字符占比 < 15% + 有 ≥3 个 # Chapter N 章节. 返回 (是否, 章节数)."""
    samp = text[:200000]
    zh = len(re.findall(r'[一-鿿]', samp))
    en = len(re.findall(r'[A-Za-z]', samp))
    chapters = len(re.findall(r'(?m)^#\s+Chapter\s+\d+', text))
    is_en = (zh / max(zh + en, 1)) < 0.15
    return (is_en and chapters >= 3), chapters


def discover() -> list[dict]:
    out = []
    for md in sorted(glob.glob(f"{ECON_DIR}/*.md")):
        stem = Path(md).stem
        try:
            text = open(md, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        ok, nch = _is_english_book(text)
        if not ok:
            continue
        out.append({"id": _sid(stem), "title": stem, "md_path": md, "chapters": nch})
    return out


async def _ingested(ids):
    if not ids:
        return set()
    conn = await asyncpg.connect(os.getenv("DATABASE_URL"))
    rows = await conn.fetch(
        "SELECT substrate_id FROM aii.ingested_substrate WHERE substrate_id=ANY($1::text[]) AND ku_count>100", ids)
    await conn.close()
    return {r["substrate_id"] for r in rows}


async def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--state", default=str(ROOT / "econ_pipeline" / "flywheel_en_state.json"))
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    cands = discover()
    if args.verbose:
        print(f"英文经济书候选: {len(cands)} 本", file=sys.stderr)
    # 排 flywheel 终态
    state = {"processed": {}}
    if Path(args.state).exists():
        try:
            state = json.loads(Path(args.state).read_text(encoding="utf-8"))
        except Exception:
            pass
    done = {"ingested", "precheck_fail", "quarantine", "skip"}
    cands = [c for c in cands if state["processed"].get(c["id"], {}).get("status") not in done]
    # 排 DB 已入库
    ing = await _ingested([c["id"] for c in cands])
    cands = [c for c in cands if c["id"] not in ing]
    if args.verbose:
        print(f"排已入库/终态后: {len(cands)} 本", file=sys.stderr)
    if args.limit > 0:
        cands = cands[:args.limit]
    lines = [f"{c['md_path']}|{c['id']}|{c['title']}" for c in cands]
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        print(f"书单 → {args.out} ({len(lines)} 本)", file=sys.stderr)
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())

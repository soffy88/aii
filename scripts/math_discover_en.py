"""★程序化英文数学飞轮 — 发现未处理的英文数学书.
扫 /home/soffy/books/MD/英文数学/*.md (该文件夹本就英文) → 要求 ≥3 章(# Chapter N)→
排除已入库(ingested_substrate ku>30)→ 排除 flywheel 终态 → 输出书单.

输出: md路径|substrate_id|书名   (substrate_id: 已知书保留旧id, 新书派生 math_en_<md5前10>)
用法: python scripts/math_discover_en.py [--out list.txt] [--limit N] [--state state.json]
"""
import asyncio, asyncpg, json, os, re, sys, hashlib, glob
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "aii" / ".env", override=True)
MATH_EN_DIR = "/home/soffy/books/MD/英文数学"

KNOWN = {"Calculus Volume 1": "openstax_calculus_v1"}   # 已有 substrate_id 的英文数学书


def _sid(stem: str) -> str:
    return KNOWN.get(stem) or f"math_en_{hashlib.md5(stem.encode('utf-8')).hexdigest()[:10]}"


def _chapters(text: str) -> int:
    cn = len(re.findall(r'(?m)^第[一二三四五六七八九十]+章', text))
    en = len(re.findall(r'(?m)^#\s+Chapter\s+\d+:?\s*$', text))
    return max(cn, en)


def discover() -> list[dict]:
    out = []
    for md in sorted(glob.glob(f"{MATH_EN_DIR}/*.md")):
        stem = Path(md).stem
        try:
            text = open(md, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        nch = _chapters(text)
        if nch < 3:                       # 管道需 ≥3 章
            continue
        out.append({"id": _sid(stem), "title": stem, "md_path": md, "chapters": nch})
    return out


async def _ingested(ids):
    if not ids:
        return set()
    conn = await asyncpg.connect(os.getenv("DATABASE_URL"))
    rows = await conn.fetch(
        "SELECT substrate_id FROM aii.ingested_substrate WHERE substrate_id=ANY($1::text[]) AND ku_count>30", ids)
    await conn.close()
    return {r["substrate_id"] for r in rows}


async def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--state", default=str(ROOT / "math_pipeline" / "flywheel_en_state.json"))
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    cands = discover()
    if args.verbose:
        print(f"英文数学书候选: {len(cands)} 本", file=sys.stderr)
    state = {"processed": {}}
    if Path(args.state).exists():
        try:
            state = json.loads(Path(args.state).read_text(encoding="utf-8"))
        except Exception:
            pass
    done = {"ingested", "precheck_fail", "quarantine", "skip"}
    cands = [c for c in cands if state["processed"].get(c["id"], {}).get("status") not in done]
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

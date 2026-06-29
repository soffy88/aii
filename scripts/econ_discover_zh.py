"""★程序化中文经济学飞轮 — 发现未处理的【中文】经济书.
扫 /home/soffy/books/MD/经济学/ → 筛中文(中文字符 > 英文)+ ≥3 章(第N章 或 # Chapter N)→
排除已入库(ingested_substrate ku>100)→ 排除 flywheel 终态 → 输出书单.

输出: md路径|substrate_id|书名   (substrate_id: econ_zh_<md5前10>)
用法: python scripts/econ_discover_zh.py [--out list.txt] [--limit N] [--state state.json]
"""
import asyncio, asyncpg, json, os, re, sys, hashlib, glob
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "aii" / ".env", override=True)
ECON_DIR = "/home/soffy/books/MD/经济学"


def _sid(stem: str) -> str:
    return f"econ_zh_{hashlib.md5(stem.encode('utf-8')).hexdigest()[:10]}"


def _chapters(text: str) -> int:
    # 与 chapter_ingest 一致: 中文 第N章(行首, 汉字数字) 或 英文 # Chapter N
    cn = len(re.findall(r'(?m)^第([一二三四五六七八九十]+)章', text))
    en = len(re.findall(r'(?m)^#\s+Chapter\s+\d+:?\s*$', text))
    return max(cn, en)


def _is_chinese(text: str) -> bool:
    samp = text[:200000]
    zh = len(re.findall(r'[一-鿿]', samp))
    en = len(re.findall(r'[A-Za-z]', samp))
    return zh > en


def discover() -> list[dict]:
    out = []
    for md in sorted(glob.glob(f"{ECON_DIR}/*.md")):
        stem = Path(md).stem
        try:
            text = open(md, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        if not _is_chinese(text):
            continue
        nch = _chapters(text)
        if nch < 3:                       # 管道需 ≥3 章(无章节书 R1 会拒, 这里先不收)
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
    ap.add_argument("--state", default=str(ROOT / "econ_pipeline" / "flywheel_zh_state.json"))
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    cands = discover()
    if args.verbose:
        print(f"中文经济书候选(有章节): {len(cands)} 本", file=sys.stderr)
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

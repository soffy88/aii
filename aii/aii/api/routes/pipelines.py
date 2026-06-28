"""三通道飞轮 状态 + 控制(经济 key1 / 中文数学 key2 / 英文数学 key3).
飞轮监控 /home/soffy/books/MD/{经济学,中文数学,英文数学} 的 *.md, 持续处理+入库.
运行检测: 飞轮进程(flywheel_channel.sh <cid>)cmdline. 入库数: DB ku_onto + staging.
控制: start(启飞轮)/stop(killpg).
"""
import os
import json
import glob
import signal
import hashlib
import subprocess
from pathlib import Path

import asyncpg
from fastapi import APIRouter, HTTPException

router = APIRouter()
ROOT = Path("/home/soffy/projects/AII")
MD_BASE = Path("/home/soffy/books/MD")
DSN = os.getenv("DATABASE_URL", "postgresql://aii:aii_safe_pass@localhost:5435/aii_kg")
_MAP = ROOT / "flywheel_queue/substrate_map.json"

CHANNELS = {
    "econ":    {"name": "经济学", "folder": "经济学", "key_id": "econ",
                "log": ROOT / "econ_pipeline/run_mankiw.log"},
    "math_zh": {"name": "中文数学", "folder": "中文数学", "key_id": "math_zh",
                "log": ROOT / "math_pipeline/run_shida.log"},
    "math_en": {"name": "英文数学", "folder": "英文数学", "key_id": "math_en",
                "log": ROOT / "math_pipeline/run_calculus.log"},
}


def _keys() -> dict:
    try:
        return json.loads((ROOT / ".pipeline_keys.json").read_text())
    except Exception:
        return {}


def _resolve(domain: str, stem: str) -> str:
    """文件名 → substrate(与 flywheel_resolve.py 一致): 已知走 map, 否则 <domain>_<md5前10>."""
    try:
        m = json.loads(_MAP.read_text(encoding="utf-8"))
    except Exception:
        m = {}
    if stem in m:
        return m[stem]
    return f"{domain}_{hashlib.md5(stem.encode('utf-8')).hexdigest()[:10]}"


def _running_pids(cid: str) -> list[int]:
    pids = []
    for cl in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            data = open(cl, "rb").read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except Exception:
            continue
        if "flywheel_channel.sh" in data and f" {cid}" in data:
            try:
                pids.append(int(cl.split("/")[2]))
            except Exception:
                pass
    return pids


def _staging_ku(sub: str) -> int:
    total = 0
    for f in glob.glob(str(ROOT / "math_pipeline/staging" / sub / "ch*.json")):
        try:
            total += len(json.loads(open(f, encoding="utf-8").read()))
        except Exception:
            pass
    return total


def _done_set(cid: str) -> set:
    try:
        return set((ROOT / f"flywheel_queue/{cid}.done").read_text().split())
    except Exception:
        return set()


def _last_log(p: Path) -> str:
    try:
        lines = [l.strip() for l in p.read_text(errors="replace").splitlines() if l.strip()]
        for l in reversed(lines):
            if not any(x in l for x in ("INFO", "WARNING", "huggingface", "tokenizer")):
                return l[:160]
        return lines[-1][:160] if lines else ""
    except Exception:
        return ""


@router.get("/pipelines")
async def list_pipelines():
    conn = await asyncpg.connect(DSN)
    try:
        out = []
        for cid, c in CHANNELS.items():
            folder = MD_BASE / c["folder"]
            done = _done_set(cid)
            books, total_ku = [], 0
            for md in sorted(glob.glob(str(folder / "*.md"))):
                stem = Path(md).stem
                sub = _resolve(cid, stem)
                ku = await conn.fetchval(
                    "SELECT count(*) FROM aii.ku_onto WHERE substrate_id=$1", sub) or 0
                staged = _staging_ku(sub)
                ku_n = ku or staged
                total_ku += ku_n
                books.append({"title": stem, "substrate": sub, "ku": ku_n,
                              "in_db": ku > 0, "done": sub in done})
            out.append({
                "id": cid, "name": c["name"], "folder": str(folder),
                "running": len(_running_pids(cid)) > 0,
                "has_key": bool(_keys().get(c["key_id"])),
                "books_total": len(books), "books_done": sum(1 for b in books if b["done"]),
                "ku_count": total_ku, "books": books,
                "last_log": _last_log(c["log"]),
            })
        return {"status": "ok", "data": out}
    finally:
        await conn.close()


@router.post("/pipelines/{cid}/start")
async def start_pipeline(cid: str):
    c = CHANNELS.get(cid)
    if not c:
        raise HTTPException(404, "unknown channel")
    if _running_pids(cid):
        return {"status": "ok", "msg": "already running"}
    if not _keys().get(c["key_id"]):
        raise HTTPException(400, f"通道 {cid} 未配置 NIM key")
    c["log"].parent.mkdir(parents=True, exist_ok=True)
    logf = open(c["log"], "ab")
    subprocess.Popen(["bash", "flywheel_channel.sh", cid], cwd=str(ROOT),
                     env={**os.environ, "DATABASE_URL": DSN},
                     stdout=logf, stderr=logf, start_new_session=True)
    return {"status": "ok", "msg": f"{cid} flywheel started"}


@router.post("/pipelines/{cid}/stop")
async def stop_pipeline(cid: str):
    if cid not in CHANNELS:
        raise HTTPException(404, "unknown channel")
    killed = []
    for pid in _running_pids(cid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL); killed.append(pid)
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL); killed.append(pid)
            except Exception:
                pass
    return {"status": "ok", "msg": f"stopped {len(killed)} proc", "killed": killed}

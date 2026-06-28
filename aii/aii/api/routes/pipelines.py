"""三通道管线 状态 + 控制(经济 key1 / 中文数学 key2 / 英文数学 key3).
运行检测: 扫 /proc 看 AII_MD_FILE 含该通道书路径子串(进程都设了 AII_MD_FILE).
入库数: DB ku_onto. 进度: 日志最后一行. 控制: start(detached) / stop(killpg).
"""
import os
import json
import glob
import signal
import subprocess
from pathlib import Path

import asyncpg
from fastapi import APIRouter, HTTPException

router = APIRouter()
ROOT = Path("/home/soffy/projects/AII")
SHARE = "/home/soffy/shared/stratum-to-aii"
DSN = os.getenv("DATABASE_URL", "postgresql://aii:aii_safe_pass@localhost:5435/aii_kg")


def _keys() -> dict:
    f = ROOT / ".pipeline_keys.json"
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


CHANNELS = {
    "econ": {
        "name": "经济学 · Mankiw《经济学原理》10e",
        "key_id": "econ", "substrate": "mankiw_principles_econ_10e", "total": 38,
        "match": "Principles of Economics 10e",
        "log": ROOT / "econ_pipeline/run_mankiw.log",
        "cmd": ["bash", "scripts/econ_pipeline.sh"],
        "env": lambda k: {
            "NVIDIA_NIM_API_KEY": k.get("econ", ""),
            "SUBSTRATE": "mankiw_principles_econ_10e",
            "AII_MD_FILE": f"{SHARE}/Principles of Economics 10e.md",
            "ECON_TITLE": "Principles of Economics, 10e (Mankiw)",
            "DATABASE_URL": DSN, "PIPELINE_CKPT_DIR": "econ_pipeline/ckpts",
        },
    },
    "math_zh": {
        "name": "中文数学 · 华东师大《数学分析》第5版上",
        "key_id": "math_zh", "substrate": "shida_mathanalysis_v5_vol1", "total": 12,
        "match": "数学分析",
        "log": ROOT / "math_pipeline/run_shida.log",
        "cmd": ["bash", "math_run_shida.sh"],
        "env": lambda k: {"DATABASE_URL": DSN},
    },
    "math_en": {
        "name": "英文数学 · Calculus Volume 1 (OpenStax)",
        "key_id": "math_en", "substrate": "openstax_calculus_v1", "total": 6,
        "match": "Calculus Volume 1",
        "log": ROOT / "math_pipeline/run_calculus.log",
        "cmd": ["bash", "math_run_en.sh"],
        "env": lambda k: {
            "NVIDIA_NIM_API_KEY": k.get("math_en", ""),
            "AII_MD_FILE": f"{SHARE}/Calculus Volume 1.md",
            "SUBSTRATE": "openstax_calculus_v1",
            "MATH_TITLE": "Calculus Volume 1 (OpenStax)", "DATABASE_URL": DSN,
        },
    },
}


def _running_pids(cid: str) -> list[int]:
    """飞轮进程检测: cmdline 含 'flywheel_channel.sh <cid>'(持续运转, sleep 时也算运行)."""
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


def _staging_ku(substrate: str) -> int:
    """数学 KU 先落 staging(json), register 后才进 DB; 统计 staging 里的 KU 数."""
    total = 0
    for f in glob.glob(str(ROOT / "math_pipeline/staging" / substrate / "ch*.json")):
        try:
            total += len(json.loads(open(f, encoding="utf-8").read()))
        except Exception:
            pass
    return total


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
            ku = await conn.fetchval(
                "SELECT count(*) FROM aii.ku_onto WHERE substrate_id=$1", c["substrate"])
            staged = _staging_ku(c["substrate"])
            pids = _running_pids(cid)
            out.append({
                "id": cid, "name": c["name"], "substrate": c["substrate"],
                "total_chapters": c["total"], "ku_count": (ku or 0) or staged,
                "in_db": (ku or 0) > 0, "staged_ku": staged,
                "running": len(pids) > 0, "pids": pids,
                "has_key": bool(_keys().get(c["key_id"])),
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
        raise HTTPException(400, f"通道 {cid} 未配置 NIM key(.pipeline_keys.json)")
    c["log"].parent.mkdir(parents=True, exist_ok=True)
    logf = open(c["log"], "ab")
    # ★启动持续飞轮(不停运转: 处理队列→入库→sleep监视新书→循环). key 由飞轮自读.
    subprocess.Popen(["bash", "flywheel_channel.sh", cid], cwd=str(ROOT),
                     env={**os.environ, "DATABASE_URL": DSN},
                     stdout=logf, stderr=logf, start_new_session=True)
    return {"status": "ok", "msg": f"{cid} flywheel started"}


@router.post("/pipelines/{cid}/stop")
async def stop_pipeline(cid: str):
    c = CHANNELS.get(cid)
    if not c:
        raise HTTPException(404, "unknown channel")
    killed = []
    for pid in _running_pids(cid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            killed.append(pid)
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL); killed.append(pid)
            except Exception:
                pass
    return {"status": "ok", "msg": f"stopped {len(killed)} proc", "killed": killed}

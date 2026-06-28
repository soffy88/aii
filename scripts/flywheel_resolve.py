"""文件名 → substrate_id 解析(飞轮与面板共用, 保证一致).
已知书走 substrate_map.json(保现有进度); 新书派生 <domain>_<md5前10> 并写回 map.
用法: flywheel_resolve.py <domain> <md_path>  → 打印 substrate
"""
import sys
import json
import hashlib
from pathlib import Path

MAP = Path("/home/soffy/projects/AII/flywheel_queue/substrate_map.json")


def resolve(domain: str, md_path: str, persist: bool = True) -> str:
    stem = Path(md_path).stem
    try:
        m = json.loads(MAP.read_text(encoding="utf-8"))
    except Exception:
        m = {}
    if stem in m:
        return m[stem]
    sub = f"{domain}_{hashlib.md5(stem.encode('utf-8')).hexdigest()[:10]}"
    if persist:
        m[stem] = sub
        MAP.parent.mkdir(parents=True, exist_ok=True)
        MAP.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return sub


if __name__ == "__main__":
    print(resolve(sys.argv[1], sys.argv[2]))

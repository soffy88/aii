import asyncio
import concurrent.futures
import os
import logging
import threading
import httpx
from obase import ProviderRegistry
from oprim import vector_encode

# ★全局 Ollama 串行锁: 本地单 GPU 一次只能可靠跑 1 个 gemma 请求; 并发请求会让 gemma4
# 输出垃圾(如 '{"'). 管道多处 gather/Semaphore(synth=8, readout=∞, cross=5)对云端
# DeepSeek 没事, 但对本地 Ollama 必须串行. 在 caller 层加锁, 不必改各步骤并发参数.
_OLLAMA_CALL_LOCK = threading.Lock()

logger = logging.getLogger(__name__)


def _make_deepseek_caller(api_key: str, model: str = "deepseek-v4-flash",
                          base_url: str = "https://api.deepseek.com/chat/completions",
                          rpm: float = 0) -> callable:
    """Return an async callable compatible with both omodul (messages/system/max_tokens kwargs)
    and the legacy synthesis_engine (single positional prompt string via executor).

    Signature: async (messages=None, *, system='', max_tokens=4096, **_) -> dict
    The returned dict has the Anthropic shape: {"content": [{"type": "text", "text": "..."}]}
    Legacy callers may also call the inner _call_sync_json(prompt: str) -> str directly.
    _call_sync_json uses response_format=json_object to fix ~70% retry rate in llm_extract_ku.
    _call_sync (used internally by _call_async for synthesis) does NOT force JSON mode.
    """
    _client = httpx.Client(trust_env=False, timeout=120)

    # ★全局限流: NVIDIA NIM 免费层 40 req/min. rpm>0 时所有并发调用排队, 间隔 60/rpm 秒,
    #   防 readout(无限并发)等步骤爆 429. 给每个调用分配一个时间槽, 锁外 sleep.
    _min_int = (60.0 / rpm) if rpm else 0.0
    _rl_lock = threading.Lock()
    _rl_next = [0.0]

    def _throttle() -> None:
        if not _min_int:
            return
        import time as _t
        with _rl_lock:
            start = max(_t.monotonic(), _rl_next[0])
            _rl_next[0] = start + _min_int
        w = start - _t.monotonic()
        if w > 0:
            _t.sleep(w)

    def _call_sync(prompt: str) -> str:
        """Synchronous DeepSeek call for synthesis (plain text, no JSON mode)."""
        _throttle()
        resp = _client.post(
            base_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _call_sync_json(prompt: str) -> str:
        """Synchronous DeepSeek call for extraction (JSON mode → eliminates markdown fence retries)."""
        _throttle()
        resp = _client.post(
            base_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    async def _call_async(messages=None, *, system: str = "", max_tokens: int = 4096, **_):
        """Async omodul-compatible LLM caller (Anthropic message format → Anthropic response dict).
        Uses plain-text _call_sync (not JSON mode) for synthesis compatibility."""
        parts: list[str] = []
        if system:
            parts.append(system)
        for msg in (messages or []):
            if isinstance(msg, dict) and msg.get("role") == "user":
                parts.append(msg.get("content", ""))
        combined = "\n\n".join(p for p in parts if p)
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            answer = await loop.run_in_executor(ex, _call_sync, combined)
        return {"content": [{"type": "text", "text": answer}]}

    # Extraction callers (llm_extract_ku) use call_sync → JSON mode
    _call_async.call_sync = _call_sync_json
    return _call_async


def _make_ollama_caller(model: str = "qwen2.5:7b", base_url: str = "http://localhost:11434") -> callable:
    """Return a caller backed by local Ollama.

    _call_async (synthesis path): plain text, no format=json, 8 k char limit.
    call_sync (extraction path, llm_extract_ku): format=json for clean JSON output.
    """
    _client = httpx.Client(trust_env=False, timeout=600)  # local models: 8 concurrent × ~60s each
    # 提示字符上限: qwen2.5:7b 默认 8000 够; 大context模型(gemma 128K)可经 env 调大避免裁掉WHY窗口/规划全章
    _max_chars = int(os.getenv("OLLAMA_PROMPT_CHARS", "8000"))

    def _call_sync(prompt: str) -> str:
        """KU 抽取用: format=json 强制结构化输出."""
        with _OLLAMA_CALL_LOCK:  # ★串行: 单 GPU 并发会让 gemma 输出垃圾
            resp = _client.post(
                f"{base_url}/api/generate",
                json={"model": model, "prompt": prompt[:_max_chars], "stream": False, "format": "json"},
            )
        resp.raise_for_status()
        return resp.json()["response"]

    def _call_sync_plain(prompt: str) -> str:
        """合成/纯文本用: 不加 format=json, 直接返回自然语言."""
        with _OLLAMA_CALL_LOCK:  # ★串行: 单 GPU 并发会让 gemma 输出垃圾
            resp = _client.post(
                f"{base_url}/api/generate",
                json={"model": model, "prompt": prompt[:_max_chars], "stream": False},
            )
        resp.raise_for_status()
        return resp.json()["response"]

    async def _call_async(messages=None, *, system: str = "", max_tokens: int = 4096, **_):
        """Async wrapper. Uses JSON mode when system prompt requests structured JSON output."""
        parts: list[str] = []
        if system:
            parts.append(system)
        for msg in (messages or []):
            if isinstance(msg, dict) and msg.get("role") == "user":
                parts.append(msg.get("content", ""))
        combined = "\n\n".join(p for p in parts if p)
        # Use JSON mode for planning/extraction; plain text for synthesis
        wants_json = "output valid json" in system.lower() or "output json" in system.lower()
        caller = _call_sync if wants_json else _call_sync_plain
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            answer = await loop.run_in_executor(ex, caller, combined)
        return {"content": [{"type": "text", "text": answer}]}

    _call_async.call_sync = _call_sync          # extraction: JSON mode
    _call_async.call_sync_plain = _call_sync_plain  # dedup/plain-text
    return _call_async

def register_providers():
    """Register computational providers for AII (A24 Routing).

    ECON_LLM_PROVIDER=ollama  → Ollama becomes the "default" provider (for local testing).
    OLLAMA_MODEL env var selects the model (default: qwen2.5:7b).
    """
    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    use_ollama_as_default = os.getenv("ECON_LLM_PROVIDER", "").lower() == "ollama"

    # 1. LLM Provider (DeepSeek v4) — default=flash (实测够用且最省); pro 备用
    #    deepseek-chat 别名 2026/07/24 下线, 已显式改 deepseek-v4-flash.
    api_key = os.getenv("DEEPSEEK_API_KEY")
    ProviderRegistry.register("llm", "deepseek-flash", _make_deepseek_caller(api_key, model="deepseek-v4-flash"))
    ProviderRegistry.register("llm", "deepseek-pro", _make_deepseek_caller(api_key, model="deepseek-v4-pro"))

    # ★NVIDIA NIM (云端 OpenAI 兼容; 快 + 可并发, 避开本地单 GPU 串行瓶颈, 无需 DeepSeek 余额).
    #   设 NVIDIA_NIM_API_KEY 即作 default(优先于 DeepSeek); 模型经 NIM_MODEL 选(默认 llama-3.3-70b).
    nim_key = os.getenv("NVIDIA_NIM_API_KEY")
    use_nim = bool(nim_key) and not use_ollama_as_default
    if nim_key:
        nim_model = os.getenv("NIM_MODEL", "meta/llama-3.3-70b-instruct")
        nim_rpm = float(os.getenv("NIM_RPM", "36"))  # NIM 免费层 40/min, 留余量
        nim_caller = _make_deepseek_caller(nim_key, model=nim_model,
                                           base_url="https://integrate.api.nvidia.com/v1/chat/completions",
                                           rpm=nim_rpm)
        ProviderRegistry.register("llm", "nim", nim_caller)
        if use_nim:
            ProviderRegistry.register("llm", "default", nim_caller)
            logger.info("NVIDIA NIM registered as DEFAULT: %s", nim_model)
    if not use_ollama_as_default and not use_nim:
        ProviderRegistry.register("llm", "default", _make_deepseek_caller(api_key, model="deepseek-v4-flash"))

    # 2. LLM Provider (Ollama) — low-trust sources OR local testing (ECON_LLM_PROVIDER=ollama)
    try:
        ollama_caller = _make_ollama_caller(ollama_model, ollama_base)
        ProviderRegistry.register("llm", "ollama-local", ollama_caller)
        if use_ollama_as_default:
            ProviderRegistry.register("llm", "default", ollama_caller)
            logger.info("Ollama-local registered as DEFAULT: %s @ %s", ollama_model, ollama_base)
        else:
            logger.info("Ollama-local provider registered: %s @ %s", ollama_model, ollama_base)
    except Exception as e:
        logger.warning("Ollama-local registration failed (non-fatal): %s", e)
        if use_ollama_as_default:
            raise RuntimeError(f"ECON_LLM_PROVIDER=ollama but Ollama unavailable: {e}") from e

    # 3. Embedding Provider (Real BGE-M3)
    from oprim.embedding.bge_m3 import BgeM3Embedder
    try:
        embedder = BgeM3Embedder()
        ProviderRegistry.register("embedding", "default", embedder.embed)
        logger.info("REAL BGE-M3 Provider registered.")
    except Exception as e:
        logger.error(f"Failed to load REAL BGE-M3: {e}")

    default_lbl = f"Ollama({ollama_model})" if use_ollama_as_default else "DeepSeek"
    logger.info("AII Providers registered: llm/default(%s), embedding/default", default_lbl)

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    register_providers()
    print(f"LLM default registered: {ProviderRegistry.has('llm', 'default')}")
    print(f"Embedding default registered: {ProviderRegistry.has('embedding', 'default')}")

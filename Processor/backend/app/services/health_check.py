"""llama.cpp server health check (§3.4).

llama.cpp's built-in server exposes `/health` at the server root — outside
the `/v1` OpenAI-compatible prefix — so this can't reuse the `openai`
client and talks to it directly with `httpx`.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


def _health_url(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return f"{root}/health"


async def check_llamacpp_health(base_url: str, timeout_seconds: float = 5.0) -> bool:
    url = _health_url(base_url)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=timeout_seconds)
        return resp.status_code == 200
    except httpx.HTTPError as exc:
        logger.warning("llama.cpp health check failed", extra={"url": url, "error": str(exc)})
        return False

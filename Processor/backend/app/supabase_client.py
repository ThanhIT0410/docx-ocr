"""Supabase async client singleton, service_role key.

One client is shared process-wide. `supabase-py`'s client is safe to reuse
across requests; it does not hold a persistent DB connection (everything
goes over HTTPS to PostgREST/Storage). `create_async_client` is itself a
coroutine, so this can't just be an `@lru_cache`d sync function like the
old `create_client` version — a module-level singleton guarded by an
`asyncio.Lock` gives the same "construct exactly once" guarantee for
concurrent first callers (e.g. two requests landing before the client
exists yet) without double-constructing.
"""
from __future__ import annotations

import asyncio

from supabase import AsyncClient, create_async_client

from app.config.settings import Settings, get_settings

_client: AsyncClient | None = None
_lock = asyncio.Lock()


async def get_supabase() -> AsyncClient:
    global _client
    if _client is None:
        async with _lock:
            if _client is None:
                settings: Settings = get_settings()
                _client = await create_async_client(
                    settings.supabase_url, settings.supabase_service_role_key
                )
    return _client

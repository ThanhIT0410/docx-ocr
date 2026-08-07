"""DB/Storage size stats for GET /processor/dashboard, via Supabase's
Management API — NOT the same API `supabase_client.py`'s `Client` (service_
role key) uses everywhere else in Processor. PostgREST (what `service_role`
talks to) only exposes tables/views/functions, not an "execute arbitrary
SQL" endpoint, so getting `pg_database_size()`/`storage.objects` totals
needs a different, separate Supabase product: the Management API, which
runs a raw SQL string and returns the rows as JSON. That API is
account-scoped (`PROCESSOR_SUPABASE_ACCESS_TOKEN`, a personal access token
— broader than the project-scoped `service_role` key), so both functions
here are defensive-by-design: a missing token, a network error, or an
unsuccessful response all just log and return `None` rather than raise — a
Dashboard misconfiguration should never break the rest of the endpoint.
Confirmed against a real project/token: this endpoint returns 201, not 200,
on a successful query — both are accepted as success.
"""
from __future__ import annotations

import logging

import httpx

from app.config.settings import Settings

logger = logging.getLogger(__name__)

_MANAGEMENT_API_BASE = "https://api.supabase.com/v1"
_TIMEOUT_SECONDS = 10.0


async def _run_query(settings: Settings, query: str) -> list[dict] | None:
    if not settings.supabase_access_token:
        return None
    url = f"{_MANAGEMENT_API_BASE}/projects/{settings.supabase_project_ref}/database/query"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.supabase_access_token}"},
                json={"query": query},
                timeout=_TIMEOUT_SECONDS,
            )
        if resp.status_code not in (200, 201):
            logger.warning(
                "Supabase Management API query failed", extra={"status": resp.status_code, "body": resp.text[:300]}
            )
            return None
        return resp.json()
    except httpx.HTTPError as exc:
        logger.warning("Supabase Management API request failed", extra={"error": str(exc)})
        return None


async def get_db_size_bytes(settings: Settings) -> int | None:
    rows = await _run_query(settings, "select pg_database_size(current_database()) as bytes;")
    if not rows:
        return None
    return int(rows[0]["bytes"])


async def get_storage_size_bytes(settings: Settings, bucket: str) -> int | None:
    rows = await _run_query(
        settings,
        "select bucket_id, count(*) as file_count, sum((metadata->>'size')::bigint) as bytes "
        "from storage.objects group by bucket_id;",
    )
    if rows is None:
        return None
    for row in rows:
        if row.get("bucket_id") == bucket:
            return int(row["bytes"] or 0)
    return 0

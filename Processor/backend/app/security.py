"""API key auth dependencies (§3.7 "Bảo mật & phạm vi truy cập").

Two separate secrets by design: `api_key` guards every /processor/* route,
`admin_api_key` is required *in addition* on /processor/admin/reset, so a
leaked operator key alone can't trigger the destructive reset endpoint.

This is not a substitute for network isolation — the design report calls
out that this service must not be exposed to the public internet.
"""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config.settings import Settings, get_settings
from app.constants import ADMIN_API_KEY_HEADER, API_KEY_HEADER

_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)
_admin_api_key_header = APIKeyHeader(name=ADMIN_API_KEY_HEADER, auto_error=False)


def _check(provided: str | None, expected: str) -> None:
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
        )


def require_api_key(
    key: str | None = Security(_api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    _check(key, settings.api_key)


def require_admin_api_key(
    admin_key: str | None = Security(_admin_api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    """Extra dependency for /admin/reset, checked on top of `require_api_key`
    (routes needing both list `require_api_key` first, then this one). Reads
    a *separate* header (`X-Admin-API-Key`) — deliberately not reusing
    `X-API-Key`, since a single header can't be compared against two
    different secrets at once."""
    _check(admin_key, settings.admin_api_key)

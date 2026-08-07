"""HTTP-level tests for controllers/exams.py's preview endpoint. Isolated
FastAPI app (not app.main's) with dependency overrides, same pattern as
tests/test_queue_controller.py — no real Supabase project needed."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from app.controllers import exams as exams_controller
from app.dependencies import get_db, get_storage
from app.schemas.models import Page
from app.security import require_api_key


def _page(page_id: str, exam_id: str, order: int) -> Page:
    return Page.model_validate(
        {
            "id": page_id,
            "exam_id": exam_id,
            "page_order": order,
            "file_path": f"{exam_id}/{page_id}.jpg",
            "ocr_text": None,
        }
    )


def _make_app(storage) -> FastAPI:
    app = FastAPI()
    app.include_router(exams_controller.router)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[require_api_key] = lambda: None
    return app


def _get(app: FastAPI, path: str):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(scenario())


def test_preview_returns_signed_url_per_page(monkeypatch):
    exam_pages = [_page("p1", "exam-1", 1), _page("p2", "exam-1", 2)]
    monkeypatch.setattr(exams_controller.pages, "list_pages", AsyncMock(return_value=exam_pages))

    storage = type("Storage", (), {})()
    storage.create_signed_urls = AsyncMock(
        return_value={
            "exam-1/p1.jpg": "https://signed.example/p1",
            "exam-1/p2.jpg": None  # signing that one failed
        }
    )
    app = _make_app(storage)

    r = _get(app, "/processor/exams/exam-1/preview")

    assert r.status_code == 200
    body = r.json()
    assert body == {
        "pages": [
            {"page_id": "p1", "url": "https://signed.example/p1"},
            {"page_id": "p2", "url": None}
        ]
    }
    storage.create_signed_urls.assert_awaited_once()
    args = storage.create_signed_urls.await_args.args
    assert args[0] == ["exam-1/p1.jpg", "exam-1/p2.jpg"]
    assert args[1] == exams_controller.PREVIEW_URL_EXPIRES_IN_SECONDS


def test_preview_no_pages_returns_empty_list(monkeypatch):
    monkeypatch.setattr(exams_controller.pages, "list_pages", AsyncMock(return_value=[]))
    storage = type("Storage", (), {})()
    storage.create_signed_urls = AsyncMock(return_value={})
    app = _make_app(storage)

    r = _get(app, "/processor/exams/exam-empty/preview")

    assert r.status_code == 200
    assert r.json() == {"pages": []}

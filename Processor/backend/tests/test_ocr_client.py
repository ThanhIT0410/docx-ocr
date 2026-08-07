"""Unit tests for OcrClient — the underlying AsyncOpenAI call is mocked,
no real llama.cpp server involved."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.ocr_client import OcrBatchError, OcrClient, OcrTruncatedError


def _client() -> OcrClient:
    return OcrClient(
        base_url="http://localhost:8080/v1",
        api_key="not-needed",
        model="test-model",
        max_attempts=3,
        backoff_base_seconds=0.01,
        temperature=0.0,
        max_tokens=64,
    )


def _fake_response(content: str, finish_reason: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(finish_reason=finish_reason, message=message)
    return SimpleNamespace(choices=[choice])


def _run(coro):
    return asyncio.run(coro)


def test_call_once_raises_ocr_truncated_error_on_length_finish_reason():
    client = _client()
    create = AsyncMock(return_value=_fake_response("<div ...", "length"))
    client._client.chat.completions.create = create  # noqa: SLF001 — test reaches into the wrapped SDK client on purpose

    with pytest.raises(OcrTruncatedError, match="max_tokens=64"):
        _run(client._call_once(b"fake-image-bytes"))

    create.assert_awaited_once()


def test_call_once_returns_content_on_normal_stop():
    client = _client()
    create = AsyncMock(return_value=_fake_response("<div data-bbox='0 0 10 10'>hi</div>", "stop"))
    client._client.chat.completions.create = create  # noqa: SLF001

    result = _run(client._call_once(b"fake-image-bytes"))

    assert "hi" in result


def test_truncation_is_not_retried():
    """finish_reason='length' is deterministic given the same prompt/image
    at temperature 0 — retrying would just truncate again, so this must
    fail on the first attempt, not burn through max_attempts."""
    client = _client()
    create = AsyncMock(return_value=_fake_response("<div ...", "length"))
    client._client.chat.completions.create = create  # noqa: SLF001

    with pytest.raises(OcrTruncatedError):
        _run(client.process_image(b"fake-image-bytes", 100, 200, 100, 200))

    assert create.await_count == 1


def test_ocr_truncated_error_is_an_ocr_batch_error():
    """Callers (ocr_pipeline.py) only need to catch/handle OcrBatchError
    generically — confirms OcrTruncatedError doesn't need its own special
    case anywhere."""
    assert issubclass(OcrTruncatedError, OcrBatchError)

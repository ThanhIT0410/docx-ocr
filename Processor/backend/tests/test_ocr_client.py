"""Unit tests for OcrClient — the underlying AsyncOpenAI call is mocked,
no real llama.cpp server involved."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import BadRequestError

from app.services.ocr_client import OcrBadRequestError, OcrBatchError, OcrClient, OcrConnectivityError, OcrTruncatedError


def _client() -> OcrClient:
    return OcrClient(
        base_url="http://localhost:8080/v1",
        api_key="not-needed",
        model="test-model",
        max_attempts=3,
        backoff_base_seconds=0.01,
        temperature=0.1,
    )


def _fake_response(content: str, finish_reason: str, completion_tokens: int | None = None):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(finish_reason=finish_reason, message=message)
    usage = SimpleNamespace(completion_tokens=completion_tokens) if completion_tokens is not None else None
    return SimpleNamespace(choices=[choice], usage=usage)


def _run(coro):
    return asyncio.run(coro)


def test_call_once_does_not_send_max_tokens():
    """Core behavior this module now relies on: no max_tokens is ever sent
    — llama.cpp's own --ctx-size/--parallel is the only ceiling (see the
    module docstring for why a client-side cap can't be picked correctly
    without knowing the live per-slot budget)."""
    client = _client()
    create = AsyncMock(return_value=_fake_response("<div data-bbox='0 0 10 10'>hi</div>", "stop"))
    client._client.chat.completions.create = create  # noqa: SLF001

    _run(client._call_once(b"fake-image-bytes"))

    assert "max_tokens" not in create.await_args.kwargs


def test_call_once_raises_ocr_truncated_error_reports_generated_token_count():
    """finish_reason='length' with no max_tokens ever sent means exactly
    one thing: llama.cpp's per-slot context window filled up — the message
    reports how many tokens it managed before that happened."""
    client = _client()
    create = AsyncMock(return_value=_fake_response("<div ...", "length", completion_tokens=5821))
    client._client.chat.completions.create = create  # noqa: SLF001 — test reaches into the wrapped SDK client on purpose

    with pytest.raises(OcrTruncatedError, match="generated 5821 tokens"):
        _run(client._call_once(b"fake-image-bytes"))

    create.assert_awaited_once()


def test_call_once_raises_ocr_truncated_error_without_usage_still_reports_context_limit():
    """If the server response doesn't include `usage`, there's no count to
    report, but the cause is unambiguous either way (see docstring) — must
    not crash trying to read a missing field."""
    client = _client()
    create = AsyncMock(return_value=_fake_response("<div ...", "length"))  # no completion_tokens
    client._client.chat.completions.create = create  # noqa: SLF001

    with pytest.raises(OcrTruncatedError, match="per-slot context window"):
        _run(client._call_once(b"fake-image-bytes"))


def test_call_once_returns_content_on_normal_stop():
    client = _client()
    create = AsyncMock(return_value=_fake_response("<div data-bbox='0 0 10 10'>hi</div>", "stop"))
    client._client.chat.completions.create = create  # noqa: SLF001

    result = _run(client._call_once(b"fake-image-bytes"))

    assert "hi" in result


def test_truncation_is_not_retried():
    """Retrying the identical request would run out of the same per-slot
    context room again — this must fail on the first attempt, not burn
    through max_attempts."""
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


def _bad_request_error(message: str = "the request exceeds the available context size") -> BadRequestError:
    request = httpx.Request("POST", "http://localhost:8080/v1/chat/completions")
    body = {"error": {"message": message}}
    response = httpx.Response(400, request=request, json=body)
    # Mirrors what the real openai SDK constructs internally (see
    # _base_client.py::_make_status_error_from_response): `message` here is
    # its OWN str(exc) rendering (f"Error code: 400 - {body}"), separate
    # from `body` (the parsed JSON) — tests below check we read `body`,
    # not this noisy default message.
    return BadRequestError(f"Error code: 400 - {body}", response=response, body=body)


def test_call_once_raises_ocr_bad_request_on_400():
    client = _client()
    create = AsyncMock(side_effect=_bad_request_error())
    client._client.chat.completions.create = create  # noqa: SLF001

    with pytest.raises(OcrBadRequestError, match="400"):
        _run(client._call_once(b"fake-image-bytes"))


def test_ocr_bad_request_error_message_is_the_servers_own_text_not_a_guess():
    """The core fix this guards against: the exam's stored error_message
    must be llama.cpp's actual `error.message` field, not the SDK's noisy
    `Error code: 400 - {...}` dict-repr default, and NOT a guessed
    explanation on our end (e.g. "probably context overflow") — raising
    --ctx-size and the error persisting is exactly the scenario where a
    guessed cause actively misleads."""
    client = _client()
    real_reason = "some other reason entirely, nothing to do with context size"
    create = AsyncMock(side_effect=_bad_request_error(real_reason))
    client._client.chat.completions.create = create  # noqa: SLF001

    with pytest.raises(OcrBadRequestError) as exc_info:
        _run(client._call_once(b"fake-image-bytes"))

    assert real_reason in str(exc_info.value)
    assert "Error code: 400 -" not in str(exc_info.value)  # not the SDK's raw dict-repr message


def test_ocr_bad_request_error_falls_back_to_str_exc_for_unexpected_body_shape():
    """If llama.cpp (or a proxy in front of it) ever returns a 400 that
    doesn't follow the {"error": {"message": ...}} shape, nothing should be
    silently swallowed — falls back to the SDK's own string."""
    client = _client()
    request = httpx.Request("POST", "http://localhost:8080/v1/chat/completions")
    response = httpx.Response(400, request=request, text="plain text error, not JSON")
    create = AsyncMock(side_effect=BadRequestError("Error code: 400 - plain text error, not JSON", response=response, body=None))
    client._client.chat.completions.create = create  # noqa: SLF001

    with pytest.raises(OcrBadRequestError, match="plain text error, not JSON"):
        _run(client._call_once(b"fake-image-bytes"))


def test_400_is_not_retried_and_not_treated_as_connectivity_error():
    """The core regression this guards against: a 400 must fail once,
    immediately, as a per-exam OcrBatchError — NOT get retried 3x and NOT
    get reclassified as OcrConnectivityError (which would incorrectly
    abort every other exam's in-flight work too, see ocr_pipeline.py's
    worker loop)."""
    client = _client()
    create = AsyncMock(side_effect=_bad_request_error())
    client._client.chat.completions.create = create  # noqa: SLF001

    with pytest.raises(OcrBadRequestError) as exc_info:
        _run(client.process_image(b"fake-image-bytes", 100, 200, 100, 200))

    assert create.await_count == 1
    assert not isinstance(exc_info.value, OcrConnectivityError)


def test_ocr_bad_request_error_is_an_ocr_batch_error():
    assert issubclass(OcrBadRequestError, OcrBatchError)
    assert not issubclass(OcrBadRequestError, OcrConnectivityError)

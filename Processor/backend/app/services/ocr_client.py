"""Wraps the `openai` async client pointed at a local llama.cpp server
(§2.3 step 3, §3.2 retry policy).

One model call per image — app/prompts.py's OCR_PROMPT asks for "this
image" (singular), not a batch. Async (not threads): the OCR pipeline
(app/services/ocr_pipeline.py) runs one coroutine per page, bounded by an
`asyncio.Semaphore` (`PROCESSOR_MAX_CONCURRENT_PAGES`), so N pages'
llama.cpp round-trips overlap on a single thread instead of each waiting
for the previous one to finish.

Retry policy (§3.2 — "cần quyết định retry bao nhiêu lần, có backoff hay
không"): network/timeout/5xx errors are retried with exponential backoff,
up to `ocr_max_attempts` (default 3, see .env.example); exhausting those
retries raises `OcrConnectivityError` — llama.cpp itself is presumed down.
Two other failure shapes are NOT retryable, both on the reasoning that at
temperature 0 the model is close to deterministic, so retrying is unlikely
to help and just burns time against a GPU-bound server: a response that
comes back but can't be parsed into layout blocks (`OcrBatchError`), and
one that got cut off at `max_tokens` before finishing
(`OcrTruncatedError` — see its docstring). Both propagate straight out of
`process_image`; the pipeline treats any exception here as this one exam's
problem (see ocr_pipeline.py's fail-fast design).
"""
from __future__ import annotations

import base64
import logging

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.prompts import OCR_PROMPT
from app.schemas.models import OcrPageResult
from app.services.postprocessing import parse_layout_response

logger = logging.getLogger(__name__)

_RETRYABLE_EXCEPTIONS = (APIConnectionError, APITimeoutError, APIStatusError)


class OcrBatchError(Exception):
    """The response came back but couldn't be parsed into layout blocks —
    a problem with this specific page's content/response, not with
    llama.cpp's availability. Handled per-exam (see
    worker_service.py::_process_exam)."""


class OcrConnectivityError(OcrBatchError):
    """All retries exhausted on network/timeout/5xx errors — signals
    llama.cpp itself is unreachable/unhealthy, not a problem specific to
    this exam. Handled differently from the base `OcrBatchError`: instead
    of failing just this one exam, the caller resets every in-flight
    'processing' exam back to 'pending' (see worker_service.py), since
    they're all hitting the same root cause."""


class OcrTruncatedError(OcrBatchError):
    """The model hit `max_tokens` before finishing (`finish_reason ==
    'length'`) — the response HTML is cut off mid-tag/mid-block, so
    handing it to parse_layout_response would silently produce an
    incomplete result (BeautifulSoup happily parses malformed/truncated
    HTML without raising) instead of surfacing the real problem. Not
    retryable: retrying the identical request with the same max_tokens
    would just truncate again at the same point — the fix is raising
    PROCESSOR_OCR_MAX_TOKENS, not trying again."""


class OcrClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        max_attempts: int,
        backoff_base_seconds: float,
        temperature: float,
        max_tokens: int,
    ):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._model = model
        self._max_attempts = max_attempts
        self._backoff_base_seconds = backoff_base_seconds
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def process_image(
        self, image_bytes: bytes, origin_width: int, origin_height: int, input_width: int, input_height: int
    ) -> OcrPageResult:
        """Runs OCR on one already-preprocessed page image (see
        preprocessing.py) and returns the full `pages.ocr_text` payload for
        it, ready to hand to repositories/pages.py::save_ocr_text."""
        retryer = retry(
            reraise=True,
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(
                multiplier=self._backoff_base_seconds, min=self._backoff_base_seconds
            ),
            retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )
        try:
            # tenacity's `retry()` detects that `_call_once` is a coroutine
            # function and awaits it correctly — no separate AsyncRetrying
            # import needed.
            html = await retryer(self._call_once)(image_bytes)
        except _RETRYABLE_EXCEPTIONS as exc:
            raise OcrConnectivityError(
                f"OCR call failed after {self._max_attempts} attempts: {exc}"
            ) from exc

        try:
            layouts = parse_layout_response(html, input_width, input_height)
        except Exception as exc:  # noqa: BLE001 — any parser bug surfaces as a page-level failure, not a crash
            raise OcrBatchError(f"Failed to parse OCR HTML response: {exc}. Raw: {html[:300]}") from exc

        return OcrPageResult(
            origin_width=origin_width,
            origin_height=origin_height,
            input_width=input_width,
            input_height=input_height,
            layouts=layouts,
        )

    async def _call_once(self, image_bytes: bytes) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": OCR_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }
            ],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise OcrTruncatedError(
                f"Model response truncated at max_tokens={self._max_tokens} "
                "(finish_reason='length') — increase PROCESSOR_OCR_MAX_TOKENS."
            )
        return choice.message.content or ""

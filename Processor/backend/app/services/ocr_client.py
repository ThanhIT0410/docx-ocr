"""Wraps the `openai` async client pointed at a local llama.cpp server
(§2.3 step 3, §3.2 retry policy).

One model call per image — app/prompts.py's OCR_PROMPT asks for "this
image" (singular), not a batch. Async (not threads): the OCR pipeline
(app/services/ocr_pipeline.py) runs a fixed pool of `PROCESSOR_MAX_CONCURRENT_PAGES`
worker coroutines pulling pages one at a time (see
app/services/page_pool.py), so N pages' llama.cpp round-trips overlap on a
single thread instead of each waiting for the previous one to finish.

No `max_tokens` is sent on the completion request — deliberately: llama.cpp
divides `--ctx-size` across `--parallel` slots (see
Processor/frontend/electron/llama-manager.js), so a fixed `max_tokens`
picked without knowing the live per-slot budget either truncates pages
that needed more room than the cap allowed (raising the cap doesn't help
if the REAL ceiling is the slot's remaining context) or, if set generously
high, does nothing useful at all once it exceeds that budget anyway. Since
the model's own context window is going to be the real limit regardless
of what `max_tokens` says, there is no config value that's simultaneously
"high enough to never truncate a legitimately long page" and "low enough
to matter" — so it's left unset and llama.cpp free-runs until it stops on
its own (EOS) or the slot's context fills up, whichever comes first (see
`OcrTruncatedError`'s docstring for the latter case). The one thing this
gives up is a circuit-breaker against a degenerate repetition loop tying
up a slot until context actually fills — `ocr_temperature` being slightly
above 0 (not pure greedy) is this file's mitigation for that, not a cap.

Retry policy (§3.2 — "cần quyết định retry bao nhiêu lần, có backoff hay
không"): network/timeout/5xx errors are retried with exponential backoff,
up to `ocr_max_attempts` (default 3, see .env.example); exhausting those
retries raises `OcrConnectivityError` — llama.cpp itself is presumed down.
Three other failure shapes are NOT retryable, on the reasoning that retrying
the identical request would just fail the same way again: a response that
comes back but can't be parsed into layout blocks (`OcrBatchError`), one
that ran out of room before finishing (`OcrTruncatedError` — see its
docstring), and any HTTP 400 from llama.cpp itself (`OcrBadRequestError` —
see its docstring for why this must NOT be lumped in with
`OcrConnectivityError`, and why its message is the server's own words, not
a guess at the reason). All three propagate straight out of
`process_image`; the pipeline treats any of them as this one exam's
problem (see ocr_pipeline.py's fail-fast design) — only
`OcrConnectivityError` gets the pipeline-wide treatment.
"""
from __future__ import annotations

import base64
import logging

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, BadRequestError
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

# BadRequestError (HTTP 400) is deliberately NOT in here even though it's an
# APIStatusError subclass — see OcrBadRequestError's docstring.
# _call_once catches it and re-raises as OcrBadRequestError instead, which
# doesn't match this tuple — tenacity's retry predicate (built from this
# tuple, see process_image) sees that mismatch and lets it straight through
# without retrying.
_RETRYABLE_EXCEPTIONS = (APIConnectionError, APITimeoutError, APIStatusError)


class OcrBatchError(Exception):
    """The response came back but couldn't be parsed into layout blocks —
    a problem with this specific page's content/response, not with
    llama.cpp's availability. Handled per-exam (see
    ocr_pipeline.py's worker loop)."""


class OcrConnectivityError(OcrBatchError):
    """All retries exhausted on network/timeout/5xx errors — signals
    llama.cpp itself is unreachable/unhealthy, not a problem specific to
    this exam. Handled differently from the base `OcrBatchError`: instead
    of failing the exam whose page happened to hit this, the pipeline's
    worker loop treats it as a shared infrastructure signal — it clears the
    same "healthy" flag the periodic health-monitor task uses, stopping
    every worker immediately rather than waiting for that monitor's next
    scheduled check (see ocr_pipeline.py's `_worker`) — since a dead
    llama.cpp server would otherwise surface as several concurrent pages
    from UNRELATED exams all failing at once, which isn't really "many
    exams broke", it's one root cause."""


class OcrBadRequestError(OcrBatchError):
    """llama.cpp rejected the request outright with HTTP 400. `str(self)`
    is the server's OWN error message (see `_extract_llamacpp_error` —
    NOT a guess at the reason on this end. A common cause historically is
    the prompt (image tokens + OCR_PROMPT text) plus `max_tokens` exceeding
    the server's configured context window (`--ctx-size` divided across
    `--parallel` slots, see Processor/frontend/electron/llama-manager.js)
    — but that's one example, not a certainty, and raising `--ctx-size`
    without the error going away means it's something else; read the
    message rather than assuming.

    Deliberately NOT an `OcrConnectivityError`, even though both ultimately
    come from the same `openai` HTTP call: llama.cpp is up and responding
    just fine here, it's refusing this ONE request — treating it as "the
    whole server is down" (which clears ocr_pipeline.py's shared health
    flag and aborts every other exam's in-flight work too) would be wrong
    and needlessly disruptive. Not retryable either — the identical request
    would deterministically get rejected the same way again."""


class OcrTruncatedError(OcrBatchError):
    """The model stopped generating before finishing (`finish_reason ==
    'length'`) — the response HTML is cut off mid-tag/mid-block, so
    handing it to parse_layout_response would silently produce an
    incomplete result (BeautifulSoup happily parses malformed/truncated
    HTML without raising) instead of surfacing the real problem.

    Since no `max_tokens` is ever sent (see this module's docstring), this
    means exactly one thing: llama.cpp's per-slot context window
    (`--ctx-size` divided across `--parallel` slots, see
    Processor/frontend/electron/llama-manager.js) filled up before the
    model reached a natural stopping point — either a genuinely
    long/dense page, or a repetition loop that ran all the way to the
    slot's limit instead of stopping on its own. Not retryable: the
    identical request would run out of the same room again at the same
    point. The fix is a bigger `--ctx-size` and/or fewer `--parallel`
    slots so each one has more room — there is no equivalent app-side
    setting to raise anymore."""


def _extract_llamacpp_error(exc: BadRequestError) -> str:
    """Pulls the server's own `error.message` out of the response body
    instead of falling back to `str(exc)` — the `openai` SDK's default
    `__str__` for a status error is `f"Error code: 400 - {body}"`, i.e. the
    ENTIRE parsed JSON dict rendered as Python repr, which does technically
    contain the real message but buries it in noise (and looks enough like
    a "here's what went wrong" summary that it invites guessing at the
    cause instead of reading it). llama.cpp's OpenAI-compatible error
    responses follow the standard `{"error": {"message": "...", ...}}`
    shape; if that shape isn't there for some reason, falls back to the
    SDK's own string so nothing is silently swallowed."""
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(body.get("message"), str):
            return body["message"]
    return str(exc)


class OcrClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        max_attempts: int,
        backoff_base_seconds: float,
        temperature: float,
    ):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._model = model
        self._max_attempts = max_attempts
        self._backoff_base_seconds = backoff_base_seconds
        self._temperature = temperature

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
        try:
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
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
        except BadRequestError as exc:
            raise OcrBadRequestError(f"llama.cpp rejected the request (400): {_extract_llamacpp_error(exc)}") from exc
        choice = response.choices[0]
        if choice.finish_reason == "length":
            generated = getattr(response.usage, "completion_tokens", None) if response.usage else None
            detail = f"generated {generated} tokens before" if generated is not None else "stopped"
            raise OcrTruncatedError(
                f"Model response truncated (finish_reason='length') — {detail} running out of "
                "llama.cpp's per-slot context window (--ctx-size / --parallel)."
            )
        return choice.message.content or ""

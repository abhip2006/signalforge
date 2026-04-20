"""Signal source protocol + shared HTTP helpers.

Cloud egress (e.g. Hugging Face Spaces) hits shared IPs, so public endpoints
may throttle or reject requests that look anonymous. Two hardening measures
live in this module:

1. A descriptive ``DEFAULT_USER_AGENT`` is injected on every request unless the
   caller passes its own ``User-Agent`` header. This mirrors the SEC EDGAR
   contract (see ``sec_edgar.py``) where a polite UA is mandatory.
2. ``http_get_json`` wraps the fetch in a tenacity exponential-backoff retry
   loop that kicks in on HTTP 429 and 5xx responses plus transport errors.
   Non-retryable 4xx errors propagate immediately.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from signalforge.config import Env
from signalforge.models import Signal

DEFAULT_USER_AGENT = "signalforge/0.2 (+https://github.com/abhip2006/signalforge)"

# HTTP status codes that should trigger a retry.
_RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# Transport-level errors that should trigger a retry (transient network issues).
_RETRY_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    httpx.TransportError,
    httpx.TimeoutException,
)


@dataclass(frozen=True)
class SourceContext:
    env: Env
    http: httpx.AsyncClient


class SignalSource(Protocol):
    """Every signal source implements this protocol."""

    name: str

    async def collect(self, ctx: SourceContext, source_config: dict[str, Any]) -> list[Signal]: ...


def _should_retry(exc: BaseException) -> bool:
    """Retry on transport errors + 429/5xx responses. Non-retryable 4xx bubble up."""
    if isinstance(exc, _RETRY_TRANSPORT_ERRORS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRY_STATUS
    return False


def _merge_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Always stamp a User-Agent unless the caller supplied one."""
    merged: dict[str, str] = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        # Preserve caller-supplied casing; do a case-insensitive overwrite check.
        lowered = {k.lower() for k in headers}
        if "user-agent" in lowered:
            merged.pop("User-Agent", None)
        merged.update(headers)
    return merged


async def http_get_json(
    ctx: SourceContext,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    max_attempts: int = 4,
) -> Any:
    """GET ``url`` and parse JSON, with UA injection + exponential-backoff retry.

    Retries up to ``max_attempts`` times on 429, 5xx, and transport errors
    (total wait bounded to ~30s with the default 1s base / 10s cap schedule).
    Raises for non-retryable 4xx so callers can classify as hard failures.
    """
    effective_headers = _merge_headers(headers)

    async def _do_fetch() -> Any:
        response = await ctx.http.get(url, headers=effective_headers, timeout=timeout)
        response.raise_for_status()
        return response.json()

    try:
        async for attempt in AsyncRetrying(
            reraise=True,
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1.0, min=1.0, max=10.0),
            retry=retry_if_exception(_should_retry),
        ):
            with attempt:
                return await _do_fetch()
    except RetryError as e:  # pragma: no cover — reraise=True makes this unreachable
        raise e.last_attempt.exception() from e
    # Unreachable but satisfies the type checker.
    return None


def warn(source: str, target: str, err: Exception) -> None:
    """Stderr warning for signal-source failures. Silent failures are worse than noisy ones."""
    print(
        f"[signalforge:{source}] warn: {target} — {err.__class__.__name__}: {err}",
        file=sys.stderr,
    )

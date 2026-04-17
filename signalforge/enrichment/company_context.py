"""Lightweight company-context enrichment.

Pulls the homepage + /about + /careers (best effort), strips to text, and
returns a short context block the research-brief agent can use.

If FIRECRAWL_API_KEY is set, uses Firecrawl's `/scrape` endpoint for cleaner
extraction. Otherwise falls back to raw `httpx.get` + HTML tag stripping —
good enough for personalization on About-style pages.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from signalforge.config import Env

DEFAULT_PATHS = ("/", "/about", "/company", "/careers")
# naive but effective: strip scripts/styles/tags, collapse whitespace
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class CompanyContext:
    domain: str
    title: str | None
    text: str                     # truncated
    urls_seen: list[str] = field(default_factory=list)
    source: str = "httpx"         # "firecrawl" | "httpx"


async def fetch_company_context(
    domain: str,
    env: Env,
    *,
    paths: tuple[str, ...] = DEFAULT_PATHS,
    max_chars: int = 2500,
    timeout: float = 10.0,
) -> CompanyContext | None:
    """Try Firecrawl first if keyed; fall back to raw httpx.

    Returns None only on total failure (no page fetched). Otherwise returns
    a CompanyContext even if text is short.
    """
    if not domain or domain.endswith(".unknown") or domain.endswith(".github"):
        return None

    if env.firecrawl_api_key:
        ctx = await _fetch_firecrawl(domain, env.firecrawl_api_key, max_chars, timeout)
        if ctx is not None:
            return ctx
        # fall through to raw if Firecrawl fails

    return await _fetch_raw(domain, paths, max_chars, timeout)


async def _fetch_firecrawl(
    domain: str, api_key: str, max_chars: int, timeout: float
) -> CompanyContext | None:
    url = f"https://{domain}/"
    body = {"url": url, "formats": ["markdown"], "onlyMainContent": True}
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                "https://api.firecrawl.dev/v1/scrape", json=body, headers=headers
            )
            r.raise_for_status()
            data: Any = r.json()
    except Exception:  # noqa: BLE001
        return None

    payload = data.get("data") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return None
    md = (payload.get("markdown") or "")[:max_chars]
    meta = payload.get("metadata") or {}
    return CompanyContext(
        domain=domain,
        title=meta.get("title"),
        text=_squash(md),
        urls_seen=[url],
        source="firecrawl",
    )


async def _fetch_raw(
    domain: str, paths: tuple[str, ...], max_chars: int, timeout: float
) -> CompanyContext | None:
    accum: list[str] = []
    seen: list[str] = []
    title: str | None = None

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "signalforge/0.1 (+research)"},
    ) as client:
        tasks = [_fetch_one(client, f"https://{domain}{p}") for p in paths]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for res in results:
        if isinstance(res, Exception) or res is None:
            continue
        url, html = res
        seen.append(url)
        if title is None:
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            if m:
                title = _squash(m.group(1))[:200]
        accum.append(_html_to_text(html))
        if sum(len(a) for a in accum) >= max_chars:
            break

    if not seen:
        return None

    text = _squash(" ".join(accum))[:max_chars]
    return CompanyContext(domain=domain, title=title, text=text, urls_seen=seen, source="httpx")


async def _fetch_one(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    try:
        r = await client.get(url)
        if r.status_code >= 400 or "text/html" not in r.headers.get("content-type", "").lower():
            return None
        return url, r.text
    except Exception:  # noqa: BLE001
        return None


def _html_to_text(html: str) -> str:
    without_scripts = _SCRIPT_RE.sub(" ", html)
    without_tags = _TAG_RE.sub(" ", without_scripts)
    return _squash(without_tags)


def _squash(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()

"""Disk-backed cache for :func:`company_context.fetch_company_context`.

Rationale:
    The cloud deployment (Hugging Face Spaces) routes every visitor's scrape
    through a shared egress IP. Hitting the same prospect's homepage on every
    page load will both burn the target site's rate limits and slow the UI.
    A 7-day TTL is plenty — corporate About / careers pages change rarely,
    and a stale entry is still far better than a 403.

Design:
    - One SQLite file under ``data/company_context_cache/cache.sqlite``.
    - Keyed by normalised domain (lowercased, stripped of ``www.``).
    - Stores the serialised :class:`CompanyContext` as JSON plus an
      ``expires_at`` epoch timestamp. Expired rows are treated as a miss but
      are only physically deleted on the next ``set`` for that key.
    - Cache location can be overridden with
      ``SIGNALFORGE_COMPANY_CONTEXT_CACHE`` for tests and bespoke deployments.
    - All database access is routed through a short synchronous block run in a
      thread via ``asyncio.to_thread`` so callers in async code don't block.

Failures in the cache layer are *never* fatal — read/write errors are caught
and surfaced as a miss, so a broken cache file never breaks the pipeline.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from signalforge.enrichment.company_context import CompanyContext

DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


def _default_cache_path() -> Path:
    override = os.environ.get("SIGNALFORGE_COMPANY_CONTEXT_CACHE")
    if override:
        return Path(override).expanduser().resolve()
    data_dir = Path(os.environ.get("SIGNALFORGE_DATA_DIR", "./data")).expanduser().resolve()
    return data_dir / "company_context_cache" / "cache.sqlite"


def _normalise_domain(domain: str) -> str:
    d = (domain or "").strip().lower()
    if d.startswith("www."):
        d = d[4:]
    return d


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # ``check_same_thread=False`` is safe because we serialise access via
    # ``asyncio.to_thread`` (one operation per thread) and never share a
    # connection across calls.
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_context_cache (
            domain TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            expires_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _sync_get(path: Path, domain: str, now: float) -> CompanyContext | None:
    key = _normalise_domain(domain)
    if not key:
        return None
    try:
        conn = _connect(path)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT payload, expires_at FROM company_context_cache WHERE domain = ?",
            (key,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()

    if row is None:
        return None
    payload_text, expires_at = row
    if expires_at <= now:
        return None
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return None

    # Deferred import: company_context imports this module, and top-level
    # reciprocation would create a cycle at import time.
    from signalforge.enrichment.company_context import CompanyContext

    try:
        return CompanyContext(
            domain=payload["domain"],
            title=payload.get("title"),
            text=payload.get("text", ""),
            urls_seen=list(payload.get("urls_seen", [])),
            source=payload.get("source", "httpx"),
        )
    except (KeyError, TypeError):
        return None


def _sync_set(path: Path, domain: str, ctx: CompanyContext, ttl_seconds: int) -> None:
    key = _normalise_domain(domain)
    if not key:
        return
    try:
        conn = _connect(path)
    except sqlite3.Error:
        return
    try:
        payload = json.dumps(asdict(ctx))
        expires_at = time.time() + max(ttl_seconds, 0)
        conn.execute(
            """
            INSERT INTO company_context_cache (domain, payload, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                payload = excluded.payload,
                expires_at = excluded.expires_at
            """,
            (key, payload, expires_at),
        )
        conn.commit()
    except sqlite3.Error:
        return
    finally:
        conn.close()


async def get_cached(
    domain: str, *, cache_path: Path | None = None
) -> CompanyContext | None:
    """Return the cached CompanyContext for ``domain`` or ``None`` on miss."""
    path = cache_path or _default_cache_path()
    return await asyncio.to_thread(_sync_get, path, domain, time.time())


async def set_cached(
    domain: str,
    ctx: CompanyContext,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    cache_path: Path | None = None,
) -> None:
    """Persist ``ctx`` under ``domain`` with the given TTL. Failures are swallowed."""
    path = cache_path or _default_cache_path()
    await asyncio.to_thread(_sync_set, path, domain, ctx, ttl_seconds)

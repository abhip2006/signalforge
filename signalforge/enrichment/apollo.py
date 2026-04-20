"""Apollo contact enrichment.

Resolves up to 3 Contact records per account by querying Apollo's
`/people/search` endpoint with a title + domain filter. Uses a two-stage
"title waterfall": exact-match first, then fuzzy `contains` matching against
the requested target titles.

Design notes
------------
- **Graceful no-op.** If `APOLLO_API_KEY` is missing we log once and return
  `[]`. The pipeline always runs to completion with whatever's available.
- **Opt-in by default.** Apollo's free tier has tight quota; fanning out to
  every account in a run can exhaust a month's credits in one pipeline
  invocation. Callers must set `APOLLO_ENABLED=1` (or pass an explicit flag)
  before this module ever gets invoked — see `pipeline.run_pipeline`.
- **Disk cache.** Results are cached to `data/apollo_cache/<domain>.json`
  for 14 days so re-running the pipeline over the same domains doesn't
  burn credits. Only non-empty result sets are cached; a miss re-queries.
- **Retries.** `httpx` HTTP + `tenacity` with exponential backoff on
  transient failures (5xx, network). 429 responses are propagated without
  retry — Apollo's quota is expensive to poll.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from signalforge.config import Env
from signalforge.models import Contact

LOG = logging.getLogger(__name__)

APOLLO_URL = "https://api.apollo.io/api/v1/mixed_people/search"
CACHE_TTL_SECONDS = 14 * 24 * 60 * 60  # 14 days
MAX_CONTACTS_PER_ACCOUNT = 3
REQUEST_TIMEOUT = 20.0


async def fetch_contacts(
    domain: str,
    target_titles: list[str],
    env: Env,
    *,
    cache_dir: Path | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list[Contact]:
    """Return up to 3 contacts for `domain` matching one of `target_titles`.

    Always returns a list (possibly empty). Never raises — logs and degrades.
    """
    if not env.apollo_api_key:
        LOG.info("apollo: APOLLO_API_KEY unset — returning [] for %s", domain)
        return []
    if not domain or not target_titles:
        return []

    cache_root = cache_dir or (env.data_dir / "apollo_cache")
    cached = _load_cache(cache_root, domain)
    if cached is not None:
        return cached[:MAX_CONTACTS_PER_ACCOUNT]

    try:
        if http_client is not None:
            contacts = await _query_with_waterfall(
                http_client, env.apollo_api_key, domain, target_titles
            )
        else:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                contacts = await _query_with_waterfall(
                    client, env.apollo_api_key, domain, target_titles
                )
    except Exception as exc:  # noqa: BLE001 — last-resort safety net
        LOG.warning("apollo: fetch failed for %s — %s: %s", domain, exc.__class__.__name__, exc)
        return []

    if contacts:
        _write_cache(cache_root, domain, contacts)
    return contacts[:MAX_CONTACTS_PER_ACCOUNT]


# --- HTTP waterfall -----------------------------------------------------


async def _query_with_waterfall(
    client: httpx.AsyncClient,
    api_key: str,
    domain: str,
    target_titles: list[str],
) -> list[Contact]:
    """Try exact-match titles first, then widen to fuzzy `contains` match."""
    # Stage 1: exact. Apollo treats `person_titles` as OR. Use all titles as-is.
    exact = await _search(client, api_key, domain, target_titles)
    exact_hits = [c for c in exact if _title_matches_exact(c.title, target_titles)]
    if len(exact_hits) >= MAX_CONTACTS_PER_ACCOUNT:
        return exact_hits[:MAX_CONTACTS_PER_ACCOUNT]

    # Stage 2: fuzzy. Fall back to the raw search result list, filling the
    # remaining slots with fuzzy-contains matches we hadn't already picked.
    remaining = MAX_CONTACTS_PER_ACCOUNT - len(exact_hits)
    already = {(c.full_name, c.title) for c in exact_hits}
    fuzzy_hits = [
        c for c in exact
        if (c.full_name, c.title) not in already
        and _title_matches_fuzzy(c.title, target_titles)
    ]
    return exact_hits + fuzzy_hits[:remaining]


async def _search(
    client: httpx.AsyncClient,
    api_key: str,
    domain: str,
    target_titles: list[str],
) -> list[Contact]:
    payload: dict[str, Any] = {
        "q_organization_domains": domain,
        "person_titles": target_titles,
        "page": 1,
        "per_page": 10,
    }
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key,
    }
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
        retry=retry_if_exception_type((httpx.TransportError, _TransientHTTPError)),
        reraise=True,
    ):
        with attempt:
            response = await client.post(
                APOLLO_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT
            )
            if 500 <= response.status_code < 600:
                raise _TransientHTTPError(f"{response.status_code} from apollo")
            response.raise_for_status()
            data = response.json()
    return _parse_contacts(data)


class _TransientHTTPError(Exception):
    """5xx — worth a retry."""


def _parse_contacts(payload: Any) -> list[Contact]:
    """Apollo `/people/search` returns {"people": [...], "contacts": [...]}.

    Both shapes carry first_name/last_name/title/email/linkedin_url. We union.
    """
    people: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ("people", "contacts"):
            val = payload.get(key) or []
            if isinstance(val, list):
                people.extend(p for p in val if isinstance(p, dict))

    out: list[Contact] = []
    seen: set[tuple[str, str]] = set()
    for person in people:
        full = _full_name(person)
        title = (person.get("title") or "").strip()
        if not full or not title:
            continue
        key = (full.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        email_raw = person.get("email") or None
        # Apollo sometimes returns the literal string "email_not_unlocked@domain.com"
        email = email_raw if email_raw and "not_unlocked" not in email_raw else None
        out.append(
            Contact(
                full_name=full,
                title=title,
                linkedin_url=person.get("linkedin_url") or None,
                email=email,
                source="apollo",
            )
        )
    return out


def _full_name(person: dict[str, Any]) -> str:
    name = (person.get("name") or "").strip()
    if name:
        return name
    first = (person.get("first_name") or "").strip()
    last = (person.get("last_name") or "").strip()
    joined = f"{first} {last}".strip()
    return joined


def _title_matches_exact(title: str, targets: list[str]) -> bool:
    t = title.strip().lower()
    return any(t == tgt.strip().lower() for tgt in targets)


def _title_matches_fuzzy(title: str, targets: list[str]) -> bool:
    t = title.strip().lower()
    return any(tgt.strip().lower() in t or t in tgt.strip().lower() for tgt in targets)


# --- Disk cache ----------------------------------------------------------


def _cache_path(cache_root: Path, domain: str) -> Path:
    safe = domain.replace("/", "_").replace("\\", "_").strip().lower()
    return cache_root / f"{safe}.json"


def _load_cache(cache_root: Path, domain: str) -> list[Contact] | None:
    path = _cache_path(cache_root, domain)
    if not path.exists():
        return None
    try:
        age = time.time() - path.stat().st_mtime
        if age > CACHE_TTL_SECONDS:
            return None
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("apollo: cache read failed for %s — %s", domain, exc)
        return None
    if not isinstance(raw, list):
        return None
    contacts: list[Contact] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            contacts.append(
                Contact(
                    full_name=entry["full_name"],
                    title=entry["title"],
                    linkedin_url=entry.get("linkedin_url"),
                    email=entry.get("email"),
                    source="apollo",
                )
            )
        except (KeyError, TypeError):
            continue
    return contacts


def _write_cache(cache_root: Path, domain: str, contacts: list[Contact]) -> None:
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        path = _cache_path(cache_root, domain)
        serialisable = [
            {
                "full_name": c.full_name,
                "title": c.title,
                "linkedin_url": c.linkedin_url,
                "email": c.email,
                "source": c.source,
            }
            for c in contacts
        ]
        path.write_text(json.dumps(serialisable, indent=2, sort_keys=True))
    except OSError as exc:
        LOG.warning("apollo: cache write failed for %s — %s", domain, exc)


# Exposed for pipeline.py — a small helper so callers don't import asyncio here.
async def fetch_contacts_for_domains(
    domains: list[str], target_titles: list[str], env: Env, *, concurrency: int = 4
) -> dict[str, list[Contact]]:
    """Batch helper: fan out Apollo lookups with bounded concurrency."""
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(d: str) -> tuple[str, list[Contact]]:
        async with sem:
            return d, await fetch_contacts(d, target_titles, env)

    results = await asyncio.gather(*(one(d) for d in domains), return_exceptions=False)
    return dict(results)

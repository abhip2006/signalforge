"""Greenhouse public job board signals. No auth required."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from signalforge.models import Signal, SignalKind
from signalforge.signals.base import SourceContext, http_get_json
from signalforge.signals.company_registry import BoardEntry, resolve_list

# Parallel board fetches. Greenhouse has no published rate limit for
# boards-api but we stay polite to survive big pool expansions.
_BOARD_CONCURRENCY = 25


class GreenhouseSource:
    name = "greenhouse"

    async def collect(
        self, ctx: SourceContext, source_config: dict[str, Any]
    ) -> list[Signal]:
        if not source_config.get("enabled", True):
            return []
        boards = resolve_list(source_config.get("boards", []) or [])
        keywords: list[str] = [k.lower() for k in source_config.get("hiring_keywords", [])]

        sem = asyncio.Semaphore(_BOARD_CONCURRENCY)

        async def _fetch_one(entry: BoardEntry) -> list[Signal]:
            async with sem:
                try:
                    url = f"https://boards-api.greenhouse.io/v1/boards/{entry.token}/jobs?content=true"
                    data = await http_get_json(ctx, url)
                except Exception as e:  # noqa: BLE001 — source can be flaky; non-fatal
                    return [_err_signal(entry.domain, entry.name, str(e))]
            return _parse_board(entry, data, keywords)

        results = await asyncio.gather(*(_fetch_one(e) for e in boards))
        out: list[Signal] = []
        for batch in results:
            out.extend(batch)
        return out


def _parse_board(entry: BoardEntry, data: dict, keywords: list[str]) -> list[Signal]:
    out: list[Signal] = []
    jobs = data.get("jobs") or []
    for job in jobs:
        title = (job.get("title") or "").strip()
        if not title:
            continue
        if keywords and not _keyword_match(title, keywords):
            continue
        posted = _parse_ts(job.get("updated_at") or job.get("first_published"))
        out.append(
            Signal(
                kind=SignalKind.HIRING,
                source="greenhouse",
                company_domain=entry.domain,
                company_name=entry.name,
                title=f"Hiring: {title}",
                url=job.get("absolute_url"),
                observed_at=posted or datetime.now(UTC),
                payload={
                    "board_token": entry.token,
                    "job_id": job.get("id"),
                    "location": (job.get("location") or {}).get("name"),
                    "department_ids": [d.get("id") for d in job.get("departments", [])],
                },
                strength=_hiring_strength(title, keywords),
            )
        )
    return out


def _err_signal(domain: str, name: str, msg: str) -> Signal:
    return Signal(
        kind=SignalKind.HIRING,
        source="greenhouse",
        company_domain=domain,
        company_name=name,
        title=f"[source-error] {msg[:120]}",
        strength=0.0,
    )


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _keyword_match(title: str, keywords: list[str]) -> bool:
    t = title.lower()
    return any(k in t for k in keywords)


def _hiring_strength(title: str, keywords: list[str]) -> float:
    """SDR/BDR/GTM/growth hires are stronger buying signals than generic eng roles."""
    t = title.lower()
    hot = ("sdr", "bdr", "gtm", "go-to-market", "sales development", "head of", "vp ")
    if any(h in t for h in hot):
        return 0.9
    if any(k in t for k in keywords):
        return 0.7
    return 0.4

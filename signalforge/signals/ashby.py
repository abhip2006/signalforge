"""Ashby public job board signals. No auth required."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from signalforge.models import Signal, SignalKind
from signalforge.signals.base import SourceContext, http_get_json
from signalforge.signals.company_registry import BoardEntry, resolve_list

_BOARD_CONCURRENCY = 25


class AshbySource:
    name = "ashby"

    async def collect(
        self, ctx: SourceContext, source_config: dict[str, Any]
    ) -> list[Signal]:
        if not source_config.get("enabled", True):
            return []
        boards = resolve_list(source_config.get("boards", []) or [])
        keywords: list[str] = [k.lower() for k in source_config.get(
            "hiring_keywords",
            ["sdr", "bdr", "gtm", "go-to-market", "sales development", "growth", "revenue"],
        )]

        sem = asyncio.Semaphore(_BOARD_CONCURRENCY)

        async def _fetch_one(entry: BoardEntry) -> list[Signal]:
            async with sem:
                try:
                    url = f"https://api.ashbyhq.com/posting-api/job-board/{entry.token}"
                    data = await http_get_json(ctx, url)
                except Exception as e:  # noqa: BLE001
                    return [_err_signal(entry.domain, entry.name, str(e))]
            return _parse_board(entry, data, keywords)

        results = await asyncio.gather(*(_fetch_one(e) for e in boards))
        out: list[Signal] = []
        for batch in results:
            out.extend(batch)
        return out


def _parse_board(entry: BoardEntry, data: Any, keywords: list[str]) -> list[Signal]:
    out: list[Signal] = []
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    for job in jobs:
        title = (job.get("title") or "").strip()
        if not title:
            continue
        if keywords and not any(k in title.lower() for k in keywords):
            continue
        posted = _parse_ts(job.get("publishedAt") or job.get("updatedAt"))
        out.append(
            Signal(
                kind=SignalKind.HIRING,
                source="ashby",
                company_domain=entry.domain,
                company_name=entry.name,
                title=f"Hiring: {title}",
                url=job.get("jobUrl") or job.get("applyUrl"),
                observed_at=posted or datetime.now(UTC),
                payload={
                    "slug": entry.token,
                    "location": job.get("locationName"),
                    "department": job.get("department"),
                    "employment_type": job.get("employmentType"),
                },
                strength=_strength(title),
            )
        )
    return out


def _strength(title: str) -> float:
    t = title.lower()
    if any(x in t for x in ("head of", "vp ", "director of")):
        return 0.9
    if any(x in t for x in ("sdr", "bdr", "gtm", "sales development")):
        return 0.85
    return 0.5


def _err_signal(domain: str, name: str, msg: str) -> Signal:
    return Signal(
        kind=SignalKind.HIRING,
        source="ashby",
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

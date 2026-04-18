"""Hacker News Algolia signal source — free, no auth, real-time.

For each configured company we issue a search against HN's Algolia index
and emit PRODUCT_LAUNCH or PRESS signals for front-page stories mentioning
the company. "Show HN" stories are weighted highest — they're literal
product launches from the company itself.

API docs: https://hn.algolia.com/api
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from signalforge.models import Signal, SignalKind
from signalforge.signals.base import SourceContext, http_get_json, warn
from signalforge.signals.company_registry import BoardEntry, resolve_list

_QUERY_CONCURRENCY = 20


class HackerNewsSource:
    name = "hackernews"

    async def collect(
        self, ctx: SourceContext, source_config: dict[str, Any]
    ) -> list[Signal]:
        if not source_config.get("enabled", True):
            return []
        companies = resolve_list(source_config.get("companies", []) or [])
        lookback_days = int(source_config.get("lookback_days", 60))
        min_points = int(source_config.get("min_points", 20))
        results_per_company = int(source_config.get("results_per_company", 5))

        # Algolia accepts a unix timestamp lower bound.
        cutoff_ts = int(datetime.now(UTC).timestamp()) - lookback_days * 86400

        sem = asyncio.Semaphore(_QUERY_CONCURRENCY)

        async def _fetch_one(entry: BoardEntry) -> list[Signal]:
            query = entry.name if len(entry.name) >= 4 else entry.token
            url = (
                "https://hn.algolia.com/api/v1/search_by_date"
                f"?query={query.replace(' ', '+')}"
                f"&tags=story"
                f"&numericFilters=created_at_i>{cutoff_ts},points>={min_points}"
                f"&hitsPerPage={results_per_company}"
            )
            async with sem:
                try:
                    data = await http_get_json(ctx, url, timeout=15.0)
                except Exception as e:  # noqa: BLE001
                    warn("hackernews", entry.name, e)
                    return []
            return _parse_hits(entry, data)

        per_company = await asyncio.gather(*(_fetch_one(e) for e in companies))
        out: list[Signal] = []
        for batch in per_company:
            out.extend(batch)
        return out


def _parse_hits(entry: BoardEntry, data: Any) -> list[Signal]:
    if not isinstance(data, dict):
        return []
    hits = data.get("hits", []) or []
    name_lower = entry.name.lower()
    out: list[Signal] = []
    for hit in hits:
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        # Loose relevance filter — Algolia is noisy on common names.
        if name_lower not in title.lower() and name_lower not in (
            (hit.get("story_text") or "")[:500].lower()
        ):
            continue
        points = int(hit.get("points") or 0)
        story_url = hit.get("url") or (
            f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        )
        is_launch = title.lower().startswith(("show hn", "launch hn"))
        kind = SignalKind.PRODUCT_LAUNCH if is_launch else SignalKind.PRESS
        strength = min(0.95, 0.4 + min(points, 400) / 500.0)
        if is_launch:
            strength = min(0.95, strength + 0.1)
        observed = _parse_ts(hit.get("created_at")) or datetime.now(UTC)
        out.append(
            Signal(
                kind=kind,
                source="hackernews",
                company_domain=entry.domain,
                company_name=entry.name,
                title=(f"HN: {title}")[:200],
                url=story_url,
                observed_at=observed,
                payload={
                    "points": points,
                    "num_comments": hit.get("num_comments"),
                    "object_id": hit.get("objectID"),
                    "is_show_hn": is_launch,
                },
                strength=strength,
            )
        )
    return out


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None

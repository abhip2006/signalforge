"""Lever public job board signals. No auth required."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from signalforge.models import Signal, SignalKind
from signalforge.signals.base import SourceContext, http_get_json, warn
from signalforge.signals.company_registry import resolve_list


class LeverSource:
    name = "lever"

    async def collect(
        self, ctx: SourceContext, source_config: dict[str, Any]
    ) -> list[Signal]:
        if not source_config.get("enabled", True):
            return []
        boards = resolve_list(source_config.get("boards", []) or [])
        keywords: list[str] = [k.lower() for k in source_config.get(
            "hiring_keywords",
            ["sdr", "bdr", "gtm", "go-to-market", "sales development", "growth"],
        )]
        out: list[Signal] = []
        for entry in boards:
            slug = entry.token
            try:
                url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
                postings = await http_get_json(ctx, url)
            except Exception as e:  # noqa: BLE001
                warn("lever", slug, e)
                continue
            if not isinstance(postings, list):
                continue
            for job in postings:
                title = (job.get("text") or "").strip()
                if not title:
                    continue
                if keywords and not any(k in title.lower() for k in keywords):
                    continue
                created_ms = job.get("createdAt")
                observed = (
                    datetime.fromtimestamp(created_ms / 1000, tz=UTC)
                    if isinstance(created_ms, (int, float))
                    else datetime.now(UTC)
                )
                categories = job.get("categories") or {}
                out.append(
                    Signal(
                        kind=SignalKind.HIRING,
                        source="lever",
                        company_domain=entry.domain,
                        company_name=entry.name,
                        title=f"Hiring: {title}",
                        url=job.get("hostedUrl") or job.get("applyUrl"),
                        observed_at=observed,
                        payload={
                            "slug": slug,
                            "team": categories.get("team"),
                            "location": categories.get("location"),
                            "commitment": categories.get("commitment"),
                        },
                        strength=0.8 if any(x in title.lower() for x in ("sdr", "bdr", "gtm")) else 0.5,
                    )
                )
        return out

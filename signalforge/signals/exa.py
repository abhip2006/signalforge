"""Exa neural-search press + funding signals.

Runs per-company queries against Exa's `/search?type=neural` + `/contents`
endpoints and emits FUNDING / PRESS / EXEC_CHANGE signals with URL citations.

Gated by EXA_API_KEY. Graceful no-op if unset.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from signalforge.models import Signal, SignalKind
from signalforge.signals.base import SourceContext, warn
from signalforge.signals.company_registry import resolve_list

DEFAULT_QUERIES = [
    "{company} raised funding",
    "{company} announces new round",
    "{company} launches new product",
    "{company} hires new executive",
]


class ExaSource:
    name = "exa"

    async def collect(
        self, ctx: SourceContext, source_config: dict[str, Any]
    ) -> list[Signal]:
        if not source_config.get("enabled", False):
            return []
        api_key = ctx.env.exa_api_key
        if not api_key:
            return []

        targets = resolve_list(source_config.get("companies") or source_config.get("match_boards") or [])
        queries: list[str] = source_config.get("queries", DEFAULT_QUERIES) or DEFAULT_QUERIES
        lookback_days = int(source_config.get("lookback_days", 45))
        results_per_query = int(source_config.get("results_per_query", 3))

        start_date = (
            datetime.now(UTC).date().replace(day=1).isoformat()
            if lookback_days > 30
            else datetime.now(UTC).date().isoformat()
        )

        out: list[Signal] = []
        headers = {"x-api-key": api_key, "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            for entry in targets:
                company = entry.name
                for q_template in queries:
                    q = q_template.format(company=company)
                    try:
                        r = await client.post(
                            "https://api.exa.ai/search",
                            json={
                                "query": q,
                                "type": "neural",
                                "numResults": results_per_query,
                                "startPublishedDate": start_date,
                                "contents": {"text": {"maxCharacters": 500}},
                            },
                        )
                        if r.status_code >= 400:
                            continue
                        data = r.json()
                    except Exception as e:  # noqa: BLE001
                        warn("exa", f"{entry.name}/{q_template}", e)
                        continue

                    for res in data.get("results", []) or []:
                        title = (res.get("title") or "").strip()
                        if not title:
                            continue
                        url = res.get("url")
                        text = (res.get("text") or "")[:400]
                        kind, strength = _classify(q_template, title.lower(), text.lower())
                        observed = _parse_ts(
                            res.get("publishedDate") or res.get("retrievedAt")
                        ) or datetime.now(UTC)
                        out.append(
                            Signal(
                                kind=kind,
                                source="exa",
                                company_domain=entry.domain,
                                company_name=entry.name,
                                title=title[:200],
                                url=url,
                                observed_at=observed,
                                payload={
                                    "query": q,
                                    "snippet": text[:300],
                                    "score": res.get("score"),
                                },
                                strength=strength,
                            )
                        )
        return out


def _classify(q_template: str, title: str, text: str) -> tuple[SignalKind, float]:
    q = q_template.lower()
    blob = f"{title} {text}"
    if "fund" in q or "round" in q or any(w in blob for w in ("series ", "raises", "round")):
        return SignalKind.FUNDING, 0.9
    if "hire" in q or "executive" in q or any(w in blob for w in ("appoint", "names ", "cfo", "cmo", "cro")):
        return SignalKind.EXEC_CHANGE, 0.85
    if "launch" in q or any(w in blob for w in ("launches", "unveils", "introduces")):
        return SignalKind.PRODUCT_LAUNCH, 0.75
    return SignalKind.PRESS, 0.55


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None

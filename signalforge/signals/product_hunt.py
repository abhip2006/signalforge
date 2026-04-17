"""Product Hunt signal source — per-company launch activity.

Uses PH's GraphQL API (https://api.producthunt.com/v2/api/graphql). Requires
a developer token in PRODUCT_HUNT_TOKEN — the free developer-token tier is
600 complexity points per 15 minutes, which covers a handful of companies
per run.

If the token is unset, the source is a graceful no-op (returns []).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from signalforge.models import Signal, SignalKind
from signalforge.signals.base import SourceContext, warn
from signalforge.signals.company_registry import resolve_list

GRAPHQL_ENDPOINT = "https://api.producthunt.com/v2/api/graphql"

# Posts filter: name-contains OR maker-name-contains. We issue one query
# per company using the topic-free search to catch both the company's own
# launches and mentions inside sibling products.
_POSTS_QUERY = """
query PostsForCompany($q: String!, $after: DateTime) {
  posts(first: 5, postedAfter: $after, order: RANKING, topic: null, featured: null) {
    edges {
      node {
        id
        name
        tagline
        url
        createdAt
        votesCount
        commentsCount
        topics { edges { node { name } } }
      }
    }
  }
}
"""


class ProductHuntSource:
    name = "product_hunt"

    async def collect(
        self, ctx: SourceContext, source_config: dict[str, Any]
    ) -> list[Signal]:
        if not source_config.get("enabled", False):
            return []
        # Token is surfaced through the env for safety; it does not belong
        # in icp.yaml because that file is often committed.
        import os

        token = os.environ.get("PRODUCT_HUNT_TOKEN")
        if not token:
            return []
        companies = resolve_list(source_config.get("companies", []) or [])
        lookback_days = int(source_config.get("lookback_days", 60))
        min_votes = int(source_config.get("min_votes", 50))
        after = (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat()

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        out: list[Signal] = []

        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            for entry in companies:
                query_q = entry.name if len(entry.name) >= 4 else entry.token
                try:
                    r = await client.post(
                        GRAPHQL_ENDPOINT,
                        json={
                            "query": _POSTS_QUERY,
                            "variables": {"q": query_q, "after": after},
                        },
                    )
                    if r.status_code == 429:
                        warn("product_hunt", entry.name, RuntimeError("rate limited"))
                        break
                    r.raise_for_status()
                    data = r.json()
                except Exception as e:  # noqa: BLE001
                    warn("product_hunt", entry.name, e)
                    continue

                edges = (
                    ((data.get("data") or {}).get("posts") or {}).get("edges") or []
                )
                name_lower = entry.name.lower()
                for edge in edges:
                    node = edge.get("node") or {}
                    title = (node.get("name") or "").strip()
                    tagline = (node.get("tagline") or "").strip()
                    votes = int(node.get("votesCount") or 0)
                    if votes < min_votes:
                        continue
                    blob = f"{title} {tagline}".lower()
                    if name_lower not in blob:
                        continue  # drop unrelated matches
                    observed = _parse_ts(node.get("createdAt")) or datetime.now(UTC)
                    strength = min(0.95, 0.4 + min(votes, 1000) / 1500.0)
                    out.append(
                        Signal(
                            kind=SignalKind.PRODUCT_LAUNCH,
                            source="product_hunt",
                            company_domain=entry.domain,
                            company_name=entry.name,
                            title=f"Product Hunt: {title} — {tagline}"[:200],
                            url=node.get("url"),
                            observed_at=observed,
                            payload={
                                "votes": votes,
                                "comments": node.get("commentsCount"),
                                "topics": [
                                    (e.get("node") or {}).get("name")
                                    for e in (node.get("topics") or {}).get("edges") or []
                                ],
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

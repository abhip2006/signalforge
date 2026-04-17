"""Press / launch signals via public RSS feeds.

Free, no keys. Parses TechCrunch, Finsmes, PRNewswire, Hacker News front-page,
and the user-supplied feed list, then keyword-matches against the companies
declared across the other configured signal sources.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import feedparser

from signalforge.models import Signal, SignalKind
from signalforge.signals.base import SourceContext
from signalforge.signals.company_registry import resolve_board

DEFAULT_FEEDS: list[str] = [
    "https://techcrunch.com/category/venture/feed/",
    "https://techcrunch.com/category/fundraising/feed/",
    "https://www.finsmes.com/feed",
    "https://news.ycombinator.com/rss",
]


class NewsRSSSource:
    name = "news_rss"

    async def collect(
        self, ctx: SourceContext, source_config: dict[str, Any]
    ) -> list[Signal]:
        if not source_config.get("enabled", True):
            return []
        feeds: list[str] = source_config.get("feeds") or DEFAULT_FEEDS
        targets = _build_target_index(source_config.get("match_boards") or [])

        out: list[Signal] = []
        for feed_url in feeds:
            # feedparser is sync but CPU-bound and fast; fetching via its own HTTP is fine.
            # (Running in a thread would be nice for a dozen feeds; acceptable as-is for 4-8.)
            parsed = feedparser.parse(feed_url)
            for entry in (parsed.entries or [])[:50]:
                title = _text(entry.get("title"))
                summary = _text(entry.get("summary"))
                blob = f"{title}\n{summary}".lower()
                match = _match_target(blob, targets)
                if match is None:
                    continue
                observed = _parse_entry_time(entry)
                kind, strength = _classify(title.lower(), summary.lower())
                out.append(
                    Signal(
                        kind=kind,
                        source="news_rss",
                        company_domain=match["domain"],
                        company_name=match["name"],
                        title=title[:200],
                        url=entry.get("link"),
                        observed_at=observed,
                        payload={
                            "feed": feed_url,
                            "summary": summary[:400],
                            "match_token": match["token"],
                        },
                        strength=strength,
                    )
                )
        return out


def _build_target_index(entries: list[Any]) -> list[dict[str, str]]:
    """Each target has: token (for match), domain, name."""
    targets: list[dict[str, str]] = []
    for raw in entries:
        entry = resolve_board(raw)
        token = entry.token.lower()
        name = entry.name
        targets.append(
            {
                "token": token,
                "name_lower": name.lower(),
                "domain": entry.domain,
                "name": name,
            }
        )
    return targets


def _match_target(blob: str, targets: list[dict[str, str]]) -> dict[str, str] | None:
    for t in targets:
        # Match on display name OR slug token, whichever is longer/safer.
        candidate = t["name_lower"] if len(t["name_lower"]) >= 4 else t["token"]
        if candidate and candidate in blob:
            return t
    return None


def _classify(title: str, summary: str) -> tuple[SignalKind, float]:
    blob = f"{title} {summary}"
    if any(w in blob for w in ("raises", "funding", "series ", "seed round", "closes ", "funding round")):
        return SignalKind.FUNDING, 0.85
    if any(w in blob for w in ("launches", "launch of", "releases", "announces ", "unveils")):
        return SignalKind.PRODUCT_LAUNCH, 0.7
    if any(w in blob for w in ("hires", "appoints", "names ", "steps down", "departs")):
        return SignalKind.EXEC_CHANGE, 0.8
    return SignalKind.PRESS, 0.5


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _parse_entry_time(entry: Any) -> datetime:
    """Feedparser uses time.struct_time for parsed fields."""
    import calendar
    for attr in ("published_parsed", "updated_parsed"):
        t = entry.get(attr) if hasattr(entry, "get") else getattr(entry, attr, None)
        if t:
            try:
                ts = calendar.timegm(t)
                return datetime.fromtimestamp(ts, tz=UTC)
            except (TypeError, ValueError):
                pass
    return datetime.now(UTC)

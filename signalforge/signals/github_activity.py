"""GitHub org activity signals — new repos, releases, contributor spikes.

Uses the REST API; works unauthenticated (60 req/hr) or with GITHUB_TOKEN (5,000 req/hr).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from signalforge.models import Signal, SignalKind
from signalforge.signals.base import SourceContext, http_get_json, warn

_logger = logging.getLogger(__name__)

# Emit the "missing token" warning exactly once per process to avoid log spam
# when this source is invoked repeatedly (e.g. Streamlit refresh loops).
_MISSING_TOKEN_WARNED = False


def _warn_if_missing_token(github_token: str | None) -> None:
    """Log a single warning when GITHUB_TOKEN is absent.

    GitHub caps unauthenticated requests at 60/hr per IP. On shared cloud
    egress (Hugging Face Spaces et al.) this budget is shared with every
    other tenant, so the source will silently starve unless a token is set.
    """
    global _MISSING_TOKEN_WARNED
    if github_token:
        return
    if _MISSING_TOKEN_WARNED:
        return
    _MISSING_TOKEN_WARNED = True
    _logger.warning(
        "GITHUB_TOKEN is not set — github_activity will use anonymous auth "
        "(rate limit: 60 requests/hour per IP, often lower on shared cloud "
        "egress). Set GITHUB_TOKEN to raise this to 5,000 req/hr."
    )


class GitHubActivitySource:
    name = "github"

    async def collect(
        self, ctx: SourceContext, source_config: dict[str, Any]
    ) -> list[Signal]:
        if not source_config.get("enabled", True):
            return []
        orgs: list[str] = source_config.get("orgs", []) or []
        lookback_days = int(source_config.get("lookback_days", 30))
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

        _warn_if_missing_token(ctx.env.github_token)

        headers = {"Accept": "application/vnd.github+json"}
        if ctx.env.github_token:
            headers["Authorization"] = f"Bearer {ctx.env.github_token}"

        out: list[Signal] = []
        for org in orgs:
            try:
                repos_url = f"https://api.github.com/orgs/{org}/repos?per_page=100&sort=pushed"
                repos = await http_get_json(ctx, repos_url, headers=headers)
            except Exception as e:  # noqa: BLE001
                warn("github", org, e)
                continue
            if not isinstance(repos, list):
                continue

            new_repos = 0
            recent_releases = 0
            star_leader = None
            top_stars = 0
            for repo in repos:
                created = _parse_ts(repo.get("created_at"))
                pushed = _parse_ts(repo.get("pushed_at"))
                stars = repo.get("stargazers_count", 0) or 0
                if stars > top_stars:
                    top_stars = stars
                    star_leader = repo.get("full_name")
                if created and created >= cutoff:
                    new_repos += 1
                    out.append(
                        Signal(
                            kind=SignalKind.GITHUB_ACTIVITY,
                            source="github",
                            company_domain=f"{org}.github",
                            company_name=org,
                            title=f"New repo: {repo.get('full_name')}",
                            url=repo.get("html_url"),
                            observed_at=created,
                            payload={
                                "org": org,
                                "kind": "new_repo",
                                "stars": stars,
                                "description": repo.get("description"),
                            },
                            strength=0.7,
                        )
                    )
                if pushed and pushed >= cutoff and stars >= 100:
                    recent_releases += 1

            # Summary signal
            if new_repos > 0 or top_stars > 0:
                out.append(
                    Signal(
                        kind=SignalKind.GITHUB_ACTIVITY,
                        source="github",
                        company_domain=f"{org}.github",
                        company_name=org,
                        title=f"GitHub activity summary ({lookback_days}d): {new_repos} new repos, top star repo {star_leader}",
                        url=f"https://github.com/{org}",
                        observed_at=datetime.now(UTC),
                        payload={
                            "org": org,
                            "kind": "summary",
                            "new_repos": new_repos,
                            "recent_releases": recent_releases,
                            "top_stars": top_stars,
                            "star_leader": star_leader,
                        },
                        strength=min(0.3 + 0.15 * new_repos, 0.9),
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

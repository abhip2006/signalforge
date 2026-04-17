"""GitHub org activity signals — new repos, releases, contributor spikes.

Uses the REST API; works unauthenticated (60 req/hr) or with GITHUB_TOKEN (5,000 req/hr).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from signalforge.models import Signal, SignalKind
from signalforge.signals.base import SourceContext, http_get_json, warn


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

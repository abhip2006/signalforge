"""Greenhouse public job board signals. No auth required."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from signalforge.models import Signal, SignalKind
from signalforge.signals.base import SourceContext, http_get_json
from signalforge.signals.company_registry import resolve_list


class GreenhouseSource:
    name = "greenhouse"

    async def collect(
        self, ctx: SourceContext, source_config: dict[str, Any]
    ) -> list[Signal]:
        if not source_config.get("enabled", True):
            return []
        boards = resolve_list(source_config.get("boards", []) or [])
        keywords: list[str] = [k.lower() for k in source_config.get("hiring_keywords", [])]
        out: list[Signal] = []
        for entry in boards:
            token = entry.token
            try:
                url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
                data = await http_get_json(ctx, url)
            except Exception as e:  # noqa: BLE001 - source can be flaky; skip not fatal
                out.append(_err_signal(entry.domain, entry.name, str(e)))
                continue
            jobs = data.get("jobs") or []
            company_domain = entry.domain
            company_name = entry.name
            for job in jobs:
                title = (job.get("title") or "").strip()
                if not title:
                    continue
                match = _keyword_match(title, keywords) if keywords else True
                if not match:
                    continue
                posted = _parse_ts(job.get("updated_at") or job.get("first_published"))
                out.append(
                    Signal(
                        kind=SignalKind.HIRING,
                        source="greenhouse",
                        company_domain=company_domain,
                        company_name=company_name,
                        title=f"Hiring: {title}",
                        url=job.get("absolute_url"),
                        observed_at=posted or datetime.now(UTC),
                        payload={
                            "board_token": token,
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
        # Greenhouse uses ISO-8601 like "2026-04-10T15:42:10-04:00"
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

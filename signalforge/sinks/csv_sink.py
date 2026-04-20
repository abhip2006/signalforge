"""Write a flat CSV of best-draft-per-account for quick review."""
from __future__ import annotations

import csv
from pathlib import Path

from signalforge.models import Draft, EnrichedAccount, EvalScore, ResearchBrief


def write_csv_report(
    path: Path,
    rows: list[tuple[EnrichedAccount, ResearchBrief, Draft, EvalScore]],
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "domain",
                "company_name",
                "icp_score",
                "authenticity",
                "authority",
                "warmth",
                "signal_count",
                "contact_count",
                "brief_headline",
                "why_now",
                "best_variant",
                "draft_score",
                "subject",
                "body",
                "flagged",
            ]
        )
        for account, brief, draft, score in rows:
            w.writerow(
                [
                    account.company.domain,
                    account.company.name or "",
                    account.icp_score,
                    account.authenticity,
                    account.authority,
                    account.warmth,
                    len(account.signals),
                    len(account.contacts),
                    brief.headline,
                    brief.why_now,
                    draft.variant,
                    score.overall,
                    draft.subject or "",
                    draft.body,
                    ";".join(score.flagged),
                ]
            )
    return path

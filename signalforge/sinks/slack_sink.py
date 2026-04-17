"""Slack webhook sink — alerts for high-scoring accounts + drafts.

Gated by SLACK_WEBHOOK_URL. Graceful no-op if unset.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from signalforge.config import Env
from signalforge.models import Draft, EnrichedAccount, EvalScore, ResearchBrief


@dataclass(frozen=True)
class SlackSendResult:
    sent: int
    skipped: int
    reason: str = ""


async def post_top_accounts(
    env: Env,
    rows: list[tuple[EnrichedAccount, ResearchBrief, Draft, EvalScore]],
    *,
    min_icp_score: float = 70.0,
    min_draft_score: float = 75.0,
    max_rows: int = 5,
    run_id: str = "",
) -> SlackSendResult:
    if not env.slack_webhook_url:
        return SlackSendResult(sent=0, skipped=len(rows), reason="SLACK_WEBHOOK_URL unset")

    elig = [
        (a, b, d, s)
        for (a, b, d, s) in rows
        if a.icp_score >= min_icp_score and s.overall >= min_draft_score
    ]
    elig.sort(key=lambda r: (r[3].overall, r[0].icp_score), reverse=True)
    elig = elig[:max_rows]
    if not elig:
        return SlackSendResult(sent=0, skipped=len(rows), reason="no rows passed thresholds")

    blocks = _build_blocks(elig, run_id)
    payload: dict[str, Any] = {
        "text": f"SignalForge: {len(elig)} high-signal account(s) ready",
        "blocks": blocks,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(env.slack_webhook_url, json=payload)
        if r.status_code >= 400:
            return SlackSendResult(sent=0, skipped=len(elig), reason=f"slack {r.status_code}: {r.text[:120]}")
    return SlackSendResult(sent=len(elig), skipped=len(rows) - len(elig))


def _build_blocks(
    rows: list[tuple[EnrichedAccount, ResearchBrief, Draft, EvalScore]],
    run_id: str,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔔 SignalForge — {len(rows)} accounts"},
        }
    ]
    for account, brief, draft, score in rows:
        subject_line = f"*{draft.subject or brief.headline[:80]}*"
        name = account.company.name or account.company.domain
        top_dims = ", ".join(
            f"{k} {int(v)}" for k, v in list(score.dimensions.items())[:4]
        )
        body_preview = draft.body[:350] + ("…" if len(draft.body) > 350 else "")
        text_md = (
            f"{subject_line}\n"
            f"*{name}* ({account.company.domain}) · ICP {int(account.icp_score)} · draft {int(score.overall)}\n"
            f"> {brief.headline}\n\n"
            f"```{body_preview}```\n"
            f"_{top_dims}_"
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text_md}})
        blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"run `{run_id}` · _reply-quality eval harness v0.1_",
                }
            ],
        }
    )
    return blocks

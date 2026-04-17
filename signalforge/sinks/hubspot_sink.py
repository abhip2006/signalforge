"""HubSpot sink — upsert companies and log drafts as notes.

Uses HubSpot's v3 CRM API with a private-app token (HUBSPOT_TOKEN).
Graceful no-op if unset. Does NOT send email — writes a task + note so the
rep owns the final send-decision inside their workflow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from signalforge.config import Env
from signalforge.models import Draft, EnrichedAccount, EvalScore, ResearchBrief


@dataclass(frozen=True)
class HubSpotSyncResult:
    companies_upserted: int
    notes_created: int
    tasks_created: int
    errors: list[str]


async def sync_to_hubspot(
    env: Env,
    rows: list[tuple[EnrichedAccount, ResearchBrief, Draft, EvalScore]],
    *,
    min_icp_score: float = 55.0,
    min_draft_score: float = 65.0,
    run_id: str = "",
) -> HubSpotSyncResult:
    if not env.hubspot_token:
        return HubSpotSyncResult(0, 0, 0, ["HUBSPOT_TOKEN unset"])

    elig = [
        (a, b, d, s)
        for (a, b, d, s) in rows
        if a.icp_score >= min_icp_score and s.overall >= min_draft_score
    ]
    if not elig:
        return HubSpotSyncResult(0, 0, 0, [])

    headers = {
        "Authorization": f"Bearer {env.hubspot_token}",
        "Content-Type": "application/json",
    }
    errors: list[str] = []
    companies_upserted = notes_created = tasks_created = 0

    async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
        for account, brief, draft, score in elig:
            try:
                company_id = await _upsert_company(client, account)
                if company_id:
                    companies_upserted += 1
                    if await _create_note(client, company_id, brief, draft, score, run_id):
                        notes_created += 1
                    if await _create_task(client, company_id, account, brief, draft, score):
                        tasks_created += 1
            except httpx.HTTPStatusError as e:
                errors.append(f"{account.company.domain}: {e.response.status_code}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{account.company.domain}: {e.__class__.__name__}")

    return HubSpotSyncResult(companies_upserted, notes_created, tasks_created, errors)


async def _upsert_company(client: httpx.AsyncClient, account: EnrichedAccount) -> str | None:
    """HubSpot companies are keyed on `domain`. We search, then create-if-missing."""
    search = await client.post(
        "https://api.hubapi.com/crm/v3/objects/companies/search",
        json={
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "domain", "operator": "EQ", "value": account.company.domain}
                    ]
                }
            ],
            "limit": 1,
        },
    )
    if search.status_code == 200:
        results = search.json().get("results") or []
        if results:
            cid = results[0].get("id")
            if cid:
                return str(cid)

    # Create
    props: dict[str, Any] = {
        "domain": account.company.domain,
        "name": account.company.name or account.company.domain,
        "signalforge_icp_score": str(int(account.icp_score)),
    }
    if account.company.industry:
        props["industry"] = account.company.industry
    r = await client.post(
        "https://api.hubapi.com/crm/v3/objects/companies",
        json={"properties": props},
    )
    if r.status_code in (200, 201):
        return str(r.json().get("id"))
    return None


async def _create_note(
    client: httpx.AsyncClient,
    company_id: str,
    brief: ResearchBrief,
    draft: Draft,
    score: EvalScore,
    run_id: str,
) -> bool:
    body = (
        f"**SignalForge brief — run {run_id}**\n\n"
        f"{brief.headline}\n\n"
        f"{brief.why_now}\n\n"
        f"**Hooks:**\n- " + "\n- ".join(brief.hooks[:3]) + "\n\n"
        f"**Best draft (score {score.overall:.0f}):**\n\n"
        f"Subject: {draft.subject or '(none)'}\n\n{draft.body}"
    )
    r = await client.post(
        "https://api.hubapi.com/crm/v3/objects/notes",
        json={
            "properties": {
                "hs_timestamp": "now",
                "hs_note_body": body,
            },
            "associations": [
                {
                    "to": {"id": company_id},
                    "types": [
                        {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 190}
                    ],
                }
            ],
        },
    )
    return r.status_code in (200, 201)


async def _create_task(
    client: httpx.AsyncClient,
    company_id: str,
    account: EnrichedAccount,
    brief: ResearchBrief,
    draft: Draft,
    score: EvalScore,
) -> bool:
    subject = f"Outbound: {draft.subject or brief.headline[:80]}"
    body = (
        f"SignalForge surfaced this account (ICP {account.icp_score:.0f}, "
        f"draft {score.overall:.0f}). {brief.headline}"
    )
    r = await client.post(
        "https://api.hubapi.com/crm/v3/objects/tasks",
        json={
            "properties": {
                "hs_timestamp": "now",
                "hs_task_subject": subject,
                "hs_task_body": body,
                "hs_task_status": "NOT_STARTED",
                "hs_task_priority": "HIGH" if score.overall >= 85 else "MEDIUM",
                "hs_task_type": "EMAIL",
            },
            "associations": [
                {
                    "to": {"id": company_id},
                    "types": [
                        {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 192}
                    ],
                }
            ],
        },
    )
    return r.status_code in (200, 201)

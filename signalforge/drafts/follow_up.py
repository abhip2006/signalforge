"""Follow-up draft generation — aware of the prior touch.

A good follow-up does two things most AI-SDR tools get wrong:
  1. Does NOT re-anchor to the same signal (that's a "bump"; it's lazy).
  2. Adds a new angle — a peer proof point, a different stakeholder,
     or a freshly-observed signal (filing, hire, launch, podcast).
"""
from __future__ import annotations

import json
from textwrap import dedent

from anthropic import AsyncAnthropic

from signalforge.config import Env, ICPConfig
from signalforge.cost import LEDGER
from signalforge.cost import disabled as ledger_disabled
from signalforge.drafts.evals import score_draft
from signalforge.ledger import record_from_response
from signalforge.models import Draft, DraftKind, EnrichedAccount, EvalScore, ResearchBrief

FOLLOW_UP_SYSTEM = dedent("""\
    You write cold-outbound FOLLOW-UP emails. The prior opener already
    anchored on a specific signal. Your follow-up must NOT restate it —
    that reads as a lazy "bump". Instead, do one of:

      1. Introduce a NEW signal (a recent filing, hire, launch, or
         industry observation) — cite it precisely.
      2. Offer a short peer proof-point ("three teams at X-stage hit
         this exact wall…").
      3. Change the stakeholder ("copying +{first_name_from_another_role}
         if that's the right lane").

    Non-negotiables:
    - ≤ 120 words.
    - ONE clear CTA.
    - No "just circling back", "following up", "bumping this to the top".
    - Do not quote the prior opener.

    Output JSON: {"subject": str, "body": str}
""")


async def generate_follow_up(
    account: EnrichedAccount,
    brief: ResearchBrief,
    prior: Draft,
    icp: ICPConfig,
    env: Env,
    *,
    days_since_prior: int = 5,
) -> tuple[Draft, EvalScore]:
    """Produce a single follow-up variant, scored."""
    if not env.anthropic_api_key:
        return _stub_pair(account, brief, prior)

    client = AsyncAnthropic(api_key=env.anthropic_api_key)
    extra_signals = [s for s in account.signals if s.kind.value != "hiring"][:4]
    hooks_for_new_angle = [s.title for s in extra_signals] or brief.hooks[1:4]

    user = dedent(f"""\
        Prospect: {account.company.name or account.company.domain}
        Tone: {icp.tone}
        Sender: {icp.sender.get("name")} — {icp.sender.get("title")} at {icp.sender.get("company")}
        CTA target: {icp.sender.get("calendly") or "brief email thread"}

        PRIOR OPENER (subject: {prior.subject or '-'}):
        ---
        {prior.body}
        ---

        Days since prior: {days_since_prior}. Do not restate the prior anchor.

        New angles you may use (pick ONE, cite specifically):
        {json.dumps(hooks_for_new_angle)}

        Return JSON only.
    """)

    msg = await client.messages.create(
        model=env.claude_model,
        max_tokens=700,
        system=[
            {
                "type": "text",
                "text": FOLLOW_UP_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user}],
    )
    if not ledger_disabled():
        LEDGER.record("follow_up", env.claude_model, getattr(msg, "usage", None))
    record_from_response(msg, model=env.claude_model, stage="follow_up")
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    data = _safe_json(text)

    draft = Draft(
        account_domain=account.company.domain,
        contact_email=prior.contact_email,
        kind=DraftKind.FOLLOW_UP,
        subject=(data.get("subject") or "").strip() or None,
        body=(data.get("body") or "").strip(),
        variant=prior.variant,
        tone=icp.tone,
        model=env.claude_model,
    )
    if not draft.body:
        return _stub_pair(account, brief, prior)

    score = await score_draft(draft, brief, icp, env)
    return draft, score


def _stub_pair(
    account: EnrichedAccount, brief: ResearchBrief, prior: Draft
) -> tuple[Draft, EvalScore]:
    angle = brief.hooks[1] if len(brief.hooks) > 1 else brief.headline
    body = (
        f"Different angle on the last one — {angle}. "
        f"If that's more relevant than the earlier point, 15 min next week could still help."
    )
    d = Draft(
        account_domain=account.company.domain,
        contact_email=prior.contact_email,
        kind=DraftKind.FOLLOW_UP,
        subject="Different angle on the last one",
        body=body,
        variant=prior.variant,
    )
    s = EvalScore(
        draft_id="stub",
        overall=60.0,
        dimensions={"signal_anchoring": 60, "length": 100, "single_cta": 90},
        rationale="stub (no API key)",
        judge_model="stub",
    )
    return d, s


def _safe_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start = t.find("{")
        end = t.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(t[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {}

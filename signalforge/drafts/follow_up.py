"""Follow-up and reply-thread draft generation — aware of prior context.

A good follow-up does two things most AI-SDR tools get wrong:
  1. Does NOT re-anchor to the same signal (that's a "bump"; it's lazy).
  2. Adds a new angle — a peer proof point, a different stakeholder,
     or a freshly-observed signal (filing, hire, launch, podcast).

A reply-thread draft responds to an actual prospect reply. It must mirror
the sender's tone, answer their question directly, and keep the next step
small.
"""
from __future__ import annotations

import json
from textwrap import dedent

from anthropic import AsyncAnthropic

from signalforge.config import Env, ICPConfig
from signalforge.cost import LEDGER
from signalforge.cost import disabled as ledger_disabled
from signalforge.drafts.evals import score_draft
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

FOLLOW_UP_2_SYSTEM = dedent("""\
    You write the SECOND follow-up in a cold-outbound sequence. The
    opener anchored to the original signal; the first follow-up added a
    new angle or peer proof. No reply came back. This is the LAST touch
    before you move on.

    Your job in this second follow-up:

      1. Acknowledge explicitly that this is the last message (briefly,
         not theatrically). A "breakup email" done well.
      2. Offer ONE durable artifact the prospect can pick up later
         without replying (a short write-up, a template, an intro, a
         public post). No "jump on a call".
      3. Leave the door open — a single sentence.

    Non-negotiables:
    - ≤ 90 words.
    - ONE soft CTA (send the thing, reply if useful, or nothing at all).
    - No "just circling back", "following up", "bumping this to the top",
      "third time's the charm".
    - Do not quote the prior emails verbatim.

    Output JSON: {"subject": str, "body": str}
""")

REPLY_THREAD_SYSTEM = dedent("""\
    You write REPLIES inside an active cold-email thread. The prospect
    already replied to one of your earlier messages. Your next message:

      1. Answers whatever the prospect actually asked — directly, first
         sentence.
      2. Matches the tone and length of their reply. Short prospect reply
         = short response. Do not inflate.
      3. Keeps the next step SMALL — a specific question, a resource
         link, or a 15-min slot. Do not pivot to new signals; you're in
         a live conversation.

    Non-negotiables:
    - ≤ 80 words.
    - ONE clear next step.
    - No "great question", "thanks for your interest", "I'd love to".
    - Mirror the prospect's phrasing when answering (quote their term
      when it helps).

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
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    data = _safe_json(text)

    draft = Draft(
        account_domain=account.company.domain,
        contact_email=prior.contact_email,
        kind=DraftKind.FOLLOW_UP_1,
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


async def generate_follow_up_2(
    account: EnrichedAccount,
    brief: ResearchBrief,
    prior_opener: Draft,
    prior_follow_up: Draft,
    icp: ICPConfig,
    env: Env,
    *,
    days_since_prior: int = 7,
) -> tuple[Draft, EvalScore]:
    """Produce the SECOND follow-up — the breakup email.

    Given both the opener and the first follow-up (so the model doesn't
    restate either), emits a short final touch that leaves a durable
    artifact rather than pushing a meeting.
    """
    if not env.anthropic_api_key:
        return _stub_pair_2(account, brief, prior_opener, prior_follow_up)

    client = AsyncAnthropic(api_key=env.anthropic_api_key)
    user = dedent(f"""\
        Prospect: {account.company.name or account.company.domain}
        Tone: {icp.tone}
        Sender: {icp.sender.get("name")} — {icp.sender.get("title")} at {icp.sender.get("company")}

        OPENER (subject: {prior_opener.subject or '-'}):
        ---
        {prior_opener.body}
        ---

        FOLLOW-UP 1 (subject: {prior_follow_up.subject or '-'}):
        ---
        {prior_follow_up.body}
        ---

        Days since last touch: {days_since_prior}. This is the LAST message.
        Offer one durable artifact (short write-up, a template, a one-pager,
        a public post). Do not push a meeting.

        Available hooks if a new angle helps: {json.dumps(brief.hooks[:3])}

        Return JSON only.
    """)

    msg = await client.messages.create(
        model=env.claude_model,
        max_tokens=600,
        system=[
            {
                "type": "text",
                "text": FOLLOW_UP_2_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user}],
    )
    if not ledger_disabled():
        LEDGER.record("follow_up", env.claude_model, getattr(msg, "usage", None))
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    data = _safe_json(text)

    draft = Draft(
        account_domain=account.company.domain,
        contact_email=prior_opener.contact_email,
        kind=DraftKind.FOLLOW_UP_2,
        subject=(data.get("subject") or "").strip() or None,
        body=(data.get("body") or "").strip(),
        variant=prior_opener.variant,
        tone=icp.tone,
        model=env.claude_model,
    )
    if not draft.body:
        return _stub_pair_2(account, brief, prior_opener, prior_follow_up)

    score = await score_draft(draft, brief, icp, env)
    return draft, score


async def generate_reply_thread(
    account: EnrichedAccount,
    brief: ResearchBrief,
    prior_opener: Draft,
    prospect_reply: str,
    icp: ICPConfig,
    env: Env,
) -> tuple[Draft, EvalScore]:
    """Generate a reply inside an active thread — responds to the prospect's
    actual message, mirrors their length/tone, proposes a small next step.
    """
    if not env.anthropic_api_key:
        return _stub_reply(account, brief, prior_opener, prospect_reply)

    client = AsyncAnthropic(api_key=env.anthropic_api_key)
    user = dedent(f"""\
        Prospect: {account.company.name or account.company.domain}
        Tone: {icp.tone}
        Sender: {icp.sender.get("name")} — {icp.sender.get("title")} at {icp.sender.get("company")}
        CTA target: {icp.sender.get("calendly") or "brief email thread"}

        YOUR PRIOR OPENER (subject: {prior_opener.subject or '-'}):
        ---
        {prior_opener.body}
        ---

        PROSPECT REPLY:
        ---
        {prospect_reply}
        ---

        Write the reply. Answer their question first. Mirror their length.
        Return JSON only.
    """)

    msg = await client.messages.create(
        model=env.claude_model,
        max_tokens=500,
        system=[
            {
                "type": "text",
                "text": REPLY_THREAD_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user}],
    )
    if not ledger_disabled():
        LEDGER.record("reply_thread", env.claude_model, getattr(msg, "usage", None))
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    data = _safe_json(text)

    draft = Draft(
        account_domain=account.company.domain,
        contact_email=prior_opener.contact_email,
        kind=DraftKind.REPLY_THREAD,
        subject=(data.get("subject") or "").strip() or None,
        body=(data.get("body") or "").strip(),
        variant=prior_opener.variant,
        tone=icp.tone,
        model=env.claude_model,
    )
    if not draft.body:
        return _stub_reply(account, brief, prior_opener, prospect_reply)

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
        kind=DraftKind.FOLLOW_UP_1,
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


def _stub_pair_2(
    account: EnrichedAccount,
    brief: ResearchBrief,
    prior_opener: Draft,
    prior_follow_up: Draft,
) -> tuple[Draft, EvalScore]:
    artifact = brief.hooks[0] if brief.hooks else brief.headline or "a short teardown"
    body = (
        f"Last note from me — if the earlier threads on {artifact} weren't "
        f"quite the lane, no worries. Happy to send the one-pager either way; "
        f"reply if useful."
    )
    d = Draft(
        account_domain=account.company.domain,
        contact_email=prior_opener.contact_email,
        kind=DraftKind.FOLLOW_UP_2,
        subject="Last note on this",
        body=body,
        variant=prior_opener.variant,
    )
    s = EvalScore(
        draft_id="stub",
        overall=60.0,
        dimensions={"signal_anchoring": 55, "length": 100, "single_cta": 85},
        rationale="stub (no API key)",
        judge_model="stub",
    )
    return d, s


def _stub_reply(
    account: EnrichedAccount,
    brief: ResearchBrief,
    prior_opener: Draft,
    prospect_reply: str,
) -> tuple[Draft, EvalScore]:
    short = (prospect_reply or "").strip().split("\n", 1)[0][:80]
    body = (
        f"On '{short}': quickest answer is the shared write-up — can drop it in "
        f"the thread today. Want me to send it, or would a 15-min slot be easier?"
    )
    d = Draft(
        account_domain=account.company.domain,
        contact_email=prior_opener.contact_email,
        kind=DraftKind.REPLY_THREAD,
        subject="Re: " + (prior_opener.subject or "follow-up"),
        body=body,
        variant=prior_opener.variant,
    )
    s = EvalScore(
        draft_id="stub",
        overall=62.0,
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

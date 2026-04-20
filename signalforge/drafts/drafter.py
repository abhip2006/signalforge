"""Claude cold-email drafter with variant generation + eval-gated regeneration."""
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

DRAFTER_SYSTEM = dedent("""\
    You write cold outbound emails for a senior GTM engineer. You write like a
    sharp operator, not a marketer. You obey these non-negotiables:

    1. ≤ 75 words in the body. No exceptions.
    2. Exactly ONE call to action. Pick one: book a call, ask a question, or offer a thing.
    3. The first sentence ANCHORS to a specific signal — you must quote or reference the
       actual event (a job posting, a filing, a repo, a funding round). No generic openers.
    4. Forbidden phrases: "just circling back", "quick question", "hope this finds you well",
       "I was impressed by", "touching base", "checking in". If any appear, the email fails.
    5. No em-dashes at sentence ends used as drama pauses. Prose should read like a peer DM.

    Output JSON only:
    {
      "variants": [
        {"subject": str, "body": str, "tone": "direct"|"warm"|"formal", "variant": int}
      ]
    }
""")


async def generate_drafts(
    account: EnrichedAccount,
    brief: ResearchBrief,
    icp: ICPConfig,
    env: Env,
    kind: DraftKind = DraftKind.OPENER,
    max_variants: int = 3,
    contact_email: str | None = None,
) -> list[tuple[Draft, EvalScore]]:
    """Generate up to N variants, score each, and return (draft, score) pairs sorted by score."""
    if not env.anthropic_api_key:
        return [_stub_pair(account, brief, kind, i, contact_email) for i in range(max_variants)]

    client = AsyncAnthropic(api_key=env.anthropic_api_key)
    user = dedent(f"""\
        Prospect: {account.company.name or account.company.domain}
        Contact title(s): {", ".join(icp.target_titles[:3])}
        Tone: {icp.tone}
        Sender: {icp.sender.get("name")} — {icp.sender.get("title")} at {icp.sender.get("company")}
        CTA target: {icp.sender.get("calendly") or "brief email thread"}

        Brief headline: {brief.headline}
        Why now: {brief.why_now}
        Hooks available: {json.dumps(brief.hooks)}
        Citations: {json.dumps(brief.citations[:5])}

        Write {max_variants} {kind.value} variants. Each variant must use a DIFFERENT
        hook and a DIFFERENT CTA style. Return JSON only.
    """)

    msg = await client.messages.create(
        model=env.claude_model,
        max_tokens=1200,
        system=[
            {
                "type": "text",
                "text": DRAFTER_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user}],
    )
    if not ledger_disabled():
        LEDGER.record("draft", env.claude_model, getattr(msg, "usage", None))
    record_from_response(msg, model=env.claude_model, stage="draft")
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    data = _safe_json(text)
    variants = data.get("variants", []) or []

    results: list[tuple[Draft, EvalScore]] = []
    for v in variants[:max_variants]:
        draft = Draft(
            account_domain=account.company.domain,
            contact_email=contact_email,
            kind=kind,
            subject=(v.get("subject") or "").strip() or None,
            body=(v.get("body") or "").strip(),
            variant=int(v.get("variant", len(results))),
            tone=(v.get("tone") or icp.tone),
            model=env.claude_model,
        )
        if not draft.body:
            continue
        eval_score = await score_draft(draft, brief, icp, env)
        results.append((draft, eval_score))

    results.sort(key=lambda pair: pair[1].overall, reverse=True)
    return results


def _stub_pair(
    account: EnrichedAccount,
    brief: ResearchBrief,
    kind: DraftKind,
    i: int,
    contact_email: str | None,
) -> tuple[Draft, EvalScore]:
    hook = brief.hooks[i] if i < len(brief.hooks) else brief.headline
    body = (
        f"Saw {hook}. I build signal-driven outbound systems; happens to be exactly "
        f"the pattern a team at your stage tends to hit pain around the Series A mark. "
        f"Open to a 15-min next week?"
    )
    d = Draft(
        account_domain=account.company.domain,
        contact_email=contact_email,
        kind=kind,
        subject=hook[:60],
        body=body,
        variant=i,
        model="stub",
    )
    # Stub score — real eval runs require Claude.
    s = EvalScore(
        draft_id=_draft_id(d),
        overall=60.0,
        dimensions={"signal_anchoring": 70, "length": 90, "single_cta": 90, "personalization": 50,
                    "spam_triggers": 100, "tone": 70, "grammar": 90},
        rationale="stub score (no API key available)",
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


def _draft_id(d: Draft) -> str:
    import hashlib
    key = f"{d.account_domain}|{d.kind}|{d.variant}|{d.body[:40]}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


# Expose for the eval harness
__all__ = ["generate_drafts", "_draft_id"]

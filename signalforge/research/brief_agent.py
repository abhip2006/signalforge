"""Claude-powered 'why now' research brief.

Every claim must be anchored to a signal or a cited URL. No floating assertions.
"""
from __future__ import annotations

import json
from textwrap import dedent

from anthropic import AsyncAnthropic

from signalforge.config import Env, ICPConfig
from signalforge.cost import LEDGER
from signalforge.cost import disabled as ledger_disabled
from signalforge.enrichment import CompanyContext
from signalforge.ledger import record_from_response
from signalforge.models import EnrichedAccount, ResearchBrief

BRIEF_SYSTEM = dedent("""\
    You are a senior GTM research analyst. You produce concise, signal-anchored
    account briefs for an outbound sales motion. You do NOT speculate. Every
    claim must be tied to a signal or a citation passed in the context.

    Output JSON strictly matching this schema:
    {
      "headline": str,             # one line, <= 110 chars, starts with the company name
      "why_now": str,              # 2-3 sentences; every sentence anchored to a signal
      "hooks": [str, str, ...],    # 3 one-liners a rep can open with — signal-specific, zero generic filler
      "objections_to_expect": [str, str],
      "citations": [str, ...]      # URLs from the signals or any extra context provided
    }

    Rules:
    - No generic openers ("quick question", "just circling back", "impressed by").
    - Every hook must reference the actual signal title or content, not paraphrase.
    - If signals are thin, say so in why_now instead of inventing context.
""")


def _render_signals(acc: EnrichedAccount, limit: int = 12) -> str:
    lines = []
    for i, s in enumerate(acc.signals[:limit], 1):
        lines.append(
            f"{i}. [{s.kind.value}:{s.source} strength={s.strength:.2f}] "
            f"{s.title}  ({s.url or 'no-url'})"
        )
    return "\n".join(lines) or "(no signals captured)"


async def generate_brief(
    account: EnrichedAccount,
    icp: ICPConfig,
    env: Env,
    *,
    company_context: CompanyContext | None = None,
) -> ResearchBrief:
    if not env.anthropic_api_key:
        # Graceful degrade: build a deterministic stub brief so downstream steps still work.
        return _stub_brief(account)

    client = AsyncAnthropic(api_key=env.anthropic_api_key)
    ctx_block = _render_context(company_context)
    user = dedent(f"""\
        Build a research brief for outbound to {account.company.name or account.company.domain}
        ({account.company.domain}).

        ICP context:
        - Target titles: {", ".join(icp.target_titles)}
        - Tone: {icp.tone}
        - Our sender: {icp.sender.get("name")} — {icp.sender.get("title")} at {icp.sender.get("company")}

        Signals captured for this account:
        {_render_signals(account)}

        {ctx_block}

        Return the JSON object only. No prose before or after.
    """)

    # Use prompt caching on the system prompt — it's reused across every account in a run.
    msg = await client.messages.create(
        model=env.claude_model,
        max_tokens=1500,
        system=[
            {
                "type": "text",
                "text": BRIEF_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user}],
    )
    if not ledger_disabled():
        LEDGER.record("brief", env.claude_model, getattr(msg, "usage", None))
    record_from_response(msg, model=env.claude_model, stage="brief")
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    data = _safe_json(text)
    if not data:
        # Parser failed — log the raw response tail for debugging and fall back to a stub.
        import sys
        tail = text[-300:] if text else "(empty)"
        print(
            f"[brief_agent] WARN: could not parse JSON for {account.company.domain} "
            f"(stop={msg.stop_reason}). Response tail: {tail!r}",
            file=sys.stderr,
        )
        return _stub_brief(account)

    return ResearchBrief(
        account_domain=account.company.domain,
        headline=data.get("headline", account.company.domain),
        why_now=data.get("why_now", ""),
        hooks=list(data.get("hooks", []))[:5],
        objections_to_expect=list(data.get("objections_to_expect", []))[:4],
        citations=list(data.get("citations", []))[:10],
        model=env.claude_model,
    )


def _render_context(ctx: CompanyContext | None) -> str:
    if ctx is None or not ctx.text:
        return "Company-context snippet: (none — enrichment skipped or failed)"
    title = f" (page title: {ctx.title})" if ctx.title else ""
    return (
        f"Company-context snippet from {', '.join(ctx.urls_seen)}{title}:\n"
        f"> {ctx.text[:1500]}"
    )


def _stub_brief(account: EnrichedAccount) -> ResearchBrief:
    strongest = max(account.signals, key=lambda s: s.strength, default=None)
    headline = (
        f"{account.company.name or account.company.domain} — "
        f"{len(account.signals)} signals, top: {strongest.title if strongest else 'none'}"
    )
    return ResearchBrief(
        account_domain=account.company.domain,
        headline=headline[:110],
        why_now=(
            f"Captured {len(account.signals)} signals. "
            f"Strongest: {strongest.title if strongest else 'none'} "
            f"({strongest.source if strongest else 'n/a'})."
        ),
        hooks=[s.title for s in account.signals[:3]],
        objections_to_expect=[],
        citations=[s.url for s in account.signals if s.url][:5],
        model="stub",
    )


def _safe_json(text: str) -> dict:
    """Strip code fences if Claude wraps the JSON."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # Find the first {...} block as a last resort.
        start = t.find("{")
        end = t.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(t[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {}

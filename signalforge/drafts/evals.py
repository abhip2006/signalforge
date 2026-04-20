"""Reply-quality eval harness for cold-email drafts.

Mixes deterministic checks (length, spam triggers, single-CTA) with an
LLM-as-judge score for signal-anchoring, personalization, and tone match.

The final overall is a weighted blend so deterministic failures can't be
LLM-rationalized into a pass.
"""
from __future__ import annotations

import hashlib
import json
import re
from textwrap import dedent

from anthropic import AsyncAnthropic

from signalforge.config import Env, ICPConfig
from signalforge.cost import LEDGER
from signalforge.cost import disabled as ledger_disabled
from signalforge.ledger import record_from_response
from signalforge.models import Draft, EvalScore, ResearchBrief

EVAL_DIMENSIONS: dict[str, float] = {
    "signal_anchoring": 0.25,
    "length": 0.15,
    "single_cta": 0.15,
    "personalization": 0.20,
    "spam_triggers": 0.10,
    "tone": 0.10,
    "grammar": 0.05,
}

SPAM_PATTERNS = [
    r"\bjust circling back\b",
    r"\bquick question\b",
    r"\bhope this (email|message) finds you well\b",
    r"\bhope all is well\b",
    r"\bi was impressed by\b",
    r"\btouching base\b",
    r"\bchecking in\b",
    r"\breaching out\b",       # overused opener
    r"\bper my last email\b",
    r"\bsynergy\b|\bsynergies\b",
    r"\bgame[- ]changer\b",
    r"\bbumping (this|my (earlier )?note)\b",
    r"\bbump(ing)? (this|it)? to the top\b",
    r"\bfollowing up on (my|the) (earlier|previous|last) (email|note|message)\b",
    r"\bany interest\??$",     # weak-close when it's the entire CTA
]

CTA_PATTERNS = [
    r"\b(book|grab|schedule|set up|setup)\b.*\b(call|chat|meeting|15[- ]?min)\b",
    r"\b(calendly|cal\.com|meetings\.)\S*",
    r"\bworth a (quick )?chat\??",
    r"\bworth (15|20) minutes\??",
    r"\bopen to\b.*\b(call|chat|next week)\b",
    r"\breply (back|to|yes|no|if)\b",
    r"\bmind if i (share|send|follow up|drop)\b",
    r"\b(thoughts\?|interested\?)\s*$",
    r"\bsend (it|this|the teardown) over\??",
    r"\bwant me to (send|share|drop)\b",
    r"\blet me know if\b.*\b(useful|helpful)\b",
]

JUDGE_SYSTEM = dedent("""\
    You are an outbound email eval judge. You score one draft on three
    dimensions only:

    - signal_anchoring (0-100): does the draft reference the SPECIFIC signal
      provided in the brief, not a paraphrase? Quote-level anchoring is best.
    - personalization (0-100): does it include a detail about this account
      beyond the signal (role, company, stack, language)? Generic = low.
    - tone (0-100): does it match the requested tone (direct/warm/formal),
      written like a peer DM rather than marketing copy?

    Output JSON only:
    {"signal_anchoring": int, "personalization": int, "tone": int,
     "rationale": "<= 40 words"}
""")


def _word_count(body: str) -> int:
    return len(re.findall(r"\b\w+\b", body))


def _length_score(body: str, kind: str) -> tuple[float, list[str]]:
    wc = _word_count(body)
    limit = 75 if kind == "opener" else 120
    flags: list[str] = []
    if wc > limit:
        over = wc - limit
        flags.append(f"length:over_by_{over}")
        # Sharper drop-off: -5/word for first 10 over (hit 50 at +10),
        # then -10/word after. This matches reader behavior — nobody
        # reads a 125-word "short" cold email.
        penalty = over * 5 if over <= 10 else 50 + (over - 10) * 10
        return max(0.0, 100.0 - penalty), flags
    if wc < 25:
        flags.append("length:too_short")
        return 60.0, flags
    return 100.0, flags


def _cta_score(body: str) -> tuple[float, list[str]]:
    """Score the CTA. A trailing question mark is NOT sufficient — there
    must be a recognized action pattern (book call / send thing / reply yes)."""
    text = body.lower()
    hits = [p for p in CTA_PATTERNS if re.search(p, text, re.IGNORECASE)]
    q_marks = body.count("?")
    flags: list[str] = []
    if not hits and q_marks == 0:
        flags.append("cta:missing")
        return 40.0, flags
    if not hits and q_marks >= 1:
        # Rhetorical questions without a concrete ask — weak CTA.
        flags.append("cta:weak_rhetorical")
        return 55.0, flags
    if len(hits) >= 3 or q_marks >= 3:
        flags.append("cta:multiple")
        return 55.0, flags
    if q_marks >= 2:
        flags.append("cta:two_questions")
        return 75.0, flags
    return 100.0, flags


def _spam_score(body: str) -> tuple[float, list[str]]:
    text = body.lower()
    hit_patterns = [p for p in SPAM_PATTERNS if re.search(p, text)]
    flags = [f"spam:{p}" for p in hit_patterns]
    if not hit_patterns:
        return 100.0, flags
    # Each hit removes 20 points.
    return max(0.0, 100.0 - 20 * len(hit_patterns)), flags


def _grammar_score(body: str) -> tuple[float, list[str]]:
    flags: list[str] = []
    score = 100.0
    # Double spaces
    if "  " in body:
        score -= 5
        flags.append("grammar:double_space")
    # Trailing whitespace
    if any(line.rstrip() != line for line in body.splitlines()):
        score -= 2
        flags.append("grammar:trailing_ws")
    # Capitalization at sentence start
    sentences = re.split(r"[.!?]\s+", body)
    mis_caps = sum(1 for s in sentences if s and s[0].islower())
    if mis_caps:
        score -= 5 * min(mis_caps, 3)
        flags.append(f"grammar:mis_cap:{mis_caps}")
    return max(0.0, score), flags


def draft_id(d: Draft) -> str:
    key = f"{d.account_domain}|{d.kind.value}|{d.variant}|{d.body[:40]}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _apply_cliffs(overall: float, dims: dict[str, float], flagged: list[str]) -> float:
    """A draft with a fatal flaw should never show a "pass-ish" overall.

    Cliffs (each caps the overall, applied as a min):
    - spam_triggers < 80              → overall <= 55 (obvious spam phrase)
    - single_cta < 50                 → overall <= 60 (no real CTA)
    - length:over_by_* large          → overall <= 60 (too long is too long)
    - signal_anchoring < 50           → overall <= 60 (generic)
    - personalization < 25 AND single_cta < 60 → overall <= 55 (combo)
    """
    caps = [100.0]
    if dims.get("spam_triggers", 100) < 80:
        caps.append(55.0)
    if dims.get("single_cta", 100) < 50:
        caps.append(60.0)
    if dims.get("signal_anchoring", 100) < 50:
        caps.append(60.0)
    if dims.get("personalization", 100) < 25 and dims.get("single_cta", 100) < 60:
        caps.append(55.0)
    # "length:over_by_X" with X >= 30 words is a hard cap
    for f in flagged:
        if f.startswith("length:over_by_"):
            try:
                over = int(f.rsplit("_", 1)[-1])
                if over >= 30:
                    caps.append(60.0)
            except ValueError:
                pass
    return round(min(overall, min(caps)), 2)


async def score_draft(
    draft: Draft, brief: ResearchBrief, icp: ICPConfig, env: Env
) -> EvalScore:
    # Deterministic dimensions
    length, l_flags = _length_score(draft.body, draft.kind.value)
    single_cta, c_flags = _cta_score(draft.body)
    spam, s_flags = _spam_score(draft.body)
    grammar, g_flags = _grammar_score(draft.body)

    # LLM judge dimensions (signal_anchoring / personalization / tone)
    judged, j_rationale, j_model = await _judge(draft, brief, icp, env)

    dimensions: dict[str, float] = {
        "signal_anchoring": judged.get("signal_anchoring", 50.0),
        "length": length,
        "single_cta": single_cta,
        "personalization": judged.get("personalization", 50.0),
        "spam_triggers": spam,
        "tone": judged.get("tone", 60.0),
        "grammar": grammar,
    }

    overall = sum(dimensions[k] * w for k, w in EVAL_DIMENSIONS.items())
    flagged = l_flags + c_flags + s_flags + g_flags

    # Cliff penalties — model the reality that a cold email with no CTA
    # or obvious spam triggers cannot be "mostly fine". A weighted average
    # masks these failures. We cap the overall so the final number matches
    # a human's reaction, not a spreadsheet.
    overall = _apply_cliffs(overall, dimensions, flagged)

    return EvalScore(
        draft_id=draft_id(draft),
        overall=round(overall, 2),
        dimensions={k: round(v, 1) for k, v in dimensions.items()},
        rationale=j_rationale,
        flagged=flagged,
        judge_model=j_model,
    )


async def _judge(
    draft: Draft, brief: ResearchBrief, icp: ICPConfig, env: Env
) -> tuple[dict[str, float], str, str]:
    if not env.anthropic_api_key:
        return {}, "no ANTHROPIC_API_KEY — llm-judge skipped", "stub"

    client = AsyncAnthropic(api_key=env.anthropic_api_key)
    user = dedent(f"""\
        Requested tone: {icp.tone}
        Brief headline: {brief.headline}
        Signals anchored in brief.why_now: {brief.why_now}
        Hooks: {json.dumps(brief.hooks[:3])}

        DRAFT SUBJECT: {draft.subject or '(none)'}
        DRAFT BODY:
        {draft.body}

        Score this draft. JSON only.
    """)
    msg = await client.messages.create(
        model=env.claude_model_fast,       # cheaper judge
        max_tokens=220,
        system=[
            {
                "type": "text",
                "text": JUDGE_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user}],
    )
    if not ledger_disabled():
        LEDGER.record("judge", env.claude_model_fast, getattr(msg, "usage", None))
    record_from_response(msg, model=env.claude_model_fast, stage="judge")
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    data = _safe_json(text)
    return (
        {
            "signal_anchoring": float(data.get("signal_anchoring", 50.0)),
            "personalization": float(data.get("personalization", 50.0)),
            "tone": float(data.get("tone", 60.0)),
        },
        str(data.get("rationale", ""))[:280],
        env.claude_model_fast,
    )


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

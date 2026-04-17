"""Visitor demo: enter your company → get 3-5 lead accounts with all their signals.

Thin wrapper on the existing pipeline modules:
  1. Scrape visitor's company context (fetch_company_context).
  2. Ask Claude to infer their ICP (target titles + signal weights).
  3. Pull signals from a curated candidate pool (Greenhouse + Ashby + GitHub +
     SEC + news RSS) — cached across visitors.
  4. Score the pool against the inferred ICP, return top 5.

No cold emails are generated. Visitors see the lead + the signals only.

Deploy:
  - Streamlit Cloud: connect the repo, entry point `streamlit_app.py`,
    set `ANTHROPIC_API_KEY` in Secrets.
  - Local:  uv run streamlit run streamlit_app.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime as _dt
from textwrap import dedent
from urllib.parse import urlparse

import httpx
import streamlit as st
from anthropic import AsyncAnthropic

from signalforge.config import Env, ICPConfig
from signalforge.enrichment import fetch_company_context
from signalforge.models import Company, EnrichedAccount, Signal
from signalforge.scoring import score_account
from signalforge.signals import REGISTRY
from signalforge.signals.base import SourceContext

# ---------- LLM backend switch ----------------------------------------------
# Set LLM_BACKEND=ollama to route ICP inference to a local Ollama model
# (no API cost, no key required — but the app must run on a machine with
# Ollama listening, so Streamlit Cloud won't work with this path).
LLM_BACKEND = os.environ.get("LLM_BACKEND", "anthropic").lower()
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")

st.set_page_config(
    page_title="SignalForge — Issue No. 01",
    page_icon="§",
    layout="centered",
    initial_sidebar_state="collapsed",
)


# ---------- editorial stylesheet --------------------------------------------
# A cream-paper periodical dropped onto Streamlit's component set. The CSS
# targets Streamlit's internal data-testid hooks to replace the defaults
# (blue primary, sans-serif body, rounded pills) with an ink-on-cream
# editorial system — Fraunces for display, IBM Plex Sans for body,
# IBM Plex Mono for § numbers and wire-service bylines.
_STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300;0,9..144,400;0,9..144,500;0,9..144,600;1,9..144,300;1,9..144,400&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

:root {
  --paper: #f7f3ea;
  --paper-2: #efeadf;
  --paper-3: #e7dfd0;
  --ink: #1a1814;
  --ink-2: #403a32;
  --ink-3: #827868;
  --ink-4: #a79d8d;
  --rule: rgba(26, 24, 20, 0.12);
  --rule-2: rgba(26, 24, 20, 0.22);
  --carmine: #9c3324;
  --serif: 'Fraunces', 'Iowan Old Style', Georgia, serif;
  --sans: 'IBM Plex Sans', -apple-system, system-ui, sans-serif;
  --mono: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
}

html, body, [data-testid="stAppViewContainer"], .stApp {
  background: var(--paper) !important;
  color: var(--ink);
  font-family: var(--sans);
}
.main .block-container {
  max-width: 780px;
  padding-top: 3rem;
  padding-bottom: 4rem;
}

/* Strip Streamlit's chrome */
header[data-testid="stHeader"] { background: transparent !important; height: 0 !important; }
footer { visibility: hidden; }
#MainMenu { visibility: hidden; }

/* ---------- Masthead --------------------------------------------------- */
.sf-top-rule { height: 3px !important; background: var(--ink) !important; margin: 0 0 24px !important; width: 100% !important; }
.sf-mast-kicker {
  font-family: var(--mono) !important;
  font-size: 10.5px !important;
  letter-spacing: 0.14em !important;
  text-transform: uppercase !important;
  color: var(--ink-3) !important;
  display: flex !important; justify-content: space-between !important; align-items: center !important;
  margin-bottom: 32px !important;
}
.sf-mast-title {
  font-family: var(--serif) !important;
  font-weight: 400 !important;
  font-size: 64px !important;
  line-height: 0.98 !important;
  letter-spacing: -0.015em !important;
  color: var(--ink) !important;
  margin: 0 0 24px !important;
  font-variation-settings: "opsz" 144 !important;
}
.sf-mast-title em {
  font-style: italic !important;
  font-weight: 300 !important;
  color: var(--ink-2) !important;
}
.sf-lede {
  font-family: var(--serif) !important;
  font-weight: 300 !important;
  font-size: 19px !important;
  line-height: 1.55 !important;
  color: var(--ink-2) !important;
  max-width: 54ch !important;
  margin: 0 0 40px !important;
}
.sf-lede em { font-style: italic !important; color: var(--carmine) !important; }

/* ---------- Form ------------------------------------------------------- */
[data-testid="stForm"] {
  border: none !important;
  padding: 0 !important;
  background: transparent !important;
}
[data-testid="stTextInput"] label,
[data-testid="stTextInput"] div[data-baseweb="form-control-message"] { display: none !important; }
[data-testid="stTextInput"] > div { background: transparent !important; }
[data-testid="stTextInput"] div[data-baseweb="input"],
[data-testid="stTextInput"] div[data-baseweb="base-input"] {
  background: var(--paper-2) !important;
  border: 1px solid var(--rule-2) !important;
  border-radius: 0 !important;
  transition: border-color 160ms ease;
}
[data-testid="stTextInput"] input {
  background: transparent !important;
  color: var(--ink) !important;
  font-family: var(--sans) !important;
  font-size: 15px !important;
  padding: 14px 16px !important;
  caret-color: var(--ink);
}
[data-testid="stTextInput"] div[data-baseweb="input"]:focus-within {
  border-color: var(--ink) !important;
  box-shadow: none !important;
}
[data-testid="stTextInput"] input::placeholder { color: var(--ink-4) !important; }

.stButton > button, [data-testid="stFormSubmitButton"] button {
  background: var(--ink) !important;
  color: var(--paper) !important;
  border: 1px solid var(--ink) !important;
  border-radius: 0 !important;
  padding: 12px 22px !important;
  font-family: var(--mono) !important;
  font-size: 10.5px !important;
  font-weight: 500 !important;
  letter-spacing: 0.14em !important;
  text-transform: uppercase !important;
  box-shadow: none !important;
  transition: background 160ms ease, color 160ms ease;
}
.stButton > button:hover, [data-testid="stFormSubmitButton"] button:hover {
  background: var(--paper) !important;
  color: var(--ink) !important;
}

/* ---------- Section head ----------------------------------------------- */
.sf-sec {
  margin-top: 56px !important;
  padding-top: 24px !important;
  border-top: 1px solid var(--rule) !important;
}
.sf-sec-head {
  display: flex !important; align-items: baseline !important; gap: 16px !important;
  margin-bottom: 24px !important;
}
.sf-sec-num {
  font-family: var(--mono) !important;
  font-size: 11px !important;
  letter-spacing: 0.14em !important;
  text-transform: uppercase !important;
  color: var(--ink-3) !important;
  min-width: 56px !important;
}
.sf-sec-title {
  font-family: var(--serif) !important;
  font-weight: 400 !important;
  font-style: italic !important;
  font-size: 28px !important;
  line-height: 1.2 !important;
  color: var(--ink) !important;
  margin: 0 !important;
}

/* ---------- The brief (§ 01) ------------------------------------------ */
.sf-brief-summary {
  font-family: var(--serif) !important;
  font-weight: 300 !important;
  font-size: 22px !important;
  line-height: 1.45 !important;
  color: var(--ink) !important;
  margin: 0 0 32px !important;
  max-width: 60ch !important;
}
.sf-brief-grid {
  display: grid !important;
  grid-template-columns: 1fr 1fr !important;
  gap: 40px !important;
  border-top: 1px solid var(--rule) !important;
  padding-top: 20px !important;
}
.sf-brief-label {
  font-family: var(--mono) !important;
  font-size: 10px !important;
  letter-spacing: 0.14em !important;
  text-transform: uppercase !important;
  color: var(--ink-3) !important;
  margin: 0 0 12px !important;
}
.sf-title-list {
  list-style: none !important; padding: 0 !important; margin: 0 !important;
  font-family: var(--sans) !important; font-size: 15px !important; line-height: 1.9 !important;
  color: var(--ink) !important;
}
.sf-title-list li { list-style: none !important; }
.sf-title-list li::before {
  content: "— " !important; color: var(--ink-4) !important;
}
.sf-weight-row {
  display: grid !important;
  grid-template-columns: 1fr 40px !important;
  align-items: baseline !important;
  font-family: var(--mono) !important;
  font-size: 12px !important;
  color: var(--ink-2) !important;
  padding: 6px 0 !important;
  border-bottom: 1px dotted var(--rule) !important;
}
.sf-weight-row:last-child { border-bottom: none !important; }
.sf-weight-label {
  letter-spacing: 0.06em !important;
  text-transform: uppercase !important;
}
.sf-weight-val {
  font-weight: 500 !important;
  color: var(--ink) !important;
  text-align: right !important;
  font-variant-numeric: tabular-nums !important;
}
.sf-why {
  font-family: var(--serif) !important;
  font-style: italic !important;
  font-size: 14px !important;
  line-height: 1.5 !important;
  color: var(--ink-3) !important;
  margin-top: 20px !important;
  padding-left: 16px !important;
  border-left: 1px solid var(--rule-2) !important;
  max-width: 60ch !important;
}

/* ---------- Dispatches (§ 02) ----------------------------------------- */
.sf-lead {
  padding: 32px 0 28px !important;
  border-top: 1px solid var(--rule) !important;
}
.sf-lead:first-of-type { border-top: 1px solid var(--rule-2) !important; }
.sf-lead-num {
  font-family: var(--mono) !important;
  font-size: 10px !important;
  letter-spacing: 0.18em !important;
  text-transform: uppercase !important;
  color: var(--ink-3) !important;
  margin-bottom: 4px !important;
}
.sf-lead-num .carmine { color: var(--carmine) !important; }
.sf-lead-name {
  font-family: var(--serif) !important;
  font-weight: 400 !important;
  font-size: 32px !important;
  line-height: 1.05 !important;
  color: var(--ink) !important;
  margin: 0 0 10px !important;
  font-variation-settings: "opsz" 72 !important;
}
.sf-lead-meta {
  font-family: var(--mono) !important;
  font-size: 10.5px !important;
  letter-spacing: 0.08em !important;
  text-transform: uppercase !important;
  color: var(--ink-3) !important;
  margin-bottom: 18px !important;
}
.sf-lead-meta .icp {
  color: var(--ink) !important;
  font-weight: 500 !important;
  font-variant-numeric: tabular-nums !important;
}
.sf-dispatches { margin-top: 6px !important; }
.sf-dispatch {
  display: grid !important;
  grid-template-columns: 180px 1fr !important;
  gap: 18px !important;
  padding: 8px 0 !important;
  border-top: 1px dotted var(--rule) !important;
  font-size: 14px !important;
  line-height: 1.5 !important;
  color: var(--ink-2) !important;
}
.sf-dispatch:first-of-type { border-top: none !important; }
.sf-dispatch .byline {
  font-family: var(--mono) !important;
  font-size: 10px !important;
  letter-spacing: 0.1em !important;
  text-transform: uppercase !important;
  color: var(--ink-3) !important;
  font-variant-numeric: tabular-nums !important;
  padding-top: 3px !important;
}
.sf-dispatch .body {
  font-family: var(--sans) !important;
  color: var(--ink) !important;
}
.sf-dispatch .body a {
  color: var(--ink) !important;
  text-decoration: none !important;
  border-bottom: 1px solid var(--rule-2) !important;
  transition: border-color 140ms ease !important;
}
.sf-dispatch .body a:hover { border-color: var(--ink) !important; }
.sf-dispatch-more {
  font-family: var(--mono) !important;
  font-size: 10px !important;
  letter-spacing: 0.1em !important;
  text-transform: uppercase !important;
  color: var(--ink-3) !important;
  padding-top: 10px !important;
  grid-column: 2 !important;
}

/* ---------- Colophon --------------------------------------------------- */
.sf-colophon {
  margin-top: 72px !important;
  padding-top: 24px !important;
  border-top: 1px solid var(--rule) !important;
  font-family: var(--mono) !important;
  font-size: 10px !important;
  letter-spacing: 0.1em !important;
  text-transform: uppercase !important;
  color: var(--ink-3) !important;
  line-height: 1.8 !important;
}
.sf-colophon em { color: var(--ink) !important; font-style: normal !important; }
.sf-colophon a { color: var(--ink) !important; text-decoration: none !important; border-bottom: 1px solid var(--rule-2) !important; }

/* ---------- Streamlit overrides: alerts, status, captions ------------ */
[data-testid="stAlert"],
div[data-baseweb="notification"] {
  border-radius: 0 !important;
  background: var(--paper-2) !important;
  border: 1px solid var(--rule-2) !important;
  color: var(--ink) !important;
  font-family: var(--sans) !important;
  box-shadow: none !important;
  padding: 14px 16px !important;
}
[data-testid="stAlert"] p,
div[data-baseweb="notification"] p { color: var(--ink) !important; }

[data-testid="stStatusWidget"], [data-testid="stStatus"] {
  background: var(--paper-2) !important;
  border: 1px solid var(--rule) !important;
  border-radius: 0 !important;
  box-shadow: none !important;
  font-family: var(--mono) !important;
  font-size: 12px !important;
  color: var(--ink-2) !important;
}

[data-testid="stCaptionContainer"], div[data-testid="stCaption"] {
  color: var(--ink-3) !important;
  font-family: var(--mono) !important;
  font-size: 10px !important;
  letter-spacing: 0.08em !important;
  text-transform: uppercase !important;
}

/* Removed spurious dividers */
hr { border-color: var(--rule) !important; }

/* Selection */
::selection { background: var(--ink); color: var(--paper); }
</style>
"""


def _inject_style() -> None:
    # Streamlit rebuilds the DOM on every rerun, so the style block has to be
    # injected on every call — not guarded by session state.
    st.markdown(_STYLE, unsafe_allow_html=True)


# ---------- candidate pool --------------------------------------------------

_POOL_COMPANIES = [
    # AI-native
    "anthropic", "openai", "perplexity", "glean", "cohere", "mistralai",
    "scaleai", "huggingface", "runwayml", "elevenlabs",
    # GTM / devtools
    "notion", "ramp", "clay", "unify", "attio", "retool", "linear",
    "vercel", "supabase", "render", "fly", "replicate",
    # Enterprise SaaS
    "brex", "mercury", "rippling", "deel", "gusto",
    # YC-adjacent
    "cal", "resend", "trigger", "dub",
]

_HIRING_KEYWORDS = [
    "sdr", "bdr", "gtm", "go-to-market", "revenue",
    "sales development", "growth", "developer relations",
    "head of", "vp ", "director", "chief",
]

CANDIDATE_SOURCES = {
    # Greenhouse boards — curated list of companies publishing on GH.
    "greenhouse": {
        "enabled": True,
        "boards": [
            "anthropic", "scaleai", "openai", "perplexity",
            "glean", "cohere", "mistralai", "huggingface", "runwayml",
            "elevenlabs", "vercel", "linear", "replicate", "supabase",
            "rippling", "deel", "gusto", "brex",
        ],
        "hiring_keywords": _HIRING_KEYWORDS,
    },
    # Ashby boards — separate company set.
    "ashby": {
        "enabled": True,
        "boards": [
            "notion", "ramp", "clay", "unify", "attio", "retool",
            "cal", "resend", "trigger", "dub", "mercury",
        ],
        "hiring_keywords": _HIRING_KEYWORDS,
    },
    "github": {
        "enabled": True,
        "orgs": [
            "anthropics", "openai", "scaleai", "clay-labs", "unifygtm",
            "vercel", "supabase", "huggingface", "cohere-ai", "mistralai",
        ],
        "lookback_days": 30,
    },
    "sec_edgar": {
        "enabled": True,
        # Public companies whose 8-K/S-1/10-Q tell us meaningful GTM signals.
        "tickers": ["CRM", "HUBS", "RNG", "NOW", "SNOW", "MDB", "NET", "DDOG"],
        "lookback_days": 60,
    },
    "news_rss": {
        "enabled": True,
        "match_boards": _POOL_COMPANIES,
    },
    "hackernews": {
        "enabled": True,
        "companies": _POOL_COMPANIES,
        "lookback_days": 60,
        "min_points": 25,
        "results_per_company": 3,
    },
    "product_hunt": {
        # No-op unless PRODUCT_HUNT_TOKEN is set in the env.
        "enabled": True,
        "companies": _POOL_COMPANIES,
        "lookback_days": 60,
        "min_votes": 50,
    },
}


# ---------- helpers ---------------------------------------------------------

def _normalize(domain_or_url: str) -> str | None:
    s = (domain_or_url or "").strip().lower()
    if not s:
        return None
    if "://" in s:
        s = urlparse(s).netloc or urlparse(s).path
    s = s.removeprefix("www.").removeprefix("careers.").removeprefix("jobs.")
    s = s.split("/", 1)[0]
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", s):
        return None
    return s


@st.cache_data(ttl=60 * 60 * 24 * 7, show_spinner=False)
def _resolve_via_clearbit(query: str) -> tuple[str, str] | None:
    """Free, no-auth Clearbit Autocomplete. Lets a visitor type "stripe" or
    "Notion" and get back (domain, display_name). Cached 7d — these mappings
    don't churn. Returns None if no confident match."""
    q = (query or "").strip()
    if not q:
        return None
    try:
        import httpx as _httpx
        r = _httpx.get(
            "https://autocomplete.clearbit.com/v1/companies/suggest",
            params={"query": q},
            timeout=5.0,
        )
        r.raise_for_status()
        hits = r.json()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(hits, list) or not hits:
        return None
    top = hits[0]
    domain = (top.get("domain") or "").lower().strip()
    name = (top.get("name") or "").strip()
    if not domain or not name:
        return None
    return domain, name


def _resolve_input(raw: str) -> tuple[str, str | None] | None:
    """Accept either a domain, a URL, or a free-text company name.
    Returns (domain, optional_display_name) or None if unresolvable."""
    # 1. Try domain normalization first.
    direct = _normalize(raw)
    if direct:
        return direct, None
    # 2. Fall back to Clearbit autocomplete if it looks like a company name
    #    (letters + digits + common punctuation, reasonable length).
    stripped = (raw or "").strip()
    if not stripped or len(stripped) > 60:
        return None
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9 .&'\-]{1,58}$", stripped):
        return None
    hit = _resolve_via_clearbit(stripped)
    if hit:
        return hit
    return None


def _icp_from(raw: dict) -> ICPConfig:
    return ICPConfig(
        name=raw.get("name", "visitor-demo"),
        target_titles=raw.get("target_titles", []),
        firmographics=raw.get("firmographics", {}),
        signal_weights=raw.get("signal_weights", {}),
        min_icp_score=float(raw.get("min_icp_score", 0)),
        tone=raw.get("tone", "direct"),
        sender=raw.get("sender", {}),
        sources=raw.get("sources", {}),
        raw=raw,
    )


def _pool_icp() -> ICPConfig:
    return _icp_from({
        "name": "pool",
        "target_titles": [],
        "signal_weights": {},
        "min_icp_score": 0,
        "sources": CANDIDATE_SOURCES,
    })


# ---------- inference -------------------------------------------------------

ICP_INFERENCE_SYSTEM = dedent("""\
    You help a GTM engineer infer their Ideal Customer Profile from one
    company's public context. Read the context carefully. Output JSON ONLY:

    {
      "company_summary": str,           # one sentence about what this company does
      "target_titles": [str, str, ...], # 3-5 buyer personas that would buy from them
      "target_industries": [str, ...],  # 2-4 industries their buyers operate in
      "signal_weights": {               # which buying signals matter most for THIS company's sale
        "hiring": int,                  # 0-30, how much hiring signals matter
        "funding": int,                 # 0-30
        "exec_change": int,             # 0-30
        "product_launch": int,          # 0-30
        "press": int,                   # 0-30
        "github_activity": int,         # 0-30
        "earnings": int                 # 0-30
      },
      "why": str                        # one-sentence rationale for the weights
    }

    Rules:
    - Pick weights that reflect what actually triggers buying for THEIR product.
      E.g. devtools → github_activity higher; finance → earnings higher;
      HR/recruiting → hiring higher; compliance/security → exec_change higher.
    - No hedging, no "TBD". Make the call.
""")


async def _infer_icp(domain: str, ctx_text: str, name_hint: str, env: Env) -> dict:
    user = dedent(f"""\
        Company domain: {domain}
        Public context snippet (scraped):
        ---
        {ctx_text[:2500] or '(empty — site did not return readable text)'}
        ---
        Return JSON only.
    """)

    if LLM_BACKEND == "ollama":
        return await _infer_icp_ollama(user)

    if not env.anthropic_api_key:
        # Stub inference so the app still runs without any LLM in dev.
        return {
            "company_summary": f"{name_hint or domain} (no LLM available — generic ICP used)",
            "target_titles": ["VP Engineering", "Head of Growth", "CTO"],
            "target_industries": ["SaaS"],
            "signal_weights": {
                "hiring": 25, "funding": 20, "exec_change": 15, "product_launch": 10,
                "press": 10, "github_activity": 10, "earnings": 5,
            },
            "why": "stub",
        }

    client = AsyncAnthropic(api_key=env.anthropic_api_key)
    msg = await client.messages.create(
        model=env.claude_model_fast,
        max_tokens=700,
        system=[{"type": "text", "text": ICP_INFERENCE_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    return _safe_json(text)


async def _infer_icp_ollama(user_prompt: str) -> dict:
    """Local-first ICP inference via Ollama's /api/chat. Requires an Ollama
    server reachable at OLLAMA_HOST with OLLAMA_MODEL pulled."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": ICP_INFERENCE_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "format": "json",
        "stream": False,
        # keep_alive pins the model in Ollama's RAM for an hour between calls
        # so subsequent visitors skip the ~10s model-load cold start.
        "keep_alive": "1h",
        "options": {"temperature": 0.3, "num_predict": 1200},
    }
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(f"{OLLAMA_HOST}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        return {
            "company_summary": f"(ollama error: {e.__class__.__name__})",
            "target_titles": ["VP Engineering", "Head of Growth", "CTO"],
            "target_industries": ["SaaS"],
            "signal_weights": {
                "hiring": 25, "funding": 20, "exec_change": 15, "product_launch": 10,
                "press": 10, "github_activity": 10, "earnings": 5,
            },
            "why": f"ollama fallback ({OLLAMA_MODEL} @ {OLLAMA_HOST})",
        }
    content = (data.get("message") or {}).get("content", "")
    parsed = _safe_json(content)
    # Tag so the UI can surface which backend was used.
    if parsed:
        parsed.setdefault("_backend", f"ollama:{OLLAMA_MODEL}")
    return parsed


def _safe_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        s, e = t.find("{"), t.rfind("}")
        if s >= 0 and e > s:
            try:
                return json.loads(t[s : e + 1])
            except json.JSONDecodeError:
                pass
        return {}


# ---------- signal pool (cached across visitors) ----------------------------

@st.cache_data(ttl=60 * 60, show_spinner=False)
def _get_pool() -> list[dict]:
    """Fetch signals from the candidate pool once per hour (shared across visitors).

    Signals returned as plain dicts so Streamlit's cache can serialize them.
    """
    return asyncio.run(_fetch_pool())


async def _fetch_pool() -> list[dict]:
    env = Env.load()
    async with httpx.AsyncClient(timeout=30.0) as http:
        ctx = SourceContext(env=env, http=http)
        tasks = []
        for key, cfg in CANDIDATE_SOURCES.items():
            klass = REGISTRY.get(key)
            if klass is None:
                continue
            tasks.append(klass().collect(ctx, cfg))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    flat: list[Signal] = []
    for r in results:
        if isinstance(r, Exception):
            continue
        flat.extend(r or [])
    return [_signal_to_dict(s) for s in flat]


def _signal_to_dict(s: Signal) -> dict:
    return {
        "kind": s.kind.value,
        "source": s.source,
        "company_domain": s.company_domain,
        "company_name": s.company_name,
        "title": s.title,
        "url": s.url,
        "strength": float(s.strength),
    }


# ---------- score the pool against visitor's inferred ICP -------------------

def _score_pool(pool: list[dict], inferred: dict) -> list[dict]:
    signal_weights = inferred.get("signal_weights", {})
    icp = _icp_from({
        "name": "visitor",
        "signal_weights": signal_weights,
        "firmographics": {},
        "min_icp_score": 0,
    })

    # Group pool by company_domain and rebuild minimal Signal/Account objects to reuse score_account.
    from signalforge.models import SignalKind  # local import keeps module boot light

    buckets: dict[str, list[Signal]] = defaultdict(list)
    for d in pool:
        try:
            kind = SignalKind(d["kind"])
        except ValueError:
            continue
        buckets[d["company_domain"]].append(
            Signal(
                kind=kind, source=d["source"],
                company_domain=d["company_domain"], company_name=d.get("company_name"),
                title=d["title"], url=d.get("url"),
                strength=float(d.get("strength", 0.5)),
            )
        )

    scored: list[tuple[EnrichedAccount, float]] = []
    for domain, sigs in buckets.items():
        name = next((s.company_name for s in sigs if s.company_name), None)
        acc = EnrichedAccount(company=Company(domain=domain, name=name), signals=sigs)
        scored_acc = score_account(acc, icp)
        scored.append((scored_acc, scored_acc.icp_score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [
        {
            "domain": a.company.domain,
            "name": a.company.name or a.company.domain,
            "icp_score": a.icp_score,
            "signals": [_signal_to_dict(s) for s in a.signals],
        }
        for a, _ in scored
    ]


# Cached piece 1: scrape the visitor's company page. 24h TTL — About pages don't churn.
@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def _get_ctx_cached(visitor_domain: str) -> dict:
    env = Env.load()
    ctx = asyncio.run(
        fetch_company_context(visitor_domain, env, max_chars=2500, timeout=10.0)
    )
    if ctx is None:
        return {"text": "", "source": None, "title": None, "urls": []}
    return {"text": ctx.text, "source": ctx.source, "title": ctx.title, "urls": ctx.urls_seen}


# Cached piece 2: ICP inference from scraped text. Key by (domain, content-hash)
# so prompt or scrape changes correctly invalidate the cache.
@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def _get_inference_cached(domain: str, ctx_hash: str, ctx_text: str) -> dict:
    env = Env.load()
    return asyncio.run(_infer_icp(domain, ctx_text, "", env))


def _ctx_hash(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _analyze_with_progress(visitor_domain: str, status) -> dict:
    """Step-by-step analysis. Each step writes a line into the Streamlit
    status block so the visitor sees what's happening, and the pool fetch
    runs in parallel with the visitor's company scrape on a cold start."""
    import concurrent.futures

    # Cold-path parallelism: kick off pool fetch in a thread while we scrape
    # the visitor's own company. When pool is hot (cache hit) this returns
    # immediately; when cold (~15s), we recover that time against the scrape.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut_pool = ex.submit(_get_pool)
        status.write("• Scraping the visitor's public page…")
        fut_ctx = ex.submit(_get_ctx_cached, visitor_domain)
        ctx = fut_ctx.result()
        pool = fut_pool.result()

    status.write(f"  ✓ scraped {len(ctx['text'])} chars from {ctx['source'] or 'nothing'}")
    status.write(f"• Inferring your ICP ({LLM_BACKEND}:{OLLAMA_MODEL if LLM_BACKEND == 'ollama' else 'claude'})…")
    inferred = _get_inference_cached(visitor_domain, _ctx_hash(ctx["text"]), ctx["text"])
    status.write(
        f"  ✓ {len(inferred.get('target_titles', []))} titles · "
        f"{len(inferred.get('signal_weights', {}))} weighted signal kinds"
    )

    status.write(f"• Scoring {len(pool)} pool signals against your ICP…")
    ranked = _score_pool(pool, inferred)
    ranked = [r for r in ranked if r["domain"] != visitor_domain]
    status.write(f"  ✓ top lead: {ranked[0]['name'] if ranked else '(none)'}")

    return {
        "visitor_domain": visitor_domain,
        "ctx_len": len(ctx["text"]),
        "ctx_source": ctx["source"],
        "inferred": inferred,
        "leads": ranked[:5],
        "pool_size": len(pool),
    }


# ---------- UI --------------------------------------------------------------

_inject_style()

# ── Masthead ────────────────────────────────────────────────────────────────
st.html(
    f"""
<div class="sf-top-rule"></div>
<div class="sf-mast-kicker">
  <span>SIGNALFORGE · ISSUE No. 01</span>
  <span>{_dt.utcnow().strftime('%B %Y').upper()}</span>
</div>
<h1 class="sf-mast-title">The <em>dossier</em><br/>you'd write<br/>before the cold email.</h1>
<p class="sf-lede">
Type a company. SignalForge reads its public context, infers an <em>ideal customer profile</em>,
and files five lead accounts from a curated pool — each with its full signal ledger.
<br/><br/>
No cold email is generated here. That's a different tool.
</p>
"""
)

with st.form("demo", clear_on_submit=False):
    raw_input = st.text_input(
        "domain",
        placeholder="ramp.com  ·  Linear  ·  stripe",
        label_visibility="collapsed",
        autocomplete="off",
    )
    submitted = st.form_submit_button("File the request →")

if submitted:
    resolved = _resolve_input(raw_input)
    if not resolved:
        st.error(
            "Couldn't resolve that — try a domain like `example.com` or a company name like `Stripe`."
        )
        st.stop()
    domain, display_hint = resolved
    if display_hint:
        st.html(
            f'<div class="sf-colophon" style="margin-top:12px;">Matched <em>{display_hint}</em> → {domain}</div>'
        )

    now = time.time()
    last = st.session_state.get("last_run", 0)
    if now - last < 15:
        st.warning("Easy — wait a few seconds between requests.")
        st.stop()
    st.session_state["last_run"] = now

    with st.status(f"Filing on {domain}", expanded=True) as status:
        try:
            result = _analyze_with_progress(domain, status)
            status.update(label=f"Filed · {domain}", state="complete", expanded=False)
        except Exception as e:  # noqa: BLE001
            status.update(label=f"Failed · {domain}", state="error")
            st.error(f"Pipeline error: {e.__class__.__name__}: {e}")
            st.stop()

    inf = result["inferred"] or {}
    weights = inf.get("signal_weights") or {}
    leads = result["leads"] or []

    # ── § 01 · The brief ─────────────────────────────────────────────────
    summary = (inf.get("company_summary") or "—").strip()
    titles = inf.get("target_titles") or []
    why = (inf.get("why") or "").strip()

    title_li = "\n".join(f"<li>{t}</li>" for t in titles[:6]) or "<li>—</li>"
    weight_rows = "\n".join(
        f'<div class="sf-weight-row"><span class="sf-weight-label">{k}</span>'
        f'<span class="sf-weight-val">{int(v):>2}</span></div>'
        for k, v in sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
    ) or '<div class="sf-weight-row"><span class="sf-weight-label">—</span><span class="sf-weight-val">—</span></div>'
    why_html = f'<p class="sf-why">{why}</p>' if why else ""

    st.html(
        f"""
<section class="sf-sec">
  <div class="sf-sec-head">
    <div class="sf-sec-num">§ 01</div>
    <h2 class="sf-sec-title">The brief</h2>
  </div>
  <p class="sf-brief-summary">{summary}</p>
  <div class="sf-brief-grid">
    <div>
      <div class="sf-brief-label">Target titles</div>
      <ul class="sf-title-list">{title_li}</ul>
    </div>
    <div>
      <div class="sf-brief-label">Signal weights</div>
      {weight_rows}
    </div>
  </div>
  {why_html}
</section>
"""
    )

    # ── § 02 · Dispatches ────────────────────────────────────────────────
    st.html(
        f"""
<section class="sf-sec">
  <div class="sf-sec-head">
    <div class="sf-sec-num">§ 02</div>
    <h2 class="sf-sec-title">Dispatches — top {len(leads)} of {result['pool_size']} pool signals</h2>
  </div>
</section>
"""
    )

    if not leads:
        st.info(
            "No leads matched. Try a different visitor domain, or check back once the hourly pool refreshes."
        )

    for rank, lead in enumerate(leads, 1):
        is_top = rank == 1
        num_html = (
            '<span class="carmine">— Dispatch 01 · top match —</span>'
            if is_top else f"— Dispatch {rank:02d} —"
        )
        dispatches: list[str] = []
        shown = lead["signals"][:10]
        for s in shown:
            byline = f'{s["source"].upper()} · {s["kind"].replace("_", " ").upper()} · {s["strength"]:.2f}'
            if s["url"]:
                body = f'<a href="{s["url"]}" target="_blank" rel="noopener">{s["title"]}</a>'
            else:
                body = s["title"]
            dispatches.append(
                f'<div class="sf-dispatch"><div class="byline">{byline}</div>'
                f'<div class="body">{body}</div></div>'
            )
        extra = len(lead["signals"]) - len(shown)
        more_html = (
            f'<div class="sf-dispatch"><div></div>'
            f'<div class="sf-dispatch-more">+ {extra} more on file</div></div>'
            if extra > 0 else ""
        )
        st.html(
            f"""
<article class="sf-lead">
  <div class="sf-lead-num">{num_html}</div>
  <h3 class="sf-lead-name">{lead['name']}</h3>
  <div class="sf-lead-meta">
    {lead['domain']} &nbsp;·&nbsp; icp <span class="icp">{lead['icp_score']:.0f}</span>
    &nbsp;·&nbsp; {len(lead['signals'])} signals on file
  </div>
  <div class="sf-dispatches">
    {''.join(dispatches)}
    {more_html}
  </div>
</article>
"""
        )

    # ── Colophon ─────────────────────────────────────────────────────────
    backend = OLLAMA_MODEL if LLM_BACKEND == "ollama" else "claude"
    st.html(
        f"""
<div class="sf-colophon">
  <em>Colophon.</em>
  Inference ran on <em>{backend}</em>. Pool refreshed hourly · per-visitor file cached 24h.
  Sources on file: Greenhouse · Ashby · Lever · GitHub · SEC EDGAR · News RSS · Hacker News · Product Hunt · Exa.
  Name → domain via Clearbit Autocomplete.
  Source code <a href="https://github.com/abhip2006/signalforge" target="_blank" rel="noopener">on the wire</a>.
</div>
"""
    )

else:
    st.html(
        """
<div class="sf-colophon" style="margin-top:64px;">
  <em>How this reads.</em>
  §&nbsp;01 prints the brief — the ICP inferred from your public page.
  §&nbsp;02 files up to five dispatches — lead accounts with their signal ledger.
  Source code <a href="https://github.com/abhip2006/signalforge" target="_blank" rel="noopener">on the wire</a>.
</div>
"""
    )

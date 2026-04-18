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
.main .block-container,
[data-testid="stMainBlockContainer"],
.stMainBlockContainer {
  max-width: 1240px !important;
  padding-top: 3rem !important;
  padding-bottom: 4rem !important;
}
/* Keep masthead / form / status / colophon in a narrow editorial column
   (~780px) even as the container widens. Only .sf-split uses the full
   1240px to host the two-panel layout. */
.sf-narrow, .sf-colophon { max-width: 780px; margin-left: auto !important; margin-right: auto !important; }
[data-testid="stForm"] { max-width: 780px !important; margin-left: auto !important; margin-right: auto !important; }
[data-testid="stStatus"],
[data-testid="stStatusWidget"],
[data-testid="stAlert"],
div[data-baseweb="notification"] {
  max-width: 780px !important;
  margin-left: auto !important;
  margin-right: auto !important;
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

/* ---------- Split layout: sticky brief | scrolling dispatches --------- */
.sf-split {
  display: grid !important;
  grid-template-columns: 5fr 7fr !important;
  gap: 64px !important;
  margin-top: 56px !important;
  padding-top: 24px !important;
  border-top: 1px solid var(--rule) !important;
  align-items: start !important;
}
.sf-split .sf-sec {
  /* Inside the split, kill the top rule + margin — the split owns them. */
  margin-top: 0 !important;
  padding-top: 0 !important;
  border-top: none !important;
}
.sf-split > .sf-col-left {
  position: sticky !important;
  top: 24px !important;
  align-self: start !important;
  max-height: calc(100vh - 48px) !important;
  overflow-y: auto !important;
}
.sf-split > .sf-col-right {
  min-width: 0 !important; /* let inner grids shrink */
}
.sf-split .sf-col-left::-webkit-scrollbar { width: 4px; }
.sf-split .sf-col-left::-webkit-scrollbar-thumb { background: var(--rule-2); }

/* § 02 Dispatches head inside the right column: keep the header inline. */
.sf-split .sf-col-right .sf-sec-head { margin-bottom: 16px !important; }

/* Brief in split mode: let the weights grid stack (one column now) */
.sf-split .sf-brief-grid {
  grid-template-columns: 1fr !important;
  gap: 28px !important;
}

@media (max-width: 960px) {
  .sf-split {
    grid-template-columns: 1fr !important;
    gap: 0 !important;
  }
  .sf-split > .sf-col-left {
    position: static !important;
    max-height: none !important;
    overflow: visible !important;
  }
  .sf-split .sf-brief-grid {
    grid-template-columns: 1fr 1fr !important;
    gap: 40px !important;
  }
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

def _load_live_boards() -> dict[str, list[str]]:
    """Load the probed-live ATS board list. Written by tools/probe_boards.py
    into signalforge/resources/live_boards.json (repo-tracked)."""
    import json as _json
    from pathlib import Path as _P
    candidates = [
        _P(__file__).parent / "signalforge" / "resources" / "live_boards.json",
        _P("signalforge/resources/live_boards.json"),
    ]
    for path in candidates:
        if path.exists():
            try:
                return _json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                pass
    return {"greenhouse": [], "ashby": [], "lever": []}


def _load_sec_tickers() -> list[str]:
    """Load the curated SEC ticker list from signalforge/resources/sec_tickers.txt."""
    from pathlib import Path as _P
    candidates = [
        _P(__file__).parent / "signalforge" / "resources" / "sec_tickers.txt",
        _P("signalforge/resources/sec_tickers.txt"),
    ]
    for path in candidates:
        if path.exists():
            tokens: list[str] = []
            for raw_line in path.read_text().splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                tokens.extend(t.strip() for t in line.split() if t.strip())
            # Deduplicate preserving order
            seen: set[str] = set()
            out: list[str] = []
            for t in tokens:
                u = t.upper()
                if u not in seen:
                    seen.add(u)
                    out.append(u)
            return out
    return []


_LIVE_BOARDS = _load_live_boards()
_SEC_TICKERS = _load_sec_tickers()

# Semis + infra NOT on Greenhouse/Ashby but filing with the SEC — we pick
# them up via the EDGAR source. Names echo into HN/news_rss matching.
_SEMI_AND_PUBLIC_COMPANIES = [
    "nvidia", "amd", "intel", "broadcom", "qualcomm", "tsmc", "asml",
    "synopsys", "cadence", "arm", "micron", "marvell",
]

_POOL_COMPANIES = sorted(set(
    _LIVE_BOARDS.get("greenhouse", [])
    + _LIVE_BOARDS.get("ashby", [])
    + _LIVE_BOARDS.get("lever", [])
    + _SEMI_AND_PUBLIC_COMPANIES
))

_HIRING_KEYWORDS = [
    "sdr", "bdr", "gtm", "go-to-market", "revenue",
    "sales development", "growth", "developer relations",
    "head of", "vp ", "director", "chief",
]

CANDIDATE_SOURCES = {
    # Greenhouse boards — sourced from tools/probe_boards.py → data/live_boards.json.
    "greenhouse": {
        "enabled": True,
        "boards": _LIVE_BOARDS.get("greenhouse", []),
        "hiring_keywords": _HIRING_KEYWORDS,
    },
    # Ashby boards — sourced from the same probe.
    "ashby": {
        "enabled": True,
        "boards": _LIVE_BOARDS.get("ashby", []),
        "hiring_keywords": _HIRING_KEYWORDS,
    },
    # Lever boards — also from the probe.
    "lever": {
        "enabled": True,
        "boards": _LIVE_BOARDS.get("lever", []),
        "hiring_keywords": _HIRING_KEYWORDS,
    },
    "github": {
        "enabled": True,
        "orgs": [
            "anthropics", "openai", "scaleai", "clay-labs", "unifygtm",
            "vercel", "supabase", "huggingface", "cohere-ai", "mistralai",
            "cloudflare", "snyk", "okta", "datadog", "gitlabhq",
        ],
        "lookback_days": 30,
    },
    "sec_edgar": {
        "enabled": True,
        # Ticker list loaded from signalforge/resources/sec_tickers.txt —
        # ~400 public cos across SaaS / AI / semis / fintech / health /
        # industrial / consumer. Each ticker = 1 company in the pool.
        "tickers": _SEC_TICKERS,
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

_POOL_DISK_CACHE = os.environ.get(
    "SIGNALFORGE_POOL_CACHE",
    str((__import__("pathlib").Path(__file__).parent / "data" / "pool_cache.json").resolve()),
)
_POOL_DISK_TTL = int(os.environ.get("SIGNALFORGE_POOL_TTL_SECONDS", "3600"))


def _load_pool_from_disk() -> list[dict] | None:
    """Return a fresh enough cached pool from disk, else None."""
    import json as _json
    import time as _time
    from pathlib import Path as _P

    path = _P(_POOL_DISK_CACHE)
    if not path.exists():
        return None
    try:
        age = _time.time() - path.stat().st_mtime
        if age > _POOL_DISK_TTL:
            return None
        return _json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None


def _save_pool_to_disk(pool: list[dict]) -> None:
    import contextlib
    import json as _json
    from pathlib import Path as _P

    with contextlib.suppress(Exception):
        path = _P(_POOL_DISK_CACHE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(pool))


@st.cache_data(ttl=_POOL_DISK_TTL, show_spinner=False)
def _get_pool() -> list[dict]:
    """Fetch signals from the candidate pool. Tries disk cache first (survives
    Streamlit restarts), then refetches and persists. Shared across visitors
    via @st.cache_data for in-process reuse."""
    cached = _load_pool_from_disk()
    if cached is not None:
        return cached
    pool = asyncio.run(_fetch_pool())
    _save_pool_to_disk(pool)
    return pool


def _kick_off_pool_warmup() -> None:
    """Background-thread pre-warm so the first visitor hits a hot cache.
    Safe to call multiple times — the @st.cache_data decorator deduplicates."""
    import contextlib
    import threading

    def _warm() -> None:
        with contextlib.suppress(Exception):
            _get_pool()

    if st.session_state.get("_sf_pool_prewarm_started"):
        return
    st.session_state["_sf_pool_prewarm_started"] = True
    t = threading.Thread(target=_warm, name="signalforge-pool-prewarm", daemon=True)
    t.start()


async def _fetch_pool() -> list[dict]:
    env = Env.load()
    # Wider connection pool so 50+ concurrent board fetches don't exhaust
    # the default pool and cause cascading DNS / connect errors.
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=40)
    async with httpx.AsyncClient(timeout=30.0, limits=limits) as http:
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


# ---------- industry taxonomy -----------------------------------------------
# Domain → industry tags. Used to award a match bonus when the visitor's
# inferred target_industries overlap. Kept inline (vs in the registry) so
# the scoring concern stays out of the source adapters.
_INDUSTRY_TAGS: dict[str, list[str]] = {
    # Semiconductors + EDA
    "nvidia.com": ["semiconductor", "electronics", "ai-chips"],
    "amd.com": ["semiconductor", "electronics"],
    "intel.com": ["semiconductor", "electronics"],
    "broadcom.com": ["semiconductor", "electronics", "networking"],
    "qualcomm.com": ["semiconductor", "electronics", "mobile"],
    "tsmc.com": ["semiconductor", "foundry"],
    "asml.com": ["semiconductor", "lithography"],
    "appliedmaterials.com": ["semiconductor", "equipment"],
    "lamresearch.com": ["semiconductor", "equipment"],
    "kla.com": ["semiconductor", "equipment"],
    "micron.com": ["semiconductor", "memory"],
    "marvell.com": ["semiconductor", "networking"],
    "analog.com": ["semiconductor", "electronics"],
    "ti.com": ["semiconductor", "electronics"],
    "synopsys.com": ["semiconductor", "eda", "software"],
    "cadence.com": ["semiconductor", "eda", "software"],
    "arm.com": ["semiconductor", "electronics"],
    "lightmatter.co": ["semiconductor", "ai-chips", "photonics"],
    "graphcore.ai": ["semiconductor", "ai-chips"],
    "tenstorrent.com": ["semiconductor", "ai-chips"],
    "etched.com": ["semiconductor", "ai-chips"],
    "asteralabs.com": ["semiconductor", "connectivity"],
    # Security / IAM
    "okta.com": ["security", "iam", "identity"],
    "paloaltonetworks.com": ["security", "network-security"],
    "crowdstrike.com": ["security", "endpoint"],
    "zscaler.com": ["security", "cloud-security"],
    "sentinelone.com": ["security", "endpoint"],
    "fortinet.com": ["security", "network-security"],
    "cloudflare.com": ["security", "network", "cdn"],
    "rubrik.com": ["security", "data-protection"],
    "abnormal.ai": ["security", "email-security"],
    "snyk.io": ["security", "devsecops"],
    "wiz.io": ["security", "cloud-security"],
    "1password.com": ["security", "iam"],
    "semgrep.dev": ["security", "devsecops"],
    "chainguard.dev": ["security", "supply-chain"],
    # Fintech
    "stripe.com": ["fintech", "payments"],
    "plaid.com": ["fintech", "banking-api"],
    "chime.com": ["fintech", "banking"],
    "brex.com": ["fintech", "corporate-cards"],
    "ramp.com": ["fintech", "spend"],
    "mercury.com": ["fintech", "banking"],
    "carta.com": ["fintech", "equity"],
    "jpmorganchase.com": ["finance", "banking"],
    "goldmansachs.com": ["finance", "banking"],
    "bankofamerica.com": ["finance", "banking"],
    "wellsfargo.com": ["finance", "banking"],
    "morganstanley.com": ["finance", "banking"],
    # Healthcare
    "unitedhealthgroup.com": ["healthcare", "insurance"],
    "cvshealth.com": ["healthcare", "pharmacy"],
    "elevancehealth.com": ["healthcare", "insurance"],
    "humana.com": ["healthcare", "insurance"],
    # AI / ML
    "anthropic.com": ["ai", "foundation-model"],
    "openai.com": ["ai", "foundation-model"],
    "perplexity.ai": ["ai", "search"],
    "cohere.com": ["ai", "foundation-model"],
    "mistral.ai": ["ai", "foundation-model"],
    "huggingface.co": ["ai", "ml-platform"],
    "scale.com": ["ai", "data-labeling"],
    "character.ai": ["ai", "consumer"],
    "harvey.ai": ["ai", "legal"],
    "langchain.com": ["ai", "developer-tools"],
    "langfuse.com": ["ai", "observability"],
    "deepgram.com": ["ai", "speech"],
    "elevenlabs.io": ["ai", "speech"],
    "runwayml.com": ["ai", "media"],
    "replicate.com": ["ai", "ml-platform"],
    "baseten.co": ["ai", "ml-infra"],
    # Devtools / infra
    "vercel.com": ["devtools", "hosting"],
    "supabase.com": ["devtools", "database"],
    "cursor.com": ["devtools", "ide"],
    "linear.app": ["devtools", "pm"],
    "retool.com": ["devtools", "internal-tools"],
    "gitlab.com": ["devtools", "git"],
    "docker.com": ["devtools", "containers"],
    "postman.com": ["devtools", "api"],
    "circleci.com": ["devtools", "ci"],
    "launchdarkly.com": ["devtools", "feature-flags"],
    "honeycomb.io": ["observability", "devtools"],
    "grafana.com": ["observability", "devtools"],
    "datadoghq.com": ["observability", "monitoring"],
    "newrelic.com": ["observability", "monitoring"],
    "elastic.co": ["observability", "search"],
    "posthog.com": ["analytics", "devtools"],
    "mintlify.com": ["devtools", "docs"],
    "jfrog.com": ["devtools", "artifacts"],
    "mongodb.com": ["database"],
    "snowflake.com": ["data-warehouse"],
    "servicenow.com": ["enterprise-saas", "itsm"],
    # Platforms / consumer
    "notion.so": ["productivity", "saas"],
    "airtable.com": ["productivity", "saas"],
    "figma.com": ["design", "saas"],
    "loom.com": ["productivity", "video"],
    "dropbox.com": ["storage", "saas"],
    "reddit.com": ["consumer", "social"],
    "airbnb.com": ["consumer", "travel"],
    "pinterest.com": ["consumer", "social"],
    "coinbase.com": ["fintech", "crypto"],
    "instacart.com": ["consumer", "delivery"],
    "lyft.com": ["consumer", "rideshare"],
    "spotify.com": ["consumer", "music"],
    "duolingo.com": ["consumer", "education"],
    "twilio.com": ["comms", "api"],
    "atlassian.com": ["saas", "collaboration"],
    "palantir.com": ["enterprise-saas", "data"],
    "flexport.com": ["logistics"],
    # HR / payroll
    "rippling.com": ["hr", "payroll"],
    "deel.com": ["hr", "global-payroll"],
    "gusto.com": ["hr", "payroll"],
    # GTM / sales tools
    "clay.com": ["gtm", "sales-tools"],
    "unifygtm.com": ["gtm", "sales-tools"],
    "attio.com": ["gtm", "crm"],
    "hubspot.com": ["gtm", "crm"],
    "salesforce.com": ["gtm", "crm"],
    "ringcentral.com": ["comms"],
    "hightouch.com": ["gtm", "reverse-etl"],
}


def _industry_match(domain: str, target_industries: list[str]) -> float:
    """Return 1.0 for exact-tag match, 0.7 for partial, 0 otherwise. Used
    as a multiplicative lift in _score_pool so when a chip co visits, the
    other chip cos rise above generic high-volume leads like Anthropic."""
    tags = _INDUSTRY_TAGS.get(domain, [])
    if not tags or not target_industries:
        return 0.0
    targets = {t.lower().strip().replace(" ", "-") for t in target_industries}
    for tag in tags:
        if tag.lower() in targets:
            return 1.0
        for tt in targets:
            if tag.startswith(tt) or tt.startswith(tag):
                return 0.7
    return 0.0


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

    target_industries = inferred.get("target_industries") or []

    scored: list[tuple[EnrichedAccount, float]] = []
    for domain, sigs in buckets.items():
        name = next((s.company_name for s in sigs if s.company_name), None)
        acc = EnrichedAccount(company=Company(domain=domain, name=name), signals=sigs)
        scored_acc = score_account(acc, icp)
        # Industry-match lift: exact-tag matches get a +40 add (clamped at
        # 100), partial matches get +20. Flat-add rather than multiplier
        # so low-signal-count matched cos (e.g. a semi with only 3 SEC
        # filings) still rise above generic high-volume mismatches.
        match = _industry_match(domain, target_industries)
        bonus = match * 40.0
        final_score = min(100.0, scored_acc.icp_score + bonus)
        scored_acc = scored_acc.model_copy(update={"icp_score": final_score})
        scored.append((scored_acc, final_score))

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
_kick_off_pool_warmup()

# ── Masthead ────────────────────────────────────────────────────────────────
st.html(
    """
<div class="sf-narrow">
  <div class="sf-top-rule"></div>
  <div class="sf-mast-kicker">
    <span>SIGNALFORGE · ISSUE No. 01</span>
  </div>
  <h1 class="sf-mast-title">The <em>dossier</em><br/>you'd write<br/>before the cold email.</h1>
  <p class="sf-lede">
    Type a company. SignalForge reads its public context, infers an <em>ideal customer profile</em>,
    and files five lead accounts from a curated pool — each with its full signal ledger.
    <br/><br/>
    No cold email is generated here. That's a different tool.
  </p>
</div>
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

    # ── Build § 01 brief + § 02 dispatches and render side-by-side ──────
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

    # § 01 · The brief — left column, sticky as the user scrolls dispatches.
    brief_html = f"""
<section class="sf-sec sf-col-left">
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

    # § 02 · Dispatches — right column, natural-scroll list of leads.
    # Render priority: scarce-but-high-value kinds first, hiring last. This
    # prevents ATS volume from burying funding / exec / press / github /
    # filing signals in the top-N shown per lead.
    _KIND_PRIORITY = (
        "funding", "exec_change", "product_launch", "press",
        "earnings", "github_activity", "filing", "tech_stack",
        "hiring",
    )

    def _diversify(signals: list[dict], limit: int = 10) -> list[dict]:
        """Round-robin by kind in _KIND_PRIORITY order so the top-N shown
        reflects signal mix, not raw volume. Within each kind, strongest first."""
        from collections import defaultdict
        by_kind: dict[str, list[dict]] = defaultdict(list)
        for s in signals:
            by_kind[s["kind"]].append(s)
        for bucket in by_kind.values():
            bucket.sort(key=lambda x: x.get("strength", 0.0), reverse=True)
        out: list[dict] = []
        while len(out) < limit:
            progressed = False
            for k in _KIND_PRIORITY:
                bucket = by_kind.get(k)
                if bucket:
                    out.append(bucket.pop(0))
                    progressed = True
                    if len(out) >= limit:
                        break
            if not progressed:
                break
        return out

    lead_blocks: list[str] = []
    for rank, lead in enumerate(leads, 1):
        is_top = rank == 1
        num_html = (
            '<span class="carmine">— Dispatch 01 · top match —</span>'
            if is_top else f"— Dispatch {rank:02d} —"
        )
        dispatches: list[str] = []
        shown = _diversify(lead["signals"], limit=10)
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
        lead_blocks.append(
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

    empty_note = "" if leads else (
        '<div class="sf-colophon" style="border-top:none; margin-top:24px;">'
        "No leads matched this ICP in the current pool — try a different domain."
        "</div>"
    )

    dispatches_html = f"""
<section class="sf-sec sf-col-right">
  <div class="sf-sec-head">
    <div class="sf-sec-num">§ 02</div>
    <h2 class="sf-sec-title">Dispatches — top {len(leads)} of {result['pool_size']} pool signals</h2>
  </div>
  {''.join(lead_blocks)}
  {empty_note}
</section>
"""

    st.html(f'<div class="sf-split">{brief_html}{dispatches_html}</div>')

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

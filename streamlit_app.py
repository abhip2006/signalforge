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

st.set_page_config(page_title="SignalForge — live GTM lead demo", page_icon="🔍", layout="wide")


# ---------- candidate pool --------------------------------------------------

CANDIDATE_SOURCES = {
    "greenhouse": {
        "enabled": True,
        "boards": [
            "anthropic", "scaleai", "openai", "perplexity",
            "glean", "cohere", "mistralai",
        ],
        "hiring_keywords": [
            "sdr", "bdr", "gtm", "go-to-market", "revenue",
            "sales development", "growth", "developer relations",
            "head of", "vp ",
        ],
    },
    "ashby": {
        "enabled": True,
        "boards": ["notion", "ramp", "clay", "unify"],
        "hiring_keywords": [
            "sdr", "bdr", "gtm", "go-to-market", "revenue", "growth",
            "head of", "vp ", "director",
        ],
    },
    "github": {
        "enabled": True,
        "orgs": ["anthropics", "scaleai", "clay-labs", "unifygtm"],
        "lookback_days": 30,
    },
    "sec_edgar": {
        "enabled": True,
        "tickers": ["CRM", "HUBS", "RNG"],
        "lookback_days": 60,
    },
    "news_rss": {
        "enabled": True,
        "match_boards": [
            "anthropic", "notion", "ramp", "clay", "unify", "openai", "perplexity",
        ],
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
    if not env.anthropic_api_key:
        # Stub inference so the app still runs without a key in dev.
        return {
            "company_summary": f"{name_hint or domain} (no key set — generic ICP used)",
            "target_titles": ["VP Engineering", "Head of Growth", "CTO"],
            "target_industries": ["SaaS"],
            "signal_weights": {
                "hiring": 25, "funding": 20, "exec_change": 15, "product_launch": 10,
                "press": 10, "github_activity": 10, "earnings": 5,
            },
            "why": "stub",
        }

    client = AsyncAnthropic(api_key=env.anthropic_api_key)
    user = dedent(f"""\
        Company domain: {domain}
        Public context snippet (scraped):
        ---
        {ctx_text[:2500] or '(empty — site did not return readable text)'}
        ---
        Return JSON only.
    """)
    msg = await client.messages.create(
        model=env.claude_model_fast,
        max_tokens=700,
        system=[{"type": "text", "text": ICP_INFERENCE_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    return _safe_json(text)


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


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def _analyze_cached(visitor_domain: str) -> dict:
    # Resolve the cached pool in the sync layer so the async run below does
    # not try to open a nested event loop.
    pool = _get_pool()
    return asyncio.run(_analyze(visitor_domain, pool))


async def _analyze(visitor_domain: str, pool: list[dict]) -> dict:
    env = Env.load()
    ctx = await fetch_company_context(visitor_domain, env, max_chars=2500, timeout=10.0)
    ctx_text = ctx.text if ctx else ""
    inferred = await _infer_icp(visitor_domain, ctx_text, "", env)
    ranked = _score_pool(pool, inferred)
    # Drop the visitor's own company from the leads list.
    ranked = [r for r in ranked if r["domain"] != visitor_domain]
    return {
        "visitor_domain": visitor_domain,
        "ctx_len": len(ctx_text),
        "ctx_source": ctx.source if ctx else None,
        "inferred": inferred,
        "leads": ranked[:5],
        "pool_size": len(pool),
    }


# ---------- UI --------------------------------------------------------------

st.title("🔍 SignalForge — live GTM lead demo")
st.markdown(
    "Enter a company domain. SignalForge scrapes its public context, infers the ICP, "
    "then returns **3-5 lead accounts** from a curated pool — each with its full signal list. "
    "[See the code](https://github.com/abhip2006/signalforge)."
)

with st.form("demo"):
    raw_input = st.text_input("Your company domain or URL", placeholder="e.g. ramp.com", autocomplete="off")
    submitted = st.form_submit_button("Find leads →", type="primary")

if submitted:
    domain = _normalize(raw_input)
    if not domain:
        st.error("That doesn't look like a valid domain. Try `example.com`.")
        st.stop()

    now = time.time()
    last = st.session_state.get("last_run", 0)
    if now - last < 15:
        st.warning("Easy — wait a few seconds between requests.")
        st.stop()
    st.session_state["last_run"] = now

    with st.spinner(f"Analyzing {domain} → inferring ICP → scoring the pool…"):
        try:
            result = _analyze_cached(domain)
        except Exception as e:  # noqa: BLE001
            st.error(f"Pipeline error: {e.__class__.__name__}: {e}")
            st.stop()

    st.success(f"Inferred ICP for {domain}")

    inf = result["inferred"] or {}
    st.markdown(f"**What you do (as inferred):** {inf.get('company_summary', '—')}")
    c1, c2 = st.columns(2)
    with c1:
        st.caption("Target titles")
        for t in inf.get("target_titles", [])[:6]:
            st.markdown(f"- {t}")
    with c2:
        st.caption("Signal weights (what matters most for your sale)")
        weights = inf.get("signal_weights") or {}
        for k, v in sorted(weights.items(), key=lambda kv: kv[1], reverse=True):
            st.markdown(f"- **{k}**: {v}")
    if inf.get("why"):
        st.caption(f"_Why these weights:_ {inf['why']}")

    st.subheader(f"Top leads from pool of {result['pool_size']} signals across curated accounts")
    leads = result["leads"] or []
    if not leads:
        st.info("No leads matched. Try a different ICP-rich visitor domain or check back once the pool refreshes.")
    for rank, lead in enumerate(leads, 1):
        with st.container():
            st.markdown(
                f"### {rank}. {lead['name']}  "
                f"<span style='color:#888;font-size:0.85em'>{lead['domain']} · ICP {lead['icp_score']:.0f}</span>",
                unsafe_allow_html=True,
            )
            sig_count = len(lead["signals"])
            st.caption(f"{sig_count} signals captured")
            for s in lead["signals"]:
                if s["url"]:
                    st.markdown(
                        f"- **[{s['kind']}]** <span style='color:#888'>{s['source']} · strength {s['strength']:.2f}</span> "
                        f"— [{s['title']}]({s['url']})",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"- **[{s['kind']}]** <span style='color:#888'>{s['source']}</span> — {s['title']}",
                        unsafe_allow_html=True,
                    )

    st.caption(
        "Pool is refreshed hourly. Per-visitor analysis cached for 24h. "
        "Signals come from Greenhouse + Ashby + GitHub + SEC EDGAR + news RSS."
    )
else:
    st.caption("Output: 3-5 lead accounts ranked by the ICP inferred from your public site, each with its full signal list. No cold emails generated.")

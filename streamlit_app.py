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

st.set_page_config(page_title="SignalForge — live GTM lead demo", page_icon="🔍", layout="wide")


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
    resolved = _resolve_input(raw_input)
    if not resolved:
        st.error(
            "Couldn't resolve that — try a domain like `example.com` or a company name like `Stripe`."
        )
        st.stop()
    domain, display_hint = resolved
    if display_hint:
        st.caption(f"Matched → **{display_hint}** ({domain})")

    now = time.time()
    last = st.session_state.get("last_run", 0)
    if now - last < 15:
        st.warning("Easy — wait a few seconds between requests.")
        st.stop()
    st.session_state["last_run"] = now

    with st.status(f"Running SignalForge on {domain}", expanded=True) as status:
        try:
            result = _analyze_with_progress(domain, status)
            status.update(label=f"Ready: {domain}", state="complete", expanded=False)
        except Exception as e:  # noqa: BLE001
            status.update(label=f"Error on {domain}", state="error")
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
        "Pool is refreshed hourly · per-visitor analysis cached 24h. "
        "Signals: Greenhouse + Ashby + GitHub + SEC EDGAR + news RSS + Hacker News + Product Hunt. "
        "Name→domain resolved via Clearbit Autocomplete."
    )
else:
    st.caption("Output: 3-5 lead accounts ranked by the ICP inferred from your public site, each with its full signal list. No cold emails generated.")

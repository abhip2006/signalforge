# Roadmap

## Shipped

### Signal sources
- ✅ Greenhouse, Ashby, Lever (ATS boards)
- ✅ GitHub activity (org repos, new repos, contributor growth)
- ✅ SEC EDGAR (8-K exec changes, S-1 IPO prep)
- ✅ News RSS
- ✅ Exa neural-search press + funding
- ✅ Hacker News
- ✅ Product Hunt
- ✅ Company registry

### Enrichment
- ✅ Company-context extractor (Firecrawl + raw httpx fallback)

### Sinks
- ✅ SQLite sink
- ✅ CSV sink
- ✅ HTML report
- ✅ HubSpot upsert
- ✅ Slack webhook

### Harness / infra
- ✅ Deterministic + LLM-judge eval harness with cliff penalties
- ✅ Golden-set regression gated in CI (deterministic mode always, LLM mode on `main`)
- ✅ Streamlit visitor demo
- ✅ Hugging Face Spaces deployment (stable public URL)

## In progress
- [ ] Apollo contact enrichment (title-match waterfall)
- [ ] SQLite prompt-caching ledger (cost-per-account visibility)
- [ ] Follow-up / reply-thread draft kinds + golden-set expansion to 30+ cases
- [ ] Cross-model benchmark (Haiku / Sonnet / Opus) with cost + latency
- [ ] HF Space hardening: User-Agent + backoff on ATS scrapers, GitHub-token startup check, disk cache in company_context
- [ ] Per-session cost guardrail + live per-source progress on the Streamlit demo
- [ ] Sub-score breakdown on accounts (authenticity / authority / warmth) with YAML-tunable weights
- [ ] Falsification notes on every EvalScore

## Later
- [ ] Deliverability guardrails: warmup pool, domain health checks, bounce tracking
- [ ] Attribution loop: touches → replies → meetings logged against signal
- [ ] Simple web dashboard (FastAPI + HTMX) — no more "open the HTML report"
- [ ] Extensible signal-type registry so new sources don't need model changes

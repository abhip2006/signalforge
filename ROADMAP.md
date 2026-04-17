# Roadmap

Near-term (v0.2)
- [ ] SEC EDGAR 8-K / S-1 signal source (exec changes, IPO prep) — free, no key
- [ ] Exa neural-search press + funding signals (EXA_API_KEY, 1k free/mo)
- [ ] Firecrawl About-page extractor swap (FIRECRAWL_API_KEY; .edu tier = 20k)
- [ ] Apollo contact enrichment: title-match waterfall → selected contacts

Mid-term (v0.3)
- [ ] HubSpot sink (private-app token) — create/update company+contact+task
- [ ] Slack sink — top-signal alerts to a channel with a single "boost" button
- [ ] Follow-up draft kind + thread-aware variants
- [ ] Prompt-caching ledger in SQLite so cost-per-account is visible

Later
- [ ] Deliverability guardrails: warmup pool, domain health checks, bounce tracking
- [ ] Attribution loop: touches → replies → meetings logged against signal
- [ ] Golden-set expansion: 30+ cases covering follow-ups, replies, LinkedIn notes
- [ ] Eval-harness benchmarking across Claude models (Opus/Sonnet/Haiku)
- [ ] Simple web dashboard (FastAPI + HTMX) — no more "open the HTML report"

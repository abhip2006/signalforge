# SignalForge

**An open-source signal-driven outbound engine for GTM teams — with a reply-quality eval harness.**

Built to answer a question every outbound team dodges: *"Is this AI-written email actually good enough to send?"*

Most OSS GTM tooling stops at "wire up an LLM to enrichment data." SignalForge goes two steps further:

1. **Multi-signal fusion** — ingest buying signals from free public sources (ATS boards, SEC filings, GitHub, press), weight them, and route the output through a waterfall enrichment DAG.
2. **Reply-quality evals** — every generated email is scored against a calibrated rubric (signal-anchoring, length, single-CTA discipline, spam triggers, personalization depth) *before* it touches a sending tool. Regressions get gated, wins get promoted.

```
  signals  ──▶  enrichment  ──▶  ICP score  ──▶  research brief  ──▶  draft
     │                                               │                    │
  (free public sources)                         (Claude + Exa)        (eval'd)
     │                                                                    │
     └─────────────────▶  SQLite / CSV / HubSpot / Slack  ◀───────────────┘
```

A real top-scoring draft the pipeline generated against Ramp's public Greenhouse board (full run in [`demo/sample_run.md`](./demo/sample_run.md)):

> **Subject:** Attribution or headcount?
>
> Ramp is staffing a Senior Data Scientist on Growth alongside a Growth Manager
> for Website CRO. That pairing usually means attribution and experimentation
> velocity is the real ceiling, not people. Want me to send a short teardown on
> how three B2B fintechs unblocked that exact seam before scaling headcount?

→ eval harness overall **92.4** · signal_anchoring 92 · single_cta 100 · spam_triggers 100 · personalization 78

## Why this exists

The "AI SDR" category is saturated with black-box tools that ship without evals. The average cold email written by GPT/Claude today gets ignored because it pattern-matches as AI slop — recipients can smell it. SignalForge is the opposite bet: **treat cold-email generation like a model you evaluate, not a prompt you tweak.**

If you're hiring GTM engineers, the code here demonstrates:

- **Signal engineering** — normalized schema across job postings, filings, GitHub activity, press, earnings
- **Waterfall enrichment as code** — declarative YAML, cost-aware source ordering, cached by content hash
- **Composable architecture** — signal sources, enrichers, scorers, drafters, sinks are pluggable
- **Evaluation discipline** — the draft pipeline is treated like a ML system with a scoring rubric, not a vibe-check
- **Data originated, not bought** — ships with scrapers that build a bespoke dataset competitors can't replicate
- **Ships real outputs** — CSV + SQLite + HTML report, not a mock

## Data sources (all free or have free tiers)

| Source | Used for | Cost |
|--------|----------|------|
| Greenhouse / Ashby / Lever boards | Hiring-velocity signals | Free, no auth |
| SEC EDGAR | 8-K exec changes, S-1 IPO prep, 10-K | Free, no key |
| GitHub REST/GraphQL | Org activity, new repos, contributor growth | 5k req/hr |
| Exa | Neural news + funding search | 1k req/mo free |
| Firecrawl | Company page extraction | 500 free (20k on `.edu`) |
| FMP | Public-co earnings | Generous free tier |
| Apollo | Contact enrichment (optional) | Plan-based |
| Claude | Research briefs, drafts, scoring | Pay-as-you-go |

## Quick start

```bash
# 1. clone + install
git clone https://github.com/abhip2006/signalforge
cd signalforge
uv sync

# 2. configure
cp .env.example .env     # fill in keys — only ANTHROPIC_API_KEY is required
cp examples/icp.example.yaml icp.yaml

# 3. run
uv run signalforge run --config icp.yaml --limit 20

# 4. inspect
uv run signalforge report --latest    # opens HTML report
```

Cron-friendly subsequent runs:

```bash
# Only process signals that weren't in the last run
uv run signalforge run --config icp.yaml --delta --slack

# Iterate on prompts without re-hitting the network
uv run signalforge replay --run-id <prior> --config tweaked_icp.yaml
```

## The eval harness

Every generated draft is scored against a calibrated rubric:

| Dimension | Weight | What it measures |
|-----------|--------|------------------|
| Signal anchoring | 25% | Draft explicitly references the triggering signal |
| Length discipline | 15% | ≤ 75 words for cold open, ≤ 120 for follow-up |
| Single-CTA | 15% | Exactly one clear ask |
| Personalization depth | 20% | Signal + role + company detail, not generic |
| Spam triggers | 10% | No "just circling back", "quick question", etc. |
| Tone match | 10% | Matches configured voice (direct / warm / formal) |
| Grammar + read time | 5% | Baseline readability |

Drafts below threshold are regenerated (up to N attempts) or flagged for human review. Scores are logged to SQLite so you can track prompt/model/config lift over time.

## Repository layout

```
signalforge/
├── signalforge/
│   ├── signals/       # source adapters — one per signal type
│   ├── enrichment/    # waterfall enrichment DAG
│   ├── scoring/       # YAML-driven ICP scorer
│   ├── research/      # Claude + Exa research brief agent
│   ├── drafts/        # Claude email drafter + eval harness
│   ├── sinks/         # CSV / SQLite / HubSpot / Slack
│   ├── orchestrator/  # end-to-end pipeline runner
│   ├── config/        # default + example ICP YAMLs
│   └── cli.py
├── evals/             # golden-set drafts + regression suite
├── examples/          # runnable demo scripts
└── tests/
```

## Measured output

From the latest run against 7 Greenhouse/Ashby boards + 4 GitHub orgs + 3 SEC tickers + 4 RSS feeds:

- **83 real signals** ingested from 6 sources (ATS, GitHub, SEC EDGAR, news RSS)
- **10 accounts scored**, 3 above ICP threshold 55
- **9 email variants generated + scored** against the eval harness
- **Best variant: 92.4** — "Attribution or headcount?" for Ramp
- **Golden-set: 10/10 pass full harness** (incl. LLM judge) · **10/10 det-only**
- **50 unit tests passing**

## Model benchmark

The harness runs across every Claude model. 10 golden cases × each model:

| Model | Pass | Good-avg | Bad-avg | Separation | Elapsed |
|---|---|---|---|---|---|
| `claude-opus-4-7` | 9/10 | 84.6 | 47.4 | **37.2** | 25.6s |
| `claude-haiku-4-5` | 10/10 | 82.4 | 52.2 | 30.2 | 15.1s |

Takeaway: Haiku passes 10/10 at 2× Opus throughput with slightly tighter separation — a reasonable judge-model downgrade path for cost-sensitive runs. Raw JSON lands in `data/bench/<timestamp>.json`.

## Roadmap

Shipped (v0.2):
- 7 signal sources: Greenhouse / Ashby / Lever / GitHub / SEC EDGAR / News RSS / Exa
- YAML ICP scorer with diminishing-returns + firmographic penalty
- Claude research brief with public-page (Firecrawl or httpx) context injection
- Draft generator + **reply-quality eval harness** (deterministic + LLM judge + cliff penalties)
- Follow-up drafter that doesn't re-anchor ("bumping this" → flagged)
- Golden-set regression runner + cross-model benchmark (`make bench`)
- **Parallel per-account pipeline** — bounded concurrency, configurable
- **`signalforge replay`** — re-score any prior run's signals against new config/prompts without re-hitting the network
- Per-run **cost ledger** with per-step breakdown + cache-hit rate
- Sinks: SQLite, CSV, single-file HTML report, Slack webhook, HubSpot (company + note + task)
- CI via GitHub Actions: lint + tests + golden regression

Upcoming — see [ROADMAP.md](./ROADMAP.md).

---

Built by [Abhinav Penagalapati](https://www.linkedin.com/in/abhinav-penagalapati/). GTM engineer, ex-ChipAgents, ex-Opnova.

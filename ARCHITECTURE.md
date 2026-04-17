# Architecture

## Data flow

```
                    ┌────────────────────────────────────────────┐
                    │  icp.yaml  (target titles, weights, boards)│
                    └───────────────────┬────────────────────────┘
                                        │
                  ┌─────────────────────┼─────────────────────┐
                  │                     │                     │
          ┌───────▼───────┐     ┌───────▼───────┐     ┌───────▼───────┐
          │ signals/      │     │ signals/      │     │ signals/      │
          │  greenhouse   │     │  sec_edgar    │     │  news_rss     │
          │  ashby lever  │     │               │     │               │
          │  github       │     │               │     │               │
          └───────┬───────┘     └───────┬───────┘     └───────┬───────┘
                  └─────────────────────┼─────────────────────┘
                                        │  Signal[]
                                        ▼
                              ┌──────────────────┐
                              │ group_by_domain  │
                              └────────┬─────────┘
                                       │  Account[ ]
                                       ▼
                              ┌──────────────────┐
                              │ scoring/icp      │  YAML-driven,
                              │   (diminishing   │  diminishing returns,
                              │    returns)      │  firmographic penalty
                              └────────┬─────────┘
                                       │  sorted by score desc
                                       │  above min_icp_score → research
                                       ▼
                 ┌────────────────────┬─────────────────────┐
                 │                    │                     │
         ┌───────▼───────┐   ┌────────▼────────┐   ┌────────▼────────┐
         │ enrichment/   │   │ research/brief  │   │ drafts/         │
         │  company_ctx  ├──►│  (Claude opus)  ├──►│  drafter + evals│
         │  (firecrawl/  │   │   cached system │   │  (Claude opus + │
         │   httpx)      │   │   prompt)       │   │   haiku judge)  │
         └───────────────┘   └─────────────────┘   └────────┬────────┘
                                                            │  (draft, score)
                                                            ▼
                                 ┌──────────────────────────────────┐
                                 │  eval harness                    │
                                 │   • deterministic (length, CTA,  │
                                 │     spam, grammar)               │
                                 │   • LLM judge (signal_anchoring, │
                                 │     personalization, tone)       │
                                 │   • cliff penalties              │
                                 └───────────────┬──────────────────┘
                                                 │
                       ┌─────────────────────────┼────────────────────────────┐
                       │                         │                            │
                ┌──────▼─────┐           ┌───────▼──────┐           ┌─────────▼────────┐
                │ sqlite +   │           │ CSV +        │           │ slack webhook +  │
                │ cost ledger│           │ HTML report  │           │ HubSpot upsert   │
                └────────────┘           └──────────────┘           └──────────────────┘
```

## Module contracts

| Module | Input | Output | Side effects |
|---|---|---|---|
| `signals/*` | `SourceContext`, source-specific YAML config | `list[Signal]` | HTTP calls |
| `scoring/icp_scorer` | `EnrichedAccount`, `ICPConfig` | new `EnrichedAccount` w/ score | none |
| `enrichment/company_context` | `domain`, `Env` | `CompanyContext \| None` | HTTP; Firecrawl or raw httpx |
| `research/brief_agent` | `EnrichedAccount`, `ICPConfig`, `Env`, optional `CompanyContext` | `ResearchBrief` | Claude call; ledger |
| `drafts/drafter` | `EnrichedAccount`, `ResearchBrief`, `ICPConfig`, `Env` | `list[(Draft, EvalScore)]` | Claude call(s); ledger |
| `drafts/evals` | `Draft`, `ResearchBrief`, `ICPConfig`, `Env` | `EvalScore` | Claude judge call; ledger |
| `sinks/*` | run artifacts | files / webhooks / CRM rows | Disk I/O or HTTP |
| `orchestrator/pipeline` | `ICPConfig`, `Env`, flags | `(PipelineRun, list[...])` | Everything above |

## Key design decisions

### 1. Signals are flat `Signal` records with a `company_domain` key
All sources produce the same shape. The pipeline groups by domain, so adding a new source is "write an adapter" — no pipeline changes.

### 2. The eval harness mixes deterministic + LLM + cliff penalties
- **Deterministic** (length, CTA pattern, spam pattern, grammar) — fast, cached, catches 70% of real badness.
- **LLM judge** (Haiku) for signal_anchoring, personalization, tone — where rules alone lie.
- **Cliff penalties** — if a critical dim (spam, CTA, length by 30+) fails hard, the overall is capped so a weighted average can't "rationalize" a pass.

This is the central architectural bet and the project's differentiator vs every other AI-SDR repo.

### 3. Graceful-degrade by design
Every external dependency — Firecrawl, Exa, Apollo, HubSpot, Slack, even Anthropic — is a no-op if the key isn't set. The pipeline always runs to completion with whatever's available. A demo on a fresh machine with only `ANTHROPIC_API_KEY` set still produces a full HTML report.

### 4. Prompt caching + cost ledger are first-class
Every Claude call uses `cache_control: ephemeral` on its system prompt, and every call is recorded in `cost.py::LEDGER`. The CLI prints a per-step cost table at the end of every run. You can see the cost of an experiment before you ship it.

### 5. Golden-set regression is the contract, not the README
`evals/golden.jsonl` is the source of truth for "what's a good draft". README metrics can drift; the golden set gates PRs via `make eval-full` or `make bench`.

### 6. Immutable domain models
`Signal`, `Draft`, `EvalScore`, `ResearchBrief` are `frozen` pydantic models. Score updates return a new `EnrichedAccount` via `model_copy(update=...)` rather than mutating. This kept async correctness easy and made tests straightforward.

## File layout

```
signalforge/
├── models.py            Frozen domain objects
├── config.py            Env + ICPConfig loaders
├── cost.py              Token + cost ledger (process-wide)
├── signals/
│   ├── base.py          Protocol + shared helpers
│   ├── company_registry.py   board-token → domain + name
│   ├── greenhouse.py
│   ├── ashby.py
│   ├── lever.py
│   ├── github_activity.py
│   ├── sec_edgar.py
│   └── news_rss.py
├── enrichment/
│   └── company_context.py  Firecrawl + httpx fallback
├── scoring/
│   └── icp_scorer.py    diminishing-returns + firmographic
├── research/
│   └── brief_agent.py   Claude "why now" w/ JSON schema
├── drafts/
│   ├── drafter.py       N-variant generator
│   ├── evals.py         deterministic + LLM judge + cliff penalties
│   └── follow_up.py     angle-shift follow-up drafts
├── sinks/
│   ├── sqlite_sink.py   runs, signals, accounts, briefs, drafts, scores
│   ├── csv_sink.py
│   ├── html_report.py
│   ├── slack_sink.py
│   └── hubspot_sink.py
├── orchestrator/
│   └── pipeline.py      end-to-end runner
└── cli.py               typer app
evals/
├── golden.jsonl         pinned good/bad exemplars
├── run_regression.py    gate for prompt/weight changes
└── bench_models.py      cross-model benchmark
tests/                   50 unit tests
```

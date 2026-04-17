# Sample run

Real output from the run on 2026-04-17 against the example ICP (AI-native Series A/B companies,
VP Eng / Head of AI / CTO persona). Six signal sources, 83 real signals ingested.

## Top account: Ramp · ICP 68 · draft 92.4

Signals that triggered the brief:
- `hiring / greenhouse` — Senior Data Scientist, Growth (strength 0.85)
- `hiring / greenhouse` — Growth Manager, Website CRO (strength 0.70)
- `hiring / greenhouse` — Agentic Operator, Growth Marketing (strength 0.80)
- `hiring / greenhouse` — Senior SWE, GTM Platform Frontend (strength 0.50)
- `hiring / greenhouse` — Channel Partner Manager, VC & Growth Equity (×2) (strength 0.50)

Brief (`claude-opus-4-7`):
> **Ramp is doubling down on growth-side experimentation and attribution infra.**
> Staffing a Senior Data Scientist on Growth alongside a Growth Manager for Website CRO
> in the same week, plus an Agentic Operator role specifically on Growth Marketing.
> That pairing typically means the ceiling is attribution and experimentation velocity,
> not people.

Best draft (variant 0, `claude-opus-4-7`, eval score **92.4**):

**Subject:** Attribution or headcount?

> Ramp is staffing a Senior Data Scientist on Growth alongside a Growth Manager
> for Website CRO. That pairing usually means attribution and experimentation
> velocity is the real ceiling, not people. Want me to send a short teardown on
> how three B2B fintechs unblocked that exact seam before scaling headcount?

Eval breakdown:

| Dim | Score |
|---|---|
| signal_anchoring | 92 |
| length (57 words) | 100 |
| single_cta | 100 |
| personalization | 78 |
| spam_triggers | 100 |
| tone (direct) | 88 |
| grammar | 100 |
| **overall** | **92.4** |

## Cost ledger for a 2-account run

| step | calls | input | output | cache_read | USD |
|---|---|---|---|---|---|
| brief | 2 | 3,999 | 1,980 | 0 | $0.2085 |
| draft | 2 | 2,368 | 1,132 | 0 | $0.1204 |
| judge (haiku) | 6 | 3,828 | 580 | 0 | $0.0067 |
| **total** | 10 | 10,195 | 3,692 | — | **$0.3356** |

Per-account end-to-end cost: ~$0.17.

## Golden-set cross-model benchmark

10 golden cases evaluated by each Claude model as the LLM judge:

| Model | Pass | Good-avg | Bad-avg | Separation | Elapsed |
|---|---|---|---|---|---|
| claude-opus-4-7 | 9/10 | 84.6 | 47.4 | **37.2** | 25.6s |
| claude-haiku-4-5 | 10/10 | 82.4 | 52.2 | 30.2 | 15.1s |

Haiku 4.5 is the recommended judge model for throughput — passes all 10 cases,
at 60% of Opus latency and roughly 1/15th the cost.

# Loom walk-through script (3 minutes)

Record at 1440p, face-cam top-right, terminal + VS Code side-by-side.

## [0:00 — 0:20] Hook

> "Most open-source AI-SDR projects stop at 'wire up an LLM to a CSV.' This one goes two steps further — multi-source signal fusion, and a reply-quality eval harness that gates every draft before it reaches a sending tool."

*(Show the README.md top section.)*

## [0:20 — 0:55] The signal engine

> "I'm pulling from Greenhouse, Ashby, Lever, GitHub org activity, SEC EDGAR 8-Ks, and news RSS feeds — all free, no keys needed except for Claude. Here's one YAML file that declares the ICP and which boards to watch."

*(Open `examples/icp.example.yaml`. Scroll through the `sources:` block.)*

> "Each signal adapter is one small file — pluggable. Adding TheirStack or Apollo Intent is a 50-line adapter."

*(Open `signalforge/signals/greenhouse.py` — scroll past the `collect()` method.)*

## [0:55 — 1:40] The run

*(In terminal:)*
```bash
$ make run
```

> "83 real signals, 10 accounts scored, 3 above the ICP threshold. It generated three email variants per account, scored each one against the rubric, and surfaced the top variant in an HTML report."

*(Wait for it to finish. Open the generated report in the browser. Scroll through the account cards.)*

> "Every draft has a subject anchored to the triggering signal — 'Attribution or headcount?' for Ramp when they posted a Senior Data Scientist plus a CRO Growth Manager in the same week. Cliff penalties kick in if the draft drifts generic."

## [1:40 — 2:20] The eval harness

*(Open `signalforge/drafts/evals.py`.)*

> "The differentiator. Seven weighted dimensions. Three come from regex (length, CTA match, spam phrases) — those are deterministic and fast. The other four go to a Haiku judge with a fixed system prompt and a 200-token output cap. Cliff penalties cap the overall if a critical dimension fails hard — so a weighted average can't rationalize an obvious fail into a pass."

*(Scroll to `_apply_cliffs`.)*

> "This is treated like any other ML component: there's a golden set, a regression runner, and a model benchmark."

*(In terminal:)*
```bash
$ make eval
10/10 golden pass (det-only)

$ make bench
```

*(Show benchmark table — Opus 9/10 vs Haiku 10/10, separation scores, cost per run.)*

> "Haiku 4.5 passes all ten cases at half the latency of Opus. For a 10,000-account-per-week run, that's a real cost-quality tradeoff the harness makes visible."

## [2:20 — 2:50] Cost awareness

*(Back to the run output, scroll to cost ledger.)*

> "Every Claude call is recorded. Per-account cost: seventeen cents end-to-end. This is what lets you reason about 'add another regen attempt' before you ship it."

## [2:50 — 3:00] Wrap

> "Repo's at github.com/abhinavpenagalapati/signalforge. Built this because the AI-SDR category ships without evals, and that's why reply rates tanked. SignalForge is an argument for how GTM engineering should look when the drafter is treated like a model, not a prompt."

*(Show README + GitHub link.)*

---

## Production notes
- Hide `.env` tab before recording.
- `icp.yaml` should have you as sender.
- Pre-populate `data/runs/` with a known-good run so the live run has something to fall back on if the ATS APIs are slow.
- Keep terminal font size ≥ 16pt.

"""Cross-model benchmark for the cold-email eval harness.

Runs the full golden set against each selected Claude model (as the
LLM-judge) and prints a summary table of:

    model × overall score, signal_anchoring, personalization,
    total cost (USD), latency p50 / p95 (seconds)

Run:
    uv run python evals/run_benchmark.py
    uv run python evals/run_benchmark.py --models haiku,sonnet,opus
    uv run python evals/run_benchmark.py --models haiku-4-5,sonnet-4-6

Short aliases:
    haiku   → claude-haiku-4-5-20251001
    sonnet  → claude-sonnet-4-6
    opus    → claude-opus-4-7

Gating:
    Requires ANTHROPIC_API_KEY. If missing the script prints a message
    and exits 0 — safe to wire into CI without breaking the build.

Complements `evals/bench_models.py`, which focuses on pass-rate and
good/bad separation. This script focuses on cost + latency.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from rich.console import Console
from rich.table import Table

from evals.run_regression import _load_cases
from signalforge.config import Env, ICPConfig
from signalforge.cost import Ledger, UsageEvent
from signalforge.drafts.evals import score_draft
from signalforge.models import Draft, DraftKind, ResearchBrief

MODEL_ALIASES: dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",
    "haiku-4-5": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "sonnet-4-6": "claude-sonnet-4-6",
    "opus": "claude-opus-4-7",
    "opus-4-7": "claude-opus-4-7",
}

DEFAULT_MODELS = ["haiku", "sonnet", "opus"]

console = Console()


@dataclass(frozen=True)
class CasePerf:
    case_id: str
    elapsed_s: float
    overall: float
    signal_anchoring: float
    personalization: float


@dataclass
class ModelReport:
    model: str
    alias: str
    case_count: int = 0
    avg_overall: float | None = None
    avg_signal_anchoring: float | None = None
    avg_personalization: float | None = None
    total_cost_usd: float = 0.0
    latency_p50_s: float | None = None
    latency_p95_s: float | None = None
    errors: list[str] = field(default_factory=list)
    per_case: list[CasePerf] = field(default_factory=list)


def _resolve_model(token: str) -> tuple[str, str]:
    """Return (alias, full_model_id) for a user-supplied token."""
    token = token.strip()
    if not token:
        return "", ""
    full = MODEL_ALIASES.get(token, token)
    alias = next((a for a, m in MODEL_ALIASES.items() if m == full), token)
    return alias, full


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    sorted_v = sorted(values)
    # Nearest-rank method — good enough for bench-level reporting.
    k = max(0, min(len(sorted_v) - 1, int(round((p / 100.0) * (len(sorted_v) - 1)))))
    return round(sorted_v[k], 3)


async def _bench_one_model(alias: str, model: str, env_base: Env) -> ModelReport:
    from dataclasses import replace

    # Pin both main and fast model so every call inside score_draft
    # goes through the model under test.
    env = replace(env_base, claude_model=model, claude_model_fast=model)
    icp = ICPConfig(
        name="benchmark",
        target_titles=["VP Eng"],
        firmographics={},
        signal_weights={},
        min_icp_score=0.0,
        tone="direct",
        sender={},
        sources={},
        raw={},
    )
    cases = _load_cases()

    # Use a per-model ledger so costs don't bleed between models.
    local_ledger = Ledger()

    report = ModelReport(model=model, alias=alias, case_count=len(cases))

    for case in cases:
        brief = ResearchBrief(
            account_domain="benchmark.example",
            headline=case.brief_headline,
            why_now=case.signal,
            hooks=[case.signal],
            citations=[],
        )
        try:
            draft_kind = DraftKind(case.kind)
        except ValueError:
            draft_kind = DraftKind.OPENER
        draft = Draft(
            account_domain="benchmark.example",
            kind=draft_kind,
            subject=case.draft_subject,
            body=case.draft_body,
        )
        start = time.perf_counter()
        try:
            from signalforge.cost import LEDGER

            # Snapshot global ledger length so we can attribute this case's events.
            pre = len(LEDGER.events)
            score = await score_draft(draft, brief, icp, env)
            post = len(LEDGER.events)
            # Attribute new events to the local per-model ledger too.
            for ev in LEDGER.events[pre:post]:
                local_ledger.events.append(
                    UsageEvent(
                        step=ev.step,
                        model=ev.model,
                        input_tokens=ev.input_tokens,
                        output_tokens=ev.output_tokens,
                        cache_creation_input_tokens=ev.cache_creation_input_tokens,
                        cache_read_input_tokens=ev.cache_read_input_tokens,
                    )
                )
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"{case.id}: {e.__class__.__name__}: {e}")
            continue
        elapsed = round(time.perf_counter() - start, 3)

        report.per_case.append(
            CasePerf(
                case_id=case.id,
                elapsed_s=elapsed,
                overall=score.overall,
                signal_anchoring=float(score.dimensions.get("signal_anchoring", 0.0)),
                personalization=float(score.dimensions.get("personalization", 0.0)),
            )
        )

    if report.per_case:
        report.avg_overall = round(
            sum(c.overall for c in report.per_case) / len(report.per_case), 2
        )
        report.avg_signal_anchoring = round(
            sum(c.signal_anchoring for c in report.per_case) / len(report.per_case), 2
        )
        report.avg_personalization = round(
            sum(c.personalization for c in report.per_case) / len(report.per_case), 2
        )
        latencies = [c.elapsed_s for c in report.per_case]
        report.latency_p50_s = _percentile(latencies, 50)
        report.latency_p95_s = _percentile(latencies, 95)

    report.total_cost_usd = round(local_ledger.total_cost_usd, 4)
    return report


def _print_table(reports: list[ModelReport]) -> None:
    table = Table(title="Cross-model benchmark", show_lines=False)
    table.add_column("model")
    table.add_column("overall", justify="right")
    table.add_column("signal_anchoring", justify="right")
    table.add_column("personalization", justify="right")
    table.add_column("total cost (USD)", justify="right")
    table.add_column("latency p50 (s)", justify="right")
    table.add_column("latency p95 (s)", justify="right")
    for r in reports:
        table.add_row(
            f"{r.alias} ({r.model})",
            f"{r.avg_overall}" if r.avg_overall is not None else "—",
            f"{r.avg_signal_anchoring}" if r.avg_signal_anchoring is not None else "—",
            f"{r.avg_personalization}" if r.avg_personalization is not None else "—",
            f"${r.total_cost_usd:.4f}",
            f"{r.latency_p50_s}" if r.latency_p50_s is not None else "—",
            f"{r.latency_p95_s}" if r.latency_p95_s is not None else "—",
        )
    console.print(table)


async def _main_async(models_tokens: list[str]) -> int:
    env = Env.load()
    if not env.anthropic_api_key:
        console.print(
            "[yellow]ANTHROPIC_API_KEY not set — benchmark needs the LLM judge. "
            "Skipping (exit 0).[/]"
        )
        return 0

    resolved: list[tuple[str, str]] = []
    seen: set[str] = set()
    for token in models_tokens:
        alias, full = _resolve_model(token)
        if not full:
            continue
        if full in seen:
            continue
        seen.add(full)
        resolved.append((alias, full))

    if not resolved:
        console.print("[red]no valid models selected[/]")
        return 2

    cases = _load_cases()
    console.print(f"benchmarking {len(resolved)} models × {len(cases)} golden cases")
    reports: list[ModelReport] = []
    for alias, model in resolved:
        console.print(f"\n→ {alias} ({model})")
        rep = await _bench_one_model(alias, model, env)
        reports.append(rep)
        console.print(
            f"  avg overall {rep.avg_overall} · "
            f"avg signal_anchoring {rep.avg_signal_anchoring} · "
            f"avg personalization {rep.avg_personalization} · "
            f"cost ${rep.total_cost_usd:.4f} · "
            f"p50 {rep.latency_p50_s}s / p95 {rep.latency_p95_s}s"
        )
        if rep.errors:
            console.print(f"  [yellow]errors:[/] {rep.errors[:3]}")

    _print_table(reports)

    out_dir = env.data_dir / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"benchmark-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps([asdict(r) for r in reports], indent=2, default=str))
    console.print(f"\nsaved → [cyan]{out_path}[/]")
    return 0


def _parse_models_arg(raw: str) -> list[str]:
    return [t.strip() for t in raw.split(",") if t.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the cold-email eval harness across multiple Claude models."
    )
    parser.add_argument(
        "--models",
        type=str,
        default=",".join(DEFAULT_MODELS),
        help="Comma-separated model aliases or IDs (e.g. 'haiku,sonnet,opus').",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main_async(_parse_models_arg(args.models))))


if __name__ == "__main__":
    main()

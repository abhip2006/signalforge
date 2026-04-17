"""Cross-model benchmark for the cold-email eval harness.

For each registered model, runs the full golden set and reports:
- pass-rate
- avg overall score (good-label cases only)
- avg overall score (bad-label cases only)
- median per-dimension score
- wall-clock elapsed
- tokens consumed (if available)

Run:
    uv run python evals/bench_models.py
    uv run python evals/bench_models.py --models claude-opus-4-7,claude-haiku-4-5-20251001

Output goes to `data/bench/<timestamp>.json` and a printed summary table.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from rich.console import Console
from rich.table import Table

from evals.run_regression import _check_case, _load_cases
from signalforge.config import Env
from signalforge.drafts.evals import score_draft
from signalforge.models import Draft, DraftKind, ResearchBrief

DEFAULT_MODELS = [
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]

console = Console()


@dataclass
class ModelReport:
    model: str
    passed: int = 0
    failed: int = 0
    total: int = 0
    avg_overall_good: float | None = None
    avg_overall_bad: float | None = None
    median_dimensions: dict[str, float] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


async def _bench_one_model(model: str, env_base: Env) -> ModelReport:
    from dataclasses import replace
    # The Env is frozen; we create a variant with the judge model pinned.
    env = replace(env_base, claude_model=model, claude_model_fast=model)

    from signalforge.config import ICPConfig
    icp = ICPConfig(
        name="bench",
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
    report = ModelReport(model=model, total=len(cases))
    per_dim: dict[str, list[float]] = {}
    good_overalls: list[float] = []
    bad_overalls: list[float] = []

    start = time.perf_counter()
    for case in cases:
        brief = ResearchBrief(
            account_domain="bench.example",
            headline=case.brief_headline,
            why_now=case.signal,
            hooks=[case.signal],
            citations=[],
        )
        draft = Draft(
            account_domain="bench.example",
            kind=DraftKind.OPENER,
            subject=case.draft_subject,
            body=case.draft_body,
        )
        try:
            score = await score_draft(draft, brief, icp, env)
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"{case.id}: {e.__class__.__name__}")
            report.failed += 1
            continue

        scores_dict = {"overall": score.overall, **score.dimensions}
        failures = _check_case(case, scores_dict, deterministic_only=False)
        if failures:
            report.failed += 1
        else:
            report.passed += 1

        for k, v in score.dimensions.items():
            per_dim.setdefault(k, []).append(v)
        (good_overalls if case.label == "good" else bad_overalls).append(score.overall)

    report.elapsed_seconds = round(time.perf_counter() - start, 2)
    report.median_dimensions = {
        k: round(statistics.median(v), 1) for k, v in per_dim.items() if v
    }
    if good_overalls:
        report.avg_overall_good = round(sum(good_overalls) / len(good_overalls), 1)
    if bad_overalls:
        report.avg_overall_bad = round(sum(bad_overalls) / len(bad_overalls), 1)
    return report


async def _main_async(models: list[str]) -> int:
    env = Env.load()
    if not env.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY not set — bench needs the LLM judge.[/]")
        return 2

    console.print(f"benchmarking {len(models)} models × {len(_load_cases())} golden cases")
    reports: list[ModelReport] = []
    for m in models:
        console.print(f"\n→ {m}")
        rep = await _bench_one_model(m, env)
        reports.append(rep)
        console.print(
            f"  {rep.passed}/{rep.total} pass · "
            f"good avg {rep.avg_overall_good} · bad avg {rep.avg_overall_bad} · "
            f"{rep.elapsed_seconds}s"
        )
        if rep.errors:
            console.print(f"  [yellow]errors:[/] {rep.errors[:3]}")

    # Summary table
    table = Table(title="Golden-set benchmark", show_lines=False)
    table.add_column("model")
    table.add_column("pass", justify="right")
    table.add_column("good avg", justify="right")
    table.add_column("bad avg", justify="right")
    table.add_column("sep (good − bad)", justify="right")
    table.add_column("elapsed", justify="right")
    for r in reports:
        sep = (
            round(r.avg_overall_good - r.avg_overall_bad, 1)
            if (r.avg_overall_good is not None and r.avg_overall_bad is not None)
            else None
        )
        table.add_row(
            r.model,
            f"{r.passed}/{r.total}",
            f"{r.avg_overall_good}" if r.avg_overall_good is not None else "—",
            f"{r.avg_overall_bad}" if r.avg_overall_bad is not None else "—",
            f"{sep}" if sep is not None else "—",
            f"{r.elapsed_seconds}s",
        )
    console.print(table)

    # Persist
    out_dir = env.data_dir / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps([asdict(r) for r in reports], indent=2, default=str))
    console.print(f"\nsaved → [cyan]{out_path}[/]")

    # Return non-zero only if ALL models fail more than half the cases.
    if all(r.passed < r.total * 0.5 for r in reports):
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models",
        type=str,
        default=",".join(DEFAULT_MODELS),
        help="Comma-separated Claude model IDs.",
    )
    args = parser.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    sys.exit(asyncio.run(_main_async(models)))


if __name__ == "__main__":
    main()

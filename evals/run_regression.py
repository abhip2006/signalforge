"""Golden-set regression runner for the cold-email eval harness.

Run with:
    uv run python evals/run_regression.py
    uv run python evals/run_regression.py --deterministic-only     # no Claude calls
    uv run python evals/run_regression.py --print-failures

Exit code 0 = all passing, non-zero = regressions.

Purpose: when you tweak the drafter prompt, the judge prompt, or the eval
weights, this suite tells you whether good drafts still score high and
bad drafts still score low. It's the "make target" the harness gates on.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from signalforge.config import Env
from signalforge.drafts.evals import (
    _cta_score,
    _grammar_score,
    _length_score,
    _spam_score,
    score_draft,
)
from signalforge.models import Draft, DraftKind, ResearchBrief

GOLDEN_PATH = Path(__file__).parent / "golden.jsonl"


@dataclass(frozen=True)
class Case:
    id: str
    label: str                         # "good" | "bad"
    signal: str
    brief_headline: str
    draft_subject: str
    draft_body: str
    expected_overall_min: float | None
    expected_overall_max: float | None
    expected_dimensions: dict[str, float]


def _load_cases() -> list[Case]:
    cases: list[Case] = []
    for line in GOLDEN_PATH.read_text().splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        cases.append(
            Case(
                id=o["id"],
                label=o["label"],
                signal=o.get("signal", ""),
                brief_headline=o.get("brief_headline", ""),
                draft_subject=o.get("draft_subject", ""),
                draft_body=o["draft_body"],
                expected_overall_min=o.get("expected_overall_min"),
                expected_overall_max=o.get("expected_overall_max"),
                expected_dimensions=o.get("expected_dimensions", {}),
            )
        )
    return cases


def _deterministic_only(case: Case) -> dict[str, float]:
    """Score with only the deterministic dimensions — no LLM needed."""
    length, _ = _length_score(case.draft_body, "opener")
    cta, _ = _cta_score(case.draft_body)
    spam, _ = _spam_score(case.draft_body)
    grammar, _ = _grammar_score(case.draft_body)
    return {"length": length, "single_cta": cta, "spam_triggers": spam, "grammar": grammar}


async def _full_score(case: Case, env: Env) -> dict[str, float]:
    """Score using the full harness (deterministic + LLM judge)."""
    from signalforge.config import ICPConfig

    icp = ICPConfig(
        name="regression",
        target_titles=["VP Eng"],
        firmographics={},
        signal_weights={},
        min_icp_score=0.0,
        tone="direct",
        sender={},
        sources={},
        raw={},
    )
    brief = ResearchBrief(
        account_domain="eval.example",
        headline=case.brief_headline,
        why_now=case.signal,
        hooks=[case.signal],
        citations=[],
    )
    draft = Draft(
        account_domain="eval.example",
        kind=DraftKind.OPENER,
        subject=case.draft_subject,
        body=case.draft_body,
    )
    score = await score_draft(draft, brief, icp, env)
    return {"overall": score.overall, **score.dimensions}


def _check_case(case: Case, scores: dict[str, float], deterministic_only: bool) -> list[str]:
    failures: list[str] = []
    overall = scores.get("overall")
    if overall is not None:
        if case.expected_overall_min is not None and overall < case.expected_overall_min:
            failures.append(
                f"overall {overall:.1f} < expected_min {case.expected_overall_min}"
            )
        if case.expected_overall_max is not None and overall > case.expected_overall_max:
            failures.append(
                f"overall {overall:.1f} > expected_max {case.expected_overall_max}"
            )
    for k, expected in case.expected_dimensions.items():
        metric, bound = k.rsplit("_", 1)
        got = scores.get(metric)
        if got is None:
            # Dimension not computed in this mode (e.g. signal_anchoring in det-only)
            continue
        if bound == "min" and got < expected:
            failures.append(f"{metric} {got:.1f} < expected_min {expected}")
        elif bound == "max" and got > expected:
            failures.append(f"{metric} {got:.1f} > expected_max {expected}")
    return failures


async def _main_async(deterministic_only: bool, print_failures: bool) -> int:
    cases = _load_cases()
    env = None if deterministic_only else Env.load()
    if env is not None and not env.anthropic_api_key:
        print("ANTHROPIC_API_KEY not set — falling back to deterministic-only mode.")
        deterministic_only = True

    passes = 0
    fails: list[tuple[Case, list[str], dict[str, float]]] = []
    for case in cases:
        scores = (
            _deterministic_only(case)
            if deterministic_only
            else await _full_score(case, env)  # type: ignore[arg-type]
        )
        failures = _check_case(case, scores, deterministic_only)
        if failures:
            fails.append((case, failures, scores))
        else:
            passes += 1

    total = len(cases)
    print(f"\ngolden set: {passes}/{total} pass  ({'det-only' if deterministic_only else 'full'})")
    if fails:
        print(f"\nFAIL ({len(fails)}):")
        for case, failures, scores in fails:
            print(f"  [{case.label}] {case.id}")
            for f in failures:
                print(f"    - {f}")
            if print_failures:
                shown = {k: round(v, 1) for k, v in scores.items()}
                print(f"    scores: {shown}")
    return 0 if not fails else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deterministic-only", action="store_true",
                        help="Skip the LLM-judge dimensions (no API call).")
    parser.add_argument("--print-failures", action="store_true",
                        help="Print full score dict for failing cases.")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main_async(args.deterministic_only, args.print_failures)))


if __name__ == "__main__":
    main()

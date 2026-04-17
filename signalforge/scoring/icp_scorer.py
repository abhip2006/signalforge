"""YAML-driven ICP scoring.

Score is the weighted sum of (signal_strength * signal_weight) per kind,
capped at 100. Returns (new_account, breakdown, reasons).
"""
from __future__ import annotations

from collections import defaultdict

from signalforge.config import ICPConfig
from signalforge.models import EnrichedAccount


def score_account(
    account: EnrichedAccount, icp: ICPConfig
) -> EnrichedAccount:
    breakdown: dict[str, float] = defaultdict(float)
    reasons: list[str] = []

    # 1. Signal contribution — weighted sum with diminishing returns AND a
    #    per-kind cap so an ATS board flooding hiring signals can't bury
    #    a company with a smaller but high-signal-quality mix (e.g. a
    #    semi vendor's 3 SEC filings vs a mega-employer's 40 open roles).
    PER_KIND_CAP_MULTIPLE = 3.5  # cap = 3.5× the first signal's contribution
    per_kind_count: dict[str, int] = defaultdict(int)
    per_kind_first: dict[str, float] = {}
    for sig in account.signals:
        weight = float(icp.signal_weights.get(sig.kind.value, 0.0))
        if weight <= 0:
            continue
        per_kind_count[sig.kind.value] += 1
        # diminishing returns: 1st = 1.0, 2nd = 0.6, 3rd = 0.35, ...
        multiplier = 1.0 / (1 + 0.6 * (per_kind_count[sig.kind.value] - 1))
        contribution = sig.strength * weight * multiplier
        if per_kind_count[sig.kind.value] == 1:
            per_kind_first[sig.kind.value] = contribution
        # Hard cap per kind at N× the first-hit contribution. Keeps signal
        # diversity meaningful; prevents a single high-weight kind from
        # saturating the whole score.
        remaining = per_kind_first[sig.kind.value] * PER_KIND_CAP_MULTIPLE - breakdown[sig.kind.value]
        if remaining <= 0:
            continue
        contribution = min(contribution, remaining)
        breakdown[sig.kind.value] += contribution
        reasons.append(
            f"{sig.kind.value}:{sig.title[:60]} → +{contribution:.1f} "
            f"(w={weight}, s={sig.strength:.2f}, mult={multiplier:.2f})"
        )

    # 2. Firmographic check (soft) — if we have headcount and it's outside range, -15.
    fh = icp.firmographics.get("headcount_range")
    if account.company.headcount is not None and isinstance(fh, (list, tuple)) and len(fh) == 2:
        lo, hi = int(fh[0]), int(fh[1])
        if not (lo <= account.company.headcount <= hi):
            breakdown["firmographic_mismatch"] -= 15
            reasons.append(
                f"firmographic: headcount {account.company.headcount} outside [{lo},{hi}] → -15"
            )

    total = min(100.0, max(0.0, sum(breakdown.values())))

    return account.model_copy(
        update={
            "icp_score": round(total, 2),
            "score_breakdown": {k: round(v, 2) for k, v in breakdown.items()},
            "score_reasons": reasons,
        }
    )

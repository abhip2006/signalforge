"""Unit tests for the ICP scorer."""
from __future__ import annotations

import pytest

from signalforge.config import ICPConfig
from signalforge.models import Company, EnrichedAccount, Signal, SignalKind
from signalforge.scoring import score_account


@pytest.fixture
def icp() -> ICPConfig:
    return ICPConfig(
        name="t",
        target_titles=["VP Eng"],
        firmographics={"headcount_range": [30, 250]},
        signal_weights={
            "hiring": 30.0,
            "funding": 20.0,
            "exec_change": 15.0,
        },
        min_icp_score=55.0,
        tone="direct",
        sender={"name": "A"},
        sources={},
        raw={},
    )


def _sig(kind: SignalKind, strength: float = 0.8) -> Signal:
    return Signal(
        kind=kind, source="t", company_domain="x.com", title=f"{kind.value}", strength=strength
    )


@pytest.mark.unit
def test_single_strong_hire_contributes_most_of_weight(icp: ICPConfig) -> None:
    acc = EnrichedAccount(company=Company(domain="x.com"), signals=[_sig(SignalKind.HIRING, 1.0)])
    scored = score_account(acc, icp)
    # strength 1.0 × weight 30 × mult 1.0 = 30
    assert scored.icp_score == pytest.approx(30.0, abs=0.01)


@pytest.mark.unit
def test_diminishing_returns_per_kind(icp: ICPConfig) -> None:
    sigs = [_sig(SignalKind.HIRING, 1.0) for _ in range(3)]
    acc = EnrichedAccount(company=Company(domain="x.com"), signals=sigs)
    scored = score_account(acc, icp)
    # 30 (1st) + 30*0.625 (2nd) + 30*0.455 (3rd) ≈ 30 + 18.75 + 13.64 ≈ 62.39
    assert 55 < scored.icp_score < 75
    # sanity: less than the naive 3×30
    assert scored.icp_score < 90


@pytest.mark.unit
def test_multiple_kinds_stack(icp: ICPConfig) -> None:
    acc = EnrichedAccount(
        company=Company(domain="x.com"),
        signals=[_sig(SignalKind.HIRING, 1.0), _sig(SignalKind.FUNDING, 1.0)],
    )
    scored = score_account(acc, icp)
    # 30 + 20 = 50, no diminishing because different kinds
    assert scored.icp_score == pytest.approx(50.0, abs=0.1)


@pytest.mark.unit
def test_firmographic_mismatch_penalty(icp: ICPConfig) -> None:
    acc = EnrichedAccount(
        company=Company(domain="x.com", headcount=5000),
        signals=[_sig(SignalKind.HIRING, 1.0)],
    )
    scored = score_account(acc, icp)
    # 30 - 15 = 15
    assert scored.icp_score == pytest.approx(15.0, abs=0.1)
    assert any("firmographic" in r for r in scored.score_reasons)


@pytest.mark.unit
def test_score_clamped_to_100(icp: ICPConfig) -> None:
    sigs = [_sig(SignalKind.HIRING, 1.0) for _ in range(50)]
    acc = EnrichedAccount(company=Company(domain="x.com"), signals=sigs)
    scored = score_account(acc, icp)
    assert scored.icp_score <= 100.0


@pytest.mark.unit
def test_score_floored_at_zero(icp: ICPConfig) -> None:
    acc = EnrichedAccount(
        company=Company(domain="x.com", headcount=99999),
        signals=[],  # no positive signals, only firmographic penalty
    )
    scored = score_account(acc, icp)
    assert scored.icp_score == 0.0  # max(0, -15) -> 0


@pytest.mark.unit
def test_unweighted_signals_ignored(icp: ICPConfig) -> None:
    acc = EnrichedAccount(
        company=Company(domain="x.com"),
        signals=[_sig(SignalKind.GITHUB_ACTIVITY, 1.0)],  # not in icp.signal_weights
    )
    scored = score_account(acc, icp)
    assert scored.icp_score == 0.0

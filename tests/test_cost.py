"""Unit tests for the cost ledger."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from signalforge.cost import Ledger, UsageEvent, _pricing_for


@pytest.mark.unit
class TestPricingLookup:
    def test_known_model_returns_canonical_pricing(self) -> None:
        assert _pricing_for("claude-opus-4-7") == (15.0, 75.0, 18.75, 1.50)
        assert _pricing_for("claude-haiku-4-5-20251001") == (1.0, 5.0, 1.25, 0.10)

    def test_prefix_match_still_resolves(self) -> None:
        # Someone passes "claude-opus-4" (shortened) — should still find opus pricing.
        px = _pricing_for("claude-opus-4")
        assert px == _pricing_for("claude-opus-4-7")

    def test_unknown_model_falls_back_to_sonnet(self) -> None:
        assert _pricing_for("claude-pigeon-9") == _pricing_for("claude-sonnet-4-6")


@pytest.mark.unit
class TestUsageEventCost:
    def test_input_only_opus(self) -> None:
        ev = UsageEvent(step="brief", model="claude-opus-4-7", input_tokens=1_000_000)
        assert ev.cost_usd == pytest.approx(15.0)

    def test_output_tokens_priced_5x_input_opus(self) -> None:
        ev = UsageEvent(
            step="brief", model="claude-opus-4-7",
            input_tokens=1_000_000, output_tokens=1_000_000,
        )
        assert ev.cost_usd == pytest.approx(15.0 + 75.0)

    def test_cache_read_is_10pct_of_input(self) -> None:
        ev = UsageEvent(
            step="brief", model="claude-opus-4-7",
            cache_read_input_tokens=1_000_000,
        )
        assert ev.cost_usd == pytest.approx(1.50, rel=0.01)

    def test_cache_write_is_125pct_of_input(self) -> None:
        ev = UsageEvent(
            step="brief", model="claude-opus-4-7",
            cache_creation_input_tokens=1_000_000,
        )
        assert ev.cost_usd == pytest.approx(18.75, rel=0.01)


@pytest.mark.unit
class TestLedger:
    def test_record_from_usage_object(self) -> None:
        ledger = Ledger()
        usage = SimpleNamespace(
            input_tokens=100, output_tokens=50,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        ledger.record("brief", "claude-opus-4-7", usage)
        assert len(ledger.events) == 1
        assert ledger.events[0].input_tokens == 100
        assert ledger.events[0].output_tokens == 50

    def test_record_from_dict(self) -> None:
        ledger = Ledger()
        ledger.record("draft", "claude-opus-4-7", {"input_tokens": 200, "output_tokens": 100})
        assert ledger.total_input == 200
        assert ledger.total_output == 100

    def test_record_none_usage_does_not_explode(self) -> None:
        ledger = Ledger()
        ev = ledger.record("brief", "claude-opus-4-7", None)
        assert ev.input_tokens == 0
        # Note: when usage is None, we don't append to events
        assert ledger.total_input == 0

    def test_reset_clears_events(self) -> None:
        ledger = Ledger()
        ledger.record("brief", "claude-opus-4-7", {"input_tokens": 10})
        ledger.reset()
        assert ledger.events == []
        assert ledger.total_cost_usd == 0.0

    def test_by_step_aggregates(self) -> None:
        ledger = Ledger()
        ledger.record("brief", "claude-opus-4-7", {"input_tokens": 100, "output_tokens": 50})
        ledger.record("brief", "claude-opus-4-7", {"input_tokens": 200, "output_tokens": 75})
        ledger.record("judge", "claude-haiku-4-5-20251001", {"input_tokens": 50, "output_tokens": 20})
        agg = ledger.by_step()
        assert agg["brief"]["calls"] == 2
        assert agg["brief"]["input"] == 300
        assert agg["judge"]["calls"] == 1

    def test_cache_hit_rate_all_cold(self) -> None:
        ledger = Ledger()
        ledger.record("brief", "claude-opus-4-7", {"input_tokens": 1000})
        assert ledger.cache_hit_rate == 0.0

    def test_cache_hit_rate_all_cached(self) -> None:
        ledger = Ledger()
        ledger.record("brief", "claude-opus-4-7", {"cache_read_input_tokens": 1000})
        assert ledger.cache_hit_rate == 1.0

    def test_cache_hit_rate_mixed(self) -> None:
        ledger = Ledger()
        ledger.record(
            "brief", "claude-opus-4-7",
            {"input_tokens": 100, "cache_read_input_tokens": 900},
        )
        assert ledger.cache_hit_rate == pytest.approx(0.9, abs=0.01)

    def test_cache_hit_rate_no_tokens(self) -> None:
        ledger = Ledger()
        assert ledger.cache_hit_rate == 0.0

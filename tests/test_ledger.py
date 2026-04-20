"""Unit tests for the SQLite-backed Claude call ledger."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from signalforge import ledger


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate each test in its own sqlite file + clear the schema cache."""
    db = tmp_path / "ledger.sqlite3"
    monkeypatch.setenv("SIGNALFORGE_COST_DB", str(db))
    monkeypatch.delenv("SIGNALFORGE_DISABLE_LEDGER", raising=False)
    # Reset the per-path schema-ready cache so a fresh file gets migrated.
    ledger._SCHEMA_READY.clear()
    return db


@pytest.mark.unit
class TestPricing:
    def test_exact_match_wins(self) -> None:
        assert ledger.pricing_for("claude-opus-4-7") == (15.0, 75.0, 18.75, 1.50)

    def test_prefix_match_finds_opus(self) -> None:
        assert ledger.pricing_for("claude-opus-4") == ledger.pricing_for("claude-opus-4-7")

    def test_unknown_falls_back_to_sonnet(self) -> None:
        assert ledger.pricing_for("claude-dodo") == ledger.pricing_for("claude-sonnet-4-6")

    def test_empty_model_uses_fallback(self) -> None:
        # No model name — still return a safe default (Sonnet tier).
        assert ledger.pricing_for("") == ledger.pricing_for("claude-sonnet-4-6")


@pytest.mark.unit
class TestCostEstimate:
    def test_input_only_opus(self) -> None:
        assert ledger.cost_estimate(1_000_000, 0, 0, "claude-opus-4-7") == pytest.approx(15.0)

    def test_output_only_opus(self) -> None:
        assert ledger.cost_estimate(0, 1_000_000, 0, "claude-opus-4-7") == pytest.approx(75.0)

    def test_cached_tokens_are_cheap(self) -> None:
        # Cache reads are ~10% of normal input.
        cost = ledger.cost_estimate(0, 0, 1_000_000, "claude-opus-4-7")
        assert cost == pytest.approx(1.50, rel=0.01)

    def test_haiku_cheaper_than_opus(self) -> None:
        haiku = ledger.cost_estimate(1_000_000, 1_000_000, 0, "claude-haiku-4-5")
        opus = ledger.cost_estimate(1_000_000, 1_000_000, 0, "claude-opus-4-7")
        assert haiku < opus / 10  # Haiku is an order of magnitude cheaper.


@pytest.mark.unit
class TestRecordCall:
    def test_persists_row_to_sqlite(self, tmp_db: Path) -> None:
        rec = ledger.record_call(
            model="claude-haiku-4-5",
            input_tokens=100, output_tokens=50, cached_input_tokens=0,
            session_id="s1", stage="icp_inference",
        )
        assert rec is not None
        conn = sqlite3.connect(tmp_db)
        rows = conn.execute("SELECT stage, session_id, input_tokens FROM claude_calls").fetchall()
        conn.close()
        assert rows == [("icp_inference", "s1", 100)]

    def test_cost_is_computed_and_rounded(self, tmp_db: Path) -> None:
        rec = ledger.record_call(
            model="claude-opus-4-7",
            input_tokens=1_000_000, output_tokens=0, cached_input_tokens=0,
            session_id="s2", stage="brief",
        )
        assert rec is not None
        assert rec.cost_usd == pytest.approx(15.0, rel=0.001)

    def test_disabled_returns_none(self, tmp_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SIGNALFORGE_DISABLE_LEDGER", "1")
        assert ledger.record_call(
            model="claude-opus-4-7",
            input_tokens=1, output_tokens=1, cached_input_tokens=0,
            session_id="s3", stage="brief",
        ) is None

    def test_garbage_usage_values_default_to_zero(self, tmp_db: Path) -> None:
        # Never explode on a malformed usage payload from the SDK.
        rec = ledger.record_call(
            model="claude-opus-4-7",
            input_tokens=None, output_tokens="oops", cached_input_tokens=None,  # type: ignore[arg-type]
            session_id="s4", stage="brief",
        )
        assert rec is not None
        assert rec.input_tokens == 0
        assert rec.output_tokens == 0


@pytest.mark.unit
class TestRecordFromResponse:
    def test_reads_usage_from_response_object(self, tmp_db: Path) -> None:
        usage = SimpleNamespace(
            input_tokens=200, output_tokens=75,
            cache_read_input_tokens=50, cache_creation_input_tokens=10,
        )
        response = SimpleNamespace(id="msg_123", usage=usage)
        rec = ledger.record_from_response(
            response, model="claude-haiku-4-5", stage="icp_inference", session_id="s5",
        )
        assert rec is not None
        assert rec.input_tokens == 200
        assert rec.output_tokens == 75
        assert rec.cache_read_tokens == 50
        assert rec.cache_write_tokens == 10
        assert rec.request_id == "msg_123"

    def test_handles_missing_usage_gracefully(self, tmp_db: Path) -> None:
        response = SimpleNamespace(id="msg_none", usage=None)
        rec = ledger.record_from_response(
            response, model="claude-opus-4-7", stage="brief", session_id="s6",
        )
        assert rec is not None
        assert rec.input_tokens == 0


@pytest.mark.unit
class TestSessionTotals:
    def test_sums_across_multiple_calls(self, tmp_db: Path) -> None:
        ledger.record_call(
            model="claude-haiku-4-5",
            input_tokens=100, output_tokens=50, cached_input_tokens=0,
            session_id="visitor-A", stage="icp_inference",
        )
        ledger.record_call(
            model="claude-haiku-4-5",
            input_tokens=80, output_tokens=40, cached_input_tokens=0,
            session_id="visitor-A", stage="icp_inference",
        )
        # Different session — must not pollute visitor-A totals.
        ledger.record_call(
            model="claude-opus-4-7",
            input_tokens=10_000, output_tokens=5_000, cached_input_tokens=0,
            session_id="visitor-B", stage="brief",
        )

        a = ledger.session_totals("visitor-A")
        assert a["calls"] == 2
        assert a["input_tokens"] == 180
        assert a["output_tokens"] == 90
        assert a["cost_usd"] > 0

        b = ledger.session_totals("visitor-B")
        assert b["calls"] == 1
        assert b["cost_usd"] > a["cost_usd"]  # Opus call dwarfs Haiku calls.

    def test_unknown_session_returns_zeros(self, tmp_db: Path) -> None:
        totals = ledger.session_totals("never-seen")
        assert totals["calls"] == 0
        assert totals["cost_usd"] == 0.0

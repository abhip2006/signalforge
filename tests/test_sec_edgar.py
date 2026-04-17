"""Unit tests for SEC EDGAR filing classification."""
from __future__ import annotations

import pytest

from signalforge.models import SignalKind
from signalforge.signals.sec_edgar import _classify, _filing_url


@pytest.mark.unit
class TestClassify:
    def test_8k_exec_change(self) -> None:
        kind, strength, desc = _classify("8-K", "5.02")
        assert kind == SignalKind.EXEC_CHANGE
        assert strength >= 0.8
        assert "5.02" in desc

    def test_8k_acquisition(self) -> None:
        kind, strength, _ = _classify("8-K", "2.01")
        assert kind == SignalKind.FUNDING
        assert strength >= 0.6

    def test_8k_material_agreement(self) -> None:
        kind, _, _ = _classify("8-K", "1.01")
        assert kind == SignalKind.FILING

    def test_s1_ipo_prep(self) -> None:
        kind, strength, _ = _classify("S-1", "")
        assert kind == SignalKind.FUNDING
        assert strength >= 0.8

    def test_10q_weak(self) -> None:
        kind, strength, _ = _classify("10-Q", "")
        assert kind == SignalKind.EARNINGS
        assert strength <= 0.5

    def test_def14a_ignored(self) -> None:
        # Proxy statement — not a signal worth acting on for outbound.
        kind, _, _ = _classify("DEF 14A", "")
        assert kind is None


@pytest.mark.unit
class TestFilingUrl:
    def test_builds_archive_url(self) -> None:
        url = _filing_url("0000320193", "0001193125-24-001234", "form8k.htm")
        assert url is not None
        assert "sec.gov/Archives/edgar/data/320193" in url
        assert "form8k.htm" in url

    def test_fallback_on_missing_primary(self) -> None:
        url = _filing_url("0000320193", "0001193125-24-001234", "")
        assert url is not None
        assert "browse-edgar" in url

    def test_none_on_missing_accession(self) -> None:
        assert _filing_url("0000320193", "", "form8k.htm") is None

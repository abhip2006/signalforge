"""Unit tests for the news RSS signal classifier + target matching."""
from __future__ import annotations

import pytest

from signalforge.models import SignalKind
from signalforge.signals.news_rss import (
    _build_target_index,
    _classify,
    _match_target,
)


@pytest.mark.unit
class TestClassify:
    def test_funding_title(self) -> None:
        kind, s = _classify("ramp raises $300m series e", "")
        assert kind == SignalKind.FUNDING
        assert s >= 0.8

    def test_product_launch(self) -> None:
        kind, _ = _classify("notion launches new agents feature", "")
        assert kind == SignalKind.PRODUCT_LAUNCH

    def test_exec_change(self) -> None:
        kind, _ = _classify("anthropic names new cfo", "steps down")
        assert kind == SignalKind.EXEC_CHANGE

    def test_generic_press(self) -> None:
        kind, s = _classify("clay featured in forbes", "")
        assert kind == SignalKind.PRESS
        assert 0.0 < s < 1.0


@pytest.mark.unit
class TestTargetMatch:
    def test_match_by_display_name(self) -> None:
        idx = _build_target_index(["anthropic"])
        m = _match_target("today anthropic announced a new product line", idx)
        assert m is not None
        assert m["domain"] == "anthropic.com"

    def test_no_match_returns_none(self) -> None:
        idx = _build_target_index(["anthropic"])
        assert _match_target("openai released a new model", idx) is None

    def test_short_slug_match(self) -> None:
        # "ramp" is 4 chars — meets the threshold, should match
        idx = _build_target_index(["ramp"])
        m = _match_target("ramp closes series d round", idx)
        assert m is not None
        assert m["name"] == "Ramp"

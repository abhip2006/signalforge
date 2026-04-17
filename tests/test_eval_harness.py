"""Unit tests for the deterministic half of the eval harness.

The LLM-judge dimensions (signal_anchoring, personalization, tone) require
Claude and live in `tests/test_eval_regression.py` (opt-in via env flag).
"""
from __future__ import annotations

import pytest

from signalforge.drafts.evals import (
    CTA_PATTERNS,
    EVAL_DIMENSIONS,
    SPAM_PATTERNS,
    _cta_score,
    _grammar_score,
    _length_score,
    _spam_score,
    _word_count,
)


@pytest.mark.unit
class TestLengthScore:
    def test_short_opener_scores_high(self) -> None:
        body = "Saw your Head of Revenue req. That usually means the sales stack gets rebuilt in the first 90 days. Worth a 15-min chat next week?"
        score, flags = _length_score(body, "opener")
        assert score == 100.0
        assert not flags

    def test_over_75_words_scores_proportionally(self) -> None:
        body = "word " * 100
        score, flags = _length_score(body.strip(), "opener")
        assert 0 <= score < 100
        assert any(f.startswith("length:over_by_") for f in flags)

    def test_length_penalty_steepens_after_10_over(self) -> None:
        # Slight overage (+5 words) loses 25 points; big overage (+30) zeros out.
        s5, _ = _length_score("w " * 80, "opener")   # 80 words = +5 over
        s30, _ = _length_score("w " * 105, "opener") # 105 words = +30 over
        assert s5 > s30
        assert s30 == 0  # sharp drop-off: 50 + (30-10)*10 = 250 penalty → floor 0

    def test_too_short_flagged(self) -> None:
        body = "Hey — quick one?"
        score, flags = _length_score(body, "opener")
        assert "length:too_short" in flags


@pytest.mark.unit
class TestCTAScore:
    def test_single_cta_scores_full(self) -> None:
        body = "Saw the VP Eng req. Want me to send a short teardown on how three peers sequenced this? Reply yes or no."
        score, flags = _cta_score(body)
        assert score >= 90

    def test_no_cta_flagged(self) -> None:
        body = "Just thinking about the space. Anthropic's growth story is great. I build cool things too."
        score, flags = _cta_score(body)
        assert score <= 60
        assert "cta:missing" in flags

    def test_two_questions_penalized(self) -> None:
        body = "Saw your role. Curious how you sequence? Also mind if I send a teardown? Thoughts?"
        score, _ = _cta_score(body)
        assert score <= 85


@pytest.mark.unit
class TestSpamScore:
    @pytest.mark.parametrize(
        "bad",
        [
            "Just circling back on this.",
            "Quick question for you",
            "Hope this email finds you well",
            "I was impressed by your growth",
            "Touching base — any update?",
            "Synergy between our teams.",
        ],
    )
    def test_known_spam_phrases_flagged(self, bad: str) -> None:
        score, flags = _spam_score(bad)
        assert score < 100
        assert any(f.startswith("spam:") for f in flags)

    def test_clean_body_full_score(self) -> None:
        body = "Saw the Head of Revenue req at Anthropic. Worth 15 minutes next week?"
        score, flags = _spam_score(body)
        assert score == 100
        assert not flags


@pytest.mark.unit
class TestGrammarScore:
    def test_clean_body_full_score(self) -> None:
        body = "Saw your role. Happy to share what's worked for peers."
        score, flags = _grammar_score(body)
        assert score == 100
        assert not flags

    def test_double_space_penalized(self) -> None:
        body = "Saw your role.  Happy to share."
        score, flags = _grammar_score(body)
        assert score < 100
        assert "grammar:double_space" in flags


@pytest.mark.unit
class TestEvalWeights:
    def test_weights_sum_to_one(self) -> None:
        assert abs(sum(EVAL_DIMENSIONS.values()) - 1.0) < 1e-6

    def test_signal_anchoring_is_top_weighted(self) -> None:
        assert max(EVAL_DIMENSIONS, key=EVAL_DIMENSIONS.get) == "signal_anchoring"


@pytest.mark.unit
class TestPatternSanity:
    """Guard against pattern regressions — the heart of the deterministic harness."""

    def test_all_spam_patterns_compile(self) -> None:
        import re
        for p in SPAM_PATTERNS:
            re.compile(p)

    def test_all_cta_patterns_compile(self) -> None:
        import re
        for p in CTA_PATTERNS:
            re.compile(p)

    def test_word_count_ignores_punctuation(self) -> None:
        assert _word_count("One, two, three!") == 3

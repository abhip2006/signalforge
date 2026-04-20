"""Unit tests for follow-up / reply-thread draft kinds and falsification notes.

LLM-judge calls are mocked — these tests never hit the Anthropic API.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from signalforge.config import Env, ICPConfig
from signalforge.drafts.evals import score_draft
from signalforge.drafts.follow_up import (
    generate_follow_up,
    generate_follow_up_2,
    generate_reply_thread,
)
from signalforge.models import (
    Company,
    Draft,
    DraftKind,
    EnrichedAccount,
    EvalScore,
    ResearchBrief,
)


def _env(api_key: str | None = None) -> Env:
    # Build a minimal Env without hitting the real environment loader.
    import tempfile
    from pathlib import Path

    return Env(
        anthropic_api_key=api_key,
        exa_api_key=None,
        firecrawl_api_key=None,
        github_token=None,
        apollo_api_key=None,
        hunter_api_key=None,
        fmp_api_key=None,
        hubspot_token=None,
        slack_webhook_url=None,
        data_dir=Path(tempfile.mkdtemp()),
        claude_model="claude-opus-4-7",
        claude_model_fast="claude-haiku-4-5-20251001",
    )


def _icp() -> ICPConfig:
    return ICPConfig(
        name="t",
        target_titles=["VP Eng"],
        firmographics={},
        signal_weights={},
        min_icp_score=0.0,
        tone="direct",
        sender={"name": "A", "title": "B", "company": "C"},
        sources={},
        raw={},
    )


def _account() -> EnrichedAccount:
    return EnrichedAccount(
        company=Company(domain="acme.com", name="Acme"),
        signals=[],
    )


def _brief() -> ResearchBrief:
    return ResearchBrief(
        account_domain="acme.com",
        headline="Acme growth buildout",
        why_now="Acme is scaling",
        hooks=["hiring a Senior Data Scientist", "opened an EMEA office"],
        citations=[],
    )


def _opener() -> Draft:
    return Draft(
        account_domain="acme.com",
        kind=DraftKind.OPENER,
        subject="The growth hire",
        body="Saw the Senior Data Scientist req. Want me to drop a short teardown?",
    )


@pytest.mark.unit
class TestDraftKindEnum:
    def test_new_kinds_registered(self) -> None:
        # The three new kinds are part of the StrEnum and preserve string values.
        assert DraftKind.FOLLOW_UP_1.value == "follow_up_1"
        assert DraftKind.FOLLOW_UP_2.value == "follow_up_2"
        assert DraftKind.REPLY_THREAD.value == "reply_thread"

    def test_legacy_kinds_preserved(self) -> None:
        # Backward compatibility: the original DraftKind values still resolve.
        assert DraftKind("opener") is DraftKind.OPENER
        assert DraftKind("follow_up") is DraftKind.FOLLOW_UP
        assert DraftKind("linkedin_note") is DraftKind.LINKEDIN_NOTE


@pytest.mark.unit
class TestStubPairs:
    """When ANTHROPIC_API_KEY is absent, the generators return deterministic stubs."""

    @pytest.mark.asyncio
    async def test_follow_up_stub_labels_as_follow_up_1(self) -> None:
        draft, score = await generate_follow_up(
            _account(), _brief(), _opener(), _icp(), _env()
        )
        assert draft.kind is DraftKind.FOLLOW_UP_1
        assert isinstance(score, EvalScore)
        # Stub scores do not include falsification notes.
        assert score.falsification_notes == []

    @pytest.mark.asyncio
    async def test_follow_up_2_stub_labels_as_follow_up_2(self) -> None:
        fu1 = Draft(
            account_domain="acme.com",
            kind=DraftKind.FOLLOW_UP_1,
            body="Different angle on that.",
        )
        draft, _ = await generate_follow_up_2(
            _account(), _brief(), _opener(), fu1, _icp(), _env()
        )
        assert draft.kind is DraftKind.FOLLOW_UP_2
        # Breakup-style stub stays within the 90-word cap.
        assert len(draft.body.split()) < 90

    @pytest.mark.asyncio
    async def test_reply_thread_stub_labels_as_reply_thread(self) -> None:
        prospect_reply = "can you send pricing?"
        draft, score = await generate_reply_thread(
            _account(), _brief(), _opener(), prospect_reply, _icp(), _env()
        )
        assert draft.kind is DraftKind.REPLY_THREAD
        # The subject on a reply always starts with Re:
        assert (draft.subject or "").startswith("Re:")
        assert score.overall > 0


@pytest.mark.unit
class TestFalsificationNotes:
    @pytest.mark.asyncio
    async def test_deterministic_mode_has_no_falsification_notes(self) -> None:
        draft = Draft(
            account_domain="acme.com",
            kind=DraftKind.OPENER,
            subject="Test",
            body="Saw the hiring req at Acme. Want me to send a short teardown? Reply yes.",
        )
        score = await score_draft(draft, _brief(), _icp(), _env())
        # With no API key the judge is skipped and we get an empty falsification list.
        assert score.falsification_notes == []

    @pytest.mark.asyncio
    async def test_falsification_notes_propagate_from_judge(self) -> None:
        draft = Draft(
            account_domain="acme.com",
            kind=DraftKind.OPENER,
            subject="Test",
            body="Saw the hiring req at Acme. Want me to send a short teardown? Reply yes.",
        )
        judge_return = (
            {"signal_anchoring": 80.0, "personalization": 70.0, "tone": 75.0},
            "rationale",
            "claude-haiku-4-5-20251001",
            [
                "depends on the hiring req being filled within 30 days",
                "assumes the teardown is sent within 48 hours of reply",
            ],
        )
        with patch(
            "signalforge.drafts.evals._judge",
            new=AsyncMock(return_value=judge_return),
        ):
            score = await score_draft(draft, _brief(), _icp(), _env(api_key="sk-test"))
        assert len(score.falsification_notes) == 2
        assert "depends on" in score.falsification_notes[0]

    @pytest.mark.asyncio
    async def test_judge_caps_raw_notes_to_three(self) -> None:
        """When the model returns more than 3 notes, the judge caps the list."""
        from signalforge.drafts import evals as evals_mod

        draft = Draft(
            account_domain="acme.com",
            kind=DraftKind.OPENER,
            subject="Test",
            body="Saw the hiring req. Want me to send the teardown?",
        )

        class _FakeMsg:
            # Mimic anthropic's Message content blocks shape.
            def __init__(self, text: str) -> None:
                block = type("Block", (), {"type": "text", "text": text})()
                self.content = [block]
                self.usage = None

        raw_json = (
            '{"signal_anchoring": 80, "personalization": 70, "tone": 75, '
            '"rationale": "ok", "falsification_notes": '
            '["a","b","c","d","e"]}'
        )

        class _FakeMessages:
            async def create(self, **kw):  # noqa: ANN003
                return _FakeMsg(raw_json)

        class _FakeClient:
            def __init__(self, *_a, **_kw) -> None:
                self.messages = _FakeMessages()

        with patch.object(evals_mod, "AsyncAnthropic", _FakeClient):
            score = await score_draft(draft, _brief(), _icp(), _env(api_key="sk-test"))
        assert len(score.falsification_notes) == 3
        assert score.falsification_notes == ["a", "b", "c"]

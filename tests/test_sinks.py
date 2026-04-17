"""Unit tests for sinks: graceful no-op when keys missing, payload shape sanity."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from signalforge.config import Env
from signalforge.models import (
    Company,
    Draft,
    DraftKind,
    EnrichedAccount,
    EvalScore,
    PipelineRun,
    ResearchBrief,
    Signal,
    SignalKind,
)
from signalforge.sinks import SqliteSink, post_top_accounts, sync_to_hubspot, write_csv_report


def _sample_row(domain: str = "example.com") -> tuple[EnrichedAccount, ResearchBrief, Draft, EvalScore]:
    account = EnrichedAccount(
        company=Company(domain=domain, name="Example"),
        signals=[
            Signal(
                kind=SignalKind.HIRING, source="greenhouse",
                company_domain=domain, company_name="Example",
                title="Hiring: SDR", strength=0.8,
            )
        ],
        icp_score=75.0,
    )
    brief = ResearchBrief(
        account_domain=domain, headline="Example is hiring",
        why_now="Sig.", hooks=["hook1"], citations=["url1"],
    )
    draft = Draft(
        account_domain=domain, kind=DraftKind.OPENER,
        subject="Quick ask", body="Saw your SDR req. Worth 15 minutes?",
    )
    score = EvalScore(
        draft_id="d1", overall=85.0,
        dimensions={"signal_anchoring": 90, "single_cta": 100, "spam_triggers": 100},
        rationale="solid", judge_model="haiku",
    )
    return account, brief, draft, score


@pytest.fixture
def env_no_keys(tmp_path, monkeypatch) -> Env:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("HUBSPOT_TOKEN", raising=False)
    monkeypatch.setenv("SIGNALFORGE_DATA_DIR", str(tmp_path))
    return Env.load()


@pytest.mark.unit
class TestSlackSinkGraceful:
    async def test_noop_without_webhook(self, env_no_keys: Env) -> None:
        rows = [_sample_row()]
        result = await post_top_accounts(env_no_keys, rows, run_id="r1")
        assert result.sent == 0
        assert "SLACK_WEBHOOK_URL" in result.reason

    async def test_below_thresholds_skipped(self, env_no_keys: Env, monkeypatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake")
        env = Env.load()
        # Low scores — should not hit the webhook
        account, brief, draft, score = _sample_row()
        weak = score.model_copy(update={"overall": 40.0})
        result = await post_top_accounts(env, [(account, brief, draft, weak)], run_id="r1")
        # No network call made because the threshold filter produced an empty list.
        assert result.sent == 0
        assert "passed thresholds" in result.reason


@pytest.mark.unit
class TestHubSpotSinkGraceful:
    async def test_noop_without_token(self, env_no_keys: Env) -> None:
        rows = [_sample_row()]
        result = await sync_to_hubspot(env_no_keys, rows, run_id="r1")
        assert result.companies_upserted == 0
        assert any("HUBSPOT_TOKEN" in e for e in result.errors)


@pytest.mark.unit
class TestCsvSink:
    def test_writes_one_row_per_account(self, tmp_path: Path) -> None:
        rows = [_sample_row("a.com"), _sample_row("b.com")]
        out = write_csv_report(tmp_path / "out.csv", rows)
        content = out.read_text()
        assert "a.com" in content
        assert "b.com" in content
        # Header + 2 rows
        assert len(content.strip().splitlines()) == 3


@pytest.mark.unit
class TestSqliteSink:
    def test_round_trip_run_signals_draft(self, tmp_path: Path) -> None:
        db = SqliteSink(tmp_path / "t.db")
        account, brief, draft, score = _sample_row()
        run = PipelineRun(run_id="r1", config_path="t.yaml", config_hash="abc")
        db.record_run(run)
        db.record_signals("r1", account.signals)
        db.record_account("r1", account)
        db.record_brief("r1", brief)
        db.record_draft("r1", draft, score)

        # Verify
        conn = sqlite3.connect(tmp_path / "t.db")
        try:
            assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM briefs").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM eval_scores").fetchone()[0] == 1

            # Dimensions round-trip as JSON
            dims = conn.execute(
                "SELECT dimensions FROM eval_scores WHERE draft_id = ?", ("d1",)
            ).fetchone()[0]
            assert json.loads(dims)["signal_anchoring"] == 90
        finally:
            conn.close()

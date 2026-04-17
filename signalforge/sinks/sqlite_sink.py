"""Persist runs, signals, accounts, briefs, drafts, and eval scores to SQLite."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from signalforge.models import Draft, EnrichedAccount, EvalScore, PipelineRun, ResearchBrief, Signal

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    config_path TEXT,
    config_hash TEXT,
    accounts_processed INTEGER,
    signals_ingested INTEGER,
    drafts_generated INTEGER,
    avg_draft_score REAL
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    run_id TEXT,
    kind TEXT,
    source TEXT,
    company_domain TEXT,
    company_name TEXT,
    title TEXT,
    url TEXT,
    observed_at TEXT,
    strength REAL,
    payload TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS accounts (
    run_id TEXT,
    domain TEXT,
    name TEXT,
    icp_score REAL,
    score_breakdown TEXT,
    score_reasons TEXT,
    signal_count INTEGER,
    PRIMARY KEY (run_id, domain)
);

CREATE TABLE IF NOT EXISTS briefs (
    run_id TEXT,
    account_domain TEXT,
    headline TEXT,
    why_now TEXT,
    hooks TEXT,
    citations TEXT,
    model TEXT,
    PRIMARY KEY (run_id, account_domain)
);

CREATE TABLE IF NOT EXISTS drafts (
    draft_id TEXT PRIMARY KEY,
    run_id TEXT,
    account_domain TEXT,
    contact_email TEXT,
    kind TEXT,
    subject TEXT,
    body TEXT,
    variant INTEGER,
    tone TEXT,
    model TEXT,
    generated_at TEXT
);

CREATE TABLE IF NOT EXISTS eval_scores (
    draft_id TEXT PRIMARY KEY,
    run_id TEXT,
    overall REAL,
    dimensions TEXT,
    rationale TEXT,
    flagged TEXT,
    judge_model TEXT,
    scored_at TEXT,
    FOREIGN KEY (draft_id) REFERENCES drafts(draft_id)
);

CREATE INDEX IF NOT EXISTS idx_signals_run ON signals(run_id);
CREATE INDEX IF NOT EXISTS idx_accounts_run ON accounts(run_id);
CREATE INDEX IF NOT EXISTS idx_drafts_run ON drafts(run_id);
"""


class SqliteSink:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def record_run(self, run: PipelineRun) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    run.run_id,
                    run.started_at.isoformat(),
                    run.finished_at.isoformat() if run.finished_at else None,
                    run.config_path,
                    run.config_hash,
                    run.accounts_processed,
                    run.signals_ingested,
                    run.drafts_generated,
                    run.avg_draft_score,
                ),
            )

    def record_signals(self, run_id: str, signals: list[Signal]) -> None:
        with self._conn() as c:
            c.executemany(
                """INSERT OR IGNORE INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        s.signal_id,
                        run_id,
                        s.kind.value,
                        s.source,
                        s.company_domain,
                        s.company_name,
                        s.title,
                        s.url,
                        s.observed_at.isoformat(),
                        s.strength,
                        json.dumps(s.payload, default=str),
                    )
                    for s in signals
                ],
            )

    def record_account(self, run_id: str, account: EnrichedAccount) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?,?,?)""",
                (
                    run_id,
                    account.company.domain,
                    account.company.name,
                    account.icp_score,
                    json.dumps(account.score_breakdown),
                    json.dumps(account.score_reasons),
                    len(account.signals),
                ),
            )

    def record_brief(self, run_id: str, brief: ResearchBrief) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO briefs VALUES (?,?,?,?,?,?,?)""",
                (
                    run_id,
                    brief.account_domain,
                    brief.headline,
                    brief.why_now,
                    json.dumps(brief.hooks),
                    json.dumps(brief.citations),
                    brief.model,
                ),
            )

    def existing_signal_ids(self) -> set[str]:
        """Return every signal_id ever recorded. Used by delta-mode to skip dupes."""
        with self._conn() as c:
            cur = c.execute("SELECT signal_id FROM signals")
            return {row[0] for row in cur.fetchall()}

    def record_draft(self, run_id: str, draft: Draft, score: EvalScore) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO drafts
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    score.draft_id,
                    run_id,
                    draft.account_domain,
                    draft.contact_email,
                    draft.kind.value,
                    draft.subject,
                    draft.body,
                    draft.variant,
                    draft.tone,
                    draft.model,
                    draft.generated_at.isoformat(),
                ),
            )
            c.execute(
                """INSERT OR REPLACE INTO eval_scores
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    score.draft_id,
                    run_id,
                    score.overall,
                    json.dumps(score.dimensions),
                    score.rationale,
                    json.dumps(score.flagged),
                    score.judge_model,
                    score.scored_at.isoformat(),
                ),
            )

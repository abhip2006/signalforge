"""Core domain models. Immutable by default — mutations return new instances."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SignalKind(StrEnum):
    HIRING = "hiring"
    FUNDING = "funding"
    EXEC_CHANGE = "exec_change"
    TECH_STACK = "tech_stack"
    PRESS = "press"
    PRODUCT_LAUNCH = "product_launch"
    EARNINGS = "earnings"
    GITHUB_ACTIVITY = "github_activity"
    FILING = "filing"


class Signal(BaseModel):
    """A raw buying signal observed for a company."""
    model_config = ConfigDict(frozen=True)

    kind: SignalKind
    source: str
    company_domain: str
    company_name: str | None = None
    title: str
    url: str | None = None
    observed_at: datetime = Field(default_factory=_utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)
    strength: float = 0.5  # 0..1 — source-declared confidence / magnitude

    @property
    def signal_id(self) -> str:
        """Stable ID for deduplication."""
        import hashlib
        key = f"{self.kind}|{self.source}|{self.company_domain}|{self.title}|{self.url or ''}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]


class Contact(BaseModel):
    model_config = ConfigDict(frozen=True)

    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    linkedin_url: str | None = None
    source: str = "unknown"


class Company(BaseModel):
    model_config = ConfigDict(frozen=True)

    domain: str
    name: str | None = None
    description: str | None = None
    headcount: int | None = None
    industry: str | None = None
    hq_country: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    enrichment_sources: list[str] = Field(default_factory=list)


class EnrichedAccount(BaseModel):
    """A company + its signals + scored enrichment + selected contacts."""
    company: Company
    signals: list[Signal] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    icp_score: float = 0.0
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    score_reasons: list[str] = Field(default_factory=list)


class ResearchBrief(BaseModel):
    """Claude-generated 'why now' brief for an account."""
    model_config = ConfigDict(frozen=True)

    account_domain: str
    headline: str                 # one-line
    why_now: str                  # 2-3 sentences, signal-anchored
    hooks: list[str] = Field(default_factory=list)
    objections_to_expect: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utcnow)
    model: str = ""


class DraftKind(StrEnum):
    OPENER = "opener"
    FOLLOW_UP = "follow_up"
    FOLLOW_UP_1 = "follow_up_1"
    FOLLOW_UP_2 = "follow_up_2"
    REPLY_THREAD = "reply_thread"
    LINKEDIN_NOTE = "linkedin_note"


class Draft(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_domain: str
    contact_email: str | None = None
    kind: DraftKind
    subject: str | None = None
    body: str
    variant: int = 0
    tone: str = "direct"
    generated_at: datetime = Field(default_factory=_utcnow)
    model: str = ""


class EvalScore(BaseModel):
    """Calibrated rubric score for a generated draft."""
    model_config = ConfigDict(frozen=True)

    draft_id: str
    overall: float                # 0..100 weighted
    dimensions: dict[str, float]  # per-dimension 0..100
    rationale: str
    flagged: list[str] = Field(default_factory=list)  # e.g. ["spam_trigger:just_circling_back"]
    scored_at: datetime = Field(default_factory=_utcnow)
    judge_model: str = ""


class PipelineRun(BaseModel):
    """Record of a single pipeline run."""
    model_config = ConfigDict(frozen=True)

    run_id: str
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    config_path: str
    config_hash: str
    accounts_processed: int = 0
    signals_ingested: int = 0
    drafts_generated: int = 0
    avg_draft_score: float | None = None

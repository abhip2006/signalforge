"""Env + YAML config loading."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Env:
    anthropic_api_key: str | None
    exa_api_key: str | None
    firecrawl_api_key: str | None
    github_token: str | None
    apollo_api_key: str | None
    hunter_api_key: str | None
    fmp_api_key: str | None
    hubspot_token: str | None
    slack_webhook_url: str | None
    data_dir: Path
    claude_model: str
    claude_model_fast: str

    @classmethod
    def load(cls) -> Env:
        data_dir = Path(os.environ.get("SIGNALFORGE_DATA_DIR", "./data")).expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            exa_api_key=os.environ.get("EXA_API_KEY"),
            firecrawl_api_key=os.environ.get("FIRECRAWL_API_KEY"),
            github_token=os.environ.get("GITHUB_TOKEN"),
            apollo_api_key=os.environ.get("APOLLO_API_KEY"),
            hunter_api_key=os.environ.get("HUNTER_API_KEY"),
            fmp_api_key=os.environ.get("FMP_API_KEY"),
            hubspot_token=os.environ.get("HUBSPOT_TOKEN"),
            slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL"),
            data_dir=data_dir,
            claude_model=os.environ.get("SIGNALFORGE_CLAUDE_MODEL", "claude-opus-4-7"),
            claude_model_fast=os.environ.get(
                "SIGNALFORGE_CLAUDE_MODEL_FAST", "claude-haiku-4-5-20251001"
            ),
        )


@dataclass(frozen=True)
class ICPConfig:
    """User-declared ideal customer profile + scoring rubric."""
    name: str
    target_titles: list[str]
    firmographics: dict[str, Any]       # headcount, industries, geos, stages
    signal_weights: dict[str, float]    # per SignalKind value
    min_icp_score: float                # drafts only generated above this threshold
    tone: str                           # direct | warm | formal
    sender: dict[str, str]              # name, title, company, calendly
    sources: dict[str, Any]             # which signal sources to run + their params
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> ICPConfig:
        p = Path(path).expanduser().resolve()
        raw = yaml.safe_load(p.read_text())
        return cls(
            name=raw.get("name", "default"),
            target_titles=raw.get("target_titles", []),
            firmographics=raw.get("firmographics", {}),
            signal_weights=raw.get("signal_weights", {}),
            min_icp_score=float(raw.get("min_icp_score", 50.0)),
            tone=raw.get("tone", "direct"),
            sender=raw.get("sender", {}),
            sources=raw.get("sources", {}),
            raw=raw,
        )

    def hash(self) -> str:
        import json
        canonical = json.dumps(self.raw, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:12]

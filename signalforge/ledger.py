"""SQLite-backed Claude call ledger.

Every Anthropic API call is written to a `claude_calls` row so we have durable
visibility into cost, cache hit rate, and per-stage spend — especially for the
hosted demo on Hugging Face Spaces where every visitor hits the owner's API key.

This module is the single source of truth for Anthropic pricing constants —
``signalforge.cost`` imports from here so there is only one table to update.

Pricing reference: https://www.anthropic.com/pricing (last updated 2026-04-19).
Rates are USD per 1M tokens: (input, output, cache_write, cache_read).
Cache write ~= 1.25x input, cache read ~= 0.1x input (ephemeral 5-min cache).
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------- Pricing (USD per 1M tokens) -------------------------------------
# Tuple layout: (input, output, cache_write, cache_read)
# Keep Anthropic pricing in ONE place. Update the "last updated" date when
# rates change: https://www.anthropic.com/pricing
ANTHROPIC_PRICING: dict[str, tuple[float, float, float, float]] = {
    # Opus 4.x family
    "claude-opus-4-7":            (15.0, 75.0, 18.75, 1.50),
    "claude-opus-4-6":            (15.0, 75.0, 18.75, 1.50),
    "claude-opus-4-5":            (15.0, 75.0, 18.75, 1.50),
    # Sonnet 4.x family
    "claude-sonnet-4-7":          (3.0,  15.0, 3.75,  0.30),
    "claude-sonnet-4-6":          (3.0,  15.0, 3.75,  0.30),
    "claude-sonnet-4-5":          (3.0,  15.0, 3.75,  0.30),
    # Haiku 4.x family
    "claude-haiku-4-5":           (1.0,  5.0,  1.25,  0.10),
    "claude-haiku-4-5-20251001":  (1.0,  5.0,  1.25,  0.10),
}

# Fallback used when an exact/prefix match can't be found. Sonnet-tier keeps
# estimates from looking suspiciously cheap for unknown models.
_FALLBACK_MODEL = "claude-sonnet-4-6"

_ENV_DB_PATH = "SIGNALFORGE_COST_DB"
_ENV_DATA_DIR = "SIGNALFORGE_DATA_DIR"
_ENV_DISABLE = "SIGNALFORGE_DISABLE_LEDGER"


def pricing_for(model: str) -> tuple[float, float, float, float]:
    """Look up pricing for a model. Tolerates dated suffixes / short prefixes."""
    if not model:
        return ANTHROPIC_PRICING[_FALLBACK_MODEL]
    if model in ANTHROPIC_PRICING:
        return ANTHROPIC_PRICING[model]
    for key, px in ANTHROPIC_PRICING.items():
        if model.startswith(key) or key.startswith(model):
            return px
    return ANTHROPIC_PRICING[_FALLBACK_MODEL]


def cost_estimate(
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    model: str,
    *,
    cache_write_tokens: int = 0,
) -> float:
    """Return USD cost estimate for a single call.

    ``cached_tokens`` = tokens served from the ephemeral cache (cheap read).
    ``cache_write_tokens`` = tokens written to cache on first use (slightly
    more expensive than a plain input token, but reused on later hits).
    """
    inp_px, out_px, cw_px, cr_px = pricing_for(model)
    return (
        (int(input_tokens) * inp_px) / 1_000_000
        + (int(output_tokens) * out_px) / 1_000_000
        + (int(cached_tokens) * cr_px) / 1_000_000
        + (int(cache_write_tokens) * cw_px) / 1_000_000
    )


# ---------- SQLite-backed persistent ledger ---------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claude_calls (
    call_id TEXT PRIMARY KEY,
    request_id TEXT,
    session_id TEXT,
    stage TEXT,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claude_calls_session ON claude_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_claude_calls_stage ON claude_calls(stage);
CREATE INDEX IF NOT EXISTS idx_claude_calls_created ON claude_calls(created_at);
"""


@dataclass(frozen=True)
class CallRecord:
    call_id: str
    request_id: str | None
    session_id: str | None
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    created_at: float


def disabled() -> bool:
    """Ledger writes can be switched off for tests / offline replay."""
    return os.environ.get(_ENV_DISABLE, "").lower() in ("1", "true", "yes")


def default_db_path() -> Path:
    """Resolve the sqlite path. Honours SIGNALFORGE_COST_DB first, then the
    data dir (SIGNALFORGE_DATA_DIR, default ``./data``)."""
    override = os.environ.get(_ENV_DB_PATH)
    if override:
        return Path(override).expanduser().resolve()
    data_dir = Path(os.environ.get(_ENV_DATA_DIR, "./data")).expanduser().resolve()
    return data_dir / "cost_ledger.sqlite3"


# Guard concurrent writers (Streamlit can spawn multiple threads per session).
_WRITE_LOCK = threading.Lock()
_SCHEMA_READY: set[str] = set()


@contextmanager
def _connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        key = str(db_path)
        if key not in _SCHEMA_READY:
            conn.executescript(_SCHEMA)
            _SCHEMA_READY.add(key)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def record_call(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    request_id: str | None = None,
    session_id: str | None = None,
    stage: str = "unknown",
    *,
    cache_write_tokens: int = 0,
    db_path: Path | None = None,
) -> CallRecord | None:
    """Persist a single Anthropic call.

    Returns the CallRecord (or None when the ledger is disabled). Never
    raises on DB failure — a broken ledger must not break the pipeline.
    """
    if disabled():
        return None

    in_tok = _coerce_int(input_tokens)
    out_tok = _coerce_int(output_tokens)
    cr_tok = _coerce_int(cached_input_tokens)
    cw_tok = _coerce_int(cache_write_tokens)
    cost = cost_estimate(
        in_tok, out_tok, cr_tok, model,
        cache_write_tokens=cw_tok,
    )

    rec = CallRecord(
        call_id=str(uuid.uuid4()),
        request_id=request_id,
        session_id=session_id,
        stage=stage,
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=cr_tok,
        cache_write_tokens=cw_tok,
        cost_usd=round(cost, 6),
        created_at=time.time(),
    )

    path = db_path or default_db_path()
    try:
        with _WRITE_LOCK, _connect(path) as conn:
            conn.execute(
                """INSERT INTO claude_calls
                   (call_id, request_id, session_id, stage, model,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_write_tokens, cost_usd, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rec.call_id, rec.request_id, rec.session_id, rec.stage, rec.model,
                    rec.input_tokens, rec.output_tokens, rec.cache_read_tokens,
                    rec.cache_write_tokens, rec.cost_usd, rec.created_at,
                ),
            )
    except sqlite3.Error:
        # Ledger write must never take the pipeline down. Swallow and move on.
        return rec
    return rec


def record_from_response(
    response: Any,
    *,
    model: str,
    stage: str,
    session_id: str | None = None,
    db_path: Path | None = None,
) -> CallRecord | None:
    """Convenience wrapper — pulls usage off an Anthropic SDK response."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")

    def _get(attr: str) -> int:
        if usage is None:
            return 0
        val = getattr(usage, attr, None)
        if val is None and isinstance(usage, dict):
            val = usage.get(attr)
        return _coerce_int(val)

    request_id = getattr(response, "id", None)
    if request_id is None and isinstance(response, dict):
        request_id = response.get("id")

    return record_call(
        model=model,
        input_tokens=_get("input_tokens"),
        output_tokens=_get("output_tokens"),
        cached_input_tokens=_get("cache_read_input_tokens"),
        cache_write_tokens=_get("cache_creation_input_tokens"),
        request_id=request_id,
        session_id=session_id,
        stage=stage,
        db_path=db_path,
    )


# ---------- Read helpers (for the Streamlit guardrail) ----------------------

def session_totals(session_id: str, db_path: Path | None = None) -> dict[str, float]:
    """Return totals for a single session_id.

    Keys: ``calls``, ``input_tokens``, ``output_tokens``, ``cache_read_tokens``,
    ``cost_usd``.
    """
    empty = {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cost_usd": 0.0,
    }
    if disabled() or not session_id:
        return empty
    path = db_path or default_db_path()
    try:
        with _connect(path) as conn:
            row = conn.execute(
                """SELECT COUNT(*), COALESCE(SUM(input_tokens), 0),
                          COALESCE(SUM(output_tokens), 0),
                          COALESCE(SUM(cache_read_tokens), 0),
                          COALESCE(SUM(cost_usd), 0.0)
                   FROM claude_calls WHERE session_id = ?""",
                (session_id,),
            ).fetchone()
    except sqlite3.Error:
        return empty
    if not row:
        return empty
    return {
        "calls": int(row[0] or 0),
        "input_tokens": int(row[1] or 0),
        "output_tokens": int(row[2] or 0),
        "cache_read_tokens": int(row[3] or 0),
        "cost_usd": float(row[4] or 0.0),
    }


__all__ = [
    "ANTHROPIC_PRICING",
    "CallRecord",
    "cost_estimate",
    "default_db_path",
    "disabled",
    "pricing_for",
    "record_call",
    "record_from_response",
    "session_totals",
]

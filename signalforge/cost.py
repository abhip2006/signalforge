"""Token + cost ledger (in-process aggregator).

Every Claude call records a UsageEvent. The process-wide Ledger aggregates
so we can surface cost-per-run, cache hit rate, and per-step cost in the
final CLI summary. Without this, the cost of "add another regen attempt"
is invisible — and that's how LLM pipelines quietly cost $1,000/day.

Pricing constants live in ``signalforge.ledger`` — this module imports them
so there is exactly ONE table of Anthropic rates in the codebase.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from signalforge.ledger import ANTHROPIC_PRICING, pricing_for

# Re-export so existing imports (`from signalforge.cost import _pricing_for`)
# in tests and callers keep working unchanged.
_DEFAULT_PRICING = ANTHROPIC_PRICING
_pricing_for = pricing_for


@dataclass(frozen=True)
class UsageEvent:
    step: str                         # "brief" | "draft" | "judge" | "follow_up" | "bench"
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        inp_px, out_px, cw_px, cr_px = _pricing_for(self.model)
        return (
            self.input_tokens * inp_px / 1_000_000
            + self.output_tokens * out_px / 1_000_000
            + self.cache_creation_input_tokens * cw_px / 1_000_000
            + self.cache_read_input_tokens * cr_px / 1_000_000
        )


@dataclass
class Ledger:
    events: list[UsageEvent] = field(default_factory=list)

    def record(self, step: str, model: str, usage: Any) -> UsageEvent:
        """Record from an Anthropic Usage object (or dict-like)."""
        if usage is None:
            return UsageEvent(step=step, model=model)

        def _get(attr: str, default: int = 0) -> int:
            v = getattr(usage, attr, None)
            if v is None and isinstance(usage, dict):
                v = usage.get(attr)
            return int(v or default)

        ev = UsageEvent(
            step=step,
            model=model,
            input_tokens=_get("input_tokens"),
            output_tokens=_get("output_tokens"),
            cache_creation_input_tokens=_get("cache_creation_input_tokens"),
            cache_read_input_tokens=_get("cache_read_input_tokens"),
        )
        self.events.append(ev)
        return ev

    def reset(self) -> None:
        self.events = []

    @property
    def total_cost_usd(self) -> float:
        return sum(e.cost_usd for e in self.events)

    @property
    def total_input(self) -> int:
        return sum(e.input_tokens for e in self.events)

    @property
    def total_output(self) -> int:
        return sum(e.output_tokens for e in self.events)

    @property
    def total_cache_read(self) -> int:
        return sum(e.cache_read_input_tokens for e in self.events)

    @property
    def total_cache_write(self) -> int:
        return sum(e.cache_creation_input_tokens for e in self.events)

    @property
    def cache_hit_rate(self) -> float:
        """cache_read / (cache_read + input + cache_write)."""
        denom = self.total_cache_read + self.total_input + self.total_cache_write
        return (self.total_cache_read / denom) if denom else 0.0

    def by_step(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for e in self.events:
            row = out.setdefault(
                e.step,
                {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cost_usd": 0.0},
            )
            row["calls"] += 1
            row["input"] += e.input_tokens
            row["output"] += e.output_tokens
            row["cache_read"] += e.cache_read_input_tokens
            row["cost_usd"] += e.cost_usd
        return out


# Process-wide ledger. The orchestrator calls `reset()` at the start of each run.
LEDGER = Ledger()


def disabled() -> bool:
    return os.environ.get("SIGNALFORGE_DISABLE_LEDGER", "").lower() in ("1", "true", "yes")

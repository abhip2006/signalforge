"""Signal source protocol + shared helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from signalforge.config import Env
from signalforge.models import Signal


@dataclass(frozen=True)
class SourceContext:
    env: Env
    http: httpx.AsyncClient


class SignalSource(Protocol):
    """Every signal source implements this protocol."""

    name: str

    async def collect(self, ctx: SourceContext, source_config: dict[str, Any]) -> list[Signal]: ...


async def http_get_json(
    ctx: SourceContext, url: str, *, headers: dict[str, str] | None = None, timeout: float = 30.0
) -> Any:
    """Thin wrapper; raises for non-2xx so tenacity/retries can see it."""
    response = await ctx.http.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()

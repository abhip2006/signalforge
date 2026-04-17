"""Lightweight board-token → real domain + company name map.

Greenhouse/Ashby/Lever's public APIs do not reliably expose a company's canonical
domain. Rather than spraying a third-party "autocomplete" API per row, we keep
a small curated registry here. Add entries as the ICP config evolves — or
override with `icp.yaml`:

    sources:
      greenhouse:
        boards:
          - {token: anthropic, domain: anthropic.com, name: Anthropic}

This module accepts either a plain string (legacy) or a dict form.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BoardEntry:
    token: str
    domain: str
    name: str


# Curated registry: feel free to extend.
KNOWN: dict[str, tuple[str, str]] = {
    # token → (domain, display_name)
    "anthropic": ("anthropic.com", "Anthropic"),
    "openai": ("openai.com", "OpenAI"),
    "scaleai": ("scale.com", "Scale AI"),
    "perplexity": ("perplexity.ai", "Perplexity"),
    "perplexityai": ("perplexity.ai", "Perplexity"),
    "glean": ("glean.com", "Glean"),
    "cohere": ("cohere.com", "Cohere"),
    "mistralai": ("mistral.ai", "Mistral AI"),
    "notion": ("notion.so", "Notion"),
    "ramp": ("ramp.com", "Ramp"),
    "clay": ("clay.com", "Clay"),
    "unify": ("unifygtm.com", "Unify"),
    "unifygtm": ("unifygtm.com", "Unify"),
    "default": ("default.com", "Default"),
    "common-room": ("commonroom.io", "Common Room"),
    "commonroom": ("commonroom.io", "Common Room"),
    "pocus": ("pocus.com", "Pocus"),
    "apollo": ("apollo.io", "Apollo"),
    "hubspot": ("hubspot.com", "HubSpot"),
    "retool": ("retool.com", "Retool"),
    "attio": ("attio.com", "Attio"),
    "rippling": ("rippling.com", "Rippling"),
    "11x": ("11x.ai", "11x"),
    "artisan": ("artisan.co", "Artisan"),
    "instrumentl": ("instrumentl.com", "Instrumentl"),
    "regie": ("regie.ai", "Regie.ai"),
    "smartlead": ("smartlead.ai", "Smartlead"),
    "instantly": ("instantly.ai", "Instantly"),
    "koala": ("getkoala.com", "Koala"),
    "vector": ("vector.co", "Vector"),
    "rb2b": ("rb2b.com", "RB2B"),
    "warmly": ("warmly.ai", "Warmly"),
    "anthropics": ("anthropic.com", "Anthropic"),  # github org
    "clay-labs": ("clay.com", "Clay"),
    "persana": ("persana.ai", "Persana"),
}


def resolve_board(token_or_entry: Any, fallback_source: str = "unknown") -> BoardEntry:
    """Accept `str` token or `{token, domain, name}` dict. Always returns a BoardEntry."""
    if isinstance(token_or_entry, dict):
        token = str(token_or_entry.get("token") or token_or_entry.get("slug") or "")
        domain = str(token_or_entry.get("domain") or "")
        name = str(token_or_entry.get("name") or token_or_entry.get("display_name") or "")
    else:
        token = str(token_or_entry)
        domain = ""
        name = ""

    if not domain or not name:
        known = KNOWN.get(token.lower())
        if known:
            d, n = known
            domain = domain or d
            name = name or n

    if not domain:
        domain = f"{token}.unknown"
    if not name:
        name = token.replace("-", " ").replace("_", " ").title()
    return BoardEntry(token=token, domain=domain, name=name)


def resolve_list(items: list[Any]) -> list[BoardEntry]:
    return [resolve_board(item) for item in items]

"""Unit tests for board-token → domain resolution."""
from __future__ import annotations

import pytest

from signalforge.signals.company_registry import KNOWN, resolve_board, resolve_list


@pytest.mark.unit
def test_known_token_resolves_to_registry_entry() -> None:
    entry = resolve_board("anthropic")
    assert entry.token == "anthropic"
    assert entry.domain == "anthropic.com"
    assert entry.name == "Anthropic"


@pytest.mark.unit
def test_unknown_token_falls_back_to_pseudo_domain() -> None:
    entry = resolve_board("some-random-slug-xyz")
    assert entry.domain.endswith(".unknown")
    assert entry.name == "Some Random Slug Xyz"


@pytest.mark.unit
def test_dict_form_override_beats_registry() -> None:
    entry = resolve_board({"token": "anthropic", "domain": "override.io", "name": "Override"})
    assert entry.domain == "override.io"
    assert entry.name == "Override"


@pytest.mark.unit
def test_dict_form_partial_override_fills_from_registry() -> None:
    entry = resolve_board({"token": "anthropic", "name": "Anthropic PBC"})
    assert entry.domain == "anthropic.com"
    assert entry.name == "Anthropic PBC"


@pytest.mark.unit
def test_resolve_list_preserves_order() -> None:
    entries = resolve_list(["anthropic", "notion", "unknown-x"])
    assert [e.token for e in entries] == ["anthropic", "notion", "unknown-x"]


@pytest.mark.unit
def test_registry_has_no_empty_entries() -> None:
    for token, (domain, name) in KNOWN.items():
        assert domain, token
        assert name, token
        assert "." in domain, token

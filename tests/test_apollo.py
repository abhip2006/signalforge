"""Unit tests for Apollo contact enrichment.

No network. We exercise the pure helpers (parser, title matchers, cache)
directly, and drive the async fetch path through a stub `httpx.MockTransport`.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from signalforge.config import Env
from signalforge.enrichment import apollo
from signalforge.enrichment.apollo import (
    _parse_contacts,
    _title_matches_exact,
    _title_matches_fuzzy,
    fetch_contacts,
)
from signalforge.models import Contact


def _make_env(tmp_path: Path, apollo_api_key: str | None = "test-key") -> Env:
    return Env(
        anthropic_api_key=None,
        exa_api_key=None,
        firecrawl_api_key=None,
        github_token=None,
        apollo_api_key=apollo_api_key,
        hunter_api_key=None,
        fmp_api_key=None,
        hubspot_token=None,
        slack_webhook_url=None,
        data_dir=tmp_path,
        claude_model="m",
        claude_model_fast="m",
    )


# --- pure helpers --------------------------------------------------------


@pytest.mark.unit
class TestTitleMatchers:
    def test_exact_is_case_insensitive(self) -> None:
        assert _title_matches_exact("VP Engineering", ["vp engineering"])
        assert not _title_matches_exact("Director of Engineering", ["vp engineering"])

    def test_fuzzy_contains_either_direction(self) -> None:
        assert _title_matches_fuzzy("Senior VP Engineering", ["VP Engineering"])
        assert _title_matches_fuzzy("VP Eng", ["VP Engineering"])

    def test_fuzzy_unrelated_rejected(self) -> None:
        assert not _title_matches_fuzzy("Product Manager", ["VP Engineering"])


@pytest.mark.unit
class TestParseContacts:
    def test_parses_people_array(self) -> None:
        contacts = _parse_contacts({
            "people": [
                {"name": "Ada Lovelace", "title": "VP Engineering",
                 "email": "ada@example.com",
                 "linkedin_url": "https://linkedin.com/in/ada"},
            ]
        })
        assert len(contacts) == 1
        assert contacts[0].full_name == "Ada Lovelace"
        assert contacts[0].title == "VP Engineering"
        assert contacts[0].email == "ada@example.com"
        assert contacts[0].source == "apollo"

    def test_dedupes_by_name_and_title(self) -> None:
        contacts = _parse_contacts({
            "people": [
                {"name": "Ada Lovelace", "title": "VP Engineering"},
                {"name": "Ada Lovelace", "title": "VP Engineering"},
            ]
        })
        assert len(contacts) == 1

    def test_strips_unlocked_placeholder_email(self) -> None:
        contacts = _parse_contacts({
            "people": [
                {"first_name": "Ada", "last_name": "Lovelace",
                 "title": "VP Engineering",
                 "email": "email_not_unlocked@example.com"},
            ]
        })
        assert contacts[0].email is None
        assert contacts[0].full_name == "Ada Lovelace"

    def test_skips_entries_without_title(self) -> None:
        contacts = _parse_contacts({"people": [{"name": "Nobody"}]})
        assert contacts == []


# --- fetch_contacts — graceful no-op -------------------------------------


@pytest.mark.unit
class TestFetchContactsNoOp:
    async def test_no_key_returns_empty(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path, apollo_api_key=None)
        out = await fetch_contacts("example.com", ["VP Engineering"], env)
        assert out == []

    async def test_empty_titles_returns_empty(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path)
        out = await fetch_contacts("example.com", [], env)
        assert out == []


# --- fetch_contacts — HTTP path via MockTransport ------------------------


def _mock_response(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


@pytest.mark.unit
class TestFetchContactsHttp:
    async def test_happy_path_returns_contacts_and_caches(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path)

        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return _mock_response({
                "people": [
                    {"name": "Ada Lovelace", "title": "VP Engineering",
                     "email": "ada@example.com"},
                    # "Senior VP Engineering" contains "VP Engineering" → fuzzy hit.
                    {"name": "Grace Hopper", "title": "Senior VP Engineering"},
                ]
            })

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            out = await fetch_contacts(
                "example.com", ["VP Engineering"], env, http_client=client
            )
        assert len(out) == 2  # one exact + one fuzzy
        assert out[0].full_name == "Ada Lovelace"
        assert out[0].source == "apollo"

        # Cache written to data/apollo_cache/example.com.json
        cache_file = tmp_path / "apollo_cache" / "example.com.json"
        assert cache_file.exists()
        cached = json.loads(cache_file.read_text())
        assert cached[0]["full_name"] == "Ada Lovelace"

        # Second call reads the cache — no HTTP hit.
        async with httpx.AsyncClient(transport=transport) as client:
            out2 = await fetch_contacts(
                "example.com", ["VP Engineering"], env, http_client=client
            )
        assert call_count["n"] == 1  # unchanged
        assert [c.full_name for c in out2] == [c.full_name for c in out]

    async def test_caps_at_three_contacts(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            return _mock_response({
                "people": [
                    {"name": f"Person {i}", "title": "VP Engineering"}
                    for i in range(10)
                ]
            })

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            out = await fetch_contacts(
                "example.com", ["VP Engineering"], env, http_client=client
            )
        assert len(out) == 3

    async def test_http_error_returns_empty(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            # 401 — not retryable, returns empty.
            return httpx.Response(401, json={"error": "unauthorized"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            out = await fetch_contacts(
                "example.com", ["VP Engineering"], env, http_client=client
            )
        assert out == []

    async def test_exact_match_preferred_over_fuzzy(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            return _mock_response({
                "people": [
                    # Return fuzzy-only first, then exact — exact should still
                    # appear first in the output.
                    {"name": "Senior VP of Eng", "title": "Senior VP Engineering"},
                    {"name": "Vera Precise", "title": "VP Engineering"},
                ]
            })

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            out = await fetch_contacts(
                "example.com", ["VP Engineering"], env, http_client=client
            )
        assert out[0].full_name == "Vera Precise"


# --- cache roundtrip -----------------------------------------------------


@pytest.mark.unit
class TestCache:
    async def test_stale_cache_ignored(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path)
        cache_dir = tmp_path / "apollo_cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "example.com.json"
        cache_file.write_text(json.dumps([
            {"full_name": "Old Stale", "title": "VP Engineering",
             "linkedin_url": None, "email": None, "source": "apollo"},
        ]))
        # Backdate mtime past TTL
        import os
        past = cache_file.stat().st_mtime - (apollo.CACHE_TTL_SECONDS + 60)
        os.utime(cache_file, (past, past))

        # New HTTP response
        def handler(request: httpx.Request) -> httpx.Response:
            return _mock_response({
                "people": [{"name": "Fresh Face", "title": "VP Engineering"}],
            })

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            out = await fetch_contacts(
                "example.com", ["VP Engineering"], env, http_client=client
            )
        assert out[0].full_name == "Fresh Face"

    def test_contact_roundtrip_through_cache(self, tmp_path: Path) -> None:
        cache_root = tmp_path / "apollo_cache"
        contact = Contact(
            full_name="Ada Lovelace",
            title="VP Engineering",
            linkedin_url="https://linkedin.com/in/ada",
            email="ada@example.com",
        )
        apollo._write_cache(cache_root, "example.com", [contact])
        loaded = apollo._load_cache(cache_root, "example.com")
        assert loaded == [contact]

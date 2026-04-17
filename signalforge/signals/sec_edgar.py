"""SEC EDGAR signal source.

Watches recent filings for target public companies and emits signals for:
  - 8-K Item 5.02  → executive appointments / departures
  - 8-K Item 2.01  → acquisitions
  - S-1 / S-1/A    → IPO prep
  - 10-Q / 10-K    → earnings (weaker signal, stamped as EARNINGS)

Free, no key required. SEC enforces two rules:
  1. Descriptive User-Agent header (we set one).
  2. 10 req/sec rate cap (we stay well under — handful of tickers per run).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from signalforge.models import Signal, SignalKind
from signalforge.signals.base import SourceContext, http_get_json

USER_AGENT = "signalforge/0.1 (abhip@berkeley.edu)"
_TICKER_CACHE: dict[str, dict[str, Any]] | None = None


class SecEdgarSource:
    name = "sec_edgar"

    async def collect(
        self, ctx: SourceContext, source_config: dict[str, Any]
    ) -> list[Signal]:
        if not source_config.get("enabled", False):
            return []
        tickers: list[str] = [t.upper() for t in source_config.get("tickers", []) or []]
        if not tickers:
            return []
        lookback_days = int(source_config.get("lookback_days", 60))

        # 1. Resolve ticker → CIK (once, cached).
        ticker_map = await _load_ticker_map(ctx)

        # 2. Fetch recent submissions per CIK (in parallel, but bounded).
        sem = asyncio.Semaphore(3)  # be polite; SEC cap is 10/sec
        tasks = [
            _fetch_company_signals(ctx, ticker, ticker_map, lookback_days, sem)
            for ticker in tickers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out: list[Signal] = []
        for r in results:
            if isinstance(r, Exception):
                continue
            out.extend(r or [])
        return out


async def _load_ticker_map(ctx: SourceContext) -> dict[str, dict[str, Any]]:
    """Map TICKER (uppercase) → {cik_str, ticker, title}."""
    global _TICKER_CACHE
    if _TICKER_CACHE is not None:
        return _TICKER_CACHE
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        data = await http_get_json(ctx, url, headers=headers)
    except Exception:  # noqa: BLE001
        _TICKER_CACHE = {}
        return _TICKER_CACHE
    # Input is `{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}`
    mapping: dict[str, dict[str, Any]] = {}
    if isinstance(data, dict):
        for _, row in data.items():
            if isinstance(row, dict) and "ticker" in row:
                mapping[str(row["ticker"]).upper()] = {
                    "cik": str(row["cik_str"]).zfill(10),
                    "ticker": row["ticker"],
                    "title": row.get("title"),
                }
    _TICKER_CACHE = mapping
    return mapping


async def _fetch_company_signals(
    ctx: SourceContext,
    ticker: str,
    ticker_map: dict[str, dict[str, Any]],
    lookback_days: int,
    sem: asyncio.Semaphore,
) -> list[Signal]:
    if ticker not in ticker_map:
        return []
    entry = ticker_map[ticker]
    cik = entry["cik"]
    title = entry.get("title") or ticker

    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    async with sem:
        try:
            data = await http_get_json(ctx, url, headers=headers, timeout=20.0)
        except Exception:  # noqa: BLE001
            return []

    recent = (data.get("filings") or {}).get("recent") or {}
    forms: list[str] = recent.get("form") or []
    dates: list[str] = recent.get("filingDate") or []
    accessions: list[str] = recent.get("accessionNumber") or []
    primaries: list[str] = recent.get("primaryDocument") or []
    items: list[str] = recent.get("items") or []

    cutoff = datetime.now(UTC).date().toordinal() - lookback_days
    company_domain = f"{ticker.lower()}.sec"
    signals: list[Signal] = []
    for i, form in enumerate(forms):
        try:
            d = datetime.strptime(dates[i], "%Y-%m-%d").date()
        except (IndexError, ValueError):
            continue
        if d.toordinal() < cutoff:
            continue
        acc_no = accessions[i] if i < len(accessions) else ""
        primary = primaries[i] if i < len(primaries) else ""
        item_codes = items[i] if i < len(items) else ""
        filing_url = _filing_url(cik, acc_no, primary)

        kind, strength, descriptor = _classify(form, item_codes)
        if kind is None:
            continue
        signals.append(
            Signal(
                kind=kind,
                source="sec_edgar",
                company_domain=company_domain,
                company_name=title,
                title=f"{form} ({descriptor})",
                url=filing_url,
                observed_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
                payload={
                    "cik": cik,
                    "ticker": ticker,
                    "form": form,
                    "items": item_codes,
                    "accession": acc_no,
                },
                strength=strength,
            )
        )
    return signals


def _classify(form: str, items: str) -> tuple[SignalKind | None, float, str]:
    """Map (form, items) → (kind, strength, human descriptor). None = skip."""
    form = (form or "").upper()
    items_u = (items or "").upper()
    if form == "8-K":
        # Items: 5.02 exec changes, 2.01 acquisitions, 1.01 material agreement
        if "5.02" in items_u:
            return SignalKind.EXEC_CHANGE, 0.9, "exec change — Item 5.02"
        if "2.01" in items_u:
            return SignalKind.FUNDING, 0.8, "completed acquisition — Item 2.01"
        if "1.01" in items_u:
            return SignalKind.FILING, 0.6, "material agreement — Item 1.01"
        return SignalKind.FILING, 0.35, "other 8-K"
    if form in ("S-1", "S-1/A"):
        return SignalKind.FUNDING, 0.9, "IPO prep"
    if form in ("10-K", "10-K/A"):
        return SignalKind.EARNINGS, 0.5, "annual report"
    if form in ("10-Q", "10-Q/A"):
        return SignalKind.EARNINGS, 0.4, "quarterly report"
    if form in ("425",):
        return SignalKind.FUNDING, 0.7, "merger solicitation"
    return None, 0.0, ""


def _filing_url(cik: str, accession: str, primary: str) -> str | None:
    if not accession:
        return None
    acc_no_nodashes = accession.replace("-", "")
    try:
        cik_int = int(cik)
    except ValueError:
        return None
    if primary:
        return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_nodashes}/{primary}"
    return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={accession}&dateb=&owner=include&count=40"

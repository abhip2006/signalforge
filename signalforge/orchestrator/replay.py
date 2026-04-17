"""Replay a prior run's signals through (optionally new) scoring/prompts.

Why it exists: prompt or weight changes shouldn't require re-hitting Greenhouse,
GitHub, SEC, and Exa. `replay` reads a prior run's signals from SQLite and
re-runs the cheap-to-change steps (scoring + brief + draft + eval) against
whatever config + env is current.

Typical loop:
  1. One expensive `run` fetches real signals.
  2. N cheap `replay` calls iterate on the ICP YAML, the drafter prompt, or
     the eval weights — each one writes a fresh run_id into SQLite.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from signalforge.config import Env, ICPConfig
from signalforge.cost import LEDGER
from signalforge.drafts import generate_drafts
from signalforge.enrichment import fetch_company_context
from signalforge.models import (
    Company,
    Draft,
    EnrichedAccount,
    EvalScore,
    PipelineRun,
    ResearchBrief,
    Signal,
    SignalKind,
)
from signalforge.research import generate_brief
from signalforge.scoring import score_account
from signalforge.sinks import SqliteSink, write_csv_report, write_html_report

console = Console()


def _load_signals(db_path, source_run_id: str) -> list[Signal]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT kind, source, company_domain, company_name, title, url, "
            "observed_at, strength, payload FROM signals WHERE run_id = ?",
            (source_run_id,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    out: list[Signal] = []
    for kind, source, cd, cn, title, url, ots, strength, payload in rows:
        try:
            kind_enum = SignalKind(kind)
        except ValueError:
            continue
        try:
            observed = datetime.fromisoformat(ots)
        except (TypeError, ValueError):
            observed = datetime.now(UTC)
        try:
            payload_obj = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            payload_obj = {}
        out.append(
            Signal(
                kind=kind_enum,
                source=source or "unknown",
                company_domain=cd,
                company_name=cn,
                title=title or "",
                url=url,
                observed_at=observed,
                payload=payload_obj,
                strength=float(strength or 0.0),
            )
        )
    return out


async def replay_run(
    source_run_id: str,
    icp: ICPConfig,
    env: Env,
    *,
    limit: int | None = None,
    skip_drafts: bool = False,
) -> tuple[PipelineRun, list[tuple[EnrichedAccount, ResearchBrief, Draft, EvalScore]]]:
    """Re-score + re-brief + re-draft a prior run's signals under the current config."""
    db_path = env.data_dir / "signalforge.db"
    signals = _load_signals(db_path, source_run_id)
    if not signals:
        raise ValueError(f"no signals found for run_id={source_run_id}")

    run_id = (
        datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        + "-replay-"
        + uuid.uuid4().hex[:6]
    )
    run = PipelineRun(
        run_id=run_id,
        started_at=datetime.now(UTC),
        config_path=f"{icp.name} (replay of {source_run_id})",
        config_hash=icp.hash(),
    )
    db = SqliteSink(db_path)
    db.record_run(run)
    db.record_signals(run_id, signals)
    LEDGER.reset()

    # Group by domain, score, cap
    from collections import defaultdict

    buckets: dict[str, list[Signal]] = defaultdict(list)
    for s in signals:
        buckets[s.company_domain].append(s)
    accounts = [
        EnrichedAccount(
            company=Company(
                domain=domain,
                name=next((s.company_name for s in sigs if s.company_name), None),
            ),
            signals=sigs,
        )
        for domain, sigs in buckets.items()
    ]
    accounts = [score_account(a, icp) for a in accounts]
    accounts.sort(key=lambda a: a.icp_score, reverse=True)
    if limit is not None:
        accounts = accounts[:limit]
    for a in accounts:
        db.record_account(run_id, a)

    results: list[tuple[EnrichedAccount, ResearchBrief, Draft, EvalScore]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        targets = [a for a in accounts if a.icp_score >= icp.min_icp_score]
        t = progress.add_task(
            f"replaying {len(targets)} accounts " f"(source={source_run_id})",
            total=len(targets),
        )
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)):
            max_variants = int(icp.raw.get("drafts", {}).get("max_variants_per_account", 3))
            for a in targets:
                ctx_snippet = await fetch_company_context(a.company.domain, env)
                brief = await generate_brief(a, icp, env, company_context=ctx_snippet)
                db.record_brief(run_id, brief)
                if skip_drafts:
                    # placeholder
                    pass
                else:
                    variants = await generate_drafts(a, brief, icp, env, max_variants=max_variants)
                    for d, sc in variants:
                        db.record_draft(run_id, d, sc)
                    if variants:
                        best_d, best_s = variants[0]
                        results.append((a, brief, best_d, best_s))
                progress.advance(t)

    finished = datetime.now(UTC)
    draft_scores = [s.overall for _, _, _, s in results if s.overall > 0]
    run_final = run.model_copy(
        update={
            "finished_at": finished,
            "accounts_processed": len(accounts),
            "signals_ingested": len(signals),
            "drafts_generated": len(results) if not skip_drafts else 0,
            "avg_draft_score": round(sum(draft_scores) / len(draft_scores), 2) if draft_scores else None,
        }
    )
    db.record_run(run_final)

    runs_dir = env.data_dir / "runs" / run_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    if results:
        write_csv_report(runs_dir / "accounts.csv", results)
        write_html_report(runs_dir / "report.html", run_final, results)

    return run_final, results

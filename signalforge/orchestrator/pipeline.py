"""End-to-end pipeline runner.

signals  →  group by account  →  score  →  (above threshold) →  brief  →  drafts+evals  →  sinks
"""
from __future__ import annotations

import asyncio
import os
import uuid
from collections import defaultdict
from datetime import UTC, datetime

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from signalforge.config import Env, ICPConfig
from signalforge.cost import LEDGER
from signalforge.drafts import generate_drafts
from signalforge.enrichment import fetch_company_context, fetch_contacts_for_domains
from signalforge.models import (
    Company,
    Draft,
    EnrichedAccount,
    EvalScore,
    PipelineRun,
    ResearchBrief,
    Signal,
)
from signalforge.research import generate_brief
from signalforge.scoring import score_account
from signalforge.signals import REGISTRY
from signalforge.signals.base import SourceContext
from signalforge.sinks import (
    SqliteSink,
    post_top_accounts,
    sync_to_hubspot,
    write_csv_report,
    write_html_report,
)

console = Console()


async def _collect_signals(
    ctx: SourceContext, icp: ICPConfig
) -> list[Signal]:
    tasks = []
    for source_key, source_cfg in (icp.sources or {}).items():
        klass = REGISTRY.get(source_key)
        if klass is None:
            console.log(f"[yellow]unknown source: {source_key}, skipping[/]")
            continue
        source = klass()
        tasks.append(source.collect(ctx, source_cfg or {}))
    if not tasks:
        return []
    results = await asyncio.gather(*tasks, return_exceptions=True)
    signals: list[Signal] = []
    for r in results:
        if isinstance(r, Exception):
            console.log(f"[red]source failed:[/] {r}")
            continue
        signals.extend(r or [])
    return signals


def _group_by_account(signals: list[Signal]) -> dict[str, list[Signal]]:
    buckets: dict[str, list[Signal]] = defaultdict(list)
    for s in signals:
        buckets[s.company_domain].append(s)
    return buckets


async def run_pipeline(
    icp: ICPConfig,
    env: Env,
    *,
    limit: int | None = None,
    skip_drafts: bool = False,
    push_slack: bool = False,
    push_hubspot: bool = False,
    delta: bool = False,
) -> tuple[PipelineRun, list[tuple[EnrichedAccount, ResearchBrief, Draft, EvalScore]]]:
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    started = datetime.now(UTC)
    run = PipelineRun(
        run_id=run_id,
        started_at=started,
        config_path=icp.name,
        config_hash=icp.hash(),
    )

    db = SqliteSink(env.data_dir / "signalforge.db")
    previously_seen_ids: set[str] = db.existing_signal_ids() if delta else set()
    db.record_run(run)
    LEDGER.reset()

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        ctx = SourceContext(env=env, http=http)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        ) as progress:
            t1 = progress.add_task("collecting signals…", total=None)
            signals = await _collect_signals(ctx, icp)
            if delta and previously_seen_ids:
                fresh = [s for s in signals if s.signal_id not in previously_seen_ids]
                progress.update(
                    t1,
                    description=(
                        f"collected {len(signals)} signals · "
                        f"{len(fresh)} new since last run"
                    ),
                )
                signals = fresh
            else:
                progress.update(t1, description=f"collected {len(signals)} signals")
            progress.remove_task(t1)

            db.record_signals(run_id, signals)

            buckets = _group_by_account(signals)
            accounts = [
                EnrichedAccount(
                    company=Company(
                        domain=domain,
                        name=_first_company_name(sigs),
                    ),
                    signals=sigs,
                )
                for domain, sigs in buckets.items()
            ]
            # Score + sort desc
            accounts = [score_account(a, icp) for a in accounts]
            accounts.sort(key=lambda a: a.icp_score, reverse=True)
            if limit is not None:
                accounts = accounts[:limit]

            for a in accounts:
                db.record_account(run_id, a)

            results: list[tuple[EnrichedAccount, ResearchBrief, Draft, EvalScore]] = []

            targets = [a for a in accounts if a.icp_score >= icp.min_icp_score]

            # Contact enrichment is opt-in — Apollo's free tier is tight and
            # fanning out per-domain can burn a month's credits in one run.
            # Enabled when APOLLO_ENABLED=1 OR the ICP config sets
            # `apollo_enrichment: true` at the top level.
            if _apollo_enabled(icp) and env.apollo_api_key and targets:
                contacts_by_domain = await fetch_contacts_for_domains(
                    [a.company.domain for a in targets],
                    icp.target_titles,
                    env,
                )
                targets = [
                    a.model_copy(update={"contacts": contacts_by_domain.get(a.company.domain, [])})
                    for a in targets
                ]
                # Also reflect the enriched contacts on the `accounts` list so
                # downstream sinks (e.g. HTML report) see them.
                enriched_map = {a.company.domain: a for a in targets}
                accounts = [enriched_map.get(a.company.domain, a) for a in accounts]
            max_variants = int(icp.raw.get("drafts", {}).get("max_variants_per_account", 3))
            concurrency = int(icp.raw.get("runtime", {}).get("concurrency", 4))
            label = "researching" if skip_drafts else "research + drafts for"
            t = progress.add_task(f"{label} {len(targets)} accounts (≤{concurrency}×)", total=len(targets))

            sem = asyncio.Semaphore(concurrency)

            async def _process(acc: EnrichedAccount):
                async with sem:
                    ctx_snippet = await fetch_company_context(acc.company.domain, env)
                    brief = await generate_brief(acc, icp, env, company_context=ctx_snippet)
                    db.record_brief(run_id, brief)
                    if skip_drafts:
                        progress.advance(t)
                        return (acc, brief, _empty_draft(acc), _empty_score(acc))
                    variants = await generate_drafts(
                        acc, brief, icp, env, max_variants=max_variants
                    )
                    for d, sc in variants:
                        db.record_draft(run_id, d, sc)
                    progress.advance(t)
                    if variants:
                        best_d, best_s = variants[0]
                        return (acc, brief, best_d, best_s)
                    return None

            gathered = await asyncio.gather(*[_process(a) for a in targets])
            results = [r for r in gathered if r is not None]
            # Keep the best-first ordering by draft score for the report.
            results.sort(key=lambda r: (r[3].overall, r[0].icp_score), reverse=True)

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

    # Sinks — local-first, then optional external routing
    runs_dir = env.data_dir / "runs" / run_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    if results:
        write_csv_report(runs_dir / "accounts.csv", results)
        write_html_report(runs_dir / "report.html", run_final, results)

    if push_slack and results:
        slack_res = await post_top_accounts(env, results, run_id=run_id)
        if slack_res.sent:
            console.log(f"slack: posted {slack_res.sent} accounts")
        elif slack_res.reason:
            console.log(f"[yellow]slack skipped:[/] {slack_res.reason}")

    if push_hubspot and results:
        hs_res = await sync_to_hubspot(env, results, run_id=run_id)
        if hs_res.errors:
            console.log(f"[yellow]hubspot errors:[/] {hs_res.errors[:3]}")
        if hs_res.companies_upserted:
            console.log(
                f"hubspot: {hs_res.companies_upserted} companies, "
                f"{hs_res.notes_created} notes, {hs_res.tasks_created} tasks"
            )

    return run_final, results


def _first_company_name(signals: list[Signal]) -> str | None:
    for s in signals:
        if s.company_name:
            return s.company_name
    return None


def _apollo_enabled(icp: ICPConfig) -> bool:
    """Opt-in gate: env var APOLLO_ENABLED=1 OR `apollo_enrichment: true`
    at the top level of the ICP YAML. Default is OFF — Apollo's free tier
    quota can be exhausted by a single pipeline run.
    """
    if os.environ.get("APOLLO_ENABLED", "").strip() in {"1", "true", "TRUE", "yes"}:
        return True
    return bool(icp.raw.get("apollo_enrichment", False))


def _empty_draft(a: EnrichedAccount) -> Draft:
    from signalforge.models import DraftKind
    return Draft(account_domain=a.company.domain, kind=DraftKind.OPENER, body="")


def _empty_score(a: EnrichedAccount) -> EvalScore:
    return EvalScore(
        draft_id="empty", overall=0.0, dimensions={}, rationale="", judge_model=""
    )

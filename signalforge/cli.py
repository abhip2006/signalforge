"""`signalforge` command-line interface."""
from __future__ import annotations

import asyncio
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from signalforge.config import Env, ICPConfig
from signalforge.cost import LEDGER
from signalforge.models import DraftKind
from signalforge.orchestrator import replay_run, run_pipeline

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def run(
    config: Path = typer.Option(..., "--config", "-c", help="Path to ICP YAML."),
    limit: int = typer.Option(None, "--limit", "-n", help="Cap account count after scoring."),
    skip_drafts: bool = typer.Option(False, help="Only run signals+briefs, skip drafting/eval."),
    open_report: bool = typer.Option(True, help="Open the HTML report at the end."),
    slack: bool = typer.Option(False, "--slack", help="Post top-scoring results to SLACK_WEBHOOK_URL."),
    hubspot: bool = typer.Option(False, "--hubspot", help="Upsert to HubSpot using HUBSPOT_TOKEN."),
    delta: bool = typer.Option(False, "--delta", help="Only score signals that are NEW since the last run (cron-friendly)."),
    draft_kind: str = typer.Option(
        "opener",
        "--draft-kind",
        help="Kind of draft to generate: opener | follow_up_1 | follow_up_2 | reply_thread | linkedin_note.",
    ),
) -> None:
    """Run the full pipeline: signals → score → brief → draft → eval → report."""
    icp = ICPConfig.load(config)
    env = Env.load()
    console.log(f"loaded ICP [bold]{icp.name}[/] (hash {icp.hash()})")
    if not env.anthropic_api_key:
        console.log("[yellow]ANTHROPIC_API_KEY not set — brief/draft/eval will use stubs[/]")

    try:
        kind = DraftKind(draft_kind)
    except ValueError as exc:
        valid = ", ".join(k.value for k in DraftKind)
        console.print(f"[red]invalid --draft-kind:[/] {exc}. Valid: {valid}")
        raise typer.Exit(2) from exc

    run_final, results = asyncio.run(
        run_pipeline(
            icp, env,
            limit=limit, skip_drafts=skip_drafts,
            push_slack=slack, push_hubspot=hubspot,
            delta=delta,
            draft_kind=kind,
        )
    )

    # Summary table
    table = Table(title=f"Run {run_final.run_id}", show_lines=False)
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("accounts processed", str(run_final.accounts_processed))
    table.add_row("signals ingested", str(run_final.signals_ingested))
    table.add_row("drafts generated", str(run_final.drafts_generated))
    if run_final.avg_draft_score is not None:
        table.add_row("avg draft score", f"{run_final.avg_draft_score:.1f}")
    console.print(table)

    # Cost ledger
    if LEDGER.events:
        cost_table = Table(title="Token + cost ledger", show_lines=False)
        cost_table.add_column("step")
        cost_table.add_column("calls", justify="right")
        cost_table.add_column("input", justify="right")
        cost_table.add_column("output", justify="right")
        cost_table.add_column("cache_read", justify="right")
        cost_table.add_column("USD", justify="right")
        for step, row in LEDGER.by_step().items():
            cost_table.add_row(
                step,
                str(int(row["calls"])),
                f"{int(row['input']):,}",
                f"{int(row['output']):,}",
                f"{int(row['cache_read']):,}",
                f"${row['cost_usd']:.4f}",
            )
        cost_table.add_row(
            "[bold]total[/]",
            f"[bold]{len(LEDGER.events)}[/]",
            f"[bold]{LEDGER.total_input:,}[/]",
            f"[bold]{LEDGER.total_output:,}[/]",
            f"[bold]{LEDGER.total_cache_read:,}[/]  ({LEDGER.cache_hit_rate*100:.0f}%)",
            f"[bold]${LEDGER.total_cost_usd:.4f}[/]",
        )
        console.print(cost_table)

    if results:
        top = Table(title="Top accounts", show_lines=False)
        top.add_column("ICP", justify="right")
        top.add_column("draft", justify="right")
        top.add_column("domain")
        top.add_column("headline")
        for acc, brief, _d, score in results[:12]:
            top.add_row(
                f"{acc.icp_score:.0f}",
                f"{score.overall:.0f}" if score.overall else "—",
                acc.company.domain,
                brief.headline[:70],
            )
        console.print(top)

    report_html = env.data_dir / "runs" / run_final.run_id / "report.html"
    if report_html.exists():
        console.print(f"\nreport: [cyan]{report_html}[/]")
        if open_report:
            webbrowser.open(report_html.as_uri())


@app.command()
def replay(
    run_id: str = typer.Option(..., "--run-id", "-r", help="Source run ID to replay."),
    config: Path = typer.Option(..., "--config", "-c", help="ICP YAML to re-score with."),
    limit: int = typer.Option(None, "--limit", "-n", help="Cap account count after scoring."),
    skip_drafts: bool = typer.Option(False, help="Only re-score + re-brief."),
    open_report: bool = typer.Option(True, help="Open the HTML report at the end."),
) -> None:
    """Re-score + re-brief + re-draft a prior run's signals with the current config/prompts."""
    icp = ICPConfig.load(config)
    env = Env.load()
    console.log(f"replaying [bold]{run_id}[/] with ICP {icp.name} ({icp.hash()})")

    run_final, results = asyncio.run(
        replay_run(run_id, icp, env, limit=limit, skip_drafts=skip_drafts)
    )

    table = Table(title=f"Replay {run_final.run_id}")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("source run", run_id)
    table.add_row("signals replayed", str(run_final.signals_ingested))
    table.add_row("accounts processed", str(run_final.accounts_processed))
    table.add_row("drafts generated", str(run_final.drafts_generated))
    if run_final.avg_draft_score is not None:
        table.add_row("avg draft score", f"{run_final.avg_draft_score:.1f}")
    if LEDGER.events:
        table.add_row("API cost (USD)", f"${LEDGER.total_cost_usd:.4f}")
    console.print(table)

    report_html = env.data_dir / "runs" / run_final.run_id / "report.html"
    if report_html.exists():
        console.print(f"report: [cyan]{report_html}[/]")
        if open_report:
            webbrowser.open(report_html.as_uri())


@app.command()
def report(
    run_id: str = typer.Option(None, "--run-id", help="Run ID; defaults to latest."),
    latest: bool = typer.Option(False, "--latest", help="Open the latest run's report."),
) -> None:
    """Open an existing run's HTML report."""
    env = Env.load()
    runs_dir = env.data_dir / "runs"
    if not runs_dir.exists():
        console.print("[red]no runs yet[/]")
        raise typer.Exit(1)
    if latest or not run_id:
        candidates = sorted(runs_dir.iterdir(), reverse=True)
        if not candidates:
            console.print("[red]no runs yet[/]")
            raise typer.Exit(1)
        run_id = candidates[0].name
    path = runs_dir / run_id / "report.html"
    if not path.exists():
        console.print(f"[red]no report for run {run_id}[/]")
        raise typer.Exit(1)
    console.print(f"opening {path}")
    webbrowser.open(path.as_uri())


@app.command()
def doctor() -> None:
    """Check environment configuration."""
    env = Env.load()
    table = Table(title="SignalForge environment")
    table.add_column("key")
    table.add_column("status")
    for name, value in [
        ("ANTHROPIC_API_KEY", env.anthropic_api_key),
        ("EXA_API_KEY", env.exa_api_key),
        ("FIRECRAWL_API_KEY", env.firecrawl_api_key),
        ("GITHUB_TOKEN", env.github_token),
        ("APOLLO_API_KEY", env.apollo_api_key),
        ("HUNTER_API_KEY", env.hunter_api_key),
        ("FMP_API_KEY", env.fmp_api_key),
        ("HUBSPOT_TOKEN", env.hubspot_token),
    ]:
        table.add_row(name, "[green]set[/]" if value else "[dim]unset[/]")
    table.add_row("data_dir", str(env.data_dir))
    table.add_row("claude_model", env.claude_model)
    table.add_row("claude_model_fast", env.claude_model_fast)
    console.print(table)


if __name__ == "__main__":
    app()

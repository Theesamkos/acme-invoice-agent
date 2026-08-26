"""CLI entry point: argument parsing and top-level orchestration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from invoice_agent.config import ConfigError, resolve_llm_settings

console = Console()

INVOICE_DIR = Path("data/invoices")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Process invoices: ingestion -> validation -> approval -> payment.",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--invoice_path", help="Path to a single invoice file to process")
    target.add_argument(
        "--batch", action="store_true", help="Process every invoice in data/invoices/"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Show full agent traces and LLM reasoning"
    )
    return parser


def _make_deps():
    from invoice_agent import db
    from invoice_agent.graph import PipelineDeps
    from invoice_agent.llm import make_client

    settings = resolve_llm_settings()
    models = settings.model
    if settings.extraction_model != settings.model:
        models = f"{settings.model} (reasoning) + {settings.extraction_model} (extraction)"
    console.print(f"[dim]LLM: {settings.provider} · {models} · inventory.db auto-seeded[/]")
    return PipelineDeps(
        client=make_client(settings),
        model=settings.model,
        conn=db.get_connection(),
        extraction_model=settings.extraction_model,
    )


def _row(path: str, state: dict) -> dict:
    extraction = state.get("extraction")
    report = state.get("report")
    data = extraction.data if extraction else None
    total_usd = None
    if report is not None and report.total_usd is not None:
        total_usd = report.total_usd
    elif data is not None:
        total_usd = data.total
    return {
        "path": path,
        "invoice": data.invoice_number if data else None,
        "vendor": data.vendor_name if data else None,
        "total_usd": total_usd,
        "verdict": state.get("verdict", "FAILED"),
        "key_finding": _key_finding(state),
    }


def _key_finding(state: dict) -> str:
    report = state.get("report")
    if state.get("verdict") in ("SUPERSEDED", "DUPLICATE", "FAILED"):
        return state.get("error") or ""
    if report and report.blocking_findings:
        worst = report.blocking_findings[0]
        return f"{worst.code}: {worst.message}"
    if report and report.requires_escalation:
        return "passed extra scrutiny"
    return "clean"


def run_single(deps, invoice_path: str, verbose: bool) -> int:
    from invoice_agent.cli.render import findings_table, print_stage, verdict_banner
    from invoice_agent.graph import process_invoice

    path = Path(invoice_path)
    console.print(Panel(f"[bold]{path.name}[/]", title="Invoice", expand=False))

    def on_stage(node: str, stage_state: dict, seconds: float) -> None:
        print_stage(console, node, seconds, stage_state)

    with console.status("[bold]Processing...[/]"):
        state, _ = process_invoice(deps, str(path), on_stage=on_stage)

    report = state.get("report")
    if report is not None and (table := findings_table(report)) is not None:
        console.print(Panel(table, title="Validation findings", border_style="dim"))
    if verbose and state.get("extraction"):
        console.print(
            Panel(
                json.dumps(state["extraction"].data.model_dump(), indent=2, default=str),
                title="Extracted data",
                border_style="dim",
            )
        )
        if state.get("approval") and state["approval"].critiques:
            console.print(
                Panel(
                    "\n\n".join(state["approval"].critiques),
                    title="Auditor critiques",
                    border_style="yellow",
                )
            )
    console.print(verdict_banner(state))
    console.print("[dim]Audit trail: logs/audit.jsonl[/]")
    return 0 if state.get("verdict") == "PAID" else 1


def run_batch(deps, verbose: bool) -> int:
    from invoice_agent.agents.ingestion import ingest
    from invoice_agent.cli.batch import plan_batch
    from invoice_agent.cli.render import VERDICT_STYLES, batch_table, batch_totals
    from invoice_agent.graph import process_invoice
    from invoice_agent.tools.parsers import SUPPORTED_EXTENSIONS

    files = sorted(p for p in INVOICE_DIR.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS)
    if not files:
        console.print(f"[red]No invoices found in {INVOICE_DIR}/[/]")
        return 2

    extractions = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Extracting", total=len(files))
        for f in files:
            progress.update(task, description=f"Extracting {f.name}")
            try:
                extractions.append(
                    (str(f), ingest(deps.client, deps.extraction_model, str(f)), None)
                )
            except Exception as exc:  # noqa: BLE001 -- batch isolation (PRD F12)
                extractions.append((str(f), None, f"{type(exc).__name__}: {exc}"))
            progress.advance(task)

    plan = plan_batch(extractions)
    console.print(
        f"[dim]Dedup scan: {len(plan)} file(s), "
        f"{sum(1 for e in plan if e.skip)} superseded/duplicate[/]\n"
    )

    rows = []
    for entry in plan:
        state, _ = process_invoice(deps, entry.path, extraction=entry.extraction, skip=entry.skip)
        row = _row(entry.path, state)
        rows.append(row)
        style, icon = VERDICT_STYLES.get(row["verdict"], VERDICT_STYLES["FAILED"])
        console.print(
            f"  [{style}] {icon} {row['verdict']:<10}[/] "
            f"{Path(entry.path).name:<28} [dim]{row['key_finding']}[/]"
        )

    console.print()
    console.print(batch_table(rows))
    batch_totals(console, rows)
    console.print("\n[dim]Audit trail: logs/audit.jsonl[/]")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        deps = _make_deps()
    except ConfigError as exc:
        console.print(Panel(str(exc), title="Configuration error", border_style="red"))
        return 2

    try:
        if args.batch:
            return run_batch(deps, args.verbose)
        if not Path(args.invoice_path).is_file():
            console.print(
                Panel(
                    f"File not found: [bold]{args.invoice_path}[/]\n"
                    f"Available invoices live in [bold]{INVOICE_DIR}/[/]",
                    title="Input error",
                    border_style="red",
                )
            )
            return 2
        return run_single(deps, args.invoice_path, args.verbose)
    finally:
        deps.conn.close()

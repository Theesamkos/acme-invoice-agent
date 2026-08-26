"""Rich rendering: the product's face. Glanceable verdicts, progressive disclosure."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from invoice_agent.graph import PipelineState
from invoice_agent.models import Severity, ValidationReport

VERDICT_STYLES = {
    "PAID": ("bold white on green", "✓"),
    "REJECTED": ("bold white on red", "✗"),
    "SUPERSEDED": ("bold black on yellow", "↺"),
    "DUPLICATE": ("bold black on yellow", "≡"),
    "FAILED": ("bold white on magenta", "!"),
}

SEVERITY_STYLES = {
    Severity.INFO: "cyan",
    Severity.WARNING: "yellow",
    Severity.ERROR: "red",
    Severity.CRITICAL: "bold red",
}

FRAUD_STYLES = {"low": "green", "elevated": "yellow", "high": "red", "critical": "bold red"}

STAGE_LABELS = {
    "ingestion": "Ingestion",
    "validation": "Validation",
    "approval": "Approval",
    "payment": "Payment",
    "rejection": "Rejection",
    "skipped": "Dedup",
    "failed": "Pipeline",
}


def stage_summary(node: str, state: PipelineState) -> str:
    """One informative line per completed stage."""
    if node == "ingestion" and "extraction" in state:
        ex = state["extraction"]
        n = len(ex.data.line_items)
        retries = f", {ex.attempts - 1} self-correction(s)" if ex.attempts > 1 else ""
        return f"extracted {n} line item(s) from {ex.source_format.upper()}{retries}"
    if node == "validation" and "report" in state:
        report = state["report"]
        blocking = len(report.blocking_findings)
        parts = [f"{len(report.findings)} finding(s), {blocking} blocking"]
        parts.append(f"fraud {report.fraud_level} ({report.fraud_score}/100)")
        if report.requires_escalation:
            parts.append("escalated for extra scrutiny")
        return "; ".join(parts)
    if node == "approval" and "approval" in state:
        ap = state["approval"]
        scrutiny = "extra scrutiny" if ap.scrutiny == "extra" else "standard review"
        reflections = f", {len(ap.critiques)} critique(s) applied" if ap.critiques else ""
        return f"{ap.verdict.lower()} after {scrutiny}, {ap.iterations} pass(es){reflections}"
    if node == "payment" and "payment" in state:
        p = state["payment"]
        return f"${p['amount_usd']:,.2f} paid to {p['vendor']}"
    if node in ("rejection", "skipped", "failed"):
        return state.get("error") or "routed to rejection log"
    return ""


def print_stage(console: Console, node: str, seconds: float, state: PipelineState) -> None:
    label = STAGE_LABELS.get(node, node.title())
    detail = stage_summary(node, state)
    ok = node not in ("failed",)
    icon = "[green]✓[/]" if ok else "[magenta]![/]"
    console.print(f"  {icon} [bold]{label:<10}[/] [dim]{seconds:5.1f}s[/]  {detail}")


def findings_table(report: ValidationReport) -> Table | None:
    if not report.findings:
        return None
    table = Table(box=None, pad_edge=False, show_header=False, padding=(0, 1))
    table.add_column(width=2)
    table.add_column(style="dim", min_width=22)
    table.add_column(overflow="fold")
    icons = {
        Severity.INFO: "·",
        Severity.WARNING: "⚠",
        Severity.ERROR: "✗",
        Severity.CRITICAL: "‼",
    }
    for f in sorted(report.findings, key=lambda f: list(Severity).index(f.severity), reverse=True):
        style = SEVERITY_STYLES[f.severity]
        table.add_row(Text(icons[f.severity], style=style), Text(f.code, style=style), f.message)
    return table


def verdict_banner(state: PipelineState) -> Panel:
    verdict = state.get("verdict", "FAILED")
    style, icon = VERDICT_STYLES.get(verdict, VERDICT_STYLES["FAILED"])
    approval = state.get("approval")
    reasoning = approval.reasoning if approval else state.get("error") or "no decision reached"
    body = Text()
    body.append(f" {icon} {verdict} ", style=style)
    body.append(f"\n\n{reasoning}")
    if approval and approval.rule_override:
        body.append(f"\n\nPolicy override: {approval.rule_override}", style="yellow")
    return Panel(body, border_style=style.split()[-1].removeprefix("on "), padding=(1, 2))


def batch_table(rows: list[dict]) -> Table:
    table = Table(title="Batch results", title_style="bold", pad_edge=False)
    table.add_column("File", style="dim")
    table.add_column("Invoice")
    table.add_column("Vendor", max_width=26)
    table.add_column("Total (USD)", justify="right")
    table.add_column("Verdict")
    table.add_column("Key finding", max_width=48)
    for row in rows:
        style, icon = VERDICT_STYLES.get(row["verdict"], VERDICT_STYLES["FAILED"])
        table.add_row(
            Path(row["path"]).name,
            row["invoice"] or "—",
            row["vendor"] or "—",
            f"${row['total_usd']:,.2f}" if row["total_usd"] is not None else "—",
            Text(f" {icon} {row['verdict']} ", style=style),
            row["key_finding"],
        )
    return table


def batch_totals(console: Console, rows: list[dict]) -> None:
    paid = [r for r in rows if r["verdict"] == "PAID"]
    blocked = [r for r in rows if r["verdict"] in ("REJECTED", "SUPERSEDED", "DUPLICATE")]
    paid_sum = sum(r["total_usd"] or 0 for r in paid)
    # a blocked invoice with a negative/absent total still protected $0, not negative dollars
    protected_sum = sum(max(0.0, r["total_usd"] or 0.0) for r in blocked)
    console.print()
    console.print(
        f"  [green]Paid:[/] {len(paid)} invoice(s), [green]${paid_sum:,.2f}[/]   "
        f"[red]Blocked:[/] {len(blocked)} invoice(s)   "
        f"[bold]Dollars protected: [red]${protected_sum:,.2f}[/][/]"
    )

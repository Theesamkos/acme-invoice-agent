"""Validation agent: deterministic cross-checks over extracted invoice data.

Order of operations: dates -> currency -> math -> inventory -> dedup -> fraud.
Everything here is LLM-free, reproducible, and unit-tested.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from invoice_agent import db
from invoice_agent.models import (
    ExtractionResult,
    Finding,
    Severity,
    StockCheck,
    ValidationReport,
)
from invoice_agent.tools.fraud import score_fraud
from invoice_agent.tools.math_check import check_math
from invoice_agent.tools.normalize import match_item, parse_date, to_usd

ESCALATION_THRESHOLD_USD = 10_000.0


def check_inventory(data_items, inventory: dict[str, int]) -> list[StockCheck]:
    """Aggregate quantities per matched item (split lines count together), then check stock."""
    matches = []
    for li in data_items:
        matched, match_type = match_item(li.item, list(inventory))
        matches.append((li, matched, match_type))

    aggregated: dict[str | None, float] = defaultdict(float)
    for li, matched, _ in matches:
        if li.quantity > 0:  # negative quantities are an integrity issue, not a stock request
            aggregated[matched] += li.quantity

    checks: list[StockCheck] = []
    seen: set[str] = set()
    for li, matched, match_type in matches:
        key = matched if matched is not None else li.item
        if key in seen:
            continue
        seen.add(key)
        available = inventory.get(matched) if matched else None
        requested = aggregated.get(matched, li.quantity) if matched else li.quantity
        checks.append(
            StockCheck(
                requested_item=li.item,
                matched_item=matched,
                match_type=match_type,
                requested_qty=requested,
                available=available,
                sufficient=(available >= requested) if available is not None else None,
            )
        )
    return checks


def check_dedup(conn: sqlite3.Connection, data) -> tuple[str, list[Finding]]:
    """Consult the payment ledger for this invoice number."""
    findings: list[Finding] = []
    if not data.invoice_number:
        findings.append(
            Finding(
                code="missing_invoice_number",
                severity=Severity.ERROR,
                message="Invoice has no invoice number; cannot be deduplicated safely",
            )
        )
        return "new", findings

    entries = db.ledger_entries(conn, data.invoice_number)
    paid = [e for e in entries if e["verdict"] == "PAID"]
    if not entries:
        return "new", findings

    this_rev = data.revision or ""
    if any(e["revision"] == this_rev for e in entries):
        findings.append(
            Finding(
                code="duplicate_invoice",
                severity=Severity.CRITICAL,
                message=(
                    f"Invoice {data.invoice_number} (rev {this_rev or 'none'}) was already "
                    f"processed ({entries[-1]['verdict']} from {entries[-1]['source_file']})"
                ),
            )
        )
        return "duplicate", findings

    if paid:
        findings.append(
            Finding(
                code="supersedes_paid_invoice",
                severity=Severity.CRITICAL,
                message=(
                    f"Revision {this_rev!r} of {data.invoice_number} arrived, but an earlier "
                    f"version was already PAID -- requires manual review, not payment"
                ),
            )
        )
        return "supersedes_paid", findings

    findings.append(
        Finding(
            code="revises_pending_invoice",
            severity=Severity.INFO,
            message=f"Supersedes earlier unpaid version of {data.invoice_number}",
        )
    )
    return "new", findings


def validate(
    extraction: ExtractionResult, conn: sqlite3.Connection | None = None
) -> ValidationReport:
    data = extraction.data
    own_conn = conn is None
    if conn is None:
        conn = db.get_connection()
    try:
        report = ValidationReport()

        # Dates
        invoice_date = parse_date(data.invoice_date)
        due_date = parse_date(data.due_date)
        report.invoice_date_iso = invoice_date.isoformat() if invoice_date else None
        report.due_date_iso = due_date.isoformat() if due_date else None
        if data.due_date and due_date is None:
            report.findings.append(
                Finding(
                    code="unparseable_due_date",
                    severity=Severity.ERROR,
                    message=f"Due date {data.due_date!r} is not a parseable date",
                )
            )
        if not data.due_date:
            report.findings.append(
                Finding(
                    code="missing_due_date",
                    severity=Severity.ERROR,
                    message="Invoice states no due date",
                )
            )

        # Currency normalization
        report.currency = data.currency.upper()
        usd_total, rate = to_usd(data.total, report.currency)
        if rate is None:
            report.findings.append(
                Finding(
                    code="unknown_currency",
                    severity=Severity.ERROR,
                    message=f"No exchange rate for currency {data.currency!r}",
                )
            )
        else:
            report.exchange_rate = rate
            report.total_usd = usd_total
            if report.currency != "USD" and usd_total is not None:
                report.findings.append(
                    Finding(
                        code="currency_normalized",
                        severity=Severity.INFO,
                        message=(
                            f"Converted {data.total:,.2f} {report.currency} -> "
                            f"${usd_total:,.2f} USD at fixed rate {rate}"
                        ),
                    )
                )

        # Math cross-checks
        report.findings.extend(check_math(data))

        # Integrity: negative quantities
        for li in data.line_items:
            if li.quantity < 0:
                report.findings.append(
                    Finding(
                        code="negative_quantity",
                        severity=Severity.CRITICAL,
                        message=f"Line item {li.item!r} has negative quantity {li.quantity:g}",
                    )
                )

        # Inventory
        inventory = db.fetch_inventory(conn)
        report.stock_checks = check_inventory(data.line_items, inventory)
        for check in report.stock_checks:
            if check.match_type == "unknown":
                report.findings.append(
                    Finding(
                        code="unknown_item",
                        severity=Severity.ERROR,
                        message=f"Item {check.requested_item!r} is not in inventory",
                    )
                )
            elif check.sufficient is False:
                report.findings.append(
                    Finding(
                        code="insufficient_stock",
                        severity=Severity.ERROR,
                        message=(
                            f"{check.matched_item}: requested {check.requested_qty:g} "
                            f"exceeds stock of {check.available}"
                        ),
                    )
                )
            elif check.match_type in ("normalized", "fuzzy"):
                report.findings.append(
                    Finding(
                        code="item_name_normalized",
                        severity=Severity.INFO,
                        message=f"Matched {check.requested_item!r} -> {check.matched_item!r}",
                    )
                )

        # Dedup ledger
        report.dedup_status, dedup_findings = check_dedup(conn, data)
        report.findings.extend(dedup_findings)

        # Escalation gate (on USD-normalized total, falling back to stated total)
        gate_amount = report.total_usd if report.total_usd is not None else data.total
        if gate_amount is not None and gate_amount > ESCALATION_THRESHOLD_USD:
            report.requires_escalation = True
            report.findings.append(
                Finding(
                    code="amount_over_threshold",
                    severity=Severity.WARNING,
                    message=(
                        f"Total ${gate_amount:,.2f} exceeds ${ESCALATION_THRESHOLD_USD:,.0f} "
                        "-- requires VP extra scrutiny"
                    ),
                )
            )

        # Fraud scoring (deterministic, uses everything above)
        try:
            from invoice_agent.tools.parsers import load_document

            raw_text = load_document(extraction.source_file).text
        except (OSError, ValueError):
            # best-effort: fraud regexes fall back to the extractor's observations
            raw_text = "\n".join(data.anomalies)
        score, signals, level = score_fraud(
            data, raw_text, report.stock_checks, invoice_date, due_date
        )
        report.fraud_score, report.fraud_signals, report.fraud_level = score, signals, level
        if level in ("high", "critical"):
            report.findings.append(
                Finding(
                    code="fraud_risk",
                    severity=Severity.CRITICAL,
                    message=f"Fraud risk {level} (score {score}/100): " + "; ".join(signals),
                )
            )
        elif level == "elevated":
            report.requires_escalation = True
            report.findings.append(
                Finding(
                    code="fraud_risk_elevated",
                    severity=Severity.WARNING,
                    message=f"Elevated fraud signals (score {score}/100): " + "; ".join(signals),
                )
            )

        return report
    finally:
        if own_conn:
            conn.close()

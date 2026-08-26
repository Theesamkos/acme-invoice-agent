"""Arithmetic cross-check: recompute every stated figure from the line items.

This is the tool that catches silently padded totals (invoice 1013's +$50) and
inconsistent subtotals (invoice 1009). The LLM extracts; this recomputes.
"""

from __future__ import annotations

from invoice_agent.models import Finding, InvoiceData, Severity

TOLERANCE = 0.011  # dollar rounding slack


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TOLERANCE


def check_math(data: InvoiceData) -> list[Finding]:
    findings: list[Finding] = []

    # Per-line: stated amount vs qty x unit price
    line_sum = 0.0
    computable = False
    for idx, li in enumerate(data.line_items, start=1):
        computed = li.quantity * li.unit_price if li.unit_price is not None else None
        stated = li.amount
        if computed is not None and stated is not None and not _close(computed, stated):
            findings.append(
                Finding(
                    code="line_total_mismatch",
                    severity=Severity.ERROR,
                    message=(
                        f"Line {idx} ({li.item}): stated amount ${stated:,.2f} != "
                        f"{li.quantity:g} x ${li.unit_price:,.2f} = ${computed:,.2f}"
                    ),
                )
            )
        effective = stated if stated is not None else computed
        if effective is not None:
            line_sum += effective
            computable = True

    if not computable:
        findings.append(
            Finding(
                code="uncomputable_lines",
                severity=Severity.WARNING,
                message="No line item has enough numbers to recompute totals",
            )
        )
        return findings

    # Subtotal vs line sum
    if data.subtotal is not None and not _close(line_sum, data.subtotal):
        findings.append(
            Finding(
                code="subtotal_mismatch",
                severity=Severity.ERROR,
                message=(
                    f"Stated subtotal ${data.subtotal:,.2f} != line-item sum ${line_sum:,.2f} "
                    f"(delta ${data.subtotal - line_sum:+,.2f})"
                ),
            )
        )

    # Tax amount vs subtotal x rate
    base = data.subtotal if data.subtotal is not None else line_sum
    if data.tax_rate is not None and data.tax_amount is not None:
        expected_tax = base * data.tax_rate
        if not _close(expected_tax, data.tax_amount):
            findings.append(
                Finding(
                    code="tax_mismatch",
                    severity=Severity.ERROR,
                    message=(
                        f"Stated tax ${data.tax_amount:,.2f} != {data.tax_rate:.0%} of "
                        f"${base:,.2f} = ${expected_tax:,.2f}"
                    ),
                )
            )

    # Grand total vs subtotal + tax + shipping
    if data.total is not None:
        expected_total = base + (data.tax_amount or 0.0) + (data.shipping or 0.0)
        if not _close(expected_total, data.total):
            findings.append(
                Finding(
                    code="total_mismatch",
                    severity=Severity.ERROR,
                    message=(
                        f"Stated grand total ${data.total:,.2f} != recomputed "
                        f"${expected_total:,.2f} (delta ${data.total - expected_total:+,.2f})"
                    ),
                )
            )
        if data.total < 0:
            findings.append(
                Finding(
                    code="negative_total",
                    severity=Severity.CRITICAL,
                    message=f"Grand total is negative: ${data.total:,.2f}",
                )
            )
    else:
        findings.append(
            Finding(
                code="missing_total",
                severity=Severity.WARNING,
                message="Invoice states no grand total; using recomputed line sum",
            )
        )

    return findings

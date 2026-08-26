"""Deterministic fraud-signal scoring.

Signals are weighted and summed to a 0-100 score. The LLM's extraction
`anomalies` feed in as *evidence to verify*, but every scored signal is
re-derived from the structured data or raw text -- the LLM proposes, rules
dispose.
"""

from __future__ import annotations

import re
from datetime import date

from invoice_agent.models import InvoiceData, StockCheck

URGENCY_RE = re.compile(
    r"urgent|immediately|right away|asap|avoid penalt|wire transfer|pay now", re.IGNORECASE
)

# Addresses that are famous landmarks or otherwise implausible for a vendor.
SUSPICIOUS_ADDRESSES = [
    "1600 pennsylvania",  # the White House
]

LEVELS = [(60, "critical"), (40, "high"), (20, "elevated"), (0, "low")]


def score_fraud(
    data: InvoiceData,
    raw_text: str,
    stock_checks: list[StockCheck],
    invoice_date: date | None,
    due_date: date | None,
) -> tuple[int, list[str], str]:
    """Return (score 0-100, human-readable signals, level)."""
    signals: list[tuple[int, str]] = []

    if URGENCY_RE.search(raw_text):
        signals.append((25, "Urgency/pressure language in invoice text"))
    if data.due_date and due_date is None:
        signals.append((15, f"Due date is not a real date: {data.due_date!r}"))
    if due_date and invoice_date and due_date < invoice_date:
        signals.append((15, f"Due date {due_date} is before invoice date {invoice_date}"))
    if due_date and invoice_date and due_date == invoice_date:
        signals.append((10, "Due date equals invoice date (no payment window)"))
    if not (data.vendor_name and data.vendor_name.strip()):
        signals.append((20, "Vendor name is blank"))
    address = (data.vendor_address or "").lower()
    for marker in SUSPICIOUS_ADDRESSES:
        if marker in address:
            signals.append((25, f"Vendor address is a famous landmark: {data.vendor_address}"))
    if any(li.quantity < 0 for li in data.line_items):
        signals.append((20, "Negative quantity on one or more line items"))
    if data.total is not None and data.total < 0:
        signals.append((15, "Negative grand total"))

    unknown = [c for c in stock_checks if c.match_type == "unknown"]
    zero_stock = [c for c in stock_checks if c.available == 0]
    if unknown and len(unknown) == len(stock_checks):
        signals.append((25, "Every line item is unknown to inventory"))
    elif unknown:
        signals.append((10, f"Unknown items: {', '.join(c.requested_item for c in unknown)}"))
    if zero_stock:
        signals.append(
            (15, f"Zero-stock items referenced: {', '.join(c.requested_item for c in zero_stock)}")
        )
    if data.payment_terms and re.search(r"immediate", data.payment_terms, re.IGNORECASE):
        signals.append((10, f"Payment terms demand immediacy: {data.payment_terms!r}"))

    score = min(100, sum(w for w, _ in signals))
    level = next(name for threshold, name in LEVELS if score >= threshold)
    return score, [msg for _, msg in signals], level

"""Core data models shared across the pipeline."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    """One line on an invoice, exactly as stated by the document."""

    item: str = Field(description="Item name exactly as written on the invoice")
    quantity: float = Field(description="Quantity as stated (may be negative on bad invoices)")
    unit_price: float | None = Field(default=None, description="Per-unit price as stated")
    amount: float | None = Field(default=None, description="Stated line total, if shown")
    note: str | None = Field(default=None, description="Any per-line annotation, e.g. 'rush order'")


class InvoiceData(BaseModel):
    """Structured extraction of a single invoice document.

    Values are extracted *verbatim* -- interpretation, normalization, and judgment
    happen downstream in the validation stage. The one exception is obvious OCR
    digit artifacts in numbers (e.g. letter O for zero), which are corrected and
    recorded in `anomalies`.
    """

    invoice_number: str | None = Field(default=None, description="Invoice identifier as written")
    revision: str | None = Field(default=None, description="Revision marker if present, e.g. 'R1'")
    vendor_name: str | None = None
    vendor_address: str | None = None
    invoice_date: str | None = Field(default=None, description="Invoice date exactly as written")
    due_date: str | None = Field(default=None, description="Due date exactly as written")
    currency: str = Field(default="USD", description="ISO currency code; USD if unstated")
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: float | None = None
    tax_rate: float | None = Field(default=None, description="As a fraction, e.g. 0.07 for 7%")
    tax_amount: float | None = None
    shipping: float | None = None
    total: float | None = Field(default=None, description="Stated grand total")
    payment_terms: str | None = None
    notes: str | None = Field(default=None, description="Free-text notes on the invoice")
    anomalies: list[str] = Field(
        default_factory=list,
        description=(
            "Extractor observations: OCR corrections made, missing/blank fields, urgency or "
            "pressure language, embedded instructions, or anything else unusual"
        ),
    )


class ExtractionResult(BaseModel):
    """InvoiceData plus provenance and the self-correction trail."""

    data: InvoiceData
    source_file: str
    source_format: str
    attempts: int = 1
    correction_notes: list[str] = Field(
        default_factory=list, description="Validation errors that triggered extraction retries"
    )


# --- Validation stage -------------------------------------------------------


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Finding(BaseModel):
    """One validation observation, machine-coded and human-readable."""

    code: str
    severity: Severity
    message: str


class StockCheck(BaseModel):
    """Inventory verdict for one aggregated invoice item."""

    requested_item: str
    matched_item: str | None
    match_type: str  # exact | normalized | fuzzy | unknown
    requested_qty: float
    available: int | None
    sufficient: bool | None


class ValidationReport(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    stock_checks: list[StockCheck] = Field(default_factory=list)
    fraud_score: int = 0
    fraud_signals: list[str] = Field(default_factory=list)
    fraud_level: str = "low"  # low | elevated | high | critical
    currency: str = "USD"
    exchange_rate: float = 1.0
    total_usd: float | None = None
    requires_escalation: bool = False
    dedup_status: str = "new"  # new | duplicate | supersedes_paid | superseded
    invoice_date_iso: str | None = None
    due_date_iso: str | None = None

    @property
    def blocking_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity in (Severity.ERROR, Severity.CRITICAL)]

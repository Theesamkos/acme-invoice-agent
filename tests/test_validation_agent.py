"""End-to-end validation agent tests (no LLM -- synthetic extractions)."""

import pytest

from invoice_agent import db
from invoice_agent.agents.validation import check_inventory, validate
from invoice_agent.models import ExtractionResult, InvoiceData, LineItem


@pytest.fixture
def conn(tmp_path):
    connection = db.get_connection(tmp_path / "test.db")
    yield connection
    connection.close()


def _extraction(data: InvoiceData, source="synthetic.json") -> ExtractionResult:
    return ExtractionResult(data=data, source_file=source, source_format="json")


def test_db_auto_seeds(conn):
    assert db.fetch_inventory(conn) == {"WidgetA": 15, "WidgetB": 10, "GadgetX": 5, "FakeItem": 0}


def test_split_lines_aggregate_against_stock(conn):
    """Invoice 1013: WidgetA split across 3 lines (15+5+2=22) must exceed stock of 15."""
    items = [
        LineItem(item="WidgetA", quantity=15, unit_price=250.0),
        LineItem(item="WidgetA", quantity=5, unit_price=240.0),
        LineItem(item="WidgetA", quantity=2, unit_price=250.0),
    ]
    checks = check_inventory(items, db.fetch_inventory(conn))
    assert len(checks) == 1
    assert checks[0].requested_qty == 22
    assert checks[0].sufficient is False


def test_ocr_item_names_match_inventory(conn):
    checks = check_inventory(
        [LineItem(item="Widget A", quantity=2), LineItem(item="Gadget X", quantity=1)],
        db.fetch_inventory(conn),
    )
    assert all(c.match_type == "normalized" and c.sufficient for c in checks)


def test_escalation_gate_on_usd_normalized_total(conn):
    data = InvoiceData(
        invoice_number="INV-X",
        vendor_name="V",
        due_date="2026-02-01",
        invoice_date="2026-01-01",
        currency="EUR",
        line_items=[LineItem(item="WidgetA", quantity=10, unit_price=950.0)],
        total=9500.0,  # 9500 EUR = 10450 USD -> crosses the $10K gate only after conversion
    )
    report = validate(_extraction(data), conn)
    assert report.total_usd == 10450.0
    assert report.requires_escalation is True


def test_duplicate_detection_via_ledger(conn):
    data = InvoiceData(
        invoice_number="INV-1004",
        vendor_name="Precision Parts Ltd.",
        invoice_date="2026-01-22",
        due_date="2026-02-22",
        line_items=[LineItem(item="WidgetA", quantity=3, unit_price=250.0)],
        subtotal=750.0,
        tax_rate=0.0,
        tax_amount=0.0,
        total=750.0,
    )
    first = validate(_extraction(data, "invoice_1004.json"), conn)
    assert first.dedup_status == "new"
    db.record_verdict(conn, "INV-1004", None, "PAID", "invoice_1004.json", 750.0)

    # same invoice number + revision again -> duplicate
    dup = validate(_extraction(data, "invoice_1004_copy.json"), conn)
    assert dup.dedup_status == "duplicate"
    assert any(f.code == "duplicate_invoice" for f in dup.blocking_findings)

    # a revision arriving after payment -> manual review, never auto-pay
    revised = data.model_copy(update={"revision": "R1"})
    rev_report = validate(_extraction(revised, "invoice_1004_revised.json"), conn)
    assert rev_report.dedup_status == "supersedes_paid"
    assert any(f.code == "supersedes_paid_invoice" for f in rev_report.blocking_findings)


def test_full_validation_of_1009_style_mess(conn):
    data = InvoiceData(
        invoice_number="INV-1009",
        vendor_name="",
        invoice_date="2026-01-15",
        due_date=None,
        line_items=[
            LineItem(item="WidgetA", quantity=-5, unit_price=250.0),
            LineItem(item="WidgetB", quantity=2, unit_price=500.0),
        ],
        subtotal=1000.0,
        tax_rate=0.0,
        tax_amount=0.0,
        total=-250.0,
    )
    report = validate(_extraction(data), conn)
    codes = {f.code for f in report.findings}
    assert {"missing_due_date", "subtotal_mismatch", "negative_total", "negative_quantity"} <= codes
    assert report.fraud_level in ("high", "critical")
    assert report.blocking_findings


def test_clean_invoice_passes(conn):
    data = InvoiceData(
        invoice_number="INV-1001",
        vendor_name="Widgets Inc.",
        invoice_date="2026-01-15",
        due_date="2026-02-01",
        line_items=[
            LineItem(item="WidgetA", quantity=10, unit_price=250.0),
            LineItem(item="WidgetB", quantity=5, unit_price=500.0),
        ],
        subtotal=5000.0,
        tax_rate=0.0,
        tax_amount=0.0,
        total=5000.0,
    )
    report = validate(_extraction(data), conn)
    assert report.blocking_findings == []
    assert report.fraud_level == "low"
    assert report.requires_escalation is False

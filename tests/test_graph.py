"""Offline end-to-end pipeline tests with a scripted fake LLM."""

import json

import pytest
from conftest import FakeClient

from invoice_agent import db
from invoice_agent.graph import PipelineDeps, process_invoice
from invoice_agent.models import InvoiceData, LineItem


@pytest.fixture
def deps_factory(tmp_path):
    def make(replies):
        conn = db.get_connection(tmp_path / "test.db")
        return PipelineDeps(
            client=FakeClient(replies),
            model="fake",
            conn=conn,
            audit_path=tmp_path / "audit.jsonl",
        )

    return make


def _clean_extraction_json() -> str:
    return InvoiceData(
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
    ).model_dump_json()


def _vp(decision, reasoning="Evidence is clean."):
    return json.dumps({"decision": decision, "confidence": "high", "reasoning": reasoning})


def _critic(upheld, critique=""):
    return json.dumps({"upheld": upheld, "critique": critique})


def test_end_to_end_paid(deps_factory, capsys):
    deps = deps_factory([_clean_extraction_json(), _vp("approve"), _critic(True)])
    state, timings = process_invoice(deps, "data/invoices/invoice_1001.txt")

    assert state["verdict"] == "PAID"
    assert state["payment"]["status"] == "success"
    assert "Paid 5000.0 to Widgets Inc." in capsys.readouterr().out  # case-specified contract
    assert set(timings) == {"ingestion", "validation", "approval", "payment"}

    # ledger recorded
    entries = db.ledger_entries(deps.conn, "INV-1001")
    assert [e["verdict"] for e in entries] == ["PAID"]

    # audit trail written
    lines = deps.audit_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["verdict"] == "PAID"
    assert record["approval"]["verdict"] == "APPROVED"


def test_end_to_end_rejected(deps_factory):
    fraud_json = InvoiceData(
        invoice_number="INV-1003",
        vendor_name="Fraudster LLC",
        invoice_date="2026-01-20",
        due_date="yesterday",
        line_items=[LineItem(item="FakeItem", quantity=100, unit_price=1000.0)],
        total=100000.0,
        payment_terms="Immediate",
        anomalies=["urgency language"],
    ).model_dump_json()
    deps = deps_factory([fraud_json, _vp("reject", "Fraud signals everywhere."), _critic(True)])
    state, _ = process_invoice(deps, "data/invoices/invoice_1003.txt")

    assert state["verdict"] == "REJECTED"
    assert "payment" not in state
    record = json.loads(deps.audit_path.read_text().splitlines()[0])
    assert record["validation"]["fraud_level"] in ("high", "critical")


def test_superseded_short_circuit(deps_factory):
    deps = deps_factory([])  # no LLM calls allowed
    from invoice_agent.models import ExtractionResult

    extraction = ExtractionResult(
        data=InvoiceData(invoice_number="INV-1004", vendor_name="Precision Parts Ltd."),
        source_file="data/invoices/invoice_1004.json",
        source_format="json",
    )
    state, _ = process_invoice(
        deps,
        "data/invoices/invoice_1004.json",
        extraction=extraction,
        skip=("SUPERSEDED", "Superseded by invoice_1004_revised.json; not paid"),
    )
    assert state["verdict"] == "SUPERSEDED"
    assert deps.client.calls == []
    assert db.ledger_entries(deps.conn, "INV-1004")[0]["verdict"] == "SUPERSEDED"


def test_pipeline_failure_is_isolated(deps_factory):
    deps = deps_factory(["not json " * 5] * 3)  # extraction exhausts retries
    state, _ = process_invoice(deps, "data/invoices/invoice_1001.txt")
    assert state["verdict"] == "FAILED"
    assert "error" in state
    # audit still written for the failure
    record = json.loads(deps.audit_path.read_text().splitlines()[0])
    assert record["verdict"] == "FAILED"

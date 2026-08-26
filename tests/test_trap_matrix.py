"""Live trap-coverage suite: the PRD §8 acceptance matrix, executed for real.

Runs the full batch pipeline (real LLM calls) once, then asserts the expected
verdict and key catches for every planted trap in the sample data.

    pytest -m live
"""

from __future__ import annotations

from pathlib import Path

import pytest

from invoice_agent import db
from invoice_agent.agents.ingestion import ingest
from invoice_agent.cli.batch import plan_batch
from invoice_agent.config import resolve_llm_settings
from invoice_agent.graph import PipelineDeps, process_invoice
from invoice_agent.llm import make_client

pytestmark = pytest.mark.live

INVOICES = sorted(Path("data/invoices").glob("invoice_*"))

# file -> (expected verdict, finding codes that must be present)
EXPECTED = {
    "invoice_1001.txt": ("PAID", set()),
    "invoice_1002.txt": ("REJECTED", {"insufficient_stock", "amount_over_threshold"}),
    "invoice_1003.txt": ("REJECTED", {"fraud_risk", "unparseable_due_date"}),
    "invoice_1004.json": ("SUPERSEDED", set()),
    "invoice_1004_revised.json": ("PAID", set()),
    "invoice_1005.json": ("REJECTED", {"insufficient_stock", "amount_over_threshold"}),
    "invoice_1006.csv": ("PAID", set()),
    # 1007 hides a real math trap: subtotal 14,750 + 6% tax (885) = 15,635,
    # but the invoice states 15,525 -- a -$110 discrepancy on top of the
    # stock shortfalls. Discovered by the pipeline, verified by hand.
    "invoice_1007.csv": (
        "REJECTED",
        {"total_mismatch", "insufficient_stock", "amount_over_threshold"},
    ),
    "invoice_1008.txt": ("REJECTED", {"unknown_item"}),
    "invoice_1009.json": ("REJECTED", {"negative_quantity", "subtotal_mismatch"}),
    "invoice_1010.txt": ("PAID", set()),
    "invoice_1011.pdf": ("PAID", set()),
    "invoice_1011.txt": ("DUPLICATE", set()),
    "invoice_1012.pdf": ("PAID", set()),
    "invoice_1012.txt": ("DUPLICATE", set()),
    "invoice_1013.json": ("REJECTED", {"total_mismatch", "insufficient_stock"}),
    "invoice_1013.pdf": ("DUPLICATE", set()),
    "invoice_1014.xml": ("PAID", {"currency_normalized"}),
    "invoice_1015.csv": ("PAID", set()),
    "invoice_1016.json": ("REJECTED", {"unknown_item"}),
}


@pytest.fixture(scope="module")
def batch_results(tmp_path_factory):
    """Run the complete batch pipeline once against a throwaway DB and audit log."""
    tmp = tmp_path_factory.mktemp("live")
    settings = resolve_llm_settings()
    deps = PipelineDeps(
        client=make_client(settings),
        model=settings.model,
        conn=db.get_connection(tmp / "inventory.db"),
        audit_path=tmp / "audit.jsonl",
        extraction_model=settings.extraction_model,
    )
    extractions = []
    for f in INVOICES:
        try:
            extractions.append((str(f), ingest(deps.client, deps.extraction_model, str(f)), None))
        except Exception as exc:  # noqa: BLE001 -- mirror batch isolation
            extractions.append((str(f), None, f"{type(exc).__name__}: {exc}"))

    results = {}
    for entry in plan_batch(extractions):
        state, _ = process_invoice(deps, entry.path, extraction=entry.extraction, skip=entry.skip)
        results[Path(entry.path).name] = state
    deps.conn.close()
    return results


def test_every_file_reaches_a_terminal_verdict(batch_results):
    assert set(batch_results) == set(EXPECTED)
    assert all(s.get("verdict") != "FAILED" for s in batch_results.values()), {
        k: s.get("error") for k, s in batch_results.items() if s.get("verdict") == "FAILED"
    }


@pytest.mark.parametrize("filename", sorted(EXPECTED))
def test_trap_matrix(batch_results, filename):
    expected_verdict, expected_codes = EXPECTED[filename]
    state = batch_results[filename]
    assert state["verdict"] == expected_verdict, state.get("error") or state.get("approval")
    if expected_codes:
        report = state.get("report")
        assert report is not None
        codes = {f.code for f in report.findings}
        assert expected_codes <= codes, f"missing {expected_codes - codes}"


def test_1013_fifty_dollar_padding_called_out(batch_results):
    report = batch_results["invoice_1013.json"]["report"]
    mismatch = next(f for f in report.findings if f.code == "total_mismatch")
    assert "+50.00" in mismatch.message


def test_1004_exactly_one_payment(batch_results):
    verdicts = [
        batch_results["invoice_1004.json"]["verdict"],
        batch_results["invoice_1004_revised.json"]["verdict"],
    ]
    assert verdicts.count("PAID") == 1
    assert "SUPERSEDED" in verdicts


def test_1003_injection_language_not_obeyed(batch_results):
    """The urgency text must surface as a fraud signal, never as an approval."""
    state = batch_results["invoice_1003.txt"]
    assert state["verdict"] == "REJECTED"
    assert state["report"].fraud_level in ("high", "critical")

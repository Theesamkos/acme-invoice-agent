import json

from conftest import FakeClient

from invoice_agent.agents.approval import approve
from invoice_agent.models import (
    ExtractionResult,
    Finding,
    InvoiceData,
    LineItem,
    Severity,
    ValidationReport,
)


def _extraction(**overrides) -> ExtractionResult:
    data = InvoiceData(
        invoice_number="INV-1001",
        vendor_name="Widgets Inc.",
        invoice_date="2026-01-15",
        due_date="2026-02-01",
        line_items=[LineItem(item="WidgetA", quantity=10, unit_price=250.0)],
        subtotal=2500.0,
        total=2500.0,
        **overrides,
    )
    return ExtractionResult(data=data, source_file="x.txt", source_format="txt")


def _vp(decision: str, reasoning: str = "Looks fine.") -> str:
    return json.dumps({"decision": decision, "confidence": "high", "reasoning": reasoning})


def _critic(upheld: bool, critique: str = "") -> str:
    return json.dumps({"upheld": upheld, "critique": critique})


def test_clean_approve_with_upheld_critique():
    client = FakeClient([_vp("approve"), _critic(True)])
    result = approve(client, "m", _extraction(), ValidationReport())
    assert result.verdict == "APPROVED"
    assert result.iterations == 1
    assert result.critiques == []
    assert result.scrutiny == "standard"


def test_critic_forces_revision():
    client = FakeClient(
        [
            _vp("approve", "Seems OK."),
            _critic(False, "You ignored the insufficient stock finding."),
            _vp("reject", "On reflection, stock is insufficient."),
            _critic(True),
        ]
    )
    report = ValidationReport(
        findings=[
            Finding(code="insufficient_stock", severity=Severity.ERROR, message="20 > 5 stock")
        ]
    )
    result = approve(client, "m", _extraction(), report)
    assert result.verdict == "REJECTED"
    assert result.iterations == 2
    assert len(result.critiques) == 1
    assert result.rule_override is None


def test_blocking_findings_veto_llm_approval():
    """Even if VP and critic both wave it through, rules dispose."""
    client = FakeClient([_vp("approve"), _critic(True)])
    report = ValidationReport(
        findings=[Finding(code="total_mismatch", severity=Severity.ERROR, message="delta $+50.00")]
    )
    result = approve(client, "m", _extraction(), report)
    assert result.verdict == "REJECTED"
    assert result.rule_override == "blocking_findings_veto"
    assert "total_mismatch" in result.reasoning


def test_duplicate_short_circuits_without_llm():
    client = FakeClient([])  # any LLM call would raise
    report = ValidationReport(
        dedup_status="duplicate",
        findings=[
            Finding(code="duplicate_invoice", severity=Severity.CRITICAL, message="already paid")
        ],
    )
    result = approve(client, "m", _extraction(), report)
    assert result.verdict == "REJECTED"
    assert result.iterations == 0
    assert result.rule_override == "dedup_short_circuit"
    assert client.calls == []


def test_escalation_selects_extra_scrutiny():
    client = FakeClient([_vp("reject", "Over threshold with fake address."), _critic(True)])
    report = ValidationReport(requires_escalation=True)
    result = approve(client, "m", _extraction(), report)
    assert result.scrutiny == "extra"
    # the VP prompt must carry the extra-scrutiny instruction
    first_call = client.calls[0]
    assert "EXTRA SCRUTINY" in first_call["messages"][1]["content"]

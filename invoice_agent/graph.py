"""LangGraph orchestration: ingestion -> validation -> approval -> payment/rejection.

The graph is pure control flow; each node delegates to its agent module. A
runner (`process_invoice`) wraps the graph with timing capture, ledger
recording, and audit logging so every terminal verdict leaves a paper trail.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from openai import OpenAI

from invoice_agent import audit, db
from invoice_agent.agents.approval import ApprovalResult, approve
from invoice_agent.agents.ingestion import ingest
from invoice_agent.agents.payment import execute_payment
from invoice_agent.agents.validation import validate
from invoice_agent.models import ExtractionResult, ValidationReport


class PipelineState(TypedDict, total=False):
    invoice_path: str
    extraction: ExtractionResult
    report: ValidationReport
    approval: ApprovalResult
    verdict: str  # PAID | REJECTED | SUPERSEDED | FAILED
    payment: dict
    error: str


@dataclass
class PipelineDeps:
    client: OpenAI
    model: str
    conn: sqlite3.Connection
    audit_path: Path = field(default_factory=lambda: audit.AUDIT_PATH)
    extraction_model: str = ""  # defaults to `model`

    def __post_init__(self):
        self.extraction_model = self.extraction_model or self.model


def build_graph(deps: PipelineDeps):
    def ingestion_node(state: PipelineState) -> PipelineState:
        if "extraction" in state:  # batch mode pre-extracts during the dedup scan
            return {}
        return {"extraction": ingest(deps.client, deps.extraction_model, state["invoice_path"])}

    def validation_node(state: PipelineState) -> PipelineState:
        return {"report": validate(state["extraction"], deps.conn)}

    def approval_node(state: PipelineState) -> PipelineState:
        return {"approval": approve(deps.client, deps.model, state["extraction"], state["report"])}

    def route_after_approval(state: PipelineState) -> str:
        return "payment" if state["approval"].verdict == "APPROVED" else "rejection"

    def payment_node(state: PipelineState) -> PipelineState:
        report = state["report"]
        data = state["extraction"].data
        amount = report.total_usd if report.total_usd is not None else data.total
        return {"verdict": "PAID", "payment": execute_payment(data.vendor_name, amount)}

    def rejection_node(state: PipelineState) -> PipelineState:
        return {"verdict": "REJECTED"}

    graph = StateGraph(PipelineState)
    graph.add_node("ingestion", ingestion_node)
    graph.add_node("validation", validation_node)
    graph.add_node("approval", approval_node)
    graph.add_node("payment", payment_node)
    graph.add_node("rejection", rejection_node)
    graph.add_edge(START, "ingestion")
    graph.add_edge("ingestion", "validation")
    graph.add_edge("validation", "approval")
    graph.add_conditional_edges(
        "approval", route_after_approval, {"payment": "payment", "rejection": "rejection"}
    )
    graph.add_edge("payment", END)
    graph.add_edge("rejection", END)
    return graph.compile()


def _audit_record(state: PipelineState, timings: dict[str, float]) -> dict:
    extraction = state.get("extraction")
    report = state.get("report")
    approval = state.get("approval")
    return {
        "source_file": state.get("invoice_path"),
        "verdict": state.get("verdict"),
        "error": state.get("error"),
        "invoice": extraction.data.model_dump() if extraction else None,
        "extraction_attempts": extraction.attempts if extraction else None,
        "validation": report.model_dump() if report else None,
        "approval": approval.model_dump() if approval else None,
        "payment": state.get("payment"),
        "stage_seconds": {k: round(v, 2) for k, v in timings.items()},
    }


def process_invoice(
    deps: PipelineDeps,
    invoice_path: str,
    extraction: ExtractionResult | None = None,
    skip: tuple[str, str] | None = None,
    on_stage=None,
) -> tuple[PipelineState, dict[str, float]]:
    """Run one invoice to a terminal verdict; record ledger + audit.

    `skip=(verdict, reason)` short-circuits the pipeline -- used by the batch
    dedup planner for SUPERSEDED revisions and cross-format DUPLICATE copies,
    which must never be paid. `on_stage(name, state)` is an optional UI
    callback fired after each node completes.
    """
    timings: dict[str, float] = {}
    state: PipelineState = {"invoice_path": invoice_path}
    if extraction is not None:
        state["extraction"] = extraction

    if skip is not None:
        state["verdict"], state["error"] = skip
        if on_stage:
            on_stage("skipped", state, 0.0)
    else:
        compiled = build_graph(deps)
        try:
            start = time.perf_counter()
            for step in compiled.stream(state, stream_mode="updates"):
                for node_name, update in step.items():
                    state.update(update)
                    timings[node_name] = time.perf_counter() - start
                    start = time.perf_counter()
                    if on_stage:
                        on_stage(node_name, state, timings[node_name])
        except Exception as exc:  # noqa: BLE001 -- batch isolation: any failure -> FAILED verdict
            state["verdict"] = "FAILED"
            state["error"] = f"{type(exc).__name__}: {exc}"
            if on_stage:
                on_stage("failed", state, 0.0)

    data = state["extraction"].data if "extraction" in state else None
    report = state.get("report")
    terminal = ("PAID", "REJECTED", "SUPERSEDED", "DUPLICATE")
    if data and data.invoice_number and state.get("verdict") in terminal:
        db.record_verdict(
            deps.conn,
            data.invoice_number,
            data.revision,
            state["verdict"],
            invoice_path,
            report.total_usd if report else None,
        )
    audit.write_audit(_audit_record(state, timings), deps.audit_path)
    return state, timings

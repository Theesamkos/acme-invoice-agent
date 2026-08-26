"""Batch dedup planner.

Before any payment decision, all files are extracted and grouped by invoice
number. Within a group:

- the highest revision is the payable candidate (later revisions supersede)
- earlier revisions are SUPERSEDED
- same-revision copies in other formats (e.g. invoice_1013.json vs .pdf) are
  DUPLICATEs of the first file

This guarantees an invoice number can be paid at most once per batch, no matter
how many files reference it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from invoice_agent.models import ExtractionResult


@dataclass
class PlanEntry:
    path: str
    extraction: ExtractionResult | None
    skip: tuple[str, str] | None = None  # (verdict, reason) for non-candidates
    extraction_error: str | None = None


def plan_batch(
    extractions: list[tuple[str, ExtractionResult | None, str | None]],
) -> list[PlanEntry]:
    """Input: (path, extraction-or-None, error-or-None) per file, in processing order."""
    entries = [
        PlanEntry(path=path, extraction=extraction, extraction_error=error)
        for path, extraction, error in extractions
    ]

    groups: dict[str, list[PlanEntry]] = defaultdict(list)
    for entry in entries:
        number = entry.extraction.data.invoice_number if entry.extraction else None
        # unextractable or unnumbered invoices are never dedup-grouped
        groups[number or f"__unique__{entry.path}"].append(entry)

    for group in groups.values():
        if len(group) == 1:
            continue
        best_revision = max(e.extraction.data.revision or "" for e in group)
        # first file (sorted-path order) at the best revision is the payable candidate
        candidate = next(e for e in group if (e.extraction.data.revision or "") == best_revision)
        candidate_name = Path(candidate.path).name
        for entry in group:
            if entry is candidate:
                continue
            if (entry.extraction.data.revision or "") == best_revision:
                entry.skip = (
                    "DUPLICATE",
                    f"Same invoice and revision as {candidate_name}; not paid twice",
                )
            else:
                entry.skip = (
                    "SUPERSEDED",
                    f"Superseded by revision {best_revision!r} ({candidate_name}); not paid",
                )
    return entries

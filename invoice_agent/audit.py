"""Append-only JSONL audit trail: one record per terminal verdict."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

AUDIT_PATH = Path("logs/audit.jsonl")


def write_audit(record: dict, path: str | Path = AUDIT_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(UTC).isoformat(), **record}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

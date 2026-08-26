"""SQLite bootstrap: inventory (per the case README's required setup) and the
payment ledger used for cross-run duplicate detection.

The database is a runtime artifact -- auto-created and seeded on first use so a
fresh clone needs zero manual setup.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path("inventory.db")

INVENTORY_SEED = {"WidgetA": 15, "WidgetB": 10, "GadgetX": 5, "FakeItem": 0}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (item TEXT PRIMARY KEY, stock INTEGER);
CREATE TABLE IF NOT EXISTS ledger (
    invoice_number TEXT NOT NULL,
    revision TEXT,
    verdict TEXT NOT NULL,
    source_file TEXT NOT NULL,
    total_usd REAL,
    processed_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (invoice_number, revision, source_file)
);
"""


def get_connection(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    """Open (and if needed create + seed) the database."""
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO inventory (item, stock) VALUES (?, ?)", INVENTORY_SEED.items()
        )
        conn.commit()
    return conn


def fetch_inventory(conn: sqlite3.Connection) -> dict[str, int]:
    return dict(conn.execute("SELECT item, stock FROM inventory").fetchall())


def record_verdict(
    conn: sqlite3.Connection,
    invoice_number: str,
    revision: str | None,
    verdict: str,
    source_file: str,
    total_usd: float | None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ledger (invoice_number, revision, verdict, source_file, total_usd)"
        " VALUES (?, ?, ?, ?, ?)",
        (invoice_number, revision or "", verdict, source_file, total_usd),
    )
    conn.commit()


def ledger_entries(conn: sqlite3.Connection, invoice_number: str) -> list[dict]:
    rows = conn.execute(
        "SELECT invoice_number, revision, verdict, source_file, total_usd, processed_at"
        " FROM ledger WHERE invoice_number = ? ORDER BY processed_at",
        (invoice_number,),
    ).fetchall()
    keys = ["invoice_number", "revision", "verdict", "source_file", "total_usd", "processed_at"]
    return [dict(zip(keys, row, strict=True)) for row in rows]

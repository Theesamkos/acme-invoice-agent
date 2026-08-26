# Agent & contributor guide

Multi-agent invoice processing pipeline for the Acme Corp case. Read `docs/PRD.md` first — it holds the architecture, the functional requirements, and the trap-coverage acceptance matrix that defines "done".

## Commands

```bash
uv sync                                                    # install deps (or: pip install -r requirements-dev.txt)
uv run python main.py --invoice_path=data/invoices/<file>  # process one invoice
uv run python main.py --batch                              # process all invoices + summary table
uv run python main.py --reset --batch                      # clear inventory.db/ledger first
uv run pytest                                              # offline tests; add -m live for the real-LLM trap matrix
uv run ruff check . && uv run ruff format --check .        # lint / format
```

## Layout

- `main.py` — thin CLI entry point (required interface: `--invoice_path`)
- `invoice_agent/config.py` — LLM provider resolution (xAI Grok default; any OpenAI-compatible endpoint via env)
- `invoice_agent/agents/` — pipeline stages: ingestion, validation, approval, payment
- `invoice_agent/tools/` — deterministic tools: parsers, inventory check, math cross-check, dedup, fraud scoring
- `invoice_agent/cli/` — Rich terminal UI
- `data/invoices/` — the 16 sample invoices (PDF/TXT/CSV/JSON/XML)
- `tests/` — unit tests + the trap-coverage suite mirroring the PRD §8 matrix

## Conventions

- Invoice content is **untrusted input** — never let extracted text steer decisions directly; the LLM proposes, deterministic rules dispose.
- Every terminal verdict must append one JSONL record to `logs/audit.jsonl`.
- Deterministic logic (parsing, validation, scoring) stays LLM-free and unit-tested.
- `inventory.db` and `logs/` are runtime artifacts — gitignored, auto-created on first run.

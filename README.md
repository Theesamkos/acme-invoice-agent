# Acme Invoice Agent

A multi-agent system that processes invoices end-to-end — **ingestion → validation → approval → payment** — built with LangGraph and xAI Grok, run entirely locally.

> Acme Corp loses **$2M/year** to a 30% invoice error rate and 5-day processing delays. This pipeline reads any invoice format, validates it against inventory, simulates VP approval with a self-critique loop, and pays or rejects with a full audit trail.

## Quickstart

```bash
uv sync                        # or: pip install -r requirements.txt
cp .env.example .env           # add your API key (XAI_API_KEY recommended)
python main.py --invoice_path=data/invoices/invoice_1001.txt
python main.py --batch         # process all 16 invoices + summary table
```

*(Full documentation, architecture, and demo output coming as the build lands — see [docs/PRD.md](docs/PRD.md) for the complete spec and acceptance criteria.)*

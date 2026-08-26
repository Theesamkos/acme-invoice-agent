# PRD — Acme Invoice Agent

**Owner:** Sam Orth · **Status:** Draft v1 · **Date:** 2026-08-25
**Deliverable:** Public GitHub repo — a locally-runnable multi-agent invoice processing system (Galatiq technical assessment).

---

## 1. Problem

Acme Corp's accounts-payable process is manual and failing at scale:

- **$2M/year** lost to erroneous and duplicate payments
- **30% error rate** across processed invoices
- **5-day average delay** from receipt to payment decision

Invoices arrive in inconsistent formats (PDF, TXT, CSV, JSON, XML), with real-world defects: OCR artifacts, typos, duplicate submissions, math errors, foreign currency, and outright fraud attempts. A human-only pipeline can't reliably catch these; a naive automation pipeline pays them.

## 2. Product vision

An agentic AP pipeline that behaves like a **skeptical, auditable AP team**: an ingestion clerk that reads anything, a validator that trusts nothing, a VP who escalates and self-critiques, and a payments desk that leaves a paper trail. Every decision is explainable, logged, and reproducible.

## 3. Goals & success metrics

| Goal | Metric |
|---|---|
| Process every sample invoice end-to-end | 16/16 invoices reach a terminal verdict with zero unhandled exceptions |
| Catch every planted trap | 100% of the known-trap table detected and surfaced in output (see §8) |
| Never pay twice | Invoice 1004 duplicate is superseded, not double-paid |
| Catch silent math fraud | Invoice 1013's +$50 grand-total padding is flagged |
| Explainability | Every rejection includes machine- and human-readable reasoning |
| Zero-friction evaluation | `python main.py --invoice_path=data/invoices/<file>` works from a fresh clone with one env var |

## 4. Non-goals (ruthless scope)

- No web UI, no deployment, no cloud infra — rich CLI only
- No real payment rails — payment is an explicit mock
- No OCR of scanned images — PDFs are text-extractable
- No live FX rates — EUR normalized at a fixed, documented rate
- No database beyond SQLite `inventory.db` (not shipped with the case — we create and seed it automatically on first run, per the case README's required-setup section)
- No fine-tuning / embeddings / RAG — structured extraction + rules + reflection is the right tool

## 5. Users

1. **AP operator (primary persona)** — runs the CLI per-invoice or in batch; needs at-a-glance verdicts and drill-down reasoning.
2. **Finance leadership** — consumes the audit log (JSONL) and batch summary; cares about fraud catches and dollars protected.
3. **Galatiq evaluator (the real user)** — clones the repo cold; needs instant setup, a legible README, and visible proof the traps were caught.

## 6. System architecture

**Stack:** Python 3.12+, LangGraph (orchestration), OpenAI SDK with configurable `base_url` (provider-agnostic: OpenAI or xAI/Grok), Pydantic (structured state), Rich (CLI), SQLite (inventory).

A LangGraph `StateGraph` over a typed `InvoiceState`:

```
                    ┌────────────┐
  file ──────────▶ │ INGESTION  │  multi-format parsing → structured InvoiceData
                    └─────┬──────┘  (LLM extraction w/ injection-hardened prompt)
                          ▼
                    ┌────────────┐
                    │ VALIDATION │  deterministic tools: inventory check (fuzzy match),
                    └─────┬──────┘  math recomputation, dedup registry, fraud scoring
                          ▼
                    ┌────────────┐   >$10K or fraud signals → extra scrutiny
                    │  APPROVAL  │◀─┐ reflection loop: VP verdict → critic agent
                    └─────┬──────┘──┘ challenges it → revise (max 2 iterations)
                          ▼
                ┌─────────┴─────────┐
          ┌───────────┐       ┌───────────┐
          │  PAYMENT  │       │ REJECTION │  both write to audit log (JSONL)
          │  (mock)   │       │ + reasons │
          └───────────┘       └───────────┘
```

**Agent responsibilities:**

- **Ingestion agent** — format detection (extension + content sniffing), format-specific pre-parsers (CSV/JSON/XML deterministic, PDF via pypdf text extraction), then LLM structured extraction into `InvoiceData` (vendor, invoice #, dates, currency, line items, stated totals). Invoice text is treated as **untrusted input**: the extraction prompt is hardened against instructions embedded in invoice content (e.g. 1003's "URGENT pay immediately!!!").
- **Validation agent** — deterministic tool calls, no LLM trust required:
  - *Inventory check* against `inventory.db` with normalized + fuzzy item matching (handles "Widget A" / `WidgetA`, OCR artifacts)
  - *Math cross-check* — recompute line-item sums vs stated subtotal/total; flag discrepancies (catches 1009, 1013)
  - *Dedup registry* — invoice-number ledger; a revised invoice supersedes its original (catches 1004)
  - *Fraud scoring* — weighted signals: urgency language, past-due/invalid dates, unknown vendor, suspicious address, negative quantities, unknown items
  - *Currency normalization* — EUR→USD at fixed documented rate (1014)
- **Approval agent (VP simulation)** — rule-gated: >$10K or elevated fraud score triggers extra scrutiny. LLM produces a verdict + reasoning; a **critic pass** challenges the verdict against the validation evidence ("what did you miss?"); VP revises or confirms. Bounded loop (max 2 reflections) → deterministic terminal verdict.
- **Payment agent** — mock: prints confirmation, returns success payload. Rejections routed to a rejection handler that logs structured reasoning.

**State & observability:** single Pydantic `InvoiceState` flows through the graph; every node appends to a step-trace; every run appends one JSONL record to `logs/audit.jsonl` (input hash, extracted data, validation findings, fraud score, verdict, reasoning, timings).

## 7. Functional requirements

| ID | Requirement |
|---|---|
| F1 | `python main.py --invoice_path=data/invoices/<file>` processes one invoice end-to-end |
| F2 | `python main.py --batch` processes all invoices in `data/invoices/` and renders a summary table |
| F3 | Supported formats: PDF, TXT, CSV (row + key-value styles), JSON, XML |
| F4 | Extraction returns validated structured output (Pydantic); malformed LLM output retries once, then fails gracefully |
| F5 | Inventory validation via SQLite with fuzzy item-name matching; unknown items and insufficient stock are distinct findings |
| F6 | Math cross-check flags any recomputed-vs-stated total discrepancy with the delta amount |
| F7 | Duplicate invoice numbers: later/revised version supersedes; original marked SUPERSEDED, never paid |
| F8 | Approval: >$10,000 triggers extra-scrutiny path; reflection/critique loop bounded at 2 iterations |
| F9 | Verdicts: `PAID`, `REJECTED`, `SUPERSEDED` (+ `ESCALATED` as a transient state shown in the trace) |
| F10 | Every terminal verdict logged to `logs/audit.jsonl` with full reasoning chain |
| F11 | LLM config via env: `LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL` (auto-detects `XAI_API_KEY` → xAI, or `OPENAI_API_KEY` → OpenAI); no key → clear actionable error |
| F13 | `inventory.db` auto-created and seeded on first run if absent (WidgetA 15, WidgetB 10, GadgetX 5, FakeItem 0) |
| F14 | Payment mock matches the case-specified contract: `mock_payment(vendor, amount)` → prints `Paid {amount} to {vendor}`, returns `{"status": "success"}` |
| F12 | Any single-invoice failure in batch mode is isolated — the batch completes |

## 8. Acceptance criteria — trap coverage matrix

The system is **done** when batch mode produces these outcomes:

| Invoice | Planted trap | Expected outcome |
|---|---|---|
| 1001, 1011, 1015 | clean | **PAID** |
| 1002 | typos; 20× GadgetX vs 5 stock; due = invoice date; $15,000 > $10K | **REJECTED** — insufficient stock; escalated; date anomaly flagged |
| 1003 | fraud vendor, FakeItem, past due, urgency language | **REJECTED** — fraud score critical; injection attempt neutralized |
| 1004 | duplicate | **SUPERSEDED** — no payment |
| 1004_revised | replacement | **PAID** (or rejected on merits) — exactly one payment for invoice 1004 |
| 1005 | $15,225 > $10K; fake address; GadgetX 8 vs 5 stock | **ESCALATED** → rejected on stock + address + amount signals |
| 1006 | key-value CSV, repeated keys | parsed correctly → verdict on merits |
| 1007 | multi-row CSV; $15,525 > $10K; stock shortfalls (WidgetA 20 vs 15, WidgetB 15 vs 10); hidden −$110 total error (14,750 + 885 tax ≠ 15,525) | **REJECTED** — escalated; stock + math discrepancy both cited |
| 1008 | email body; unknown items | **REJECTED** — items not in inventory |
| 1009 | negative qty, blank vendor, −$250 total, bad subtotal | **REJECTED** — multiple integrity failures enumerated |
| 1010 | same item two prices; shipping line | **PAID** — dual pricing noted; shipping excluded from inventory check |
| 1012 | OCR artifacts (`$3,500.O0`, `2O26`) | normalized + fuzzy-matched → verdict on merits |
| 1013 | grand total +$50 vs items+tax; 22× WidgetA vs 15 stock | **REJECTED** — both the $50 padding *and* stock shortfall called out |
| 1014 | EUR, different unit prices | normalized to USD → verdict on merits |
| 1016 | unknown WidgetC | **REJECTED** — unknown item |

*(1004-dedup and the 1013 +$50 catch are the headline demo moments — they go in the README.)*

## 9. CLI / UX specification

The CLI is the product's face. Principles: **glanceable verdicts, progressive disclosure, zero wall-of-text**.

- **Per-invoice run:** stage-by-stage live progress (Ingestion → Validation → Approval → Payment) with per-stage status icons and timing; a findings panel (fraud signals, math deltas, stock issues); a color-coded verdict banner — green `PAID`, red `REJECTED`, yellow `ESCALATED`, dim `SUPERSEDED` — with the reasoning chain beneath.
- **Batch mode:** compact per-invoice progress, then a final Rich table: invoice · vendor · amount · verdict · key finding. Footer totals: paid count/$, rejected count/$, **dollars protected** (rejected + superseded value) — the business-impact number.
- **`--verbose`** flag reveals the full agent trace (LLM reasoning, reflection iterations, tool calls).
- Errors are designed: missing key, missing file, unparseable invoice each produce a specific, styled, actionable message.

## 10. Security posture

- Invoice content is **untrusted input**: extraction prompts delimit invoice text and explicitly instruct the model to ignore embedded instructions; approval decisions never consume raw invoice text, only validated structured data.
- Fraud scoring is deterministic and rule-based — the LLM proposes, rules dispose.
- No secrets in repo; `.env` gitignored; README documents required env vars.

## 11. Testing strategy

- **Unit:** parsers per format, fuzzy matcher, math cross-check, dedup registry, fraud scorer — pure functions, no LLM.
- **Integration:** the trap matrix (§8) as a parametrized test — each invoice asserts its expected verdict and key finding. LLM calls mocked/recorded for deterministic CI; a `--live` marker runs against the real API.
- **Smoke:** fresh-clone script — install → single run → batch run.

## 12. Delivery plan (commit-by-commit)

Each milestone is a clean, self-contained push:

1. `docs: PRD` — this document
2. `chore: project scaffolding` — pyproject (uv), package layout, config, README skeleton
3. `feat: ingestion` — parsers + LLM extraction + structured output
4. `feat: validation` — inventory/fuzzy/math/dedup/fraud tools
5. `feat: approval + reflection loop` — VP agent, critic, escalation gate
6. `feat: payment + audit log` — mock payment, rejection handler, JSONL
7. `feat: rich CLI + batch mode` — UX layer, summary table
8. `test: trap coverage suite`
9. `docs: README + demo output` — business framing, trap-catch screenshots

## 13. Risks & mitigations

| Risk | Mitigation |
|---|---|
| LLM extraction non-determinism breaks verdicts | Structured output + Pydantic validation + retry; deterministic validation layer makes the *decision*, not the LLM |
| Evaluator runs without an API key | Fail fast with a clear message; README first section is setup; consider recorded fixtures for offline demo |
| PDF text extraction quirks | pypdf primary; TXT twins of PDFs exist in sample data as reference during dev (never as runtime fallback) |
| Over-engineering vs "shipping mindset" | Scope locked to §4 non-goals; features beyond §8 matrix only after all traps pass |

## 14. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| D1 | Repo stays private during dev, flipped public at submission | Yes — flip on final push |
| D2 | EUR→USD fixed rate | 1 EUR = 1.10 USD, documented in README |
| D3 | 1005 terminal verdict (escalated + fake address) | REJECT — address risk + >$10K; documented as policy |
| D4 | Default model | **xAI Grok** — the case README's preferred reasoning engine, and Sam's available key. Reached via the OpenAI SDK pointed at xAI's OpenAI-compatible endpoint (`https://api.x.ai/v1`), not the README's outdated `from xai import Grok` snippet. OpenAI works as a drop-in fallback via env vars |

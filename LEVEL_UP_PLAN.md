# LEVEL_UP_PLAN.md

Finalized roadmap for the DeepAgent Finance Tracker, written from an AI Agent researcher's lens after reviewing every project markdown file, the current source, and the recently-shipped Antigravity hardening plan.

---

## Context

**Design philosophy (unchanged, and worth preserving):**
LLMs act only as constrained sub-agents (`parser_agent`, `categorizer`). Python owns control flow, dedup (`processed_statements` SHA-256), numerical integrity (verbatim substring verification in `verify_statement_tx_verbatim`), and DB writes. This is a *good* architecture; the work below builds on it without changing that core.

**Recently shipped (Antigravity plan, verified):**
- Batch categorization — one `categorize_batch()` LLM call per page instead of one per debit (`categorizer.py`, `orchestrator.py`). Real speed-up.
- Mathematical balance audit — `expected = prev ± amount`, tolerance `0.01`, flags with an exact-reason string (`orchestrator.py:229-234`).
- `BatchCategoryResults` / `CategorizedItem` schemas added to `models.py`.
- 4 unit tests added in `tests/test_batch_categorizer.py`.

**Shipped but partial / needs cleanup:**
- "Dynamic taxonomy" — the Antigravity plan promised a Pydantic factory bound to `config.CATEGORIES`. What actually shipped is `CategoryResult.category: str` (permissive) plus post-hoc string snapping in `categorizer.py:64-67,94-99`. This is a workaround, not real dynamic typing.
- Balance-audit and batch categorizer have no direct unit tests — the new tests only exercise pre-existing helpers and Pydantic model instantiation.

**Still fundamentally missing:**
- No ground-truth benchmark → cannot honestly measure any change.
- No self-correction on verbatim failure → wasted signal (parser fails silently, row goes to `pending_review` with `amount=0.0`).
- Hard binary `>= 0.7` confidence cutoff (`orchestrator.py:247`) → coarse HITL routing.
- No cross-month memory → `payee_category_map` is exact-string only; near-duplicate memos re-hit the LLM every month.
- No structured logging (confirmed: no stdlib `logging`, only `print()`) → cannot trace a specific transaction's reasoning trail.

The plan below is ordered by leverage: fix the loose ends first, then build the foundation (eval + tracing), then the higher-leverage agentic upgrades.

---

## Phase 0 — Harden what just shipped [COMPLETED & VERIFIED]

Small, contained cleanups on the batch/audit code. Completed and verified with 25 passing unit tests.

### 0.1 Real dynamic taxonomy via Pydantic factory

- Add `create_category_result_model(categories: list[str])` in `finance_tracker/models.py` returning a Pydantic model with `category: Literal[*categories]`.
- In `categorizer.py`, call the factory at `_get_agent()` / `_get_batch_agent()` startup, passing `CATEGORIES`.
- Delete the post-hoc string-snapping fallback (`categorizer.py:64-67,94-99`) — no longer needed once the schema constrains the model.
- **Why:** Restores schema-level type safety. Right now an out-of-taxonomy LLM response is indistinguishable downstream from a genuinely uncertain in-taxonomy one, which will contaminate Phase 1 metrics.

### 0.2 Extract testable pure helpers

- Extract `_audit_balance(prev: float | None, amount: float, tx_type: str, balance: float | None) -> str | None` from `orchestrator.py:229-234` — returns the review reason string or `None`.
- Extract `_resolve_category(item, batch_result) -> tuple[str, str | None]` from the category-resolution block in `orchestrator.py:239-255`.
- **Why:** Neither piece is currently unit-testable in isolation.

### 0.3 Fix the ordering assumption in balance audit

- Sort `outcome.verified_transactions` by `raw_date` + `raw_time` before the audit loop, **or** add an explicit assertion + comment documenting that `parse_statement_page` returns transactions in statement order.
- **Why:** If the parser ever reorders (prompt tweak, model change), every row after the first mis-ordered one silently floods `pending_review` as a false-positive.

### 0.4 Fill the test gap the Antigravity plan skipped

In `tests/test_batch_categorizer.py`:
- Test `categorize_batch()` with a mocked agent — assert index preservation, missing-index handling, unknown-category handling (after 0.1 lands, unknown categories should never appear).
- Test `_audit_balance()` (from 0.2) — pass, fail-debit, fail-credit, `None`-prev-balance, `None`-balance cases.

**Verify Phase 0:** `PYTHONPATH=. pytest` still green; new tests cover `categorize_batch` and `_audit_balance` directly.

---

## Phase 1 — Evaluation harness (foundation for everything after) [COMPLETED & VERIFIED]

**Problem.** Docs claim verbatim verification prevents hallucination, but nothing measures it. There is no F1 on categorization, no track of token cost per statement, no baseline to regress against.

**Deliverables.**
- New `eval/` directory:
  - `eval/fixtures/` — 20–50 anonymized statement pages with hand-annotated ground-truth JSON (transactions + categories).
  - `eval/harness.py` — loads fixtures, runs `parser_agent` + `categorizer`, computes:
    - extraction precision / recall / F1
    - verbatim-verification hit rate
    - categorization precision / recall per category
    - hallucination count (LLM emitted a value that fails verbatim)
    - balance-audit true-positive / false-positive rate (needs synthetically-corrupted fixtures)
    - mean tokens / statement, wall-clock / statement
  - `eval/report.py` — writes `eval/runs/<timestamp>.md` and appends a row to `eval/history.csv` so regressions across prompt or model changes are visible.
- Wire into `pytest` as an opt-in marker (`pytest -m eval`) so CI stays fast.

**Verify.** `pytest -m eval` runs deterministically (identical config → identical numbers) and produces a report.

---

## Phase 2 — Structured tracing + token accounting [COMPLETED & VERIFIED]

**Problem.** No stdlib `logging` in the codebase. `print()` only. When a transaction ends up wrong, there is no way to trace *why*.

**Deliverables.**
- New `finance_tracker/tracing.py` — structured logger writing JSON lines to `logs/run-<timestamp>.jsonl` and human-friendly text to stdout at INFO.
- Replace `print()` calls in `orchestrator.py`, `parser_agent.py`, `categorizer.py`, `cli.py`.
- Per-transaction log fields: statement hash, page number, raw agent output, verbatim check result, confidence, tier (once Phase 4 lands), tokens in/out, wall-clock, audit result.
- New CLI: `python main.py trace <transaction_id>` — reads the JSONL logs and pretty-prints the full trail for a specific DB row.

**Verify.** After an ingest, `python main.py trace <id>` prints a coherent trail. Token totals in the log match what the LLM provider reports.

*Do this early: it unblocks debugging every phase after.*

---

## Phase 3 — Self-critique loop in `parser_agent.py`

**Problem.** When `verify_statement_tx_verbatim` fails, the transaction is written with `amount=0.0` and dumped to `pending_review`. The parser never learns from the failure inside the same run. This is the single biggest wasted signal in the pipeline. The balance audit shipped in the Antigravity plan is a *deterministic* instance of this pattern; Phase 3 generalizes it to *agentic* self-correction.

**Deliverables.**
- Wrap the current one-shot parser call in a bounded retry loop (**max 2 retries**).
- On verbatim failure, re-invoke the parser with a structured critique message: which field failed (`raw_amount` / `raw_date` / `raw_balance`), the substring searched, and the surrounding page context. Ask the agent to re-emit **only the failed transactions**.
- On balance-audit failure, similarly hand the parser: "row N expected balance X, extracted Y — re-read the balance column."
- Track retry outcomes in a new column `parser_retry_count` on the transaction record (schema migration in `db.py`).
- Keep the existing verbatim gate and balance audit as the final authority — self-critique is best-effort; if it still fails after retries, fall through to `pending_review` as today.

**Files.** `finance_tracker/agents/parser_agent.py`, `finance_tracker/agents/orchestrator.py`, `finance_tracker/db.py`.

**Verify.** Add eval fixtures crafted to trigger verbatim failure and balance-audit failure on first pass; assert self-critique recovers them. Eval harness reports the hallucination-rate delta.

---

## Phase 4 — Confidence-driven routing (replace the hard 0.7 cutoff)

**Problem.** `orchestrator.py:247` uses a binary `>= 0.7` threshold. A transaction at 0.69 goes to human review; one at 0.71 is auto-accepted. Continuous scoring with tiered routing meaningfully halves HITL friction without dropping accuracy — provided the eval harness (Phase 1) is in place to prove it.

**Deliverables.**
- Three tiers in `orchestrator.py`:
  - **High (≥ 0.85)** — auto-accept.
  - **Medium (0.6 – 0.85)** — auto-accept **and** flag `needs_spot_check` (new column). Surfaced only via `python main.py review --spot-check`.
  - **Low (< 0.6)** — existing `pending_review` behavior.
- Thresholds live in a new `finance_tracker/config.py` section (single source of truth, tunable per bank later).
- When a user overrides a mid-tier prediction, log the miss so thresholds can be re-tuned against the eval harness.

**Files.** `finance_tracker/agents/orchestrator.py`, `finance_tracker/cli.py` (new `--spot-check` flag on `review`), `finance_tracker/db.py` (`needs_spot_check` column), `finance_tracker/config.py`.

**Verify.** Eval harness reports `%auto_accepted / %spot_check / %pending_review` and per-tier categorization precision. Medium tier should hold above a defined precision floor (e.g., 0.9).

---

## Phase 5 — Cross-month memory / lightweight RAG for the categorizer

**Problem.** Each statement is processed in isolation. `payee_category_map` does flat exact-string matching, so `"STARBUCKS #4471"` and `"STARBUCKS 4471 BKK"` miss and re-hit the LLM. Categorization is inherently temporal — this is free accuracy and free tokens on the table.

**Deliverables.**
- Add a `payee_embedding` blob column to `payee_category_map` — use a small local sentence-transformer, or the same provider's embedding API if the project already depends on it.
- In `categorizer.py`, replace the current lookup with a tiered chain (before falling through to the LLM):
  1. Exact match.
  2. Embedding-similarity match (cosine ≥ 0.9).
  3. `categorize_batch()` (the batch call already shipped in the Antigravity plan).
- Log which tier resolved each transaction (uses Phase 2 tracing).
- Extend the existing cron (per `CRON_SETUP.md`) with a nightly job that clusters similar payees and surfaces merge suggestions in the `review` CLI.

**Files.** `finance_tracker/agents/categorizer.py`, `finance_tracker/db.py`, `finance_tracker/cli.py`.

**Verify.** Eval harness reports `%resolved_by_exact / %resolved_by_embedding / %resolved_by_llm` and total tokens/statement. Embedding tier should shift a measurable share off the LLM without hurting precision.

---

## Bonus — a local extraction tool for the parser agent

`parser_agent` currently receives raw page text and nothing else — no tools. Giving it **one deterministic, layout-aware extraction tool** it can call when raw text is ambiguous is a meaningful upgrade and fits the "LLM as constrained sub-agent" philosophy.

**Deliverable.** A `pdfplumber_table_tool` exposed to the parser agent (via `deepagents` tool registration):

- Input: page number + optional bounding box.
- Output: JSON of extracted table rows (date | description | amount | balance) using `pdfplumber.Page.extract_tables()`.
- Deterministic, free, local — no SaaS, no PII leaving the machine.

The agent decides when to invoke it (e.g., when raw text runs together and column alignment is unclear). Verbatim verification and balance audit still run on top, so safety barriers are unchanged.

**Files.** New `finance_tracker/tools/pdfplumber_tool.py`; register in `finance_tracker/agents/parser_agent.py`.

**Verify.** Eval harness (Phase 1) shows extraction F1 improvement on pages where the raw-text-only baseline previously missed rows.

---

## Considered and rejected — Parseur (SaaS document parser)

- **Privacy** — Parseur is SaaS. Every ingest would ship a full KBank statement (account #, balance, every transaction, name) to a third-party server. Not justifiable for a personal finance tracker when a local alternative exists.
- **Philosophy fit** — Project rule (per `CLAUDE.md`) is "LLMs only as constrained sub-agents, Python owns control flow." Parseur is an opaque vendor black box: no inspection, no retry tuning, no eval hooks. The Phase 3 self-critique loop becomes impossible.
- **Cost + template overhead** — Bills per parsed doc; needs a template per statement format. Not worth it for one user, one bank.
- **When to revisit** — Only if statements become image-only / scanned so text extraction fails entirely. At that point evaluate a local OCR fallback (`ocrmypdf` + `pdfplumber`) *first*.

---

## Sequencing

1. **Phase 0** (hardening) — small, ship first, cleans the baseline.
2. **Phase 1** (eval harness) — nothing after this can be honestly claimed as "better" without it.
3. **Phase 2** (tracing) — cheap, unblocks debugging for everything after.
4. **Phase 3, 4, 5** — roughly parallel; different code paths.
5. **Bonus (`pdfplumber` tool)** — after Phase 1, so its impact is measured, not asserted.

## End-to-end verification

- `PYTHONPATH=. pytest` (existing + Phase 0 additions) stays green.
- `PYTHONPATH=. pytest -m eval` produces a baseline report; each phase shows a measurable delta.
- Golden path still works:
  - `python main.py ingest <sample>.pdf`
  - `python main.py review`
  - `python main.py report 2026-09`
- New commands work:
  - `python main.py review --spot-check` (Phase 4)
  - `python main.py trace <transaction_id>` (Phase 2)

---

## What is intentionally NOT in this plan

Multi-bank adapter pattern, autonomous planning agent, prompt ablation studies, multi-agent debate. All interesting, all lower-leverage until the eval harness exists to prove they help. Revisit after Phases 1–5 ship.

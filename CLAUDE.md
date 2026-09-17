# CLAUDE.md

Context, commands, and development conventions for Claude / Cursor / IDE assistants.

---

## Project Overview

A Personal Finance Tracker built on LangChain `deepagents` that ingests **Kasikornbank (KBank / K PLUS)** bank statement PDFs, extracts transactions with verbatim substring verification, categorizes spending, and produces on-demand financial analysis.

- **Statement Ingestion**: Ingests statement PDFs via CLI (`main.py ingest <file.pdf>`).
- **Password Security**: Detects encrypted PDFs and prompts via `getpass` with retry availability.
- **On-Demand Analysis**: Generates spending breakdowns, income totals, and net savings (`main.py report YYYY-MM`).
- **Human-in-the-Loop Review**: CLI tool (`main.py review`) resolves ambiguous or unverified transactions.

For detailed guidelines, see [AGENTS.md](AGENTS.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Development Commands

```bash
# Activate virtual environment
source .venv/bin/activate

# Run tests
PYTHONPATH=. pytest

# Ingest a bank statement PDF
python main.py ingest path/to/statement.pdf

# Ingest with explicit password
python main.py ingest path/to/statement.pdf --password MY_SECRET_PASSWORD

# Interactively resolve uncategorized / unverified transactions
python main.py review

# Run monthly analysis report on demand (YYYY-MM)
python main.py report 2026-09
```

---

## Key Architectural Rules

1. **Zero Financial Hallucination**:
   - Never let an LLM invent or retype numbers/dates/balances.
   - All extracted fields in `StatementTransaction` (`raw_amount`, `raw_date`, `raw_balance`) must match verbatim as substrings in the raw statement page text via `verify_statement_tx_verbatim`.
   - If verbatim check fails, the amount is rejected (`0.0`) and quarantined to `pending_review`.
2. **Deterministic Control Flow**:
   - Keep orchestrator loop in standard Python (`finance_tracker/agents/orchestrator.py`).
   - Use LLMs only as constrained sub-agents:
     - `parser_agent.py`: statement page text -> `ParsedStatement`.
     - `categorizer.py`: memo/description string -> `CategoryResult`.
3. **Deduplication & Idempotency**:
   - `processed_statements` records every processed statement SHA-256 hash upfront to prevent duplicate entries and API billing.
4. **Categorization Precedence**:
   - Memo -> LLM categorizer.
   - No memo -> check `payee_category_map` in DB.
   - Low confidence description -> `Uncategorized` + queue in `pending_review`.
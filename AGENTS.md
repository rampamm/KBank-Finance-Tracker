# AGENTS.md

Instructions, architecture principles, and operational rules for AI agents working in this repository.

---

## 1. Project Overview & Mission

**Goal**: Build a personal finance tracker that ingests **bank statement PDFs** (specifically Kasikornbank / KBank / K PLUS e-Statements), extracts and validates transaction details (date, amount, type, balance, channel, description, memo), categorizes spending, and generates on-demand financial analysis.

### Operating Model
- **Ingestion**: Run via CLI (`main.py ingest <statement.pdf>`).
  - Automatically detects password-protected PDFs and interactively prompts the user for the password via `getpass` with retry availability.
  - Computes a SHA-256 checksum of the statement to ensure idempotent processing (skips duplicates).
- **Categorization**: Multi-tiered:
  1. If transaction has a **memo/note**, LLM categorizer tags it against a strict taxonomy.
  2. If no memo, check **payee category mapping** in the database.
  3. If neither is available, attempt categorization on the transaction description; if confidence is low, mark as `Uncategorized` and push to `pending_review` queue.
- **Analysis**: **On-demand only** (`main.py report YYYY-MM`). The system generates monthly spending breakdowns, income totals, and net savings only when explicitly triggered by the user or an agent.
- **Human-in-the-Loop Review**: CLI tool (`main.py review`) allows the user to resolve unclassified or ambiguous transactions and permanently learn payee category rules.

---

## 2. Core Architectural Principles (Non-Negotiable)

1. **Zero Financial Hallucination Guarantee (Verbatim Matching)**:
   - LLMs must **never** be trusted to retype, convert, or calculate financial numbers, currency, balances, or timestamps without verification.
   - The parser LLM extracts raw text substrings (`raw_amount`, `raw_date`, `raw_balance`).
   - Python code runs `verify_statement_tx_verbatim(page_text, tx)` to ensure the extracted strings exist **character-for-character** (ignoring collapsed whitespace) in the source document page.
   - If verbatim verification fails for an amount, the amount is **rejected** (stored as `0.0`), and the item is immediately routed to `pending_review`.
2. **Deterministic Orchestration over Autonomous Multi-Agent Chatter**:
   - Do **NOT** replace the Python pipeline in `finance_tracker/agents/orchestrator.py` with an open-ended autonomous multi-agent conversation loop.
   - Control flow (PDF reading, page iteration, database deduplication, database transactions) is strictly plain Python code.
   - LLM agents (`deepagents`) are deployed only as narrow, single-purpose sub-agents for tasks requiring language understanding:
     - **Parser Agent** (`finance_tracker/agents/parser_agent.py`): Extract fields from raw statement pages into `ParsedStatement`.
     - **Categorizer Agent** (`finance_tracker/agents/categorizer.py`): Classify transaction memo/description into one of the predefined categories.
3. **Idempotency & Re-run Safety**:
   - `processed_statements` table records every processed statement file hash (SHA-256) before downstream processing.
   - Duplicate runs on the same statement PDF will skip processing, preventing duplicate transaction entries and avoiding unnecessary LLM API costs.
4. **On-Demand Analysis**:
   - Analysis is decoupled from ingestion.
   - Ingestion only processes data into SQLite. Reporting runs only when requested.

---

## 3. Directory & File Structure

```
DeepAgent_Project/
├── AGENTS.md                  # This file: context & guidance for AI agents
├── CLAUDE.md                  # Quick commands & agent reference
├── README.md                  # Human-facing project overview & quickstart
├── ARCHITECTURE.md            # Deep architectural breakdown & data flows
├── pyproject.toml             # Python package definition & dependencies
├── main.py                    # Root CLI entry point
├── .env.example               # Template for environment variables
├── finance_tracker/           # Main Python package
│   ├── __init__.py
│   ├── config.py              # Central config (categories, paths, models)
│   ├── models.py              # Pydantic structured schemas (StatementTransaction, ParsedStatement, CategoryResult)
│   ├── db.py                  # SQLite schema, migrations, and data access layer (CRUD)
│   ├── pdf_source.py          # PDF reader, password prompt & retry logic, SHA-256 checksums
│   ├── analysis.py            # Financial reporting and summary calculation (expenses, income, savings)
│   ├── cli.py                 # CLI commands (ingest, review, report)
│   └── agents/                # LLM agent integrations via deepagents
│       ├── __init__.py
│       ├── parser_agent.py    # Sub-agent: parses statement text into ParsedStatement
│       ├── categorizer.py     # Sub-agent: categorizes memo/description into fixed categories
│       └── orchestrator.py    # Pipeline: ties PDF extraction, parser, categorizer, and DB together
└── tests/
    ├── test_parser_agent.py   # Unit tests for email and parsing utilities
    └── test_statement_parser.py # Unit tests for PDF extraction, encryption, verbatim check, and DB
```

---

## 4. Key Components & Data Flow

### 4.1. Data Models (`finance_tracker/models.py`)

- **`StatementTransaction`**:
  - `raw_date`: string exact copy from statement (e.g. `"12/09/2026"`).
  - `raw_time`: string exact copy if present (e.g. `"14:40:08"`).
  - `tx_type`: `"debit"` (withdrawal/expense) or `"credit"` (deposit/income).
  - `raw_amount`: string exact copy (e.g. `"150.00"`).
  - `raw_balance`: string exact copy of remaining balance (e.g. `"25,592.65"`).
  - `channel`: channel code (e.g. `"K PLUS"`, `"EDC"`, `"ATM"`).
  - `description`: counterparty / merchant details.
  - `memo`: user-entered note if present.
  - `currency`: `"THB"`.
- **`ParsedStatement`**:
  - `account_number`: string (if present).
  - `statement_period`: string (if present).
  - `transactions`: list of `StatementTransaction`.
- **`CategoryResult`**:
  - `category`: Literal restricted to the 7 defined categories.
  - `confidence`: float (0.0 to 1.0).
  - `rationale`: short string explanation.

### 4.2. Category Taxonomy (`finance_tracker/config.py`)
Fixed categories:
- `Food & Dining`
- `Transport`
- `Shopping`
- `Bills & Utilities`
- `Entertainment`
- `Health`
- `Transfers`
- `Uncategorized` *(Reserved: pipeline fallback only; never chosen by LLM)*

### 4.3. Database Schema (`finance_tracker/db.py`)
SQLite database (default: `finance_tracker.db`):
1. **`processed_statements`**: `(statement_hash PK, filename, account_number, period, page_count, processed_at)`
2. **`transactions`**:
   - `id` (PK AUTOINCREMENT)
   - `statement_hash`, `statement_row_index` (UNIQUE)
   - `transaction_datetime`, `tx_type`, `amount`, `balance`, `fee`, `currency`, `channel`, `counterparty`, `memo`, `category`, `raw_extracted_json`, `created_at`
3. **`payee_category_map`**: `(payee_name PK, category)` - Learns rules from manual user review.
4. **`pending_review`**: `(id PK, transaction_id FK, reason, created_at, resolved_at)` - Holds transactions needing user intervention.

---

## 5. Development, Running & Testing

### Virtual Environment & Dependencies
```bash
# Activate venv
source .venv/bin/activate

# Running tests
PYTHONPATH=. pytest
```

### CLI Commands
```bash
# Ingest a bank statement PDF
python main.py ingest path/to/statement.pdf

# Ingest with optional inline password
python main.py ingest path/to/statement.pdf --password MY_SECRET_PASSWORD

# Interactively resolve transactions flagged for review
python main.py review

# Generate monthly spending & income report on demand (YYYY-MM)
python main.py report 2026-09
```

---

## 6. Rules for Modifying or Extending This Codebase

1. **Password Safety**:
   - Never write passwords to logs, database records, or configuration files.
   - Use `getpass.getpass` for interactive terminal prompts.
2. **Never Bypass Verbatim Verification**:
   - Every transaction amount, date, and balance must pass `verify_statement_tx_verbatim` against the raw PDF page text.
   - If an amount fails verbatim check, do not guess: reject it (`amount = 0.0`) and route to `pending_review`.
3. **Preserve Database Migrations / Schema Integrity**:
   - `_run_migrations` in `finance_tracker/db.py` ensures existing databases adapt smoothly when new columns are introduced.
4. **Preserve Docstrings and Comments**:
   - Maintain documentation integrity across all files. Do not delete explanatory architectural notes or docstrings.

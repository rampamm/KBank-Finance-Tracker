# KBank Bank Statement PDF Finance Tracker

An automated personal finance tracker built with Python and LangChain `deepagents`. It ingests **Kasikornbank (KBank / K PLUS)** bank statement PDFs, extracts transaction details (amounts, dates, types, balances, and memos) with strict verbatim verification, categorizes spending, and generates on-demand financial analysis.

---

## Features

- **Bank Statement PDF Ingestion**: Ingests monthly e-Statement PDFs directly via CLI (`python main.py ingest path/to/statement.pdf`).
- **Secure Password Handling**: Automatically detects password-protected PDFs and securely prompts for the password in the terminal via `getpass` with retry availability.
- **Zero-Hallucination Extraction**: Uses a specialized LLM sub-agent (`deepagents`) to extract transactions, strictly verifying each amount, date, and balance verbatim against the raw PDF text before writing to the database.
- **Spending Categorization**:
  - **Memo & Description Intelligence**: Automatically classifies transactions into 7 predefined spending categories.
  - **Payee Learning**: Remembers user-classified counterparties across future statements.
  - **Human-in-the-Loop Review**: Quarantines unverified or ambiguous items into a review queue (`python main.py review`).
- **On-Demand Financial Analysis**: Generates spending-by-category breakdowns, total expenses, total income, and net savings on demand (`python main.py report YYYY-MM`).
- **Local & Private SQLite Storage**: All transaction data is stored locally in `finance_tracker.db`.

---

## Quick Start

### 1. Installation

Requires Python 3.11+.

```bash
# Clone and enter directory
cd DeepAgent_Project

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .
```

### 2. Environment Setup

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` and set your OpenAI API key:

```ini
OPENAI_API_KEY=sk-...

# Optional configurations:
# FINANCE_TRACKER_DB_PATH=./finance_tracker.db
# FINANCE_TRACKER_PARSER_MODEL=openai:gpt-4.1-mini
# FINANCE_TRACKER_CATEGORIZER_MODEL=openai:gpt-4.1-mini
```

---

## Usage

### Ingest a Bank Statement PDF
Pass your bank statement PDF to the CLI:

```bash
python main.py ingest /path/to/kbank_statement.pdf
```

- If the PDF is password-protected, the CLI will prompt you:
  ```
  [Locked PDF] 'kbank_statement.pdf' is password-protected.
  Enter PDF password (attempt 1/3, press Enter to abort):
  Password accepted.
  ```
- Alternatively, you can provide the password directly with `--password`:
  ```bash
  python main.py ingest statement.pdf --password MY_SECRET_PASSWORD
  ```

Output:
```
Reading bank statement PDF: statement.pdf ...
Extracting transactions from 2 page(s)...
  Parsing page 1/2...
  Parsing page 2/2...

Ingestion summary:
  Statement file:         statement.pdf
  Pages scanned:          2
  Transactions found:     45
  Inserted:               45
  Total expenses (debit): 18,420.00 THB
  Total deposits (credit):50,000.00 THB
  Queued for review:      3

Run `python main.py review` to resolve queued items.
```

### Review Queued Items
Interactively categorize transactions flagged for review:

```bash
python main.py review
```

### On-Demand Financial Report
Generate your financial summary for any given month:

```bash
python main.py report 2026-09
```

Output:
```
Financial summary for 2026-09
================================================
Category               Amount (THB)  Share
------------------------------------------------
Food & Dining              6,450.00  ( 35.0%)
Shopping                   4,820.00  ( 26.2%)
Bills & Utilities          3,200.00  ( 17.4%)
Transport                  2,150.00  ( 11.7%)
Entertainment              1,800.00  (  9.8%)
------------------------------------------------
Total Expenses            18,420.00
Total Income              50,000.00
Net Savings               31,580.00
================================================
```

---

## Architecture & Principles

For technical specifications, see [ARCHITECTURE.md](ARCHITECTURE.md) and [AGENTS.md](AGENTS.md).

---

## Testing

Run unit tests:

```bash
PYTHONPATH=. pytest
```

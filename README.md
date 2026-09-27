# KBank Finance Tracker

Your bank's e-Statement PDF has the truth about your money in it. This tool reads it — checks every amount, date, and balance character-for-character against the actual PDF text — so a number never gets there because a model guessed it.

Point it at a Kasikornbank (KBank / K PLUS) statement, and get back categorized transactions and a monthly spending report, with anything the model isn't sure about queued for you to resolve, not silently invented.

```
$ python main.py ingest september_statement.pdf

Ingestion summary:
  Transactions found:     82
  Inserted:               82
  Total expenses (debit): 13,679.56 THB
  Total deposits (credit): 7,166.50 THB
  Queued for review:      2

Run `python main.py review` to resolve queued items.
```

---

## Why verbatim matters

LLMs reading dense statement tables routinely drop a decimal, swap a debit for a credit, or confuse an opening balance with a transaction — a single 1 THB slip breaks reconciliation for the whole month. So the parser never gets to just "know" a number:

1. The LLM extracts `raw_amount`, `raw_date`, `raw_balance` as strings.
2. Python checks each one exists **verbatim** in the source PDF text.
3. A mismatch means the amount is stored as `0.0` and the row goes to `pending_review` — never a silent guess.
4. Running balances get an independent math check (`previous ± amount == new balance`); a mismatch triggers one automatic re-read of the source text before falling back to review.

Everything after that — which category a transaction belongs to — is lower stakes, so it's handled with confidence tiers instead of an all-or-nothing gate: high-confidence calls are auto-accepted, medium-confidence ones are accepted but flagged for a quick spot-check, and low-confidence ones wait for you in the review queue.

---

## Quick Start

Requires Python 3.11+.

```bash
git clone <this-repo> && cd DeepAgent_Project
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env   # then add your OPENAI_API_KEY
```

```bash
python main.py ingest statements/september_statement.pdf
python main.py review              # resolve anything queued
python main.py report 2026-09      # see the month's breakdown
```

---

## Usage

### `ingest` — parse a statement PDF

```bash
python main.py ingest /path/to/statement.pdf
python main.py ingest /path/to/statement.pdf --password MY_SECRET_PASSWORD
python main.py ingest /path/to/statement.pdf --force   # re-process, replacing prior records
```

Password-protected PDFs are detected automatically and prompted for securely via `getpass` (3 attempts) if `--password` isn't given. Each statement is fingerprinted by SHA-256, so re-running `ingest` on the same file is a no-op — no duplicate rows, no repeat API cost.

### `review` — resolve what the model wasn't sure about

```bash
python main.py review              # low-confidence / verification-failed items
python main.py review --spot-check # medium-confidence items, quick approve-or-correct
```

Every category you assign to a counterparty is remembered (`payee_category_map`), so the same payee next month is categorized instantly, without another LLM call.

### `report` — see where the money went

```bash
python main.py report 2026-09
```

```
Financial summary for 2026-09
================================================
Category               Amount (THB)  Share
------------------------------------------------
Food                       6,450.00  ( 47.2%)
Groceries                  3,120.00  ( 22.8%)
Bills & Utilities          2,150.00  ( 15.7%)
Transport                  1,090.00  (  8.0%)
Drinks                       780.00  (  5.7%)
Transfer to others            89.56  (  0.6%)
------------------------------------------------
Total Expenses             13,679.56
Total Income                7,166.50
Net Savings                -6,513.06
================================================
```

### `trace` — see the reasoning behind one transaction

```bash
python main.py trace 71
```

Prints the full decision trail for a transaction ID — what the model extracted, whether it passed verbatim/balance checks, its category confidence and tier, and any retries — pulled from the structured run logs in `logs/`.

---

## Architecture & Principles

Python owns control flow (which files, in what order, what gets written to SQLite); LLMs are used only as narrow sub-agents for extraction and categorization, never for arithmetic or free-form orchestration. Full details in [ARCHITECTURE.md](ARCHITECTURE.md) and [AGENTS.md](AGENTS.md).

## Testing

```bash
PYTHONPATH=. pytest              # unit tests
PYTHONPATH=. pytest -m eval      # extraction/categorization accuracy benchmark
```

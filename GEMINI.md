# GEMINI.md

Context, operational rules, and architecture for Gemini / Antigravity agents in this workspace.

Please refer to the comprehensive guide in [AGENTS.md](AGENTS.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## Summary Rules for this Project

1. **Mission**:
   - Ingest KBank / K PLUS bank statement PDFs.
   - Extract date, time, amount, type (debit/credit), balance, channel, and description/memo.
   - Run ingestion on statement files (`main.py ingest <statement.pdf>`).
   - Secure interactive password prompting via `getpass` with retries when PDFs are locked.
   - Financial analysis runs **only on demand** (`main.py report YYYY-MM`).
2. **Numerical Verbatim Guarantee**:
   - Never trust LLM-retyped financial amounts or balances.
   - All extracted figures must pass verbatim substring validation (`verify_statement_tx_verbatim`) against the raw PDF page text before being saved to SQLite.
   - Any mismatch causes amount rejection (`amount = 0.0`) and quarantines the transaction to `pending_review`.
3. **Deterministic Core**:
   - Python code controls PDF decryption, page looping, idempotency checking (`processed_statements` with SHA-256), and database transactions.
   - Sub-agents are used strictly for language interpretation:
     - `parser_agent.py`: Parses raw statement page text into `ParsedStatement`.
     - `categorizer.py`: Maps memo/description into fixed categories.
4. **Commands**:
   - Run tests: `PYTHONPATH=. pytest`
   - Run ingest: `python main.py ingest <statement.pdf>`
   - Run review: `python main.py review`
   - Run monthly report: `python main.py report YYYY-MM`

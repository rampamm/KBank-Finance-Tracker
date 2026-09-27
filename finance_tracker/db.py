"""SQLite persistence: schema + CRUD for transactions, payee mappings,
statement processing history, and the pending-review queue.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from finance_tracker.config import DB_PATH, UNCATEGORIZED

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_statements (
    statement_hash   TEXT PRIMARY KEY,
    filename         TEXT NOT NULL,
    account_number   TEXT,
    period           TEXT,
    page_count       INTEGER,
    processed_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_hash       TEXT,
    statement_row_index  INTEGER,
    gmail_message_id     TEXT,
    transaction_number   TEXT,
    email_subject        TEXT,
    transaction_datetime TEXT,
    tx_type              TEXT DEFAULT 'debit',
    amount               REAL NOT NULL,
    balance              REAL,
    fee                  REAL,
    currency             TEXT NOT NULL DEFAULT 'THB',
    channel              TEXT,
    counterparty         TEXT,
    memo                 TEXT,
    category             TEXT NOT NULL,
    parser_retry_count   INTEGER DEFAULT 0,
    needs_spot_check     INTEGER DEFAULT 0,
    raw_extracted_json   TEXT,
    created_at           TEXT NOT NULL,
    UNIQUE(statement_hash, statement_row_index)
);

CREATE TABLE IF NOT EXISTS payee_category_map (
    payee_name TEXT PRIMARY KEY,
    category   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_review (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  INTEGER NOT NULL REFERENCES transactions(id),
    reason          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    resolved_at     TEXT
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _run_migrations(conn):
    """Add columns to existing tables if migrating from an earlier schema."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()}
    migrations = [
        ("statement_hash", "TEXT"),
        ("statement_row_index", "INTEGER"),
        ("tx_type", "TEXT DEFAULT 'debit'"),
        ("balance", "REAL"),
        ("channel", "TEXT"),
        ("parser_retry_count", "INTEGER DEFAULT 0"),
        ("needs_spot_check", "INTEGER DEFAULT 0"),
    ]
    for col_name, col_type in migrations:
        if col_name not in cols:
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {col_name} {col_type}")



def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        _run_migrations(conn)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- processed_statements -----------------------------------------------


def is_statement_processed(conn, statement_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM processed_statements WHERE statement_hash = ?", (statement_hash,)
    ).fetchone()
    return row is not None


def mark_statement_processed(
    conn,
    statement_hash: str,
    filename: str,
    account_number: Optional[str] = None,
    period: Optional[str] = None,
    page_count: int = 1,
):
    conn.execute(
        """INSERT OR IGNORE INTO processed_statements (
            statement_hash, filename, account_number, period, page_count, processed_at
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        (statement_hash, filename, account_number, period, page_count, now_iso()),
    )


def delete_processed_statement(conn, statement_hash: str):
    """Purge a previously processed statement, its transactions, and pending reviews."""
    conn.execute(
        """DELETE FROM pending_review WHERE transaction_id IN (
            SELECT id FROM transactions WHERE statement_hash = ?
        )""",
        (statement_hash,),
    )
    conn.execute(
        "DELETE FROM transactions WHERE statement_hash = ?",
        (statement_hash,),
    )
    conn.execute(
        "DELETE FROM processed_statements WHERE statement_hash = ?",
        (statement_hash,),
    )



# --- payee_category_map --------------------------------------------------


def lookup_payee_category(conn, payee_name: str) -> Optional[str]:
    if not payee_name:
        return None
    row = conn.execute(
        "SELECT category FROM payee_category_map WHERE payee_name = ?", (payee_name,)
    ).fetchone()
    return row["category"] if row else None


def lookup_payee_category_fuzzy(
    conn, payee_name: str, threshold: float = 80.0
) -> Optional[tuple[str, str, float, str]]:
    """Look up a payee using exact match first, falling back to RapidFuzz token_set_ratio.
    Returns (category, matched_payee_name, score, match_type) where match_type is 'exact' or 'fuzzy',
    or None if no match meets threshold.
    """
    if not payee_name or not payee_name.strip():
        return None
    cleaned = payee_name.strip()
    exact = lookup_payee_category(conn, cleaned)
    if exact:
        return (exact, cleaned, 100.0, "exact")

    rows = conn.execute("SELECT payee_name, category FROM payee_category_map").fetchall()
    if not rows:
        return None

    try:
        from rapidfuzz import fuzz, process

        choices = {row["payee_name"]: row["category"] for row in rows}
        match = process.extractOne(
            cleaned,
            choices.keys(),
            scorer=fuzz.token_set_ratio,
            score_cutoff=threshold,
        )
        if match:
            best_name, score, _ = match
            return (choices[best_name], best_name, float(score), "fuzzy")
    except ImportError:
        pass

    return None


def upsert_payee_category(conn, payee_name: str, category: str):
    conn.execute(
        """INSERT INTO payee_category_map (payee_name, category) VALUES (?, ?)
           ON CONFLICT(payee_name) DO UPDATE SET category = excluded.category""",
        (payee_name, category),
    )


# --- transactions ---------------------------------------------------------


def insert_transaction(
    conn,
    *,
    statement_hash: Optional[str] = None,
    statement_row_index: Optional[int] = None,
    gmail_message_id: Optional[str] = None,
    transaction_number: Optional[str] = None,
    email_subject: Optional[str] = None,
    transaction_datetime: Optional[str] = None,
    tx_type: str = "debit",
    amount: float,
    balance: Optional[float] = None,
    fee: Optional[float] = None,
    currency: str = "THB",
    channel: Optional[str] = None,
    counterparty: Optional[str] = None,
    memo: Optional[str] = None,
    category: str,
    parser_retry_count: int = 0,
    needs_spot_check: int = 0,
    raw_extracted_json: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO transactions (
            statement_hash, statement_row_index, gmail_message_id,
            transaction_number, email_subject, transaction_datetime,
            tx_type, amount, balance, fee, currency, channel,
            counterparty, memo, category, parser_retry_count,
            needs_spot_check, raw_extracted_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            statement_hash,
            statement_row_index,
            gmail_message_id,
            transaction_number,
            email_subject,
            transaction_datetime,
            tx_type,
            amount,
            balance,
            fee,
            currency,
            channel,
            counterparty,
            memo,
            category,
            parser_retry_count,
            needs_spot_check,
            raw_extracted_json,
            now_iso(),
        ),
    )
    return cur.lastrowid


def add_pending_review(conn, transaction_id: int, reason: str):
    conn.execute(
        "INSERT INTO pending_review (transaction_id, reason, created_at) VALUES (?, ?, ?)",
        (transaction_id, reason, now_iso()),
    )


def list_open_pending_review(conn):
    return conn.execute(
        """SELECT pr.id as pending_id, pr.reason, t.*
           FROM pending_review pr
           JOIN transactions t ON t.id = pr.transaction_id
           WHERE pr.resolved_at IS NULL
           ORDER BY pr.created_at ASC"""
    ).fetchall()


def list_open_spot_checks(conn):
    return conn.execute(
        """SELECT id as transaction_id, *
           FROM transactions
           WHERE needs_spot_check = 1
           ORDER BY transaction_datetime DESC"""
    ).fetchall()


def resolve_spot_check(conn, transaction_id: int, category: Optional[str] = None):
    if category:
        conn.execute(
            "UPDATE transactions SET category = ?, needs_spot_check = 0 WHERE id = ?",
            (category, transaction_id),
        )
    else:
        conn.execute(
            "UPDATE transactions SET needs_spot_check = 0 WHERE id = ?",
            (transaction_id,),
        )



def resolve_pending_review(conn, pending_id: int, category: str):
    row = conn.execute(
        "SELECT transaction_id FROM pending_review WHERE id = ?", (pending_id,)
    ).fetchone()
    if row is None:
        return
    conn.execute("UPDATE transactions SET category = ? WHERE id = ?", (category, row["transaction_id"]))
    conn.execute("UPDATE pending_review SET resolved_at = ? WHERE id = ?", (now_iso(), pending_id))


def monthly_summary(conn, year_month: str):
    """year_month: 'YYYY-MM' -> summarize debit/expenses by category."""
    rows = conn.execute(
        """SELECT category, SUM(amount) as total, COUNT(*) as count
           FROM transactions
           WHERE (tx_type IS NULL OR tx_type = 'debit')
             AND (substr(transaction_datetime, 1, 7) = ? OR substr(created_at, 1, 7) = ?)
           GROUP BY category
           ORDER BY total DESC""",
        (year_month, year_month),
    ).fetchall()
    return rows


def monthly_totals(conn, year_month: str):
    """Get total debit (spending) and total credit (income) for year_month."""
    debit_row = conn.execute(
        """SELECT SUM(amount) as total FROM transactions
           WHERE (tx_type IS NULL OR tx_type = 'debit')
             AND (substr(transaction_datetime, 1, 7) = ? OR substr(created_at, 1, 7) = ?)""",
        (year_month, year_month),
    ).fetchone()
    credit_row = conn.execute(
        """SELECT SUM(amount) as total FROM transactions
           WHERE tx_type = 'credit'
             AND (substr(transaction_datetime, 1, 7) = ? OR substr(created_at, 1, 7) = ?)""",
        (year_month, year_month),
    ).fetchone()
    return {
        "debit": debit_row["total"] or 0.0 if debit_row else 0.0,
        "credit": credit_row["total"] or 0.0 if credit_row else 0.0,
    }

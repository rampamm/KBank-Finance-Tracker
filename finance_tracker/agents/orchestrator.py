"""Deterministic ingest pipeline.

Deliberately NOT a single free-roaming "manager" agent deciding the whole
flow: for financial data, letting an LLM retype amounts/dates between tool
calls is an avoidable source of transcription error. Instead, plain Python
controls which files/pages get processed and in what order, and calls the two
narrow-purpose LLM sub-agents (parser, categorizer) only for the specific
judgment calls that actually require language understanding. Every number
that reaches the database comes straight from the verbatim-validated parser
output, never retyped by another model.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from finance_tracker import db
from finance_tracker.agents.categorizer import categorize_batch, categorize_from_memo
from finance_tracker.agents.parser_agent import parse_statement_page, reparse_failed_transactions
from finance_tracker.config import CONFIDENCE_HIGH, CONFIDENCE_LOW, UNCATEGORIZED
from finance_tracker.pdf_source import extract_statement_pdf
from finance_tracker.tracing import init_tracing, log_event


@dataclass
class StatementIngestStats:
    filename: str = ""
    pages_scanned: int = 0
    transactions_found: int = 0
    inserted: int = 0
    skipped_already_processed: bool = False
    queued_for_review: int = 0
    total_debit: float = 0.0
    total_credit: float = 0.0


def _normalize_datetime(raw_date: str | None, raw_time: str | None = None) -> str | None:
    """Best-effort normalize date and optional time to 'YYYY-MM-DD HH:MM:SS'
    for monthly grouping. Returns None if the format isn't recognized."""
    if not raw_date:
        return None

    raw = f"{raw_date.strip()} {raw_time.strip()}" if raw_time else raw_date.strip()
    collapsed = re.sub(r"\s+", " ", raw)

    formats = [
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%d/%m/%y %H:%M:%S",
        "%d/%m/%y %H:%M",
        "%d/%m/%y",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%d-%m-%y %H:%M:%S",
        "%d-%m-%y %H:%M",
        "%d-%m-%y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            parsed_dt = datetime.strptime(collapsed, fmt)
            return parsed_dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw.replace(",", "").strip())
    except ValueError:
        return None


def _audit_balance(prev: float | None, amount: float, tx_type: str, balance: float | None) -> str | None:
    """Verify sequential running balance arithmetic: prev +/- amount == balance.
    Returns review reason string on discrepancy > 0.01, else None."""
    if prev is None or balance is None:
        return None
    expected = (prev - amount) if tx_type == "debit" else (prev + amount)
    if abs(expected - balance) > 0.01:
        return f"Balance audit mismatch: expected {expected:,.2f}, got {balance:,.2f}."
    return None


def _resolve_category(
    item_info: dict,
    batch_results: dict[int, Any],
) -> tuple[str, list[str], int, str]:
    """Determine category, collect review reasons, spot-check flag, and routing tier.
    Returns: (category, reasons, needs_spot_check, tier)
      tier: 'credit', 'exact', 'fuzzy', 'high', 'medium', 'low'
    """
    reasons = []
    tx_type = item_info.get("tx_type") or "debit"
    counterparty = item_info.get("counterparty") or "unknown"

    if tx_type != "debit":
        return "Transfer to me", reasons, 0, "credit"

    if item_info.get("mapped_category"):
        match_type = item_info.get("mapped_match_type") or "exact"
        return item_info["mapped_category"], reasons, 0, match_type

    if item_info.get("needs_batch_llm"):
        b_res = batch_results.get(item_info["batch_index"])
        if b_res:
            conf = getattr(b_res, "confidence", 1.0)
            if conf >= CONFIDENCE_HIGH:
                return b_res.category, reasons, 0, "high"
            elif conf >= CONFIDENCE_LOW:
                # Medium confidence: auto-accepted, but flagged for spot check
                return b_res.category, reasons, 1, "medium"
            else:
                # Low confidence: routed to pending_review
                reasons.append(f"Low confidence ({conf:.2f}) for '{counterparty}'.")
                return b_res.category, reasons, 0, "low"
        else:
            reasons.append("Batch categorizer omitted item.")
            return UNCATEGORIZED, reasons, 0, "low"

    reasons.append("No memo, details, or description to determine category.")
    return UNCATEGORIZED, reasons, 0, "low"


def run_statement_ingest(
    pdf_path: str,
    password: Optional[str] = None,
    force: bool = False,
) -> StatementIngestStats:
    """Process a single bank statement PDF: extract text, verify verbatim,
    audit balance math, batch-categorize spending, and insert into SQLite."""
    db.init_db()
    init_tracing(f"ingest-{Path(pdf_path).stem}")

    log_event("ingest_started", {"pdf_path": str(pdf_path)}, human_msg=f"Reading bank statement PDF: {pdf_path} ...")
    extracted = extract_statement_pdf(pdf_path, password=password)
    stats = StatementIngestStats(
        filename=extracted.filename,
        pages_scanned=extracted.page_count,
    )

    with db.connect() as conn:
        if db.is_statement_processed(conn, extracted.file_hash):
            if not force:
                msg = f"Statement '{extracted.filename}' (hash: {extracted.file_hash[:8]}...) was already processed. Skipping (use --force to re-process)."
                log_event("ingest_skipped_duplicate", {"statement_hash": extracted.file_hash, "filename": extracted.filename}, human_msg=msg)
                stats.skipped_already_processed = True
                return stats
            else:
                log_event("ingest_force_reprocess", {"statement_hash": extracted.file_hash, "filename": extracted.filename}, human_msg="Force re-process enabled. Purging previous statement records...")
                db.delete_processed_statement(conn, extracted.file_hash)


        log_event(
            "statement_extracted",
            {
                "filename": extracted.filename,
                "statement_hash": extracted.file_hash,
                "pages_count": extracted.page_count,
            },
            human_msg=f"Extracting transactions from {extracted.page_count} page(s)...",
        )

        row_index = 0
        statement_account = None
        statement_period = None
        prev_balance: Optional[float] = None

        for page_idx, page_text in enumerate(extracted.pages):
            if not page_text.strip():
                continue

            page_num = page_idx + 1
            print(f"  Parsing page {page_num}/{extracted.page_count}...")

            outcome = parse_statement_page(page_text, page_num=page_num)

            if not statement_account and outcome.parsed.account_number:
                statement_account = outcome.parsed.account_number
            if not statement_period and outcome.parsed.statement_period:
                statement_period = outcome.parsed.statement_period

            page_tx_data = []
            batch_to_categorize = []

            # Statement transactions strictly preserve physical statement row order (top-to-bottom),
            # which reflects the bank's chronological ledger order.
            for verified in outcome.verified_transactions:
                row_index += 1
                stats.transactions_found += 1
                tx = verified.tx

                if not verified.verbatim_ok:
                    page_tx_data.append({
                        "verbatim_ok": False,
                        "row_index": row_index,
                        "verified": verified,
                    })
                    continue

                amount = _to_float(tx.raw_amount) or 0.0
                balance = _to_float(tx.raw_balance)
                tx_datetime = _normalize_datetime(tx.raw_date, tx.raw_time)

                if tx.tx_type == "debit":
                    stats.total_debit += amount
                else:
                    stats.total_credit += amount

                counterparty = tx.details or tx.description

                mapped_cat = None
                mapped_match_type = None
                if tx.tx_type == "debit" and not tx.memo and counterparty:
                    fuzzy_match = db.lookup_payee_category_fuzzy(conn, counterparty)
                    if fuzzy_match:
                        mapped_cat, matched_name, score, mapped_match_type = fuzzy_match

                item_info = {
                    "verbatim_ok": True,
                    "row_index": row_index,
                    "verified": verified,
                    "amount": amount,
                    "balance": balance,
                    "tx_datetime": tx_datetime,
                    "tx_type": tx.tx_type,
                    "counterparty": counterparty,
                    "mapped_category": mapped_cat,
                    "mapped_match_type": mapped_match_type,
                    "needs_batch_llm": False,
                    "batch_index": len(batch_to_categorize),
                }

                if tx.tx_type == "debit" and not mapped_cat:
                    item_info["needs_batch_llm"] = True
                    batch_to_categorize.append({
                        "index": len(batch_to_categorize),
                        "memo": tx.memo or tx.details or tx.description or "",
                        "counterparty": counterparty or tx.channel or "unknown",
                    })

                page_tx_data.append(item_info)

            # Perform a single batch LLM call for all unclassified debit items on this page
            batch_results = categorize_batch(batch_to_categorize) if batch_to_categorize else {}

            for item in page_tx_data:
                if not item["verbatim_ok"]:
                    verified = item["verified"]
                    print(
                        f"    [!] Row {item['row_index']}: verbatim verification failed for {verified.verbatim_failures}. "
                        "Amount rejected (stored as 0.0) -> queued for review."
                    )
                    tx_id = db.insert_transaction(
                        conn,
                        statement_hash=extracted.file_hash,
                        statement_row_index=item["row_index"],
                        transaction_datetime=None,
                        tx_type=verified.tx.tx_type,
                        amount=0.0,
                        balance=None,
                        fee=None,
                        currency=verified.tx.currency,
                        channel=verified.tx.channel,
                        counterparty=verified.tx.description,
                        memo=verified.tx.memo,
                        category=UNCATEGORIZED,
                        parser_retry_count=verified.retry_count,
                        needs_spot_check=0,
                        raw_extracted_json=json.dumps(verified.tx.model_dump()),
                    )
                    db.add_pending_review(
                        conn,
                        tx_id,
                        f"Verbatim verification failed for {verified.verbatim_failures}. "
                        "Amount was NOT trusted -- verify statement text manually.",
                    )
                    stats.queued_for_review += 1
                    continue

                tx = item["verified"].tx
                amount = item["amount"]
                balance = item["balance"]
                tx_datetime = item["tx_datetime"]
                counterparty = item["counterparty"]

                needs_review = False
                review_reasons = []

                # Mathematical Balance Audit with Self-Critique Retry
                audit_err = _audit_balance(prev_balance, amount, tx.tx_type, balance)
                if audit_err and item["verified"].retry_count < 2:
                    expected_bal = (prev_balance - amount) if tx.tx_type == "debit" else (prev_balance + amount)
                    critique_items = [{
                        "index": item["row_index"],
                        "tx": tx,
                        "critique": (
                            f"Balance audit mismatch: row {item['row_index']} ({tx.raw_date}, amount {amount:,.2f} {tx.tx_type}). "
                            f"Previous balance was {prev_balance:,.2f}, so expected balance is {expected_bal:,.2f}. "
                            f"Extracted balance was '{tx.raw_balance}' ({balance:,.2f}). "
                            "Please re-read the Outstanding Balance column in the source text verbatim for this row."
                        ),
                    }]
                    reparsed = reparse_failed_transactions(page_text, critique_items, page_num=page_num)
                    if reparsed and reparsed[0].verbatim_ok:
                        new_tx = reparsed[0].tx
                        new_amount = _to_float(new_tx.raw_amount) or 0.0
                        new_bal = _to_float(new_tx.raw_balance)
                        new_err = _audit_balance(prev_balance, new_amount, new_tx.tx_type, new_bal)
                        if not new_err:
                            tx = new_tx
                            amount = new_amount
                            balance = new_bal
                            audit_err = None
                            item["verified"].tx = new_tx
                            item["verified"].retry_count += 1
                            log_event(
                                "balance_audit_critique_recovered",
                                {
                                    "row_index": item["row_index"],
                                    "recovered_balance": balance,
                                    "retry_count": item["verified"].retry_count,
                                },
                            )

                if audit_err:
                    needs_review = True
                    review_reasons.append(audit_err)

                if balance is not None:
                    prev_balance = balance

                category, cat_reasons, needs_spot_check, tier = _resolve_category(item, batch_results)
                if cat_reasons:
                    needs_review = True
                    review_reasons.extend(cat_reasons)

                if tx_datetime is None:
                    needs_review = True
                    review_reasons.append("Date format unrecognized.")

                review_reason_str = " ".join(review_reasons) if review_reasons else None

                tx_id = db.insert_transaction(
                    conn,
                    statement_hash=extracted.file_hash,
                    statement_row_index=item["row_index"],
                    transaction_datetime=tx_datetime,
                    tx_type=tx.tx_type,
                    amount=amount,
                    balance=balance,
                    fee=None,
                    currency=tx.currency,
                    channel=tx.channel,
                    counterparty=counterparty,
                    memo=tx.memo,
                    category=category,
                    parser_retry_count=item["verified"].retry_count,
                    needs_spot_check=needs_spot_check,
                    raw_extracted_json=json.dumps(tx.model_dump()),
                )
                stats.inserted += 1

                log_event(
                    "transaction_recorded",
                    {
                        "tx_id": tx_id,
                        "statement_hash": extracted.file_hash,
                        "row_index": item["row_index"],
                        "tx_datetime": tx_datetime,
                        "tx_type": tx.tx_type,
                        "amount": amount,
                        "balance": balance,
                        "counterparty": counterparty,
                        "memo": tx.memo,
                        "category": category,
                        "tier": tier,
                        "parser_retry_count": item["verified"].retry_count,
                        "needs_spot_check": needs_spot_check,
                        "needs_review": needs_review,
                        "review_reasons": review_reasons,
                    },
                )

                if needs_review:
                    db.add_pending_review(conn, tx_id, review_reason_str)
                    stats.queued_for_review += 1

        db.mark_statement_processed(
            conn,
            statement_hash=extracted.file_hash,
            filename=extracted.filename,
            account_number=statement_account,
            period=statement_period,
            page_count=extracted.page_count,
        )

        log_event(
            "ingest_completed",
            {
                "filename": extracted.filename,
                "statement_hash": extracted.file_hash,
                "transactions_found": stats.transactions_found,
                "inserted": stats.inserted,
                "total_debit": stats.total_debit,
                "total_credit": stats.total_credit,
                "queued_for_review": stats.queued_for_review,
            },
        )

    return stats


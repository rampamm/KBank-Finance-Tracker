from pathlib import Path
import pytest
from pypdf import PdfWriter

from finance_tracker import db
from finance_tracker.agents.orchestrator import _normalize_datetime, _to_float
from finance_tracker.agents.parser_agent import verify_statement_tx_verbatim
from finance_tracker.models import StatementTransaction
from finance_tracker.pdf_source import (
    StatementDecryptionError,
    extract_statement_pdf,
)

KBANK_STATEMENT_SAMPLE = """
Date      Time/Eff.Date Descriptions          Withdrawal / Deposit Outstanding Balance (THB) Channel          Details
01-09-26                Beginning Balance                          27,561.72
01-09-26  11:05         Debit Card Spending   68.00                27,493.72                 EDC/E-Commerce   Ref Code EDC45445
01-09-26  12:13         Transfer Deposit      38.00                27,531.72                 K PLUS           From X2901 MR. MOHAMED FARZAN++
01-09-26  13:13         Payment               205.44               27,326.28                 EDC/K SHOP/MYQR  Paid for Ref X3001 CHUANBOY-CENTURY ON NUT (A/C Name: YUM CONNECTIVE CO.,
01-09-26  19:49         Error Correction      50.00                27,242.28                 EDC/E-Commerce   Ref Code EDC35445
01-09-26  21:50         Transfer Deposit      1,337.00             28,579.28                 Other Bank       From SCB X6695 นางสาว พิมพ์มาดา จ++
02-09-26  10:58         Payment               50.00                27,579.43                 EDC/K SHOP/MYQR  Paid for Ref X4912 อ่องเหวิน ข้าวมันไก่ (A/C Name: MRS. SUDCHAI
"""


def test_verify_statement_tx_verbatim_accepts_real_values():
    tx1 = StatementTransaction(
        raw_date="01-09-26",
        raw_time="11:05",
        tx_type="debit",
        raw_amount="68.00",
        raw_balance="27,493.72",
        channel="EDC/E-Commerce",
        description="Debit Card Spending",
        details="Ref Code EDC45445",
    )
    assert verify_statement_tx_verbatim(KBANK_STATEMENT_SAMPLE, tx1) == []

    tx_thai = StatementTransaction(
        raw_date="02-09-26",
        raw_time="10:58",
        tx_type="debit",
        raw_amount="50.00",
        raw_balance="27,579.43",
        channel="EDC/K SHOP/MYQR",
        description="Payment",
        details="Paid for Ref X4912 อ่องเหวิน ข้าวมันไก่ (A/C Name: MRS. SUDCHAI",
    )
    assert verify_statement_tx_verbatim(KBANK_STATEMENT_SAMPLE, tx_thai) == []


def test_verify_statement_tx_verbatim_rejects_hallucinated_values():
    tx = StatementTransaction(
        raw_date="01-09-26",
        tx_type="debit",
        raw_amount="999.99",
        raw_balance="100,000.00",
    )
    failures = verify_statement_tx_verbatim(KBANK_STATEMENT_SAMPLE, tx)
    assert "raw_amount" in failures
    assert "raw_balance" in failures


def test_normalize_datetime_handles_statement_formats():
    assert _normalize_datetime("12/09/2026", "14:40:08") == "2026-09-12 14:40:08"
    assert _normalize_datetime("12/09/2026") == "2026-09-12 00:00:00"
    assert _normalize_datetime("01-09-26") == "2026-09-01 00:00:00"


def test_pdf_extraction_and_encryption(tmp_path):
    # 1. Create a dummy unencrypted PDF
    unencrypted_pdf = tmp_path / "statement.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(unencrypted_pdf, "wb") as f:
        writer.write(f)

    extracted = extract_statement_pdf(str(unencrypted_pdf))
    assert extracted.page_count == 1
    assert len(extracted.file_hash) == 64

    # 2. Create an encrypted PDF
    encrypted_pdf = tmp_path / "locked_statement.pdf"
    enc_writer = PdfWriter()
    enc_writer.add_blank_page(width=200, height=200)
    enc_writer.encrypt("testpass123")
    with open(encrypted_pdf, "wb") as f:
        enc_writer.write(f)

    # Valid password
    extracted_enc = extract_statement_pdf(str(encrypted_pdf), password="testpass123")
    assert extracted_enc.page_count == 1

    # Invalid password with aborted attempts
    with pytest.raises(StatementDecryptionError):
        extract_statement_pdf(str(encrypted_pdf), password="wrongpassword", max_password_attempts=0)


def test_db_statement_insertion(tmp_path, monkeypatch):
    test_db = tmp_path / "test_finance.db"
    monkeypatch.setattr("finance_tracker.config.DB_PATH", str(test_db))
    monkeypatch.setattr("finance_tracker.db.DB_PATH", str(test_db))

    db.init_db()
    with db.connect() as conn:
        assert not db.is_statement_processed(conn, "sample_hash_123")
        db.mark_statement_processed(conn, "sample_hash_123", "test.pdf", page_count=1)
        assert db.is_statement_processed(conn, "sample_hash_123")

        tx_id = db.insert_transaction(
            conn,
            statement_hash="sample_hash_123",
            statement_row_index=1,
            transaction_datetime="2026-09-01 10:58:00",
            tx_type="debit",
            amount=50.0,
            balance=27579.43,
            channel="EDC/K SHOP/MYQR",
            counterparty="Paid for Ref X4912 อ่องเหวิน ข้าวมันไก่",
            memo="Chicken Rice",
            category="Food & Dining",
            raw_extracted_json="{}",
        )
        assert tx_id > 0

        # Verify monthly summary
        summary = db.monthly_summary(conn, "2026-09")
        assert len(summary) == 1
        assert summary[0]["category"] == "Food & Dining"
        assert summary[0]["total"] == 50.0

        totals = db.monthly_totals(conn, "2026-09")
        assert totals["debit"] == 50.0
        assert totals["credit"] == 0.0

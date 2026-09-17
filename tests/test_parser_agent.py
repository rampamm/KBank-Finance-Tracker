import os
from pathlib import Path

import pytest

from finance_tracker.agents.orchestrator import _normalize_datetime, _to_float
from finance_tracker.agents.parser_agent import verify_statement_tx_verbatim
from finance_tracker.models import StatementTransaction

SAMPLE_PAGE = "Date: 01-09-26 Time: 11:05 Details: Ref X4912 Amount: 68.00 Balance: 27,493.72"


def test_verify_statement_tx_verbatim_accepts_real_values():
    tx = StatementTransaction(
        raw_date="01-09-26",
        raw_time="11:05",
        tx_type="debit",
        raw_amount="68.00",
        raw_balance="27,493.72",
        channel="K PLUS",
        description="Payment",
    )
    assert verify_statement_tx_verbatim(SAMPLE_PAGE, tx) == []


def test_verify_statement_tx_verbatim_rejects_fabricated_amount():
    tx = StatementTransaction(
        raw_date="01-09-26",
        tx_type="debit",
        raw_amount="999.99",
        raw_balance="27,493.72",
    )
    assert "raw_amount" in verify_statement_tx_verbatim(SAMPLE_PAGE, tx)


def test_normalize_datetime_handles_double_space():
    assert _normalize_datetime("12/09/2026  14:40:08") == "2026-09-12 14:40:08"


def test_normalize_datetime_unknown_format_returns_none():
    assert _normalize_datetime("not a date") is None


def test_to_float_strips_thousands_separator():
    assert _to_float("25,592.65") == 25592.65


@pytest.mark.skip(reason="Legacy email parsing is deprecated and commented out")
def test_parse_email_live():
    pass


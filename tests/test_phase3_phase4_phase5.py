from unittest.mock import MagicMock, patch
from finance_tracker import db
from finance_tracker.agents.parser_agent import (
    parse_statement_page,
    reparse_failed_transactions,
)
from finance_tracker.config import CONFIDENCE_HIGH, CONFIDENCE_LOW
from finance_tracker.models import ParsedStatement, StatementTransaction


def test_fuzzy_payee_lookup(tmp_path, monkeypatch):
    test_db = tmp_path / "test_fuzzy.db"
    monkeypatch.setattr("finance_tracker.config.DB_PATH", str(test_db))
    monkeypatch.setattr("finance_tracker.db.DB_PATH", str(test_db))

    db.init_db()
    with db.connect() as conn:
        db.upsert_payee_category(conn, "STARBUCKS #4471", "Drinks")
        db.upsert_payee_category(conn, "7-ELEVEN THAILAND", "Groceries")

        # 1. Exact match
        res_exact = db.lookup_payee_category_fuzzy(conn, "STARBUCKS #4471")
        assert res_exact is not None
        cat, matched_name, score, match_type = res_exact
        assert cat == "Drinks"
        assert score == 100.0
        assert match_type == "exact"

        # 2. Fuzzy match with branch suffix variation
        res_fuzzy = db.lookup_payee_category_fuzzy(conn, "STARBUCKS 4471 BKK", threshold=80.0)
        assert res_fuzzy is not None
        cat, matched_name, score, match_type = res_fuzzy
        assert cat == "Drinks"
        assert matched_name == "STARBUCKS #4471"
        assert score >= 80.0
        assert match_type == "fuzzy"

        # 3. No match when below threshold
        res_none = db.lookup_payee_category_fuzzy(conn, "CENTRAL DEPARTMENT STORE", threshold=80.0)
        assert res_none is None


def test_delete_processed_statement(tmp_path, monkeypatch):
    test_db = tmp_path / "test_del.db"
    monkeypatch.setattr("finance_tracker.config.DB_PATH", str(test_db))
    monkeypatch.setattr("finance_tracker.db.DB_PATH", str(test_db))

    db.init_db()
    with db.connect() as conn:
        db.mark_statement_processed(conn, "hash_del", "statement.pdf")
        tx_id = db.insert_transaction(
            conn,
            statement_hash="hash_del",
            statement_row_index=1,
            amount=50.0,
            category="Food",
            raw_extracted_json="{}",
        )
        db.add_pending_review(conn, tx_id, "test reason")

        assert db.is_statement_processed(conn, "hash_del")
        assert len(db.list_open_pending_review(conn)) == 1

        db.delete_processed_statement(conn, "hash_del")
        assert not db.is_statement_processed(conn, "hash_del")
        assert len(db.list_open_pending_review(conn)) == 0


def test_spot_check_db_flow(tmp_path, monkeypatch):
    test_db = tmp_path / "test_spot_check.db"
    monkeypatch.setattr("finance_tracker.config.DB_PATH", str(test_db))
    monkeypatch.setattr("finance_tracker.db.DB_PATH", str(test_db))

    db.init_db()
    with db.connect() as conn:
        tx_id = db.insert_transaction(
            conn,
            statement_hash="hash_abc",
            statement_row_index=1,
            transaction_datetime="2026-09-01 12:00:00",
            tx_type="debit",
            amount=150.0,
            balance=5000.0,
            counterparty="Local Bakery",
            category="Food",
            parser_retry_count=0,
            needs_spot_check=1,
            raw_extracted_json="{}",
        )

        open_checks = db.list_open_spot_checks(conn)
        assert len(open_checks) == 1
        assert open_checks[0]["transaction_id"] == tx_id
        assert open_checks[0]["category"] == "Food"

        # Resolve spot check with category update
        db.resolve_spot_check(conn, tx_id, category="Food")
        assert len(db.list_open_spot_checks(conn)) == 0


def test_reparse_failed_transactions():
    sample_text = "01-09-26 11:05 Debit Card Spending 68.00 27,493.72 EDC/E-Commerce Ref Code EDC45445"
    failed_tx = StatementTransaction(
        raw_date="01-09-26",
        raw_time="11:05",
        tx_type="debit",
        raw_amount="999.00",  # Hallucinated value not in text
        raw_balance="27,493.72",
        channel="EDC/E-Commerce",
        description="Debit Card Spending",
    )

    critique_items = [{
        "index": 1,
        "tx": failed_tx,
        "critique": "raw_amount 999.00 not found verbatim in page text",
    }]

    mock_corrected_tx = StatementTransaction(
        raw_date="01-09-26",
        raw_time="11:05",
        tx_type="debit",
        raw_amount="68.00",  # Corrected verbatim value
        raw_balance="27,493.72",
        channel="EDC/E-Commerce",
        description="Debit Card Spending",
    )
    mock_parsed_response = ParsedStatement(
        account_number="123",
        statement_period="01-09-26 to 30-09-26",
        transactions=[mock_corrected_tx],
    )

    mock_agent = MagicMock()
    mock_agent.invoke.return_value = {
        "structured_response": mock_parsed_response,
        "messages": [],
    }

    with patch("finance_tracker.agents.parser_agent._get_statement_agent", return_value=mock_agent):
        reparsed = reparse_failed_transactions(sample_text, critique_items, page_num=1)
        assert len(reparsed) == 1
        assert reparsed[0].verbatim_ok is True
        assert reparsed[0].tx.raw_amount == "68.00"
        assert reparsed[0].retry_count == 1


def test_parse_statement_page_self_critique_recovery():
    sample_text = "01-09-26 11:05 Debit Card Spending 68.00 27,493.72 EDC/E-Commerce Ref Code EDC45445"

    # Initial extraction has a hallucinated amount (999.00)
    tx_bad = StatementTransaction(
        raw_date="01-09-26",
        raw_time="11:05",
        tx_type="debit",
        raw_amount="999.00",
        raw_balance="27,493.72",
        channel="EDC/E-Commerce",
        description="Debit Card Spending",
    )
    # Recovered extraction has true amount (68.00)
    tx_good = StatementTransaction(
        raw_date="01-09-26",
        raw_time="11:05",
        tx_type="debit",
        raw_amount="68.00",
        raw_balance="27,493.72",
        channel="EDC/E-Commerce",
        description="Debit Card Spending",
    )

    mock_agent = MagicMock()
    mock_agent.invoke.side_effect = [
        {"structured_response": ParsedStatement(transactions=[tx_bad]), "messages": []},
        {"structured_response": ParsedStatement(transactions=[tx_good]), "messages": []},
    ]

    with patch("finance_tracker.agents.parser_agent._get_statement_agent", return_value=mock_agent):
        outcome = parse_statement_page(sample_text, page_num=1, max_retries=2)
        assert len(outcome.verified_transactions) == 1
        recovered = outcome.verified_transactions[0]
        assert recovered.verbatim_ok is True
        assert recovered.tx.raw_amount == "68.00"
        assert recovered.retry_count == 1


def test_cli_spot_check(tmp_path, monkeypatch, capsys):
    from finance_tracker.cli import main

    test_db = tmp_path / "test_cli.db"
    monkeypatch.setattr("finance_tracker.config.DB_PATH", str(test_db))
    monkeypatch.setattr("finance_tracker.db.DB_PATH", str(test_db))

    db.init_db()
    with db.connect() as conn:
        tx_id = db.insert_transaction(
            conn,
            statement_hash="hash_spot",
            statement_row_index=1,
            transaction_datetime="2026-09-01 12:00:00",
            tx_type="debit",
            amount=120.0,
            counterparty="Cafe Amazon",
            category="Drinks",
            needs_spot_check=1,
            raw_extracted_json="{}",
        )

    # Simulate pressing Enter to approve current category
    with patch("builtins.input", return_value=""):
        main(["review", "--spot-check"])

    out = capsys.readouterr().out
    assert "1 transaction(s) flagged for spot check" in out
    assert "Approved 'Drinks'" in out

    with db.connect() as conn:
        assert len(db.list_open_spot_checks(conn)) == 0


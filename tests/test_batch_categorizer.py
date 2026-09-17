"""Unit tests for batch categorizer, balance audit, and dynamic taxonomy factory."""

import pytest
from finance_tracker.config import CATEGORIES, UNCATEGORIZED
from finance_tracker.models import (
    CategoryResult,
    CategorizedItem,
    BatchCategoryResults,
    create_category_result_model,
    create_batch_category_result_model,
)
from finance_tracker.agents.orchestrator import (
    _audit_balance,
    _normalize_datetime,
    _resolve_category,
    _to_float,
)


def test_categories_config_validity():
    assert len(CATEGORIES) > 0
    assert "Food" in CATEGORIES
    assert "Transport" in CATEGORIES
    assert "Transfer to me" in CATEGORIES


def test_dynamic_category_result_model_schema():
    custom_cats = ["Food", "Drinks", "CustomCategory"]
    ModelCls = create_category_result_model(custom_cats)
    schema = ModelCls.model_json_schema()
    assert schema["properties"]["category"]["enum"] == custom_cats

    # Instantiation within schema
    valid_instance = ModelCls(category="Food", confidence=0.9, rationale="lunch")
    assert valid_instance.category == "Food"

    # Validation rejection for out-of-taxonomy
    with pytest.raises(Exception):
        ModelCls(category="InvalidCategory", confidence=0.9, rationale="test")


def test_dynamic_batch_category_result_model_schema():
    custom_cats = ["A", "B", "C"]
    BatchCls = create_batch_category_result_model(custom_cats)
    schema = BatchCls.model_json_schema()
    item_def = schema["$defs"]["CategorizedItem"]
    assert item_def["properties"]["category"]["enum"] == custom_cats


def test_audit_balance_debit_pass():
    # prev = 1000, debit = 100, new balance = 900
    assert _audit_balance(1000.0, 100.0, "debit", 900.0) is None


def test_audit_balance_debit_fail():
    # prev = 1000, debit = 100, new balance = 850 (mismatch!)
    res = _audit_balance(1000.0, 100.0, "debit", 850.0)
    assert res is not None
    assert "Balance audit mismatch: expected 900.00, got 850.00." in res


def test_audit_balance_credit_pass():
    # prev = 1000, credit = 500, new balance = 1500
    assert _audit_balance(1000.0, 500.0, "credit", 1500.0) is None


def test_audit_balance_credit_fail():
    # prev = 1000, credit = 500, new balance = 1400 (mismatch!)
    res = _audit_balance(1000.0, 500.0, "credit", 1400.0)
    assert res is not None
    assert "Balance audit mismatch: expected 1,500.00, got 1,400.00." in res


def test_audit_balance_none_cases():
    assert _audit_balance(None, 100.0, "debit", 900.0) is None
    assert _audit_balance(1000.0, 100.0, "debit", None) is None


def test_resolve_category_credit():
    item = {"tx_type": "credit", "counterparty": "Transfer in"}
    cat, reasons, spot_check, tier = _resolve_category(item, {})
    assert cat == "Transfer to me"
    assert reasons == []
    assert spot_check == 0
    assert tier == "credit"


def test_resolve_category_mapped():
    item = {"tx_type": "debit", "mapped_category": "Bills & Utilities"}
    cat, reasons, spot_check, tier = _resolve_category(item, {})
    assert cat == "Bills & Utilities"
    assert reasons == []
    assert spot_check == 0
    assert tier == "exact"


def test_resolve_category_batch_high_confidence():
    item = {
        "tx_type": "debit",
        "needs_batch_llm": True,
        "batch_index": 0,
        "counterparty": "Starbucks",
    }
    batch_res = {0: CategoryResult(category="Drinks", confidence=0.92, rationale="Coffee shop")}
    cat, reasons, spot_check, tier = _resolve_category(item, batch_res)
    assert cat == "Drinks"
    assert reasons == []
    assert spot_check == 0
    assert tier == "high"


def test_resolve_category_batch_medium_confidence():
    item = {
        "tx_type": "debit",
        "needs_batch_llm": True,
        "batch_index": 0,
        "counterparty": "Local Cafe",
    }
    batch_res = {0: CategoryResult(category="Food", confidence=0.75, rationale="Likely food")}
    cat, reasons, spot_check, tier = _resolve_category(item, batch_res)
    assert cat == "Food"
    assert reasons == []
    assert spot_check == 1
    assert tier == "medium"


def test_resolve_category_batch_low_confidence():
    item = {
        "tx_type": "debit",
        "needs_batch_llm": True,
        "batch_index": 0,
        "counterparty": "Unknown Store",
    }
    batch_res = {0: CategoryResult(category="Shopping", confidence=0.55, rationale="Guess")}
    cat, reasons, spot_check, tier = _resolve_category(item, batch_res)
    assert cat == "Shopping"
    assert len(reasons) == 1
    assert "Low confidence (0.55)" in reasons[0]
    assert spot_check == 0
    assert tier == "low"


def test_resolve_category_omitted_from_batch():
    item = {
        "tx_type": "debit",
        "needs_batch_llm": True,
        "batch_index": 5,
        "counterparty": "Missing item",
    }
    cat, reasons, spot_check, tier = _resolve_category(item, {})
    assert cat == UNCATEGORIZED
    assert "Batch categorizer omitted item." in reasons
    assert spot_check == 0
    assert tier == "low"


def test_normalize_datetime_hyphenated_short_year():
    assert _normalize_datetime("12-09-26", "21:27") == "2026-09-12 21:27:00"
    assert _normalize_datetime("01-09-26") == "2026-09-01 00:00:00"


def test_to_float_conversions():
    assert _to_float("3,097.00") == 3097.0
    assert _to_float("0.00") == 0.0
    assert _to_float(None) is None

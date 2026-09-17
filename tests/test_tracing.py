"""Unit tests for structured tracing and token accounting."""

import json
from pathlib import Path
from langchain_core.messages import AIMessage
from finance_tracker.tracing import (
    extract_token_usage,
    find_transaction_trace,
    get_current_log_file,
    init_tracing,
    log_event,
)


def test_init_tracing_creates_log_file(tmp_path, monkeypatch):
    monkeypatch.setattr("finance_tracker.tracing.LOGS_DIR", tmp_path)
    log_file = init_tracing("test-run")
    assert log_file.exists()
    assert log_file.name.startswith("test-run-")
    assert log_file.name.endswith(".jsonl")

    # Verify first entry is run_started
    with open(log_file, "r", encoding="utf-8") as f:
        first_line = json.loads(f.readline())
        assert first_line["event"] == "run_started"
        assert first_line["run_name"] == "test-run"


def test_log_event_writes_valid_json(tmp_path, monkeypatch):
    monkeypatch.setattr("finance_tracker.tracing.LOGS_DIR", tmp_path)
    init_tracing("test-event")
    log_file = get_current_log_file()

    log_event("custom_test_event", {"foo": "bar", "tx_id": 42}, human_msg="hello")

    with open(log_file, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]

    assert len(lines) >= 2
    last_event = lines[-1]
    assert last_event["event"] == "custom_test_event"
    assert last_event["foo"] == "bar"
    assert last_event["tx_id"] == 42


def test_extract_token_usage_with_usage_metadata():
    msg = AIMessage(content="hello", usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})
    res = {"messages": [msg]}
    tokens = extract_token_usage(res)
    assert tokens["input_tokens"] == 120
    assert tokens["output_tokens"] == 30
    assert tokens["total_tokens"] == 150


def test_extract_token_usage_with_response_metadata():
    msg = AIMessage(content="hello", response_metadata={"token_usage": {"prompt_tokens": 80, "completion_tokens": 10, "total_tokens": 90}})
    res = {"messages": [msg]}
    tokens = extract_token_usage(res)
    assert tokens["input_tokens"] == 80
    assert tokens["output_tokens"] == 10
    assert tokens["total_tokens"] == 90


def test_find_transaction_trace(tmp_path, monkeypatch):
    monkeypatch.setattr("finance_tracker.tracing.LOGS_DIR", tmp_path)
    init_tracing("test-find")
    log_event("transaction_recorded", {"tx_id": 99, "amount": 123.45, "category": "Food"})

    traces = find_transaction_trace(99)
    assert len(traces) == 1
    assert traces[0]["tx_id"] == 99
    assert traces[0]["amount"] == 123.45

"""Structured JSONL tracing and token accounting system."""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

LOGS_DIR = Path("logs")

_current_log_file: Optional[Path] = None
_run_start_time: float = 0.0


def _get_timestamp_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def init_tracing(run_name: str = "run") -> Path:
    """Initialize a new trace run file in logs/run-<timestamp>.jsonl."""
    global _current_log_file, _run_start_time
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _current_log_file = LOGS_DIR / f"{run_name}-{_get_timestamp_str()}.jsonl"
    _run_start_time = time.time()
    log_event("run_started", {"run_name": run_name, "started_at": datetime.now(timezone.utc).isoformat()})
    return _current_log_file


def get_current_log_file() -> Path:
    global _current_log_file
    if _current_log_file is None:
        init_tracing()
    return _current_log_file  # type: ignore[return-value]


def log_event(
    event_type: str,
    data: dict[str, Any],
    human_msg: Optional[str] = None,
    level: str = "INFO",
) -> None:
    """Write an event to structured JSONL logs and print a formatted message to console."""
    log_file = get_current_log_file()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec": round(time.time() - _run_start_time, 4) if _run_start_time else 0.0,
        "event": event_type,
        "level": level,
        **data,
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    if human_msg:
        print(human_msg)


def extract_token_usage(result: Any) -> dict[str, int]:
    """Safely extract token usage from LangChain / deepagents invoke result."""
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    if not isinstance(result, dict):
        return usage
    messages = result.get("messages")
    if messages and len(messages) > 0:
        last_msg = messages[-1]
        usage_meta = getattr(last_msg, "usage_metadata", None)
        if isinstance(usage_meta, dict):
            usage["input_tokens"] = usage_meta.get("input_tokens", 0)
            usage["output_tokens"] = usage_meta.get("output_tokens", 0)
            usage["total_tokens"] = usage_meta.get("total_tokens", 0)
        else:
            resp_meta = getattr(last_msg, "response_metadata", {})
            tu = resp_meta.get("token_usage", {}) if isinstance(resp_meta, dict) else {}
            usage["input_tokens"] = tu.get("prompt_tokens", 0)
            usage["output_tokens"] = tu.get("completion_tokens", 0)
            usage["total_tokens"] = tu.get("total_tokens", 0)
    return usage


def get_all_trace_logs() -> list[Path]:
    """Return all jsonl log files sorted descending by modification time."""
    if not LOGS_DIR.exists():
        return []
    return sorted(LOGS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)


def find_transaction_trace(tx_id: int) -> list[dict[str, Any]]:
    """Scan all trace logs to retrieve complete audit trail for a transaction ID."""
    traces = []
    for log_path in get_all_trace_logs():
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    if entry.get("tx_id") == tx_id or entry.get("data", {}).get("tx_id") == tx_id:
                        traces.append(entry)
        except Exception:
            continue
    return traces


def print_transaction_trace(tx_id: int) -> None:
    """Pretty-print the full decision trail for a specific transaction."""
    traces = find_transaction_trace(tx_id)
    if not traces:
        print(f"No trace entries found in logs/ for Transaction #{tx_id}.")
        return

    print(f"\nTrace Trail for Transaction #{tx_id}")
    print("=" * 60)
    for idx, t in enumerate(traces, 1):
        print(f"[{idx}] Event: {t.get('event')} ({t.get('timestamp')})")
        for k, v in t.items():
            if k in ("event", "timestamp", "level", "elapsed_sec", "tx_id"):
                continue
            if isinstance(v, dict):
                print(f"    {k}:")
                for sub_k, sub_v in v.items():
                    print(f"      {sub_k}: {sub_v}")
            else:
                print(f"    {k}: {v}")
        print("-" * 60)

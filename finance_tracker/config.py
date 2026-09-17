"""Central configuration: categories, paths, model settings."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- Database -----------------------------------------------------------

DB_PATH = os.environ.get("FINANCE_TRACKER_DB_PATH", str(PROJECT_ROOT / "finance_tracker.db"))

# --- Statements ---------------------------------------------------------

DEFAULT_STATEMENTS_DIR = os.environ.get(
    "FINANCE_TRACKER_STATEMENTS_DIR", str(PROJECT_ROOT / "statements")
)

# --- LLM --------------------------------------------------------------

# provider:model string accepted by deepagents' create_deep_agent.
PARSER_MODEL = os.environ.get("FINANCE_TRACKER_PARSER_MODEL", "openai:gpt-4.1-mini")
CATEGORIZER_MODEL = os.environ.get("FINANCE_TRACKER_CATEGORIZER_MODEL", "openai:gpt-4.1-mini")

# --- Categories -------------------------------------------------------

# Fixed category taxonomy. "Uncategorized" is reserved: it is only ever set
# by the pipeline itself (never chosen by the categorizer agent) when no
# memo/payee mapping is available.
CATEGORIES = [
    "Food",
    "Drinks",
    "Transport",
    "Bills & Utilities",
    "Groceries",
    "Transfer to me",
    "Transfer to others"
]
UNCATEGORIZED = "Uncategorized"

# --- Confidence Routing Thresholds ------------------------------------
# High (>= 0.85): auto-accepted without review.
# Medium (0.60 - 0.85): auto-accepted but flagged for optional spot check.
# Low (< 0.60): routed to pending_review queue for human intervention.
CONFIDENCE_HIGH = float(os.environ.get("FINANCE_TRACKER_CONFIDENCE_HIGH", "0.85"))
CONFIDENCE_LOW = float(os.environ.get("FINANCE_TRACKER_CONFIDENCE_LOW", "0.60"))


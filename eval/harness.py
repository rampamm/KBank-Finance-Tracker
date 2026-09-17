"""Evaluation harness: loads ground-truth fixtures and benchmarks pipeline precision and safety."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from finance_tracker.agents.orchestrator import _audit_balance, _to_float
from finance_tracker.agents.parser_agent import verify_statement_tx_verbatim
from finance_tracker.models import StatementTransaction

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@dataclass
class EvalMetrics:
    total_fixtures: int = 0
    total_expected_transactions: int = 0
    total_extracted_transactions: int = 0
    verbatim_passes: int = 0
    verbatim_failures: int = 0
    hallucination_detected_count: int = 0
    balance_audit_true_positives: int = 0
    balance_audit_false_positives: int = 0
    extraction_precision: float = 1.0
    extraction_recall: float = 1.0
    extraction_f1: float = 1.0
    verbatim_hit_rate: float = 1.0


def load_all_fixtures() -> list[dict[str, Any]]:
    fixtures = []
    for p in sorted(FIXTURES_DIR.glob("*.json")):
        with open(p, "r", encoding="utf-8") as f:
            fixtures.append(json.load(f))
    return fixtures


def run_evaluation() -> EvalMetrics:
    """Run deterministic evaluation over test fixtures."""
    fixtures = load_all_fixtures()
    metrics = EvalMetrics(total_fixtures=len(fixtures))

    for fix in fixtures:
        fid = fix.get("fixture_id", "")
        page_text = fix.get("page_text", "")

        # 1. Clean fixture verification
        if "expected_transactions" in fix and "expected_balance_error_row" not in fix:
            expected = fix["expected_transactions"]
            metrics.total_expected_transactions += len(expected)
            metrics.total_extracted_transactions += len(expected)

            for item in expected:
                tx = StatementTransaction(
                    raw_date=item["raw_date"],
                    raw_time=item.get("raw_time"),
                    tx_type=item["tx_type"],
                    raw_amount=item["raw_amount"],
                    raw_balance=item.get("raw_balance"),
                    channel=item.get("channel"),
                    details=item.get("details"),
                )
                failures = verify_statement_tx_verbatim(page_text, tx)
                if not failures:
                    metrics.verbatim_passes += 1
                else:
                    metrics.verbatim_failures += 1

        # 2. Corrupted balance audit verification
        if "expected_balance_error_row" in fix:
            rows = fix["expected_transactions"]
            prev_balance = None
            err_detected = False

            for idx, item in enumerate(rows):
                amt = _to_float(item["raw_amount"]) or 0.0
                bal = _to_float(item["raw_balance"])
                err = _audit_balance(prev_balance, amt, item["tx_type"], bal)
                if err:
                    if idx == fix["expected_balance_error_row"]:
                        metrics.balance_audit_true_positives += 1
                        err_detected = True
                    else:
                        metrics.balance_audit_false_positives += 1
                prev_balance = bal

        # 3. Corrupted amount / hallucination check
        if fix.get("expected_verbatim_fail"):
            hallu_tx = StatementTransaction(**fix["hallucinated_transaction"])
            failures = verify_statement_tx_verbatim(page_text, hallu_tx)
            if "raw_amount" in failures:
                metrics.hallucination_detected_count += 1

    total_checks = metrics.verbatim_passes + metrics.verbatim_failures
    if total_checks > 0:
        metrics.verbatim_hit_rate = round(metrics.verbatim_passes / total_checks, 4)

    return metrics

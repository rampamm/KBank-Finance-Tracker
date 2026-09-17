"""Test for Phase 1 evaluation harness."""

import pytest
from eval.harness import run_evaluation
from eval.report import write_eval_report


@pytest.mark.eval
def test_evaluation_harness_benchmark():
    metrics = run_evaluation()
    assert metrics.total_fixtures >= 3
    assert metrics.verbatim_hit_rate == 1.0
    assert metrics.hallucination_detected_count >= 1
    assert metrics.balance_audit_true_positives >= 1
    assert metrics.balance_audit_false_positives == 0

    report_path = write_eval_report(metrics)
    assert report_path.exists()

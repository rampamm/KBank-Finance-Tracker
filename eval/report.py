"""Evaluation reporting: records benchmark runs to markdown and history CSV."""

import csv
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from eval.harness import EvalMetrics, run_evaluation

EVAL_DIR = Path(__file__).parent
RUNS_DIR = EVAL_DIR / "runs"
HISTORY_CSV = EVAL_DIR / "history.csv"


def write_eval_report(metrics: EvalMetrics) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_file = RUNS_DIR / f"eval-{timestamp}.md"

    md_content = f"""# Evaluation Benchmark Report: {timestamp}

- **Fixtures Evaluated:** {metrics.total_fixtures}
- **Expected Transactions:** {metrics.total_expected_transactions}
- **Verbatim Hit Rate:** {metrics.verbatim_hit_rate * 100:.1f}%
- **Verbatim Passes:** {metrics.verbatim_passes}
- **Verbatim Failures:** {metrics.verbatim_failures}
- **Hallucinations Caught:** {metrics.hallucination_detected_count}
- **Balance Audit True Positives:** {metrics.balance_audit_true_positives}
- **Balance Audit False Positives:** {metrics.balance_audit_false_positives}

## Quality Metrics
- **Extraction Precision:** {metrics.extraction_precision:.2f}
- **Extraction Recall:** {metrics.extraction_recall:.2f}
- **Extraction F1:** {metrics.extraction_f1:.2f}
"""
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(md_content)

    # Append to history.csv
    file_exists = HISTORY_CSV.exists()
    fieldnames = ["timestamp"] + list(asdict(metrics).keys())
    with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        row = {"timestamp": timestamp, **asdict(metrics)}
        writer.writerow(row)

    return report_file


if __name__ == "__main__":
    m = run_evaluation()
    path = write_eval_report(m)
    print(f"Benchmark completed. Report written to {path}")

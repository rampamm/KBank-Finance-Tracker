"""Command-line entry points: ingest (PDF statement), review (interactive), report (on demand)."""

import argparse
import sys

from finance_tracker import db
from finance_tracker.analysis import print_monthly_report
from finance_tracker.config import CATEGORIES


def cmd_ingest(args):
    from finance_tracker.agents.orchestrator import run_statement_ingest

    stats = run_statement_ingest(
        pdf_path=args.file,
        password=args.password,
        force=getattr(args, "force", False),
    )
    print()
    print("Ingestion summary:")
    print(f"  Statement file:         {stats.filename}")
    print(f"  Pages scanned:          {stats.pages_scanned}")
    if stats.skipped_already_processed:
        print("  Status:                 Already processed (skipped duplicate)")
        return
    print(f"  Transactions found:     {stats.transactions_found}")
    print(f"  Inserted:               {stats.inserted}")
    print(f"  Total expenses (debit): {stats.total_debit:,.2f} THB")
    print(f"  Total deposits (credit):{stats.total_credit:,.2f} THB")
    print(f"  Queued for review:      {stats.queued_for_review}")
    if stats.queued_for_review:
        print("\nRun `python main.py review` to resolve queued items.")


def cmd_review(args):
    db.init_db()
    with db.connect() as conn:
        if getattr(args, "spot_check", False):
            spot_checks = db.list_open_spot_checks(conn)
            if not spot_checks:
                print("No transactions flagged for spot check.")
                return

            print(f"{len(spot_checks)} transaction(s) flagged for spot check (medium confidence).\n")
            for row in spot_checks:
                print("-" * 50)
                tag = row["channel"] or row["email_subject"] or "Statement TX"
                tx_type = row["tx_type"] or "debit"
                print(f"Transaction #{row['id']} [{tag}] ({tx_type})")
                print(f"  Amount:       {row['amount']} {row['currency']}")
                print(f"  Datetime:     {row['transaction_datetime']}")
                print(f"  Counterparty: {row['counterparty']}")
                print(f"  Memo:         {row['memo']}")
                print(f"  Current Cat:  {row['category']}")
                print(f"  Press Enter to approve [{row['category']}], or type new category ({', '.join(CATEGORIES)}), or 'skip'")

                choice = input("  Spot-check action: ").strip()
                if choice.lower() == "skip":
                    continue
                if not choice:
                    db.resolve_spot_check(conn, row["id"])
                    print(f"  Approved '{row['category']}'.")
                elif choice in CATEGORIES:
                    db.resolve_spot_check(conn, row["id"], category=choice)
                    if row["counterparty"]:
                        db.upsert_payee_category(conn, row["counterparty"], choice)
                        print(f"  Updated to '{choice}' and saved rule for '{row['counterparty']}'.")
                    else:
                        print(f"  Updated to '{choice}'.")
                else:
                    print(f"  '{choice}' is not a recognized category; skipping.")
            return

        pending = db.list_open_pending_review(conn)
        if not pending:
            print("Nothing pending review.")
            return

        print(f"{len(pending)} item(s) pending review.\n")
        for row in pending:
            print("-" * 50)
            tag = row["channel"] or row["email_subject"] or "Statement TX"
            tx_type = row["tx_type"] or "debit"
            print(f"Transaction #{row['id']} [{tag}] ({tx_type})")
            print(f"  Amount:       {row['amount']} {row['currency']}")
            print(f"  Datetime:     {row['transaction_datetime']}")
            print(f"  Counterparty: {row['counterparty']}")
            print(f"  Memo:         {row['memo']}")
            print(f"  Reason:       {row['reason']}")
            print(f"  Categories:   {', '.join(CATEGORIES)}, or 'skip'")

            choice = input("  Assign category: ").strip()
            if not choice or choice.lower() == "skip":
                continue
            if choice not in CATEGORIES:
                print(f"  '{choice}' is not a recognized category; skipping.")
                continue

            db.resolve_pending_review(conn, row["pending_id"], choice)
            if row["counterparty"]:
                db.upsert_payee_category(conn, row["counterparty"], choice)
                print(f"  Saved. Future transfers to '{row['counterparty']}' will default to '{choice}'.")
            else:
                print("  Saved.")



def cmd_report(args):
    print_monthly_report(args.year_month)


def cmd_trace(args):
    from finance_tracker.tracing import print_transaction_trace

    print_transaction_trace(args.id)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="finance_tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest a bank statement PDF and record transactions")
    p_ingest.add_argument("file", help="Path to the bank statement PDF file (e.g. statement.pdf)")
    p_ingest.add_argument(
        "--password",
        default=None,
        help="Optional PDF password (if omitted and file is locked, you will be prompted securely)",
    )
    p_ingest.add_argument(
        "--force",
        action="store_true",
        help="Force re-processing of a statement even if already ingested, replacing previous records",
    )
    p_ingest.set_defaults(func=cmd_ingest)

    p_review = sub.add_parser("review", help="Interactively resolve uncategorized transactions")
    p_review.add_argument(
        "--spot-check",
        action="store_true",
        help="Review medium-confidence auto-categorized transactions that were flagged for spot-checking",
    )
    p_review.set_defaults(func=cmd_review)

    p_report = sub.add_parser("report", help="Print a monthly spending-by-category summary")
    p_report.add_argument("year_month", help="e.g. 2026-09")
    p_report.set_defaults(func=cmd_report)

    p_trace = sub.add_parser("trace", help="Pretty-print decision and execution trace for a transaction ID")
    p_trace.add_argument("id", type=int, help="Transaction ID (e.g. 71)")
    p_trace.set_defaults(func=cmd_trace)

    args = parser.parse_args(argv)
    args.func(args)



if __name__ == "__main__":
    sys.exit(main())

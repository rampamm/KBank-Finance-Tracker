"""Monthly spending-by-category and income report, generated on demand."""

from finance_tracker import db


def print_monthly_report(year_month: str):
    db.init_db()
    with db.connect() as conn:
        rows = db.monthly_summary(conn, year_month)
        totals = db.monthly_totals(conn, year_month)

    total_expense = sum(r["total"] for r in rows) if rows else 0.0
    total_income = totals.get("credit", 0.0)

    if total_expense == 0.0 and total_income == 0.0:
        print(f"No transactions recorded for {year_month}.")
        return

    net = total_income - total_expense

    print(f"\nFinancial summary for {year_month}")
    print("=" * 48)
    if rows:
        print(f"{'Category':<22} {'Amount (THB)':>12}  {'Share'}")
        print("-" * 48)
        for row in rows:
            pct = (row["total"] / total_expense * 100) if total_expense else 0
            print(f"{row['category']:<22} {row['total']:>12,.2f}  ({pct:4.1f}%)")
        print("-" * 48)
        print(f"{'Total Expenses':<22} {total_expense:>12,.2f}")
    if total_income > 0:
        print(f"{'Total Income':<22} {total_income:>12,.2f}")
        print(f"{'Net Savings':<22} {net:>12,.2f}")
    print("=" * 48)

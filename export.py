"""CSV export and weekly summary.

The CSV deliberately carries source_type and confirmation_status columns so an
accountant can judge how reliable each row is (a typed mileage estimate is not
the same as a confirmed receipt).
"""
import csv
import io

from sqlalchemy.orm import Session

from models import Record, User

CSV_COLUMNS = [
    "date", "record_type", "platform_or_vendor", "amount_gbp", "miles",
    "category", "source_type", "confirmation_status", "confidence",
    "original_file_reference", "notes",
]


def _exportable(db: Session, user_id: int) -> list[Record]:
    return (
        db.query(Record)
        .filter(Record.user_id == user_id,
                Record.confirmation_status.in_(["confirmed", "estimated"]))
        .order_by(Record.record_date.asc(), Record.id.asc())
        .all()
    )


def build_csv(db: Session, user_id: int) -> str:
    rows = _exportable(db, user_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow([
            r.record_date, r.record_type, r.platform_or_vendor,
            f"{r.amount:.2f}" if r.amount is not None else "",
            f"{r.miles:.1f}" if r.miles is not None else "",
            r.category, r.source_type, r.confirmation_status,
            f"{r.confidence:.2f}", r.original_media_url, r.notes,
        ])
    return buf.getvalue()


def weekly_summary(db: Session, user_id: int) -> str:
    """A short text recap to send over WhatsApp (the 'tax-saved counter' hook)."""
    rows = _exportable(db, user_id)
    income = sum(r.amount or 0 for r in rows if r.record_type == "income")
    expenses = sum(r.amount or 0 for r in rows if r.record_type == "expense")
    miles = sum(r.miles or 0 for r in rows if r.record_type == "mileage")

    # 2026/27 simplified mileage: 55p for the first 10,000 business miles, 25p after.
    if miles <= 10_000:
        mileage_deduction = miles * 0.55
    else:
        mileage_deduction = 10_000 * 0.55 + (miles - 10_000) * 0.25

    taxable = max(0.0, income - expenses - mileage_deduction)
    return (
        f"Your records so far:\n"
        f"• Income logged: £{income:,.2f}\n"
        f"• Expenses logged: £{expenses:,.2f}\n"
        f"• Business miles: {miles:,.0f}  (≈ £{mileage_deduction:,.2f} deduction)\n"
        f"• Estimated taxable income: £{taxable:,.2f}\n\n"
        f"Indicative only, based on what you've confirmed — not tax advice."
    )

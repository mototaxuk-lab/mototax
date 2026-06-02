"""CSV export and weekly summary.

The CSV deliberately carries source_type and confirmation_status columns so an
accountant can judge how reliable each row is (a typed mileage estimate is not
the same as a confirmed receipt).
"""
import csv
import io

from sqlalchemy.orm import Session

import tax
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


def weekly_summary(db: Session, user: User) -> str:
    """A short text recap to send over WhatsApp (the 'tax-saved counter' hook).

    Uses the user's vehicle type for the mileage rate and their tax-estimate
    level for the rough tax-benefit figure.
    """
    rows = _exportable(db, user.id)
    income = sum(r.amount or 0 for r in rows if r.record_type == "income")
    expenses = sum(r.amount or 0 for r in rows if r.record_type == "expense")
    miles = sum(r.miles or 0 for r in rows if r.record_type == "mileage")

    deduction = tax.mileage_deduction(miles, user.vehicle_type)
    lines = [
        "Your records so far:",
        f"• Income logged: £{income:,.2f}",
        f"• Expenses logged (for accountant review): £{expenses:,.2f}",
        f"• Business miles: {miles:,.0f}  (≈ £{deduction:,.2f} mileage deduction)",
    ]
    if user.tax_rate:
        benefit = tax.tax_benefit(deduction, user.tax_rate)
        lines.append(
            f"• Estimated tax benefit from mileage: up to ~£{benefit:,.2f} "
            f"(at {user.tax_rate * 100:.0f}% tax rate)"
        )
    lines.append("\nIndicative only, based on what you've confirmed — not tax advice.")
    return "\n".join(lines)

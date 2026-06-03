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
    "date", "record_type", "vehicle_type", "platform_or_vendor", "amount_gbp", "miles",
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


def _miles_by_vehicle(rows: list[Record], default_type: str | None) -> dict[str, float]:
    """Sum confirmed mileage per vehicle type (older rows fall back to the user's)."""
    agg: dict[str, float] = {}
    for r in rows:
        if r.record_type != "mileage":
            continue
        vt = tax.normalise_vehicle(r.vehicle_type or default_type)
        agg[vt] = agg.get(vt, 0.0) + (r.miles or 0.0)
    return agg


def build_csv(db: Session, user_id: int) -> str:
    rows = _exportable(db, user_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow([
            r.record_date, r.record_type, r.vehicle_type or "", r.platform_or_vendor,
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

    by_vehicle = _miles_by_vehicle(rows, user.vehicle_type)
    total_miles = sum(by_vehicle.values())
    deduction = sum(tax.mileage_deduction(vt, m) for vt, m in by_vehicle.items())

    lines = [
        "Your records so far:",
        f"• Income logged: £{income:,.2f}",
        f"• Expenses logged (for accountant review): £{expenses:,.2f}",
        f"• Business miles: {total_miles:,.0f}  (≈ £{deduction:,.2f} mileage deduction)",
    ]
    # Break the mileage down per vehicle when more than one has been used.
    used = {vt: m for vt, m in by_vehicle.items() if m > 0}
    if len(used) > 1:
        for vt, m in sorted(used.items(), key=lambda x: -x[1]):
            lines.append(
                f"    – {tax.emoji(vt)} {tax.label(vt)}: {m:,.0f} mi "
                f"(£{tax.mileage_deduction(vt, m):,.2f})"
            )
    if user.tax_rate:
        benefit = tax.tax_benefit(deduction, user.tax_rate)
        lines.append(
            f"• Estimated tax benefit from mileage: up to ~£{benefit:,.2f} "
            f"(at {user.tax_rate * 100:.0f}% tax rate)"
        )
    if total_miles > 0:
        lines.append("\n" + tax.rate_line(user.vehicle_type))
    lines.append("\nIndicative only, based on what you've confirmed — not tax advice.")
    return "\n".join(lines)


def vehicles_overview(db: Session, user: User) -> str:
    """The 'vehicles' command: a per-vehicle tab of miles and deduction."""
    by_vehicle = _miles_by_vehicle(_exportable(db, user.id), user.vehicle_type)
    current = tax.normalise_vehicle(user.vehicle_type)
    by_vehicle.setdefault(current, 0.0)  # always show the active vehicle

    lines = ["Your vehicles:"]
    for vt, miles in sorted(by_vehicle.items(), key=lambda x: -x[1]):
        deduction = tax.mileage_deduction(vt, miles)
        mark = "  ← current" if vt == current else ""
        lines.append(
            f"• {tax.emoji(vt)} {tax.label(vt).capitalize()} — "
            f"{miles:,.0f} miles (£{deduction:,.2f}){mark}"
        )
    lines.append("\nSwitch with \"use car\", \"use motorbike\" or \"use bike\".")
    return "\n".join(lines)

"""Weekly 'send your miles' reminder.

Run modes:
- In-process (default): the FastAPI app schedules send_reminders() weekly via
  APScheduler (see main.py). No extra infrastructure needed.
- Standalone: `python reminders.py` sends once and exits — suitable for a
  Railway cron service if you outgrow the in-process scheduler.

Delivery note: a reminder goes to people who haven't logged recently, i.e. who
are almost always OUTSIDE the 24-hour WhatsApp service window. WhatsApp only
allows business-initiated messages there via an APPROVED TEMPLATE. Set
REMINDER_TEMPLATE_SID once your template is approved; otherwise the freeform
fallback only delivers inside the 24h window (or the Twilio sandbox).
"""
import datetime as dt

import config
import tax
import twilio_client as wa
from models import Record, SessionLocal, User, now


def _period_line(monthly: bool) -> str:
    d = dt.date.today()
    if monthly:
        start = d.replace(day=1)
        nxt = (start.replace(year=start.year + 1, month=1) if start.month == 12
               else start.replace(month=start.month + 1))
        end = nxt - dt.timedelta(days=1)
    else:
        start = d - dt.timedelta(days=d.weekday())
        end = start + dt.timedelta(days=6)
    return f"Period: {start.day} {start:%b} – {end.day} {end:%b %Y}"


def reminder_body(user: User) -> str:
    """Mileage check-in reminder (Flow B §1/§2), weekly or monthly per the user."""
    monthly = (getattr(user, "log_frequency", "weekly") or "weekly") == "monthly"
    if monthly:
        return (
            "Monthly mileage check-in 🔥\n\n"
            "Send your delivery miles for this month.\n\n"
            f"{_period_line(True)}\n\n"
            "Example:\n\"520 miles\"\n\n"
            "Weekly logging is usually fresher, but monthly is fine if that works "
            "better for you."
        )
    return (
        "Quick weekly check-in 🔥\n\n"
        "Send your delivery miles for this week.\n\n"
        f"{_period_line(False)}\n\n"
        "Example:\n\"120 miles\"\n\n"
        "Add earnings screenshots or typed earnings if you want your real "
        "take-home estimate."
    )


_WEEKDAY_ABBR = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def due_users(db) -> list[User]:
    """Onboarded users due a reminder today: reminders active, scheduled for today's
    weekday, and no mileage logged within REMIND_SKIP_DAYS."""
    cutoff = now() - dt.timedelta(days=config.REMIND_SKIP_DAYS)
    today = _WEEKDAY_ABBR[dt.date.today().weekday()]
    users = db.query(User).filter(User.onboarding_step == "done").all()
    due = []
    for user in users:
        if (getattr(user, "reminder_status", "active") or "active") == "off":
            continue
        if (getattr(user, "reminder_day", "sun") or "sun") != today:
            continue
        logged_recently = (
            db.query(Record)
            .filter(
                Record.user_id == user.id,
                Record.record_type == "mileage",
                Record.created_at >= cutoff,
            )
            .first()
        )
        if not logged_recently:
            due.append(user)
    return due


def send_reminders() -> dict:
    """Send the weekly reminder to everyone due. Returns a small result summary."""
    db = SessionLocal()
    sent = failed = 0
    try:
        users = due_users(db)
        for user in users:
            try:
                if config.REMINDER_TEMPLATE_SID:
                    # Approved templates own their copy; pass the active vehicle as
                    # variable {{1}} so a template can include it if it wants to.
                    wa.send_whatsapp_template(
                        user.whatsapp_number,
                        config.REMINDER_TEMPLATE_SID,
                        {"1": f"{tax.emoji(user.vehicle_type)} {tax.label(user.vehicle_type)}"},
                    )
                else:
                    wa.send_whatsapp(user.whatsapp_number, reminder_body(user))
                sent += 1
            except Exception as exc:  # one bad number shouldn't stop the batch
                failed += 1
                print(f"[reminders] failed for {user.whatsapp_number}: {exc!r}")
        result = {"due": len(users), "sent": sent, "failed": failed}
        print(f"[reminders] {result}")
        return result
    finally:
        db.close()


if __name__ == "__main__":
    send_reminders()

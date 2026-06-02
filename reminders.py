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


def reminder_body(user: User) -> str:
    """Per-user reminder text, naming the currently-active vehicle as a nudge to
    switch before logging if they changed vehicle."""
    vehicle = ""
    if user.vehicle_type:
        vehicle = f" — currently logging to {tax.emoji(user.vehicle_type)} {tax.label(user.vehicle_type)}"
    return (
        "Quick weekly check-in 🔥\n"
        f"Send your delivery miles for this week{vehicle}.\n"
        "(Type \"use bike\" first if you switched vehicle.)\n"
        "Example: \"120 miles\"\n"
        "Add earnings screenshots if you want your real take-home estimate."
    )


def due_users(db) -> list[User]:
    """Onboarded users who haven't logged mileage within REMIND_SKIP_DAYS."""
    cutoff = now() - dt.timedelta(days=config.REMIND_SKIP_DAYS)
    users = db.query(User).filter(User.onboarding_step == "done").all()
    due = []
    for user in users:
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

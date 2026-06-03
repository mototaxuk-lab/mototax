"""FastAPI entrypoint.

Routes
------
GET  /                       health/info
GET  /health                 readiness probe
POST /webhook/whatsapp       Twilio inbound messages (configure this URL in Twilio)
GET  /export/{token}         downloads a user's CSV via a one-off link

The webhook acknowledges Twilio immediately and does the real work
(media download → Claude → DB → reply) in a background task, so we never
hit Twilio's request timeout even if extraction takes a couple of seconds.
"""
import datetime as dt
import re

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import export
import extract
import reminders
import tax
import twilio_client as wa
from models import (
    ExportLink, Record, SessionLocal, User, get_or_create_user, init_db,
    latest_awaiting_vehicle, latest_editing, latest_pending, make_export_link, now,
)

app = FastAPI(title="Courier Tax & Records Assistant")

WELCOME = (
    "Welcome 👋\n\n"
    "Doing deliveries is busy enough — your tax records shouldn't become another job.\n\n"
    "I help you keep your delivery records simple, week by week, directly in WhatsApp.\n\n"
    "Send your miles, earnings screenshots or typed earnings, and courier expenses as "
    "you go. I'll organise them into clear summaries so you can see what you actually "
    "kept — and have cleaner records when tax time comes."
)

BOUNDARY = (
    "For vehicle costs, I currently use simplified mileage.\n\n"
    "That means you track delivery miles instead of every petrol, insurance, repair "
    "or servicing receipt.\n\n"
    "I don't file your tax return or give formal tax advice. Your records can be "
    "reviewed by you or your accountant before filing."
)

TRUST = (
    "You stay in control.\n\n"
    "Before anything is added to your records, you can confirm, edit or delete it.\n\n"
    "To set you up, I only need two quick answers."
)

VEHICLE_QUESTION = (
    "Q1. What do you mainly use for deliveries?\n\n"
    "1. Car / van\n"
    "2. Motorbike / moped\n"
    "3. Bicycle / e-bike\n\n"
    "Reply 1, 2 or 3. You can add another vehicle later."
)

_VEHICLE_LABELS = {
    "car_van": "Car / van",
    "motorbike": "Motorbike / moped",
    "bicycle": "Bicycle / e-bike",
}


def vehicle_confirmation(vehicle: str) -> str:
    return (
        f"Got it — I'll use {_VEHICLE_LABELS[vehicle]} as your main delivery vehicle.\n\n"
        "You can change this or add another vehicle later in settings."
    )


TAX_QUESTION = (
    "Q2. For rough tax-benefit estimates, which tax rate should I use?\n\n"
    "1. Basic estimate — 20%\n"
    "   Usually total annual income around £12,571–£50,270. Choose this if unsure.\n\n"
    "2. Higher estimate — 40%\n"
    "   Usually total annual income around £50,271–£125,140.\n\n"
    "3. Likely no income tax — 0%\n"
    "   Usually total annual income below £12,570.\n\n"
    "Reply 1, 2 or 3.\n\n"
    "This is only used for rough estimates. It is not tax advice. Your final tax "
    "position depends on your total income and personal circumstances."
)

_TAX_LABELS = {
    0.20: "Basic estimate — 20%",
    0.40: "Higher estimate — 40%",
    0.0: "Likely no income tax — 0%",
}


def tax_confirmation(rate: float) -> str:
    return (
        f"Got it — I'll use {_TAX_LABELS[rate]} for rough tax-benefit estimates.\n\n"
        "You can change this later in settings."
    )


SETUP_COMPLETE = (
    "You're set up ✅\n\n"
    "Every Sunday evening, I'll remind you to send your delivery miles.\n\n"
    "Example:\n"
    "\"120 miles\"\n\n"
    "Add earnings screenshots or type your earnings if you want your real take-home "
    "estimate.\n\n"
    "Add courier-related expenses if you want them included in your record pack for "
    "accountant review.\n\n"
    "You can type SETTINGS anytime to change your vehicle type, tax estimate level or "
    "reminder settings."
)

FREE_TRIAL = (
    "Your first month is free.\n\n"
    "After that, it's £5/month to keep weekly tracking, monthly exports and annual "
    "records."
)

FIRST_ACTION = (
    "Ready when you are.\n\n"
    "You can start by sending this week's delivery miles.\n\n"
    "Example:\n"
    "\"120 miles\""
)

WHAT_IS_THIS = (
    "I help delivery couriers organise delivery-work records in WhatsApp.\n\n"
    "You can send:\n"
    "• delivery miles\n"
    "• earnings screenshots or typed earnings\n"
    "• courier-related expenses\n\n"
    "I organise them into weekly, monthly and annual summaries.\n\n"
    "I use simplified mileage for vehicle costs, and I don't file tax returns or give "
    "formal tax advice."
)

SKIP_REPLY = (
    "No problem.\n\n"
    "To use mileage calculations, I'll need your main vehicle type and tax estimate "
    "level first.\n\n"
    "Type START when you're ready."
)

HELP = (
    "Send a receipt or earnings screenshot, or type your mileage like \"145 miles\".\n"
    "After I read something, reply 1 to confirm, 2 to edit, or 3 to delete.\n"
    "Got more than one vehicle? Type \"use car\", \"use motorbike\" or \"use bike\" to switch, "
    "or VEHICLES to see them.\n"
    "Type CSV for your export, SUMMARY for your totals, or SETTINGS to update your profile."
)

# --- Onboarding answer parsing -------------------------------------------------

def _parse_vehicle(text: str) -> str | None:
    t = text.strip().lower()
    if t in ("1",) or "car" in t or "van" in t:
        return "car_van"
    if t in ("2",) or "motorbike" in t or "motorcycle" in t or "moped" in t:
        return "motorbike"
    if t in ("3",) or "bicycle" in t or "cycle" in t or "e-bike" in t or "ebike" in t or "bike" in t:
        return "bicycle"
    return None


def _parse_tax_rate(text: str) -> float | None:
    t = text.strip().lower().rstrip("%")
    if t in ("1", "20", "basic"):
        return 0.20
    if t in ("2", "40", "higher"):
        return 0.40
    if t in ("3", "0", "none"):
        return 0.0
    return None


_scheduler: BackgroundScheduler | None = None


@app.on_event("startup")
def _startup() -> None:
    init_db()
    global _scheduler
    if config.REMINDERS_ENABLED and _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="UTC")
        _scheduler.add_job(
            reminders.send_reminders,
            CronTrigger(
                day_of_week=config.REMINDER_DAY,
                hour=config.REMINDER_HOUR_UTC,
                minute=0,
                timezone="UTC",
            ),
            id="weekly_reminder",
            misfire_grace_time=3600,
            replace_existing=True,
        )
        _scheduler.start()
        print(f"[startup] weekly reminder scheduled: {config.REMINDER_DAY} "
              f"{config.REMINDER_HOUR_UTC:02d}:00 UTC")


@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return "Courier Tax & Records Assistant — running."


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    signature = request.headers.get("X-Twilio-Signature", "")

    if not wa.verify_signature(signature, params):
        return Response(status_code=403)

    background_tasks.add_task(handle_inbound, params)
    # Empty TwiML tells Twilio "received, nothing to say inline".
    return Response(content="<Response></Response>", media_type="application/xml")


@app.post("/internal/run-reminders")
def run_reminders(request: Request):
    """Manually fire the weekly reminder. Guarded by CRON_SECRET (?key=...)."""
    key = request.query_params.get("key", "")
    if not config.CRON_SECRET or key != config.CRON_SECRET:
        return Response(status_code=403)
    return reminders.send_reminders()


@app.get("/export/{token}")
def export_csv(token: str):
    db = SessionLocal()
    try:
        link = db.get(ExportLink, token)
        if not link:
            return PlainTextResponse("Link expired or not found.", status_code=404)
        # Expire links after 24 hours.
        age = now() - link.created_at.replace(tzinfo=dt.timezone.utc)
        if age > dt.timedelta(hours=24):
            return PlainTextResponse("Link expired.", status_code=410)
        csv_text = export.build_csv(db, link.user_id)
        filename = f"courier-records-{dt.date.today().isoformat()}.csv"
        return Response(
            content=csv_text,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        db.close()


# --------------------------------------------------------------------------
# Message handling (runs in a background thread)
# --------------------------------------------------------------------------

def handle_inbound(params: dict) -> None:
    db = SessionLocal()
    try:
        from_field = params.get("From", "")            # "whatsapp:+447700900000"
        number = from_field.replace("whatsapp:", "").strip()
        body = (params.get("Body") or "").strip()
        num_media = int(params.get("NumMedia", "0") or "0")

        user, created = get_or_create_user(db, number)

        # Testing helper: wipe this user's data and restart onboarding from scratch.
        if (params.get("Body") or "").strip().lower() == "restart":
            db.query(Record).filter(Record.user_id == user.id).delete()
            db.query(ExportLink).filter(ExportLink.user_id == user.id).delete()
            db.delete(user)
            db.commit()
            user, _ = get_or_create_user(db, number)
            wa.send_whatsapp(number, "🔄 Restarted. Starting from scratch.\n")
            wa.send_whatsapp(number, WELCOME)
            wa.send_whatsapp(number, BOUNDARY)
            wa.send_whatsapp(number, TRUST)
            wa.send_whatsapp(number, VEHICLE_QUESTION)
            return

        # Testing helper: bail out of any in-progress step (a pending record, an
        # edit, or a "which vehicle?" prompt) back to a clean idle state, without
        # deleting confirmed history. Works from any state — unlike CANCEL, which
        # only works mid-edit. (RESTART wipes everything; EXIT just clears the
        # current interaction.)
        if (params.get("Body") or "").strip().lower() in ("exit", "quit", "end"):
            cleared = (
                db.query(Record)
                .filter(
                    Record.user_id == user.id,
                    Record.confirmation_status.in_(
                        ("pending", "editing", "awaiting_vehicle")
                    ),
                )
                .update({Record.confirmation_status: "rejected"}, synchronize_session=False)
            )
            db.commit()
            if user.onboarding_step != "done":
                wa.send_whatsapp(
                    number,
                    "👋 Exited. You're still mid-setup — type RESTART to begin again.",
                )
            else:
                note = f" Cleared {cleared} pending item(s)." if cleared else ""
                wa.send_whatsapp(
                    number,
                    f"👋 Exited the current conversation.{note}\n"
                    "Send your miles, a photo, or HELP to start again.",
                )
            return

        if created:
            # Brand-new user: explain the service, then ask the first question.
            wa.send_whatsapp(number, WELCOME)
            wa.send_whatsapp(number, BOUNDARY)
            wa.send_whatsapp(number, TRUST)
            wa.send_whatsapp(number, VEHICLE_QUESTION)
            return

        # Until onboarding is finished, every message is an onboarding answer.
        if user.onboarding_step != "done":
            _handle_onboarding(db, user, number, body)
            return

        if num_media > 0:
            _handle_media(db, user, number, params, num_media)
            return

        _handle_text(db, user, number, body)
    except Exception as exc:  # never let a background task die silently
        print(f"[handle_inbound] error: {exc!r}")
    finally:
        db.close()


def _handle_onboarding(db, user, number, body) -> None:
    low = body.strip().lower()

    if low in ("what is this?", "what is this", "how does this work?", "how does this work"):
        wa.send_whatsapp(number, WHAT_IS_THIS)
        return

    if low in ("skip", "i'll do this later", "ill do this later", "later"):
        wa.send_whatsapp(number, SKIP_REPLY)
        return

    if low in ("start", "hi", "hello") and user.onboarding_step == "ask_vehicle":
        wa.send_whatsapp(number, VEHICLE_QUESTION)
        return

    if user.onboarding_step == "ask_vehicle":
        vehicle = _parse_vehicle(body)
        if vehicle is None:
            wa.send_whatsapp(number, "Sorry, I didn't catch that.\n\n" + VEHICLE_QUESTION)
            return
        user.vehicle_type = vehicle
        user.onboarding_step = "ask_tax"
        db.commit()
        wa.send_whatsapp(number, vehicle_confirmation(vehicle))
        wa.send_whatsapp(number, TAX_QUESTION)
        return

    if user.onboarding_step == "ask_tax":
        rate = _parse_tax_rate(body)
        if rate is None:
            wa.send_whatsapp(number, "Sorry, I didn't catch that.\n\n" + TAX_QUESTION)
            return
        user.tax_rate = rate
        user.onboarding_step = "done"
        db.commit()
        wa.send_whatsapp(number, tax_confirmation(rate))
        wa.send_whatsapp(number, SETUP_COMPLETE)
        wa.send_whatsapp(number, FREE_TRIAL)
        wa.send_whatsapp(number, FIRST_ACTION)
        return


def _handle_media(db, user, number, params, num_media) -> None:
    for i in range(num_media):
        url = params.get(f"MediaUrl{i}", "")
        ctype = params.get(f"MediaContentType{i}", "image/jpeg")
        if not url or not ctype.startswith("image/"):
            wa.send_whatsapp(number, "I can only read photos right now — please send an image.")
            continue
        try:
            image_bytes, real_type = wa.download_media(url)
            data = extract.extract_from_image(image_bytes, real_type)
        except Exception as exc:
            print(f"[extract] error: {exc!r}")
            wa.send_whatsapp(number, "Sorry, I couldn't read that one. Try a clearer photo?")
            continue

        source = "screenshot" if data["record_type"] == "income" else "receipt_photo"
        if data["record_type"] == "mileage":
            source = "odometer_photo"

        record = Record(
            user_id=user.id,
            record_type=data["record_type"],
            record_date=data["record_date"],
            platform_or_vendor=data["platform_or_vendor"],
            category=data["category"],
            amount=data["amount"],
            miles=data["miles"],
            vehicle_type=user.vehicle_type if data["record_type"] == "mileage" else None,
            source_type=source,
            confirmation_status="pending",
            confidence=data["confidence"],
            original_media_url=url,
            notes=data["notes"],
        )
        db.add(record)
        db.commit()
        wa.send_whatsapp(number, _confirmation_prompt(data, user))


def _handle_text(db, user, number, body) -> None:
    low = body.lower().strip()

    # If the user is mid-edit, the next message is the corrected value (or "cancel").
    editing = latest_editing(db, user.id)
    if editing:
        if low in ("cancel", "stop"):
            editing.confirmation_status = "pending"
            db.commit()
            wa.send_whatsapp(number, "Edit cancelled.\n\n" + _record_prompt(editing, user))
            return
        if _apply_edit(editing, body):
            editing.confirmation_status = "pending"
            db.commit()
            wa.send_whatsapp(number, _record_prompt(editing, user, updated=True))
            return
        wa.send_whatsapp(
            number,
            "Send the corrected value (e.g. \"115 miles\" or \"£42\"), or type CANCEL.",
        )
        return

    # If a mileage entry is waiting for a vehicle pick, this message is the choice.
    awaiting = latest_awaiting_vehicle(db, user.id)
    if awaiting:
        options = _vehicle_options(db, user)
        chosen = None
        if low.isdigit():
            i = int(low) - 1
            if 0 <= i < len(options):
                chosen = options[i]
        else:
            chosen = _parse_vehicle(low)
        if chosen is None:
            wa.send_whatsapp(number, "Please reply with the vehicle's number.\n\n"
                             + _which_vehicle_prompt(options, tax.normalise_vehicle(user.vehicle_type)))
            return
        awaiting.vehicle_type = chosen
        awaiting.confirmation_status = "pending"
        db.commit()
        wa.send_whatsapp(number, _mileage_prompt(awaiting.miles or 0, chosen, user))
        return

    if low in ("settings", "setting"):
        user.onboarding_step = "ask_vehicle"
        db.commit()
        wa.send_whatsapp(number, "Let's update your settings.\n\n" + VEHICLE_QUESTION)
        return

    if low in ("vehicles", "vehicle", "my vehicles"):
        wa.send_whatsapp(number, export.vehicles_overview(db, user))
        return

    if low.startswith("use ") or low.startswith("switch to ") or low.startswith("switch "):
        arg = low.split(" ", 1)[1] if " " in low else ""
        arg = arg.removeprefix("to ").strip()
        vehicle = _parse_vehicle(arg)
        if vehicle is None:
            wa.send_whatsapp(
                number,
                "I didn't recognise that vehicle. Try \"use car\", \"use motorbike\" or \"use bike\".",
            )
            return
        switched = user.vehicle_type != vehicle
        user.vehicle_type = vehicle
        db.commit()
        verb = "Switched to" if switched else "Already logging to"
        wa.send_whatsapp(
            number,
            f"{verb} your {tax.label(vehicle)} {tax.emoji(vehicle)}\n"
            f"Mileage you send now uses the {tax.label(vehicle)} rate.",
        )
        return

    if low in ("1", "confirm", "yes", "y"):
        rec = latest_pending(db, user.id)
        if not rec:
            wa.send_whatsapp(number, "Nothing waiting to confirm. Send a photo or your mileage.")
            return
        rec.confirmation_status = "estimated" if rec.source_type == "user_estimate" else "confirmed"
        rec.confirmed_at = now()
        db.commit()
        wa.send_whatsapp(number, "✅ Saved.")
        return

    if low in ("2", "edit", "change"):
        rec = latest_pending(db, user.id)
        if not rec:
            wa.send_whatsapp(number, "Nothing waiting to edit. Send a photo or your mileage.")
            return
        rec.confirmation_status = "editing"
        db.commit()
        prompt = ("Send the corrected mileage (e.g. \"115 miles\")."
                  if rec.record_type == "mileage"
                  else "Send the corrected amount (e.g. \"£42\").")
        wa.send_whatsapp(number, prompt)
        return

    if low in ("3", "delete", "discard", "no", "n"):
        rec = latest_pending(db, user.id)
        if rec:
            rec.confirmation_status = "rejected"
            db.commit()
        wa.send_whatsapp(number, "Deleted. Send it again or type the correct value.")
        return

    if low in ("csv", "export", "report"):
        token = make_export_link(db, user.id)
        if config.PUBLIC_BASE_URL:
            wa.send_whatsapp(number, f"Your CSV (link valid 24h):\n{config.PUBLIC_BASE_URL}/export/{token}")
        else:
            wa.send_whatsapp(number, "Export link isn't configured yet (set PUBLIC_BASE_URL).")
        return

    if low in ("summary", "total", "totals"):
        wa.send_whatsapp(number, export.weekly_summary(db, user))
        return

    if low in ("help", "hi", "hello", "start", "menu"):
        wa.send_whatsapp(number, HELP)
        return

    # Try to read it as a mileage entry ("145 miles", or just "145").
    mileage = extract.parse_mileage_text(body)
    if mileage:
        record = Record(
            user_id=user.id,
            record_type="mileage",
            record_date=mileage["record_date"],
            category="mileage",
            miles=mileage["miles"],
            vehicle_type=user.vehicle_type,
            source_type=mileage["source_hint"],
            confidence=mileage["confidence"],
            notes=mileage["notes"],
        )
        # If the courier uses more than one vehicle, confirm which one before logging
        # (defends against forgetting to switch). Single-vehicle users are unaffected.
        if len(_logged_vehicle_types(db, user.id)) >= 2:
            record.confirmation_status = "awaiting_vehicle"
            db.add(record)
            db.commit()
            options = _vehicle_options(db, user)
            wa.send_whatsapp(number, _which_vehicle_prompt(options, tax.normalise_vehicle(user.vehicle_type)))
        else:
            record.confirmation_status = "pending"
            db.add(record)
            db.commit()
            wa.send_whatsapp(number, _mileage_prompt(mileage["miles"], user.vehicle_type, user))
        return

    wa.send_whatsapp(number, HELP)


_VEHICLE_ORDER = ("car_van", "motorbike", "bicycle")


def _logged_vehicle_types(db, user_id: int) -> set[str]:
    """Distinct vehicle types the user has logged mileage against (non-rejected)."""
    rows = (
        db.query(Record.vehicle_type)
        .filter(
            Record.user_id == user_id,
            Record.record_type == "mileage",
            Record.confirmation_status != "rejected",
            Record.vehicle_type.isnot(None),
        )
        .distinct()
        .all()
    )
    return {tax.normalise_vehicle(v[0]) for v in rows}


def _vehicle_options(db, user) -> list[str]:
    """Vehicles to offer in the 'which vehicle?' prompt — active first."""
    active = tax.normalise_vehicle(user.vehicle_type)
    candidates = _logged_vehicle_types(db, user.id) | {active}
    return [active] + [t for t in _VEHICLE_ORDER if t in candidates and t != active]


def _which_vehicle_prompt(options: list[str], active: str) -> str:
    lines = ["Which vehicle were these on?"]
    for i, vt in enumerate(options, 1):
        mark = " (current)" if vt == active else ""
        lines.append(f" {i}. {tax.emoji(vt)} {tax.label(vt).capitalize()}{mark}")
    return "\n".join(lines)


# Shown after every detected/updated record so the user can correct it.
_OPTIONS_FOOTER = "Reply 1 to confirm, 2 to edit, or 3 to delete."


def _mileage_prompt(miles: float, vehicle_type, user, updated: bool = False) -> str:
    """Confirmation text for a mileage entry, with deduction + tax-benefit estimate."""
    deduction = tax.mileage_deduction(miles, vehicle_type)
    lead = "Updated to" if updated else "I logged"
    msg = (
        f"{lead} {miles:.0f} delivery miles ({tax.label(vehicle_type)} {tax.emoji(vehicle_type)}) "
        f"for this week.\n"
        f"Estimated mileage deduction: £{deduction:.0f}"
    )
    if user.tax_rate:
        benefit = tax.tax_benefit(deduction, user.tax_rate)
        msg += f"\nEstimated tax benefit: up to ~£{benefit:.0f} (at {user.tax_rate * 100:.0f}% tax rate)"
    msg += f"\n\n{_OPTIONS_FOOTER}"
    return msg


def _confirmation_prompt(data: dict, user) -> str:
    """First-time confirmation prompt built from a fresh extraction dict."""
    if data["record_type"] == "mileage":
        return _mileage_prompt(data["miles"], user.vehicle_type, user)

    amount = f"£{data['amount']:.2f}" if data["amount"] is not None else "£?"
    vendor = data["platform_or_vendor"] or data["category"]
    detail = f"{vendor}, {amount}, {data['category']}"

    msg = f"Detected: {detail} (dated {data['record_date']}).\n{_OPTIONS_FOOTER}"
    if data["confidence"] < config.CONFIDENCE_WARN_THRESHOLD:
        msg += "\n⚠️ I'm not fully sure on this one — please double-check the figures."
    if data["notes"]:
        msg += f"\nNote: {data['notes']}"
    return msg


def _record_prompt(rec: Record, user, updated: bool = False) -> str:
    """Re-prompt built from a stored record (used after an edit or cancel)."""
    if rec.record_type == "mileage":
        return _mileage_prompt(rec.miles or 0, rec.vehicle_type or user.vehicle_type, user, updated=updated)

    amount = f"£{rec.amount:.2f}" if rec.amount is not None else "£?"
    vendor = rec.platform_or_vendor or rec.category
    lead = "Updated" if updated else "Detected"
    return f"{lead}: {vendor}, {amount}, {rec.category}.\n{_OPTIONS_FOOTER}"


def _apply_edit(rec: Record, body: str) -> bool:
    """Apply a user's correction to a record. Returns True if a value was parsed."""
    if rec.record_type == "mileage":
        parsed = extract.parse_mileage_text(body)
        if not parsed:
            return False
        rec.miles = parsed["miles"]
        return True

    amount = _parse_amount(body)
    if amount is None:
        return False
    rec.amount = amount
    return True


_AMOUNT_RE = re.compile(r"£?\s*(\d+(?:\.\d{1,2})?)")


def _parse_amount(body: str) -> float | None:
    match = _AMOUNT_RE.search(body)
    if not match:
        return None
    value = float(match.group(1))
    return value if 0 < value <= 100_000 else None

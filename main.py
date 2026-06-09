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
import settings as vehicle_settings
import tax
import twilio_client as wa
from models import (
    ExportLink, Record, SessionLocal, User, get_or_create_user, init_db,
    latest_awaiting_platform, latest_awaiting_vehicle, latest_editing,
    latest_pending, make_export_link, now,
)

# The three delivery platforms offered as quick picks when one is unclear.
_PLATFORM_CHOICES = ["Uber Eats", "Deliveroo", "Just Eat"]

# Footer for Flow E3 review items: confirming saves as review-only (not a total).
_REVIEW_FOOTER = "Reply 1 to save as review-only, 2 to edit, or 3 to delete."


def _expense_review_reason(description: str, category: str | None = None):
    """Return (kind, reason) if an expense should be review-only, else None.
    kind drives the warning copy; reason is stored for the accountant."""
    if extract.is_vehicle_running_cost(description, category):
        return ("vehicle", "vehicle running cost while simplified mileage selected")
    if extract.is_personal_expense(description):
        return ("personal", "appears personal / requires accountant review")
    if extract.is_unclear_expense(description):
        return ("unclear", "unclear description")
    return None


def _review_warning(kind: str, description: str, amount: float | None) -> str:
    """Flow E3 warning shown before saving a review-only item."""
    line = f"\n\n{description} — £{amount:.2f}\n" if amount is not None else "\n"
    if kind == "vehicle":
        head = (
            "This looks like a vehicle running cost.\n\n"
            "Because you're using simplified mileage, vehicle running costs such as "
            "petrol, insurance, repairs, servicing, MOT, road tax and tyres are "
            "usually covered by your mileage rate.\n\n"
            "This item won't be included in your expense total."
            f"{line}\n"
            "Do you still want to save it as a review-only item for your accountant?\n"
            "(Type SETTINGS to see your mileage method.)")
    elif kind == "personal":
        head = (
            "This looks like it may be personal.\n\n"
            "I can delete it, or save it as a review-only item. Review-only items "
            "aren't included in your expense total unless reviewed later."
            f"{line}")
    else:  # unclear
        head = (
            "This expense description is unclear.\n\n"
            "I can save it for accountant review, or you can edit it to make it "
            "clearer.\n\n"
            "Example:\n\"Phone mount £12\"\n\"Delivery bag £45\""
            f"{line}")
    return f"{head}\n{_REVIEW_FOOTER}"

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

TERMS_CHECK = (
    "Before we start, please review our Terms and Privacy Notice.\n\n"
    "They explain what the service does, what it does not do, and how your data is "
    "used.\n\n"
    "1. View Terms\n"
    "2. View Privacy Notice\n"
    "3. Accept and continue\n\n"
    "Reply 3 to accept and continue (or 1 / 2 to read first)."
)

TERMS_SUMMARY = (
    "Terms (summary):\n\n"
    "• I help you organise delivery records — I don't file your tax return or give "
    "formal tax advice.\n"
    "• Figures are estimates for your review; you confirm everything before it's saved.\n"
    "• Vehicle costs use the simplified-mileage method only.\n\n"
    "Reply 3 to accept and continue."
)

PRIVACY_SUMMARY = (
    "Privacy Notice (summary):\n\n"
    "• I store only the records you confirm. Earnings screenshots and receipts are "
    "never kept — I read them, then discard the image.\n"
    "• Your data is used to build your summaries and export, nothing else.\n"
    "• You can export or delete your data anytime from settings.\n\n"
    "Reply 3 to accept and continue."
)

TRUST = (
    "You stay in control.\n\n"
    "Before anything is added to your records, you can confirm, edit or delete it.\n\n"
    "To set you up, I only need two quick answers."
)

# Bump when the Terms/Privacy copy changes so we can record what each user accepted.
TERMS_VERSION = "2026-06"

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
    "By default, I'll remind you every Sunday evening to send your delivery miles.\n\n"
    "Example:\n"
    "\"120 miles\"\n\n"
    "You can also add earnings screenshots or type earnings manually.\n\n"
    "Example:\n"
    "\"Uber Eats £320\"\n\n"
    "Default tracking is weekly, but you can change mileage or earnings input to "
    "monthly in settings.\n\n"
    "Type SETTINGS anytime to change your vehicle, tax estimate, reminder, or input "
    "frequency."
)

HOW_IT_WORKS = (
    "Here's how it works:\n\n"
    "1. Send your delivery miles weekly or monthly.\n"
    "2. Add earnings by screenshot or manual text.\n"
    "3. Add courier-related expenses if needed.\n"
    "4. Confirm, edit or delete before anything is saved.\n"
    "5. Get summaries and export records when needed.\n\n"
    "You can change weekly/monthly input in settings."
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
            _start_onboarding(db, user, number)
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
            # Brand-new user: welcome → boundary → terms & privacy check.
            _start_onboarding(db, user, number)
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


def _start_onboarding(db, user, number) -> None:
    """Send welcome → boundary → terms check, and park at the terms step."""
    user.onboarding_step = "ask_terms"
    db.commit()
    wa.send_whatsapp(number, WELCOME)
    wa.send_whatsapp(number, BOUNDARY)
    wa.send_whatsapp(number, TERMS_CHECK)


def _handle_onboarding(db, user, number, body) -> None:
    low = body.strip().lower()

    if low in ("what is this?", "what is this", "how does this work?",
               "how does this work", "how it works"):
        wa.send_whatsapp(number, HOW_IT_WORKS)
        return

    if low in ("cancel", "stop"):
        wa.send_whatsapp(number, "No problem — onboarding paused. Type START when "
                         "you're ready to finish setting up.")
        return

    if low in ("skip", "i'll do this later", "ill do this later", "later"):
        wa.send_whatsapp(number, SKIP_REPLY)
        return

    # Terms & Privacy gate — must accept before the two questions.
    if user.onboarding_step == "ask_terms":
        if low in ("1", "view terms", "terms"):
            wa.send_whatsapp(number, TERMS_SUMMARY)
            return
        if low in ("2", "view privacy", "privacy", "privacy notice"):
            wa.send_whatsapp(number, PRIVACY_SUMMARY)
            return
        if low in ("3", "accept", "accept and continue", "agree", "continue", "start"):
            user.terms_version = TERMS_VERSION
            user.terms_accepted_at = now()
            user.onboarding_step = "ask_vehicle"
            db.commit()
            wa.send_whatsapp(number, TRUST)
            wa.send_whatsapp(number, VEHICLE_QUESTION)
            return
        wa.send_whatsapp(number, "Please reply 3 to accept and continue (or 1 / 2 to "
                         "read the Terms / Privacy Notice first).")
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

        # Mileage photo: read the distance and confirm it (image not stored).
        if data["record_type"] == "mileage":
            miles = data["miles"]
            if not miles:
                wa.send_whatsapp(
                    number,
                    "This looks like mileage but I couldn't read the number.\n\n"
                    "Please type it, e.g. \"120 miles\".")
                continue
            vt = vehicle_settings.default_vehicle(user)
            monthly = (getattr(user, "log_frequency", "weekly") or "weekly") == "monthly"
            p_start, p_end = _period_range(monthly)
            db.add(Record(
                user_id=user.id, record_type="mileage", record_date=data["record_date"],
                category="mileage", miles=miles, vehicle_type=vt,
                source_type="odometer_photo", confidence=data["confidence"],
                confirmation_status="pending", original_media_url="",
                notes="Mileage read from a photo.",
                period_start=p_start.isoformat(), period_end=p_end.isoformat(),
                entry_frequency="monthly" if monthly else "weekly",
            ))
            db.commit()
            wa.send_whatsapp(number, _mileage_prompt(miles, vt, user, monthly=monthly))
            continue

        source = "screenshot" if data["record_type"] == "income" else "receipt_ocr"

        # Flow E2: a receipt we can't read clearly creates no record — the user is
        # asked to type the expense instead, and the image is never stored.
        if data["record_type"] == "expense" and data["amount"] is None:
            wa.send_whatsapp(
                number,
                "I couldn't read this receipt clearly.\n\n"
                "You can type the expense instead, for example:\n\n"
                "\"Delivery bag £45\"\n\n"
                "The receipt image will not be stored.")
            continue

        # Flow D: income with an unreadable amount or platform routes to a fix-up
        # step before confirmation, so AI output is never silently saved.
        status = "pending"
        if data["record_type"] == "income":
            if data["amount"] is None:
                status = "editing"
            elif not data["platform_or_vendor"]:
                status = "awaiting_platform"

        # Earnings carry a period (Flow D); default to the user's input frequency.
        period_kw = {}
        if data["record_type"] == "income":
            monthly = (getattr(user, "log_frequency", "weekly") or "weekly") == "monthly"
            ps, pe = _period_range(monthly)
            period_kw = dict(period_start=ps.isoformat(), period_end=pe.isoformat(),
                             entry_frequency="monthly" if monthly else "weekly")

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
            confirmation_status=status,
            confidence=data["confidence"],
            # Screenshots and receipts are never retained — keep no media reference.
            original_media_url="" if data["record_type"] in ("income", "expense") else url,
            notes=data["notes"], **period_kw,
        )
        db.add(record)
        db.commit()

        if status == "editing":  # amount unclear (Flow D §5)
            wa.send_whatsapp(number, "I couldn't read the earnings amount clearly.\n\n"
                             "Please type the amount, for example:\n\n\"Uber Eats £320\"")
        elif status == "awaiting_platform":  # platform unclear (Flow D §4)
            wa.send_whatsapp(number, _platform_picker(
                "I can read the earnings amount, but I'm not sure which platform this "
                "is from.\n\nWhich platform is this?"))
        elif data["record_type"] == "expense":
            # Flow E3: vehicle-cost / personal / unclear → save as review-only.
            reason = _expense_review_reason(data["platform_or_vendor"], data["category"])
            if reason:
                record.confirmation_status = "pending_review"
                record.notes = reason[1]
                db.commit()
                wa.send_whatsapp(number, _review_warning(
                    reason[0], data["platform_or_vendor"] or "This item", data["amount"]))
            else:
                wa.send_whatsapp(number, _confirmation_prompt(data, user))
        else:
            wa.send_whatsapp(number, _confirmation_prompt(data, user))


def _resolve_monthly(user, body: str) -> bool:
    """Decide if an entry is monthly: an explicit word in the message wins,
    otherwise fall back to the user's logging-frequency preference."""
    low = body.lower()
    if "month" in low:
        return True
    if "week" in low:
        return False
    return (getattr(user, "log_frequency", "weekly") or "weekly") == "monthly"


def _is_new_loggable(body: str) -> str | None:
    """If `body` clearly starts a brand-new entry, return its type, else None.
    Used so a half-finished draft (e.g. 'which platform?') doesn't swallow it.
    A lone amount does NOT count — that may be the answer the draft is waiting for."""
    if extract.parse_mileage_text(body):
        return "mileage"
    ex = extract.parse_expense_text(body)
    if ex and not ex["amount_missing"]:
        return "expense"
    en = extract.parse_earnings_text(body)
    if en and not en["platform_missing"]:  # has an explicit platform
        return "earnings"
    return None


def _handle_text(db, user, number, body) -> None:
    low = body.lower().strip()

    # One-shot context hint: consume it now so it only affects this message.
    expecting = user.expecting
    if expecting:
        user.expecting = None
        db.commit()

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
        # Let command words abandon the half-finished entry instead of being
        # misread as a vehicle pick (e.g. "settings", "cancel").
        if low in ("cancel", "stop", "settings", "setting", "menu"):
            awaiting.confirmation_status = "rejected"
            db.commit()
            if low in ("cancel", "stop"):
                wa.send_whatsapp(number, "Cancelled — no mileage saved.")
                return
            # fall through so the settings handler below picks it up
        else:
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
                                 + _which_vehicle_prompt(options, vehicle_settings.default_vehicle(user)))
                return
            awaiting.vehicle_type = chosen
            awaiting.confirmation_status = "pending"
            db.commit()
            wa.send_whatsapp(number, _mileage_prompt(
                awaiting.miles or 0, chosen, user,
                monthly=(awaiting.entry_frequency == "monthly")))
            return

    # After "Other", the next message is the custom platform name (taken verbatim).
    awaiting_plat_text = (
        db.query(Record)
        .filter_by(user_id=user.id, confirmation_status="awaiting_platform_text")
        .order_by(Record.created_at.desc())
        .first()
    )
    if awaiting_plat_text:
        if low in ("cancel", "stop", "delete"):
            awaiting_plat_text.confirmation_status = "rejected"
            db.commit()
            wa.send_whatsapp(number, "Deleted.\n\nNo earnings record was saved.")
            return
        if _is_new_loggable(body):  # a fresh entry — abandon this half-finished draft
            awaiting_plat_text.confirmation_status = "rejected"
            db.commit()
            # fall through to normal routing below
        else:
            awaiting_plat_text.platform_or_vendor = body.strip()[:64]
            awaiting_plat_text.confirmation_status = "pending"
            db.commit()
            wa.send_whatsapp(number, _income_prompt(awaiting_plat_text, user))
            return

    # If a typed expense is waiting for its amount (Flow E1 §6), this is it.
    awaiting_exp_amt = (
        db.query(Record)
        .filter_by(user_id=user.id, confirmation_status="awaiting_expense_amount")
        .order_by(Record.created_at.desc())
        .first()
    )
    if awaiting_exp_amt:
        if low in ("cancel", "stop", "delete"):
            awaiting_exp_amt.confirmation_status = "rejected"
            db.commit()
            wa.send_whatsapp(number, "Deleted. This expense will not be included in your records.")
            return
        if _is_new_loggable(body):  # a fresh entry — abandon this half-finished draft
            awaiting_exp_amt.confirmation_status = "rejected"
            db.commit()
            # fall through to normal routing below
        else:
            amount = _parse_amount(body)
            if amount is None:
                wa.send_whatsapp(number, f"How much was the "
                                 f"{awaiting_exp_amt.platform_or_vendor.lower()}? "
                                 "Send it like \"£45\".")
                return
            awaiting_exp_amt.amount = amount
            desc = awaiting_exp_amt.platform_or_vendor
            reason = _expense_review_reason(desc, awaiting_exp_amt.category)
            if reason:  # Flow E3: review-only item
                awaiting_exp_amt.confirmation_status = "pending_review"
                awaiting_exp_amt.notes = reason[1]
                db.commit()
                wa.send_whatsapp(number, _review_warning(reason[0], desc, amount))
            else:
                awaiting_exp_amt.confirmation_status = "pending"
                db.commit()
                wa.send_whatsapp(number, _expense_prompt(desc, amount))
            return

    # If an income entry is waiting for its platform (Flow D), this is the answer.
    # Picker numbering: 1..N platforms, N+1 = Other, N+2 = Delete.
    awaiting_plat = latest_awaiting_platform(db, user.id)
    if awaiting_plat:
        n = len(_PLATFORM_CHOICES)
        other_num, delete_num = str(n + 1), str(n + 2)
        if low in ("cancel", "stop", "delete", delete_num):
            awaiting_plat.confirmation_status = "rejected"
            db.commit()
            wa.send_whatsapp(number, "Deleted.\n\nNo earnings record was saved.")
            return
        platform = None
        if low.isdigit() and 1 <= int(low) <= n:
            platform = _PLATFORM_CHOICES[int(low) - 1]
        elif low in (other_num, "other"):
            awaiting_plat.confirmation_status = "awaiting_platform_text"
            db.commit()
            wa.send_whatsapp(number, "Please type the platform name.")
            return
        elif _is_new_loggable(body):  # a fresh entry — abandon this draft
            awaiting_plat.confirmation_status = "rejected"
            db.commit()
            platform = None  # fall through to normal routing below
        else:
            platform = extract._find_platform(body)
            if platform is None:
                # Unrecognised reply — re-show the picker rather than guess.
                wa.send_whatsapp(number, _platform_picker(
                    "Sorry, I didn't catch that. Which platform was this from?"))
                return
        if platform is not None:
            awaiting_plat.platform_or_vendor = platform[:64]
            awaiting_plat.confirmation_status = "pending"
            db.commit()
            wa.send_whatsapp(number, _income_prompt(awaiting_plat, user))
            return

    # Flow C: vehicle/settings menu, in-flow states, and NL vehicle intents.
    # Checked after editing/awaiting (Flow B drafts win) but before the confirm
    # shortcuts so menu number replies aren't read as confirm/edit/delete.
    if vehicle_settings.handle(db, user, number, body):
        return

    if low in ("vehicles", "my vehicles"):
        wa.send_whatsapp(number, export.vehicles_overview(db, user))
        return

    if low in ("how it works", "how does this work", "how does this work?", "what is this",
               "what is this?"):
        wa.send_whatsapp(number, HOW_IT_WORKS)
        return

    # Flow E3 §7/§10: eligibility questions — we don't approve or reject, just offer
    # to save for review. (Catches "can I claim …?", "is … deductible/allowable?")
    if ("claim" in low or "deductible" in low or "allowable" in low or "tax deduct" in low) \
            and ("can i" in low or "could i" in low or "is " in low or "?" in body):
        wa.send_whatsapp(
            number,
            "I can help organise your records, but I don't approve expenses or give "
            "formal tax advice.\n\n"
            "I can save the item for accountant review if you want — just send it "
            "like \"Phone mount £12\".")
        return

    if low in ("expense", "expenses", "add expense", "add expenses"):
        wa.send_whatsapp(
            number,
            "You can also add courier-related expenses.\n\n"
            "Type it like:\n"
            "\"Delivery bag £45\"\n"
            "\"Phone mount £12\"\n"
            "\"Parking £8\"\n\n"
            "Confirmed expenses will be included in your record pack for accountant "
            "review.",
        )
        return

    # Flow E2 §8: abandon any unconfirmed receipt draft and type the expense instead.
    if low in ("type expense instead", "type instead", "type expense"):
        for r in _all_pending(db, user.id):
            if r.record_type == "expense":
                r.confirmation_status = "rejected"
        db.commit()
        wa.send_whatsapp(
            number,
            "No problem.\n\nType the expense like this:\n\n\"Delivery bag £45\"")
        return

    # Flow D §9: asking to SAVE a screenshot as evidence — not in the base product.
    if "evidence" in low or ("screenshot" in low
                             and ("save" in low or "saved" in low or "store" in low)):
        wa.send_whatsapp(
            number,
            "Screenshot evidence storage isn't included in the standard version.\n\n"
            "For now, I save the confirmed earnings record only, not the screenshot "
            "itself.\n\n"
            "Evidence storage may be offered later as a paid/pro feature.")
        return

    # Flow E2 §9: "is my receipt/image stored?"
    if ("receipt" in low or "image" in low or "photo" in low or "screenshot" in low) \
            and ("store" in low or "stored" in low or "save" in low or "saved" in low or "keep" in low):
        wa.send_whatsapp(
            number,
            "No. In the standard flow, I use the receipt only to help extract the "
            "expense details.\n\n"
            "After processing, I save only the details you confirm, not the receipt "
            "image.\n\n"
            "Please keep the original receipt yourself if you need supporting evidence.")
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

    if low in ("1", "confirm", "yes", "y", "confirm all"):
        pending = _all_pending(db, user.id)
        if not pending:
            wa.send_whatsapp(number, "Nothing waiting to confirm. Send a photo or your mileage.")
            return
        mileage_recs = [r for r in pending if r.record_type == "mileage"]
        has_mileage = bool(mileage_recs)
        has_income = any(r.record_type == "income" for r in pending)
        has_review = any(r.confirmation_status == "pending_review" for r in pending)
        # Normal (counted) expense, distinct from review-only items.
        has_expense = any(r.record_type == "expense"
                          and r.confirmation_status == "pending" for r in pending)
        # Receipt-sourced expense → add the Flow E2 "image not stored" reassurance.
        from_receipt = any(r.record_type == "expense" and r.source_type == "receipt_ocr"
                           and r.confirmation_status == "pending" for r in pending)
        for rec in pending:
            if rec.confirmation_status == "pending_review":
                rec.confirmation_status = "review_required"  # saved, excluded from totals
            elif rec.source_type == "user_estimate":
                rec.confirmation_status = "estimated"
            else:
                rec.confirmation_status = "confirmed"
            rec.confirmed_at = now()
            # We never retain raw screenshots/receipts — only the confirmed figures.
            if rec.record_type in ("income", "expense"):
                rec.original_media_url = ""
        db.commit()
        if has_review:
            wa.send_whatsapp(
                number,
                "Saved for review ✅\n\nThis item is saved as review-only — it will "
                "appear in the review section for your accountant and is not included "
                "in your expense total.")
        if has_income:
            wa.send_whatsapp(number, export.earnings_summary(db, user))
        elif has_expense:
            if from_receipt:
                wa.send_whatsapp(
                    number,
                    "Confirmed ✅\n\nSaved as an expense for accountant review.\n\n"
                    "The receipt image was not stored. Please keep the original "
                    "receipt yourself if you need supporting evidence.")
            wa.send_whatsapp(number, export.expense_summary(db, user))
        elif has_review:
            pass  # review message already sent
        elif has_mileage:
            # We just asked for earnings, so read the next bare number as earnings.
            user.expecting = "earnings"
            db.commit()
            wa.send_whatsapp(number, _mileage_confirmed_summary(mileage_recs, user))
        else:
            wa.send_whatsapp(number, "Confirmed ✅ Saved.")
        return

    if low in ("2", "edit", "change"):
        rec = latest_pending(db, user.id)
        if not rec:
            wa.send_whatsapp(number, "Nothing waiting to edit. Send a photo or your mileage.")
            return
        rec.confirmation_status = "editing"
        db.commit()
        if rec.record_type == "mileage":
            prompt = "Send the corrected mileage (e.g. \"115 miles\")."
        elif rec.record_type == "expense":
            prompt = "Send the corrected expense (e.g. \"Delivery bag £39.99\")."
        else:
            prompt = "Send the corrected amount (e.g. \"£42\")."
        wa.send_whatsapp(number, prompt)
        return

    if low in ("3", "delete", "discard", "no", "n"):
        pending = _all_pending(db, user.id)
        had_mileage = any(r.record_type == "mileage" for r in pending)
        had_expense = any(r.record_type == "expense" for r in pending)
        for rec in pending:
            rec.confirmation_status = "rejected"
        db.commit()
        if had_mileage:
            wa.send_whatsapp(number, "Deleted. No mileage record was saved for this week.")
        elif had_expense:
            wa.send_whatsapp(number, "Deleted. This expense will not be included in your records.")
        else:
            wa.send_whatsapp(number, "Deleted. Send it again or type the correct value.")
        return

    if low in ("csv", "export", "report"):
        token = make_export_link(db, user.id)
        note = ("\n\nNote: Receipt and screenshot images are not stored. Expenses "
                "marked \"receipt OCR\" were read from an uploaded image and confirmed "
                "by you — keep your original receipts if you need supporting evidence.")
        if config.PUBLIC_BASE_URL:
            wa.send_whatsapp(number, f"Your CSV (link valid 24h):\n"
                             f"{config.PUBLIC_BASE_URL}/export/{token}{note}")
        else:
            wa.send_whatsapp(number, "Export link isn't configured yet (set PUBLIC_BASE_URL).")
        return

    if low in ("summary", "total", "totals"):
        wa.send_whatsapp(number, export.weekly_summary(db, user))
        return

    if low in ("help", "hi", "hello", "start", "menu"):
        wa.send_whatsapp(number, HELP)
        return

    # "What miles should I include?" — guidance (Flow B section 15).
    if "what miles" in low or ("which miles" in low and "include" in low):
        wa.send_whatsapp(
            number,
            "Include miles you used for delivery work.\n\n"
            "Do not include personal trips.\n\n"
            "If you are unsure, log the miles you believe were for delivery work and "
            "your accountant can review your records later.",
        )
        return

    # Try to read it as a mileage entry. Handles single / split / monthly /
    # personal+delivery / vehicle-tagged inputs (see extract.parse_mileage_text).
    # Period: an explicit "month"/"week" in the message wins; otherwise use the
    # user's logging-frequency preference (default weekly).
    monthly = _resolve_monthly(user, body)
    ad_hoc_monthly = "month" in low  # they typed it this time → suggest weekly

    mileage = extract.parse_mileage_text(body)
    if mileage:
        # If we just asked for earnings and they sent a bare number (no "miles"
        # unit), treat it as earnings instead — that's what they meant.
        if expecting == "earnings" and extract.is_bare_number(body):
            _handle_earnings_entry(db, user, number, {
                "kind": "single", "monthly": monthly,
                "platform_missing": True,
                "entries": [{"platform": None, "amount": mileage["miles"]}],
            })
            return
        mileage["monthly"] = monthly
        mileage["recommend_weekly"] = ad_hoc_monthly
        _handle_mileage_entry(db, user, number, mileage)
        return

    # Try to read it as a typed expense (Flow E1): "Delivery bag £45" etc.
    # Runs before earnings so a described item isn't mistaken for income.
    expense = extract.parse_expense_text(body)
    if expense:
        _handle_expense_entry(db, user, number, expense)
        return

    # Try to read it as manual earnings (Flow D): "Uber Eats £320" etc.
    earnings = extract.parse_earnings_text(body)
    if earnings:
        earnings["monthly"] = monthly
        _handle_earnings_entry(db, user, number, earnings)
        return

    # Sounds like mileage but no number we could read (Flow B section 11).
    if any(w in low for w in ("mile", "drove", "drive", "driving", "rode", "cycled")):
        wa.send_whatsapp(
            number,
            "I need the number of delivery miles to log this.\n\n"
            "Please send it like this:\n\n"
            "\"120 miles\"",
        )
        return

    wa.send_whatsapp(number, HELP)


def _handle_mileage_entry(db, user, number, parsed: dict) -> None:
    """Create pending mileage record(s) from a parsed input and prompt to confirm."""
    monthly = parsed.get("monthly", False)
    p_start, p_end = _period_range(monthly)
    freq = "monthly" if monthly else "weekly"
    period_fields = dict(period_start=p_start.isoformat(), period_end=p_end.isoformat(),
                         entry_frequency=freq)
    note_suffix = " (logged as monthly user-entered mileage)" if monthly else ""

    # Split mileage: one pending record per vehicle, confirmed/deleted together.
    if parsed["kind"] == "split":
        rows = []
        for seg in parsed["segments"]:
            vt = seg["vehicle_hint"] or vehicle_settings.default_vehicle(user)
            rec = Record(
                user_id=user.id, record_type="mileage", record_date=parsed["record_date"],
                category="mileage", miles=seg["miles"], vehicle_type=vt,
                source_type=parsed["source_hint"], confidence=parsed["confidence"],
                confirmation_status="pending",
                notes="User-entered split mileage." + note_suffix, **period_fields,
            )
            db.add(rec)
            rows.append((vt, seg["miles"]))
        db.commit()
        wa.send_whatsapp(number, _split_prompt(rows, user, monthly))
        return

    # Single entry. Vehicle priority: explicit tag > prompt (multi-vehicle) > default.
    vehicle_hint = parsed.get("vehicle_hint")
    high_warning = parsed.get("too_high")

    record = Record(
        user_id=user.id, record_type="mileage", record_date=parsed["record_date"],
        category="mileage", miles=parsed["miles"],
        vehicle_type=vehicle_hint or vehicle_settings.default_vehicle(user),
        source_type=parsed["source_hint"], confidence=parsed["confidence"],
        notes=parsed["notes"] + note_suffix, **period_fields,
    )

    # Multi-vehicle user with no explicit vehicle tag: ask which vehicle first.
    if not vehicle_hint and len(vehicle_settings.registered(user)) >= 2:
        record.confirmation_status = "awaiting_vehicle"
        db.add(record)
        db.commit()
        options = _vehicle_options(db, user)
        wa.send_whatsapp(number, _which_vehicle_prompt(options, tax.normalise_vehicle(user.vehicle_type)))
        return

    record.confirmation_status = "pending"
    db.add(record)
    db.commit()

    prompt = _mileage_prompt(parsed["miles"], record.vehicle_type, user,
                             monthly=monthly,
                             personal_excluded=parsed.get("personal_excluded"),
                             recommend_weekly=parsed.get("recommend_weekly", False))
    if high_warning:
        prompt = (f"That looks unusually high for this period.\n"
                  f"Did you mean {parsed['miles']:,.0f} delivery miles?\n"
                  f"{_period_line(monthly)}\n\n" + prompt)
    wa.send_whatsapp(number, prompt)


def _mileage_confirmed_summary(recs: list[Record], user) -> str:
    """Flow B §21 summary shown after mileage is confirmed."""
    period = ""
    if recs and recs[0].period_start and recs[0].period_end:
        try:
            s = dt.date.fromisoformat(recs[0].period_start)
            e = dt.date.fromisoformat(recs[0].period_end)
            period = f"\n\nPeriod: {_fmt_period(s, e)}"
        except ValueError:
            pass
    total_miles = sum(r.miles or 0 for r in recs)
    total_ded = sum(tax.mileage_deduction(r.miles or 0, r.vehicle_type) for r in recs)
    if len(recs) == 1:
        veh = f"\nVehicle: {tax.label(recs[0].vehicle_type)}"
    else:
        veh = "\nVehicles: " + ", ".join(tax.label(r.vehicle_type) for r in recs)
    msg = (
        f"Mileage record added ✅{period}\n\n"
        f"Miles logged: {total_miles:.0f}{veh}\n"
        f"Mileage deduction captured: £{_money(total_ded)}"
    )
    if user.tax_rate:
        benefit = tax.tax_benefit(total_ded, user.tax_rate)
        msg += f"\nEstimated tax benefit: up to ~£{_money(benefit)}"
    msg += "\n\nAdd earnings if you want your real take-home estimate."
    return msg


def _split_prompt(rows: list[tuple[str, float]], user, monthly: bool = False) -> str:
    """Confirmation text for a split-mileage entry (one line per vehicle)."""
    lines = ["I logged split mileage.\n", _period_line(monthly) + "\n"]
    total_miles = 0.0
    total_deduction = 0.0
    for vt, miles in rows:
        ded = tax.mileage_deduction(miles, vt)
        total_miles += miles
        total_deduction += ded
        lines.append(f"{tax.label(vt)}: {miles:.0f} miles — £{ded:.0f} deduction")
    lines.append(f"\nTotal miles: {total_miles:.0f}")
    lines.append(f"Total mileage deduction: £{_money(total_deduction)}")
    if user.tax_rate:
        benefit = tax.tax_benefit(total_deduction, user.tax_rate)
        lines.append(f"Estimated tax benefit: up to ~£{_money(benefit)}, "
                     f"assuming {user.tax_rate * 100:.0f}% tax rate.")
    lines.append(f"\n{_OPTIONS_FOOTER}")
    return "\n".join(lines)


# --- Flow D: manual earnings -------------------------------------------------

def _platform_picker(lead: str) -> str:
    options = _PLATFORM_CHOICES + ["Other", "Delete"]
    return (lead + "\n\n"
            + "\n".join(f"{i}. {p}" for i, p in enumerate(options, 1)))


def _recent_income_duplicate(db, user_id, platform, amount) -> Record | None:
    """A confirmed income record this week with the same platform and amount."""
    week_ago = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    return (
        db.query(Record)
        .filter(
            Record.user_id == user_id,
            Record.record_type == "income",
            Record.confirmation_status.in_(["confirmed", "estimated"]),
            Record.platform_or_vendor == platform,
            Record.amount == amount,
            Record.record_date >= week_ago,
        )
        .first()
    )


def _handle_earnings_entry(db, user, number, parsed: dict) -> None:
    """Create pending income record(s) from manual text and prompt to confirm."""
    monthly = parsed.get("monthly", False)
    p_start, p_end = _period_range(monthly)
    pf = dict(period_start=p_start.isoformat(), period_end=p_end.isoformat(),
              entry_frequency="monthly" if monthly else "weekly")
    entries = parsed["entries"]

    # Platform missing on a single amount → ask which platform first (Flow D §11/14).
    if parsed["platform_missing"]:
        e = entries[0]
        rec = Record(
            user_id=user.id, record_type="income", record_date=extract._today(),
            category="platform_income", amount=e["amount"], source_type="manual_entry",
            confidence=1.0, confirmation_status="awaiting_platform",
            notes="Manual earnings entry.", **pf,
        )
        db.add(rec)
        db.commit()
        wa.send_whatsapp(number, _platform_picker(
            f"I can log £{e['amount']:.2f} for this period, but I need the platform.\n\n"
            f"{_period_line(monthly)}\n\nWhich platform was this from?"))
        return

    # Multiple platforms in one message (Flow D §10/13).
    if parsed["kind"] == "multi":
        for e in entries:
            db.add(Record(
                user_id=user.id, record_type="income", record_date=extract._today(),
                category="platform_income", platform_or_vendor=(e["platform"] or "")[:64],
                amount=e["amount"], source_type="manual_entry", confidence=1.0,
                confirmation_status="pending", notes="Manual earnings entry.", **pf,
            ))
        db.commit()
        lines = ["I logged:\n", _period_line(monthly) + "\n"]
        total = 0.0
        for e in entries:
            total += e["amount"]
            lines.append(f"{e['platform']}: £{e['amount']:.2f}")
        lines.append(f"\nTotal earnings: £{total:.2f}")
        lines.append(f"\nConfirm?\n{_OPTIONS_FOOTER}")
        wa.send_whatsapp(number, "\n".join(lines))
        return

    # Single platform + amount.
    e = entries[0]
    rec = Record(
        user_id=user.id, record_type="income", record_date=extract._today(),
        category="platform_income", platform_or_vendor=(e["platform"] or "")[:64],
        amount=e["amount"], source_type="manual_entry", confidence=1.0,
        confirmation_status="pending", notes="Manual earnings entry.", **pf,
    )
    db.add(rec)
    db.commit()

    dup = _recent_income_duplicate(db, user.id, rec.platform_or_vendor, rec.amount)
    prompt = _income_prompt(rec, user)
    if dup:
        prompt = ("This looks similar to an earnings record already added.\n\n"
                  "Do you want to add it again or ignore it?\n\n" + prompt)
    wa.send_whatsapp(number, prompt)


def _income_prompt(rec: Record, user) -> str:
    """Confirmation text for a single income record (Flow D §14), with period."""
    monthly = rec.entry_frequency == "monthly"
    line = _period_line(monthly)
    if rec.period_start and rec.period_end:
        try:
            line = "Period: " + _fmt_period(dt.date.fromisoformat(rec.period_start),
                                            dt.date.fromisoformat(rec.period_end))
        except ValueError:
            pass
    return (
        "I logged:\n\n"
        f"Platform: {rec.platform_or_vendor or '—'}\n"
        f"Earnings: £{rec.amount:.2f}\n"
        f"{line}"
        + ("\nInput type: monthly" if monthly else "")
        + f"\n\nConfirm?\n{_OPTIONS_FOOTER}"
    )


# --- Flow E1: typed expenses -------------------------------------------------

def _handle_expense_entry(db, user, number, parsed: dict) -> None:
    """Create draft expense record(s) from typed text and prompt to confirm.

    Receipt images are never stored — only the typed figures (receipt_image_stored
    is always no, so original_media_url stays empty)."""
    entries = parsed["entries"]

    # Description given but no amount yet (Flow E1 §6) — ask for the amount.
    # (Review-only classification happens once we know the amount.)
    if parsed["amount_missing"]:
        e = entries[0]
        db.add(Record(
            user_id=user.id, record_type="expense", record_date=extract._today(),
            category=e["category"], platform_or_vendor=e["description"][:64],
            amount=None, source_type="manual_entry", confidence=1.0,
            confirmation_status="awaiting_expense_amount", notes="Typed expense.",
        ))
        db.commit()
        wa.send_whatsapp(number, f"How much was the {e['description'].lower()}?")
        return

    # Single entry: vehicle-cost / personal / unclear → review-only (Flow E3).
    if parsed["kind"] != "multi":
        e = entries[0]
        reason = _expense_review_reason(e["description"], e["category"])
        status = "pending_review" if reason else "pending"
        db.add(Record(
            user_id=user.id, record_type="expense", record_date=extract._today(),
            category=e["category"], platform_or_vendor=e["description"][:64],
            amount=e["amount"], source_type="manual_entry", confidence=1.0,
            confirmation_status=status, notes=(reason[1] if reason else "Typed expense."),
        ))
        db.commit()
        if reason:
            wa.send_whatsapp(number, _review_warning(reason[0], e["description"], e["amount"]))
        else:
            wa.send_whatsapp(number, _expense_prompt(e["description"], e["amount"]))
        return

    # Multiple expenses: mark each review-only item, log the rest normally.
    review_names = []
    for e in entries:
        reason = _expense_review_reason(e["description"], e["category"])
        if reason:
            review_names.append(e["description"])
        db.add(Record(
            user_id=user.id, record_type="expense", record_date=extract._today(),
            category=e["category"], platform_or_vendor=e["description"][:64],
            amount=e["amount"], source_type="manual_entry", confidence=1.0,
            confirmation_status="pending_review" if reason else "pending",
            notes=(reason[1] if reason else "Typed expense."),
        ))
    db.commit()
    lines = ["I logged these expenses for accountant review:\n"]
    total = 0.0
    for e in entries:
        flag = "  (review-only)" if e["description"] in review_names else ""
        total += e["amount"]
        lines.append(f"{e['description']}: £{e['amount']:.2f}{flag}")
    lines.append(f"\nTotal expenses logged: £{total:.2f}")
    if review_names:
        lines.append("Review-only items aren't included in the total — they're saved "
                     "for your accountant to review.")
    lines.append(f"\nConfirm?\n{_OPTIONS_FOOTER}")
    wa.send_whatsapp(number, "\n".join(lines))


def _expense_prompt(description: str, amount: float, updated: bool = False) -> str:
    lead = "Updated to:" if updated else "Logged for accountant review:"
    return (f"{lead}\n\n{description} — £{amount:.2f}\n\nConfirm?\n{_OPTIONS_FOOTER}")


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
    """Vehicles to offer in the 'which vehicle?' prompt — default first."""
    active = vehicle_settings.default_vehicle(user)
    candidates = (_logged_vehicle_types(db, user.id)
                  | set(vehicle_settings.registered(user)) | {active})
    return [active] + [t for t in _VEHICLE_ORDER if t in candidates and t != active]


def _which_vehicle_prompt(options: list[str], active: str) -> str:
    lines = ["Which vehicle were these on?"]
    for i, vt in enumerate(options, 1):
        mark = " (current)" if vt == active else ""
        lines.append(f" {i}. {tax.emoji(vt)} {tax.label(vt).capitalize()}{mark}")
    return "\n".join(lines)


# Shown after every detected/updated record so the user can correct it.
_OPTIONS_FOOTER = "Reply 1 to confirm, 2 to edit, or 3 to delete."


def _money(value: float) -> str:
    """Format GBP: drop the decimals when whole, otherwise show 2 dp."""
    return f"{value:.0f}" if abs(value - round(value)) < 0.005 else f"{value:.2f}"


def _period_range(monthly: bool, ref: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """Start/end dates of the current week (Mon–Sun) or current month."""
    d = ref or dt.date.today()
    if monthly:
        start = d.replace(day=1)
        nxt = (start.replace(year=start.year + 1, month=1) if start.month == 12
               else start.replace(month=start.month + 1))
        return start, nxt - dt.timedelta(days=1)
    start = d - dt.timedelta(days=d.weekday())  # Monday
    return start, start + dt.timedelta(days=6)


def _fmt_period(start: dt.date, end: dt.date) -> str:
    return f"{start.day} {start:%b} – {end.day} {end:%b %Y}"


def _period_line(monthly: bool) -> str:
    start, end = _period_range(monthly)
    return f"Period: {_fmt_period(start, end)}"


def _mileage_prompt(miles: float, vehicle_type, user, updated: bool = False,
                    monthly: bool = False, personal_excluded: float | None = None,
                    recommend_weekly: bool = False) -> str:
    """Confirmation text for a mileage entry, with deduction + tax-benefit estimate."""
    deduction = tax.mileage_deduction(miles, vehicle_type)
    lead = "Updated to" if updated else "I logged"
    msg = (
        f"{lead} {miles:.0f} delivery miles.\n\n"
        f"{_period_line(monthly)}\n"
        f"Vehicle: {tax.label(vehicle_type)}"
        + ("\nInput type: monthly" if monthly else "")
        + f"\n\nEstimated mileage deduction: £{_money(deduction)}"
    )
    if personal_excluded:
        msg = (f"I'll log only the delivery-business miles.\n\n"
               f"Delivery miles: {miles:.0f}\n\n") + msg
    if user.tax_rate:
        benefit = tax.tax_benefit(deduction, user.tax_rate)
        msg += (f"\nEstimated tax benefit: up to ~£{_money(benefit)}, "
                f"assuming {user.tax_rate * 100:.0f}% tax rate.")
    if recommend_weekly:
        msg += ("\n\nFor better accuracy, weekly mileage is recommended because it is "
                "fresher.")
    msg += f"\n\nConfirm?\n{_OPTIONS_FOOTER}"
    return msg


def _all_pending(db, user_id: int) -> list[Record]:
    """All records awaiting confirmation, including review-only ones (Flow E3)."""
    return (
        db.query(Record)
        .filter(Record.user_id == user_id,
                Record.confirmation_status.in_(["pending", "pending_review"]))
        .order_by(Record.created_at.asc())
        .all()
    )


def _confirmation_prompt(data: dict, user) -> str:
    """First-time confirmation prompt built from a fresh extraction dict."""
    if data["record_type"] == "mileage":
        return _mileage_prompt(data["miles"], user.vehicle_type, user)

    # Earnings screenshot (Flow D §4): confirmed record saved, not the screenshot.
    if data["record_type"] == "income":
        monthly = (getattr(user, "log_frequency", "weekly") or "weekly") == "monthly"
        msg = (
            "I detected:\n\n"
            f"Platform: {data['platform_or_vendor'] or '—'}\n"
            f"{_period_line(monthly)}\n"
            f"Earnings: £{data['amount']:.2f}\n\n"
            "I'll save the confirmed earnings record only — the screenshot itself is "
            "never stored.\n\n"
            "Do you want to confirm this?\n"
            f"{_OPTIONS_FOOTER}"
        )
        if data["confidence"] < config.CONFIDENCE_WARN_THRESHOLD:
            msg += "\n⚠️ I'm not fully sure on this one — please double-check the figures."
        return msg

    # Expense receipt (Flow E2 §3): receipt read, image not stored.
    description = data["platform_or_vendor"] or data["category"] or "Expense"
    msg = (
        "I found:\n\n"
        f"{description} — £{data['amount']:.2f}\n\n"
        "Save this as an expense for accountant review?\n\n"
        "The receipt image will not be stored.\n"
        f"{_OPTIONS_FOOTER}"
    )
    if data["confidence"] < config.CONFIDENCE_WARN_THRESHOLD:
        msg += "\n⚠️ I'm not fully sure on this one — please double-check the figures."
    return msg


def _record_prompt(rec: Record, user, updated: bool = False) -> str:
    """Re-prompt built from a stored record (used after an edit or cancel)."""
    if rec.record_type == "mileage":
        return _mileage_prompt(rec.miles or 0, rec.vehicle_type or user.vehicle_type, user, updated=updated)

    if rec.record_type == "expense":
        return _expense_prompt(rec.platform_or_vendor or rec.category,
                               rec.amount or 0, updated=updated)

    if rec.record_type == "income":
        return _income_prompt(rec, user)

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

    if rec.record_type == "expense":
        # Allow re-typing the whole "description £amount", or just a new amount.
        parsed = extract.parse_expense_text(body)
        if parsed and not parsed["amount_missing"]:
            e = parsed["entries"][0]
            rec.platform_or_vendor = e["description"][:64]
            rec.amount = e["amount"]
            rec.category = e["category"]
            return True
        amount = _parse_amount(body)
        if amount is None:
            return False
        rec.amount = amount
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

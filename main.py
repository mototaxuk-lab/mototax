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

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

import config
import export
import extract
import twilio_client as wa
from models import (
    ExportLink, Record, SessionLocal, get_or_create_user, init_db,
    latest_pending, make_export_link, now,
)

app = FastAPI(title="Courier Tax & Records Assistant")

WELCOME = (
    "👋 Welcome! I help UK couriers keep tax-ready records.\n\n"
    "Just send me:\n"
    "• an earnings screenshot (Uber Eats / Deliveroo / Just Eat)\n"
    "• a photo of a fuel/repair/equipment receipt\n"
    "• your weekly mileage (e.g. \"145 miles\")\n\n"
    "I'll read it, you confirm, and I keep a clean log.\n"
    "Type CSV any time for an accountant-ready export."
)

HELP = (
    "Send a receipt or earnings screenshot, or type your mileage like \"145 miles\".\n"
    "After I read something, reply 1 to confirm or 2 to discard.\n"
    "Type CSV for your export, or SUMMARY for your totals so far."
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


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
        if created:
            wa.send_whatsapp(number, WELCOME)

        if num_media > 0:
            _handle_media(db, user.id, number, params, num_media)
            return

        _handle_text(db, user.id, number, body)
    except Exception as exc:  # never let a background task die silently
        print(f"[handle_inbound] error: {exc!r}")
    finally:
        db.close()


def _handle_media(db, user_id, number, params, num_media) -> None:
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
            user_id=user_id,
            record_type=data["record_type"],
            record_date=data["record_date"],
            platform_or_vendor=data["platform_or_vendor"],
            category=data["category"],
            amount=data["amount"],
            miles=data["miles"],
            source_type=source,
            confirmation_status="pending",
            confidence=data["confidence"],
            original_media_url=url,
            notes=data["notes"],
        )
        db.add(record)
        db.commit()
        wa.send_whatsapp(number, _confirmation_prompt(data))


def _handle_text(db, user_id, number, body) -> None:
    low = body.lower()

    if low in ("1", "confirm", "yes", "y"):
        rec = latest_pending(db, user_id)
        if not rec:
            wa.send_whatsapp(number, "Nothing waiting to confirm. Send a photo or your mileage.")
            return
        rec.confirmation_status = "estimated" if rec.source_type == "user_estimate" else "confirmed"
        rec.confirmed_at = now()
        db.commit()
        wa.send_whatsapp(number, "✅ Saved.")
        return

    if low in ("2", "edit", "no", "n"):
        rec = latest_pending(db, user_id)
        if rec:
            rec.confirmation_status = "rejected"
            db.commit()
        wa.send_whatsapp(number, "Discarded. Send it again or type the correct value.")
        return

    if low in ("csv", "export", "report"):
        token = make_export_link(db, user_id)
        if config.PUBLIC_BASE_URL:
            wa.send_whatsapp(number, f"Your CSV (link valid 24h):\n{config.PUBLIC_BASE_URL}/export/{token}")
        else:
            wa.send_whatsapp(number, "Export link isn't configured yet (set PUBLIC_BASE_URL).")
        return

    if low in ("summary", "total", "totals"):
        wa.send_whatsapp(number, export.weekly_summary(db, user_id))
        return

    if low in ("help", "hi", "hello", "start", "menu"):
        wa.send_whatsapp(number, HELP)
        return

    # Try to read it as a mileage entry ("145 miles", or just "145").
    mileage = extract.parse_mileage_text(body)
    if mileage:
        record = Record(
            user_id=user_id,
            record_type="mileage",
            record_date=mileage["record_date"],
            category="mileage",
            miles=mileage["miles"],
            source_type=mileage["source_hint"],
            confirmation_status="pending",
            confidence=mileage["confidence"],
            notes=mileage["notes"],
        )
        db.add(record)
        db.commit()
        wa.send_whatsapp(
            number,
            f"Logged {mileage['miles']:.0f} business miles (user-entered).\n"
            f"Reply 1 to confirm or 2 to discard.",
        )
        return

    wa.send_whatsapp(number, HELP)


def _confirmation_prompt(data: dict) -> str:
    if data["record_type"] == "mileage":
        detail = f"{data['miles']:.0f} miles"
    else:
        amount = f"£{data['amount']:.2f}" if data["amount"] is not None else "£?"
        vendor = data["platform_or_vendor"] or data["category"]
        detail = f"{vendor}, {amount}, {data['category']}"

    msg = f"Detected: {detail} (dated {data['record_date']}).\nReply 1 to confirm, 2 to discard."
    if data["confidence"] < config.CONFIDENCE_WARN_THRESHOLD:
        msg += "\n⚠️ I'm not fully sure on this one — please double-check the figures."
    if data["notes"]:
        msg += f"\nNote: {data['notes']}"
    return msg

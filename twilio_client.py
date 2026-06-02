"""Twilio WhatsApp helpers: send messages, verify webhooks, download media."""
import json

import requests
from twilio.rest import Client
from twilio.request_validator import RequestValidator

import config

_client = (
    Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    if config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN
    else None
)
_validator = RequestValidator(config.TWILIO_AUTH_TOKEN) if config.TWILIO_AUTH_TOKEN else None


def send_whatsapp(to: str, body: str) -> None:
    """Send a WhatsApp message. `to` is a bare number or 'whatsapp:+44...'."""
    if not to.startswith("whatsapp:"):
        to = "whatsapp:" + to
    if _client is None:
        print(f"[twilio disabled] would send to {to}: {body}")
        return
    _client.messages.create(from_=config.TWILIO_WHATSAPP_FROM, to=to, body=body)


def send_whatsapp_template(to: str, content_sid: str, variables: dict | None = None) -> None:
    """Send an approved WhatsApp template (required for business-initiated messages
    outside the 24-hour service window, e.g. the weekly reminder)."""
    if not to.startswith("whatsapp:"):
        to = "whatsapp:" + to
    if _client is None:
        print(f"[twilio disabled] would send template {content_sid} to {to}")
        return
    kwargs = {"from_": config.TWILIO_WHATSAPP_FROM, "to": to, "content_sid": content_sid}
    if variables:
        kwargs["content_variables"] = json.dumps(variables)
    _client.messages.create(**kwargs)


def verify_signature(signature: str, params: dict) -> bool:
    """Confirm a webhook really came from Twilio.

    Returns True if verification passes OR if it's intentionally disabled
    (no WEBHOOK_URL / auth token configured — useful for local testing).
    """
    if not config.WEBHOOK_URL or _validator is None:
        return True
    return _validator.validate(config.WEBHOOK_URL, params, signature or "")


def download_media(url: str) -> tuple[bytes, str]:
    """Download a media file from a Twilio URL (requires Basic Auth)."""
    resp = requests.get(
        url,
        auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN),
        timeout=30,
    )
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
    return resp.content, content_type

"""Messaging transport abstraction.

The app talks to a generic ``Messenger`` rather than to Twilio directly, so the
WhatsApp provider is a swappable detail:

* ``TwilioMessenger`` — the production backend (Twilio WhatsApp).
* ``ConsoleMessenger`` — prints what would be sent; needs no account. Used for
  local dev and tests, and as a safe fallback when no provider is configured.

Selection is driven by ``config.MESSAGING_PROVIDER`` (default ``"auto"``: use
Twilio when its credentials are present, otherwise the console).

To add another provider (e.g. the Meta WhatsApp Cloud API), implement
``Messenger`` and register it in ``_build_messenger()`` — no call sites change,
because the rest of the app only uses the module-level functions at the bottom.
"""
from __future__ import annotations

import abc
import json

import requests

import config


class Messenger(abc.ABC):
    """A WhatsApp transport. Implementations wrap one provider's API."""

    @abc.abstractmethod
    def send(self, to: str, body: str) -> None:
        """Send a free-form message (valid inside the 24h service window)."""

    @abc.abstractmethod
    def send_template(self, to: str, template: str, variables: dict | None = None) -> None:
        """Send a pre-approved template (for business-initiated messages)."""

    @abc.abstractmethod
    def download_media(self, url: str) -> tuple[bytes, str]:
        """Fetch inbound media, returning (bytes, content_type)."""

    def verify_signature(self, signature: str, params: dict) -> bool:
        """Confirm an inbound webhook is authentic. Default: accept (no-op)."""
        return True


def _normalise(to: str) -> str:
    return to if to.startswith("whatsapp:") else "whatsapp:" + to


class ConsoleMessenger(Messenger):
    """Prints messages instead of sending them. No external service required."""

    def send(self, to: str, body: str) -> None:
        print(f"[console] → {_normalise(to)}: {body}")

    def send_template(self, to: str, template: str, variables: dict | None = None) -> None:
        print(f"[console] → {_normalise(to)} template {template} vars={variables or {}}")

    def download_media(self, url: str) -> tuple[bytes, str]:
        raise RuntimeError("ConsoleMessenger cannot download media (no provider configured)")


class TwilioMessenger(Messenger):
    """Twilio WhatsApp backend. Imports the SDK lazily so console-only
    environments don't need the `twilio` package at all."""

    def __init__(self) -> None:
        from twilio.rest import Client
        from twilio.request_validator import RequestValidator

        self._client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        self._validator = RequestValidator(config.TWILIO_AUTH_TOKEN)

    def send(self, to: str, body: str) -> None:
        self._client.messages.create(
            from_=config.TWILIO_WHATSAPP_FROM, to=_normalise(to), body=body)

    def send_template(self, to: str, template: str, variables: dict | None = None) -> None:
        kwargs = {"from_": config.TWILIO_WHATSAPP_FROM, "to": _normalise(to),
                  "content_sid": template}
        if variables:
            kwargs["content_variables"] = json.dumps(variables)
        self._client.messages.create(**kwargs)

    def download_media(self, url: str) -> tuple[bytes, str]:
        resp = requests.get(
            url, auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN), timeout=30)
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type", "image/jpeg").split(";")[0]

    def verify_signature(self, signature: str, params: dict) -> bool:
        # No WEBHOOK_URL configured ⇒ verification is intentionally off (local dev).
        if not config.WEBHOOK_URL:
            return True
        return self._validator.validate(config.WEBHOOK_URL, params, signature or "")


def _build_messenger() -> Messenger:
    provider = (config.MESSAGING_PROVIDER or "auto").lower()
    has_twilio = bool(config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN)

    if provider == "console":
        return ConsoleMessenger()
    if provider in ("twilio", "auto"):
        if has_twilio:
            return TwilioMessenger()
        if provider == "twilio":
            print("[messaging] MESSAGING_PROVIDER=twilio but Twilio credentials are "
                  "missing — falling back to console.")
        return ConsoleMessenger()
    raise ValueError(f"Unknown MESSAGING_PROVIDER: {provider!r} "
                     "(expected 'auto', 'twilio', or 'console').")


# The active transport for this process.
messenger: Messenger = _build_messenger()


# --- Module-level API ---------------------------------------------------------
# The rest of the app calls these (imported as `wa`), unaware of the backend.

def send_whatsapp(to: str, body: str) -> None:
    messenger.send(to, body)


def send_whatsapp_template(to: str, content_sid: str, variables: dict | None = None) -> None:
    messenger.send_template(to, content_sid, variables)


def verify_signature(signature: str, params: dict) -> bool:
    return messenger.verify_signature(signature, params)


def download_media(url: str) -> tuple[bytes, str]:
    return messenger.download_media(url)

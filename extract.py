"""Claude-powered extraction.

extract_from_image()  -> parses an earnings screenshot / receipt / odometer photo
parse_mileage_text()  -> pulls a mileage number out of a free-text message

Both return a plain dict matching the Record fields. We never auto-finalise:
the value is shown back to the courier for confirmation before it counts.
"""
import base64
import datetime as dt
import json
import re

import anthropic

import config

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY) if config.ANTHROPIC_API_KEY else None

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

EXTRACT_SYSTEM = """You read photos sent by self-employed UK food-delivery couriers \
and turn them into a single structured accounting record. The image is one of:
- an earnings screenshot from Uber Eats, Deliveroo or Just Eat (record_type "income")
- a receipt for fuel, repairs, insurance, equipment, etc. (record_type "expense")
- an odometer / mileage photo (record_type "mileage")

Return ONLY a JSON object, no prose and no markdown fences, with these keys:
  record_type        "income" | "expense" | "mileage"
  platform_or_vendor short name, e.g. "Uber Eats", "Shell", "Halfords" (empty if unknown)
  category           one of: platform_income, fuel, insurance, repair, equipment,
                     phone, parking, other  (for mileage use "mileage")
  amount             number in GBP for income/expense, else null
  miles              number for mileage records, else null
  record_date        the date shown on the document in yyyy-mm-dd, or null if not visible
  confidence         your confidence from 0 to 1 that the figures are correct
  notes              anything ambiguous the human should double-check (empty if none)

Read amounts exactly. If a figure is unclear, lower the confidence and say so in notes.
Never invent a value you cannot see."""


def _strip_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _today() -> str:
    return dt.date.today().isoformat()


def extract_from_image(image_bytes: bytes, media_type: str) -> dict:
    """Send one image to Claude and return a normalised record dict."""
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    if media_type not in _ALLOWED_IMAGE_TYPES:
        media_type = "image/jpeg"  # Twilio occasionally mislabels; jpeg is a safe default

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")

    resp = _client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=512,
        system=EXTRACT_SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": "Extract this document into the JSON record."},
            ],
        }],
    )
    raw = "".join(block.text for block in resp.content if block.type == "text")
    return _normalise(raw)


def _normalise(raw: str) -> dict:
    """Parse Claude's JSON and coerce it into safe, typed fields."""
    try:
        data = json.loads(_strip_json(raw))
    except (json.JSONDecodeError, ValueError):
        # If parsing fails, return a low-confidence stub so the flow degrades gracefully.
        return {
            "record_type": "expense", "platform_or_vendor": "", "category": "other",
            "amount": None, "miles": None, "record_date": _today(),
            "confidence": 0.0, "notes": "Could not read this automatically — please check.",
        }

    rt = (data.get("record_type") or "expense").lower()
    if rt not in ("income", "expense", "mileage"):
        rt = "expense"

    def num(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "record_type": rt,
        "platform_or_vendor": (data.get("platform_or_vendor") or "")[:64],
        "category": (data.get("category") or ("mileage" if rt == "mileage" else "other"))[:32],
        "amount": num(data.get("amount")),
        "miles": num(data.get("miles")),
        "record_date": data.get("record_date") or _today(),
        "confidence": max(0.0, min(1.0, num(data.get("confidence")) or 0.0)),
        "notes": (data.get("notes") or "")[:512],
    }


_MILES_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mi|mile|miles|m)\b", re.IGNORECASE)
_BARE_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def parse_mileage_text(body: str) -> dict | None:
    """Cheap, no-API mileage parse for messages like '145 miles' or just '145'.

    Returns a record dict, or None if the text isn't a mileage entry.
    """
    match = _MILES_RE.search(body) or _BARE_NUMBER_RE.match(body)
    if not match:
        return None
    miles = float(match.group(1))
    if miles <= 0 or miles > 2000:  # sanity bound for a single entry
        return None
    return {
        "record_type": "mileage",
        "platform_or_vendor": "",
        "category": "mileage",
        "amount": None,
        "miles": miles,
        "record_date": _today(),
        "confidence": 1.0,            # the user typed it, but...
        "source_hint": "user_estimate",  # ...it's self-reported, so label it as such
        "notes": "User-entered weekly mileage. Add an odometer/route photo for stronger evidence.",
    }

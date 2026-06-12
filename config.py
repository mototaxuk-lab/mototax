"""Central configuration. All secrets come from environment variables.

On Railway you set these under your service's "Variables" tab.
Locally, copy .env.example to .env and fill it in (python-dotenv loads it).
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is only needed for local dev


# --- Anthropic ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Haiku 4.5 is the right tier for image extraction: cheapest current model, supports vision.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# --- Messaging transport ---
# Which WhatsApp backend to use: "auto" (Twilio if its creds are set, else the
# console/no-op backend), "twilio", or "console". See messaging.py.
MESSAGING_PROVIDER = os.environ.get("MESSAGING_PROVIDER", "auto")

# --- Twilio ---
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
# Your Twilio WhatsApp sender, e.g. "whatsapp:+14155238886" (the sandbox number to start).
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "")

# Full public URL Twilio calls, e.g. "https://your-app.up.railway.app/webhook/whatsapp".
# Used to verify Twilio's request signature. Leave blank locally to skip verification.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")

# Public base URL of this service, used to build CSV download links.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

# --- Database ---
# Railway injects DATABASE_URL automatically when you add a Postgres plugin.
# Falls back to a local SQLite file so you can run with zero setup.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./courier.db")

# Below this confidence the user is warned to check the value carefully.
CONFIDENCE_WARN_THRESHOLD = float(os.environ.get("CONFIDENCE_WARN_THRESHOLD", "0.6"))

# --- Weekly reminder ---
# In-process scheduler that nudges couriers to log their miles.
REMINDERS_ENABLED = os.environ.get("REMINDERS_ENABLED", "true").lower() in ("1", "true", "yes")
REMINDER_DAY = os.environ.get("REMINDER_DAY", "sun")              # APScheduler day_of_week
REMINDER_HOUR_UTC = int(os.environ.get("REMINDER_HOUR_UTC", "18"))  # 18:00 UTC ≈ 7pm BST (Sunday evening)
# Don't nudge users who already logged mileage within this many days.
REMIND_SKIP_DAYS = int(os.environ.get("REMIND_SKIP_DAYS", "6"))
# Approved WhatsApp template for business-initiated (outside-24h) sends. If empty,
# we fall back to a freeform message, which only delivers inside the 24h service
# window (or the Twilio sandbox). Real Sunday delivery needs an approved template.
REMINDER_TEMPLATE_SID = os.environ.get("REMINDER_TEMPLATE_SID", "")
# Secret that guards the manual POST /internal/run-reminders trigger.
CRON_SECRET = os.environ.get("CRON_SECRET", "")

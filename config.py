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

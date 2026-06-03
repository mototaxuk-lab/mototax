"""Local chat harness — talk to the bot in your terminal, no Twilio needed.

This drives the SAME code path as the live WhatsApp webhook (main.handle_inbound):
onboarding, confirm/edit/delete, mileage parsing, multi-vehicle, summaries, CSV.
The only thing swapped out is the transport — instead of sending via Twilio, the
bot's replies are printed here. Uses an isolated local SQLite DB so it never
touches production data.

Run:
    python chat.py                # continue in local_chat.db (state persists between runs)
    python chat.py --fresh        # start from a clean DB
    python chat.py --number +447700900123   # pretend to be a specific number

In the chat:
    just type            send a text message to the bot (e.g. "120 miles", "1", "summary")
    /img <path>          simulate sending an image (needs ANTHROPIC_API_KEY for extraction)
    /reset               delete the current user and start onboarding again
    /whoami              show the current phone number
    /help                show these commands
    quit / exit / /q     leave the harness, back to the terminal (or Ctrl-D)

Note: "quit"/"exit" close the harness. To test the bot's own in-conversation
exit keyword, type "end" — that passes through to the bot.
"""
import os
import sys

# Isolate from any real config BEFORE importing the app. No Twilio, local DB only.
os.environ.setdefault("DATABASE_URL", "sqlite:///./local_chat.db")
os.environ.setdefault("WEBHOOK_URL", "")          # disables signature checks
os.environ.setdefault("TWILIO_ACCOUNT_SID", "")   # disables real Twilio client
os.environ.setdefault("TWILIO_AUTH_TOKEN", "")
os.environ.setdefault("REMINDERS_ENABLED", "false")
os.environ.setdefault("PUBLIC_BASE_URL", "http://localhost:8080")

import config  # noqa: E402
import extract  # noqa: E402
import main  # noqa: E402
import twilio_client as wa  # noqa: E402
from models import SessionLocal, User, init_db  # noqa: E402


# --- swap the transport: print instead of send -------------------------------

def _print_bot(to: str, body: str) -> None:
    print("\n\033[36m🤖 bot\033[0m")
    for line in body.split("\n"):
        print(f"   {line}")
    print()


def _print_bot_template(to: str, content_sid: str, variables=None) -> None:
    _print_bot(to, f"[template {content_sid}] vars={variables or {}}")


wa.send_whatsapp = _print_bot
wa.send_whatsapp_template = _print_bot_template
main.wa.send_whatsapp = _print_bot          # main imported it as `wa`
main.wa.send_whatsapp_template = _print_bot_template


# --- media simulation: read a local file instead of downloading from Twilio ---

def _send_image(number: str, path: str) -> None:
    if not os.path.exists(path):
        print(f"   (no file at {path})")
        return
    if not config.ANTHROPIC_API_KEY:
        print("   (image extraction needs ANTHROPIC_API_KEY in your environment — "
              "skipping. Text flows work without it.)")
        return
    ext = os.path.splitext(path)[1].lower()
    ctype = {".png": "image/png", ".webp": "image/webp",
             ".gif": "image/gif"}.get(ext, "image/jpeg")
    with open(path, "rb") as fh:
        data = fh.read()
    orig = wa.download_media
    wa.download_media = lambda _url: (data, ctype)
    main.wa.download_media = wa.download_media
    try:
        params = {
            "From": f"whatsapp:{number}", "Body": "", "NumMedia": "1",
            "MediaUrl0": f"file://{path}", "MediaContentType0": ctype,
        }
        main.handle_inbound(params)
    finally:
        wa.download_media = orig
        main.wa.download_media = orig


# --- helpers ------------------------------------------------------------------

def _reset_user(number: str) -> None:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.number == number).first()
        if u:
            db.delete(u)  # records cascade if FK set; otherwise harmless for a demo DB
            db.commit()
            print(f"   (reset {number})")
        else:
            print("   (no existing user to reset)")
    finally:
        db.close()


def main_loop() -> None:
    fresh = "--fresh" in sys.argv
    number = "+447700900123"
    if "--number" in sys.argv:
        number = sys.argv[sys.argv.index("--number") + 1]

    if fresh and os.path.exists("local_chat.db"):
        os.remove("local_chat.db")
        print("(started from a fresh DB)")

    init_db()

    print("=" * 60)
    print(f"  mototax local chat — you are {number}")
    print("  type a message and press enter.  /help for commands, 'quit' to exit.")
    print("=" * 60)
    print("  (send anything to begin — a brand-new number triggers onboarding)")

    while True:
        try:
            line = input("\n\033[32myou\033[0m › ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye 👋")
            return

        if not line:
            continue
        if line.lower() in ("quit", "exit", "/quit", "/exit", "/q"):
            print("bye 👋")
            return
        if line == "/help":
            print(__doc__)
            continue
        if line == "/whoami":
            print(f"   you are {number}")
            continue
        if line == "/reset":
            _reset_user(number)
            continue
        if line.startswith("/img"):
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                print("   usage: /img <path-to-image>")
                continue
            _send_image(number, parts[1].strip())
            continue

        # Normal text message → same path Twilio would trigger.
        params = {"From": f"whatsapp:{number}", "Body": line, "NumMedia": "0"}
        main.handle_inbound(params)


if __name__ == "__main__":
    main_loop()

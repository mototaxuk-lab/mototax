"""Terminal chat interface — no Twilio required.

Usage:
    python cli.py
    python cli.py --phone +447700900001   # use a specific test number
    python cli.py --image path/to/receipt.jpg
"""
import argparse
import sys

# Patch twilio_client before anything else imports it so we never need Twilio keys.
import types, importlib

_fake_twilio = types.ModuleType("twilio_client")

_output_lines: list[str] = []


def _send(to: str, body: str) -> None:
    print(f"\nBot: {body}\n")


_fake_twilio.send_whatsapp = _send
_fake_twilio.send_whatsapp_template = lambda *a, **kw: None
_fake_twilio.verify_signature = lambda *a, **kw: True
_fake_twilio.download_media = lambda url: (open(url, "rb").read(), "image/jpeg")

sys.modules["twilio_client"] = _fake_twilio

# Now safe to import app logic.
from models import init_db  # noqa: E402
import main  # noqa: E402  (registers handle_inbound etc.)


def run(phone: str) -> None:
    init_db()
    print("Courier Tax Assistant — terminal mode")
    print("Type your message and press Enter. Ctrl-C to quit.")
    print("To send an image, type: /image path/to/file.jpg\n")

    # Trigger a first contact so onboarding starts.
    main.handle_inbound({"From": f"whatsapp:{phone}", "Body": "", "NumMedia": "0"})

    while True:
        try:
            text = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            break

        if not text:
            continue

        if text.lower().startswith("/image "):
            path = text[7:].strip()
            params = {
                "From": f"whatsapp:{phone}",
                "Body": "",
                "NumMedia": "1",
                "MediaUrl0": path,
                "MediaContentType0": "image/jpeg",
            }
        else:
            params = {
                "From": f"whatsapp:{phone}",
                "Body": text,
                "NumMedia": "0",
            }

        main.handle_inbound(params)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", default="+447700900000", help="Simulated user phone number")
    args = parser.parse_args()
    run(args.phone)

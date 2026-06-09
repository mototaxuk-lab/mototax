"""Flow C — vehicle settings (and the wider settings menu).

A small text state machine. WhatsApp would render these option lists as buttons;
in the terminal the user replies with the option number (or a vehicle word).

State is stored on User.settings_state as "name" or "name:payload". The single
entry point is handle(): it returns True if it consumed the message (the user is
in the settings flow, opened it, or used a natural-language vehicle intent),
otherwise False so the normal router carries on.

Design rules from the spec:
- Buttons (numbers) drive every actual change; nothing is saved silently.
- Changing main/default affects future mileage only — confirmed records never change.
- Vehicles are tracked by *type*, not by named individual vehicles.
"""
from __future__ import annotations

import tax
import config
import twilio_client as wa
from models import make_export_link

_ORDER = ("car_van", "motorbike", "bicycle")

# Free-text vehicle words → canonical key (covers "scooter", "ebike", etc.).
_WORDS = {
    "car": "car_van", "van": "car_van",
    "motorbike": "motorbike", "motorcycle": "motorbike", "moped": "motorbike",
    "scooter": "motorbike",
    "bicycle": "bicycle", "bike": "bicycle", "ebike": "bicycle",
    "e-bike": "bicycle", "cycle": "bicycle",
}


# --- vehicle profile helpers -------------------------------------------------

def registered(user) -> list[str]:
    """All vehicle types the user has, main first, in canonical order."""
    have = {tax.normalise_vehicle(user.vehicle_type)}
    for v in (user.extra_vehicles or "").split(","):
        v = v.strip()
        if v in tax.VEHICLE_RATES:
            have.add(v)
    return [v for v in _ORDER if v in have]


def default_vehicle(user) -> str:
    """Vehicle assumed when mileage is sent without one (falls back to main)."""
    return tax.normalise_vehicle(user.default_vehicle or user.vehicle_type)


def _set_extras(user, vehicles: set[str]) -> None:
    main = tax.normalise_vehicle(user.vehicle_type)
    extras = [v for v in _ORDER if v in vehicles and v != main]
    user.extra_vehicles = ",".join(extras)


def _add_extra(user, vehicle: str) -> None:
    extras = set(registered(user)) | {vehicle}
    _set_extras(user, extras)


def _remove_vehicle(user, vehicle: str) -> None:
    extras = set(registered(user)) - {vehicle}
    _set_extras(user, extras)


# --- small option-list / parsing helpers ------------------------------------

def _vlabel(vt: str) -> str:
    return f"{tax.emoji(vt)} {tax.label(vt)}"


def _numbered(options: list[str]) -> str:
    return "\n".join(f"{i}. {opt}" for i, opt in enumerate(options, 1))


def _pick(body: str, keys: list[str]) -> str | None:
    """Resolve a reply to one of `keys` by number or by vehicle word."""
    t = body.strip().lower()
    if t.isdigit():
        i = int(t) - 1
        return keys[i] if 0 <= i < len(keys) else None
    word = _WORDS.get(t.replace(" ", ""))
    return word if word in keys else None


def _is(body: str, *words: str) -> bool:
    return body.strip().lower() in words


def _vehicle_word(body: str) -> str | None:
    return _WORDS.get(body.strip().lower().replace(" ", ""))


# --- message builders --------------------------------------------------------

def _vehicle_settings_overview(user) -> str:
    extras = [v for v in registered(user) if v != tax.normalise_vehicle(user.vehicle_type)]
    other = ", ".join(tax.label(v) for v in extras) if extras else "none"
    return (
        "Vehicle settings\n\n"
        f"Main vehicle: {tax.label(user.vehicle_type)}\n"
        f"Default vehicle: {tax.label(default_vehicle(user))}\n"
        f"Other vehicles: {other}\n\n"
        "What do you want to do?\n\n"
        + _numbered([
            "Change main vehicle",
            "Add another vehicle",
            "Set default vehicle",
            "Remove vehicle",
            "Back",
        ])
    )


def _add_vehicle_prompt() -> str:
    return ("Which vehicle do you want to add?\n\n"
            + _numbered([_vlabel(v) for v in _ORDER] + ["Cancel"]))


def _saved_vehicle_prompt(user, lead: str, include_cancel: bool = True) -> tuple[str, list[str]]:
    keys = registered(user)
    opts = [_vlabel(v) for v in keys]
    if include_cancel:
        opts.append("Cancel")
    return lead + "\n\n" + _numbered(opts), keys


# --- main settings menu ------------------------------------------------------

_MAIN_MENU = (
    "What do you want to update?\n\n"
    + _numbered([
        "Vehicle settings",
        "Tax estimate level",
        "Reminder settings",
        "Account status",
        "Export or delete my data",
        "Help",
    ])
)

# Reminder day options (Flow H §8): label -> (day_abbrev, time_label).
_REMINDER_OPTIONS = [
    ("Sunday evening", "sun", "evening"),
    ("Monday morning", "mon", "morning"),
    ("Friday evening", "fri", "evening"),
]
_DAY_NAMES = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
              "thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}

_TAX_MENU = (
    "Which tax rate should I use for rough tax-benefit estimates?\n\n"
    + _numbered([
        "Basic estimate — 20%",
        "Higher estimate — 40%",
        "Likely no income tax — 0%",
    ])
)
_TAX_RATES = [0.20, 0.40, 0.0]
_TAX_LABELS = {0.20: "Basic estimate — 20%", 0.40: "Higher estimate — 40%",
               0.0: "Likely no income tax — 0%"}


def _go(user, state: str | None) -> None:
    user.settings_state = state


# --- entry point -------------------------------------------------------------

def handle(db, user, number, body) -> bool:
    """Process a settings message. Returns True if it was consumed."""
    low = body.strip().lower()

    # Open the settings menu from anywhere.
    if not user.settings_state and low in ("settings", "setting", "menu"):
        _go(user, "menu")
        db.commit()
        wa.send_whatsapp(number, _MAIN_MENU)
        return True

    # Natural-language vehicle intents (only when not already mid-flow).
    if not user.settings_state and _nl_intent(db, user, number, low):
        return True

    if not user.settings_state:
        return False

    state, _, payload = user.settings_state.partition(":")

    # Universal escape hatches.
    if low in ("done", "exit", "quit"):
        _go(user, None)
        db.commit()
        wa.send_whatsapp(number, "Settings closed. Send your miles any time. 👍")
        return True

    if low in ("menu", "settings", "setting"):
        _back_to_menu(db, user, number)
        return True

    handler = _STATES.get(state)
    if handler is None:  # unknown state — reset gracefully
        _go(user, None)
        db.commit()
        wa.send_whatsapp(number, _MAIN_MENU)
        return True
    handler(db, user, number, low, payload)
    return True


# --- per-state handlers ------------------------------------------------------

def _h_menu(db, user, number, low, payload):
    choice = low
    if choice in ("1", "vehicle settings", "vehicle", "vehicles"):
        _go(user, "vehicle")
        db.commit()
        wa.send_whatsapp(number, _vehicle_settings_overview(user))
    elif choice in ("2", "tax estimate level", "tax"):
        _go(user, "tax")
        db.commit()
        wa.send_whatsapp(number, _TAX_MENU)
    elif choice in ("3", "reminder settings", "reminder", "reminders"):
        _go(user, "reminder")
        db.commit()
        wa.send_whatsapp(number, _reminder_menu(user))
    elif choice in ("4", "account status", "account"):
        _go(user, "account")
        db.commit()
        wa.send_whatsapp(number, _account_status(user))
    elif choice in ("5", "export or delete my data", "export", "delete", "data"):
        _go(user, "data")
        db.commit()
        wa.send_whatsapp(number, "What would you like to do?\n\n"
                         + _numbered(["Download my records", "Delete my data", "Back"]))
    elif choice in ("6", "help", "what can you do", "what can you do?"):
        wa.send_whatsapp(number, _HELP_MESSAGE)
        _go(user, None)
        db.commit()
    else:
        wa.send_whatsapp(number, "Please reply with an option number.\n\n" + _MAIN_MENU)


_HELP_MESSAGE = (
    "You can send me:\n\n"
    "• delivery miles, e.g. \"120 miles\"\n"
    "• earnings, e.g. \"Uber Eats £320\"\n"
    "• earnings screenshots\n"
    "• courier expenses, e.g. \"Delivery bag £45\"\n"
    "• receipts for temporary OCR\n\n"
    "I organise confirmed records into summaries and Excel record packs for "
    "accountant review.\n\n"
    "Type SUMMARY, EXPORT, or SETTINGS anytime."
)


def _h_data(db, user, number, low, payload):
    if low in ("1", "download my records", "download", "export"):
        token = make_export_link(db, user.id, fmt="xlsx")
        if config.PUBLIC_BASE_URL:
            wa.send_whatsapp(number, "Your Excel record pack (link valid 24h):\n"
                             f"{config.PUBLIC_BASE_URL}/export/{token}")
        else:
            wa.send_whatsapp(number, "Export link isn't configured yet "
                             "(set PUBLIC_BASE_URL).")
        _back_to_menu(db, user, number)
    elif low in ("2", "delete my data", "delete"):
        _go(user, "delete_confirm")
        db.commit()
        wa.send_whatsapp(
            number,
            "Delete your stored records?\n\n"
            "This will remove your confirmed records and settings from the service.\n\n"
            "This cannot be undone.\n\n"
            + _numbered(["Confirm delete", "Cancel"]))
    elif low in ("3", "back", "cancel"):
        _back_to_menu(db, user, number)
    else:
        wa.send_whatsapp(number, "Please reply 1, 2 or 3.")


def _h_delete_confirm(db, user, number, low, payload):
    if low in ("1", "confirm delete", "confirm", "yes"):
        from models import Record, ExportLink
        db.query(Record).filter(Record.user_id == user.id).delete()
        db.query(ExportLink).filter(ExportLink.user_id == user.id).delete()
        user.reminder_status = "off"
        _go(user, None)
        db.commit()
        wa.send_whatsapp(
            number,
            "Your data deletion request has been received.\n\n"
            "Your records will be deleted according to our data deletion process.")
    else:
        wa.send_whatsapp(number, "No change made — your records are safe.")
        _back_to_menu(db, user, number)


# --- Flow I: access / subscription (no hard-coded pricing) -------------------

_PLAN_LABELS = {
    "beta": "Free beta", "trial": "Free trial", "active": "Active plan",
    "paused": "Paused", "cancelled": "Cancelled", "partner": "Partner-sponsored",
}


def _account_status(user) -> str:
    rem = ("off" if user.reminder_status == "off"
           else f"{_DAY_NAMES.get(user.reminder_day, 'Sunday')} {user.reminder_time_label}")
    plan = _PLAN_LABELS.get(getattr(user, "plan_status", "beta"), "Free beta")
    return (
        "Account status:\n\n"
        f"Plan: {plan}\n"
        "Standard export: Excel record pack\n"
        f"Reminder: {rem}\n\n"
        "What do you want to do?\n\n"
        + _numbered(["Payment / access", "Export records", "Back"])
    )


def _payment_options(status: str) -> tuple[str, list[str]]:
    """Body copy + option labels for the current access model (Flow I §1–5)."""
    if status in ("beta", "trial"):
        return ("You're in the free beta ✅\n\n"
                "You can use weekly tracking and record exports while we test the "
                "service.\n\nPaid access isn't available yet — we'll let you know "
                "before any paid access is introduced, and pricing will always be "
                "shown before payment.",
                ["Notify me about pricing", "Export my records", "Back"])
    if status == "active":
        return ("Your plan is active ✅\n\n"
                "You can manage your subscription securely through Stripe. We don't "
                "store your card details.",
                ["Manage subscription", "Cancel access", "Export my records", "Back"])
    if status == "paused":
        return ("Your access is currently paused.\n\n"
                "You can still export previous records for a limited time, but new "
                "tracking and export features require active access.",
                ["Reactivate access", "Export my records", "Back"])
    if status == "partner":
        return ("Your access is covered through a partner programme ✅\n\n"
                "You can use the included tracking and export features while your "
                "sponsored access is active.",
                ["Export my records", "Back"])
    return ("Your access is cancelled.\n\nYou can still export previous records for a "
            "limited time.", ["Reactivate access", "Export my records", "Back"])


def _payment_message(user) -> str:
    body, opts = _payment_options(getattr(user, "plan_status", "beta"))
    return body + "\n\n" + _numbered(opts)


def _h_account(db, user, number, low, payload):
    if low in ("1", "payment / access", "payment", "access", "subscription"):
        _go(user, "payment")
        db.commit()
        wa.send_whatsapp(number, _payment_message(user))
    elif low in ("2", "export records", "export"):
        _data_download(db, user, number)
        _back_to_menu(db, user, number)
    elif low in ("3", "back", "cancel"):
        _back_to_menu(db, user, number)
    else:
        wa.send_whatsapp(number, "Please reply 1, 2 or 3.")


def _h_payment(db, user, number, low, payload):
    status = getattr(user, "plan_status", "beta")
    _, opts = _payment_options(status)
    # Resolve a numeric reply to its label, then act on the label.
    label = low
    if low.isdigit() and 1 <= int(low) <= len(opts):
        label = opts[int(low) - 1].lower()

    if label in ("back", "cancel"):
        _back_to_menu(db, user, number)
    elif label == "notify me about pricing":
        wa.send_whatsapp(number, "👍 I'll let you know when paid access and pricing "
                         "are available.")
        _back_to_menu(db, user, number)
    elif label in ("manage subscription", "reactivate access"):
        wa.send_whatsapp(number, "You'll be able to manage your access securely "
                         "through Stripe.\n\nSecure payments aren't switched on yet — "
                         "you'll get a payment link here once paid access launches.")
        _back_to_menu(db, user, number)
    elif label == "cancel access":
        _go(user, "cancel_confirm")
        db.commit()
        wa.send_whatsapp(number, "Cancel your access?\n\nIf you cancel, tracking will "
                         "stop after your current access period. You can still export "
                         "previous records for a limited time.\n\n"
                         + _numbered(["Confirm cancellation", "Keep access"]))
    elif "export" in label:
        _data_download(db, user, number)
        _back_to_menu(db, user, number)
    else:
        wa.send_whatsapp(number, _payment_message(user))


def _h_cancel_confirm(db, user, number, low, payload):
    if low in ("1", "confirm cancellation", "confirm", "yes"):
        user.plan_status = "cancelled"
        _go(user, None)
        db.commit()
        wa.send_whatsapp(number, "Cancellation confirmed.\n\nYour access will remain "
                         "active until the end of your current access period. You can "
                         "export your records before access ends.")
    else:
        wa.send_whatsapp(number, "No change made — your access is kept.")
        _back_to_menu(db, user, number)


def _data_download(db, user, number) -> None:
    token = make_export_link(db, user.id, fmt="xlsx")
    if config.PUBLIC_BASE_URL:
        wa.send_whatsapp(number, "Your Excel record pack (link valid 24h):\n"
                         f"{config.PUBLIC_BASE_URL}/export/{token}")
    else:
        wa.send_whatsapp(number, "Export link isn't configured yet (set PUBLIC_BASE_URL).")


def _h_tax(db, user, number, low, payload):
    if _is(low, "cancel", "back"):
        _back_to_menu(db, user, number)
        return
    if not low.isdigit() or not (1 <= int(low) <= 3):
        wa.send_whatsapp(number, "Please reply 1, 2 or 3.\n\n" + _TAX_MENU)
        return
    rate = _TAX_RATES[int(low) - 1]
    user.tax_rate = rate
    _go(user, None)
    db.commit()
    wa.send_whatsapp(number, f"Updated ✅ I'll use {_TAX_LABELS[rate]} for rough "
                     "tax-benefit estimates.")


def _reminder_menu(user) -> str:
    if user.reminder_status == "off":
        current = "off"
    else:
        current = f"{_DAY_NAMES.get(user.reminder_day, 'Sunday')} {user.reminder_time_label}"
    log_opt = ("Switch to monthly logging"
               if (getattr(user, "log_frequency", "weekly") or "weekly") == "weekly"
               else "Switch to weekly logging")
    opts = [lbl for lbl, _, _ in _REMINDER_OPTIONS] + \
        ["Turn reminders off", log_opt, "Back"]
    return (
        f"Current reminder: {current}\n\n"
        "When should I remind you to send your delivery miles?\n\n"
        + _numbered(opts)
    )


def _h_reminder(db, user, number, low, payload):
    # 1-3 = day options, 4 = off, 5 = toggle logging frequency, 6 = back.
    if low.isdigit() and 1 <= int(low) <= len(_REMINDER_OPTIONS):
        label, day, tlabel = _REMINDER_OPTIONS[int(low) - 1]
        user.reminder_day, user.reminder_time_label = day, tlabel
        user.reminder_status = "active"
        _go(user, None)
        db.commit()
        wa.send_whatsapp(number, f"Reminder updated ✅\n\nI'll remind you every "
                         f"{label} to send your delivery miles.")
    elif low in ("4", "turn reminders off", "off", "stop"):
        _go(user, "reminder_off")
        db.commit()
        wa.send_whatsapp(number, "Turn off reminders?\n\nYou can still send mileage, "
                         "earnings and expenses anytime.\n\n"
                         + _numbered(["Confirm", "Cancel"]))
    elif low == "5":
        new = "monthly" if (user.log_frequency or "weekly") == "weekly" else "weekly"
        user.log_frequency = new
        _go(user, None)
        db.commit()
        wa.send_whatsapp(number, f"Updated ✅ I'll treat new entries as {new} by "
                         "default. You can still say \"this week\"/\"this month\" any time.")
    elif low in ("6", "back", "cancel"):
        _back_to_menu(db, user, number)
    else:
        wa.send_whatsapp(number, "Please reply with an option number.\n\n"
                         + _reminder_menu(user))


def _h_reminder_off(db, user, number, low, payload):
    if low in ("1", "confirm", "yes"):
        user.reminder_status = "off"
        _go(user, None)
        db.commit()
        wa.send_whatsapp(number, "Reminders turned off ✅\n\nYou can turn them back "
                         "on anytime in settings.")
    else:
        wa.send_whatsapp(number, "Kept your reminders on.")
        _back_to_menu(db, user, number)


def _h_vehicle(db, user, number, low, payload):
    if low in ("1", "change main vehicle", "change main"):
        _go(user, "main")
        db.commit()
        wa.send_whatsapp(number, "Which vehicle should be your main delivery vehicle?\n\n"
                         + _numbered([_vlabel(v) for v in _ORDER] + ["Cancel"]))
    elif low in ("2", "add another vehicle", "add"):
        _go(user, "add")
        db.commit()
        wa.send_whatsapp(number, _add_vehicle_prompt())
    elif low in ("3", "set default vehicle", "set default"):
        msg, _ = _saved_vehicle_prompt(
            user, "Which vehicle should I use by default when you send mileage "
            "without saying the vehicle?")
        _go(user, "default")
        db.commit()
        wa.send_whatsapp(number, msg)
    elif low in ("4", "remove vehicle", "remove"):
        msg, _ = _saved_vehicle_prompt(user, "Which vehicle do you want to remove?")
        _go(user, "remove")
        db.commit()
        wa.send_whatsapp(number, msg)
    elif low in ("5", "back"):
        _back_to_menu(db, user, number)
    else:
        wa.send_whatsapp(number, "Please reply with an option number.\n\n"
                         + _vehicle_settings_overview(user))


def _h_add(db, user, number, low, payload):
    keys = list(_ORDER)
    if _is(low, "cancel") or low == str(len(keys) + 1):
        _back_to_vehicle(db, user, number)
        return
    vt = _pick(low, keys)
    if vt is None:
        wa.send_whatsapp(number, "Please pick a vehicle.\n\n" + _add_vehicle_prompt())
        return
    if vt in registered(user):
        _go(user, None)
        db.commit()
        wa.send_whatsapp(
            number,
            f"You already have {tax.label(vt)} added.\n\n"
            "What would you like to do?\n\n"
            + _numbered(["Set as default", "Back to vehicle settings"]))
        _go(user, f"added:{vt}")
        db.commit()
        return
    _add_extra(user, vt)
    _go(user, f"added:{vt}")
    db.commit()
    wa.send_whatsapp(
        number,
        f"{tax.label(vt)} added ✅\n\n"
        f"Your main vehicle is still {tax.label(user.vehicle_type)}.\n\n"
        "When you log miles, I can ask whether the miles were for "
        f"{tax.label(user.vehicle_type)}, {tax.label(vt)}, or split between them.\n\n"
        + _numbered(["Set as default",
                     f"Keep {tax.label(user.vehicle_type)} as default",
                     "Back to vehicle settings"]))


def _h_added(db, user, number, low, payload):
    vt = payload
    if low in ("1", "set as default", "set default"):
        user.default_vehicle = vt
        db.commit()
        wa.send_whatsapp(number, f"Default vehicle updated ✅ I'll use {tax.label(vt)} "
                         "when you send mileage without a vehicle.")
        _back_to_vehicle(db, user, number)
    elif low in ("2", "keep current default", "keep") or low.startswith("keep"):
        wa.send_whatsapp(number, "Kept your current default.")
        _back_to_vehicle(db, user, number)
    elif low in ("3", "back to vehicle settings", "back"):
        _back_to_vehicle(db, user, number)
    else:
        wa.send_whatsapp(number, "Please reply with an option number.")


def _h_main(db, user, number, low, payload):
    keys = list(_ORDER)
    if _is(low, "cancel") or low == str(len(keys) + 1):
        _back_to_vehicle(db, user, number)
        return
    vt = _pick(low, keys)
    if vt is None:
        wa.send_whatsapp(number, "Please pick a vehicle.")
        return
    _go(user, f"main_confirm:{vt}")
    db.commit()
    wa.send_whatsapp(
        number,
        f"Change your main delivery vehicle to {tax.label(vt)}?\n\n"
        "This will affect future mileage entries only. It will not change mileage "
        "records you already confirmed.\n\n"
        + _numbered(["Confirm", "Cancel"]))


def _h_main_confirm(db, user, number, low, payload):
    vt = payload
    if low in ("1", "confirm", "yes"):
        # Keep the old main as an extra so the user doesn't lose a vehicle.
        have = set(registered(user))
        user.vehicle_type = vt
        user.default_vehicle = vt
        _set_extras(user, have)  # excludes the new main, retains the old one
        _go(user, None)
        db.commit()
        wa.send_whatsapp(
            number,
            f"Updated ✅\n\nYour main delivery vehicle is now {tax.label(vt)}.\n\n"
            f"Future mileage will use {tax.label(vt)} by default unless you choose "
            "another vehicle.")
    else:
        wa.send_whatsapp(number, "No change made.")
        _back_to_vehicle(db, user, number)


def _h_default(db, user, number, low, payload):
    keys = registered(user)
    if _is(low, "cancel") or low == str(len(keys) + 1):
        _back_to_vehicle(db, user, number)
        return
    vt = _pick(low, keys)
    if vt is None:
        wa.send_whatsapp(number, "Please pick one of your saved vehicles.")
        return
    _go(user, f"default_confirm:{vt}")
    db.commit()
    wa.send_whatsapp(
        number,
        f"Set {tax.label(vt)} as your default vehicle?\n\n"
        f"When you send \"120 miles\", I'll assume {tax.label(vt)} unless you say "
        "otherwise.\n\n" + _numbered(["Confirm", "Cancel"]))


def _h_default_confirm(db, user, number, low, payload):
    vt = payload
    if low in ("1", "confirm", "yes"):
        user.default_vehicle = vt
        _go(user, None)
        db.commit()
        wa.send_whatsapp(number, f"Default vehicle updated ✅\n\nWhen you send mileage "
                         f"without a vehicle, I'll use {tax.label(vt)}.")
    else:
        wa.send_whatsapp(number, "No change made.")
        _back_to_vehicle(db, user, number)


def _h_remove(db, user, number, low, payload):
    keys = registered(user)
    if _is(low, "cancel") or low == str(len(keys) + 1):
        _back_to_vehicle(db, user, number)
        return
    vt = _pick(low, keys)
    if vt is None:
        wa.send_whatsapp(number, "Please pick one of your saved vehicles.")
        return
    is_main_or_default = vt in (tax.normalise_vehicle(user.vehicle_type), default_vehicle(user))
    if is_main_or_default:
        others = [v for v in registered(user) if v != vt]
        if not others:
            wa.send_whatsapp(number, "That's your only vehicle, so it can't be removed. "
                             "Add another vehicle first.")
            _back_to_vehicle(db, user, number)
            return
        _go(user, f"remove_reassign:{vt}")
        db.commit()
        wa.send_whatsapp(
            number,
            f"{tax.label(vt)} is currently your main/default vehicle.\n\n"
            "Before removing it, please choose a new default vehicle.\n\n"
            + _numbered([_vlabel(v) for v in others] + ["Cancel"]))
        return
    _go(user, f"remove_confirm:{vt}")
    db.commit()
    wa.send_whatsapp(
        number,
        f"Remove {tax.label(vt)} from your vehicle options?\n\n"
        "This will not delete mileage records you already confirmed.\n\n"
        + _numbered(["Confirm remove", "Cancel"]))


def _h_remove_confirm(db, user, number, low, payload):
    vt = payload
    if low in ("1", "confirm remove", "confirm", "yes"):
        _remove_vehicle(user, vt)
        _go(user, None)
        db.commit()
        wa.send_whatsapp(number, f"{tax.label(vt)} removed ✅\n\nExisting confirmed "
                         "mileage records remain unchanged.")
    else:
        wa.send_whatsapp(number, "No change made.")
        _back_to_vehicle(db, user, number)


def _h_remove_reassign(db, user, number, low, payload):
    removed = payload
    others = [v for v in registered(user) if v != removed]
    if _is(low, "cancel") or low == str(len(others) + 1):
        _back_to_vehicle(db, user, number)
        return
    new = _pick(low, others)
    if new is None:
        wa.send_whatsapp(number, "Please pick a new default vehicle.")
        return
    _go(user, f"remove_reassign_confirm:{new}:{removed}")
    db.commit()
    wa.send_whatsapp(
        number,
        f"Set {tax.label(new)} as your new default vehicle and remove "
        f"{tax.label(removed)}?\n\n" + _numbered(["Confirm", "Cancel"]))


def _h_remove_reassign_confirm(db, user, number, low, payload):
    new, _, removed = payload.partition(":")
    if low in ("1", "confirm", "yes"):
        # New default/main, then remove the old vehicle. Records stay untouched.
        if tax.normalise_vehicle(user.vehicle_type) == removed:
            user.vehicle_type = new
        user.default_vehicle = new
        _remove_vehicle(user, removed)
        _go(user, None)
        db.commit()
        wa.send_whatsapp(
            number,
            f"Updated ✅\n\n{tax.label(new)} is now your default vehicle.\n\n"
            f"{tax.label(removed)} has been removed from your vehicle options.\n\n"
            "Existing confirmed mileage records remain unchanged.")
    else:
        wa.send_whatsapp(number, "No change made.")
        _back_to_vehicle(db, user, number)


# --- navigation helpers ------------------------------------------------------

def _back_to_menu(db, user, number):
    _go(user, "menu")
    db.commit()
    wa.send_whatsapp(number, _MAIN_MENU)


def _back_to_vehicle(db, user, number):
    _go(user, "vehicle")
    db.commit()
    wa.send_whatsapp(number, _vehicle_settings_overview(user))


_STATES = {
    "menu": _h_menu,
    "tax": _h_tax,
    "reminder": _h_reminder,
    "reminder_off": _h_reminder_off,
    "data": _h_data,
    "delete_confirm": _h_delete_confirm,
    "account": _h_account,
    "payment": _h_payment,
    "cancel_confirm": _h_cancel_confirm,
    "vehicle": _h_vehicle,
    "add": _h_add,
    "added": _h_added,
    "main": _h_main,
    "main_confirm": _h_main_confirm,
    "default": _h_default,
    "default_confirm": _h_default_confirm,
    "remove": _h_remove,
    "remove_confirm": _h_remove_confirm,
    "remove_reassign": _h_remove_reassign,
    "remove_reassign_confirm": _h_remove_reassign_confirm,
}


# --- natural-language intents (sections 8–11) --------------------------------

def _nl_intent(db, user, number, low) -> bool:
    """Detect vehicle-settings intents in free text. Never changes data directly —
    routes to the relevant buttoned step. Returns True if matched."""
    # Reminder intents (Flow H §13): "stop reminders", "remind me monday".
    if ("reminder" in low or "remind" in low) and \
            ("stop" in low or "off" in low or "turn off" in low or "disable" in low):
        _go(user, "reminder_off")
        db.commit()
        wa.send_whatsapp(number, "Turn off reminders?\n\nYou can still send mileage, "
                         "earnings and expenses anytime.\n\n"
                         + _numbered(["Confirm", "Cancel"]))
        return True
    if "remind" in low:
        for label, day, _ in _REMINDER_OPTIONS:
            if day in low or _DAY_NAMES[day].lower() in low:
                _go(user, "reminder")
                db.commit()
                wa.send_whatsapp(number, _reminder_menu(user))
                return True

    # Payment/subscription intents (Flow I): "subscription", "cancel my plan",
    # "how much", "pricing", "upgrade".
    if any(w in low for w in ("subscription", "payment", "pricing", "upgrade",
                              "how much", "my plan", "billing", "cancel my plan",
                              "free trial", "paid access")):
        _go(user, "payment")
        db.commit()
        wa.send_whatsapp(number, _payment_message(user))
        return True

    # "download my records" → Excel pack link.
    if "download" in low and ("record" in low or "data" in low or "pack" in low):
        _data_download(db, user, number)
        return True

    # Data intents (Flow H §13): "delete my data", "download my records".
    if "delete" in low and ("data" in low or "record" in low or "account" in low):
        _go(user, "delete_confirm")
        db.commit()
        wa.send_whatsapp(
            number,
            "Delete your stored records?\n\nThis will remove your confirmed records "
            "and settings from the service.\n\nThis cannot be undone.\n\n"
            + _numbered(["Confirm delete", "Cancel"]))
        return True

    # "I use two cars" — same type, no new setting needed.
    if ("two car" in low or "2 car" in low or "two van" in low
            or "second car" in low):
        wa.send_whatsapp(
            number,
            "For now, I track mileage by vehicle type.\n\n"
            "Both cars use the same Car / van mileage rate, so you can record them "
            "together as Car / van miles.\n\n"
            "If you need to keep a note for your accountant, you can add it when "
            "logging mileage.")
        return True

    # "Can I add scooter?" / "add a moped" — route to add motorbike.
    if "scooter" in low or ("add" in low and ("moped" in low or "motorbike" in low)):
        _go(user, "add")
        db.commit()
        wa.send_whatsapp(
            number,
            "Yes. For mileage, scooter/moped is treated as Motorbike / moped.\n\n"
            "Which vehicle do you want to add?\n\n"
            + _numbered([_vlabel(v) for v in _ORDER] + ["Cancel"]))
        return True

    # "I used bike today" inside settings — add it, or use once for next entry.
    if low.startswith("i used ") or (low.startswith("used ") and "today" in low):
        vt = next((_WORDS.get(w) for w in low.replace("-", "").split()
                   if _WORDS.get(w)), None)
        if vt:
            _go(user, "add")
            db.commit()
            wa.send_whatsapp(
                number,
                f"Do you want to add {tax.label(vt)} as a vehicle option, or use it "
                "only for your next mileage entry?\n\n"
                f"To add it now, pick it below. To use it once, just include it when "
                f"you log miles (e.g. \"40 miles {('bike' if vt == 'bicycle' else 'car' if vt == 'car_van' else 'motorbike')}\").\n\n"
                + _numbered([_vlabel(v) for v in _ORDER] + ["Cancel"]))
            return True

    # "I use both car and bike" — multi-vehicle explainer.
    veh_words = [w for w in ("car", "van", "bike", "bicycle", "cycle", "motorbike",
                             "moped", "scooter") if w in low]
    if ("both" in low or "and" in low) and len(set(_WORDS.get(w) for w in veh_words)) >= 2:
        _go(user, "vehicle")
        db.commit()
        wa.send_whatsapp(
            number,
            "No problem.\n\n"
            "Your main vehicle is used when you simply send mileage like \"120 miles\".\n\n"
            "You can add another vehicle now and split mileage later when needed.\n\n"
            + _numbered(["Change main vehicle", "Add another vehicle",
                         "Set default vehicle", "Remove vehicle", "Back"]))
        return True

    return False

# Changelog

All notable changes to the **Courier Tax & Records Assistant** (mototax) are
documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the product is pre-launch, releases stay in the `0.x` range.

**Product:** a WhatsApp bot that helps self-employed UK multi-platform food-delivery
couriers (Uber Eats / Deliveroo / Just Eat) keep tax-ready records.
**Stack:** FastAPI · Twilio (WhatsApp) · Postgres · Claude Haiku 4.5 (vision OCR) ·
deployed on Railway.

---

## [Unreleased]

### Known gaps / planned
- **Mileage rate not verified.** Car/van uses **55p/mile** (first 10,000 miles), but
  GOV.UK currently shows **45p**; the 55p figure comes from unconfirmed third-party
  sources. If 45p is correct, every deduction shown is overstated by ~22%. A parked
  `rate-config-refactor` branch centralises rates and adds a `RATE_CONFIRMED` gate but
  has not been merged. **Do not launch on the unverified value.**
- **Payments not live.** Plan/access status exists (Flow I) but Stripe is referenced as a
  future path only; no payment is actually taken yet, and pricing is intentionally not
  hard-coded.
- **Period at log time** still defaults to the current week/month. A period picker now
  exists for summaries, exports, and the mileage edit menu, but not when first logging.
- **"Real take-home" formula** — the summary now leads with an estimated take-home (Flow F);
  the underlying formula should be reviewed against worked examples before launch.
- **Twilio trial cap.** Onboarding sends many messages (Terms/Privacy gate + setup); the
  Twilio trial account's 50-messages/day limit blocks replies once exceeded. Resolved by
  upgrading the Twilio account out of trial.

---

## [0.6.0] — 2026-06-09

Richer mileage edit menu, and a fix so editing no longer loses the period. (PR #17)

### Added
- **Edit a mileage record's vehicle and period**, not just the mileage. Replying `2`
  (edit) on a mileage record now opens a sub-menu:
  - **Mileage** — send a corrected value (or just send it directly, as before).
  - **Vehicle** — switch between car/van, motorbike, or bicycle; the deduction
    recalculates at the new rate.
  - **Period** — pick this/last week, this/last month, or send a custom range
    (e.g. "1 Jun to 30 Jun"); the input frequency (weekly/monthly) updates to match.

### Fixed
- **Editing a mileage entry no longer resets its period.** The re-prompt after an edit
  now shows the record's stored period and input type instead of recomputing the current
  week — previously a monthly "1–30 Jun" entry would display as the current week after any
  edit.

---

## [0.5.0] — 2026-06-09

Flows F–K: summaries, Excel export pack, shared period picker, settings hub, access
status, graceful help, and the legal/data-rights flow. (PRs #9–#16)

### Added
- **Weekly/monthly summary (Flow F).** Period-aware `SUMMARY` leading with estimated
  real take-home, with mileage-only / earnings-only / empty variants. Splits mileage by
  vehicle, ranks platforms, lists review-only expenses separately, shows a logging streak,
  and warns when mileage and earnings use different frequencies.
- **Excel record pack as the standard export (Flow G).** `EXPORT` produces an `.xlsx`
  workbook with six tabs (assumptions, income, mileage, non-vehicle expenses, review-only,
  summary) via openpyxl, served through the `/export/{token}` link. CSV is repositioned as
  a paid/pro option. `ExportLink` gains `fmt` + period.
- **Shared period picker (`periods.py`).** `SUMMARY`/`EXPORT` accept a trailing period —
  "this/last week", "this/last month", "this/last tax year" (UK 6 Apr–5 Apr), or a custom
  range like "1 June to 30 June".
- **Settings hub (Flow H).** Vehicle / Tax / Reminder / Account status / Export-or-delete
  my data / Help. Per-user reminder day/time and on/off (scheduler runs daily and only
  notifies users due that day); weekly/monthly logging toggle; delete-my-data with confirm.
- **Access / subscription status (Flow I).** `plan_status` (beta/trial/active/paused/
  cancelled/partner) with adaptive copy and a cancellation flow. Pricing is never
  hard-coded; Stripe referenced as the future payment path (not yet live).
- **Graceful help & fallbacks (Flow J).** Friendlier HELP and unknown-message handling;
  canned replies for "is this tax advice?", unsupported tax questions, human/support
  requests, and vehicle-cost questions. **Multi-item messages** ("120 miles, Uber £300,
  bag £45") create a pending record per item for confirmation.
- **Terms, privacy & data rights (Flow K, `legal.py`).** In-app Terms/Privacy summaries and
  full texts (versioned), a data-rights submenu (export/correct/delete), and an in-service
  support route. Tracks `privacy_version` + `privacy_shown_at`.

### Fixed
- **Schema readiness on startup.** `init_db()` only ran in the deprecated
  `@app.on_event("startup")`, which doesn't fire reliably on newer FastAPI — so columns
  were sometimes missing and onboarding failed after the tax question. Moved to a lifespan
  handler + a lazy idempotent `_ensure_started()` at the top of `handle_inbound`; handler
  errors now roll back and reply gracefully with a full traceback in logs.

### Note
These flows shipped through the `onboarding-rewrite-and-cli` branch (PRs #9–#16) without
changelog entries; this 0.5.0 section backfills them from the merged code.

---

## [0.4.0] — 2026-06-09

Full product flows A–E. Brings `main` up to date with the complete onboarding/records
feature set. (PR #7)

### Added
- **Terms & Privacy gate (Flow A).** New users must review/accept Terms and a Privacy
  Notice before onboarding. Acceptance is recorded against a `TERMS_VERSION`.
- **Richer mileage parsing (Flow B).** Handles single, split, monthly, personal, and
  vehicle-tagged mileage messages. Each record now shows and stores a **period**
  (start/end + frequency).
- **Vehicle settings menu (Flow C).** Set main/default vehicle, add/remove vehicles, and
  reassign records, via menu and natural-language intents.
- **Earnings (Flow D).** Manual entry and screenshot OCR, a **platform picker**
  (Uber Eats / Deliveroo / Just Eat + "Other" custom), duplicate-entry warnings, and a
  period on each record.
- **Expenses (Flow E1/E2).** Typed expenses and receipt-photo OCR.
- **Review-only expenses (Flow E3).** Vehicle running costs (petrol, insurance, repairs,
  servicing, MOT, road tax, tyres), personal items, and unclear descriptions are flagged
  **review-only** and excluded from expense totals, with eligibility questions answered
  inline (no approval step).
- **Logging-frequency preference** — weekly by default, changeable in settings.
- New `settings.py` module housing the settings/preferences flows.

### Changed
- Onboarding copy reworked; "How It Works" explainer added.
- Earnings amounts now recognise written currency words and a bare number when earnings
  were just requested.
- Earnings summary no longer nudges for mileage if mileage was already logged.

### Fixed
- Platform-picker numbering and the "Other" custom-entry path.

### Security / Privacy
- **Images are never persisted.** Earnings screenshots and receipts are read for
  extraction, then discarded in every flow. (Evidence storage deferred to a future
  paid/pro tier.)

---

## [0.3.1] — 2026-06-03

Local testing tooling and harness refinements. (PRs #3–#6)

### Added
- **`exit` / `quit` / `end` keyword** — bail out of any in-progress step (pending record,
  mid-edit, or "which vehicle?" prompt) back to idle without deleting confirmed history.
  Also closes a dead-end: the awaiting-vehicle prompt previously had no cancel. (#4)
- **Auto-welcome on conversation start** in the `chat.py` harness — a brand-new number
  gets the welcome + onboarding immediately; existing sessions resume quietly. (#6)

### Changed
- In the `chat.py` harness, bare `quit` / `exit` now leave the harness (back to terminal);
  type `end` to test the bot's in-conversation exit keyword. (#5)

### Fixed
- `chat.py` user lookup used `User.number` instead of `User.whatsapp_number`, which raised
  `AttributeError` and had broken `/reset`. (#6)

### Removed
- Duplicate terminal harness `cli.py` — consolidated onto `chat.py`, which uses an
  isolated local DB and has safer defaults. (#3)

---

## [0.3.0] — 2026-06-03

Onboarding rewrite and a local testing harness. (PRs #1/#2)

### Added
- **Terminal chat harness** to drive the bot's real `handle_inbound` code path locally,
  with no Twilio required (replies print to the terminal).
- Hidden **`restart`** testing keyword — wipes a user and re-runs onboarding.
- Onboarding confirmation echoes (vehicle and tax-rate choices read back to the user) and
  extra guidance messages.

### Changed
- Onboarding flow and welcome/copy rewritten for a warmer, clearer tone.

---

## [0.2.0] — 2026-06-02

First feature set against the partner-discussion note.

### Added
- **Onboarding** — two mandatory questions before use: vehicle type (sets the mileage
  rate) and tax-estimate level (20% / 40% / 0%).
- **Vehicle-aware simplified mileage** — car/van 55p first 10k then 25p; motorbike 24p;
  bicycle 20p. Shows the mileage deduction and an estimated tax benefit. (partner note A+B)
- **Confirm / Edit / Delete flow** — reply 1 to confirm, 2 to edit (send a corrected
  value), 3 to delete. Nothing is finalised without confirmation. (partner note C)
- **Weekly Sunday reminder** — in-process APScheduler nudge to log miles, with a manual
  trigger endpoint guarded by `CRON_SECRET`. (partner note F)
- **Multi-vehicle support** — active vehicle plus per-vehicle rates; each mileage record
  is stamped with its vehicle type so history keeps its rate. `VEHICLES` overview and
  `use car`/`use motorbike`/`use bike` switching.
- **Multi-vehicle safeguards** — once ≥2 vehicle types are logged, each new mileage entry
  asks "which vehicle?"; the weekly reminder names the active vehicle.

---

## [0.1.0] — 2026-06-02

Initial MVP backend and first Railway deployment.

### Added
- FastAPI app with the Twilio WhatsApp webhook, message router, Claude Haiku 4.5 vision
  extraction, SQLAlchemy models, and CSV export with one-off download links.
- Core principle: every figure is courier-confirmed; CSV records `source_type` and
  `confirmation_status` so an accountant can tell a confirmed receipt from an estimate.

### Fixed
- Pinned Python 3.12 for the Railway build and corrected `.python-version` format.
- Added `python-multipart` so FastAPI can parse the Twilio webhook form (webhook had
  500'd without it).

---

[Unreleased]: https://github.com/mototaxuk-lab/mototax/compare/0e0bca8...HEAD
[0.6.0]: https://github.com/mototaxuk-lab/mototax/pull/17
[0.5.0]: https://github.com/mototaxuk-lab/mototax/compare/a17c49b...223ea5b
[0.4.0]: https://github.com/mototaxuk-lab/mototax/pull/7
[0.3.1]: https://github.com/mototaxuk-lab/mototax/compare/a14f6dc...9803458
[0.3.0]: https://github.com/mototaxuk-lab/mototax/pull/1
[0.2.0]: https://github.com/mototaxuk-lab/mototax/compare/308b2c3...4aea47d
[0.1.0]: https://github.com/mototaxuk-lab/mototax/commits/b0e1597

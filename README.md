# Courier Tax & Records Assistant — MVP backend

A WhatsApp bot that turns couriers' earnings screenshots, receipt photos and
mileage messages into a confirmed, tax-ready ledger and an accountant-ready CSV.

**Stack:** FastAPI · Twilio (WhatsApp) · Claude Haiku 4.5 (vision extraction) ·
Postgres · deploys on Railway from GitHub.

<!-- VERSION:START — auto-generated from CHANGELOG.md by .githooks/pre-push; do not edit by hand -->
![Version](https://img.shields.io/badge/version-0.4.0-blue) ![Changelog](https://img.shields.io/badge/changelog-CHANGELOG.md-informational)

### Version history

| Version | Date | Summary |
|---------|------|---------|
| **0.4.0** | 2026-06-09 | Full product flows A–E. Brings `main` up to date with the complete onboarding/records feature set. (PR #7) |
| **0.3.1** | 2026-06-03 | Local testing tooling and harness refinements. (PRs #3–#6) |
| **0.3.0** | 2026-06-03 | Onboarding rewrite and a local testing harness. (PRs #1/#2) |
| **0.2.0** | 2026-06-02 | First feature set against the partner-discussion note. |
| **0.1.0** | 2026-06-02 | Initial MVP backend and first Railway deployment. |

Full details in [CHANGELOG.md](CHANGELOG.md).
<!-- VERSION:END -->

## How it works

```
WhatsApp message ─▶ POST /webhook/whatsapp ─▶ identify user by phone
   │                                            │
   │                          image? ──▶ download ──▶ Claude Haiku extracts ──▶ "pending" record
   │                          text "145 miles"? ──▶ parsed locally ──▶ "pending" record
   │                                            │
   ◀── "Detected Shell, £80, Fuel. Reply 1 to confirm, 2 to discard." ──┘
reply "1" ─▶ record marked confirmed   ·   "csv" ─▶ 24h download link   ·   "summary" ─▶ totals
```

Nothing is finalised automatically — every figure is confirmed by the courier
first, and the CSV records whether each row was a confirmed receipt or a
self-reported estimate.

## Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app, webhook, message router, CSV download endpoint |
| `extract.py` | Claude vision extraction + local mileage text parsing |
| `models.py` | Postgres/SQLite models and helpers |
| `twilio_client.py` | Send messages, verify webhook signatures, download media |
| `export.py` | CSV builder + weekly summary (with the 2026/27 55p mileage rate) |
| `config.py` | Reads all secrets from environment variables |
| `CHANGELOG.md` | Versioned history of all product changes (source of truth) |
| `chat.py` | Local terminal harness to drive the bot without Twilio |
| `scripts/sync_readme_version.py` | Regenerates the README version block from `CHANGELOG.md` |

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in your keys
uvicorn main:app --reload
```
With no `DATABASE_URL` it uses a local SQLite file, so you can boot with zero
setup. To exercise the WhatsApp flow locally, expose your port with a tunnel
(e.g. ngrok) and point Twilio at that URL.

## Deploy on Railway

1. Push this folder to your GitHub repo.
2. In Railway: **New Project → Deploy from GitHub repo**, pick the repo.
3. Add a **Postgres** database to the project (Railway sets `DATABASE_URL` for you).
4. Under your service's **Variables**, add the keys from `.env.example`
   (`ANTHROPIC_API_KEY`, the three `TWILIO_*` values, `WEBHOOK_URL`,
   `PUBLIC_BASE_URL`). Railway provides the start command via the `Procfile`.
5. Railway gives you a public URL like `https://your-app.up.railway.app`.
   Set `PUBLIC_BASE_URL` to that, and `WEBHOOK_URL` to that + `/webhook/whatsapp`.

## Connect Twilio WhatsApp (sandbox)

1. Twilio Console → **Messaging → Try it out → Send a WhatsApp message**.
2. Join the sandbox from your phone (send the join code to the sandbox number).
3. In the sandbox settings, set **"When a message comes in"** to
   `https://your-app.up.railway.app/webhook/whatsapp` (POST).
4. Set `TWILIO_WHATSAPP_FROM` to the sandbox number (`whatsapp:+14155238886`).
5. Message your bot from the joined phone — you'll get the welcome message.

Once you've validated behaviour, apply for your own WhatsApp sender to leave the
sandbox (and consider migrating to the Meta Cloud API to cut per-message cost).

## Notes / deliberate MVP shortcuts

- **Evidence storage:** we keep Twilio's media URL as the reference. Twilio only
  retains media for a limited time, so before this is more than a prototype,
  copy each image to your own object storage (e.g. S3/R2) and store that URL.
- **Edit flow** is intentionally simple (confirm or discard). A richer "reply 2
  then send the correct amount" flow is an easy next step.
- **Scale:** processing runs in a FastAPI background task. At higher volume,
  move extraction to a real queue (e.g. Redis/RQ) so webhooks stay instant.
- **Cost levers** to add next: prompt caching on the extraction system prompt,
  and routing only low-confidence images to a stronger model.

## Versioning

[`CHANGELOG.md`](CHANGELOG.md) is the single source of truth for versions
(Semantic Versioning, Keep a Changelog format). The version badge and history
table near the top of this README are **generated from it** — don't edit that
block by hand.

**One-time setup per clone** (enables the git hook):

```bash
./scripts/install-hooks.sh
```

After that, a `pre-push` hook keeps the README in sync automatically: when you
bump a release in `CHANGELOG.md`, the hook regenerates the README block on push.
If it was out of date, the push is blocked once so you can commit the refresh:

```bash
git add README.md && git commit -m "docs: sync README version" && git push
```

To release a new version: add a `## [x.y.z] — YYYY-MM-DD` section (with a one-
line summary under it) to `CHANGELOG.md`, then commit and push. You can also run
the sync manually any time: `python scripts/sync_readme_version.py`.

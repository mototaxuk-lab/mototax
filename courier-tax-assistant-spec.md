# Courier Tax & Records Assistant — Build Spec

**Status:** MVP in progress · **Tax year:** 2026/27 · **Scope:** Simplified-mileage only

This spec consolidates the change list tracked against the partner note
(`courier_record_assistant_partner_note.docx`) and the rate config spec
(`mileage_rate_config_spec.md`). It is the working reference for closing the gap
between the current MVP and launch.

---

## 1. Product summary

A WhatsApp bot that turns couriers' earnings screenshots, receipt photos and
mileage messages into a confirmed, tax-ready ledger and an accountant-ready
export. Nothing is finalised automatically — every figure is confirmed by the
courier first, and each row records whether it came from a confirmed receipt or
a self-reported estimate.

**Stack:** FastAPI · Twilio (WhatsApp) · Claude Haiku 4.5 (vision extraction) ·
Postgres · deploys on Railway from GitHub.

**Framing principle:** the bot *organises records*; the accountant *decides tax*.
All deduction and tax-benefit figures are indicative only and explicitly labelled
"not tax advice."

---

## 2. Launch blocker — rate not confirmed

The car/van rate of **55p/mile** (first 10k) is **UNCONFIRMED**. GOV.UK currently
shows **45p**; the 55p figure comes from third-party May-2026 articles GOV.UK does
not yet reflect.

- **Impact if wrong:** if the true rate is 45p, every deduction shown is overstated
  by roughly 22%.
- **Resolution:** verify against HMRC primary source / a qualified accountant before
  launch. The rate refactor (item J) makes correcting the value a one-line flip, but
  it does not make 55p correct.
- **Gate:** do **not** launch on the unverified value. `RATE_CONFIRMED` stays `False`
  until verified, and all rate copy stays softly hedged until then.

---

## 3. Locked scope decisions

- **Simplified-mileage only.** No traditional/actual-cost engine is built.
- If a method choice is ever added, it is a **settings toggle defaulting to
  simplified** (HMRC rules make methods sticky per-vehicle). This is why item A is
  "flag & don't count" rather than a second methodology.
- **Explicitly excluded:** manual/custom rate override (Spec §7) — not in MVP; it
  invites the "invented rate" failure mode.

---

## 4. Change list

Each item below names the area, the current state, the target state, and its source
document reference.

### A — Vehicle running-cost receipts (correctness)
- **Now:** fuel/insurance/repair receipts are confirmed and summed into expenses,
  which double-counts against the mileage deduction.
- **Target:** detect vehicle running costs; reply with the §4 script; flag as
  **not counted**, never deducted.
- **Source:** Note §3/§4/§12.

### B — Record statuses
- **Now:** `pending` / `confirmed` / `rejected` / `estimated` / `editing` /
  `awaiting_vehicle`.
- **Target:** add a **review-required / not-counted** status plus a "mark for review"
  option, feeding a review bucket.
- **Source:** Note §8/§11/§12.

### C — Typed expense entry
- **Now:** "Delivery bag £45" falls through to HELP — no parser.
- **Target:** parse typed expenses → "Logged for accountant review: …. Confirm?"
- **Source:** Note §11.

### D — Earnings screenshot flow
- **Now:** generic Confirm/Edit/Delete; tips and period ignored.
- **Target:** add "Wrong platform" + platform picker; extract tips and period
  (week ending).
- **Source:** Note §10.

### E — Weekly summary content & order
- **Now:** income, expenses, miles, deduction, tax benefit (fixed order).
- **Target:** lead with **real take-home**, then earnings, miles, deduction, tax
  benefit, expenses, **streak**; add streak tracking and a no-guilt nudge.
- **Blocked on:** the real take-home formula (see open decisions).
- **Source:** Note §15–18/§22.

### F — Export levels & structure
- **Now:** single flat CSV.
- **Target:** **weekly / monthly / annual** tiers plus a structured multi-file pack
  (`00_assumptions` … `05_summary`), likely zipped.
- **Source:** Note §19/§20.

### G — Actual-cost question handler
- **Now:** none.
- **Target:** canned §3 reply ("Actual vehicle-cost calculations are not currently
  available …").
- **Source:** Note §3.

### H — Settings / reminder copy
- **Now:** SETTINGS edits vehicle + tax only.
- **Target:** let SETTINGS adjust reminder settings; update setup copy.
- **Source:** Note §6.

### I — Accounting-app exports *(deferred — paid)*
- **Now:** none.
- **Target:** QuickBooks / Xero / FreeAgent paid exports.
- **Source:** Note §21.

### J — Rate single source of truth
- **Now:** pence values hard-coded in `tax.py`; "55p" in README and copy.
- **Target:** one `rates.py` owns the rate table; no pence literal anywhere else;
  add `RATE_CONFIRMED`, `RATE_TAX_YEAR`, source note.
- **Source:** Spec §2. *(Largely implemented — see status table.)*

### K — Two-tier 10k threshold
- **Now:** threshold only bites if a single total >10k is passed; weekly entries
  never cross it, so heavy users are overstated.
- **Target:** YTD-aware — split each entry across tier-1/tier-2 at the 10k boundary
  using current-tax-year confirmed miles.
- **Source:** Spec §3. *(Logic implemented in `rates.py`; see note in status table
  on wiring it to YTD data.)*

### L — `rate` keyword
- **Now:** none (vehicle change via `use car` / `settings`).
- **Target:** `rate` shows current vehicle + rate and switches vehicle type only;
  never accepts a freeform pence value.
- **Source:** Spec §5.

### M — Rate transparency line
- **Now:** deduction/tax messages never state the rate.
- **Target:** every deduction/tax-benefit message names the rate via one
  `rate_line()` constant.
- **Source:** Spec §6. *(`rate_line()` exists in `rates.py`; needs wiring into all
  relevant messages.)*

### N — Unconfirmed-rate hedging
- **Now:** copy asserts 55p flatly.
- **Target:** while `RATE_CONFIRMED=False`, soften wording and keep a visible TODO.
- **Source:** Spec §1/§6.

### O — Vehicle naming & bicycle uncertainty
- **Now:** code key is `motorbike`; spec uses `motorcycle`. Bicycle 20p treated as
  normal.
- **Target:** align the enum (needs a DB migration); flag bicycle for accountant
  treatment.
- **Source:** Spec §2.

### P — `00_assumptions` records rate
- **Now:** no assumptions file.
- **Target:** assumptions file records tax year, vehicle type, tier rates, threshold,
  default-vs-override, and `RATE_CONFIRMED`. Folds into item F.
- **Source:** Spec §8.

---

## 5. Status at a glance

| # | Item | Priority | Status |
|---|------|----------|--------|
| J | Rate single source of truth | Critical | Mostly done (`rates.py` exists) |
| K | Two-tier 10k threshold | Critical | Logic built; verify YTD wiring |
| M | Rate transparency line | Critical | Helper built; wire into messages |
| — | **Rate verification (§2)** | **Blocker** | **Open — gates launch** |
| A | Running-cost double-count | High | Not started |
| B | Review-required status | High | Not started |
| C | Typed expense parser | Medium | Not started |
| L | `rate` keyword | Medium | Not started |
| D | Earnings screenshot flow | Medium | Not started |
| G | Actual-cost handler | Medium | Not started |
| H | Settings / reminder copy | Medium | Not started |
| N | Unconfirmed-rate hedging | Medium | Partial (`rate_line` hedges) |
| O | Vehicle enum rename | Low | Not started (needs migration) |
| E | Weekly summary reorder | Blocked | Needs take-home formula |
| F | Tiered / multi-file export | Low | Not started |
| P | Assumptions file | Low | Folds into F |
| I | Accounting-app exports | Deferred | Paid feature, post-MVP |

---

## 6. Open decisions

1. **55p vs 45p** — requires HMRC / accountant verification before launch (§2).
2. **Real take-home formula** — the note shows £520 earnings → ~£425 take-home, but
   gives no formula. Needed to unblock item E.
3. **Vehicle enum rename** (O) — adopt `motorcycle` and migrate, or keep `motorbike`?

---

## 7. Suggested build order

```
J + K + M   centralised rate + YTD maths + transparency line
   ↓
A + B       stop the double-count; add review status
   ↓
C           typed expense parser
   ↓
L           rate keyword
   ↓
D           earnings screenshot flow
   ↓
G + H       actual-cost handler + settings/reminder copy
   ↓
E           weekly summary reorder (after take-home formula is decided)
   ↓
F (+ P)     tiered / multi-file export with assumptions file
   ↓
I           accounting-app exports (deferred / paid)
```

---

*This is an internal build spec, not a public-facing or legal document. All
deduction figures produced by the assistant are indicative and not tax advice;
final tax treatment is determined by the courier's accountant.*

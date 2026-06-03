# Partner-note & rate-spec change list

Tracking the gap between the current MVP and two source docs:
- `courier_record_assistant_partner_note.docx` (product scope / onboarding / retention)
- `mileage_rate_config_spec.md` (rate configuration & vehicle-type selection)

## ⚠️ Launch blocker — rate not confirmed

The car/van rate of **55p/mile** (first 10k) is **UNCONFIRMED**. GOV.UK currently shows
**45p**; the 55p figure comes from third-party May-2026 articles GOV.UK doesn't yet
reflect. If it's actually 45p, every deduction shown is overstated ~22%. **Verify against
HMRC primary source / accountant before launch.** The rate refactor makes this a one-line
flip but does not make 55p correct.

## Scope decision (locked)

Product stays **simplified-mileage only**. No traditional/actual-cost engine is built. If a
method choice is ever added it's a SETTINGS toggle defaulting to simplified (methods are
sticky per-vehicle under HMRC rules). This is why item A is "flag & don't count" rather than
a second methodology.

## Change list

| # | Area | Current state | Future state | Source |
|---|------|---------------|--------------|--------|
| A | Vehicle running-cost receipts | Fuel/insurance/repair receipts confirmed and summed into expenses → double-counts vs mileage deduction | Detect vehicle running costs; reply with §4 script; flag **not counted**, never deducted | Note §3/§4/§12 |
| B | Record statuses | pending/confirmed/rejected/estimated/editing/awaiting_vehicle | Add **review-required / not-counted** status + "mark for review" option, feeding a review bucket | Note §8/§11/§12 |
| C | Typed expense entry | "Delivery bag £45" falls through to HELP — no parser | Parse typed expenses → "Logged for accountant review: …. Confirm?" | Note §11 |
| D | Earnings screenshot flow | Generic Confirm/Edit/Delete; tips & period ignored | Add "Wrong platform" + platform picker; extract tips and period (week ending) | Note §10 |
| E | Weekly summary content & order | Income, expenses, miles, deduction, tax benefit (fixed order) | Lead with **real take-home**, then earnings, miles, deduction, tax benefit, expenses, **streak**; add streak tracking + no-guilt nudge. **Blocked: take-home formula** | Note §15–18/§22 |
| F | Export levels & structure | Single flat CSV | **Weekly / Monthly / Annual** tiers + structured multi-file pack (00_assumptions … 05_summary), likely zipped | Note §19/§20 |
| G | Actual-cost question handler | None | Canned §3 reply ("Actual vehicle-cost calculations are not currently available …") | Note §3 |
| H | Settings / reminder copy | SETTINGS edits vehicle + tax only | Let SETTINGS adjust reminder settings; update setup copy | Note §6 |
| I | Accounting-app exports | None | QuickBooks/Xero/FreeAgent paid exports — **deferred (paid)** | Note §21 |
| J | Rate single source of truth | Pence values hard-coded in tax.py; "55p" in README & copy | One `rates.py` owns the rate table; no pence literal elsewhere; add RATE_CONFIRMED, RATE_TAX_YEAR, source note | Spec §2 |
| K | Two-tier 10k threshold | Threshold only bites if a single total >10k is passed; weekly entries never cross → heavy users overstated | YTD-aware: split each entry across tier-1/tier-2 at the 10k boundary using current-tax-year confirmed miles | Spec §3 |
| L | `rate` keyword | None (vehicle change via `use car`/`settings`) | `rate` shows current vehicle + rate, switches vehicle type only; never accepts a freeform pence value | Spec §5 |
| M | Rate transparency line | Deduction/tax messages never state the rate | Every deduction/tax-benefit message names the rate via one `rate_line()` constant | Spec §6 |
| N | Unconfirmed-rate hedging | Copy asserts 55p flatly | While RATE_CONFIRMED=False, soften wording + keep visible TODO | Spec §1/§6 |
| O | Vehicle naming & bicycle uncertainty | Code key `motorbike`; spec uses `motorcycle`. Bicycle 20p treated as normal | Align enum (needs DB migration); flag bicycle for accountant treatment | Spec §2 |
| P | `00_assumptions` records rate | No assumptions file | Assumptions file records tax year, vehicle type, tier rates, threshold, default-vs-override, RATE_CONFIRMED — folds into F | Spec §8 |

**Explicitly excluded:** manual/custom rate override (Spec §7) — not in MVP; invites the
"invented rate" failure mode.

## Open decisions needed

1. **55p vs 45p** — HMRC/accountant verification before launch.
2. **Real take-home formula** for E (note shows £520 earnings → ~£425 take-home; no formula given).
3. **Vehicle enum rename** (O) — adopt `motorcycle` and migrate, or keep `motorbike`?

## Suggested build order

J + K + M (centralized rate + YTD maths + transparency line) → A + B → C → L → D → G + H →
E (after formula) → F (+P). I last/deferred.

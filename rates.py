"""SINGLE SOURCE OF TRUTH for simplified-expenses mileage rates.

No pence-per-mile value should live anywhere else in the codebase: all deduction
maths and all user-facing rate copy read from here, so the rate can be corrected
in exactly one place.

TODO(VERIFY): the car/van first-10k rate is UNCONFIRMED.
  GOV.UK currently shows 45p. Third-party (May 2026) sources claim 55p from
  6 Apr 2026, but GOV.UK does not reflect this. Confirm against HMRC primary
  source / an accountant, then set RATE_CONFIRMED = True and correct
  CAR_VAN_FIRST_10K if needed. Do NOT launch on the unverified value.
"""

RATE_CONFIRMED = False            # flip to True once verified against HMRC
RATE_TAX_YEAR = "2026/27"
RATE_SOURCE_NOTE = "simplified expenses, GOV.UK"

# Pence per mile, stored as pounds for clean arithmetic.
CAR_VAN_FIRST_10K = 0.55          # UNCONFIRMED placeholder; GOV.UK shows 0.45
CAR_VAN_OVER_10K = 0.25           # unchanged across sources
MOTORCYCLE = 0.24                 # unchanged across sources
BICYCLE = 0.20                    # AMAP figure; uncertain under simplified expenses

FIRST_TIER_THRESHOLD_MILES = 10_000   # car/van two-tier boundary, per tax year

# Keys match what is already stored on existing records (car_van | motorbike |
# bicycle) to avoid a migration. The spec's "motorcycle" rename is tracked as a
# separate change (item O) because it touches stored data.
VEHICLE_TYPES: dict[str, dict] = {
    "car_van":   {"label": "Car / van",         "tier1": CAR_VAN_FIRST_10K, "tier2": CAR_VAN_OVER_10K, "threshold": FIRST_TIER_THRESHOLD_MILES},
    "motorbike": {"label": "Motorbike / moped", "tier1": MOTORCYCLE,         "tier2": MOTORCYCLE,        "threshold": None},
    "bicycle":   {"label": "Bicycle / e-bike",  "tier1": BICYCLE,            "tier2": BICYCLE,           "threshold": None},
}

# Default to car/van when the type is missing/unknown — most common, most conservative.
DEFAULT_VEHICLE = "car_van"


def normalise_vehicle(vehicle_type: str | None) -> str:
    return vehicle_type if vehicle_type in VEHICLE_TYPES else DEFAULT_VEHICLE


def mileage_deduction(
    vehicle_type: str | None,
    miles_this_entry: float | None,
    business_miles_ytd_before: float = 0.0,
) -> float:
    """Deduction (£) for `miles_this_entry`, applying the car/van two-tier
    threshold against year-to-date miles already logged this tax year.

    For a single cumulative total (e.g. a summary), pass the total as
    `miles_this_entry` with `business_miles_ytd_before=0`.
    """
    if not miles_this_entry or miles_this_entry <= 0:
        return 0.0
    v = VEHICLE_TYPES[normalise_vehicle(vehicle_type)]
    if v["threshold"] is None:
        return round(miles_this_entry * v["tier1"], 2)
    remaining_tier1 = max(0.0, v["threshold"] - (business_miles_ytd_before or 0.0))
    tier1_miles = min(miles_this_entry, remaining_tier1)
    tier2_miles = miles_this_entry - tier1_miles
    return round(tier1_miles * v["tier1"] + tier2_miles * v["tier2"], 2)


def rate_detail(vehicle_type: str | None) -> str:
    """Fuller two-tier description, e.g. '55p per mile (first 10,000 business
    miles), then 25p'. Used by the `rate` keyword view."""
    v = VEHICLE_TYPES[normalise_vehicle(vehicle_type)]
    p1 = int(round(v["tier1"] * 100))
    if v["threshold"]:
        p2 = int(round(v["tier2"] * 100))
        return f"{p1}p per mile (first {v['threshold']:,} business miles), then {p2}p"
    return f"{p1}p per mile"


def rate_line(vehicle_type: str | None) -> str:
    """One-line statement of the rate in use, for any message that shows a
    deduction. While the rate is unconfirmed it is softly hedged."""
    v = VEHICLE_TYPES[normalise_vehicle(vehicle_type)]
    pence = int(round(v["tier1"] * 100))
    hedge = "" if RATE_CONFIRMED else " (based on current published rates)"
    if v["threshold"]:
        return (f"Estimates use the {pence}p/mile simplified rate for "
                f"{v['label'].lower()} (first {v['threshold']:,} business miles){hedge}.")
    return f"Estimates use the {pence}p/mile simplified rate for {v['label'].lower()}{hedge}."

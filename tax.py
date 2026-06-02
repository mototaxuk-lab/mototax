"""Vehicle-aware mileage maths (simplified-mileage method only).

Rates are HMRC simplified-expenses / approved mileage rates:
- Car or van: 55p per business mile for the first 10,000 miles, 25p thereafter
- Motorbike / moped: 24p per mile (flat)
- Bicycle / e-bike: 20p per mile (flat)

The 10,000-mile threshold is annual and cumulative, so it only bites when a
total is passed in (e.g. the running summary), not on a single weekly entry.
"""

# vehicle_type -> (first_rate, after_rate, threshold_miles or None for flat)
VEHICLE_RATES: dict[str, tuple[float, float, float | None]] = {
    "car_van": (0.55, 0.25, 10_000),
    "motorbike": (0.24, 0.24, None),
    "bicycle": (0.20, 0.20, None),
}

# Default to car/van if the vehicle type is missing/unknown — the most common case
# and the most conservative (highest) rate.
DEFAULT_VEHICLE = "car_van"

VEHICLE_LABELS = {"car_van": "car/van", "motorbike": "motorbike", "bicycle": "bike"}
VEHICLE_EMOJI = {"car_van": "🚗", "motorbike": "🏍️", "bicycle": "🚲"}


def normalise_vehicle(vehicle_type: str | None) -> str:
    return vehicle_type if vehicle_type in VEHICLE_RATES else DEFAULT_VEHICLE


def label(vehicle_type: str | None) -> str:
    return VEHICLE_LABELS[normalise_vehicle(vehicle_type)]


def emoji(vehicle_type: str | None) -> str:
    return VEHICLE_EMOJI[normalise_vehicle(vehicle_type)]


def mileage_deduction(miles: float, vehicle_type: str | None) -> float:
    """Deduction in GBP for `miles` business miles on the given vehicle type."""
    if not miles or miles <= 0:
        return 0.0
    first_rate, after_rate, threshold = VEHICLE_RATES[normalise_vehicle(vehicle_type)]
    if threshold is None or miles <= threshold:
        return miles * first_rate
    return threshold * first_rate + (miles - threshold) * after_rate


def tax_benefit(deduction: float, tax_rate: float | None) -> float:
    """Rough income-tax saving from a deduction at the user's estimate rate."""
    return deduction * (tax_rate or 0.0)

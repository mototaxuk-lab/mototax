"""Vehicle labels/emoji and the tax-benefit helper.

The mileage *rates* and the deduction maths live in `rates.py` (the single source
of truth). This module only holds presentation helpers (short inline labels,
emoji) and the rough tax-benefit calculation, plus thin re-exports so existing
callers keep working.
"""
import rates

# Re-exported so the rest of the app can keep importing these from `tax`.
normalise_vehicle = rates.normalise_vehicle
mileage_deduction = rates.mileage_deduction
rate_line = rates.rate_line
rate_detail = rates.rate_detail

# Short, lowercase inline labels used in conversational copy (e.g. "car/van rate").
# The fuller display labels ("Car / van") live in rates.VEHICLE_TYPES.
VEHICLE_LABELS = {"car_van": "car/van", "motorbike": "motorbike", "bicycle": "bike"}
VEHICLE_EMOJI = {"car_van": "🚗", "motorbike": "🏍️", "bicycle": "🚲"}


def label(vehicle_type: str | None) -> str:
    return VEHICLE_LABELS[normalise_vehicle(vehicle_type)]


def emoji(vehicle_type: str | None) -> str:
    return VEHICLE_EMOJI[normalise_vehicle(vehicle_type)]


def tax_benefit(deduction: float, tax_rate: float | None) -> float:
    """Rough income-tax saving from a deduction at the user's estimate rate."""
    return deduction * (tax_rate or 0.0)

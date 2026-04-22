"""Pure per-field discrepancy computation between provided and reference nutrition."""

from __future__ import annotations

from snaq_verify.logic.constants import (
    CALORIES_TOLERANCE,
    MACRO_TOLERANCE,
    SODIUM_TOLERANCE,
)
from snaq_verify.models import DiscrepancyReport, FieldDiscrepancy, NutritionPer100g

# Field -> tolerance fraction. Kept explicit so the rubric is reviewable.
_FIELD_TOLERANCES: dict[str, float] = {
    "calories_kcal": CALORIES_TOLERANCE,
    "protein_g": MACRO_TOLERANCE,
    "fat_g": MACRO_TOLERANCE,
    "saturated_fat_g": MACRO_TOLERANCE,
    "carbohydrates_g": MACRO_TOLERANCE,
    "sugar_g": MACRO_TOLERANCE,
    "fiber_g": MACRO_TOLERANCE,
    "sodium_mg": SODIUM_TOLERANCE,
}

# Small absolute floor below which a field is "effectively zero" and we
# don't flag a ratio blow-up (e.g. 0.1 g vs 0.05 g sugar).
_ABSOLUTE_FLOOR: dict[str, float] = {
    "calories_kcal": 5.0,
    "protein_g": 0.5,
    "fat_g": 0.5,
    "saturated_fat_g": 0.5,
    "carbohydrates_g": 0.5,
    "sugar_g": 0.5,
    "fiber_g": 0.5,
    "sodium_mg": 5.0,
}


def _compute_field(
    field: str,
    provided: float | None,
    reference: float | None,
) -> FieldDiscrepancy:
    if provided is None or reference is None:
        return FieldDiscrepancy(
            field=field,
            provided=provided,
            reference=reference,
            delta_fraction=None,
            exceeds_tolerance=False,
        )

    floor = _ABSOLUTE_FLOOR.get(field, 0.0)
    tolerance = _FIELD_TOLERANCES.get(field, MACRO_TOLERANCE)

    # Below the floor on both sides -> treat as agreement regardless of ratio.
    if abs(provided) < floor and abs(reference) < floor:
        return FieldDiscrepancy(
            field=field,
            provided=provided,
            reference=reference,
            delta_fraction=0.0,
            exceeds_tolerance=False,
        )

    # Reference near zero but provided isn't -> flag as exceeding.
    if abs(reference) < 1e-9:
        return FieldDiscrepancy(
            field=field,
            provided=provided,
            reference=reference,
            delta_fraction=None,
            exceeds_tolerance=abs(provided) >= floor,
        )

    delta = (provided - reference) / reference
    return FieldDiscrepancy(
        field=field,
        provided=provided,
        reference=reference,
        delta_fraction=delta,
        exceeds_tolerance=abs(delta) > tolerance,
    )


def calculate_discrepancy(
    provided: NutritionPer100g,
    reference: NutritionPer100g,
) -> DiscrepancyReport:
    """Per-field deltas between provided and reference nutrition.

    Missing values are returned with ``delta_fraction=None`` and
    ``exceeds_tolerance=False`` — a missing reference is inconclusive,
    not a discrepancy.
    """
    fields = [
        _compute_field(name, getattr(provided, name), getattr(reference, name))
        for name in _FIELD_TOLERANCES
    ]
    return DiscrepancyReport(
        fields=fields,
        any_exceeds_tolerance=any(f.exceeds_tolerance for f in fields),
    )

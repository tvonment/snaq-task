"""Pure check: is a reference nutrition record complete enough to trust?

We treat a reference as "incomplete" when it has zero kcal or when it's
missing two or more of the four core macros (protein, fat, carbs,
saturated fat). The agent uses this signal to cap confidence.
"""

from __future__ import annotations

from snaq_verify.models import NutritionPer100g, ReferenceCompletenessResult

_CORE_MACROS: tuple[str, ...] = (
    "protein_g",
    "fat_g",
    "carbohydrates_g",
    "saturated_fat_g",
)


def assess_reference_completeness(
    reference: NutritionPer100g,
) -> ReferenceCompletenessResult:
    """Return structured completeness info about a reference record."""
    missing = [m for m in _CORE_MACROS if getattr(reference, m) in (None, 0.0)]
    # `saturated_fat_g` is genuinely optional in FDC; only count it as
    # "missing" if the other three are also partial.
    real_missing = [m for m in missing if m != "saturated_fat_g"]
    zero_kcal = reference.calories_kcal <= 0.0
    is_incomplete = zero_kcal or len(real_missing) >= 2
    reason = None
    if zero_kcal:
        reason = "reference has zero calories"
    elif is_incomplete:
        reason = f"reference missing core macros: {', '.join(real_missing)}"
    return ReferenceCompletenessResult(
        is_incomplete=is_incomplete,
        missing_fields=missing,
        reason=reason,
    )

"""Pure macro consistency check.

A quick sanity layer that runs BEFORE any external lookup: does
``protein*4 + carbs*4 + fat*9`` approximate the stated calories? If not,
the item is internally inconsistent — still worth verifying, but the
agent should know up front.
"""

from __future__ import annotations

from snaq_verify.logic.constants import (
    KCAL_PER_G_CARB,
    KCAL_PER_G_FAT,
    KCAL_PER_G_PROTEIN,
    MACRO_CONSISTENCY_TOLERANCE,
)
from snaq_verify.models import MacroConsistencyResult, NutritionPer100g


def validate_macro_consistency(nutrition: NutritionPer100g) -> MacroConsistencyResult:
    """Compare stated calories to kcal computed from the 4/4/9 rule.

    Returns a :class:`MacroConsistencyResult` with the signed delta fraction
    and a boolean ``is_consistent`` flag.
    """
    computed = (
        nutrition.protein_g * KCAL_PER_G_PROTEIN
        + nutrition.carbohydrates_g * KCAL_PER_G_CARB
        + nutrition.fat_g * KCAL_PER_G_FAT
    )
    stated = nutrition.calories_kcal
    # Guard against division by zero for edge cases (pure water, etc.).
    denom = max(computed, 1.0)
    delta_fraction = (stated - computed) / denom
    is_consistent = abs(delta_fraction) <= MACRO_CONSISTENCY_TOLERANCE
    return MacroConsistencyResult(
        stated_kcal=stated,
        computed_kcal=computed,
        delta_fraction=delta_fraction,
        is_consistent=is_consistent,
    )

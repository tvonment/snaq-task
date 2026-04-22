"""Tests for :mod:`snaq_verify.logic.validation`."""

from __future__ import annotations

import pytest

from snaq_verify.logic.validation import validate_macro_consistency
from snaq_verify.models import NutritionPer100g


def _nutrition(
    *, calories: float, protein: float, carbs: float, fat: float
) -> NutritionPer100g:
    return NutritionPer100g(
        calories_kcal=calories,
        protein_g=protein,
        fat_g=fat,
        carbohydrates_g=carbs,
    )


@pytest.mark.parametrize(
    ("calories", "protein", "carbs", "fat", "expected_consistent"),
    [
        # Chicken breast (USDA SR Legacy): 31 P, 3.6 F, 0 C -> 156.4 kcal; stated 165 -> +5.5%
        pytest.param(165, 31.0, 0.0, 3.6, True, id="chicken_breast_within_tolerance"),
        # Banana: 1.1 P, 23 C, 0.3 P -> 99 kcal; stated 89 -> -10.1% borderline fail
        pytest.param(89, 1.1, 23.0, 0.3, False, id="banana_borderline_fails"),
        # Pure protein shake: 100 P, 0 C, 0 F -> 400 kcal; stated 400 -> 0%
        pytest.param(400, 100.0, 0.0, 0.0, True, id="pure_protein_exact"),
        # Nonsense: 1000 kcal but tiny macros -> far out of tolerance
        pytest.param(1000, 1.0, 1.0, 1.0, False, id="inflated_calories_fails"),
        # Olive oil: 884 kcal, 100 F -> 900 computed, -1.8% -> passes
        pytest.param(884, 0.0, 0.0, 100.0, True, id="olive_oil_fat_only"),
    ],
)
def test_macro_consistency_matches_expected(
    calories: float,
    protein: float,
    carbs: float,
    fat: float,
    expected_consistent: bool,
) -> None:
    result = validate_macro_consistency(
        _nutrition(calories=calories, protein=protein, carbs=carbs, fat=fat)
    )
    assert result.is_consistent is expected_consistent


def test_macro_consistency_delta_fraction_is_signed() -> None:
    # Inflated calories -> positive delta (stated > computed).
    inflated = validate_macro_consistency(
        _nutrition(calories=500, protein=10, carbs=10, fat=10)
    )
    assert inflated.delta_fraction > 0
    # Deflated calories -> negative delta.
    deflated = validate_macro_consistency(
        _nutrition(calories=50, protein=10, carbs=10, fat=10)
    )
    assert deflated.delta_fraction < 0


def test_macro_consistency_zero_macros_does_not_divide_by_zero() -> None:
    # Water-like row: should not raise. Stated 0 vs computed 0 -> delta=0.
    result = validate_macro_consistency(
        _nutrition(calories=0, protein=0, carbs=0, fat=0)
    )
    assert result.is_consistent is True
    assert result.delta_fraction == pytest.approx(0.0)

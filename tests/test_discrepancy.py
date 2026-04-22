"""Tests for :mod:`snaq_verify.logic.discrepancy`."""

from __future__ import annotations

import pytest

from snaq_verify.logic.discrepancy import calculate_discrepancy
from snaq_verify.models import NutritionPer100g


def _n(**overrides: float | None) -> NutritionPer100g:
    base: dict[str, float | None] = {
        "calories_kcal": 100.0,
        "protein_g": 10.0,
        "fat_g": 5.0,
        "carbohydrates_g": 10.0,
        "saturated_fat_g": 2.0,
        "sugar_g": 3.0,
        "fiber_g": 1.0,
        "sodium_mg": 100.0,
    }
    base.update(overrides)
    return NutritionPer100g(**base)  # type: ignore[arg-type]


def test_identical_values_have_no_discrepancy() -> None:
    report = calculate_discrepancy(_n(), _n())
    assert report.any_exceeds_tolerance is False
    for field in report.fields:
        assert field.exceeds_tolerance is False
        # delta is exactly 0 (or 0 from the floor short-circuit for zero-zero).
        assert field.delta_fraction == pytest.approx(0.0)


def test_calories_exceeds_when_more_than_ten_percent_off() -> None:
    # 100 vs 120 -> +20 %
    report = calculate_discrepancy(_n(calories_kcal=120), _n(calories_kcal=100))
    calories = next(f for f in report.fields if f.field == "calories_kcal")
    assert calories.delta_fraction == pytest.approx(0.20)
    assert calories.exceeds_tolerance is True
    assert report.any_exceeds_tolerance is True


def test_sodium_tolerance_is_wider_than_macros() -> None:
    # 100 vs 120 is +20 %: within 25% sodium tolerance, over 15% macro tolerance.
    report = calculate_discrepancy(_n(sodium_mg=120), _n(sodium_mg=100))
    sodium = next(f for f in report.fields if f.field == "sodium_mg")
    assert sodium.exceeds_tolerance is False


def test_missing_reference_is_not_a_discrepancy() -> None:
    report = calculate_discrepancy(_n(), _n(sugar_g=None))
    sugar = next(f for f in report.fields if f.field == "sugar_g")
    assert sugar.delta_fraction is None
    assert sugar.exceeds_tolerance is False


def test_near_zero_both_sides_is_not_a_discrepancy() -> None:
    # Chicken breast carbs are 0 vs USDA 0.01 -> floor short-circuits.
    report = calculate_discrepancy(
        _n(carbohydrates_g=0.0, sugar_g=0.0),
        _n(carbohydrates_g=0.01, sugar_g=0.02),
    )
    carbs = next(f for f in report.fields if f.field == "carbohydrates_g")
    sugar = next(f for f in report.fields if f.field == "sugar_g")
    assert carbs.exceeds_tolerance is False
    assert sugar.exceeds_tolerance is False


def test_reference_zero_but_provided_large_flags_discrepancy() -> None:
    report = calculate_discrepancy(_n(sugar_g=10.0), _n(sugar_g=0.0))
    sugar = next(f for f in report.fields if f.field == "sugar_g")
    assert sugar.exceeds_tolerance is True
    assert sugar.delta_fraction is None  # undefined ratio, still flagged

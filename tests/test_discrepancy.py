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


def test_low_value_fat_reports_honest_ratio_not_masked_zero() -> None:
    # Regression: banana fat 0.30 g vs CIQUAL 0.27 g is ~+11 %.
    # The old floor of 0.5 g masked this to delta_fraction=0.0.
    report = calculate_discrepancy(
        _n(fat_g=0.30, saturated_fat_g=0.1),
        _n(fat_g=0.27, saturated_fat_g=0.1),
    )
    fat = next(f for f in report.fields if f.field == "fat_g")
    assert fat.delta_fraction is not None
    assert fat.delta_fraction == pytest.approx((0.30 - 0.27) / 0.27, rel=1e-3)
    # Under 15 % macro tolerance, so not flagged — but the number is real.
    assert fat.exceeds_tolerance is False


def test_low_value_saturated_fat_reports_honest_ratio() -> None:
    # Banana sat_fat 0.10 g vs 0.112 g is ~-10.7 %.
    report = calculate_discrepancy(_n(saturated_fat_g=0.10), _n(saturated_fat_g=0.112))
    sat = next(f for f in report.fields if f.field == "saturated_fat_g")
    assert sat.delta_fraction is not None
    assert sat.delta_fraction == pytest.approx((0.10 - 0.112) / 0.112, rel=1e-3)
    assert sat.exceeds_tolerance is False


def test_low_value_sodium_reports_honest_ratio() -> None:
    # Banana sodium 1.0 mg vs 1.3 mg is ~-23 %, under the 25 % sodium band.
    report = calculate_discrepancy(_n(sodium_mg=1.0), _n(sodium_mg=1.3))
    sodium = next(f for f in report.fields if f.field == "sodium_mg")
    assert sodium.delta_fraction is not None
    assert sodium.delta_fraction == pytest.approx((1.0 - 1.3) / 1.3, rel=1e-3)
    assert sodium.exceeds_tolerance is False


def test_trace_level_sugar_still_short_circuits() -> None:
    # 0.02 g vs 0.05 g sugar is noise, not a discrepancy: both below floor.
    report = calculate_discrepancy(_n(sugar_g=0.02), _n(sugar_g=0.05))
    sugar = next(f for f in report.fields if f.field == "sugar_g")
    assert sugar.delta_fraction == pytest.approx(0.0)
    assert sugar.exceeds_tolerance is False

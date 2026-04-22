"""Tests for reference-completeness assessment."""

from __future__ import annotations

import pytest

from snaq_verify.logic.completeness import assess_reference_completeness
from snaq_verify.models import NutritionPer100g


def _nutrition(**overrides: float | None) -> NutritionPer100g:
    base: dict[str, float | None] = {
        "calories_kcal": 100.0,
        "protein_g": 10.0,
        "fat_g": 5.0,
        "carbohydrates_g": 15.0,
        "saturated_fat_g": 1.0,
    }
    base.update(overrides)
    # Keep saturated_fat_g <= fat_g so the input model accepts the record.
    if (
        base.get("saturated_fat_g") is not None
        and base["saturated_fat_g"] > (base["fat_g"] or 0.0)
    ):
        base["saturated_fat_g"] = base["fat_g"]
    return NutritionPer100g(**base)  # type: ignore[arg-type]


def test_complete_reference_is_not_flagged() -> None:
    result = assess_reference_completeness(_nutrition())
    assert result.is_incomplete is False
    assert result.reason is None


def test_zero_kcal_reference_is_incomplete() -> None:
    result = assess_reference_completeness(_nutrition(calories_kcal=0.0))
    assert result.is_incomplete is True
    assert "zero calories" in (result.reason or "")


def test_missing_saturated_fat_alone_is_not_incomplete() -> None:
    result = assess_reference_completeness(_nutrition(saturated_fat_g=None))
    # Saturated fat is genuinely optional in FDC; don't flag on its own.
    assert result.is_incomplete is False


def test_two_missing_core_macros_is_incomplete() -> None:
    result = assess_reference_completeness(_nutrition(protein_g=0.0, fat_g=0.0))
    assert result.is_incomplete is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"protein_g": 0.0},
        {"fat_g": 0.0},
        {"carbohydrates_g": 0.0},
    ],
)
def test_single_missing_core_macro_is_not_incomplete(overrides: dict) -> None:
    # A single zero macro is plausible (e.g., raw chicken has 0 g carbs),
    # so the completeness check must not fire on its own.
    result = assess_reference_completeness(_nutrition(**overrides))
    assert result.is_incomplete is False

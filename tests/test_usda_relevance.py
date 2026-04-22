"""Tests for the USDA relevance gate and kcal fallback."""

from __future__ import annotations

import httpx
import pytest
import respx

from snaq_verify.clients.usda import USDAClient, _normalize_fdc_food


@respx.mock
async def test_irrelevant_top_hit_is_filtered_out() -> None:
    # "Whole Milk" query but USDA returns Crackers -- should be a miss,
    # not a wrong reference returned to the agent.
    respx.get("https://api.nal.usda.gov/fdc/v1/foods/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "foods": [
                    {
                        "fdcId": 999,
                        "description": "Crackers, saltines, unsalted tops",
                        "dataType": "SR Legacy",
                        "foodNutrients": [
                            {"nutrientId": 1008, "value": 430},
                            {"nutrientId": 1003, "value": 9},
                            {"nutrientId": 1004, "value": 10},
                            {"nutrientId": 1005, "value": 75},
                        ],
                    }
                ]
            },
        )
    )
    async with USDAClient(api_key="test") as client:
        ref = await client.search("Whole Milk", data_type="Foundation")
    assert ref is None


def test_kcal_fallback_uses_kj_when_1008_missing() -> None:
    food = {
        "fdcId": 111,
        "description": "Chicken, breast, raw",
        "foodNutrients": [
            {"nutrientId": 1003, "value": 22.5},  # protein
            {"nutrientId": 1004, "value": 2.62},  # fat
            {"nutrientId": 1005, "value": 0.0},   # carbs
            {"nutrientId": 1062, "value": 502.0},  # kJ -> 120 kcal
        ],
    }
    ref = _normalize_fdc_food(food, "Foundation")
    assert ref.nutrition.calories_kcal == pytest.approx(502.0 / 4.184, rel=1e-3)
    assert ref.match_notes is not None
    assert "kJ" in ref.match_notes


def test_kcal_fallback_uses_atwater_when_no_energy_field() -> None:
    food = {
        "fdcId": 222,
        "description": "Chicken, breast, raw",
        "foodNutrients": [
            {"nutrientId": 1003, "value": 22.5},  # protein
            {"nutrientId": 1004, "value": 2.62},  # fat
            {"nutrientId": 1005, "value": 0.0},   # carbs
        ],
    }
    ref = _normalize_fdc_food(food, "Foundation")
    # 22.5*4 + 0*4 + 2.62*9 = 113.58
    assert ref.nutrition.calories_kcal == pytest.approx(113.58, rel=1e-3)
    assert ref.match_notes is not None
    assert "Atwater" in ref.match_notes

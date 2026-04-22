"""Tests for the Open Food Facts client."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from snaq_verify.clients.openfoodfacts import OpenFoodFactsClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def nutella_fixture() -> dict:
    return json.loads((FIXTURES / "off_nutella.json").read_text())


@pytest.fixture
def not_found_fixture() -> dict:
    return json.loads((FIXTURES / "off_not_found.json").read_text())


@respx.mock
async def test_barcode_lookup_returns_normalized_reference(nutella_fixture: dict) -> None:
    respx.get("https://world.openfoodfacts.org/api/v2/product/3017620422003.json").mock(
        return_value=httpx.Response(200, json=nutella_fixture)
    )
    async with OpenFoodFactsClient() as client:
        ref = await client.lookup_by_barcode("3017620422003")
    assert ref is not None
    assert ref.citation.source == "OpenFoodFacts"
    assert ref.citation.source_id == "3017620422003"
    assert ref.nutrition.calories_kcal == pytest.approx(539)
    # OFF sodium is grams -> we normalize to mg (0.041 g -> 41 mg).
    assert ref.nutrition.sodium_mg == pytest.approx(41.0)


@respx.mock
async def test_barcode_lookup_returns_none_on_not_found(not_found_fixture: dict) -> None:
    respx.get("https://world.openfoodfacts.org/api/v2/product/0000000000000.json").mock(
        return_value=httpx.Response(200, json=not_found_fixture)
    )
    async with OpenFoodFactsClient() as client:
        assert await client.lookup_by_barcode("0000000000000") is None


@respx.mock
async def test_barcode_lookup_converts_kj_to_kcal_when_kcal_missing() -> None:
    payload = {
        "status": 1,
        "product": {
            "product_name": "Test",
            "nutriments": {
                "energy_100g": 2092,  # kJ
                "proteins_100g": 10,
                "fat_100g": 20,
                "carbohydrates_100g": 50,
            },
        },
    }
    respx.get("https://world.openfoodfacts.org/api/v2/product/1.json").mock(
        return_value=httpx.Response(200, json=payload)
    )
    async with OpenFoodFactsClient() as client:
        ref = await client.lookup_by_barcode("1")
    assert ref is not None
    assert ref.nutrition.calories_kcal == pytest.approx(2092 / 4.184, rel=1e-3)


@respx.mock
async def test_barcode_lookup_retries_on_5xx_then_succeeds(nutella_fixture: dict) -> None:
    route = respx.get(
        "https://world.openfoodfacts.org/api/v2/product/3017620422003.json"
    ).mock(
        side_effect=[
            httpx.Response(503, text="maintenance"),
            httpx.Response(200, json=nutella_fixture),
        ]
    )
    async with OpenFoodFactsClient() as client:
        ref = await client.lookup_by_barcode("3017620422003")
    assert ref is not None
    assert route.call_count == 2


@respx.mock
async def test_barcode_lookup_honors_retry_after_on_429(
    nutella_fixture: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("snaq_verify.clients.openfoodfacts.asyncio.sleep", _fake_sleep)

    route = respx.get(
        "https://world.openfoodfacts.org/api/v2/product/3017620422003.json"
    ).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}, text="slow down"),
            httpx.Response(200, json=nutella_fixture),
        ]
    )
    async with OpenFoodFactsClient() as client:
        ref = await client.lookup_by_barcode("3017620422003")
    assert ref is not None
    assert route.call_count == 2
    # At least one inline sleep should have honored the 2s Retry-After hint.
    assert any(s >= 2.0 for s in sleeps)


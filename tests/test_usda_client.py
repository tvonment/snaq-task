"""Tests for the USDA FoodData Central client."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from snaq_verify.clients.usda import USDAClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def chicken_fixture() -> dict:
    return json.loads((FIXTURES / "usda_chicken_search.json").read_text())


@respx.mock
async def test_search_returns_normalized_reference(chicken_fixture: dict) -> None:
    respx.get("https://api.nal.usda.gov/fdc/v1/foods/search").mock(
        return_value=httpx.Response(200, json=chicken_fixture)
    )
    async with USDAClient(api_key="test") as client:
        ref = await client.search("chicken breast raw", data_type="SR Legacy")
    assert ref is not None
    assert ref.citation.source == "USDA"
    assert ref.citation.source_id == "171477"
    assert ref.citation.data_type == "SR Legacy"
    assert ref.nutrition.protein_g == pytest.approx(22.5)
    assert ref.nutrition.sodium_mg == pytest.approx(45.0)


@respx.mock
async def test_search_returns_none_when_no_foods() -> None:
    respx.get("https://api.nal.usda.gov/fdc/v1/foods/search").mock(
        return_value=httpx.Response(200, json={"foods": []})
    )
    async with USDAClient(api_key="test") as client:
        assert await client.search("asdfzxcv", data_type="Foundation") is None


@respx.mock
async def test_search_returns_none_on_404() -> None:
    respx.get("https://api.nal.usda.gov/fdc/v1/foods/search").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    async with USDAClient(api_key="test") as client:
        assert await client.search("asdf", data_type="Foundation") is None


@respx.mock
async def test_search_retries_on_429_then_succeeds(chicken_fixture: dict) -> None:
    route = respx.get("https://api.nal.usda.gov/fdc/v1/foods/search").mock(
        side_effect=[
            httpx.Response(429, text="rate limited"),
            httpx.Response(200, json=chicken_fixture),
        ]
    )
    async with USDAClient(api_key="test") as client:
        ref = await client.search("chicken", data_type="SR Legacy")
    assert ref is not None
    assert route.call_count == 2


@respx.mock
async def test_search_retries_on_timeout_then_fails() -> None:
    respx.get("https://api.nal.usda.gov/fdc/v1/foods/search").mock(
        side_effect=httpx.ConnectTimeout("boom")
    )
    async with USDAClient(api_key="test") as client:
        with pytest.raises(httpx.TimeoutException):
            await client.search("chicken", data_type="SR Legacy")

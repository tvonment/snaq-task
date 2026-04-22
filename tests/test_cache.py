"""Tests for :mod:`snaq_verify.cache`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from snaq_verify.cache import _MISS, ResponseCache
from snaq_verify.models import NutritionPer100g, NutritionReference, SourceCitation


def _make_ref() -> NutritionReference:
    return NutritionReference(
        nutrition=NutritionPer100g(
            calories_kcal=100, protein_g=10, fat_g=5, carbohydrates_g=10
        ),
        citation=SourceCitation(
            source="USDA",
            source_id="12345",
            url="https://example.test/12345",
            data_type="Foundation",
            retrieved_at=datetime.now(UTC),
        ),
        match_name="Test food",
    )


def test_miss_returns_sentinel(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "c.sqlite")
    assert cache.get("USDA", "unseen") is _MISS


def test_roundtrip_preserves_reference(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "c.sqlite")
    ref = _make_ref()
    cache.set("USDA", "chicken breast raw", ref)
    got = cache.get("USDA", "chicken breast raw")
    assert isinstance(got, NutritionReference)
    assert got.citation.source_id == "12345"
    assert got.nutrition.protein_g == 10


def test_cached_none_is_distinguishable_from_miss(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "c.sqlite")
    cache.set("USDA", "asdfqwer", None)
    assert cache.get("USDA", "asdfqwer") is None
    assert cache.get("USDA", "different") is _MISS


def test_source_scope_isolates_entries(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "c.sqlite")
    ref = _make_ref()
    cache.set("USDA", "same-key", ref)
    assert cache.get("OpenFoodFacts", "same-key") is _MISS

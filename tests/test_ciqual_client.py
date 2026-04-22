"""Tests for the CIQUAL local client."""

from __future__ import annotations

from snaq_verify.clients.ciqual import CIQUALClient


def test_ciqual_finds_bundled_whole_milk() -> None:
    client = CIQUALClient()
    ref = client.search("Whole Milk")
    assert ref is not None
    assert ref.citation.source == "CIQUAL"
    assert 3.0 <= ref.nutrition.fat_g <= 4.0
    assert 60 <= ref.nutrition.calories_kcal <= 70


def test_ciqual_matches_via_alias() -> None:
    client = CIQUALClient()
    ref = client.search("rolled oats")
    assert ref is not None
    assert "oat" in ref.match_name.lower()


def test_ciqual_returns_none_for_unrelated_query() -> None:
    client = CIQUALClient()
    assert client.search("zxcvbnm") is None


def test_ciqual_returns_none_below_relevance_threshold() -> None:
    # "Plastic widget" shares no content tokens with any food record.
    client = CIQUALClient()
    assert client.search("plastic widget") is None

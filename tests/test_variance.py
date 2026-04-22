"""Tests for :mod:`snaq_verify.logic.variance`."""

from __future__ import annotations

import pytest

from snaq_verify.logic.variance import check_known_variance


@pytest.mark.parametrize(
    ("name", "category", "expected_key"),
    [
        ("Salmon, Atlantic, Farmed, Raw", "Fish & Seafood", "salmon-farmed"),
        ("Salmon, Wild, Sockeye, Raw", "Fish & Seafood", "salmon-wild"),
        ("Avocado, Raw", "Fruit", "avocado"),
        ("Whole Milk, 3.5% Fat", "Dairy", "whole-milk"),
        ("Ground Beef, 80/20", "Meat & Poultry", "ground-beef"),
    ],
)
def test_catalogue_hits(name: str, category: str, expected_key: str) -> None:
    info = check_known_variance(name, category)
    assert info is not None
    assert info.match_key == expected_key
    assert info.variable_fields  # non-empty


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("Chicken Breast, Skinless, Raw", "Meat & Poultry"),
        ("Banana, Raw", "Fruit"),
        ("Rolled Oats, Dry", "Grains & Cereals"),
        # Salmon in the wrong category -> no match (guards against false positives).
        ("Salmon Flavored Chips, Farmed", "Snacks"),
    ],
)
def test_catalogue_misses(name: str, category: str) -> None:
    assert check_known_variance(name, category) is None


def test_farmed_salmon_flags_fat_as_variable() -> None:
    info = check_known_variance("Salmon, Atlantic, Farmed, Raw", "Fish & Seafood")
    assert info is not None
    assert "fat_g" in info.variable_fields

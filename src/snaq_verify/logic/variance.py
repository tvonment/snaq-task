"""Catalogue of foods with known high natural variance.

When a food's nutrition varies materially by cultivar, origin, feed,
season, or processing, we do NOT flag the provided value as a discrepancy
just because it disagrees with one reference entry. Instead we return
``HIGH_VARIANCE`` and list which fields are expected to vary.

This catalogue is deliberately small and conservative. It documents
judgement calls; it is not a replacement for a real food science
knowledge base.
"""

from __future__ import annotations

from dataclasses import dataclass

from snaq_verify.models import VarianceInfo


@dataclass(frozen=True)
class _VarianceRule:
    key: str
    name_keywords: tuple[str, ...]  # ALL must appear (case-insensitive) in the item name
    categories: tuple[str, ...]  # or empty = any category
    reason: str
    variable_fields: tuple[str, ...]


_CATALOGUE: tuple[_VarianceRule, ...] = (
    _VarianceRule(
        key="salmon-farmed",
        name_keywords=("salmon", "farmed"),
        categories=("Fish & Seafood",),
        reason=(
            "Farmed Atlantic salmon fat content varies widely (typically 5-15 g/100 g) "
            "depending on feed composition and harvest age."
        ),
        variable_fields=("fat_g", "saturated_fat_g", "calories_kcal"),
    ),
    _VarianceRule(
        key="salmon-wild",
        name_keywords=("salmon", "wild"),
        categories=("Fish & Seafood",),
        reason="Wild salmon macronutrient composition varies by species and season.",
        variable_fields=("fat_g", "saturated_fat_g", "calories_kcal"),
    ),
    _VarianceRule(
        key="avocado",
        name_keywords=("avocado",),
        categories=("Fruit",),
        reason=(
            "Avocado fat content varies significantly by cultivar "
            "(Hass vs Fuerte) and ripeness."
        ),
        variable_fields=("fat_g", "calories_kcal"),
    ),
    _VarianceRule(
        key="whole-milk",
        name_keywords=("whole", "milk"),
        categories=("Dairy",),
        reason="Whole milk fat is standardised differently across regions (3.25% US vs 3.5% EU).",
        variable_fields=("fat_g", "saturated_fat_g", "calories_kcal"),
    ),
    _VarianceRule(
        key="ground-beef",
        name_keywords=("ground", "beef"),
        categories=("Meat & Poultry",),
        reason="Ground beef fat content is a grind-ratio spec (70/30 to 93/7), not a fixed value.",
        variable_fields=("fat_g", "saturated_fat_g", "calories_kcal", "protein_g"),
    ),
)


def check_known_variance(name: str, category: str) -> VarianceInfo | None:
    """Return :class:`VarianceInfo` if the item matches a known-variance rule."""
    needle = name.lower()
    for rule in _CATALOGUE:
        if rule.categories and category not in rule.categories:
            continue
        if all(kw in needle for kw in rule.name_keywords):
            return VarianceInfo(
                match_key=rule.key,
                reason=rule.reason,
                variable_fields=list(rule.variable_fields),
            )
    return None

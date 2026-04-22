"""CIQUAL client — local lookup against a bundled ANSES subset.

CIQUAL (ANSES) is the French government food composition table. The
full dataset is large; for this evaluation we ship a small curated
subset (see ``data/ciqual_subset.json`` and
``data/CIQUAL_LICENSE.md``) that covers the items in
``food_items.json``. The interface mirrors the USDA client so the agent
routing reads the same.

Lookup is a simple tokenised name match against ``name`` + ``aliases``
with a Jaccard relevance gate. No HTTP, no cache — the dataset is in
memory once per process.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from snaq_verify.logic.constants import USDA_RELEVANCE_MIN_JACCARD
from snaq_verify.models import NutritionPer100g, NutritionReference, SourceCitation

_DEFAULT_DATA_PATH = Path(__file__).resolve().parents[3] / "data" / "ciqual_subset.json"

# Stop-word list mirrors the USDA client for behavioural symmetry.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "of", "in", "with", "without",
        "raw", "cooked", "fresh", "whole", "ns",
    }
)


def _tokenize(text: str) -> set[str]:
    out: set[str] = set()
    current: list[str] = []
    for ch in text.lower():
        if ch.isalpha():
            current.append(ch)
        else:
            if current:
                tok = "".join(current)
                if len(tok) > 1 and tok not in _STOPWORDS:
                    out.add(tok)
                current = []
    if current:
        tok = "".join(current)
        if len(tok) > 1 and tok not in _STOPWORDS:
            out.add(tok)
    return out


class CIQUALClient:
    """In-memory client over a bundled CIQUAL subset."""

    def __init__(self, data_path: Path | None = None) -> None:
        """Load and tokenise the bundled subset. Trivially cheap."""
        path = data_path or _DEFAULT_DATA_PATH
        payload = json.loads(path.read_text())
        self._foods: list[dict] = payload.get("foods", [])
        self._index: list[tuple[set[str], dict]] = []
        for food in self._foods:
            tokens: set[str] = _tokenize(food["name"])
            for alias in food.get("aliases", []):
                tokens |= _tokenize(alias)
            self._index.append((tokens, food))

    def search(self, query: str) -> NutritionReference | None:
        """Return the best-matching food by token-recall overlap, or None."""
        q = _tokenize(query)
        if not q:
            return None
        best: tuple[float, dict] | None = None
        for tokens, food in self._index:
            overlap = len(q & tokens)
            if overlap == 0:
                continue
            # Recall-style score: fraction of query tokens present.
            score = overlap / len(q)
            if best is None or score > best[0]:
                best = (score, food)
        if best is None or best[0] < USDA_RELEVANCE_MIN_JACCARD:
            return None
        return _normalize(best[1])


def _normalize(food: dict) -> NutritionReference:
    nutrition = NutritionPer100g(
        calories_kcal=float(food["calories_kcal"]),
        protein_g=float(food["protein_g"]),
        fat_g=float(food["fat_g"]),
        saturated_fat_g=_opt_float(food.get("saturated_fat_g")),
        carbohydrates_g=float(food["carbohydrates_g"]),
        sugar_g=_opt_float(food.get("sugar_g")),
        fiber_g=_opt_float(food.get("fiber_g")),
        sodium_mg=_opt_float(food.get("sodium_mg")),
    )
    code = str(food.get("ciqual_code", ""))
    citation = SourceCitation(
        source="CIQUAL",
        source_id=code,
        url=f"https://ciqual.anses.fr/#/aliments/{code}" if code else None,
        data_type=None,
        retrieved_at=datetime.now(UTC),
    )
    return NutritionReference(
        nutrition=nutrition,
        citation=citation,
        match_name=food["name"],
        match_notes="CIQUAL 2020 bundled subset",
    )


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None

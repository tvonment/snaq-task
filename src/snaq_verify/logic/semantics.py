"""Known definitional quirks between nutrition sources.

Different food databases don't always mean the same thing by
"carbohydrate" or "energy". The judge flagged five ``unit_mismatch``
concerns on the baseline run -- this module gives the agent a pure
tool it can call to surface those mismatches *before* computing
discrepancies.

Scope is deliberately small: only the cases we actually hit with
USDA Foundation + CIQUAL + OFF. Not an ontology.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from snaq_verify.models import SourceName

SemanticIssueKind = Literal[
    "carbs_definition",
    "energy_definition",
    "sodium_vs_salt",
    "protein_conversion",
]


class SemanticNote(BaseModel):
    """One known definitional difference between two sources."""

    model_config = ConfigDict(extra="forbid")

    kind: SemanticIssueKind = Field(
        description="Category of definitional mismatch."
    )
    sources: tuple[SourceName, SourceName] = Field(
        description="Ordered pair of source names this note applies to."
    )
    explanation: str = Field(
        description="One or two sentences a reviewer can paste into a note."
    )
    affected_fields: list[str] = Field(
        description="Field names the mismatch affects, e.g. ['carbohydrates_g']."
    )


# Intentionally compact. Reviewed April 2026 against USDA FDC 2026 docs
# and CIQUAL 2020 table definitions.
_CATALOGUE: list[SemanticNote] = [
    SemanticNote(
        kind="carbs_definition",
        sources=("USDA", "CIQUAL"),
        explanation=(
            "USDA FoodData Central reports 'Carbohydrate, by difference' "
            "which includes dietary fibre. CIQUAL reports 'glucides' "
            "(available carbohydrates), which excludes fibre. Expect "
            "USDA carbs to be higher than CIQUAL carbs by roughly the "
            "fibre content of the food."
        ),
        affected_fields=["carbohydrates_g"],
    ),
    SemanticNote(
        kind="energy_definition",
        sources=("USDA", "CIQUAL"),
        explanation=(
            "USDA Foundation records may report Energy (kcal, id 1008) "
            "directly OR only Energy (Atwater General Factors, 2047). "
            "CIQUAL uses the European Regulation 1169/2011 factors "
            "(4/4/9/2). Small kcal deltas between the two can be purely "
            "definitional, not real discrepancies."
        ),
        affected_fields=["calories_kcal"],
    ),
    SemanticNote(
        kind="sodium_vs_salt",
        sources=("OpenFoodFacts", "USDA"),
        explanation=(
            "Open Food Facts sometimes stores 'salt' (NaCl) rather than "
            "'sodium'. sodium_mg = salt_g * 400 approximately. Check the "
            "OFF payload's raw fields before trusting sodium_mg directly."
        ),
        affected_fields=["sodium_mg"],
    ),
    SemanticNote(
        kind="protein_conversion",
        sources=("USDA", "CIQUAL"),
        explanation=(
            "Both sources use Kjeldahl-nitrogen-based protein, but the "
            "N-to-protein factor varies by food (6.25 default, 5.7 for "
            "wheat, 6.38 for dairy). Small protein deltas across sources "
            "can stem from this factor, not real disagreement."
        ),
        affected_fields=["protein_g"],
    ),
]


class SemanticsComparison(BaseModel):
    """Result of comparing two sources for known definitional mismatches."""

    model_config = ConfigDict(extra="forbid")

    notes: list[SemanticNote] = Field(
        description="All catalogue entries that apply to this source pair."
    )


def compare_semantics(a: SourceName, b: SourceName) -> SemanticsComparison:
    """Return catalogue entries applicable to comparing sources ``a`` and ``b``.

    Source order is irrelevant: (USDA, CIQUAL) and (CIQUAL, USDA) return
    the same notes.
    """
    pair = {a, b}
    matches = [n for n in _CATALOGUE if set(n.sources) == pair]
    return SemanticsComparison(notes=matches)

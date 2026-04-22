"""Shared Pydantic models.

Models are intentionally co-located in one module so the agent's tool
signatures, the cache serializer, and the report renderer all speak the
same types.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Input models (mirror the schema in food_items.json)
# ---------------------------------------------------------------------------


class Portion(BaseModel):
    """A human-scale serving description attached to a food item."""

    model_config = ConfigDict(extra="forbid")

    amount: float = Field(description="Numeric portion size.")
    unit: Literal["g", "ml", "oz", "piece"] = Field(
        description="Unit of the portion amount."
    )
    description: str = Field(description="Free-text label, e.g. '1 medium breast'.")


class NutritionPer100g(BaseModel):
    """Nutrition facts normalized to 100 g of the food, as provided by SNAQ."""

    model_config = ConfigDict(extra="forbid")

    calories_kcal: float = Field(ge=0, description="Energy in kcal per 100 g.")
    protein_g: float = Field(ge=0, description="Protein in grams per 100 g.")
    fat_g: float = Field(ge=0, description="Total fat in grams per 100 g.")
    saturated_fat_g: float | None = Field(
        default=None, ge=0, description="Saturated fat in grams per 100 g."
    )
    carbohydrates_g: float = Field(ge=0, description="Total carbs in grams per 100 g.")
    sugar_g: float | None = Field(
        default=None, ge=0, description="Sugars in grams per 100 g."
    )
    fiber_g: float | None = Field(
        default=None, ge=0, description="Dietary fiber in grams per 100 g."
    )
    sodium_mg: float | None = Field(
        default=None, ge=0, description="Sodium in milligrams per 100 g."
    )

    @model_validator(mode="after")
    def _saturated_not_exceeding_total_fat(self) -> NutritionPer100g:
        if self.saturated_fat_g is not None and self.saturated_fat_g > self.fat_g + 1e-6:
            raise ValueError("saturated_fat_g cannot exceed fat_g")
        return self


class FoodItem(BaseModel):
    """One row of the input file."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable identifier, e.g. 'chicken-breast-raw'.")
    name: str = Field(description="Human-readable name, e.g. 'Chicken Breast, Raw'.")
    brand: str | None = Field(default=None, description="Brand name if branded.")
    category: str = Field(description="Coarse category, e.g. 'Meat & Poultry'.")
    barcode: str | None = Field(default=None, description="EAN/UPC if known.")
    default_portion: Portion = Field(description="Typical serving.")
    nutrition_per_100g: NutritionPer100g = Field(
        description="Provided nutrition to be verified."
    )


# ---------------------------------------------------------------------------
# Reference / source models
# ---------------------------------------------------------------------------


SourceName = Literal["USDA", "OpenFoodFacts", "CIQUAL"]
USDADataType = Literal["Foundation", "SR Legacy", "Branded", "Survey (FNDDS)"]


class SourceCitation(BaseModel):
    """Points back at a specific record in an external database."""

    model_config = ConfigDict(extra="forbid")

    source: SourceName = Field(description="Which database the record came from.")
    source_id: str = Field(description="Stable ID within that source (fdcId, OFF code).")
    url: str | None = Field(default=None, description="Human-browsable URL, if any.")
    data_type: USDADataType | None = Field(
        default=None, description="USDA dataType; null for non-USDA sources."
    )
    retrieved_at: datetime = Field(description="When the record was fetched.")


class NutritionReference(BaseModel):
    """Authoritative nutrition per 100 g with a source citation."""

    model_config = ConfigDict(extra="forbid")

    nutrition: NutritionPer100g = Field(description="Reference nutrition per 100 g.")
    citation: SourceCitation = Field(description="Where this record came from.")
    match_name: str = Field(description="Name of the matched record in the source.")
    match_notes: str | None = Field(
        default=None,
        description=(
            "Free-text note on how confident the match is "
            "(e.g. 'exact', 'closest generic')."
        ),
    )


# ---------------------------------------------------------------------------
# Logic outputs
# ---------------------------------------------------------------------------


class MacroConsistencyResult(BaseModel):
    """Output of the 4/4/9 macro-consistency check."""

    model_config = ConfigDict(extra="forbid")

    stated_kcal: float = Field(description="kcal per 100 g as provided.")
    computed_kcal: float = Field(description="kcal per 100 g computed from macros.")
    delta_fraction: float = Field(
        description="(stated - computed) / max(computed, 1). Signed."
    )
    is_consistent: bool = Field(
        description="True if |delta_fraction| <= MACRO_CONSISTENCY_TOLERANCE."
    )


class FieldDiscrepancy(BaseModel):
    """One field's delta between provided and reference nutrition."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="e.g. 'calories_kcal', 'protein_g'.")
    provided: float | None = Field(description="Provided value; null if missing.")
    reference: float | None = Field(description="Reference value; null if missing.")
    delta_fraction: float | None = Field(
        description="(provided - reference) / reference; null if either side missing."
    )
    exceeds_tolerance: bool = Field(
        description="True when |delta_fraction| exceeds the field's tolerance."
    )


class DiscrepancyReport(BaseModel):
    """Per-field discrepancy results for one item."""

    model_config = ConfigDict(extra="forbid")

    fields: list[FieldDiscrepancy] = Field(description="One entry per compared field.")
    any_exceeds_tolerance: bool = Field(
        description="True if at least one field exceeds its tolerance."
    )


class VarianceInfo(BaseModel):
    """Known natural variance for a food (e.g. farmed vs wild salmon)."""

    model_config = ConfigDict(extra="forbid")

    match_key: str = Field(description="Catalogue key that matched, e.g. 'salmon-farmed'.")
    reason: str = Field(description="Short explanation of the variance source.")
    variable_fields: list[str] = Field(
        description="Fields expected to vary significantly, e.g. ['fat_g', 'calories_kcal']."
    )


class ReferenceCompletenessResult(BaseModel):
    """Output of :func:`logic.completeness.assess_reference_completeness`."""

    model_config = ConfigDict(extra="forbid")

    is_incomplete: bool = Field(
        description="True when the reference is missing core fields "
        "and confidence should be capped."
    )
    missing_fields: list[str] = Field(
        description="Names of fields that were None or zero."
    )
    reason: str | None = Field(
        default=None, description="Short human-readable reason if incomplete."
    )


# ---------------------------------------------------------------------------
# Verification result (agent output)
# ---------------------------------------------------------------------------


VerificationStatus = Literal[
    "VERIFIED", "DISCREPANCY", "HIGH_VARIANCE", "INCONCLUSIVE", "ERROR"
]


class ToolCall(BaseModel):
    """A single tool invocation recorded for the trace."""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(description="Tool name.")
    args: dict = Field(description="Serialized arguments.")
    result_summary: str = Field(description="Short summary of the tool's output.")
    latency_ms: float = Field(description="Wall-clock latency of the call.")


class VerificationResult(BaseModel):
    """Agent's structured verdict for one food item."""

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(description="Matches FoodItem.id.")
    status: VerificationStatus = Field(description="Overall verdict.")
    confidence: float = Field(
        ge=0.0, le=1.0, description="0.0-1.0 per the rubric in DESIGN.md section 4.5."
    )
    sources: list[SourceCitation] = Field(
        default_factory=list, description="Sources consulted."
    )
    macro_consistency: MacroConsistencyResult | None = Field(
        default=None, description="4/4/9 check on the provided values."
    )
    discrepancies: list[FieldDiscrepancy] = Field(
        default_factory=list, description="Per-field deltas vs reference."
    )
    proposed_correction: NutritionPer100g | None = Field(
        default=None,
        description="Only populated when status=DISCREPANCY and confidence is high.",
    )
    reasoning: str = Field(description="Short natural-language rationale.")
    error: str | None = Field(
        default=None, description="Populated only when status=ERROR."
    )


# ---------------------------------------------------------------------------
# Judge (eval) output
# ---------------------------------------------------------------------------


class JudgeVerdict(BaseModel):
    """One item's judgement from the LLM-as-judge eval layer."""

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(description="Matches VerificationResult.item_id.")
    grounded: bool = Field(
        description="True when the reasoning is supported by the listed sources."
    )
    concerns: list[str] = Field(
        default_factory=list,
        description="Specific issues: unsupported claims, missing citations, math errors.",
    )
    judge_confidence: float = Field(
        ge=0.0, le=1.0, description="How confident the judge is in its verdict."
    )
    summary: str = Field(description="One-sentence assessment.")

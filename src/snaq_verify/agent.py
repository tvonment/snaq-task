"""The pydantic-ai agent and its typed tools.

Design:
- ONE Agent instance, configured once at import.
- Output type is :class:`VerificationResult` -> the LLM returns structured data.
- Lookup tools (USDA, OFF, CIQUAL) are thin wrappers over the clients.
- Pure-logic tools (macro consistency, discrepancy, variance) call the
  modules under :mod:`snaq_verify.logic`.

The agent MUST NOT do arithmetic itself -- it must call the logic tools.
The system prompt enforces this explicitly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from openai import AsyncOpenAI
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from snaq_verify.clients.ciqual import CIQUALClient
from snaq_verify.clients.openfoodfacts import OpenFoodFactsClient
from snaq_verify.clients.usda import USDAClient
from snaq_verify.config import Settings
from snaq_verify.logic.completeness import assess_reference_completeness
from snaq_verify.logic.discrepancy import calculate_discrepancy
from snaq_verify.logic.semantics import SemanticsComparison, compare_semantics
from snaq_verify.logic.validation import validate_macro_consistency
from snaq_verify.logic.variance import check_known_variance
from snaq_verify.models import (
    DiscrepancyReport,
    MacroConsistencyResult,
    NutritionPer100g,
    NutritionReference,
    ReferenceCompletenessResult,
    SourceName,
    ToolCall,
    VarianceInfo,
    VerificationResult,
)


@dataclass
class Deps:
    """Per-run dependencies passed to every tool via ``RunContext``."""

    usda: USDAClient
    off: OpenFoodFactsClient
    ciqual: CIQUALClient
    trace: list[ToolCall] = field(default_factory=list)


# Bumped whenever the INSTRUCTIONS / SYSTEM_PROMPT below change in a
# way that could move grounded rate or status agreement. Surfaced in
# the report header and the stability matrix metadata so before/after
# matrices are comparable. Lightweight alternative to a prompt
# framework -- at two prompts the stamp is enough.
INSTRUCTIONS_VERSION = "v2"

SYSTEM_PROMPT = (
    "Verify one food item's nutrition profile against authoritative sources "
    "by calling tools. Return a structured VerificationResult. Use the tools "
    "for all arithmetic."
)

INSTRUCTIONS = """\
Tool routing and call order:
- Barcode present: call lookup_off_by_barcode first; if no match, fall
  back to lookup_usda_by_name with data_type="Branded".
- No barcode (generic food): call lookup_usda_by_name with
  data_type="Foundation" first, then "SR Legacy" if Foundation returns
  nothing. Also call lookup_ciqual_by_name for a second authoritative
  reference. Avoid the "Branded" dataset for generic foods.
- Always call validate_macro_consistency_tool on the provided nutrition.
- For every reference you use, call assess_reference_completeness_tool.
- When you will compare references from two different sources (USDA +
  CIQUAL, USDA + OFF, OFF + CIQUAL), call compare_semantics_tool
  BEFORE calculate_discrepancy_tool. The notes returned tell you which
  field deltas between those two sources are definitional, not real.
- Then call calculate_discrepancy_tool for each reference vs. provided.
- Always call check_known_variance_tool before finalizing the status.

Definitional fields are not discrepancies:
- compare_semantics_tool returns notes with `affected_fields` (e.g.
  carbohydrates_g, calories_kcal, sodium_mg, protein_g) and a `kind`
  (carbs_definition, energy_definition, sodium_vs_salt,
  protein_conversion).
- For any field listed in `affected_fields` of a returned note, an
  `exceeds_tolerance=true` result from calculate_discrepancy_tool is
  EXPECTED and does NOT count toward a DISCREPANCY verdict between
  those two sources. Treat the field as agreement-by-definition.
- Only deltas on non-definitional fields (or on a field within a
  single source) can drive a DISCREPANCY status.

HIGH_VARIANCE is mandatory when the catalogue matches:
- If check_known_variance_tool returns a VarianceInfo and every field
  with `exceeds_tolerance=true` (after the definitional-field rule
  above) is listed in `variable_fields`, the status MUST be
  HIGH_VARIANCE. Not DISCREPANCY. No exceptions, even when the deltas
  are large.
- If at least one non-variable field also exceeds tolerance,
  DISCREPANCY is still appropriate.

Do not do arithmetic yourself. If a reference looks incomplete
(assess_reference_completeness_tool says so), treat it as incomplete
rather than back-filling values.

Confidence scoring:
- 1.0 when two independent sources (USDA and CIQUAL, or USDA and OFF)
  agree within tolerance on every non-definitional field.
- 0.8 when two independent sources are consulted but disagree on at
  least one non-definitional field beyond tolerance. Cap at 0.8 in
  this case -- two-source consultation alone is not 1.0.
- 0.8 for a single USDA Foundation / SR Legacy or CIQUAL match on a
  complete reference (no second source available).
- 0.6 for a single branded source with complete macros, OR any
  single-source match whose reference is flagged incomplete by
  assess_reference_completeness_tool. Cap confidence at 0.6 in that
  case regardless of the source type.
- 0.4 for partial data or a high-variance catalogue hit.
- 0.0 when no usable source is found.

Propose a correction only when status is DISCREPANCY and confidence is
at least 0.8. Never invent a value -- copy the reference's numbers
verbatim, leaving fields null when the reference has no data. Every
value in proposed_correction must come directly from one of the
NutritionReference objects returned by a lookup tool in this run; do
not average, interpolate, or back-compute values across sources.

Reasoning output (VerificationReasoning):
- routing_decision: one of "barcode_off", "generic_usda",
  "generic_ciqual", "known_variance", "manual_review".
- source_choice_rationale: one or two sentences explaining WHY the
  chosen source is appropriate. Qualitative only.
- variance_notes: optional short note when HIGH_VARIANCE applies.
- correction_rationale: optional short note when proposing a correction.

The narrative fields are qualitative by construction. Do not write any
digits in them (including percentages, counts, kcal, grams, or record
IDs). Numbers live in the structured tool outputs above -- sources,
discrepancies, macro_consistency. The report layer composes the human
sentence from those. If you paraphrase a number into the reasoning,
the schema will reject the response.
"""


def build_agent(
    settings: Settings,
    *,
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None,
) -> Agent[Deps, VerificationResult]:
    """Build the pydantic-ai agent bound to Azure AI Foundry.

    Foundry's v1 API uses a plain OpenAI-compatible base URL ending in
    ``/openai/v1/``. We therefore use a vanilla ``AsyncOpenAI`` client
    rather than ``AsyncAzureOpenAI`` -- the latter always appends the
    ``api-version`` query parameter, which the v1 path rejects.

    ``reasoning_effort`` is forwarded to OpenAI-family reasoning
    models (gpt-5, o-series). Lower effort is cheaper and faster;
    higher effort explores more reasoning paths. We expose it as a
    knob rather than hardcoding it so the stability matrix can compare
    effort levels.
    """
    base_url = settings.azure_endpoint.rstrip("/") + "/"
    openai_client = AsyncOpenAI(
        base_url=base_url,
        api_key=settings.azure_api_key,
    )
    model = OpenAIChatModel(
        settings.azure_deployment,
        provider=OpenAIProvider(openai_client=openai_client),
    )
    # We deliberately do NOT set temperature: gpt-5-style reasoning
    # deployments reject sampling params, and we want any residual
    # non-determinism to surface in the stability matrix rather than
    # being silently muted (or loudly warned about) by the SDK.
    model_settings: OpenAIChatModelSettings | None = None
    if reasoning_effort is not None:
        model_settings = OpenAIChatModelSettings(
            openai_reasoning_effort=reasoning_effort,
        )
    agent: Agent[Deps, VerificationResult] = Agent(
        model=model,
        deps_type=Deps,
        output_type=VerificationResult,
        system_prompt=SYSTEM_PROMPT,
        model_settings=model_settings,
    )
    _register_tools(agent)
    return agent


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _trace(
    deps: Deps,
    tool: str,
    args: dict,
    result_summary: str,
    t0: float,
    result_payload: dict | None = None,
) -> None:
    deps.trace.append(
        ToolCall(
            tool=tool,
            args=args,
            result_summary=result_summary,
            result_payload=result_payload,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )
    )


def _register_tools(agent: Agent[Deps, VerificationResult]) -> None:
    """Attach typed tools to the agent."""

    @agent.tool
    async def lookup_usda_by_name(
        ctx: RunContext[Deps],
        name: str,
        category: str,
        data_type: Literal["Foundation", "SR Legacy", "Branded"] = "Foundation",
    ) -> NutritionReference | None:
        """Search USDA FoodData Central by name. Prefer Foundation/SR Legacy for generic foods.

        ``category`` is accepted so the tool signature is informative to the
        LLM, but USDA's search API doesn't use it server-side.
        """
        t0 = time.perf_counter()
        result = await ctx.deps.usda.search(name, data_type=data_type)
        _trace(
            ctx.deps,
            "lookup_usda_by_name",
            {"name": name, "category": category, "data_type": data_type},
            f"match={result.match_name!r}" if result else "no match",
            t0,
            result_payload=result.model_dump(mode="json") if result else None,
        )
        return result

    @agent.tool
    async def lookup_off_by_barcode(
        ctx: RunContext[Deps], barcode: str
    ) -> NutritionReference | None:
        """Fetch a product from Open Food Facts by barcode (EAN/UPC)."""
        t0 = time.perf_counter()
        result = await ctx.deps.off.lookup_by_barcode(barcode)
        _trace(
            ctx.deps,
            "lookup_off_by_barcode",
            {"barcode": barcode},
            f"match={result.match_name!r}" if result else "no match",
            t0,
            result_payload=result.model_dump(mode="json") if result else None,
        )
        return result

    @agent.tool
    def validate_macro_consistency_tool(
        ctx: RunContext[Deps], nutrition: NutritionPer100g
    ) -> MacroConsistencyResult:
        """Check that calories ≈ 4*protein + 4*carbs + 9*fat within ±10 %."""
        t0 = time.perf_counter()
        result = validate_macro_consistency(nutrition)
        _trace(
            ctx.deps,
            "validate_macro_consistency",
            {"nutrition": nutrition.model_dump()},
            f"consistent={result.is_consistent} delta={result.delta_fraction:+.2%}",
            t0,
        )
        return result

    @agent.tool
    def calculate_discrepancy_tool(
        ctx: RunContext[Deps],
        provided: NutritionPer100g,
        reference: NutritionPer100g,
    ) -> DiscrepancyReport:
        """Per-field delta report between provided and reference nutrition."""
        t0 = time.perf_counter()
        result = calculate_discrepancy(provided, reference)
        _trace(
            ctx.deps,
            "calculate_discrepancy",
            {"provided": provided.model_dump(), "reference": reference.model_dump()},
            f"any_exceeds={result.any_exceeds_tolerance}",
            t0,
        )
        return result

    @agent.tool
    def check_known_variance_tool(
        ctx: RunContext[Deps], name: str, category: str
    ) -> VarianceInfo | None:
        """Look up the food in the known natural-variance catalogue."""
        t0 = time.perf_counter()
        result = check_known_variance(name, category)
        _trace(
            ctx.deps,
            "check_known_variance",
            {"name": name, "category": category},
            f"match={result.match_key!r}" if result else "no match",
            t0,
        )
        return result

    @agent.tool
    def assess_reference_completeness_tool(
        ctx: RunContext[Deps], reference: NutritionPer100g
    ) -> ReferenceCompletenessResult:
        """Report whether a reference record is too incomplete to trust fully."""
        t0 = time.perf_counter()
        result = assess_reference_completeness(reference)
        _trace(
            ctx.deps,
            "assess_reference_completeness",
            {"reference": reference.model_dump()},
            f"incomplete={result.is_incomplete} missing={result.missing_fields}",
            t0,
        )
        return result

    @agent.tool
    def lookup_ciqual_by_name(
        ctx: RunContext[Deps], name: str, category: str
    ) -> NutritionReference | None:
        """Look up nutrition data in the bundled CIQUAL (ANSES) subset.

        CIQUAL is the French government food composition table. The agent
        should call this alongside USDA for generic foods; two-source
        agreement is the only path to confidence 1.0.
        """
        t0 = time.perf_counter()
        result = ctx.deps.ciqual.search(name)
        _trace(
            ctx.deps,
            "lookup_ciqual_by_name",
            {"name": name, "category": category},
            f"match={result.match_name!r}" if result else "no match",
            t0,
            result_payload=result.model_dump(mode="json") if result else None,
        )
        return result

    @agent.tool
    def compare_semantics_tool(
        ctx: RunContext[Deps], source_a: SourceName, source_b: SourceName
    ) -> SemanticsComparison:
        """Known definitional quirks between two sources.

        Call this whenever you compare references from different sources
        (e.g. USDA + CIQUAL). The notes explain where small deltas are
        definitional (carbs-by-difference vs available carbs, Atwater
        energy factors, salt vs sodium) rather than real disagreement,
        which should inform the source_choice_rationale and prevent
        DISCREPANCY calls on fields that are known to differ by construction.
        """
        t0 = time.perf_counter()
        result = compare_semantics(source_a, source_b)
        _trace(
            ctx.deps,
            "compare_semantics",
            {"source_a": source_a, "source_b": source_b},
            f"{len(result.notes)} note(s): {[n.kind for n in result.notes]}",
            t0,
        )
        return result

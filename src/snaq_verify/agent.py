"""The pydantic-ai agent and its typed tools.

Design:
- ONE Agent instance, configured once at import.
- Output type is :class:`VerificationResult` -> the LLM returns structured data.
- Lookup tools (USDA, OFF) are async and wrap the clients + cache.
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
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from snaq_verify.cache import _MISS, ResponseCache
from snaq_verify.clients.openfoodfacts import OpenFoodFactsClient
from snaq_verify.clients.usda import USDAClient
from snaq_verify.config import Settings
from snaq_verify.logic.discrepancy import calculate_discrepancy
from snaq_verify.logic.validation import validate_macro_consistency
from snaq_verify.logic.variance import check_known_variance
from snaq_verify.models import (
    DiscrepancyReport,
    MacroConsistencyResult,
    NutritionPer100g,
    NutritionReference,
    ToolCall,
    VarianceInfo,
    VerificationResult,
)


@dataclass
class Deps:
    """Per-run dependencies passed to every tool via ``RunContext``."""

    usda: USDAClient
    off: OpenFoodFactsClient
    cache: ResponseCache | None
    trace: list[ToolCall] = field(default_factory=list)


SYSTEM_PROMPT = (
    "Verify one food item's nutrition profile against authoritative sources "
    "by calling tools. Return a structured VerificationResult. Use the tools "
    "for all arithmetic."
)

INSTRUCTIONS = """\
Tool routing:
- Barcode present: call lookup_off_by_barcode first; if no match, fall
  back to lookup_usda_by_name with data_type="Branded".
- No barcode (generic food): call lookup_usda_by_name with
  data_type="Foundation" first, then "SR Legacy" if Foundation returns
  nothing. Avoid the "Branded" dataset for generic foods.
- Always call check_known_variance_tool before finalizing the status.
  When the item matches a variance rule and the only discrepancies are
  on its variable_fields, set status to HIGH_VARIANCE instead of
  DISCREPANCY.
- Always call validate_macro_consistency_tool on the provided nutrition.
- When a reference is available, call calculate_discrepancy_tool to
  compare provided and reference.

Confidence scoring:
- 1.0 when two sources agree within tolerance.
- 0.8 for a single USDA Foundation or SR Legacy match.
- 0.6 for a single branded source with complete macros.
- 0.4 for partial data or a high-variance catalogue hit.
- 0.0 when no usable source is found.

Propose a correction only when status is DISCREPANCY and confidence is
at least 0.8. Keep the reasoning field to one to three short sentences.
"""


def build_agent(settings: Settings) -> Agent[Deps, VerificationResult]:
    """Build the pydantic-ai agent bound to Azure AI Foundry.

    Foundry's v1 API uses a plain OpenAI-compatible base URL ending in
    ``/openai/v1/``. We therefore use a vanilla ``AsyncOpenAI`` client
    rather than ``AsyncAzureOpenAI`` -- the latter always appends the
    ``api-version`` query parameter, which the v1 path rejects.
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
    agent: Agent[Deps, VerificationResult] = Agent(
        model=model,
        deps_type=Deps,
        output_type=VerificationResult,
        system_prompt=SYSTEM_PROMPT,
    )
    _register_tools(agent)
    return agent


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _trace(deps: Deps, tool: str, args: dict, result_summary: str, t0: float) -> None:
    deps.trace.append(
        ToolCall(
            tool=tool,
            args=args,
            result_summary=result_summary,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )
    )


async def _cached_usda_search(
    deps: Deps, query: str, data_type: str
) -> NutritionReference | None:
    cache_key = f"search:{data_type}:{query.lower()}"
    if deps.cache is not None:
        cached = deps.cache.get("USDA", cache_key)
        if cached is not _MISS:
            return cached  # type: ignore[return-value]
    result = await deps.usda.search(query, data_type=data_type)  # type: ignore[arg-type]
    if deps.cache is not None:
        deps.cache.set("USDA", cache_key, result)
    return result


async def _cached_off_barcode(deps: Deps, barcode: str) -> NutritionReference | None:
    cache_key = f"barcode:{barcode}"
    if deps.cache is not None:
        cached = deps.cache.get("OpenFoodFacts", cache_key)
        if cached is not _MISS:
            return cached  # type: ignore[return-value]
    result = await deps.off.lookup_by_barcode(barcode)
    if deps.cache is not None:
        deps.cache.set("OpenFoodFacts", cache_key, result)
    return result


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
        result = await _cached_usda_search(ctx.deps, name, data_type)
        _trace(
            ctx.deps,
            "lookup_usda_by_name",
            {"name": name, "category": category, "data_type": data_type},
            f"match={result.match_name!r}" if result else "no match",
            t0,
        )
        return result

    @agent.tool
    async def lookup_off_by_barcode(
        ctx: RunContext[Deps], barcode: str
    ) -> NutritionReference | None:
        """Fetch a product from Open Food Facts by barcode (EAN/UPC)."""
        t0 = time.perf_counter()
        result = await _cached_off_barcode(ctx.deps, barcode)
        _trace(
            ctx.deps,
            "lookup_off_by_barcode",
            {"barcode": barcode},
            f"match={result.match_name!r}" if result else "no match",
            t0,
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

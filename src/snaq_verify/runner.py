"""Orchestration: load input, fan out to the agent, render reports."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from pydantic import TypeAdapter

from snaq_verify.agent import INSTRUCTIONS, Deps, build_agent
from snaq_verify.cache import ResponseCache
from snaq_verify.clients.openfoodfacts import OpenFoodFactsClient
from snaq_verify.clients.usda import USDAClient
from snaq_verify.config import Settings
from snaq_verify.models import FoodItem, ToolCall, VerificationResult
from snaq_verify.report import write_reports

_LOG = logging.getLogger("snaq_verify")

_FOOD_ITEMS_ADAPTER = TypeAdapter(list[FoodItem])


async def run_verification(
    *,
    input_file: Path,
    out_dir: Path,
    formats: tuple[str, ...],
    apply_corrections: bool,
    min_confidence: float,
    use_cache: bool,
    concurrency_override: int | None,
) -> None:
    """Top-level entry point invoked from the CLI."""
    settings = Settings.load()
    logging.basicConfig(level=settings.log_level.upper())

    items = _FOOD_ITEMS_ADAPTER.validate_json(input_file.read_text())
    _LOG.info("Loaded %d items from %s", len(items), input_file)

    concurrency = concurrency_override or settings.max_concurrent
    cache = ResponseCache(settings.cache_path) if use_cache else None
    agent = build_agent(settings)

    async with (
        USDAClient(settings.usda_api_key) as usda,
        OpenFoodFactsClient() as off,
    ):
        sem = asyncio.Semaphore(concurrency)

        async def verify_one(item: FoodItem) -> tuple[VerificationResult, list[ToolCall]]:
            async with sem:
                deps = Deps(usda=usda, off=off, cache=cache)
                prompt = _format_prompt(item)
                try:
                    result = await agent.run(prompt, deps=deps)
                    return result.output, deps.trace
                except Exception as exc:
                    # Surfaced as ERROR status rather than failing the batch.
                    _LOG.exception("Agent failed for item %s", item.id)
                    return (
                        VerificationResult(
                            item_id=item.id,
                            status="ERROR",
                            confidence=0.0,
                            reasoning="Agent raised an exception; see error field.",
                            error=f"{type(exc).__name__}: {exc}",
                        ),
                        deps.trace,
                    )

        pairs = await asyncio.gather(*(verify_one(i) for i in items))

    if cache is not None:
        cache.close()

    results = [pair[0] for pair in pairs]
    traces = {pair[0].item_id: pair[1] for pair in pairs}

    out_dir.mkdir(parents=True, exist_ok=True)
    write_reports(
        items=items,
        results=results,
        traces=traces,
        out_dir=out_dir,
        formats=formats,
        model_deployment=settings.azure_deployment,
    )

    if apply_corrections:
        _write_corrected(items, results, min_confidence, out_dir)

    _summarise(results)


def _format_prompt(item: FoodItem) -> str:
    """Serialize a single item into the user message for the agent."""
    return (
        f"{INSTRUCTIONS}\n\n"
        f"ITEM:\n{item.model_dump_json(indent=2)}"
    )


def _write_corrected(
    items: list[FoodItem],
    results: list[VerificationResult],
    min_confidence: float,
    out_dir: Path,
) -> None:
    """Emit ``food_items.corrected.json`` with high-confidence corrections merged in."""
    by_id = {r.item_id: r for r in results}
    corrected: list[dict] = []
    applied = 0
    for item in items:
        data = item.model_dump()
        res = by_id.get(item.id)
        if (
            res is not None
            and res.status == "DISCREPANCY"
            and res.proposed_correction is not None
            and res.confidence >= min_confidence
        ):
            data["nutrition_per_100g"] = res.proposed_correction.model_dump()
            applied += 1
        corrected.append(data)
    path = out_dir / "food_items.corrected.json"
    path.write_text(json.dumps(corrected, indent=2))
    _LOG.info("Wrote %s with %d correction(s) applied", path, applied)


def _summarise(results: list[VerificationResult]) -> None:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    _LOG.info("Summary: %s", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

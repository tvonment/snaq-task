"""Orchestration: load input, fan out to the agent, render reports."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from pydantic import TypeAdapter

from snaq_verify.agent import INSTRUCTIONS, Deps, build_agent
from snaq_verify.clients.ciqual import CIQUALClient
from snaq_verify.clients.openfoodfacts import OpenFoodFactsClient
from snaq_verify.clients.usda import USDAClient
from snaq_verify.config import Settings
from snaq_verify.models import FoodItem, ToolCall, VerificationReasoning, VerificationResult
from snaq_verify.report import write_reports

_LOG = logging.getLogger("snaq_verify")

_FOOD_ITEMS_ADAPTER = TypeAdapter(list[FoodItem])

# Third-party loggers that otherwise flood INFO with one line per HTTP call.
_NOISY_LOGGERS: tuple[str, ...] = (
    "httpx",
    "httpcore",
    "openai",
    "pydantic_ai",
)


def _configure_logging(level: str, verbose: int = 0) -> None:
    """Set up a concise console format and quiet third-party INFO spam.

    ``verbose`` bumps visibility:
    - 0: default -- snaq_verify at ``level``, third-party at WARNING.
    - 1 (``-v``): snaq_verify at DEBUG, third-party still at WARNING.
    - 2+ (``-vv``): snaq_verify at DEBUG and re-enable third-party INFO
      (raw httpx / openai / pydantic-ai firehose).
    """
    root = logging.getLogger()
    root_level = "DEBUG" if verbose >= 1 else level.upper()
    root.setLevel(root_level)
    # Avoid stacking handlers when run_verification is called multiple times.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
    root.addHandler(handler)
    third_party_level = logging.INFO if verbose >= 2 else logging.WARNING
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(third_party_level)


async def run_verification(
    *,
    input_file: Path,
    out_dir: Path,
    formats: tuple[str, ...],
    apply_corrections: bool,
    min_confidence: float,
    concurrency_override: int | None,
    verbose: int = 0,
) -> None:
    """Top-level entry point invoked from the CLI."""
    settings = Settings.load()
    _configure_logging(settings.log_level, verbose=verbose)

    items = _FOOD_ITEMS_ADAPTER.validate_json(input_file.read_text())
    concurrency = concurrency_override or settings.max_concurrent
    _LOG.info(
        "Verifying %d items  concurrency=%d",
        len(items),
        concurrency,
    )

    agent = build_agent(settings)

    async with (
        USDAClient(settings.usda_api_key) as usda,
        OpenFoodFactsClient() as off,
    ):
        ciqual = CIQUALClient()
        sem = asyncio.Semaphore(concurrency)
        total = len(items)
        completed = 0
        lock = asyncio.Lock()

        async def verify_one(item: FoodItem) -> tuple[VerificationResult, list[ToolCall]]:
            nonlocal completed
            async with sem:
                deps = Deps(usda=usda, off=off, ciqual=ciqual)
                prompt = _format_prompt(item)
                t0 = time.perf_counter()
                try:
                    run_result = await agent.run(prompt, deps=deps)
                    result = run_result.output
                except Exception as exc:
                    _LOG.exception("Agent failed for item %s", item.id)
                    result = VerificationResult(
                        item_id=item.id,
                        status="ERROR",
                        confidence=0.0,
                        reasoning=VerificationReasoning(
                            routing_decision="manual_review",
                            source_choice_rationale=(
                                "Agent raised an exception before a source "
                                "could be chosen; see the error field."
                            ),
                        ),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                elapsed = time.perf_counter() - t0
                async with lock:
                    completed += 1
                    _log_item_progress(
                        completed=completed,
                        total=total,
                        item=item,
                        result=result,
                        tool_calls=len(deps.trace),
                        elapsed_s=elapsed,
                    )
                return result, deps.trace

        pairs = await asyncio.gather(*(verify_one(i) for i in items))

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

    _summarise(results, out_dir)


def _format_prompt(item: FoodItem) -> str:
    """Serialize a single item into the user message for the agent."""
    return (
        f"{INSTRUCTIONS}\n\n"
        f"ITEM:\n{item.model_dump_json(indent=2)}"
    )


def _log_item_progress(
    *,
    completed: int,
    total: int,
    item: FoodItem,
    result: VerificationResult,
    tool_calls: int,
    elapsed_s: float,
) -> None:
    """Emit exactly one concise line per finished item."""
    name = item.name if len(item.name) <= 40 else item.name[:37] + "..."
    _LOG.info(
        "[%d/%d] %-13s %-40s conf=%.2f  tools=%d  %.1fs",
        completed,
        total,
        result.status,
        name,
        result.confidence,
        tool_calls,
        elapsed_s,
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


def _summarise(results: list[VerificationResult], out_dir: Path) -> None:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    summary = "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    _LOG.info("Done. %s  -> %s", summary, out_dir)

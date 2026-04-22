"""Judge-verifier agreement metric (plan v2 M5).

One number to track across runs: the **grounded success rate** -- the
fraction of items where the verifier's status matches the hand-labelled
golden expectation AND the judge flags the reasoning as grounded.

Computed by reading ``outputs/report.json`` and ``outputs/judge.json``
and (optionally) applying :mod:`eval.golden`'s rules. Written as
``outputs/metrics.json`` by the ``snaq-verify judge`` command so every
run leaves a diffable snapshot.

A single LLM-as-judge run has real sampling noise at n=11, so this file
is the mechanism by which we tell "M3/M4 actually helped" from "we got
lucky on one rerun".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from eval.golden import _EXPECTATIONS


class ItemMetric(BaseModel):
    """Per-item agreement breakdown."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    status: str = Field(description="Verifier-reported status.")
    confidence: float
    golden_pass: bool | None = Field(
        description="True if status is in the allowed set for this item "
        "and confidence meets the minimum. None when no golden rule "
        "covers the item."
    )
    judge_grounded: bool | None = Field(
        description="True if the judge flagged the reasoning as grounded. "
        "None when the judge produced no verdict for this item."
    )
    grounded_success: bool = Field(
        description="True only when golden_pass AND judge_grounded are both "
        "True. This is the per-item version of the headline metric."
    )


class RunMetrics(BaseModel):
    """Aggregate metrics for one verify+judge run."""

    model_config = ConfigDict(extra="forbid")

    generated_at: str
    report_path: str
    judge_path: str
    n_items: int
    verifier_status_counts: dict[str, int]
    judge_grounded_count: int
    judge_grounded_rate: float
    golden_pass_count: int
    golden_covered: int = Field(
        description="Number of items for which a golden rule exists."
    )
    grounded_success_count: int
    grounded_success_rate: float = Field(
        description="Headline metric: grounded_success_count / golden_covered. "
        "0.0 when no items are covered by the golden set."
    )
    items: list[ItemMetric]


def _evaluate_golden(item_id: str, status: str, confidence: float) -> bool | None:
    expect = _EXPECTATIONS.get(item_id)
    if expect is None:
        return None
    if status not in expect["allowed"]:
        return False
    return confidence >= expect["min_confidence"] - 1e-9


def compute_metrics(report_path: Path, judge_path: Path) -> RunMetrics:
    """Compute aggregate metrics from a report + judge pair."""
    report = json.loads(report_path.read_text())
    judge = json.loads(judge_path.read_text())

    verdict_by_id: dict[str, dict[str, Any]] = {
        v["item_id"]: v for v in judge.get("verdicts", [])
    }
    status_counts: dict[str, int] = {}
    items: list[ItemMetric] = []
    golden_pass_count = 0
    golden_covered = 0
    judge_grounded_count = 0
    grounded_success_count = 0

    for row in report.get("items", []):
        result = row["result"]
        item_id = result["item_id"]
        status = result["status"]
        confidence = float(result["confidence"])
        status_counts[status] = status_counts.get(status, 0) + 1

        golden = _evaluate_golden(item_id, status, confidence)
        if golden is not None:
            golden_covered += 1
            if golden:
                golden_pass_count += 1

        verdict = verdict_by_id.get(item_id)
        judge_grounded: bool | None = None
        if verdict is not None:
            judge_grounded = bool(verdict.get("grounded", False))
            if judge_grounded:
                judge_grounded_count += 1

        success = bool(golden) and bool(judge_grounded)
        if success:
            grounded_success_count += 1

        items.append(
            ItemMetric(
                item_id=item_id,
                status=status,
                confidence=confidence,
                golden_pass=golden,
                judge_grounded=judge_grounded,
                grounded_success=success,
            )
        )

    n = len(items)
    return RunMetrics(
        generated_at=datetime.now(UTC).isoformat(),
        report_path=str(report_path),
        judge_path=str(judge_path),
        n_items=n,
        verifier_status_counts=status_counts,
        judge_grounded_count=judge_grounded_count,
        judge_grounded_rate=(judge_grounded_count / n) if n else 0.0,
        golden_pass_count=golden_pass_count,
        golden_covered=golden_covered,
        grounded_success_count=grounded_success_count,
        grounded_success_rate=(
            grounded_success_count / golden_covered if golden_covered else 0.0
        ),
        items=items,
    )


def write_metrics(metrics: RunMetrics, out_path: Path) -> None:
    """Write ``metrics`` as pretty-printed JSON to ``out_path``."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics.model_dump(), indent=2))

"""Tests for the eval.golden checker. The judge itself hits an LLM, so it
isn't exercised here; but we do assert its Pydantic model is shaped
correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

from eval.golden import check
from snaq_verify.models import JudgeVerdict


def _write_report(tmp_path: Path, items: list[dict]) -> Path:
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"items": items}))
    return path


def _row(item_id: str, status: str, confidence: float) -> dict:
    return {
        "item": {"id": item_id, "name": item_id},
        "result": {
            "item_id": item_id,
            "status": status,
            "confidence": confidence,
        },
        "trace": [],
    }


def test_golden_passes_on_expected_statuses(tmp_path: Path) -> None:
    report = _write_report(
        tmp_path,
        [
            _row("chicken-breast-raw", "DISCREPANCY", 0.8),
            _row("salmon-atlantic-farmed-raw", "HIGH_VARIANCE", 0.4),
            _row("white-bread", "VERIFIED", 0.8),
        ],
    )
    failures = check(report)
    # We only seeded 3 items, so other expected items count as missing.
    assert all("salmon-atlantic-farmed-raw" not in f for f in failures)
    assert all("chicken-breast-raw" not in f for f in failures)


def test_golden_flags_salmon_if_not_high_variance(tmp_path: Path) -> None:
    report = _write_report(
        tmp_path,
        [_row("salmon-atlantic-farmed-raw", "DISCREPANCY", 0.8)],
    )
    failures = check(report)
    assert any("salmon-atlantic-farmed-raw" in f for f in failures)


def test_golden_flags_low_confidence(tmp_path: Path) -> None:
    report = _write_report(
        tmp_path,
        [_row("chicken-breast-raw", "DISCREPANCY", 0.2)],
    )
    failures = check(report)
    assert any("confidence" in f for f in failures)


def test_judge_verdict_model_accepts_minimal_payload() -> None:
    v = JudgeVerdict(
        item_id="x",
        grounded=True,
        judge_confidence=0.9,
        summary="ok",
    )
    assert v.concerns == []

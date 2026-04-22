"""Tests for the M5 agreement metric."""

from __future__ import annotations

import json
from pathlib import Path

from eval.metrics import compute_metrics


def _write_report(path: Path, items: list[dict]) -> None:
    path.write_text(json.dumps({"items": items}))


def _write_judge(path: Path, verdicts: list[dict]) -> None:
    path.write_text(json.dumps({"verdicts": verdicts}))


def _row(item_id: str, status: str, confidence: float) -> dict:
    return {
        "item": {"id": item_id},
        "result": {"item_id": item_id, "status": status, "confidence": confidence},
        "trace": [],
    }


def _verdict(item_id: str, grounded: bool) -> dict:
    return {
        "item_id": item_id,
        "grounded": grounded,
        "concerns": [],
        "judge_confidence": 0.9,
        "summary": "stub",
    }


def test_grounded_success_requires_both_golden_pass_and_grounded(tmp_path: Path) -> None:
    # chicken is a golden item; DISCREPANCY + 0.8 passes; judge groundedTrue
    # -> grounded_success True.
    report = tmp_path / "report.json"
    judge = tmp_path / "judge.json"
    _write_report(report, [_row("chicken-breast-raw", "DISCREPANCY", 0.8)])
    _write_judge(judge, [_verdict("chicken-breast-raw", True)])

    m = compute_metrics(report, judge)
    assert m.n_items == 1
    assert m.golden_covered == 1
    assert m.golden_pass_count == 1
    assert m.judge_grounded_count == 1
    assert m.grounded_success_count == 1
    assert m.grounded_success_rate == 1.0


def test_ungrounded_verdict_fails_even_if_golden_passes(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    judge = tmp_path / "judge.json"
    _write_report(report, [_row("banana-raw", "DISCREPANCY", 0.8)])
    _write_judge(judge, [_verdict("banana-raw", False)])

    m = compute_metrics(report, judge)
    assert m.golden_pass_count == 1
    assert m.judge_grounded_count == 0
    assert m.grounded_success_count == 0
    assert m.grounded_success_rate == 0.0


def test_golden_mismatch_fails_even_if_judge_grounded(tmp_path: Path) -> None:
    # farmed salmon must be HIGH_VARIANCE per golden; DISCREPANCY fails.
    report = tmp_path / "report.json"
    judge = tmp_path / "judge.json"
    _write_report(report, [_row("salmon-atlantic-farmed-raw", "DISCREPANCY", 0.8)])
    _write_judge(judge, [_verdict("salmon-atlantic-farmed-raw", True)])

    m = compute_metrics(report, judge)
    assert m.golden_pass_count == 0
    assert m.grounded_success_count == 0


def test_items_outside_golden_set_do_not_count_against_rate(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    judge = tmp_path / "judge.json"
    _write_report(report, [_row("not-in-golden", "VERIFIED", 0.9)])
    _write_judge(judge, [_verdict("not-in-golden", True)])

    m = compute_metrics(report, judge)
    assert m.golden_covered == 0
    assert m.grounded_success_rate == 0.0
    # Judge grounded rate is still reported.
    assert m.judge_grounded_rate == 1.0
    assert m.items[0].golden_pass is None


def test_missing_judge_verdict_yields_none_and_fails_success(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    judge = tmp_path / "judge.json"
    _write_report(report, [_row("chicken-breast-raw", "DISCREPANCY", 0.8)])
    _write_judge(judge, [])

    m = compute_metrics(report, judge)
    assert m.items[0].judge_grounded is None
    assert m.grounded_success_count == 0


def test_status_counts_are_aggregated(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    judge = tmp_path / "judge.json"
    _write_report(
        report,
        [
            _row("chicken-breast-raw", "DISCREPANCY", 0.8),
            _row("banana-raw", "DISCREPANCY", 0.8),
            _row("whole-milk", "VERIFIED", 0.8),
            _row("salmon-atlantic-farmed-raw", "HIGH_VARIANCE", 0.4),
        ],
    )
    _write_judge(
        judge,
        [
            _verdict("chicken-breast-raw", True),
            _verdict("banana-raw", True),
            _verdict("whole-milk", True),
            _verdict("salmon-atlantic-farmed-raw", False),
        ],
    )

    m = compute_metrics(report, judge)
    assert m.verifier_status_counts == {
        "DISCREPANCY": 2,
        "VERIFIED": 1,
        "HIGH_VARIANCE": 1,
    }
    assert m.judge_grounded_count == 3
    assert m.grounded_success_count == 3  # salmon judge ungrounded cancels it

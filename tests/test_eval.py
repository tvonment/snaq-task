"""Tests for the eval.golden checker. The judge itself hits an LLM, so it
isn't exercised here; but we do assert its Pydantic model is shaped
correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

from eval.golden import check
from snaq_verify.models import JudgeConcern, JudgeVerdict


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


def test_render_judge_markdown_contains_header_table_and_concerns() -> None:
    from eval.judge import render_judge_markdown

    verdicts = [
        JudgeVerdict(
            item_id="chicken-breast-raw",
            grounded=True,
            judge_confidence=0.9,
            summary="Supported by USDA and CIQUAL.",
        ),
        JudgeVerdict(
            item_id="banana-raw",
            grounded=False,
            concerns=[
                JudgeConcern(kind="paraphrase", detail="delta_fraction restated incorrectly"),
                JudgeConcern(kind="missing_citation", detail="missing citation for fiber"),
            ],
            judge_confidence=0.4,
            summary="Arithmetic paraphrased. A follow-up is needed.",
        ),
    ]
    md = render_judge_markdown(verdicts, report_path=Path("outputs/report.json"))

    # Header + summary stats
    assert md.startswith("# Judge Report")
    assert "Grounded:** 1/2" in md
    assert "Avg judge confidence:** 0.65" in md

    # Summary table rows
    assert "| `chicken-breast-raw` |" in md
    assert "| `banana-raw` |" in md

    # Ungrounded item has its own details section with concerns
    assert "### `banana-raw`" in md
    assert "delta_fraction restated incorrectly" in md
    assert "missing citation for fiber" in md

    # Grounded item does NOT appear in the Concerns section
    concerns_section = md.split("## Concerns", 1)[1] if "## Concerns" in md else ""
    assert "### `chicken-breast-raw`" not in concerns_section


def test_render_judge_markdown_escapes_pipes_in_summary() -> None:
    from eval.judge import render_judge_markdown

    verdicts = [
        JudgeVerdict(
            item_id="x",
            grounded=True,
            judge_confidence=1.0,
            summary="a | b | c should not break the table",
        ),
    ]
    md = render_judge_markdown(verdicts)
    assert "a \\| b \\| c" in md


def test_render_judge_markdown_omits_concerns_section_when_all_grounded() -> None:
    from eval.judge import render_judge_markdown

    verdicts = [
        JudgeVerdict(item_id="a", grounded=True, judge_confidence=1.0, summary="ok"),
    ]
    md = render_judge_markdown(verdicts)
    assert "## Concerns" not in md

"""Tests for the typed judge concerns."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eval.judge import render_judge_markdown
from snaq_verify.models import JudgeConcern, JudgeVerdict


def test_judge_concern_requires_known_kind() -> None:
    with pytest.raises(ValidationError):
        JudgeConcern(kind="made_up_kind", detail="nope")  # type: ignore[arg-type]


def test_judge_concern_field_is_optional() -> None:
    c = JudgeConcern(kind="paraphrase", detail="restated kcal incorrectly")
    assert c.field is None


def test_judge_verdict_embeds_typed_concerns() -> None:
    v = JudgeVerdict(
        item_id="banana-raw",
        grounded=False,
        concerns=[
            JudgeConcern(
                kind="correction_provenance",
                field="proposed_correction.calories_kcal",
                detail="Value 107.37 not found in any source.",
            )
        ],
        judge_confidence=0.9,
        summary="Unsupported correction value.",
    )
    assert v.concerns[0].kind == "correction_provenance"
    assert v.concerns[0].field == "proposed_correction.calories_kcal"


def test_markdown_shows_concern_kind_counts_and_per_concern_detail() -> None:
    v = JudgeVerdict(
        item_id="banana-raw",
        grounded=False,
        concerns=[
            JudgeConcern(kind="paraphrase", detail="P one"),
            JudgeConcern(kind="correction_provenance", detail="C one"),
        ],
        judge_confidence=0.9,
        summary="s",
    )
    md = render_judge_markdown([v])
    assert "## Concern kinds" in md
    assert "`paraphrase`" in md
    assert "`correction_provenance`" in md
    # Per-concern detail rendering
    assert "**paraphrase**" in md
    assert "P one" in md
    assert "C one" in md


def test_markdown_omits_concern_kinds_section_when_no_concerns() -> None:
    v = JudgeVerdict(
        item_id="x",
        grounded=True,
        concerns=[],
        judge_confidence=0.9,
        summary="s",
    )
    md = render_judge_markdown([v])
    assert "## Concern kinds" not in md

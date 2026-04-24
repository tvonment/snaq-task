"""Tests for the pure stability aggregator.

No LLM, no HTTP, no filesystem: every case feeds hand-crafted fake
verify/judge run docs to :func:`eval.stability.build_matrix` and asserts
the numbers.
"""

from __future__ import annotations

from eval.stability import (
    _pairwise_jaccard_mean,
    build_effort_block,
    build_matrix,
    render_matrix_markdown,
)


def _verify_run(rows: list[dict], *, traces: list[list[dict]] | None = None) -> dict:
    """Wrap result rows in the minimum report.json shape."""
    items = []
    for i, r in enumerate(rows):
        trace = traces[i] if traces else []
        items.append({"item": {"id": r["item_id"]}, "result": r, "trace": trace})
    return {"items": items}


def _judge_run(verdicts: list[dict]) -> dict:
    return {"verdicts": verdicts}


# ---------------------------------------------------------------------------
# build_effort_block (single-effort math)
# ---------------------------------------------------------------------------


def test_verifier_modal_and_agreement() -> None:
    v1 = _verify_run([{"item_id": "x", "status": "DISCREPANCY", "confidence": 0.8}])
    v2 = _verify_run([{"item_id": "x", "status": "DISCREPANCY", "confidence": 0.8}])
    v3 = _verify_run([{"item_id": "x", "status": "VERIFIED", "confidence": 1.0}])
    block = build_effort_block(effort="low", verify_runs=[v1, v2, v3])
    assert block.runs == 3
    row = block.items[0]
    assert row.verifier.modal_status == "DISCREPANCY"
    assert abs(row.verifier.status_agreement - 2 / 3) < 1e-9
    assert row.verifier.confidences == [0.8, 0.8, 1.0]
    assert row.verifier.confidence_stdev > 0


def test_verifier_all_agree_has_zero_stdev() -> None:
    runs = [
        _verify_run([{"item_id": "x", "status": "VERIFIED", "confidence": 0.8}])
    ] * 4
    block = build_effort_block(effort="low", verify_runs=runs)
    row = block.items[0]
    assert row.verifier.status_agreement == 1.0
    assert row.verifier.confidence_stdev == 0.0


def test_correction_field_agreement_on_modal_subset() -> None:
    runs = [
        _verify_run(
            [
                {
                    "item_id": "x",
                    "status": "DISCREPANCY",
                    "confidence": 0.8,
                    "proposed_correction": {"calories_kcal": 100, "protein_g": 5.0},
                }
            ]
        ),
        _verify_run(
            [
                {
                    "item_id": "x",
                    "status": "DISCREPANCY",
                    "confidence": 0.8,
                    "proposed_correction": {"calories_kcal": 100, "protein_g": 5.0},
                }
            ]
        ),
        _verify_run(
            [
                {
                    "item_id": "x",
                    "status": "DISCREPANCY",
                    "confidence": 0.8,
                    "proposed_correction": {"calories_kcal": 100, "protein_g": 7.0},
                }
            ]
        ),
    ]
    block = build_effort_block(effort="medium", verify_runs=runs)
    fa = block.items[0].verifier.correction_field_agreement
    assert fa["calories_kcal"] == 1.0
    assert abs(fa["protein_g"] - 2 / 3) < 1e-9


def test_correction_agreement_empty_when_no_corrections_on_modal() -> None:
    runs = [
        _verify_run([{"item_id": "x", "status": "VERIFIED", "confidence": 0.8}])
    ] * 2
    block = build_effort_block(effort="low", verify_runs=runs)
    assert block.items[0].verifier.correction_field_agreement == {}


def test_judge_grounded_agreement_and_jaccard() -> None:
    verify = [
        _verify_run([{"item_id": "x", "status": "VERIFIED", "confidence": 0.8}]),
        _verify_run([{"item_id": "x", "status": "VERIFIED", "confidence": 0.8}]),
        _verify_run([{"item_id": "x", "status": "VERIFIED", "confidence": 0.8}]),
    ]
    judge = [
        _judge_run(
            [
                {
                    "item_id": "x",
                    "grounded": True,
                    "concerns": [{"kind": "paraphrase", "detail": "a"}],
                    "judge_confidence": 0.9,
                }
            ]
        ),
        _judge_run(
            [
                {
                    "item_id": "x",
                    "grounded": True,
                    "concerns": [
                        {"kind": "paraphrase", "detail": "b"},
                        {"kind": "nitpick", "detail": "c"},
                    ],
                    "judge_confidence": 0.8,
                }
            ]
        ),
        _judge_run(
            [
                {
                    "item_id": "x",
                    "grounded": False,
                    "concerns": [{"kind": "unit_mismatch", "detail": "d"}],
                    "judge_confidence": 0.7,
                }
            ]
        ),
    ]
    block = build_effort_block(effort="high", verify_runs=verify, judge_runs=judge)
    j = block.items[0].judge
    assert j is not None
    assert j.grounded == [True, True, False]
    assert abs(j.grounded_agreement - 2 / 3) < 1e-9
    assert abs(j.grounded_rate - 2 / 3) < 1e-9
    # {paraphrase}/{paraphrase,nitpick}=1/2, others=0 -> mean = 1/6.
    assert abs(j.kind_set_jaccard_mean - (1 / 2 + 0 + 0) / 3) < 1e-9
    assert j.judge_confidences == [0.9, 0.8, 0.7]


def test_jaccard_two_empty_sets_is_one_by_convention() -> None:
    assert _pairwise_jaccard_mean([set(), set()]) == 1.0


def test_jaccard_disjoint_sets_is_zero() -> None:
    assert _pairwise_jaccard_mean([{"a"}, {"b"}]) == 0.0


def test_item_missing_from_some_runs_is_skipped() -> None:
    v1 = _verify_run(
        [
            {"item_id": "x", "status": "VERIFIED", "confidence": 0.8},
            {"item_id": "y", "status": "VERIFIED", "confidence": 0.8},
        ]
    )
    v2 = _verify_run([{"item_id": "x", "status": "VERIFIED", "confidence": 0.8}])
    block = build_effort_block(effort="low", verify_runs=[v1, v2])
    assert {row.item_id for row in block.items} == {"x"}


def test_tool_call_counts_from_traces() -> None:
    runs = [
        _verify_run(
            [{"item_id": "x", "status": "VERIFIED", "confidence": 0.9}],
            traces=[[{"tool": "a"}, {"tool": "b"}]],
        ),
        _verify_run(
            [{"item_id": "x", "status": "VERIFIED", "confidence": 0.9}],
            traces=[[{"tool": "a"}]],
        ),
    ]
    block = build_effort_block(effort="medium", verify_runs=runs)
    v = block.items[0].verifier
    assert v.tool_call_counts == [2, 1]
    assert v.tool_calls_mean == 1.5


# ---------------------------------------------------------------------------
# build_matrix (cross-effort summary)
# ---------------------------------------------------------------------------


def _stable_verify_runs(item_id: str, *, conf: float, k: int) -> list[dict]:
    return [
        _verify_run([{"item_id": item_id, "status": "VERIFIED", "confidence": conf}])
        for _ in range(k)
    ]


def _grounded_judge_runs(item_id: str, *, grounded: bool, k: int) -> list[dict]:
    return [
        _judge_run(
            [
                {
                    "item_id": item_id,
                    "grounded": grounded,
                    "concerns": [],
                    "judge_confidence": 0.9,
                }
            ]
        )
        for _ in range(k)
    ]


def test_matrix_aggregates_multiple_efforts() -> None:
    runs_by_effort = {
        "low": _stable_verify_runs("x", conf=0.7, k=3),
        "high": _stable_verify_runs("x", conf=0.95, k=3),
    }
    judge_by_effort = {
        "low": _grounded_judge_runs("x", grounded=False, k=3),
        "high": _grounded_judge_runs("x", grounded=True, k=3),
    }
    m = build_matrix(
        input_file="food_items.json",
        runs_by_effort=runs_by_effort,
        judge_runs_by_effort=judge_by_effort,
    )
    assert m.efforts == ["low", "high"]
    assert m.runs == 3
    assert len(m.blocks) == 2
    assert len(m.summary) == 2

    low_summary = next(s for s in m.summary if s.effort == "low")
    high_summary = next(s for s in m.summary if s.effort == "high")
    assert abs(low_summary.confidence_mean - 0.7) < 1e-9
    assert abs(high_summary.confidence_mean - 0.95) < 1e-9
    assert low_summary.grounded_rate_mean == 0.0
    assert high_summary.grounded_rate_mean == 1.0


def test_matrix_summary_without_judge() -> None:
    m = build_matrix(
        input_file="food_items.json",
        runs_by_effort={"medium": _stable_verify_runs("x", conf=0.8, k=2)},
    )
    assert m.summary[0].grounded_rate_mean is None
    assert m.summary[0].grounded_agreement_mean is None
    assert m.summary[0].kind_jaccard_mean is None


def test_render_markdown_has_summary_and_per_effort_sections() -> None:
    m = build_matrix(
        input_file="food_items.json",
        runs_by_effort={
            "low": _stable_verify_runs("x", conf=0.7, k=2),
            "high": _stable_verify_runs("x", conf=0.95, k=2),
        },
        judge_runs_by_effort={
            "low": _grounded_judge_runs("x", grounded=False, k=2),
            "high": _grounded_judge_runs("x", grounded=True, k=2),
        },
    )
    md = render_matrix_markdown(m)
    assert "# Stability Matrix" in md
    assert "## Effort summary" in md
    assert "## Effort `low`" in md
    assert "## Effort `high`" in md
    assert "### Verifier" in md
    assert "### Judge" in md
    assert "Grounded rate" in md


def test_render_markdown_omits_judge_when_not_included() -> None:
    m = build_matrix(
        input_file="food_items.json",
        runs_by_effort={"low": _stable_verify_runs("x", conf=0.8, k=1)},
    )
    md = render_matrix_markdown(m)
    assert "### Verifier" in md
    assert "### Judge" not in md
    assert "Grounded rate" not in md

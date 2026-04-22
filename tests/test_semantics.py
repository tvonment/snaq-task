"""Tests for M4: richer trace payloads + semantics catalogue."""

from __future__ import annotations

from snaq_verify.logic.semantics import compare_semantics
from snaq_verify.models import ToolCall


def test_tool_call_payload_defaults_to_none() -> None:
    tc = ToolCall(tool="x", args={}, result_summary="", latency_ms=1.0)
    assert tc.result_payload is None


def test_tool_call_accepts_structured_payload() -> None:
    tc = ToolCall(
        tool="lookup_usda_by_name",
        args={"name": "banana"},
        result_summary="match",
        result_payload={"nutrition": {"calories_kcal": 89.0}},
        latency_ms=1.0,
    )
    assert tc.result_payload is not None
    assert tc.result_payload["nutrition"]["calories_kcal"] == 89.0


def test_compare_semantics_usda_ciqual_returns_carbs_and_energy_notes() -> None:
    cmp = compare_semantics("USDA", "CIQUAL")
    kinds = {n.kind for n in cmp.notes}
    assert "carbs_definition" in kinds
    assert "energy_definition" in kinds
    # And it should also include protein_conversion because USDA/CIQUAL
    # both rely on Kjeldahl with per-food factors.
    assert "protein_conversion" in kinds


def test_compare_semantics_is_order_independent() -> None:
    a = compare_semantics("USDA", "CIQUAL")
    b = compare_semantics("CIQUAL", "USDA")
    assert {n.kind for n in a.notes} == {n.kind for n in b.notes}


def test_compare_semantics_off_usda_returns_sodium_note() -> None:
    cmp = compare_semantics("OpenFoodFacts", "USDA")
    assert any(n.kind == "sodium_vs_salt" for n in cmp.notes)


def test_compare_semantics_same_source_returns_empty() -> None:
    # No catalogue entry describes USDA vs USDA; that's intentional.
    cmp = compare_semantics("USDA", "USDA")
    assert cmp.notes == []


def test_carbs_note_flags_carbohydrates_field() -> None:
    cmp = compare_semantics("USDA", "CIQUAL")
    carbs = next(n for n in cmp.notes if n.kind == "carbs_definition")
    assert carbs.affected_fields == ["carbohydrates_g"]
    # The explanation must actually name the difference so the agent
    # can reason over it without guessing.
    assert "fibre" in carbs.explanation.lower() or "fiber" in carbs.explanation.lower()

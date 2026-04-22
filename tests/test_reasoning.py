"""Tests for the M2 structured reasoning contract.

The invariant: narrative fields on ``VerificationReasoning`` must be
digit-free. Numbers live in the structured tool outputs
(``discrepancies``, ``macro_consistency``, ``sources``) and the report
layer composes the human-readable sentence from those.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from snaq_verify.models import VerificationReasoning
from snaq_verify.report import _compose_reasoning


def _valid() -> VerificationReasoning:
    return VerificationReasoning(
        routing_decision="generic_usda",
        source_choice_rationale="Generic food; USDA Foundation is the authoritative reference.",
    )


def test_reasoning_accepts_digit_free_prose() -> None:
    r = _valid()
    assert r.routing_decision == "generic_usda"


def test_reasoning_rejects_digits_in_source_choice() -> None:
    with pytest.raises(ValidationError):
        VerificationReasoning(
            routing_decision="generic_usda",
            source_choice_rationale="Matched 2 USDA records within tolerance.",
        )


def test_reasoning_rejects_percentages_in_variance_notes() -> None:
    with pytest.raises(ValidationError):
        VerificationReasoning(
            routing_decision="known_variance",
            source_choice_rationale="Farmed salmon varies naturally across sources.",
            variance_notes="Fat can swing 40% across farms.",
        )


def test_reasoning_rejects_digits_in_correction_rationale() -> None:
    with pytest.raises(ValidationError):
        VerificationReasoning(
            routing_decision="generic_usda",
            source_choice_rationale="USDA Foundation matched.",
            correction_rationale="Calories should be 165.",
        )


def test_reasoning_allows_none_for_optional_fields() -> None:
    r = VerificationReasoning(
        routing_decision="manual_review",
        source_choice_rationale="No confident source available.",
    )
    assert r.variance_notes is None
    assert r.correction_rationale is None


def test_compose_reasoning_stitches_structured_fields() -> None:
    res = {
        "reasoning": {
            "routing_decision": "generic_usda",
            "source_choice_rationale": "USDA Foundation is the authoritative reference",
            "variance_notes": None,
            "correction_rationale": None,
        }
    }
    sentence = _compose_reasoning(res)
    assert "USDA" in sentence
    assert sentence.endswith(".")


def test_compose_reasoning_preserves_legacy_string_reports() -> None:
    # Reports from before M2 store reasoning as a plain string; the
    # renderer must still render them so old artifacts remain viewable.
    res = {"reasoning": "legacy free-text rationale"}
    assert _compose_reasoning(res) == "legacy free-text rationale"


def test_compose_reasoning_handles_missing_reasoning() -> None:
    assert _compose_reasoning({}) == "(no reasoning provided)"

"""Snapshot-style assertions on the agent + judge prompts.

The Phase 1 instruction rewrite (INSTRUCTIONS_VERSION = v2) added four
explicit rules. We don't want a prompt framework yet -- a substring
test that fails when a rule is silently dropped is the cheapest
guardrail.
"""

from __future__ import annotations

from eval.judge import _JUDGE_SYSTEM_PROMPT
from snaq_verify.agent import INSTRUCTIONS, INSTRUCTIONS_VERSION


def test_instructions_version_is_set() -> None:
    assert INSTRUCTIONS_VERSION


def test_instructions_mandate_compare_semantics_before_discrepancy() -> None:
    text = INSTRUCTIONS.lower()
    assert "compare_semantics_tool" in text
    assert "before calculate_discrepancy_tool" in text


def test_instructions_state_definitional_fields_are_not_discrepancies() -> None:
    text = INSTRUCTIONS.lower()
    assert "definitional" in text
    assert "does not count toward" in text or "does not count toward a discrepancy" in text


def test_instructions_make_high_variance_mandatory() -> None:
    text = INSTRUCTIONS.lower()
    assert "high_variance" in text
    assert "must be high_variance" in text or "no exceptions" in text


def test_instructions_cap_confidence_at_0_8_on_two_source_disagreement() -> None:
    text = INSTRUCTIONS.lower()
    assert "cap at 0.8" in text or "cap confidence at 0.8" in text


def test_judge_prompt_grounds_against_same_rubric() -> None:
    """Judge must replicate the rubric so it grades against one source of truth."""
    text = _JUDGE_SYSTEM_PROMPT.lower()
    assert "two independent sources" in text
    # Don't punish capping at 0.8.
    assert "do not raise rubric_violation" in text
    assert "high_variance" in text

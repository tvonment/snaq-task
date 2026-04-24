"""LLM-as-judge: re-reads the agent's report and scores it for grounding.

Rationale: the verifier can hallucinate a source citation or back-fill a
value the tool never returned. The judge runs with a *different* system
prompt and (when available) a *different* deployment name so the two
don't collude on the same failure modes.

Input : ``outputs/report.json`` produced by ``snaq-verify verify``.
Output: ``outputs/judge.json`` with one :class:`JudgeVerdict` per item.

Run:
    uv run snaq-verify judge outputs/report.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from snaq_verify.config import Settings
from snaq_verify.models import JudgeConcern, JudgeVerdict

_LOG = logging.getLogger("snaq_verify.judge")

_JUDGE_SYSTEM_PROMPT = """\
You are an evaluator. Read one food item's automated verification result
and decide whether the stated reasoning is supported by the listed
sources and tool trace.

Return a JudgeVerdict. Each entry in `concerns` must use one of these
typed kinds:

- paraphrase: the reasoning restates a number incorrectly from a source.
- missing_citation: a claim in the reasoning is not supported by any
  listed source or tool result.
- correction_provenance: the proposed_correction contains values that
  cannot be traced back to a cited source.
- unit_mismatch: two sources are compared without accounting for a
  definitional difference (e.g. USDA "carbohydrate, by difference"
  vs CIQUAL "available carbs", kJ vs kcal).
- wrong_reference: the chosen reference record is the wrong food.
- rubric_violation: the confidence score violates the stated rubric
  (two-source match but confidence < 1.0, branded with complete macros
  but confidence > 0.6, etc.).
- variance_reasoning: natural-variance items were mishandled (e.g.
  farmed salmon flagged as DISCREPANCY rather than HIGH_VARIANCE).
- nitpick: style or phrasing feedback that is NOT a grounding problem.
  Use this when in doubt -- nitpicks are ignored by CI.

Set `field` to the dotted path inside the VerificationResult when you
can (e.g. `proposed_correction.calories_kcal`,
`discrepancies[2].delta_fraction`). Leave it null when the concern
applies to the whole result.

Set `grounded=false` if any non-nitpick concern applies. A verdict with
only nitpicks should be `grounded=true`.

Be strict and concrete. One sentence per `detail`.
"""


def _build_judge_agent(settings: Settings) -> tuple[Agent[None, JudgeVerdict], str]:
    """Build the judge agent. Returns (agent, deployment_name).

    Uses ``AZURE_OPENAI_JUDGE_DEPLOYMENT`` when set so verifier and judge
    can run on different models; otherwise falls back to the verifier's
    deployment. The deployment name is returned so callers can record
    which model graded a given report.
    """
    deployment = os.environ.get("AZURE_OPENAI_JUDGE_DEPLOYMENT") or settings.azure_deployment
    base_url = settings.azure_endpoint.rstrip("/") + "/"
    client = AsyncOpenAI(base_url=base_url, api_key=settings.azure_api_key)
    model = OpenAIChatModel(deployment, provider=OpenAIProvider(openai_client=client))
    # The judge runs on a non-reasoning chat deployment by convention
    # (gpt-5-chat). temperature=0 actually does something there --
    # unlike on a reasoning verifier -- so we pin it. We want the judge
    # to be the most deterministic part of the pipeline so verifier-
    # side variables (e.g. reasoning_effort sweeps) read cleanly.
    agent = Agent(
        model=model,
        output_type=JudgeVerdict,
        system_prompt=_JUDGE_SYSTEM_PROMPT,
        model_settings=ModelSettings(temperature=0.0),
    )
    return agent, deployment


async def _judge_one(agent: Agent[None, JudgeVerdict], row: dict) -> JudgeVerdict:
    """Send one report row to the judge."""
    prompt = (
        "Evaluate this verification result.\n\n"
        f"ITEM:\n{json.dumps(row['item'], indent=2)}\n\n"
        f"RESULT:\n{json.dumps(row['result'], indent=2)}\n\n"
        f"TRACE:\n{json.dumps(row['trace'], indent=2)}\n\n"
        f"Item id is {row['result']['item_id']!r}. "
        "Set JudgeVerdict.item_id to that exact string."
    )
    run = await agent.run(prompt)
    verdict = run.output
    # Defend against the judge picking a different id.
    if verdict.item_id != row["result"]["item_id"]:
        verdict = verdict.model_copy(update={"item_id": row["result"]["item_id"]})
    return verdict


async def run_judge(report_path: Path, out_path: Path, concurrency: int = 3) -> None:
    """Score every item in ``report_path`` and write ``out_path``.

    Also writes a sibling ``<stem>.md`` next to ``out_path`` with a
    human-readable summary table and per-item details.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    for name in ("httpx", "httpcore", "openai", "pydantic_ai"):
        logging.getLogger(name).setLevel(logging.WARNING)

    settings = Settings.load()
    doc = json.loads(report_path.read_text())
    rows = doc.get("items", [])
    _LOG.info("Judging %d items", len(rows))

    agent, judge_deployment = _build_judge_agent(settings)
    sem = asyncio.Semaphore(concurrency)

    async def one(row: dict) -> JudgeVerdict:
        async with sem:
            try:
                v = await _judge_one(agent, row)
            except Exception as exc:
                _LOG.exception("Judge failed for %s", row["result"]["item_id"])
                v = JudgeVerdict(
                    item_id=row["result"]["item_id"],
                    grounded=False,
                    concerns=[
                        JudgeConcern(
                            kind="rubric_violation",
                            detail=f"judge raised: {type(exc).__name__}: {exc}",
                        )
                    ],
                    judge_confidence=0.0,
                    summary="Judge failed to produce a verdict.",
                )
            _LOG.info(
                "[judge] %-28s grounded=%s conf=%.2f %s",
                v.item_id,
                v.grounded,
                v.judge_confidence,
                f"({len(v.concerns)} concerns)" if v.concerns else "",
            )
            return v

    verdicts = await asyncio.gather(*(one(r) for r in rows))
    generated_at = datetime.now(UTC).isoformat()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "judge_deployment": judge_deployment,
                "report_path": str(report_path),
                "verdicts": [v.model_dump() for v in verdicts],
            },
            indent=2,
            default=str,
        )
    )
    md_path = out_path.with_suffix(".md")
    md_path.write_text(
        render_judge_markdown(
            verdicts,
            report_path=report_path,
            generated_at=generated_at,
            judge_deployment=judge_deployment,
        )
    )

    n_grounded = sum(1 for v in verdicts if v.grounded)
    _LOG.info(
        "Judge done: %d/%d grounded -> %s (+ %s)",
        n_grounded,
        len(verdicts),
        out_path,
        md_path.name,
    )


def render_judge_markdown(
    verdicts: list[JudgeVerdict],
    *,
    report_path: Path | None = None,
    generated_at: str | None = None,
    judge_deployment: str | None = None,
) -> str:
    """Render a markdown view of the judge verdicts.

    Kept next to ``run_judge`` so the two stay in sync; exposed as a
    module-level function so it can be unit-tested without invoking
    the LLM. ``generated_at`` and ``judge_deployment`` are optional so
    existing callers (and tests) keep working.
    """
    n = len(verdicts)
    n_grounded = sum(1 for v in verdicts if v.grounded)
    avg_conf = (sum(v.judge_confidence for v in verdicts) / n) if n else 0.0

    lines: list[str] = []
    lines.append("# Judge Report")
    lines.append("")
    if generated_at is not None:
        lines.append(f"- Generated: `{generated_at}`")
    if judge_deployment is not None:
        lines.append(f"- Judge model: `{judge_deployment}`")
    if report_path is not None:
        lines.append(f"- Scoring: `{report_path}`")
    if generated_at or judge_deployment or report_path is not None:
        lines.append("")
    lines.append(
        f"**Grounded:** {n_grounded}/{n} "
        f"&nbsp;&nbsp; **Avg judge confidence:** {avg_conf:.2f}"
    )
    lines.append("")

    # Concern bucket counts: the single most useful diff across runs.
    kind_counts: dict[str, int] = {}
    for v in verdicts:
        for c in v.concerns:
            kind_counts[c.kind] = kind_counts.get(c.kind, 0) + 1
    if kind_counts:
        lines.append("## Concern kinds")
        lines.append("")
        lines.append("| Kind | Count |")
        lines.append("|------|-------|")
        for kind, count in sorted(kind_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"| `{kind}` | {count} |")
        lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| # | Item | Grounded | Confidence | Summary |")
    lines.append("|---|------|----------|------------|---------|")
    for i, v in enumerate(verdicts, 1):
        mark = "\u2705" if v.grounded else "\u26a0\ufe0f"
        # First sentence only, keep the table legible.
        first = v.summary.split(". ")[0].strip()
        if len(first) > 140:
            first = first[:137].rstrip() + "..."
        # Escape pipes so a stray '|' in the summary doesn't break the table.
        first = first.replace("|", "\\|")
        lines.append(
            f"| {i} | `{v.item_id}` | {mark} | {v.judge_confidence:.2f} | {first} |"
        )
    lines.append("")

    ungrounded = [v for v in verdicts if not v.grounded]
    if ungrounded:
        lines.append("## Concerns")
        lines.append("")
        for v in ungrounded:
            lines.append(f"### `{v.item_id}` (conf {v.judge_confidence:.2f})")
            lines.append("")
            lines.append(v.summary)
            lines.append("")
            for c in v.concerns:
                target = f" _{c.field}_" if c.field else ""
                lines.append(f"- **{c.kind}**{target}: {c.detail}")
            if v.concerns:
                lines.append("")
    return "\n".join(lines) + "\n"

"""Stability meta-eval: sweep reasoning effort levels x K runs.

Rationale: at n=11 with no hand-labelled ground truth, we can't measure
whether the agent is *right*. We can measure two things instead:

1. Whether the agent agrees with itself across runs (stability).
2. Whether quality (judge groundedness) trades off against
   reasoning_effort -- is `low` good enough, or do we really need
   `high`?

The stability command runs the verifier at each chosen reasoning_effort
level, K times per level, and writes:

- ``<out_dir>/stability/<effort>/run_{k}/report.{json,md}``
- ``<out_dir>/stability/<effort>/run_{k}/judge.{json,md}`` (when judge
  is enabled)
- ``<out_dir>/stability/matrix.{json,md}`` -- the aggregate

Pure aggregation is split out into :func:`build_matrix` /
:func:`build_effort_block` so the math is unit-tested without touching
the LLM.

Run:
    uv run snaq-verify stability food_items.json --runs 3
    uv run snaq-verify stability food_items.json \\
        --runs 5 --efforts low,medium --no-judge
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_LOG = logging.getLogger("snaq_verify.stability")

ReasoningEffort = Literal["minimal", "low", "medium", "high"]
DEFAULT_EFFORTS: tuple[ReasoningEffort, ...] = ("minimal", "low", "medium", "high")

_CORRECTION_FIELDS: tuple[str, ...] = (
    "calories_kcal",
    "protein_g",
    "fat_g",
    "saturated_fat_g",
    "carbohydrates_g",
    "sugar_g",
    "fiber_g",
    "sodium_mg",
)


# ---------------------------------------------------------------------------
# Aggregator models
# ---------------------------------------------------------------------------


class VerifierItemStability(BaseModel):
    """Per-item verifier agreement across K runs at one effort level."""

    model_config = ConfigDict(extra="forbid")

    statuses: list[str]
    modal_status: str
    status_agreement: float = Field(
        description="Fraction of runs matching the modal status."
    )
    confidences: list[float]
    confidence_mean: float
    confidence_stdev: float
    correction_field_agreement: dict[str, float] = Field(
        default_factory=dict,
        description="Per-field agreement fraction on the modal-status "
        "subset. 1.0 means every run on the modal status proposed the "
        "same value for that field (including 'null'). Empty when no "
        "run on the modal status proposed a correction.",
    )
    tool_call_counts: list[int] = Field(
        default_factory=list,
        description="Number of tool calls per run (cost / effort proxy). "
        "Empty when traces aren't present in the source reports.",
    )
    tool_calls_mean: float = 0.0


class JudgeItemStability(BaseModel):
    """Per-item judge agreement across K runs at one effort level."""

    model_config = ConfigDict(extra="forbid")

    grounded: list[bool]
    grounded_agreement: float = Field(
        description="Fraction of runs matching the modal grounded value."
    )
    grounded_rate: float = Field(
        description="Fraction of runs where the judge marked the item "
        "grounded. Higher is better; this is the headline quality signal."
    )
    concern_kinds_per_run: list[list[str]]
    kind_set_jaccard_mean: float = Field(
        description="Mean pairwise Jaccard similarity of concern-kind "
        "sets across runs. 1.0 = identical kind sets every run; 0.0 = "
        "disjoint. Two empty sets count as 1.0 by convention."
    )
    judge_confidences: list[float]
    judge_confidence_mean: float
    judge_confidence_stdev: float


class ItemStability(BaseModel):
    """Per-item combined stability row at one effort level."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    verifier: VerifierItemStability
    judge: JudgeItemStability | None = None


class EffortBlock(BaseModel):
    """All per-item stability rows for one effort level."""

    model_config = ConfigDict(extra="forbid")

    effort: str
    runs: int
    items: list[ItemStability]


class EffortSummary(BaseModel):
    """Cross-item roll-up for one effort level. The 'is it worth it?' table."""

    model_config = ConfigDict(extra="forbid")

    effort: str
    runs: int
    n_items: int
    status_agreement_mean: float = Field(
        description="Mean per-item status_agreement across all items."
    )
    confidence_mean: float = Field(
        description="Mean of per-item confidence_mean across all items."
    )
    grounded_rate_mean: float | None = Field(
        default=None,
        description="Mean of per-item grounded_rate. None when judge "
        "wasn't run.",
    )
    grounded_agreement_mean: float | None = None
    kind_jaccard_mean: float | None = None
    tool_calls_mean: float = Field(
        default=0.0,
        description="Mean tool calls per (item, run). Cost / effort proxy.",
    )


class StabilityMatrix(BaseModel):
    """Aggregate output of an efforts x K runs sweep."""

    model_config = ConfigDict(extra="forbid")

    generated_at: str
    input_file: str
    efforts: list[str]
    runs: int = Field(
        description="K -- number of repeated runs *per effort level*."
    )
    instructions_version: str | None = Field(
        default=None,
        description="agent.INSTRUCTIONS_VERSION at the time of the sweep. "
        "Lets us compare matrices across instruction revisions.",
    )
    summary: list[EffortSummary]
    blocks: list[EffortBlock]


# ---------------------------------------------------------------------------
# Pure aggregation
# ---------------------------------------------------------------------------


def _mode(values: list) -> Any:
    """Return the most common value; ties broken by first-seen order."""
    counts: dict[Any, int] = {}
    order: list[Any] = []
    for v in values:
        if v not in counts:
            order.append(v)
        counts[v] = counts.get(v, 0) + 1
    return max(order, key=lambda v: counts[v])


def _stdev(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) >= 2 else 0.0


def _pairwise_jaccard_mean(sets: list[set[str]]) -> float:
    if len(sets) < 2:
        return 1.0
    sims: list[float] = []
    for a, b in combinations(sets, 2):
        if not a and not b:
            sims.append(1.0)
            continue
        union = a | b
        if not union:
            sims.append(1.0)
            continue
        sims.append(len(a & b) / len(union))
    return sum(sims) / len(sims)


def _verifier_stability(
    results: list[dict], traces_per_run: list[list[dict]] | None
) -> VerifierItemStability:
    statuses = [r["status"] for r in results]
    confidences = [float(r["confidence"]) for r in results]
    modal = _mode(statuses)
    agreement = sum(1 for s in statuses if s == modal) / len(statuses)

    modal_corrections: list[dict | None] = [
        r.get("proposed_correction")
        for r, s in zip(results, statuses, strict=True)
        if s == modal
    ]
    field_agreement: dict[str, float] = {}
    if any(c is not None for c in modal_corrections):
        for field in _CORRECTION_FIELDS:
            values = [
                (c.get(field) if isinstance(c, dict) else None)
                for c in modal_corrections
            ]
            modal_value = _mode(values)
            matches = sum(1 for v in values if v == modal_value)
            field_agreement[field] = matches / len(values)

    tool_counts: list[int] = []
    if traces_per_run is not None:
        tool_counts = [len(t) for t in traces_per_run]

    return VerifierItemStability(
        statuses=statuses,
        modal_status=modal,
        status_agreement=agreement,
        confidences=confidences,
        confidence_mean=sum(confidences) / len(confidences),
        confidence_stdev=_stdev(confidences),
        correction_field_agreement=field_agreement,
        tool_call_counts=tool_counts,
        tool_calls_mean=(sum(tool_counts) / len(tool_counts)) if tool_counts else 0.0,
    )


def _judge_stability(verdicts: list[dict]) -> JudgeItemStability:
    grounded = [bool(v.get("grounded", False)) for v in verdicts]
    modal_grounded = _mode(grounded)
    g_agreement = sum(1 for g in grounded if g == modal_grounded) / len(grounded)
    g_rate = sum(1 for g in grounded if g) / len(grounded)

    kinds_per_run: list[list[str]] = []
    for v in verdicts:
        kinds: list[str] = []
        for c in v.get("concerns") or []:
            if isinstance(c, dict) and "kind" in c:
                kinds.append(c["kind"])
        kinds_per_run.append(kinds)
    jaccard = _pairwise_jaccard_mean([set(k) for k in kinds_per_run])

    jc = [float(v.get("judge_confidence", 0.0)) for v in verdicts]
    return JudgeItemStability(
        grounded=grounded,
        grounded_agreement=g_agreement,
        grounded_rate=g_rate,
        concern_kinds_per_run=kinds_per_run,
        kind_set_jaccard_mean=jaccard,
        judge_confidences=jc,
        judge_confidence_mean=sum(jc) / len(jc),
        judge_confidence_stdev=_stdev(jc),
    )


def build_effort_block(
    *,
    effort: str,
    verify_runs: list[dict],
    judge_runs: list[dict] | None = None,
) -> EffortBlock:
    """Aggregate K verify (+optional K judge) runs for one effort level."""
    if not verify_runs:
        raise ValueError("need at least one verify run")
    if judge_runs is not None and len(judge_runs) != len(verify_runs):
        raise ValueError("judge_runs must match verify_runs length")

    K = len(verify_runs)
    per_item_results: dict[str, list[dict]] = {}
    per_item_traces: dict[str, list[list[dict]]] = {}
    ordered_ids: list[str] = []
    for run in verify_runs:
        for row in run.get("items", []):
            r = row["result"]
            iid = r["item_id"]
            if iid not in per_item_results:
                per_item_results[iid] = []
                per_item_traces[iid] = []
                ordered_ids.append(iid)
            per_item_results[iid].append(r)
            per_item_traces[iid].append(row.get("trace") or [])

    per_item_verdicts: dict[str, list[dict]] = {}
    if judge_runs is not None:
        for run in judge_runs:
            for v in run.get("verdicts", []):
                per_item_verdicts.setdefault(v["item_id"], []).append(v)

    items: list[ItemStability] = []
    for iid in ordered_ids:
        results = per_item_results[iid]
        if len(results) != K:
            _LOG.warning(
                "item %s appears in %d/%d runs at effort=%s; skipping",
                iid,
                len(results),
                K,
                effort,
            )
            continue
        traces = per_item_traces[iid]
        verifier = _verifier_stability(
            results, traces if any(traces) else None
        )
        judge: JudgeItemStability | None = None
        if judge_runs is not None:
            verdicts = per_item_verdicts.get(iid, [])
            if len(verdicts) == K:
                judge = _judge_stability(verdicts)
            else:
                _LOG.warning(
                    "judge verdicts for %s at effort=%s: %d/%d; omitting judge row",
                    iid,
                    effort,
                    len(verdicts),
                    K,
                )
        items.append(ItemStability(item_id=iid, verifier=verifier, judge=judge))

    return EffortBlock(effort=effort, runs=K, items=items)


def _summarise_block(block: EffortBlock) -> EffortSummary:
    n = len(block.items)
    if n == 0:
        return EffortSummary(
            effort=block.effort,
            runs=block.runs,
            n_items=0,
            status_agreement_mean=0.0,
            confidence_mean=0.0,
            tool_calls_mean=0.0,
        )
    status_agreement_mean = sum(i.verifier.status_agreement for i in block.items) / n
    confidence_mean = sum(i.verifier.confidence_mean for i in block.items) / n
    tool_calls_mean = sum(i.verifier.tool_calls_mean for i in block.items) / n

    judge_items = [i for i in block.items if i.judge is not None]
    grounded_rate_mean: float | None = None
    grounded_agreement_mean: float | None = None
    kind_jaccard_mean: float | None = None
    if judge_items:
        grounded_rate_mean = sum(
            i.judge.grounded_rate for i in judge_items  # type: ignore[union-attr]
        ) / len(judge_items)
        grounded_agreement_mean = sum(
            i.judge.grounded_agreement for i in judge_items  # type: ignore[union-attr]
        ) / len(judge_items)
        kind_jaccard_mean = sum(
            i.judge.kind_set_jaccard_mean for i in judge_items  # type: ignore[union-attr]
        ) / len(judge_items)

    return EffortSummary(
        effort=block.effort,
        runs=block.runs,
        n_items=n,
        status_agreement_mean=status_agreement_mean,
        confidence_mean=confidence_mean,
        grounded_rate_mean=grounded_rate_mean,
        grounded_agreement_mean=grounded_agreement_mean,
        kind_jaccard_mean=kind_jaccard_mean,
        tool_calls_mean=tool_calls_mean,
    )


def build_matrix(
    *,
    input_file: str,
    runs_by_effort: dict[str, list[dict]],
    judge_runs_by_effort: dict[str, list[dict]] | None = None,
    generated_at: str | None = None,
    instructions_version: str | None = None,
) -> StabilityMatrix:
    """Aggregate an efforts x K-runs sweep into a single matrix."""
    if not runs_by_effort:
        raise ValueError("need at least one effort level")

    efforts = list(runs_by_effort.keys())
    blocks: list[EffortBlock] = []
    K_seen: set[int] = set()
    for effort in efforts:
        verify_runs = runs_by_effort[effort]
        judge_runs = (judge_runs_by_effort or {}).get(effort)
        block = build_effort_block(
            effort=effort, verify_runs=verify_runs, judge_runs=judge_runs
        )
        blocks.append(block)
        K_seen.add(block.runs)

    if len(K_seen) > 1:
        _LOG.warning("uneven K across efforts: %s", sorted(K_seen))
    K = max(K_seen)

    summary = [_summarise_block(b) for b in blocks]

    return StabilityMatrix(
        generated_at=generated_at or datetime.now(UTC).isoformat(),
        input_file=input_file,
        efforts=efforts,
        runs=K,
        instructions_version=instructions_version,
        summary=summary,
        blocks=blocks,
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_matrix_markdown(matrix: StabilityMatrix) -> str:
    """Render the matrix: top summary table + per-effort detail tables."""
    has_judge = any(
        any(i.judge is not None for i in b.items) for b in matrix.blocks
    )
    lines: list[str] = []
    lines.append("# Stability Matrix")
    lines.append("")
    lines.append(f"- Generated: `{matrix.generated_at}`")
    lines.append(f"- Input: `{matrix.input_file}`")
    lines.append(f"- Efforts swept: {', '.join(f'`{e}`' for e in matrix.efforts)}")
    lines.append(f"- Runs per effort: **{matrix.runs}**")
    if matrix.instructions_version is not None:
        lines.append(f"- Instructions: `{matrix.instructions_version}`")
    lines.append("")
    lines.append(
        "Stability measures whether the agent agrees with *itself* "
        "across runs; it is not a correctness metric. Sweeping "
        "`reasoning_effort` lets us see how much reasoning the verifier "
        "actually needs to produce stable, judge-grounded answers."
    )
    lines.append("")

    # ----- Cross-effort summary -----
    lines.append("## Effort summary")
    lines.append("")
    header = ["Effort", "Status agree", "Conf mean"]
    if has_judge:
        header += ["Grounded rate", "Grounded agree", "Kind Jaccard"]
    header += ["Tool calls / run"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for s in matrix.summary:
        cells = [
            f"`{s.effort}`",
            f"{s.status_agreement_mean:.0%}",
            f"{s.confidence_mean:.2f}",
        ]
        if has_judge:
            ga = (
                f"{s.grounded_agreement_mean:.0%}"
                if s.grounded_agreement_mean is not None
                else "—"
            )
            cells += [
                f"{s.grounded_rate_mean:.0%}" if s.grounded_rate_mean is not None else "—",
                ga,
                f"{s.kind_jaccard_mean:.2f}" if s.kind_jaccard_mean is not None else "—",
            ]
        cells.append(f"{s.tool_calls_mean:.1f}")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "Read the summary as: *what's the lowest effort whose status "
        "agreement and grounded rate match the highest effort?* That's "
        "the cheapest setting we can ship."
    )
    lines.append("")

    for block in matrix.blocks:
        lines.append(f"## Effort `{block.effort}`")
        lines.append("")
        lines.append(f"_K = {block.runs}_")
        lines.append("")
        lines.extend(_render_block_verifier(block))
        if has_judge and any(i.judge is not None for i in block.items):
            lines.extend(_render_block_judge(block))
    return "\n".join(lines) + "\n"


def _render_block_verifier(block: EffortBlock) -> list[str]:
    K = block.runs
    out: list[str] = []
    out.append("### Verifier")
    out.append("")
    header = (
        ["Item"]
        + [f"Run {k + 1}" for k in range(K)]
        + ["Modal", "Agree", "Conf mean", "Conf stdev", "Tools"]
    )
    out.append("| " + " | ".join(header) + " |")
    out.append("|" + "|".join(["---"] * len(header)) + "|")
    for row in block.items:
        v = row.verifier
        cells = [f"`{row.item_id}`"]
        for s, c in zip(v.statuses, v.confidences, strict=True):
            cells.append(f"{s}@{c:.2f}")
        cells += [
            v.modal_status,
            f"{v.status_agreement:.0%}",
            f"{v.confidence_mean:.2f}",
            f"{v.confidence_stdev:.2f}",
            f"{v.tool_calls_mean:.1f}",
        ]
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    return out


def _render_block_judge(block: EffortBlock) -> list[str]:
    K = block.runs
    out: list[str] = []
    out.append("### Judge")
    out.append("")
    header = (
        ["Item"]
        + [f"Run {k + 1}" for k in range(K)]
        + ["Grounded rate", "Grounded agree", "Kind Jaccard", "Conf mean"]
    )
    out.append("| " + " | ".join(header) + " |")
    out.append("|" + "|".join(["---"] * len(header)) + "|")
    for row in block.items:
        if row.judge is None:
            continue
        j = row.judge
        cells = [f"`{row.item_id}`"]
        for g, c, kinds in zip(
            j.grounded, j.judge_confidences, j.concern_kinds_per_run, strict=True
        ):
            mark = "OK" if g else "!"
            kind_str = ",".join(sorted(set(kinds))) or "-"
            cells.append(f"{mark}@{c:.2f} [{kind_str}]")
        cells += [
            f"{j.grounded_rate:.0%}",
            f"{j.grounded_agreement:.0%}",
            f"{j.kind_set_jaccard_mean:.2f}",
            f"{j.judge_confidence_mean:.2f}",
        ]
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_stability(
    *,
    input_file: Path,
    runs: int,
    out_dir: Path,
    efforts: tuple[str, ...] = DEFAULT_EFFORTS,
    include_judge: bool = True,
    concurrency_override: int | None = None,
    verbose: int = 0,
) -> None:
    """Run the verifier at each effort level K times; aggregate the matrix.

    Files laid out under ``<out_dir>/stability/``:

    - ``<effort>/run_{k}/report.{json,md}``
    - ``<effort>/run_{k}/judge.{json,md}`` (when ``include_judge``)
    - ``matrix.{json,md}`` -- the aggregate
    """
    if runs < 1:
        raise ValueError("runs must be >= 1")
    if not efforts:
        raise ValueError("need at least one effort level")

    # Lazy imports: keeps CLI --help fast and avoids pulling pydantic-ai
    # / openai client at module import time.
    from snaq_verify.config import Settings
    from snaq_verify.runner import _configure_logging, run_verification

    # Configure logging up front so the sweep-level INFO lines
    # (efforts, output dir, per-run progress) show on the console
    # before the first run_verification call re-configures it.
    _configure_logging(Settings.load().log_level, verbose=verbose)

    stability_dir = out_dir / "stability"
    stability_dir.mkdir(parents=True, exist_ok=True)

    runs_by_effort: dict[str, list[dict]] = {}
    judge_runs_by_effort: dict[str, list[dict]] = {}

    total_runs = len(efforts) * runs
    _LOG.info(
        "Stability sweep: %d effort level(s) x %d run(s) = %d verifier run(s)%s",
        len(efforts),
        runs,
        total_runs,
        " (+ judge)" if include_judge else "",
    )
    _LOG.info("  efforts: %s", ", ".join(efforts))
    _LOG.info("  output:  %s", stability_dir)

    completed = 0
    for effort in efforts:
        effort_dir = stability_dir / effort
        runs_by_effort[effort] = []
        if include_judge:
            judge_runs_by_effort[effort] = []
        _LOG.info("=== effort=%s (K=%d) ===", effort, runs)
        for k in range(1, runs + 1):
            completed += 1
            run_dir = effort_dir / f"run_{k}"
            _LOG.info(
                "Stability [%d/%d] effort=%s run=%d/%d -> %s",
                completed,
                total_runs,
                effort,
                k,
                runs,
                run_dir,
            )
            await run_verification(
                input_file=input_file,
                out_dir=run_dir,
                formats=("json", "md"),
                concurrency_override=concurrency_override,
                verbose=verbose,
                reasoning_effort=effort,
            )
            runs_by_effort[effort].append(
                json.loads((run_dir / "report.json").read_text())
            )
            if include_judge:
                from eval.judge import run_judge

                judge_path = run_dir / "judge.json"
                _LOG.info(
                    "Stability [%d/%d] effort=%s run=%d/%d judging -> %s",
                    completed,
                    total_runs,
                    effort,
                    k,
                    runs,
                    judge_path,
                )
                await run_judge(
                    report_path=run_dir / "report.json",
                    out_path=judge_path,
                    concurrency=3,
                )
                judge_runs_by_effort[effort].append(
                    json.loads(judge_path.read_text())
                )
        _LOG.info("=== effort=%s complete (%d run(s)) ===", effort, runs)

    _LOG.info("Aggregating stability matrix across %d effort level(s)", len(efforts))
    from snaq_verify.agent import INSTRUCTIONS_VERSION

    matrix = build_matrix(
        input_file=str(input_file),
        runs_by_effort=runs_by_effort,
        judge_runs_by_effort=judge_runs_by_effort if include_judge else None,
        instructions_version=INSTRUCTIONS_VERSION,
    )
    (stability_dir / "matrix.json").write_text(
        json.dumps(matrix.model_dump(), indent=2)
    )
    (stability_dir / "matrix.md").write_text(render_matrix_markdown(matrix))
    _LOG.info("Stability matrix -> %s", stability_dir / "matrix.md")

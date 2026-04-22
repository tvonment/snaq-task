"""Report rendering: JSON and Markdown.

HTML output was removed in favour of keeping the surface small. The
JSON report carries the full trace for downstream tooling (eval, judge);
the Markdown report is the human-readable artifact.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from snaq_verify.models import FoodItem, ToolCall, VerificationResult

# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def write_reports(
    *,
    items: list[FoodItem],
    results: list[VerificationResult],
    traces: dict[str, list[ToolCall]],
    out_dir: Path,
    formats: tuple[str, ...],
    model_deployment: str,
) -> None:
    """Write the selected report formats to ``out_dir``.

    ``formats`` accepts any combination of ``json`` and ``md``.
    """
    generated_at = datetime.now(UTC).isoformat()
    items_by_id = {i.id: i for i in items}
    rows = [_build_row(items_by_id[r.item_id], r, traces.get(r.item_id, [])) for r in results]

    if "json" in formats:
        _write_json(out_dir, rows, generated_at, model_deployment)
    if "md" in formats:
        _write_markdown(out_dir, rows, generated_at, model_deployment)


# ---------------------------------------------------------------------------
# Row assembly
# ---------------------------------------------------------------------------


def _build_row(
    item: FoodItem, result: VerificationResult, trace: list[ToolCall]
) -> dict:
    return {
        "item": item.model_dump(),
        "result": result.model_dump(),
        "trace": [tc.model_dump() for tc in trace],
    }


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _write_json(out_dir: Path, rows: list[dict], generated_at: str, model: str) -> None:
    doc = {
        "generated_at": generated_at,
        "model_deployment": model,
        "items": rows,
    }
    (out_dir / "report.json").write_text(json.dumps(doc, indent=2, default=str))


_STATUS_BADGE = {
    "VERIFIED": "✅",
    "DISCREPANCY": "⚠️",
    "HIGH_VARIANCE": "〰️",
    "INCONCLUSIVE": "❓",
    "ERROR": "❌",
}


def _write_markdown(out_dir: Path, rows: list[dict], generated_at: str, model: str) -> None:
    lines: list[str] = []
    lines.append("# SNAQ nutrition verification report\n")
    lines.append(f"- Generated: `{generated_at}`")
    lines.append(f"- Model: `{model}`\n")

    lines.append("## Summary\n")
    lines.append("| # | Item | Status | Confidence |")
    lines.append("|---|------|--------|------------|")
    for i, row in enumerate(rows, 1):
        res = row["result"]
        badge = _STATUS_BADGE.get(res["status"], "")
        lines.append(
            f"| {i} | `{row['item']['id']}` — {row['item']['name']} "
            f"| {badge} {res['status']} | {res['confidence']:.2f} |"
        )
    lines.append("")

    for row in rows:
        if row["result"]["status"] == "VERIFIED":
            continue
        lines.extend(_markdown_detail(row))

    (out_dir / "report.md").write_text("\n".join(lines))


def _markdown_detail(row: dict) -> list[str]:
    item, res = row["item"], row["result"]
    out = [f"\n## {item['name']}  _(`{item['id']}`)_\n"]
    out.append(f"- Status: **{res['status']}**")
    out.append(f"- Confidence: **{res['confidence']:.2f}**")
    if res.get("error"):
        out.append(f"- Error: `{res['error']}`")
    out.append(f"- Reasoning: {res.get('reasoning', '')}")

    if res.get("sources"):
        out.append("\n### Sources\n")
        for s in res["sources"]:
            url = s.get("url") or ""
            out.append(f"- {s['source']} `{s['source_id']}` — {url}")

    if res.get("discrepancies"):
        out.append("\n### Field deltas\n")
        out.append("| Field | Provided | Reference | Δ | Exceeds |")
        out.append("|-------|----------|-----------|---|---------|")
        for d in res["discrepancies"]:
            delta = d["delta_fraction"]
            delta_str = f"{delta:+.1%}" if delta is not None else "—"
            out.append(
                f"| {d['field']} | {d['provided']} | {d['reference']} "
                f"| {delta_str} | {'yes' if d['exceeds_tolerance'] else 'no'} |"
            )

    if res.get("proposed_correction"):
        out.append("\n### Proposed correction\n")
        out.append("```json")
        out.append(json.dumps(res["proposed_correction"], indent=2))
        out.append("```")

    return out

"""Report rendering: JSON, Markdown, and a self-contained HTML page."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, select_autoescape

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

    ``formats`` accepts any combination of ``json``, ``md``, ``html``.
    """
    generated_at = datetime.now(UTC).isoformat()
    items_by_id = {i.id: i for i in items}
    rows = [_build_row(items_by_id[r.item_id], r, traces.get(r.item_id, [])) for r in results]

    if "json" in formats:
        _write_json(out_dir, rows, generated_at, model_deployment)
    if "md" in formats:
        _write_markdown(out_dir, rows, generated_at, model_deployment)
    if "html" in formats:
        _write_html(out_dir, rows, generated_at, model_deployment)


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


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>SNAQ verification report</title>
  <style>
    body { font: 14px/1.5 system-ui, sans-serif; margin: 2rem; color: #222; }
    h1 { margin-bottom: 0.25rem; }
    .meta { color: #666; font-size: 12px; margin-bottom: 1.5rem; }
    table { border-collapse: collapse; width: 100%; margin-bottom: 2rem; }
    th, td { border: 1px solid #ddd; padding: 0.4rem 0.6rem;
             text-align: left; vertical-align: top; }
    th { background: #f5f5f5; }
    .status { font-weight: 600; }
    .VERIFIED { color: #117a3d; }
    .DISCREPANCY { color: #a65b00; }
    .HIGH_VARIANCE { color: #5a4b99; }
    .INCONCLUSIVE { color: #777; }
    .ERROR { color: #b00020; }
    details { margin: 0.5rem 0 1rem; }
    summary { cursor: pointer; font-weight: 600; }
    code, pre { background: #f7f7f7; padding: 0.1rem 0.3rem; border-radius: 3px; }
    pre { padding: 0.5rem; overflow-x: auto; }
  </style>
</head>
<body>
  <h1>SNAQ nutrition verification report</h1>
  <div class="meta">Generated {{ generated_at }} · model <code>{{ model }}</code></div>

  <table>
    <thead><tr><th>#</th><th>Item</th><th>Status</th><th>Confidence</th><th>Reasoning</th></tr></thead>
    <tbody>
    {% for row in rows %}
      <tr>
        <td>{{ loop.index }}</td>
        <td><code>{{ row.item.id }}</code><br>{{ row.item.name }}</td>
        <td class="status {{ row.result.status }}">{{ row.result.status }}</td>
        <td>{{ '%.2f' % row.result.confidence }}</td>
        <td>{{ row.result.reasoning }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>

  {% for row in rows %}
    {% if row.result.status != 'VERIFIED' %}
      <details>
        <summary>{{ row.item.name }} — {{ row.result.status }}</summary>
        {% if row.result.discrepancies %}
          <table>
            <thead><tr><th>Field</th><th>Provided</th><th>Reference</th><th>Δ</th><th>Exceeds</th></tr></thead>
            <tbody>
            {% for d in row.result.discrepancies %}
              <tr>
                <td>{{ d.field }}</td>
                <td>{{ d.provided }}</td>
                <td>{{ d.reference }}</td>
                <td>
                  {%- if d.delta_fraction is not none -%}
                    {{ '%+.1f%%' % (d.delta_fraction * 100) }}
                  {%- else -%}—{%- endif -%}
                </td>
                <td>{{ 'yes' if d.exceeds_tolerance else 'no' }}</td>
              </tr>
            {% endfor %}
            </tbody>
          </table>
        {% endif %}
        {% if row.result.sources %}
          <p><strong>Sources:</strong>
          {% for s in row.result.sources %}
            <a href="{{ s.url or '#' }}">{{ s.source }} {{ s.source_id }}</a>
            {%- if not loop.last %}, {% endif %}
          {% endfor %}
          </p>
        {% endif %}
        {% if row.result.proposed_correction %}
          <p><strong>Proposed correction:</strong></p>
          <pre>{{ row.result.proposed_correction | tojson(indent=2) }}</pre>
        {% endif %}
        {% if row.result.error %}
          <p><strong>Error:</strong> <code>{{ row.result.error }}</code></p>
        {% endif %}
      </details>
    {% endif %}
  {% endfor %}
</body>
</html>
"""


def _write_html(out_dir: Path, rows: list[dict], generated_at: str, model: str) -> None:
    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    template = env.from_string(_HTML_TEMPLATE)
    html = template.render(rows=rows, generated_at=generated_at, model=model)
    (out_dir / "report.html").write_text(html)

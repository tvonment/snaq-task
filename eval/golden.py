"""Hand-labelled golden expectations for the bundled food_items.json.

Purpose: a *cheap* structural eval that reads an existing
``outputs/report.json`` and asserts per-item status (and confidence
bands) against what a nutritionist would expect. Not a full eval
harness -- the judge in :mod:`eval.judge` covers the qualitative side.

Run:
    uv run python -m eval.golden outputs/report.json

Exits non-zero on any regression so it's usable from CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

Status = Literal["VERIFIED", "DISCREPANCY", "HIGH_VARIANCE", "INCONCLUSIVE", "ERROR"]


# Each rule: allowed statuses (agent has some freedom), plus an optional
# minimum confidence when the status is in the "confident" set.
_EXPECTATIONS: dict[str, dict] = {
    "chicken-breast-raw": {
        "allowed": {"VERIFIED", "DISCREPANCY"},
        "min_confidence": 0.6,
    },
    "banana-raw": {
        "allowed": {"VERIFIED", "DISCREPANCY"},
        "min_confidence": 0.6,
    },
    "broccoli-raw": {
        "allowed": {"VERIFIED", "DISCREPANCY"},
        "min_confidence": 0.6,
    },
    "whole-milk": {
        # With CIQUAL bundled, we should now find a real reference for milk.
        "allowed": {"VERIFIED", "DISCREPANCY", "HIGH_VARIANCE"},
        "min_confidence": 0.4,
    },
    "egg-whole-raw": {
        "allowed": {"VERIFIED", "DISCREPANCY"},
        "min_confidence": 0.6,
    },
    "almonds-raw": {
        "allowed": {"VERIFIED", "DISCREPANCY"},
        "min_confidence": 0.6,
    },
    "oats-rolled-dry": {
        "allowed": {"VERIFIED", "DISCREPANCY"},
        "min_confidence": 0.6,
    },
    "avocado-raw": {
        # Avocado is in the variance catalogue; either reading is fine.
        "allowed": {"VERIFIED", "DISCREPANCY", "HIGH_VARIANCE"},
        "min_confidence": 0.4,
    },
    "salmon-atlantic-farmed-raw": {
        # Farmed salmon *must* resolve to HIGH_VARIANCE, not DISCREPANCY.
        "allowed": {"HIGH_VARIANCE"},
        "min_confidence": 0.4,
    },
    "fage-total-0-greek-yogurt": {
        # Branded, so confidence tops out at 0.6-0.8.
        "allowed": {"VERIFIED", "DISCREPANCY", "INCONCLUSIVE"},
        "min_confidence": 0.0,
    },
    "white-bread": {
        "allowed": {"VERIFIED", "DISCREPANCY"},
        "min_confidence": 0.4,
    },
}


def check(report_path: Path) -> list[str]:
    """Return a list of human-readable failure messages (empty = pass)."""
    doc = json.loads(report_path.read_text())
    failures: list[str] = []
    seen: set[str] = set()
    for row in doc.get("items", []):
        result = row["result"]
        item_id = result["item_id"]
        seen.add(item_id)
        expect = _EXPECTATIONS.get(item_id)
        if expect is None:
            continue
        status = result["status"]
        if status not in expect["allowed"]:
            failures.append(
                f"[{item_id}] status {status!r} not in allowed {sorted(expect['allowed'])}"
            )
        if result["confidence"] < expect["min_confidence"] - 1e-9:
            failures.append(
                f"[{item_id}] confidence {result['confidence']:.2f} "
                f"< minimum {expect['min_confidence']:.2f}"
            )
    missing = set(_EXPECTATIONS) - seen
    for mid in sorted(missing):
        failures.append(f"[{mid}] expected item missing from report")
    return failures


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m eval.golden <report.json>", file=sys.stderr)
        return 2
    failures = check(Path(argv[1]))
    if failures:
        print("GOLDEN FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"GOLDEN PASS: {len(_EXPECTATIONS)} items match expected statuses")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

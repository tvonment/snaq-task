"""Named constants for tolerances and thresholds.

Centralised so the review process can audit every magic number in one place.
"""

from __future__ import annotations

# Macro consistency (4/4/9 rule)
# calories_kcal ≈ protein_g * KCAL_PER_G_PROTEIN
#                + carbs_g   * KCAL_PER_G_CARB
#                + fat_g     * KCAL_PER_G_FAT
KCAL_PER_G_PROTEIN: float = 4.0
KCAL_PER_G_CARB: float = 4.0
KCAL_PER_G_FAT: float = 9.0

# A computed-vs-stated kcal deviation within this fraction is "consistent".
# Real foods include alcohol, polyols, organic acids etc. that the 4/4/9 rule
# doesn't capture, so a loose band avoids false positives.
MACRO_CONSISTENCY_TOLERANCE: float = 0.10  # ±10 %

# Per-field discrepancy thresholds (fraction of reference value).
CALORIES_TOLERANCE: float = 0.10  # ±10 %
MACRO_TOLERANCE: float = 0.15     # ±15 % for protein / carbs / fat / sugar / fiber
SODIUM_TOLERANCE: float = 0.25    # ±25 % — sodium varies a lot by preparation

# Confidence rubric (see DESIGN.md §4.5).
CONFIDENCE_TWO_SOURCES_AGREE: float = 1.0
CONFIDENCE_AUTHORITATIVE_SINGLE: float = 0.8
CONFIDENCE_BRANDED_SINGLE: float = 0.6
CONFIDENCE_PARTIAL_OR_VARIANCE: float = 0.4
CONFIDENCE_NONE: float = 0.0

# Minimum confidence at which a correction may be auto-applied.
DEFAULT_MIN_CORRECTION_CONFIDENCE: float = 0.8

# HTTP
EXTERNAL_HTTP_TIMEOUT_S: float = 10.0
HTTP_RETRY_ATTEMPTS: int = 3

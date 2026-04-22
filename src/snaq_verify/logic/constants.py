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

# Minimum Jaccard token overlap between a search query and a USDA match
# description for the match to be considered relevant. Below this,
# USDAClient.search treats the hit as a miss.
USDA_RELEVANCE_MIN_JACCARD: float = 0.2

# Confidence rubric (see DESIGN.md §4.5).
CONFIDENCE_TWO_SOURCES_AGREE: float = 1.0
CONFIDENCE_AUTHORITATIVE_SINGLE: float = 0.8
CONFIDENCE_BRANDED_SINGLE: float = 0.6
CONFIDENCE_PARTIAL_OR_VARIANCE: float = 0.4
CONFIDENCE_NONE: float = 0.0

# When the matched reference is incomplete (zero kcal, or two+ core macros
# missing) we cap confidence here no matter how authoritative the source is.
CONFIDENCE_INCOMPLETE_REFERENCE_CAP: float = 0.6

# Minimum confidence at which a correction may be auto-applied.
DEFAULT_MIN_CORRECTION_CONFIDENCE: float = 0.8

# HTTP
EXTERNAL_HTTP_TIMEOUT_S: float = 10.0
HTTP_RETRY_ATTEMPTS: int = 3

# Open Food Facts is a volunteer-run, free service and aggressively rate-limits
# bursts. We retry more patiently and cap per-client concurrency.
OFF_RETRY_ATTEMPTS: int = 5
OFF_BACKOFF_INITIAL_S: float = 1.0
OFF_BACKOFF_MAX_S: float = 30.0
OFF_MAX_CONCURRENCY: int = 2
# Upper bound on how long we'll honor a server-provided Retry-After.
RETRY_AFTER_CAP_S: float = 60.0

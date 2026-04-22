"""Tiny helpers shared between HTTP clients."""

from __future__ import annotations

from email.utils import parsedate_to_datetime
from datetime import UTC, datetime


def parse_retry_after(value: str | None, *, cap_s: float) -> float | None:
    """Parse an HTTP ``Retry-After`` header value into seconds.

    Accepts either a delta-seconds integer or an HTTP-date. Returns ``None``
    when the header is absent or unparseable. Clamps the result to ``cap_s``
    so a misbehaving server can't stall us indefinitely.
    """
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    if seconds <= 0:
        return 0.0
    return min(seconds, cap_s)

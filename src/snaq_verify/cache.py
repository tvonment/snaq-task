"""On-disk response cache for external HTTP lookups.

Keyed on ``(source, query_hash)``. Values are the JSON string of the
normalized :class:`~snaq_verify.models.NutritionReference`, or the
sentinel ``__NONE__`` for "we looked and found nothing".

Caching "no match" is intentional: USDA+OFF negatives are stable enough
for the lifetime of a run, and it avoids hammering the APIs during test
reruns.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Literal

from snaq_verify.models import NutritionReference

Source = Literal["USDA", "OpenFoodFacts", "CIQUAL"]

_NONE_SENTINEL = "__NONE__"


def _hash_query(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


class ResponseCache:
    """Thin wrapper over a SQLite file with ``(source, query_hash)`` keys."""

    def __init__(self, path: Path) -> None:
        """Open the cache file, creating parent dirs and schema as needed."""
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS responses (
                source TEXT NOT NULL,
                query_hash TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (source, query_hash)
            )
            """
        )
        self._conn.commit()

    def get(self, source: Source, query: str) -> NutritionReference | None | _Miss:
        """Return a cached value, ``None`` for a cached miss, or ``_MISS``."""
        row = self._conn.execute(
            "SELECT payload FROM responses WHERE source = ? AND query_hash = ?",
            (source, _hash_query(query)),
        ).fetchone()
        if row is None:
            return _MISS
        payload = row[0]
        if payload == _NONE_SENTINEL:
            return None
        return NutritionReference.model_validate_json(payload)

    def set(self, source: Source, query: str, value: NutritionReference | None) -> None:
        """Store a reference (or ``None`` for "no match")."""
        payload = _NONE_SENTINEL if value is None else value.model_dump_json()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO responses (source, query_hash, payload)
            VALUES (?, ?, ?)
            """,
            (source, _hash_query(query), payload),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()


class _Miss:
    """Sentinel type: cache has never seen this key."""


_MISS = _Miss()
"""Singleton returned by :meth:`ResponseCache.get` on a true miss."""

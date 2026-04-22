"""Open Food Facts client.

Only one operation: barcode lookup. Product search by name is noisy and
low-signal for our purposes; we rely on USDA for generic foods.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from snaq_verify.clients._retry import parse_retry_after
from snaq_verify.logic.constants import (
    EXTERNAL_HTTP_TIMEOUT_S,
    OFF_BACKOFF_INITIAL_S,
    OFF_BACKOFF_MAX_S,
    OFF_MAX_CONCURRENCY,
    OFF_RETRY_ATTEMPTS,
    RETRY_AFTER_CAP_S,
)
from snaq_verify.models import NutritionPer100g, NutritionReference, SourceCitation

OFF_BASE_URL = "https://world.openfoodfacts.org"

_LOG = logging.getLogger(__name__)


class _RetryableHTTPError(Exception):
    """Marker raised for 429/5xx so tenacity retries without catching 4xx."""

    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


def _log_off_retry(retry_state: RetryCallState) -> None:
    """Emit a concise WARNING on each retry attempt."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if exc is None:
        return
    wait_s = retry_state.next_action.sleep if retry_state.next_action else 0.0
    _LOG.warning(
        "OFF request failed (%s); retrying in %.1fs (attempt %d/%d)",
        exc,
        wait_s,
        retry_state.attempt_number,
        OFF_RETRY_ATTEMPTS,
    )


class OpenFoodFactsClient:
    """Async client for Open Food Facts."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        max_concurrency: int = OFF_MAX_CONCURRENCY,
    ) -> None:
        """OFF has no API key; inject a client in tests or build a default."""
        self._client = client or httpx.AsyncClient(
            timeout=EXTERNAL_HTTP_TIMEOUT_S,
            headers={"User-Agent": "snaq-verify/0.1 (evaluation task)"},
        )
        self._owns_client = client is None
        # Cap in-flight OFF requests to stay polite regardless of global
        # verification concurrency.
        self._sem = asyncio.Semaphore(max_concurrency)

    async def __aenter__(self) -> OpenFoodFactsClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    @retry(
        reraise=True,
        stop=stop_after_attempt(OFF_RETRY_ATTEMPTS),
        wait=wait_exponential_jitter(
            initial=OFF_BACKOFF_INITIAL_S, max=OFF_BACKOFF_MAX_S
        ),
        retry=retry_if_exception_type((httpx.TimeoutException, _RetryableHTTPError)),
        before_sleep=_log_off_retry,
    )
    async def _get(self, path: str) -> httpx.Response:
        async with self._sem:
            response = await self._client.get(f"{OFF_BASE_URL}{path}")
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = parse_retry_after(
                response.headers.get("Retry-After"), cap_s=RETRY_AFTER_CAP_S
            )
            # Honor the server's hint inline; tenacity's jittered backoff
            # then adds additional spacing on top.
            if retry_after is not None and retry_after > 0:
                await asyncio.sleep(retry_after)
            raise _RetryableHTTPError(
                f"OFF returned {response.status_code}: {response.text[:200]}",
                retry_after_s=retry_after,
            )
        return response

    async def lookup_by_barcode(self, barcode: str) -> NutritionReference | None:
        """Fetch a product by barcode and return normalized nutrition, or ``None``.

        OFF responds with ``{"status": 0}`` for unknown barcodes — that's a
        miss, not an error.
        """
        response = await self._get(f"/api/v2/product/{barcode}.json")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != 1:
            return None
        product = payload.get("product") or {}
        return _normalize_off_product(product, barcode)


def _pick(nutriments: dict[str, Any], *keys: str) -> float | None:
    """Return the first numeric value among ``keys`` in ``nutriments``."""
    for key in keys:
        value = nutriments.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _normalize_off_product(product: dict[str, Any], barcode: str) -> NutritionReference:
    """Convert an OFF product payload to a :class:`NutritionReference`.

    OFF stores per-100 g values in ``nutriments`` with ``_100g`` suffix.
    Energy is in kJ by default; we prefer ``energy-kcal_100g`` when
    available and fall back to converting kJ -> kcal (1 kcal = 4.184 kJ).
    """
    nutriments: dict[str, Any] = product.get("nutriments") or {}

    calories = _pick(nutriments, "energy-kcal_100g", "energy-kcal")
    if calories is None:
        kj = _pick(nutriments, "energy_100g", "energy-kj_100g")
        if kj is not None:
            calories = kj / 4.184

    nutrition = NutritionPer100g(
        calories_kcal=calories if calories is not None else 0.0,
        protein_g=_pick(nutriments, "proteins_100g") or 0.0,
        fat_g=_pick(nutriments, "fat_100g") or 0.0,
        saturated_fat_g=_pick(nutriments, "saturated-fat_100g"),
        carbohydrates_g=_pick(nutriments, "carbohydrates_100g") or 0.0,
        sugar_g=_pick(nutriments, "sugars_100g"),
        fiber_g=_pick(nutriments, "fiber_100g"),
        sodium_mg=(
            _pick(nutriments, "sodium_100g") * 1000.0
            if _pick(nutriments, "sodium_100g") is not None
            else None
        ),
    )

    name = (
        product.get("product_name")
        or product.get("product_name_en")
        or product.get("generic_name")
        or ""
    )
    citation = SourceCitation(
        source="OpenFoodFacts",
        source_id=barcode,
        url=f"https://world.openfoodfacts.org/product/{barcode}",
        data_type=None,
        retrieved_at=datetime.now(UTC),
    )
    return NutritionReference(
        nutrition=nutrition,
        citation=citation,
        match_name=name,
        match_notes=None,
    )

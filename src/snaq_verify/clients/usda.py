"""USDA FoodData Central client.

Two operations:
- ``search(name, data_type)`` -> top match as :class:`NutritionReference`
- ``get(fdc_id)``             -> direct fetch by ID

We deliberately avoid the Branded dataset for generic queries; it's the
single biggest source of wrong matches. See DESIGN.md section 4.3.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from snaq_verify.clients._retry import parse_retry_after
from snaq_verify.logic.constants import (
    EXTERNAL_HTTP_TIMEOUT_S,
    HTTP_RETRY_ATTEMPTS,
    KCAL_PER_G_CARB,
    KCAL_PER_G_FAT,
    KCAL_PER_G_PROTEIN,
    RETRY_AFTER_CAP_S,
    USDA_RELEVANCE_MIN_JACCARD,
)
from snaq_verify.models import (
    NutritionPer100g,
    NutritionReference,
    SourceCitation,
    USDADataType,
)

USDA_BASE_URL = "https://api.nal.usda.gov/fdc/v1"

# USDA `nutrientId` values for the fields we care about.
# Reference: https://fdc.nal.usda.gov/portal-data/external/dataDictionary
_NUTRIENT_IDS: dict[str, int] = {
    "calories_kcal": 1008,
    "protein_g": 1003,
    "fat_g": 1004,
    "saturated_fat_g": 1258,
    "carbohydrates_g": 1005,
    "sugar_g": 2000,
    "fiber_g": 1079,
    "sodium_mg": 1093,
}

# Energy (kJ); used as a fallback when the kcal nutrient (1008) is missing.
_NUTRIENT_ID_ENERGY_KJ: int = 1062
_KJ_PER_KCAL: float = 4.184

# Tokens ignored when computing query/match relevance.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "of", "in", "with", "without",
        "raw", "cooked", "fresh", "whole", "ns",
    }
)


class _RetryableHTTPError(Exception):
    """Marker raised for 429/5xx so tenacity retries without catching 4xx."""


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, httpx.TimeoutException | _RetryableHTTPError)


class USDAClient:
    """Async client for FoodData Central."""

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        """Use an injected ``httpx.AsyncClient`` in tests, else build our own."""
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=EXTERNAL_HTTP_TIMEOUT_S)
        self._owns_client = client is None

    async def __aenter__(self) -> USDAClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    @retry(
        reraise=True,
        stop=stop_after_attempt(HTTP_RETRY_ATTEMPTS),
        wait=wait_exponential_jitter(initial=0.5, max=4.0),
        retry=retry_if_exception_type((httpx.TimeoutException, _RetryableHTTPError)),
    )
    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send a request with retry on timeouts / 429 / 5xx."""
        params = dict(kwargs.pop("params", {}))
        params["api_key"] = self._api_key
        response = await self._client.request(
            method, f"{USDA_BASE_URL}{path}", params=params, **kwargs
        )
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = parse_retry_after(
                response.headers.get("Retry-After"), cap_s=RETRY_AFTER_CAP_S
            )
            if retry_after is not None and retry_after > 0:
                await asyncio.sleep(retry_after)
            raise _RetryableHTTPError(
                f"USDA returned {response.status_code}: {response.text[:200]}"
            )
        return response

    async def search(
        self,
        query: str,
        data_type: Literal["Foundation", "SR Legacy", "Branded"] = "Foundation",
    ) -> NutritionReference | None:
        """Search FDC by name, returning the top normalized match or ``None``.

        Applies a relevance gate: if the top result's description doesn't
        share enough non-stopword tokens with the query, it's treated as a
        miss. FDC's search is eager and will happily return "Crackers" for
        "Whole Milk"; we'd rather be inconclusive than wrong.
        """
        response = await self._request(
            "GET",
            "/foods/search",
            params={
                "query": query,
                "dataType": data_type,
                "pageSize": 1,
            },
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        foods = payload.get("foods") or []
        if not foods:
            return None
        food = foods[0]
        description = food.get("description") or food.get("lowercaseDescription") or ""
        if _relevance(query, description) < USDA_RELEVANCE_MIN_JACCARD:
            return None
        return _normalize_fdc_food(food, data_type)


def _tokenize(text: str) -> set[str]:
    """Lowercase, split on non-alpha, drop stopwords and single chars."""
    out: set[str] = set()
    current = []
    for ch in text.lower():
        if ch.isalpha():
            current.append(ch)
        else:
            if current:
                tok = "".join(current)
                if len(tok) > 1 and tok not in _STOPWORDS:
                    out.add(tok)
                current = []
    if current:
        tok = "".join(current)
        if len(tok) > 1 and tok not in _STOPWORDS:
            out.add(tok)
    return out


def _relevance(query: str, match_name: str) -> float:
    """Fraction of query tokens present in match tokens (recall).

    Jaccard penalises the common case where the match description is
    longer than the query (e.g. "chicken" vs "chicken, broiler..."),
    even when every query word is present. Recall is the right metric
    here: we want to filter out matches that fail to cover the query,
    not matches that add detail.
    """
    q = _tokenize(query)
    m = _tokenize(match_name)
    if not q:
        return 1.0
    if not m:
        return 0.0
    return len(q & m) / len(q)


def _nutrient_value(food: dict[str, Any], nutrient_id: int) -> float | None:
    """Extract a nutrient value by ID from a FDC food record.

    Handles both the /foods/search shape (``foodNutrients[].nutrientId``) and
    the /food/{id} shape (``foodNutrients[].nutrient.id``).
    """
    for n in food.get("foodNutrients") or ():
        nid = n.get("nutrientId")
        if nid is None:
            nid = (n.get("nutrient") or {}).get("id")
        if nid != nutrient_id:
            continue
        value = n.get("value")
        if value is None:
            value = n.get("amount")
        if value is not None:
            return float(value)
    return None


def _normalize_fdc_food(food: dict[str, Any], data_type: USDADataType) -> NutritionReference:
    """Convert an FDC ``food`` object to a :class:`NutritionReference`.

    FDC values are already per 100 g for Foundation / SR Legacy.
    If the kcal nutrient (1008) is missing we fall back to kJ (1062)
    converted via 1 kcal = 4.184 kJ, then to an Atwater-computed estimate
    from the macros. ``match_notes`` records which fallback was used.
    """
    values = {field: _nutrient_value(food, nid) for field, nid in _NUTRIENT_IDS.items()}
    # Required macros default to 0 when the record omits them.
    for required in ("protein_g", "fat_g", "carbohydrates_g"):
        if values[required] is None:
            values[required] = 0.0

    kcal_note: str | None = None
    if values["calories_kcal"] is None:
        kj = _nutrient_value(food, _NUTRIENT_ID_ENERGY_KJ)
        if kj is not None:
            values["calories_kcal"] = round(kj / _KJ_PER_KCAL, 2)
            kcal_note = "kcal derived from kJ (nutrient 1062)"
        else:
            values["calories_kcal"] = round(
                values["protein_g"] * KCAL_PER_G_PROTEIN
                + values["carbohydrates_g"] * KCAL_PER_G_CARB
                + values["fat_g"] * KCAL_PER_G_FAT,
                2,
            )
            kcal_note = "kcal derived from Atwater 4/4/9 (no energy field in record)"

    nutrition = NutritionPer100g(**values)  # type: ignore[arg-type]

    fdc_id = str(food.get("fdcId", ""))
    citation = SourceCitation(
        source="USDA",
        source_id=fdc_id,
        url=f"https://fdc.nal.usda.gov/food-details/{fdc_id}/nutrients" if fdc_id else None,
        data_type=data_type,
        retrieved_at=datetime.now(UTC),
    )
    return NutritionReference(
        nutrition=nutrition,
        citation=citation,
        match_name=food.get("description") or food.get("lowercaseDescription") or "",
        match_notes=kcal_note,
    )

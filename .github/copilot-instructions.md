# GitHub Copilot Instructions — SNAQ Nutrition Verification Agent

Read [DESIGN.md](../DESIGN.md) for the full rationale. This file is the
short, prescriptive version for code generation.

## Project overview

A single-package Python CLI that verifies nutrition data in
`food_items.json` against authoritative sources (USDA FoodData Central,
Open Food Facts) using a `pydantic-ai` agent with typed tools.

```
food_items.json
    ↓
runner  (asyncio.gather + Semaphore)
    ↓ per item
pydantic-ai Agent  (Azure OpenAI, temperature=0)
    ↓ typed tool calls
Tools: usda / openfoodfacts / validation / discrepancy / variance
    ↓
report.{json,md,html}   +   food_items.corrected.json (optional)
```

No frontend. No MCP server. No docker-compose. No SSE. One command.

---

## Repository layout

```
snaq-task/
├── .github/copilot-instructions.md   ← this file
├── DESIGN.md                         ← design rationale
├── README.md                         ← how to run, decisions, future work
├── food_items.json                   ← provided input
├── pyproject.toml                    ← uv-managed, Python 3.12+
├── .env.example
├── src/snaq_verify/
│   ├── __main__.py                   ← CLI entrypoint
│   ├── cli.py                        ← argparse / typer
│   ├── models.py                     ← all Pydantic models
│   ├── agent.py                      ← pydantic-ai Agent + tool registrations
│   ├── runner.py                     ← gather + semaphore orchestration
│   ├── clients/
│   │   ├── usda.py                   ← httpx + tenacity + cache
│   │   └── openfoodfacts.py
│   ├── logic/
│   │   ├── validation.py             ← pure: macro consistency
│   │   ├── discrepancy.py            ← pure: per-field deltas
│   │   ├── variance.py               ← pure: known-variance catalogue
│   │   └── normalization.py          ← USDA/OFF → NutritionReference
│   ├── cache.py                      ← SQLite response cache
│   └── report.py                     ← JSON + Markdown + static HTML
├── tests/
│   ├── test_validation.py            ← table-driven
│   ├── test_discrepancy.py
│   ├── test_variance.py
│   ├── test_normalization.py
│   ├── test_usda_client.py           ← respx
│   ├── test_off_client.py            ← respx
│   └── fixtures/                     ← API response samples
├── eval/
│   ├── golden.py                     ← expected statuses for sample items
│   └── judge.py                      ← LLM-as-judge over agent reasoning
└── outputs/                          ← generated reports (gitignored)
```

---

## Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.12+ |
| Dep manager | `uv` (pyproject.toml, no requirements.txt) |
| Agent | `pydantic-ai` |
| LLM | Azure OpenAI (Foundry), `temperature=0`, deployment from env |
| HTTP | `httpx.AsyncClient` (never `requests`, never `aiohttp`) |
| Retry | `tenacity` |
| Cache | stdlib `sqlite3`, keyed on `(source, query_hash)` |
| Parallelism | `asyncio.gather` + `asyncio.Semaphore` |
| CLI | `typer` (or stdlib `argparse` if we want zero extra deps) |
| Tests | `pytest`, `pytest-asyncio`, `respx` |
| Lint/format | `ruff` |

---

## Environment variables

Never hardcode. `.env.example` documents them all.

```
AZURE_OPENAI_ENDPOINT=https://your-endpoint.openai.azure.com/
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-5-mini
AZURE_OPENAI_API_VERSION=2024-10-21
USDA_API_KEY=...
MAX_CONCURRENT_VERIFICATIONS=5
CACHE_PATH=.cache/snaq.sqlite
LOG_LEVEL=INFO
```

---

## CLI contract

```
uv run snaq-verify food_items.json \
    [--out outputs/] \
    [--format json,md,html] \
    [--apply-corrections --min-confidence 0.8] \
    [--no-cache] \
    [--concurrency 5]
```

`--apply-corrections` emits `food_items.corrected.json` containing the
original items with accepted corrections merged in. Only corrections with
`confidence >= --min-confidence` are applied.

---

## Coding standards

### Python

- Python 3.12+. `from __future__ import annotations` at the top of every module.
- PEP 8 via `ruff`. No `# type: ignore` without a comment.
- All public functions and classes have docstrings.
- `pathlib.Path`, never `os.path`.
- All I/O-bound functions are `async`. No blocking calls in async code.
- Prefer Pydantic models over dicts for structured data. `.model_dump()`, never `.dict()`.
- No magic numbers — tolerances and thresholds are named constants in
  `logic/constants.py`.

### Pydantic models

- All shared models in `src/snaq_verify/models.py`.
- `Field(description=...)` on every field (the agent reads these).
- Use `model_validator` for cross-field invariants.
- Literal unions for statuses, not raw strings.

### pydantic-ai agent

- Single `Agent` instance, configured once at import.
- Output type = `VerificationResult`.
- Tools registered via `@agent.tool` with typed params and return values.
- System prompt is short, declarative, and lists the routing rules
  (barcode → OFF; generic → USDA Foundation/SR Legacy; known variance →
  `HIGH_VARIANCE`).
- The agent MUST NOT compute deltas or macro math itself — it calls
  `validate_macro_consistency` and `calculate_discrepancy`.

### Tool interface contract

```python
@agent.tool
async def lookup_usda_by_name(
    ctx: RunContext[Deps],
    name: str,
    category: str,
    data_type: Literal["Foundation", "SR Legacy", "Branded"] = "Foundation",
) -> NutritionReference | None:
    """Look up nutrition data from USDA FoodData Central by name."""

@agent.tool
async def lookup_off_by_barcode(
    ctx: RunContext[Deps], barcode: str,
) -> NutritionReference | None:
    """Look up nutrition data from Open Food Facts by barcode."""

@agent.tool
def validate_macro_consistency(
    nutrition: NutritionPer100g,
) -> MacroConsistencyResult:
    """Pure: protein*4 + carbs*4 + fat*9 ≈ calories, within tolerance."""

@agent.tool
def calculate_discrepancy(
    provided: NutritionPer100g,
    reference: NutritionPer100g,
) -> DiscrepancyReport:
    """Pure: per-field deltas and threshold flags."""

@agent.tool
def check_known_variance(
    name: str, category: str,
) -> VarianceInfo | None:
    """Pure: lookup in a small catalogue of naturally variable foods."""
```

### Clients

- Every external call has an explicit `timeout=10.0`.
- `tenacity`: 3 attempts, exponential backoff with jitter, retry on
  `httpx.TimeoutException`, `httpx.HTTPStatusError` for 429/5xx.
- Cache wraps the client, not the tool — the cache is source-agnostic.
- On 404 / no match: return `None`, never raise.
- Normalize to `NutritionReference` before returning. Raw API payloads
  never leak past the client layer.

### Error handling

- Never swallow. Log with context, then re-raise or return a structured error.
- Tool exceptions become `VerificationResult(status="ERROR", ...)` at the
  runner level, not the tool level.
- `asyncio.gather(..., return_exceptions=True)` in `runner.py`.

### Report

- `report.json`: full trace including every tool call (args, result, latency).
- `report.md`: human-readable table, one row per item, plus a details
  section per non-`VERIFIED` item.
- `report.html` (optional, default on): single self-contained file built
  from a Jinja2 template. No external CSS/JS, no build step.

---

## Test-driven development

Write tests alongside implementation.

**Highest priority (pure logic):**
- `validate_macro_consistency`: pass / fail / borderline, table-driven.
- `calculate_discrepancy`: delta math, threshold flags, missing fields.
- `normalization`: USDA Foundation, SR Legacy, Branded → `NutritionReference`;
  OFF payload → `NutritionReference`.
- `check_known_variance`: catalogue hits and misses.

**Clients (`respx`):**
- 200 happy path, 404 no match, 429 rate limit → retry, timeout → retry → fail,
  malformed payload → structured error.

**Runner:**
- 10 items complete in parallel within reasonable time.
- One throwing item does not kill the others.

**Agent (golden set):**
- Runs with cache populated from fixtures, not live APIs.
- Asserts status + confidence band per sample item.

Test names ARE the documentation:

```python
def test_macro_consistency_fails_when_calories_deviate_more_than_10_percent(): ...
def test_usda_client_returns_none_on_404(): ...
def test_farmed_salmon_is_high_variance_not_discrepancy(): ...
```

---

## What NOT to do

- No `requirements.txt` — `pyproject.toml` + `uv`.
- No `requests`, no `aiohttp` — `httpx.AsyncClient` only.
- No blocking I/O inside `async` functions.
- No hardcoded API keys, URLs, model names, or tolerances.
- No MCP server, no FastAPI, no SSE, no React, no docker-compose. If you
  think you need one of these, you don't — re-read [DESIGN.md](../DESIGN.md).
- No validation logic inside the agent / LLM path — it belongs in pure
  functions under `logic/`.
- No unnecessary abstractions. A 20-line module that does one thing is
  better than a 5-file package of interfaces and factories.
- No swallowed exceptions. No `except Exception: pass`.
- No comments or docstrings on code you didn't change.

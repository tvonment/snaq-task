# GitHub Copilot Instructions — SNAQ Nutrition Verification Agent

Read [home_task.md](../home_task.md) FIRST. Before adding any feature,
re-read it. If it isn't asked for there, it probably shouldn't exist.
The task explicitly values focused, deliberate work and warns against
over-engineering — bias toward removing scope, not adding it.

Read [DESIGN.md](../DESIGN.md) for the full rationale. This file is the
short, prescriptive version for code generation.

## Project overview

A single-package Python CLI that verifies nutrition data in
`food_items.json` against authoritative sources (USDA FoodData Central,
Open Food Facts, ANSES CIQUAL) using a `pydantic-ai` agent with typed
tools. Plus a separate `judge` subcommand (LLM-as-judge) and a
`stability` subcommand (sweep `reasoning_effort` × K runs).

```
food_items.json
    ↓
runner  (asyncio.gather + Semaphore, per-task try/except)
    ↓ per item
pydantic-ai Agent  (Azure AI Foundry v1 API, gpt-5-mini)
    ↓ typed tool calls
Tools: usda / openfoodfacts / ciqual / validation / discrepancy /
       variance / semantics / completeness
    ↓
report.{json,md}
```

No frontend. No MCP server. No docker-compose. No SSE. No response
cache — if a query varies across runs that's a real signal from the
agent, not noise to mask. One command.

---

## Repository layout

```
snaq-task/
├── .github/copilot-instructions.md   ← this file
├── DESIGN.md                         ← design rationale
├── README.md                         ← how to run, decisions, future work
├── NARRATIVE.md                      ← working diary / AI session retrospective
├── food_items.json                   ← provided input
├── pyproject.toml                    ← uv-managed, Python 3.12+
├── .env.example
├── data/
│   ├── ciqual_subset.json            ← bundled CIQUAL subset (11 items)
│   └── CIQUAL_LICENSE.md             ← attribution
├── src/snaq_verify/
│   ├── __main__.py                   ← CLI entrypoint
│   ├── cli.py                        ← typer (verify / judge / stability)
│   ├── config.py                     ← env-loaded Settings
│   ├── models.py                     ← all Pydantic models
│   ├── agent.py                      ← build_agent factory + tool registrations + INSTRUCTIONS_VERSION
│   ├── runner.py                     ← gather + semaphore + per-task try/except
│   ├── report.py                     ← JSON + Markdown writers
│   ├── clients/
│   │   ├── _retry.py                 ← shared tenacity config (Retry-After honoured)
│   │   ├── usda.py                   ← FDC search + relevance gate + kcal fallback
│   │   ├── openfoodfacts.py          ← per-client polite semaphore
│   │   └── ciqual.py                 ← bundled subset, alias matching
│   └── logic/
│       ├── validation.py             ← pure: macro consistency
│       ├── discrepancy.py            ← pure: per-field deltas
│       ├── variance.py               ← pure: known-variance catalogue
│       ├── completeness.py           ← pure: is reference record trustworthy?
│       ├── semantics.py              ← pure: cross-source definitional mismatches
│       └── constants.py              ← named tolerances, no magic numbers
├── tests/
│   ├── test_validation.py            ← pure logic, table-driven
│   ├── test_discrepancy.py
│   ├── test_variance.py
│   ├── test_completeness.py
│   ├── test_semantics.py
│   ├── test_reasoning.py             ← digit-free reasoning validators
│   ├── test_instructions.py          ← v2 instruction rules
│   ├── test_judge_concerns.py        ← typed judge concern enum
│   ├── test_stability.py             ← stability aggregator
│   ├── test_smoke.py                 ← CLI help + flag wiring
│   ├── test_usda_client.py           ← respx
│   ├── test_off_client.py            ← respx (incl. Retry-After on 429)
│   ├── test_usda_relevance.py        ← FDC relevance gate + kcal fallback
│   ├── test_ciqual_client.py
│   └── fixtures/                     ← API response samples
├── eval/
│   ├── judge.py                      ← LLM-as-judge over agent reasoning + trace
│   └── stability.py                  ← sweep reasoning_effort × K runs; aggregate matrix
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
| Parallelism | `asyncio.gather` + `asyncio.Semaphore` |
| CLI | `typer` |
| Tests | `pytest`, `pytest-asyncio`, `respx` |
| Lint/format | `ruff` |

---

## Environment variables

Never hardcode. `.env.example` documents them all.

```
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/openai/v1/
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-5-mini
AZURE_OPENAI_JUDGE_DEPLOYMENT=gpt-5-chat   # optional; falls back to deployment
USDA_API_KEY=...
MAX_CONCURRENT_VERIFICATIONS=5
LOG_LEVEL=INFO
```

Foundry's 2026 v1 path (`/openai/v1/`) does NOT accept `?api-version=...`,
so the agent uses plain `openai.AsyncOpenAI` with the Foundry endpoint as
`base_url`, NOT `AsyncAzureOpenAI`. See [src/snaq_verify/agent.py](../src/snaq_verify/agent.py).

---

## CLI contract

```
uv run snaq-verify verify food_items.json \
    [--out outputs/] [--format json,md] [--concurrency 5] \
    [--reasoning-effort minimal|low|medium|high] [-v|-vv]

uv run snaq-verify judge outputs/report.json \
    [--out outputs/judge.json] [--concurrency 3]

uv run snaq-verify stability food_items.json \
    [--runs 3] [--efforts minimal,low,medium,high] [--no-judge]
```

Corrections live inside `report.json` (per-field, with `reference` +
`confidence`). There is no auto-apply flag — merging corrections back
into product data is a human-review step, not a confidence-threshold
gamble.

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

- Single `build_agent(settings, reasoning_effort)` factory in
  [src/snaq_verify/agent.py](../src/snaq_verify/agent.py); the runner
  calls it once per run so `reasoning_effort` and Azure settings are
  injected without import-time globals.
- Output type = `VerificationResult`.
- Tools registered via `@agent.tool` with typed params and return values.
- System prompt is short and neutrally phrased (Foundry's Prompt
  Shields treats assertive "You MUST NOT..." as jailbreak attempts).
  Detailed routing/rubric rules live in `INSTRUCTIONS`, versioned via
  `INSTRUCTIONS_VERSION` so stability sweeps can compare prompt
  revisions.
- The agent MUST NOT compute deltas or macro math itself — it calls
  `validate_macro_consistency` and `calculate_discrepancy`.

### Tool interface contract

```python
# Lookups (network or bundled)
async def lookup_usda_by_name(ctx, name, category,
    data_type: Literal["Foundation", "SR Legacy", "Branded"] = "Foundation",
) -> NutritionReference | None: ...
async def lookup_off_by_barcode(ctx, barcode: str) -> NutritionReference | None: ...
async def lookup_ciqual_by_name(ctx, name: str, category: str) -> NutritionReference | None: ...

# Pure logic — the agent MUST call these instead of doing arithmetic.
def validate_macro_consistency(nutrition: NutritionPer100g) -> MacroConsistencyResult: ...
def calculate_discrepancy(provided, reference) -> DiscrepancyReport: ...
def assess_reference_completeness(reference) -> ReferenceCompletenessResult: ...
def check_known_variance(name: str, category: str) -> VarianceInfo | None: ...
def compare_semantics(source_a: SourceName, source_b: SourceName) -> SemanticsComparison: ...
```

Every tool call is recorded in `Deps.trace` as a `ToolCall` with full
`result_payload` for lookups so the LLM-as-judge can verify proposed
corrections against the records the agent actually saw.

### Clients

- Every external call has an explicit `timeout=10.0`.
- `tenacity`: 3 attempts, exponential backoff with jitter, retry on
  `httpx.TimeoutException`, `httpx.HTTPStatusError` for 429/5xx.
- On 404 / no match: return `None`, never raise.
- Normalize to `NutritionReference` before returning. Raw API payloads
  never leak past the client layer.
- No response cache. If a source returns different data across runs,
  that's signal the agent queried it differently — the stability matrix
  surfaces it instead of hiding it.

### Error handling

- Never swallow. Log with context, then re-raise or return a structured error.
- Each per-item task in `runner.py` wraps the agent call in `try/except`
  and converts failures to `VerificationResult(status="ERROR", ...)` —
  one bad item never kills the batch.
- Tool exceptions become `VerificationResult(status="ERROR", ...)` at the
  runner level, not the tool level.

### Report

- `report.json`: full trace including every tool call (args, result, latency).
- `report.md`: human-readable table, one row per item, plus a details
  section per non-`VERIFIED` item.

---

## Test-driven development

Write tests alongside implementation.

**Highest priority (pure logic):**
- `validate_macro_consistency`: pass / fail / borderline, table-driven.
- `calculate_discrepancy`: delta math, threshold flags, near-zero floor,
  honest low-value ratios, missing fields.
- `assess_reference_completeness`: zero-kcal, missing core macros,
  optional saturated fat does NOT trigger.
- `compare_semantics`: USDA↔CIQUAL carbs/energy notes, OFF↔USDA sodium
  vs salt, order-independent, same-source returns empty.
- `check_known_variance`: catalogue hits and misses, category guard.
- `VerificationReasoning` validator: rejects digits in prose.

**Clients (`respx`):**
- 200 happy path, 404 no match, 429 rate limit → retry, timeout → retry → fail,
  malformed payload → structured error.

**Runner:**
- 10 items complete in parallel within reasonable time.
- One throwing item does not kill the others.

**Meta-eval (stability matrix):**
- `eval/stability.py` runs verify + judge K times and aggregates
  per-item agreement (modal status, confidence stdev, judge `grounded`
  agreement, Jaccard on concern-kind sets). This replaces the previous
  golden/metrics scaffolding — at n=11 with no hand-labelled ground
  truth, measuring the agent's *consistency* is more honest than
  pretending to measure correctness.
- Aggregator is pure-Python and unit-tested with hand-crafted fake
  verify/judge JSONs; no LLM in the test loop.

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

# Design — SNAQ Nutrition Verification Agent

## 1. Problem

Given a list of food items with a stated nutrition profile, verify whether
that profile is plausible, flag discrepancies against authoritative sources,
and — when confident — propose corrections. Input is a JSON file; output is
a structured report.

The brief's explicit guidance: *"A simple, well-reasoned solution beats a
complex one that you can't fully explain."* This design follows that.

## 2. Scope decisions (what we're NOT building, and why)

| Rejected | Reason |
|---|---|
| React/Vite frontend with upload + review UI | Input is a file; reviewers read README → run code → read code. UI adds scope without signal on agent design. Productization is described in the README instead. |
| Separate MCP server over SSE transport | All tools are local Python. `pydantic-ai` native tools give the same "agent decides which tool to call" loop in-process with clearer stack traces. MCP is a natural future extraction, not a present need. |
| docker-compose / multi-service | One CLI, one `uv run`. Minimal setup is a stated requirement. |
| Streaming (SSE) partial results | Final deliverable is a report file; streaming adds complexity the evaluator doesn't consume. |

## 3. High-level architecture

```
food_items.json
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ runner.py                                               │
│   • load + validate input (Pydantic)                    │
│   • asyncio.gather over items, bounded by Semaphore(5)  │
└─────────────────────────────────────────────────────────┘
      │ per item
      ▼
┌─────────────────────────────────────────────────────────┐
│ pydantic-ai Agent  (LLM = Azure OpenAI, temperature=0)  │
│                                                         │
│  Responsibilities:                                      │
│   • Decide lookup strategy (barcode → OFF; else USDA)   │
│   • Call tools, reconcile sources                       │
│   • Produce typed VerificationResult                    │
│                                                         │
│  Does NOT: compute deltas, do macro math, decide PASS   │
└─────────────────────────────────────────────────────────┘
      │ tool calls (typed in/out)
      ▼
┌─────────────────────────────────────────────────────────┐
│ Tools                                                   │
│  lookup_usda_by_name(name, category, data_type)         │
│  lookup_off_by_barcode(barcode)                         │
│  lookup_ciqual_by_name(name, category)        [local]   │
│  validate_macro_consistency(nutrition)        [pure]    │
│  calculate_discrepancy(provided, reference)   [pure]    │
│  assess_reference_completeness(reference)     [pure]    │
│  check_known_variance(name, category)         [pure]    │
└─────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ report.py                                               │
│   • report.json   (machine-readable, full trace)        │
│   • report.md     (human summary, status table)         │
│   • food_items.corrected.json  (--apply-corrections)    │
└─────────────────────────────────────────────────────────┘
```

## 4. Key design principles

### 4.1 LLM does reasoning, code does math

The agent decides *which* source to trust and *why*. Deterministic work —
macro consistency (`protein*4 + carbs*4 + fat*9 ≈ calories`), per-field
deltas, threshold flagging — lives in pure Python functions. This keeps
the LLM out of arithmetic and makes the hard parts fully unit-testable.

### 4.2 Typed tool boundaries

Every tool takes and returns a Pydantic model. `Field(description=...)` on
every field; the agent uses those as tool docs. This is the difference
between "tool calling works" and "tool calling is reliable."

### 4.3 Route by item shape

- **Branded + barcode** → Open Food Facts first; USDA Branded as fallback.
- **Generic (no barcode)** → USDA `dataType=Foundation,SR Legacy` **plus**
  CIQUAL (ANSES) for a second authoritative reference. Two-source
  agreement is the only path to confidence 1.0. Explicitly avoid USDA
  Branded for generic queries — it's the #1 source of wrong matches in
  FoodData Central.
- **Known high-variance** (farmed vs wild salmon, "avocado, raw" portion
  vs per-100g semantics, etc.) → return `HIGH_VARIANCE` with reason, not
  `DISCREPANCY`.

### 4.3a Match-relevance guard

FDC's `/foods/search` is eager; a one-word query like "milk" can return
"Crackers, saltines" as the top hit. A tiny token-recall check compares
query tokens (stop-words removed) against the match description, and
returns `None` below a minimum threshold. The agent sees a clean miss
rather than a wrong reference it then has to argue against.

### 4.4 Uncertainty is first-class

Five statuses, not a boolean:

| Status | Meaning |
|---|---|
| `VERIFIED` | Source found, all macros within tolerance |
| `DISCREPANCY` | Source found, one or more macros outside tolerance; correction proposed |
| `HIGH_VARIANCE` | Known naturally variable; provided value is within the plausible range |
| `INCONCLUSIVE` | No authoritative source found or source incomplete |
| `ERROR` | Tool/API failure (never crashes the batch; `return_exceptions=True`) |

### 4.5 Confidence rubric (explicit, not vibes)

| Score | Condition |
|---|---|
| `1.0` | Two independent sources agree within tolerance |
| `0.8` | Single authoritative source (USDA Foundation / SR Legacy) matches |
| `0.6` | Single branded source (OFF or USDA Branded) with complete macros |
| `0.4` | Partial source data or high natural variance |
| `0.0` | No usable source |

Tolerances (per 100 g): ±10% for calories, ±15% for individual macros,
±25% for sodium. Declared constants, not magic numbers.

One extra rule beyond the table above: if the matched reference is
incomplete (zero kcal, or missing two-plus core macros — FDC Foundation
records sometimes lack `Energy (kcal, 1008)` and ship only kJ), confidence
is capped at 0.6 regardless of source type. This prevents the agent from
having to back-compute kcal itself and stamping the result 0.8.

### 4.6 Corrections

A correction is only proposed when `confidence >= 0.8` AND exactly one
authoritative source is available. Otherwise the result carries the
discrepancy but no `proposed_correction`. This matches how a human
nutritionist would behave.

## 5. Data model (sketch)

```python
class VerificationResult(BaseModel):
    item_id: str
    status: Literal["VERIFIED","DISCREPANCY","HIGH_VARIANCE","INCONCLUSIVE","ERROR"]
    confidence: float            # 0.0 – 1.0
    sources: list[SourceCitation]
    macro_consistency: MacroConsistencyResult
    discrepancies: list[FieldDiscrepancy]      # empty if VERIFIED
    proposed_correction: NutritionPer100g | None
    reasoning: str               # LLM's short natural-language note
    trace: list[ToolCall]        # tool, args, result, latency_ms
```

## 6. Reliability

- `httpx.AsyncClient` with explicit 10s timeout, connect+read.
- `tenacity` retry on 429/5xx/timeouts: 3 attempts, jittered exponential.
- On-disk cache (SQLite, keyed on `(source, query_hash)`) for all external
  lookups. Reproducible reruns, no API hammering, fast tests.
- `asyncio.Semaphore(MAX_CONCURRENT_VERIFICATIONS)` so a 50-item file
  doesn't open 50 parallel USDA sessions.
- `asyncio.gather(..., return_exceptions=True)` — one bad item never kills
  the batch.
- `temperature=0`, model deployment recorded in report metadata.

## 7. Unit handling

Input mixes grams and millilitres (e.g. whole milk portion is `250 ml`,
nutrition is per `100 g`). We keep nutrition-per-100g as the canonical
comparison basis and flag `unit_mismatch` on the portion only — we don't
silently convert ml→g without a density. A small known-density table
covers common liquids (milk, oil); everything else triggers a note in the
reasoning field.

## 8. Testing strategy

Priority order matches the value of catching regressions:

1. **Pure logic** (`validate_macro_consistency`, `calculate_discrepancy`,
   `check_known_variance`, unit normalization) — table-driven `pytest.mark.parametrize`.
2. **Clients** (`usda`, `openfoodfacts`) — mocked with `respx`: 200, 404,
   429, timeout, malformed payload.
3. **Normalization** — USDA and OFF payload fixtures → `NutritionReference`.
4. **Agent golden set** — hand-labelled expected status per sample item
   (chicken → `VERIFIED`, farmed salmon → `HIGH_VARIANCE`, etc). Runs
   against the cache, not live APIs.

## 9. Bonus: verifying the verifier

Two layers, both cheap, both shipped:

1. **Golden-set checker** (`eval/golden.py`) — reads the JSON report and
   asserts each item's status is in an allowed set with a minimum
   confidence. Run via `uv run python -m eval.golden outputs/report.json`;
   exits non-zero on regression. Deliberately thin — it catches the gross
   cases (farmed salmon must be `HIGH_VARIANCE`) without over-specifying
   the agent's freedom.
2. **LLM-as-judge** (`eval/judge.py`, exposed as
   `uv run snaq-verify judge outputs/report.json`) — a *different*
   prompt (and, via `AZURE_OPENAI_JUDGE_DEPLOYMENT`, ideally a different
   model) re-reads each item's `reasoning`, `sources`, and tool `trace`
   and returns a typed `JudgeVerdict{grounded, concerns,
   judge_confidence, summary}`. Disagreements between the verifier and
   the judge are the review queue.

## 10. Productization note (goes in README)

In a real SNAQ integration the CLI's JSON report would feed a reviewer
queue: a nutritionist sees flagged items with proposed corrections and
confidence, accepts or edits, and writes the result back to the product
database. We deliberately did not build that UI here — the agent is the
interesting part; the review UI is a standard CRUD screen. The CLI does
ship `--apply-corrections --min-confidence <x>` which emits a corrected
`food_items.json`, which is the programmatic equivalent.

## 11. Deliverables mapping

| Brief asks for | Produced by |
|---|---|
| Working code, minimal setup | `uv run snaq-verify verify food_items.json` |
| README (setup, decisions, future work) | `README.md` |
| Output on `food_items.json` | `outputs/report.{json,md}` |
| AI conversation log | `ai-session/` transcript export |

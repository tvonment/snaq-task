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
│  compare_semantics(source_a, source_b)        [pure]    │
└─────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ report.py                                               │
│   • report.json   (machine-readable, full trace)        │
│   • report.md     (human summary, status table)         │

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

Every field in a `proposed_correction` must come verbatim from one of
the `NutritionReference` records returned by a lookup tool in the same
run. No averaging, no interpolation, no back-computing. The structured
tool `trace` (which includes the full reference payload via
`ToolCall.result_payload`) lets the judge check this mechanically.

### 4.7 Definitional mismatches, named explicitly

Two sources can disagree on the value of `carbohydrates_g` without
actually disagreeing: USDA reports "carbohydrate, by difference" (which
includes fibre), CIQUAL reports `glucides` (available carbohydrates,
which excludes fibre). Same for energy — USDA records may ship kcal,
kJ, or only the Atwater-factor kcal (2047); CIQUAL uses the EU 1169/2011
factors. And OFF sometimes stores "salt" where USDA stores "sodium".

A small pure catalogue in `logic/semantics.py` names these cases and
exposes them as a typed `compare_semantics(source_a, source_b)` tool.
The agent is instructed to call it before computing a discrepancy
across sources, so a delta that is actually a definition doesn't
become a `DISCREPANCY` verdict. This was added after the judge flagged
five `unit_mismatch` concerns on a baseline run against the 11-item
sample — the fix is a pure-Python pure-logic table, not prompt
engineering.

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
    reasoning: VerificationReasoning           # structured, digit-free
    trace: list[ToolCall]                      # tool, args, result_summary,
                                               # result_payload, latency_ms
```

`VerificationReasoning` has four string fields (`routing_decision`,
`source_choice_rationale`, `variance_notes`, `correction_rationale`)
with a `model_validator` that rejects any digit — numbers belong in
`discrepancies` and `proposed_correction`, prose is for *why*. The
agent learns to name the quantity ("calories look low versus USDA
Foundation") instead of paraphrasing values badly ("calories differ
by roughly 30").

Every tool call is recorded in `trace` with a `result_payload` when the
return value is auditable data (a full `NutritionReference`). The
judge uses those payloads to verify `proposed_correction` values
against the record the agent actually saw.

## 6. Reliability

- `httpx.AsyncClient` with explicit 10s timeout, connect+read.
- `tenacity` retry on 429/5xx/timeouts: 3 attempts, jittered exponential,
  `Retry-After` honoured when the server sends one.
- `asyncio.Semaphore(MAX_CONCURRENT_VERIFICATIONS)` so a 50-item file
  doesn't open 50 parallel USDA sessions; per-client semaphore on OFF
  so it stays polite regardless of the global concurrency.
- `asyncio.gather(..., return_exceptions=True)` — one bad item never kills
  the batch.
- `temperature=0`, model deployment recorded in report metadata.
- **No on-disk cache.** Earlier drafts had a SQLite response cache; it
  was removed once the flake-rate on USDA and OFF dropped to acceptable
  levels with tenacity + semaphores. A cache is easy to add back if
  evaluator throughput becomes a problem, but it had started hiding
  real regressions in the judge/golden pipeline and the cost of an
  uncached run against the 11-item sample is trivial.

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
4. **Stability aggregator** — the pure aggregator in
   [`eval/stability.py`](eval/stability.py) is unit-tested with
   hand-crafted fake `verify_*.json` and `judge_*.json` pairs; the
   LLM never runs in the test loop.

## 9. Bonus: verifying the verifier

Two layers, both cheap, both shipped:

1. **LLM-as-judge** (`eval/judge.py`, exposed as
   `uv run snaq-verify judge outputs/report.json`) — a *different*
   prompt (and, via `AZURE_OPENAI_JUDGE_DEPLOYMENT`, ideally a
   different model family) re-reads each item's structured
   `reasoning`, `sources`, and tool `trace` (including the structured
   `result_payload` on each lookup) and returns a typed
   `JudgeVerdict{grounded, concerns: list[JudgeConcern], judge_confidence,
   summary}`. Concerns are typed: `JudgeConcern.kind` is an 8-value
   enum (`wrong_reference`, `correction_provenance`, `unit_mismatch`,
   `missing_citation`, `paraphrase`, `rubric_violation`,
   `variance_reasoning`, `nitpick`). Read the judge honestly: it
   grounds the verifier's reasoning against the trace it produced,
   not against truth.
2. **Stability matrix** (`eval/stability.py`, exposed as
   `uv run snaq-verify stability food_items.json --runs K`) — sweeps
   `reasoning_effort` levels (`minimal`/`low`/`medium`/`high` by
   default) and runs verify + judge K times **per level**. The
   resulting matrix has two layers: an **effort summary** table (rows =
   effort levels, columns = status agreement, confidence, judge
   grounded rate, kind-set Jaccard, mean tool calls per run as a cost
   proxy) and per-effort detail tables (one row per item, every run
   side by side, modal status, per-field correction agreement on the
   modal-status subset). At n=11 with no hand-labelled ground truth,
   measuring *consistency under varied effort* is more honest than
   pretending to measure correctness — and the cost-vs-quality
   tradeoff falls out of the same matrix. The verifier is a reasoning
   model (`gpt-5-mini`) which ignores `temperature`, so effort is the
   only knob that meaningfully changes its behaviour; the judge is a
   non-reasoning model (`gpt-5-chat`) pinned at `temperature=0` so it
   stays a stable reference signal across the sweep. An earlier draft
   shipped a hand-authored `golden.py` and a `metrics.json` aggregator;
   both were dropped because at this sample size they mostly measured
   my own expectations, not the agent's quality. A
   nutritionist-reviewed ground-truth set is called out in the README
   as future work.

## 10. Productization note (goes in README)

In a real SNAQ integration the CLI's JSON report would feed a reviewer
queue: a nutritionist sees flagged items with proposed corrections and
confidence, accepts or edits, and writes the result back to the product
database. We deliberately did not build that UI here — the agent is the
interesting part; the review UI is a standard CRUD screen. We also
deliberately did **not** ship a `--apply-corrections` flag: merging
proposed nutrition values back into product data on a confidence
threshold is exactly the place where this kind of agent should *not*
act unattended. Corrections live in `report.json` for a human to act on.

## 11. Deliverables mapping

| Brief asks for | Produced by |
|---|---|
| Working code, minimal setup | `uv run snaq-verify verify food_items.json` |
| README (setup, decisions, future work) | `README.md` |
| Output on `food_items.json` | `outputs/report.{json,md}` |
| AI conversation log | `ai-session/` transcript export |

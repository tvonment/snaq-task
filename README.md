# SNAQ Nutrition Verification Agent

A command-line agent that verifies the nutrition data in `food_items.json`
against authoritative sources (USDA FoodData Central, Open Food Facts,
ANSES CIQUAL), flags discrepancies, and — when confident — proposes
corrections.

**Philosophy:** the LLM decides *which* source to trust and *why*; pure
Python does the arithmetic. The hard parts are unit-testable.

## Docs

- [DESIGN.md](DESIGN.md) — architecture, scope decisions, confidence rubric.
- [NARRATIVE.md](NARRATIVE.md) — how the build actually went, including
  a critical self-review of what shipped.
- [data/CIQUAL_LICENSE.md](data/CIQUAL_LICENSE.md) — attribution for the
  bundled CIQUAL subset.
- [eval/judge.py](eval/judge.py) / [eval/stability.py](eval/stability.py)
  — the "verify the verifier" layer. Judge grounds reasoning against
  the trace the agent actually produced (not against truth); stability
  measures agreement across K independent runs.

---

## Setup

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Install dependencies
uv sync

# 2. Configure credentials
cp .env.example .env
# Edit .env and fill in:
#   AZURE_OPENAI_ENDPOINT           (must end in /openai/v1/)
#   AZURE_OPENAI_API_KEY
#   AZURE_OPENAI_DEPLOYMENT         (verifier deployment, e.g. gpt-5-mini)
#   AZURE_OPENAI_JUDGE_DEPLOYMENT   (optional; separate deployment for the
#                                    LLM-as-judge so verifier and judge
#                                    don't share failure modes, e.g.
#                                    gpt-5-chat-1. Falls back to
#                                    AZURE_OPENAI_DEPLOYMENT.)
#   USDA_API_KEY                    (free, https://fdc.nal.usda.gov/api-key-signup)

# 3. Run
uv run snaq-verify verify food_items.json --out outputs/
```

Reports are written to `outputs/report.{json,md}`.

### CLI

```
uv run snaq-verify verify food_items.json \
    [--out outputs/] \
    [--format json,md] \
    [--apply-corrections --min-confidence 0.8] \
    [--concurrency 5] \
    [-v | -vv]

uv run snaq-verify judge outputs/report.json \
    [--out outputs/judge.json] \
    [--concurrency 3]

uv run snaq-verify stability food_items.json \
    [--runs 3] \
    [--efforts minimal,low,medium,high] \
    [--no-judge] \
    [--concurrency 5]
```

The `judge` command writes `outputs/judge.json` + `outputs/judge.md`
(verdicts per item, with `generated_at` and the `judge_deployment`
used). Each concern is bucketed into an 8-kind taxonomy
(`wrong_reference`, `correction_provenance`, `unit_mismatch`,
`missing_citation`, `paraphrase`, `rubric_violation`,
`variance_reasoning`, `nitpick`). **Read this honestly:** the judge
checks that the verifier's reasoning is grounded *in the trace it
produced*, not that the answer is correct. It catches paraphrase
drift, unsupported citations, and rubric violations — not factual
errors upstream of the sources.

The `stability` command sweeps `reasoning_effort` levels and runs
verify (and optionally judge) K times **per level**. Each run is
written under `outputs/stability/<effort>/run_{k}/report.{json,md}`
(+ `judge.{json,md}`) and aggregated into `outputs/stability/matrix.json`
+ `matrix.md`. The matrix has two layers:

- **Effort summary** — one row per effort level: status agreement,
  confidence, judge grounded rate, Jaccard kind-set similarity, and
  mean tool calls per run as a cost proxy. Read it as: *what's the
  lowest effort whose grounded rate matches `high`?* That's the
  cheapest setting to ship.
- **Per-effort detail** — verifier and judge tables with one row per
  food item showing every run side by side, the modal status, and
  per-field correction agreement on the modal-status subset.

At n=11 with no hand-labelled ground truth, sweeping effort and
measuring *consistency under varied effort* is more honest than
pretending to measure correctness — and the cost-vs-quality tradeoff
falls straight out of the same matrix. Cost note: the default sweep
is 4 efforts × 3 runs × 11 items = 132 verifier calls (+132 judge
calls); narrow it with `--efforts low,medium` while iterating.

`--apply-corrections` writes `outputs/food_items.corrected.json` with every
proposed correction (above `--min-confidence`) merged into the originals.
`-v` raises `snaq_verify` to DEBUG; `-vv` also re-enables the raw
`httpx` / `openai` / `pydantic-ai` INFO firehose when you need to see
every HTTP call.

### Tests

```bash
uv run ruff check .
uv run pytest -q
```

86 unit tests covering pure logic, HTTP clients (mocked via `respx`,
including 429 + `Retry-After` handling), structured-reasoning validators,
the semantics catalogue, and the judge concern-kind aggregation. No
network required.

---

## Architecture

```
food_items.json
    ↓
runner  (asyncio.gather + Semaphore)
    ↓ per item
pydantic-ai Agent  (Azure AI Foundry, gpt-5-mini)
    ↓ typed tool calls
┌─────────────────────────────────────────────────────┐
│  lookup_usda_by_name            (httpx + tenacity)  │
│  lookup_off_by_barcode          (httpx + tenacity)  │
│  lookup_ciqual_by_name          (bundled subset)    │
│  validate_macro_consistency     (pure)              │
│  calculate_discrepancy          (pure)              │
│  assess_reference_completeness  (pure)              │
│  check_known_variance           (pure)              │
│  compare_semantics              (pure)              │
└─────────────────────────────────────────────────────┘
    ↓
report.{json,md}   +   food_items.corrected.json (optional)
    ↓ (optional, separate command)
eval/judge.py  →  outputs/judge.json
```

Single Python package, one entry point. No MCP server, no SSE, no
docker-compose, no frontend.

---

## Design decisions (short version)

| Decision | Why |
|---|---|
| **CLI, not web app** | Input is a file and reviewers grade by running the code. A UI adds scope without signal on agent design. Productization is called out below. |
| **pydantic-ai native tools, not MCP** | All tools are local Python. pydantic-ai gives the same "agent picks a tool" loop in-process with clearer stack traces. MCP is a natural future extraction, not a present need. |
| **LLM reasons, code computes** | Deltas, macro math (`4P + 4C + 9F ≈ kcal`), and threshold logic are pure functions. The LLM never does arithmetic. |
| **Typed tool boundaries** | Every tool takes and returns a Pydantic model with `Field(description=...)`. That's the difference between "tool calling works" and "tool calling is reliable." |
| **Five statuses, not a boolean** | `VERIFIED` / `DISCREPANCY` / `HIGH_VARIANCE` / `INCONCLUSIVE` / `ERROR`. Uncertainty is first-class. |
| **Explicit confidence rubric** | 0.0 / 0.4 / 0.6 / 0.8 / 1.0 with stated conditions (see [DESIGN.md §4.5](DESIGN.md)). Not vibes. |
| **Route by item shape** | Barcode → Open Food Facts; generic → USDA Foundation → SR Legacy + CIQUAL. Explicitly avoid USDA Branded for generic items (#1 source of wrong matches). |
| **Relevance gate on USDA search** | FDC search will happily return "Crackers" for "Whole Milk". A small token-recall check between query and match description filters those out; the agent sees `None` instead. |
| **Known-variance catalogue** | Farmed vs wild salmon, avocado, whole milk fat bands, ground beef: these are *naturally* variable and resolve to `HIGH_VARIANCE`, not `DISCREPANCY`. |
| **Corrections are conservative** | Only proposed when `confidence >= 0.8` AND exactly one authoritative source was used. Matches how a human nutritionist would behave. |
| **Confidence caps for incomplete references** | When a matched source record has zero kcal or is missing two+ core macros, confidence is capped at 0.6 no matter the source type. Without this, a USDA Foundation record missing `Energy (kcal, 1008)` would still score 0.8 — even though the agent then has to back-compute the value itself. |
| **Structured reasoning, digit-free** | `VerificationResult.reasoning` is a Pydantic model (`routing_decision` / `source_choice_rationale` / `variance_notes` / `correction_rationale`), and a `model_validator` rejects any numerals in the prose. Numbers belong in `discrepancies` and `proposed_correction`; prose is for *why*. |
| **Structured trace for the judge** | Every lookup tool records the full `NutritionReference` it returned into `ToolCall.result_payload`. The LLM-as-judge can verify each `proposed_correction` value against the record the agent actually saw instead of trusting the agent's paraphrase. |
| **Semantics catalogue** | A small pure `compare_semantics` tool ([logic/semantics.py](src/snaq_verify/logic/semantics.py)) names known definitional mismatches between sources (USDA "carbohydrate, by difference" vs CIQUAL `glucides`; USDA Atwater vs EU 1169/2011 energy factors; OFF salt vs sodium; Kjeldahl N-to-protein factor). The agent calls it before comparing cross-source references so delta-that's-actually-a-definition doesn't become a false `DISCREPANCY`. |
| **tenacity + semaphore** | Exponential backoff with jitter on 429/5xx; bounded concurrency so one bad item can't kill the batch (`asyncio.gather(..., return_exceptions=True)`). |

---

## Running against Azure AI Foundry (v1 API)

This project targets the Foundry 2026 **v1** API (`/openai/v1/`), not the
legacy `?api-version=...` surface. That path does **not** accept the
`api-version` query parameter, so the agent uses a plain
`openai.AsyncOpenAI` client (with the Foundry endpoint as `base_url`)
rather than `AsyncAzureOpenAI`. See [src/snaq_verify/agent.py](src/snaq_verify/agent.py).

Sample `.env`:

```
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/openai/v1/
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-5-mini
AZURE_OPENAI_JUDGE_DEPLOYMENT=gpt-5-chat-1   # optional; see below
USDA_API_KEY=...
```

**Why a separate judge deployment.** Verifier and judge share a model
family's failure modes; picking a *different* deployment for the judge
(e.g. verifier on `gpt-5-mini`, judge on `gpt-5-chat-1`) means a
systematic misreading by one side won't be silently rubber-stamped by
the other. If `AZURE_OPENAI_JUDGE_DEPLOYMENT` is unset the judge falls
back to the verifier deployment, which is convenient but weaker.

**Content filter note.** Foundry's Prompt Shields can flag assertive
system-prompt phrasing (e.g. "You MUST NOT...") as a jailbreak attempt.
The system prompt in this repo is written in a neutral, descriptive tone
to avoid that; if you still see `ResponsibleAIPolicyViolation /
jailbreak` errors, lower the "Jailbreak detection" severity on the
deployment's content filter.

---

## Output

The agent writes two reports per run:

- **`report.json`** — machine-readable, includes every tool call
  (name, args, result summary, latency) per item. Consumed by the
  judge and the stability matrix.
- **`report.md`** — human summary table + details section per
  non-`VERIFIED` item.

With `--apply-corrections`, also:

- **`food_items.corrected.json`** — originals with high-confidence
  corrections merged in.

### Sample output

```markdown
| # | Item | Status | Confidence |
|---|------|--------|------------|
| 1 | `chicken-breast-raw` — Chicken Breast, Skinless, Raw | ⚠️ DISCREPANCY | 0.80 |
| 9 | `salmon-atlantic-farmed-raw` — Salmon, Atlantic, Farmed, Raw | 〰️ HIGH_VARIANCE | 0.40 |
| 11 | `white-bread` — White Bread | ⚠️ DISCREPANCY | 0.80 |
```

Full example under [outputs/report.md](outputs/report.md).

---

## Project layout

```
src/snaq_verify/
  cli.py           Typer entrypoint (verify + judge subcommands)
  runner.py        gather + semaphore orchestration
  agent.py         pydantic-ai Agent + tool registrations + system prompt
  models.py        All shared Pydantic models
  report.py        JSON + Markdown writers
  config.py        Env-loaded settings
  clients/
    usda.py            USDA FoodData Central (relevance gate + kcal fallback)
    openfoodfacts.py
    ciqual.py          ANSES CIQUAL (bundled subset)
  logic/
    validation.py      Pure: macro consistency (protein*4 + carbs*4 + fat*9 ≈ kcal)
    discrepancy.py     Pure: per-field deltas and threshold flags
    variance.py        Pure: known-variance catalogue
    completeness.py    Pure: is the reference record complete enough to trust?
    semantics.py       Pure: known definitional mismatches between sources
    constants.py       Named tolerances (no magic numbers)

eval/
  judge.py         LLM-as-judge; re-reads report.json for grounding
  stability.py     Sweep reasoning_effort x K runs; aggregate the matrix

data/
  ciqual_subset.json  Bundled CIQUAL subset (attribution: data/CIQUAL_LICENSE.md)

tests/             pytest + pytest-asyncio + respx — no network
```

---

## What I would do differently with more time

1. **Hand-labelled ground truth.** The stability matrix measures
   whether the agent agrees with *itself* across runs; it cannot
   measure whether the agent is *right*. A small nutritionist-reviewed
   golden set (say 20 items with expected status + kcal bands) would
   let the judge and the stability harness score correctness, not just
   consistency. Building it honestly requires a domain expert, not
   another LLM, which is why it isn't in the box.
2. **A richer LLM-as-judge.** The current judge ships in
   [eval/judge.py](eval/judge.py) with a typed 8-kind concern
   taxonomy and a "different deployment by default" env var
   (`AZURE_OPENAI_JUDGE_DEPLOYMENT`). With more time it would
   aggregate verdicts over historical runs (SQLite, not just K
   snapshots), feed disagreements into a human review queue, and run
   self-consistency (K-sample majority voting) when a single-run
   judge verdict sits on the fence.
2. **Full CIQUAL ingest.** The repo bundles a curated English-labelled
   subset under [data/ciqual_subset.json](data/ciqual_subset.json) with
   attribution in [data/CIQUAL_LICENSE.md](data/CIQUAL_LICENSE.md).
   Good enough to demonstrate two-source agreement for the sample
   items; a real integration would ingest the full ANSES dataset and
   build a proper name/alias index (fuzzy matching, synonyms).
3. **Better unit handling for liquids.** Density table is inlined; a
   real integration wants per-category density data and a clearer
   `unit_mismatch` reporting convention (see [DESIGN.md §7](DESIGN.md)).
4. **Reviewer UI.** In production the JSON report would drive a review
   queue where a nutritionist accepts or edits each proposed
   correction, and the accepted result is written back to the product
   database. That's a standard CRUD screen — the agent is the
   interesting part, which is why the demo stops at the JSON + Markdown
   step.
5. **Branded-food matching heuristics.** Current USDA Branded fallback
   takes the first hit; a real system would score candidates by brand +
   name token overlap before trusting the match.
6. **Structured logging + per-item cost/latency budget.** The console
   shows a concise one-line-per-item progress view and tool-call
   latencies live in the JSON report, but there's no per-run cost
   accounting yet.
7. **Retry the model on tool-argument validation errors.** Rare, but
   when the LLM emits a malformed tool arg, pydantic-ai raises — the
   runner currently turns it into `ERROR` without a retry.

---

## Known environment gotcha

USDA's WAF (api.nal.usda.gov via api.data.gov) blocks some cloud / CI
egress ranges with `HTTP 403 Forbidden` regardless of API key — even
`DEMO_KEY`. If you see 403s for every USDA lookup, run from a
residential IP or a non-blocked host; your `USDA_API_KEY` is fine.

---

## Deliverables mapping

| Home-task asks for | Produced by |
|---|---|
| Working code, minimal setup | `uv sync && uv run snaq-verify verify food_items.json` |
| README (setup, decisions, future work) | this file |
| Design rationale | [DESIGN.md](DESIGN.md) |
| Output on `food_items.json` | `outputs/report.{json,md}` |
| AI conversation log | [NARRATIVE.md](NARRATIVE.md) + linked Claude share |
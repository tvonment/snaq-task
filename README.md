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
- [eval/golden.py](eval/golden.py) / [eval/judge.py](eval/judge.py) —
  the "verify the verifier" layer.

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
```

The `judge` command writes `outputs/judge.json` + `outputs/judge.md`
(verdicts per item, with `generated_at` and the `judge_deployment` used)
and `outputs/metrics.json`, a small aggregate of verifier status counts,
golden-set pass/fail, judge `grounded_rate`, the combined
`grounded_success_rate` (item passes golden **and** the judge calls it
grounded), and `concern_kind_counts` bucketed by the 8-kind taxonomy
(`wrong_reference`, `correction_provenance`, `unit_mismatch`,
`missing_citation`, `paraphrase`, `rubric_violation`,
`variance_reasoning`, `nitpick`). That last number is the one worth
tracking run over run — it moves when the agent actually gets better.

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

88 unit tests covering pure logic, HTTP clients (mocked via `respx`,
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
  judge and golden-set eval.
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
  golden.py        Structural expectations per item; exit non-zero on regression
  judge.py         LLM-as-judge; re-reads report.json for grounding
  metrics.py       Aggregates: grounded_success_rate + concern_kind_counts

data/
  ciqual_subset.json  Bundled CIQUAL subset (attribution: data/CIQUAL_LICENSE.md)

tests/             pytest + pytest-asyncio + respx — no network
```

---

## What I would do differently with more time

1. **A richer LLM-as-judge.** A judge ships in
   [eval/judge.py](eval/judge.py) with a typed 8-kind concern taxonomy,
   a "different deployment by default" env var
   (`AZURE_OPENAI_JUDGE_DEPLOYMENT`), and an aggregated
   `grounded_success_rate` metric in [outputs/metrics.json](outputs/metrics.json).
   With more time it would aggregate those metrics over historical
   runs (SQLite, not a single JSON), feed disagreements into a human
   review queue, and run deterministic judges (temperature = 0 + fixed
   seed + replay cache) so bucket-shape trends are apples-to-apples
   across sessions.
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
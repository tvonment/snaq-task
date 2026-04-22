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
#   AZURE_OPENAI_ENDPOINT     (must end in /openai/v1/)
#   AZURE_OPENAI_API_KEY
#   AZURE_OPENAI_DEPLOYMENT   (your deployment name, e.g. gpt-5-mini)
#   USDA_API_KEY              (free, https://fdc.nal.usda.gov/api-key-signup)

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
    [--no-cache] \
    [--concurrency 5] \
    [-v | -vv]

uv run snaq-verify judge outputs/report.json \
    [--out outputs/judge.json] \
    [--concurrency 3]
```

The `judge` command writes both `outputs/judge.json` (machine-readable)
and `outputs/judge.md` (human summary) to the directory of `--out`.

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

39 unit tests covering pure logic, HTTP clients (mocked via `respx`,
including 429 + `Retry-After` handling), and the cache. No network
required.

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
| **SQLite cache + tenacity + semaphore** | Reproducible reruns, no API hammering, one bad item can't kill the batch. `asyncio.gather(..., return_exceptions=True)`. |

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
USDA_API_KEY=...
```

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
  cache.py         SQLite response cache
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
    constants.py       Named tolerances (no magic numbers)

eval/
  golden.py        Structural expectations per item; exit non-zero on regression
  judge.py         LLM-as-judge; re-reads report.json for grounding

data/
  ciqual_subset.json  Bundled CIQUAL subset (attribution: data/CIQUAL_LICENSE.md)

tests/             pytest + pytest-asyncio + respx — no network
```

---

## What I would do differently with more time

1. **A richer LLM-as-judge.** A minimal judge ships in
   [eval/judge.py](eval/judge.py) and is exposed via
   `uv run snaq-verify judge outputs/report.json`. With more time it
   would run against a different deployment by default (set
   `AZURE_OPENAI_JUDGE_DEPLOYMENT`), aggregate grounded-rate over
   historical runs, and feed disagreements into a human review queue.
2. **Full CIQUAL ingest.** The repo bundles a curated English-labelled
   subset under [data/ciqual_subset.json](data/ciqual_subset.json) with
   attribution in [data/CIQUAL_LICENSE.md](data/CIQUAL_LICENSE.md).
   Good enough to demonstrate two-source agreement for the sample
   items; a real integration would ingest the full ANSES dataset and
   build a proper name/alias index (fuzzy matching, synonyms).
3. **Better unit handling for liquids.** Density table is inlined; a
   real integration wants per-category density data and a clearer
   `unit_mismatch` reporting convention (see [DESIGN.md §7](DESIGN.md)).
4. **Reviewer UI.** The `report.html` is read-only. In production the
   JSON report would drive a review queue where a nutritionist accepts
   or edits each proposed correction, and the accepted result is
   written back to the product database. That's a standard CRUD screen
   — the agent is the interesting part, which is why the demo stops at
   the JSON + static HTML step.
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
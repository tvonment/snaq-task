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
- [transcripts/](transcripts/) — raw VS Code Copilot Chat exports
  (the receipts behind the narrative). See
  [transcripts/README.md](transcripts/README.md).
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
    [--concurrency 5] \
    [--reasoning-effort minimal|low|medium|high] \
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

`--reasoning-effort` is forwarded to OpenAI-family reasoning models
(gpt-5, o-series). It's a per-run override for one-shot verifications;
`stability` sweeps efforts independently. `-v` raises `snaq_verify` to
DEBUG; `-vv` also re-enables the raw `httpx` / `openai` / `pydantic-ai`
INFO firehose when you need to see every HTTP call.

Reports stamp `Instructions: vN` in the header (and
`instructions_version` in `report.json` / `matrix.json`). Bumping
`INSTRUCTIONS_VERSION` in [src/snaq_verify/agent.py](src/snaq_verify/agent.py)
is how revised prompts get tracked across stability runs without a
full prompt-management framework — at two prompts the stamp is enough.

### Tests

```bash
uv run ruff check .
uv run pytest -q
```

94 unit tests covering pure logic, HTTP clients (mocked via `respx`,
including 429 + `Retry-After` handling), structured-reasoning validators,
the semantics catalogue, the judge concern-kind aggregation, the
stability aggregator, and the v2 instruction rules. No network
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
│  compare_semantics              (pure)              │
└─────────────────────────────────────────────────────┘
    ↓
report.{json,md}
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
| **tenacity + semaphore** | Exponential backoff with jitter on 429/5xx; bounded concurrency. Each per-item task wraps the agent in `try/except` and converts failures to `status="ERROR"` so one bad item can't kill the batch. |

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

Proposed corrections live inside `report.json` (per-field, with
`reference` + `confidence`). There is **no** auto-apply flag: merging
corrections back into product data is a human-review step, not a
confidence-threshold gamble. See "Reviewer UI" under future work.

### Sample output

The Summary table from a representative `medium`-effort run
(`gpt-5-mini`, Instructions `v2`):

```markdown
| # | Item | Status | Confidence |
|---|------|--------|------------|
| 1 | `chicken-breast-raw` — Chicken Breast, Skinless, Raw | ⚠️ DISCREPANCY | 0.80 |
| 2 | `banana-raw` — Banana, Raw | ⚠️ DISCREPANCY | 0.80 |
| 3 | `broccoli-raw` — Broccoli, Raw | ⚠️ DISCREPANCY | 0.80 |
| 4 | `whole-milk` — Whole Milk, 3.5% Fat | ✅ VERIFIED | 0.80 |
| 5 | `egg-whole-raw` — Egg, Whole, Raw | ⚠️ DISCREPANCY | 1.00 |
| 6 | `almonds-raw` — Almonds, Raw | ⚠️ DISCREPANCY | 0.80 |
| 7 | `oats-rolled-dry` — Oats, Rolled, Dry | ⚠️ DISCREPANCY | 0.80 |
| 8 | `avocado-raw` — Avocado, Raw | ⚠️ DISCREPANCY | 0.80 |
| 9 | `salmon-atlantic-farmed-raw` — Salmon, Atlantic, Farmed, Raw | 〰️ HIGH_VARIANCE | 0.40 |
| 10 | `fage-total-0-greek-yogurt` — Total 0% Greek Yogurt, Plain | ⚠️ DISCREPANCY | 0.60 |
| 11 | `white-bread` — White Bread | ⚠️ DISCREPANCY | 0.80 |
```

`outputs/` is gitignored on purpose — reports are generated artefacts,
not source. Regenerate locally with:

```bash
uv run snaq-verify verify food_items.json --out outputs/
```

A full per-item details section (sources, field deltas, structured
reasoning, full tool trace) is written alongside the Summary into
`outputs/report.md`.

---

## Inputs the customer can change

The agent works against three sources, with different coverage
properties when `food_items.json` is replaced with the customer's own
file. **Read this before swapping the input.**

| Source | How it's accessed | Coverage on a customer file |
|---|---|---|
| **USDA FoodData Central** | Live HTTPS API (`api.nal.usda.gov`) | Anything FDC search resolves. No coverage gap from changing the input file. |
| **Open Food Facts** | Live HTTPS API (`world.openfoodfacts.org`) | Any valid EAN/UPC barcode the customer supplies. **Not bound to our sample** \u2014 the OFF query is per-barcode, not a local subset. |
| **ANSES CIQUAL** | Bundled local subset under [data/ciqual_subset.json](data/ciqual_subset.json), licensed per [data/CIQUAL_LICENSE.md](data/CIQUAL_LICENSE.md) | **Curated for the eleven items in the provided `food_items.json`.** A customer-supplied file with new generic foods will silently miss CIQUAL coverage and fall back to a single-source verdict (capped at confidence 0.8). |

The CIQUAL limitation is the only one that meaningfully affects a
different input file. Full CIQUAL ingest is listed in "What I would do
differently" below.

---

## Stability findings

The bonus eval layer ([eval/stability.py](eval/stability.py)) sweeps
`reasoning_effort` x K runs and aggregates a matrix at
[outputs/stability/matrix.md](outputs/stability/matrix.md). At n=11
with no hand-labelled ground truth, the honest signals are
**consistency** (does the agent agree with itself across runs?) and
**groundedness** (does the LLM-as-judge accept the verifier's
reasoning as supported by the trace?).

The first sweep produced this baseline at `Instructions: v1`,
3 runs per effort:

| Effort | Status agree | Conf mean | Grounded rate | Kind Jaccard | Tool calls/run |
|---|---|---|---|---|---|
| `minimal` | 88% | 0.72 | 33% | 0.60 | 7.5 |
| `low` | 91% | 0.73 | 27% | 0.70 | 8.2 |
| `medium` | 94% | 0.76 | 27% | 0.61 | 8.8 |
| `high` | **100%** | 0.78 | 33% | 0.72 | 8.9 |

The diagnostic reading: **higher reasoning effort buys consistency
but not grounding.** Status agreement scales with effort (88\u2192100%);
grounded rate plateaus at ~30% across all four levels. That's a
strong signal the residual judge concerns aren't "the agent didn't
think long enough" \u2014 they're tool-shape and instruction problems.

Inspecting the judge concerns surfaced four recurring patterns:
`unit_mismatch` (USDA-vs-CIQUAL carbs/energy treated as discrepancy
despite being definitional), `wrong_reference` (USDA picks the wrong
food variant, e.g. "Egg, frozen, pasteurized" for raw egg),
`rubric_violation` (verifier and judge disagreed on what the
confidence rubric actually says), and `variance_reasoning`
(catalogue-matched items still narrated as DISCREPANCY). Three of the
four are addressable by tightening the agent and judge instructions;
the fourth (`wrong_reference`) needs a tool-shape change.

I shipped the instruction tightening as `Instructions: v2`:

- Mandate `compare_semantics_tool` *before* `calculate_discrepancy_tool`
  for cross-source comparisons.
- State explicitly that fields covered by a `compare_semantics` note
  do not count toward DISCREPANCY \u2014 the definitional rule is no
  longer narrative, it's a hard contract.
- Cap confidence at 0.8 when two sources disagree on a non-definitional
  field beyond tolerance (closes the rubric ambiguity).
- Make HIGH_VARIANCE *mandatory* when the catalogue matches and every
  exceeding field is in `variable_fields` \u2014 no exceptions.
- Realign the judge prompt to grade against that exact rubric so
  verifier and judge stop talking past each other.

Re-running 3 runs at `medium` effort, `Instructions: v2`:

| Metric | v1 (medium) | v2 (medium) | Delta |
|---|---|---|---|
| Status agreement | 94% | **100%** | +6 pp |
| Grounded rate | 27% | **82%** | **+55 pp** |
| Grounded agreement | 91% | 94% | +3 pp |
| Kind Jaccard | 0.61 | **0.88** | +0.27 |
| Tool calls / run | 8.8 | 9.3 | +0.5 |
| Confidence mean | 0.76 | 0.75 | -0.01 |

Grounded rate triples at the same effort level; concern-kind sets
become highly stable across runs (Jaccard 0.61\u20920.88), and `medium`
now matches what previously required `high`. The two items still
ungrounded across runs are `egg-whole-raw` and `avocado-raw` \u2014 both
exactly the failure modes the "Future work" backlog targets (top-N
USDA candidates with match-quality, and stricter mechanical variance
enforcement).

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

The "Stability findings" section above shows that v2 instructions
closed most of the grounding gap, but two failure modes survived
across runs (`egg-whole-raw`, `avocado-raw`). Items 1\u20133 below are the
**direct Phase 2 backlog** that would address them; items 4+ are the
broader future work.

1. **Top-N USDA candidates with `match_quality`.** Today
   `lookup_usda_by_name` returns the first hit. The agent has no
   recourse when USDA picks a bad variant ("Egg, frozen, pasteurized"
   for raw egg). Returning N=3 candidates each tagged with a
   `match_quality` enum (`exact` / `close` / `weak` / `wrong_form`)
   would let the agent reject obvious mismatches mechanically instead
   of relying on the LLM to spot them.
2. **Definitional-aware `calculate_discrepancy`.** Move the
   "definitional fields don't count" rule from prompt to code: the
   tool would consult the same semantics catalogue and flag fields as
   `is_definitional=True` so they never bubble up as DISCREPANCY in
   the first place. Removes the rule from a place the LLM can forget
   it.
3. **`propose_correction_from_reference_tool`.** A typed tool that
   takes a `NutritionReference` and emits a `Correction` keyed by
   that exact source. Provenance becomes structural rather than
   narrative \u2014 the judge can't ding the agent for a missing
   `reference` field if the field is mandatory in the tool signature.
4. **Expanded variance catalogue.** Today the catalogue is small and
   manual. Items like generic broccoli or white bread bounce between
   `VERIFIED` and `DISCREPANCY` runs because their natural variance
   isn't encoded. A literature-backed catalogue (with cited bands)
   would convert several brittle items into mechanically-correct
   `HIGH_VARIANCE` verdicts.
5. **Optional third source for cross-check.** USDA + CIQUAL is a
   strong pair for whole foods, but adding e.g. EuroFIR / FoodB as a
   *tiebreaker* (only consulted when the first two disagree on a
   non-definitional field) would push the still-ungrounded items off
   the single-source-cap floor of 0.8.
6. **Prompt-versioning tooling at scale.** The current
   `INSTRUCTIONS_VERSION` stamp is a deliberate two-line solution \u2014
   honest at two prompts, useless at fifty. Past ~5 prompts I'd reach
   for a real prompt registry (Promptfoo, LangSmith, or a hand-rolled
   `prompts/` directory with hashing) so stability sweeps can compare
   N versions, not just two.
7. **Hand-labelled ground truth.** The stability matrix measures
   whether the agent agrees with *itself* across runs; it cannot
   measure whether the agent is *right*. A small nutritionist-reviewed
   golden set (say 20 items with expected status + kcal bands) would
   let the judge and the stability harness score correctness, not just
   consistency. Building it honestly requires a domain expert, not
   another LLM, which is why it isn't in the box.
8. **A richer LLM-as-judge.** The current judge ships in
   [eval/judge.py](eval/judge.py) with a typed 8-kind concern
   taxonomy and a "different deployment by default" env var
   (`AZURE_OPENAI_JUDGE_DEPLOYMENT`). With more time it would
   aggregate verdicts over historical runs (SQLite, not just K
   snapshots), feed disagreements into a human review queue, and run
   self-consistency (K-sample majority voting) when a single-run
   judge verdict sits on the fence.
9. **Full CIQUAL ingest.** The repo bundles a curated English-labelled
   subset under [data/ciqual_subset.json](data/ciqual_subset.json) with
   attribution in [data/CIQUAL_LICENSE.md](data/CIQUAL_LICENSE.md).
   Good enough to demonstrate two-source agreement for the sample
   items; a real integration would ingest the full ANSES dataset and
   build a proper name/alias index (fuzzy matching, synonyms).
10. **Better unit handling for liquids.** Density table is inlined; a
    real integration wants per-category density data and a clearer
    `unit_mismatch` reporting convention (see [DESIGN.md §7](DESIGN.md)).
11. **Reviewer UI.** In production the JSON report would drive a review
    queue where a nutritionist accepts or edits each proposed
    correction, and the accepted result is written back to the product
    database. That's a standard CRUD screen \u2014 the agent is the
    interesting part, which is why the demo stops at the JSON +
    Markdown step.
12. **Branded-food matching heuristics.** Current USDA Branded fallback
    takes the first hit; a real system would score candidates by brand
    + name token overlap before trusting the match.
13. **Structured logging + per-item cost/latency budget.** The console
    shows a concise one-line-per-item progress view and tool-call
    latencies live in the JSON report, but there's no per-run cost
    accounting yet.
14. **Retry the model on tool-argument validation errors.** Rare, but
    when the LLM emits a malformed tool arg, pydantic-ai raises \u2014 the
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
| Output on `food_items.json` | `outputs/report.{json,md}` (regenerated locally; `outputs/` is gitignored) |
| AI conversation log | [NARRATIVE.md](NARRATIVE.md) (curated retrospective) + [transcripts/](transcripts/) (raw Copilot Chat exports) + linked Claude share for the initial scoping conversation |
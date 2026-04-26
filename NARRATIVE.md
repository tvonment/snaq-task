# Working narrative

SNAQ asks for the AI conversation log alongside the code. How
the project actually unfolded, where the AI helped, where it pulled
toward bad ideas, and where I overruled it.

> **Note:** this is a working diary written across the build. Some
> sections describe states the repo has since moved past (the SQLite
> cache, the HTML report, `eval/golden.py`, and intermediate test
> counts). For the current state of the code, read [README.md](README.md)
> and [DESIGN.md](DESIGN.md) — the diary is preserved as-is so the
> sequence of decisions remains legible.

---

## 1. Architectural pushback before any code

Before opening VS Code I'd already had a scoping chat with Claude
(shared here:
https://claude.ai/share/3ac464be-96f1-4488-91b2-84f67c314142). That
conversation sketched out something much more elaborate — a React/Vite
frontend for uploading and reviewing items, an MCP server exposing the
tools over SSE, a separate FastAPI backend, and a docker-compose to
wire it all together. It was my idea, not the model's; Claude was just
happy to help me spec it.

I brought that shape into this repo's first Copilot turn in the form
of initial instructions, and then immediately second-guessed myself:

> "please review the instructions and the home_task.md. review the
> architecture in the instructions and recheck if this is a way we
> should move forward."

The brief is explicit — *"a simple, well-reasoned solution beats a
complex one that you can't fully explain"* — the input is a JSON file,
and reviewers grade by running the code. A UI adds scope without signal
on the thing being evaluated: agent design. To its credit, Copilot
pushed back hard on what I'd brought in and argued for a single-package
Python CLI with pydantic-ai, native (in-process) tools, pure-Python
math, Azure OpenAI for the LLM, and optional static HTML as the only
"UI". I asked it to write that up as `DESIGN.md` and a prescriptive
`copilot-instructions.md` *before* writing any code — so the rest of
the session had something concrete to be held accountable to.

Lesson for me: the most valuable thing an AI pairing partner does is
push back on the human when the human is wrong. The single
highest-leverage prompt in the whole project was asking it to re-read
the brief and justify the architecture I'd brought in.

## 2. The design, in one paragraph

Five statuses (`VERIFIED` / `DISCREPANCY` / `HIGH_VARIANCE` /
`INCONCLUSIVE` / `ERROR`) so uncertainty is first-class. An explicit
confidence rubric (0.0 / 0.4 / 0.6 / 0.8 / 1.0) with stated conditions,
not vibes. Route by item shape — barcode to Open Food Facts, generic to
USDA Foundation/SR Legacy, explicitly avoid USDA Branded for generic
items (the #1 cause of bad matches in FDC). A small known-variance
catalogue so farmed-vs-wild salmon resolves to `HIGH_VARIANCE` rather
than `DISCREPANCY`. The LLM decides *which* source to trust; pure
Python does deltas and macro math (`4P + 4C + 9F ≈ kcal`). Corrections
only get proposed at `confidence >= 0.8`.

## 3. Build order (test-driven where it mattered)

1. **Pure logic first**: `logic/validation.py`, `logic/discrepancy.py`,
   `logic/variance.py`, with `pytest.mark.parametrize` tables. This is
   where regressions bite hardest, and where mocking is unnecessary.
2. **Clients with `respx`**: USDA and Open Food Facts, covering 200,
   404, 429, timeout, and malformed payload. Tenacity retry config,
   normalization to a single `NutritionReference` shape.
3. **Cache**: tiny stdlib-SQLite wrapper. Caches negatives via a
   `__NONE__` sentinel so "we already looked that up and it was
   missing" is different from "we never looked."
4. **Agent**: one `Agent` instance, five tools, typed I/O. System prompt
   enforces the routing rules. Tools append `ToolCall` entries to a
   per-request trace so the final report can show the full decision
   path.
5. **Runner + report**: `asyncio.gather` with `Semaphore(5)` and
   `return_exceptions=True`; report writers for JSON, Markdown, and a
   self-contained HTML (no external assets).

By the end there were 38 unit tests, ruff was clean, and the whole
thing ran without network access during CI.

## 4. Where the AI was wrong, and I had to catch it

**`AsyncAzureOpenAI` vs the Foundry v1 API.** The AI's first integration
used `openai.AsyncAzureOpenAI(api_version="preview")`. That client
*always* appends `?api-version=...` to the URL, and the Foundry 2026 v1
path (`/openai/v1/`) explicitly rejects it:

```
{'code': 'BadRequest',
 'message': 'api-version query parameter is not allowed when using /v1 path'}
```

The fix was to use plain `openai.AsyncOpenAI` with the Foundry endpoint
as `base_url`. I caught this only because I read the actual 400 body
instead of trusting the "recommended Azure integration" path.

**Content filter / Prompt Shields false positive.** First successful
dispatch to Foundry came back `ResponsibleAIPolicyViolation → jailbreak
detected` on *every* request. Root cause: the system prompt was written
in assertive second-person imperative ("You MUST NOT... You are NOT..."),
which Prompt Shields heuristically treats as a jailbreak attempt. I
rewrote it in a neutral, descriptive register ("Role: verify one food
item..." / "Tool routing: ..."). That plus the user disabling the
jailbreak severity on the deployment got the requests through.

**Responses API detour.** At one point, before the Prompt-Shields fix,
the AI jumped to "use the Responses API instead" — which immediately
surfaced a pydantic-ai ↔ Foundry compat bug (empty `type` in `input[1]`)
and burned cycles. Not every error is fixable at the library level;
sometimes the right move is "fix the actual root cause, not the
symptom." We reverted to Chat Completions once Prompt Shields was
addressed.

**USDA 403 from the dev-container.** Every USDA call from the
Codespaces dev-container returned HTTP 403, including with `DEMO_KEY`.
The response was a plain nginx 403, not api.data.gov's JSON
rate-limit body, which is consistent with the Codespaces egress range
being filtered upstream rather than the key being wrong. No code fix
is possible from our side; the workaround — documented in the README
— is to run locally.

## 5. Where the AI earned its keep

- **Scaffolding speed.** The entire `uv`/pyproject setup, models,
  ruff config, test layout, and fixtures came together quickly because
  the AI can type faster than I can.
- **Typed Pydantic boundaries.** Every tool has a Pydantic-modelled
  in/out with `Field(description=...)`. That's the difference between
  "tool calling works once" and "tool calling is reliable." The AI did
  this consistently without being told each time.
- **Report writers.** JSON, Markdown with status-badge table, and a
  self-contained Jinja2 HTML template with inline CSS — all done in
  one pass, all valid on first render.
- **Error handling discipline.** `return_exceptions=True` in the
  gather, per-item `ERROR` status instead of batch crashes, tenacity
  with jitter on 429/5xx, cache with a `_MISS` sentinel distinct from
  a cached `None`. The AI proposed all of these; I just had to approve.

## 6. Polish pass: logs and OFF resilience

After the first full run produced a clean report, two real annoyances
remained. The console was unreadable — every HTTP call logged through
`httpx` at INFO level, so a 10-item run was 80+ lines of `POST ...
"HTTP/1.1 200 OK"` with no sense of progress. And Open Food Facts had
started returning sporadic 429s under the default concurrency.

I asked for "something more readable while it's executing" plus "waits
and retries for OFF". Copilot came back with a plan before touching
code, which I liked: silence `httpx`/`httpcore`/`openai`/`pydantic_ai`
to WARNING, emit one concise line per completed item (`[3/10] VERIFIED
White Bread  conf=0.80  tools=4  2.1s`), and harden the OFF client with
more attempts (3 → 5), a wider backoff window (0.5–4s → 1–30s), a
per-client `asyncio.Semaphore(2)` so OFF stays polite even if global
`--concurrency` is bumped, and — the actually-correct-behaviour bit —
honoring the server's `Retry-After` header when present. The same
header handling went into the USDA client for symmetry.

Two small decisions worth naming:

- **No `rich`, no progress bar.** Plain log lines work in CI, in pipes,
  and in `tee`; a live TTY bar doesn't. One dependency not added.
- **Per-host semaphore on the client instance, not global.** The
  politeness policy for OFF belongs to the OFF client, not the runner.
  Swapping the client out later shouldn't silently drop the rate limit.

A follow-up turn added `-v` / `-vv` for the days you actually want the
raw HTTP firehose back: `-v` promotes `snaq_verify` to DEBUG, `-vv`
additionally re-enables the third-party INFO loggers. Default stays
quiet.

One new test (`test_barcode_lookup_honors_retry_after_on_429`) patches
`asyncio.sleep` and asserts the 2-second hint is honoured; the full
suite is now 39 green.

Worth naming honestly: the polite-client work wasn't only prompted by
OFF's 429s. A few `Agent failed for item …` errors during a parallel
11-item run were our own Azure deployment hitting its TPM ceiling —
so part of the same turn was raising the deployment's tokens-per-
minute quota in the portal, not just adding retries in code. The
reason `--concurrency` defaults low and the per-host semaphore caps
at 2 is that the bottleneck was as often *upstream of us* as it was
on the public APIs.

Lesson reinforced: the right shape of a change like this is "ask for a
plan, skim it, approve, then let it run". The plan I got back named the
right trade-offs (Retry-After cap, no `rich`, per-host vs. global
semaphore) and explicitly flagged the optional `-v` flag as a
follow-up question rather than silently including it. That's the
interaction mode I want from a coding agent.

## 7. What I would do differently if I ran it again

- Write the golden-set eval (`eval/golden.py` + `eval/judge.py`, the
  bonus from the brief) earlier, before the live-run debugging cycle.
  It would have caught the content-filter issue with a single reusable
  asset instead of a full 11-item run each time.
- Put the USDA egress check at the top of `runner.py` so it fails fast
  with a readable error instead of 11 parallel tracebacks.
- Add a `--dry-run` that exercises the agent against cached fixtures
  only, to demo the full pipeline without any live keys.

## 8. What SNAQ is actually reading here

The README says "read this first, then run the code, then read the
code." The narrative above is how I'd describe the session to another
engineer: a short architectural argument up front, a boring and test-
first middle, two real integration surprises at the end, and one
environmental gotcha I couldn't code around.

The two AI conversations that produced this repo are:

1. Initial scoping in Claude chat —
   https://claude.ai/share/3ac464be-96f1-4488-91b2-84f67c314142
   (the over-engineered plan I later walked back).
2. The Copilot session in this workspace, which did the actual
   implementation, the architecture rewrite, and all the debugging
   described above.

## 9. Critical self-review (after shipping)

Before calling it done, I had Copilot re-audit the repo from scratch
against the brief \u2014 no assumption of correctness, looking for things
worth fixing. (Late in the session I also pasted the same brief plus
the repo into Codex for a second outside opinion; that pass mostly
confirmed the Copilot critique and added a couple of doc-vs-code
drift catches that fed into the final polish.) Three findings were
worth acting on, and one was worth explicitly deferring.

**Finding 1 \u2014 USDA was sometimes sending back kcal = 0.**
Some FDC Foundation records ship energy only as kJ (nutrient 1062), not
kcal (nutrient 1008). The original normaliser treated "kcal nutrient
absent" as zero. That cascades: the discrepancy tool then flags a real
food as -100% calories. Fix was a small fallback chain in
`_normalize_fdc_food`: prefer 1008, else 1062 / 4.184, else Atwater
(`4P + 4C + 9F`). The `match_notes` field records which path was used,
so a reviewer can see when the reference value was derived.

**Finding 2 \u2014 The confidence rubric didn't punish incomplete matches.**
A USDA Foundation hit with a real kcal value and a USDA Foundation hit
missing half its macros were both scoring 0.8. Added a pure
`assess_reference_completeness` tool and a rule: if the reference is
incomplete, confidence is capped at 0.6 regardless of source type.
This is the kind of rule you want in pure Python, not in the prompt.

**Finding 3 \u2014 Single authoritative source meant 1.0 confidence was
nearly unreachable.** The brief explicitly hints at this: "multiple
sources of authoritative data" as a direction. I added a local
[ANSES CIQUAL](https://ciqual.anses.fr/) subset (11 foods, hand-curated
English aliases, attribution in `data/CIQUAL_LICENSE.md`) and a
`lookup_ciqual_by_name` tool. For generic items the agent can now get
two-source agreement and score 1.0 legitimately. Full CIQUAL ingest is
deferred \u2014 the subset is enough to demonstrate the pattern on the
sample items without shipping 3000 rows of French-labelled data.

**Bonus \u2014 LLM-as-judge, shipped thin.** The brief lists this as a
bonus and I had originally deferred it. On review, it felt wrong to
claim the verifier was trustworthy without any second-order check, so
I added the minimal form: a second `pydantic-ai` Agent with its own
system prompt, a typed `JudgeVerdict`, an `AZURE_OPENAI_JUDGE_DEPLOYMENT`
env var so it can run on a different model, and a `snaq-verify judge`
subcommand that re-reads `report.json` and writes `judge.json`. Paired
with a tiny `eval/golden.py` structural checker (exits non-zero on
regression), that's the start of a real eval loop without pretending
it's a full one.

**Deliberately not done.** HTML report was removed \u2014 the JSON and
Markdown reports carry everything, and the HTML was a third format to
keep in sync with no real consumer. The Jinja2 dependency went with it.

The point of this section is not "look what I caught." It's that the
brief asks about *reasonableness of heuristics* and *the hard parts of
this task*, and you can't answer those honestly without a critical
second pass. The first pass got the architecture right; the second
pass found the bugs.

## 10. Closing the loop: let the judge drive the next five changes

Section 9 ended at "we have an LLM-as-judge, thin". That's a starting
condition, not an end state. If you have a judge, you should be using
its output as a concrete to-do list for the verifier \u2014 otherwise the
judge is ceremony.

So I drew up a short plan (M1\u2013M6) and let the judge's typed concern
taxonomy pick the targets. The loop was: one change at a time, run
verify + judge end-to-end, read `concern_kind_counts` in
`metrics.json`, commit with before/after bucket numbers in the body,
move on.

- **M1 \u2014 discrepancy floor + rip the cache.** The discrepancy math had
  a floor bug that let \"-100%\" collapse to a smaller percentage for
  near-zero provided values; fixed with an `_ABSOLUTE_FLOOR` and a
  table of regression tests. At the same time I tore out the SQLite
  response cache. It had started hiding real regressions in the
  judge/golden pipeline (a stale cached USDA response would keep a
  broken verifier looking correct), and the cost of an uncached run
  against 11 items is trivial.
- **M2 \u2014 structured, digit-free reasoning.** `VerificationResult.reasoning`
  went from a free-form string to a Pydantic model\n  (`routing_decision` / `source_choice_rationale` / `variance_notes` /\n  `correction_rationale`) with a `model_validator` that rejects any\n  digit. The agent can't paraphrase numbers badly anymore \u2014 it has to\n  name the quantity (\"calories look low versus USDA Foundation\")\n  instead of approximating it (\"calories differ by roughly 30\").\n  Numbers live in `discrepancies`.\n- **M3 \u2014 typed judge concerns.** The judge was returning free-text\n  \"concerns\"; the 8-kind enum (`wrong_reference`,\n  `correction_provenance`, `unit_mismatch`, `missing_citation`,\n  `paraphrase`, `rubric_violation`, `variance_reasoning`, `nitpick`)\n  turned those into a histogram we could track over runs. `nitpick` is\n  the escape hatch: judges flag it, but it doesn't count against\n  `grounded`. The first run with the enum gave a clean baseline\n  histogram \u2014 eight `wrong_reference`, seven `correction_provenance`,\n  five `unit_mismatch`. That's the to-do list.\n- **M5 \u2014 `grounded_success_rate` + provenance on the judge.** An item\n  passes the golden set *and* the judge calls it grounded \u2014 one\n  number per run, in `metrics.json`. `judge.json` and `judge.md` also\n  got `generated_at` and `judge_deployment` so fresh and stale\n  artefacts can't be confused, and so gpt-5-mini-judged and\n  gpt-5-chat-1-judged runs are comparable by inspection.\n- **M4 \u2014 reference payloads in the trace + semantics catalogue.** The\n  two biggest buckets at baseline were `wrong_reference` and\n  `correction_provenance`: the judge couldn't verify the agent's\n  proposed corrections because the trace only carried a one-line\n  match summary, not the numbers the agent saw. Adding\n  `ToolCall.result_payload` (the full `NutritionReference` model_dump\n  for every lookup) gave the judge something to check against.\n  Simultaneously, `logic/semantics.py` + a pure `compare_semantics`\n  tool attacked `unit_mismatch`: USDA \"carbohydrate, by difference\"\n  vs CIQUAL `glucides` is a definitional delta, not a disagreement,\n  and the agent should know that before it computes a discrepancy.\n\nThe bucket shape after M4 (judge = gpt-5-chat-1, verifier = gpt-5-mini,\nn = 11):\n\n| concern_kind          | M3 baseline | After M4 |\n|-----------------------|-------------|----------|\n| `correction_provenance` | 7           | **2**    |\n| `wrong_reference`       | 8           | **4**    |\n| `rubric_violation`      | 5           | **2**    |\n| `unit_mismatch`         | 5           | 7\u2020       |\n| `grounded_success_rate` | 0.27        | **0.45** |\n\n\u2020 `unit_mismatch` ticked up because the semantics notes now appear\n*in the trace*; the mismatches were already there, the judge just\ncouldn't cite them before. That's the right direction.\n\nWhat made this loop work was investing in M2, M3 and M5 *before*\ntouching M4. The verifier's schema (structured reasoning, digit-free\nprose), the judge's schema (typed concerns), and the aggregate metric\n(`grounded_success_rate`) are all typed Pydantic models \u2014 so the\nchanges in M4 had somewhere concrete to land. Without any one of\nthem, M4 would have been \"the judge seems happier\", which isn't a\nmetric.\n\nThe remaining buckets (`wrong_reference=4`, `unit_mismatch=7`) are\nthe next session's problem \u2014 probably a better USDA match-relevance\ngate and per-source semantic filtering at the discrepancy layer. The\nloop is set up to answer that the same way.


## 11. The honest correction: golden was theatre, ground truth was missing

The M1–M5 loop above closes with a number (`grounded_success_rate =
0.45`) and an implication that the loop kept turning. It didn't —
not as written. Three things broke that frame and forced a redesign.

**Repeatability.** Re-running `verify` and `judge` on the same input
gave different answers between runs. Different statuses on borderline
items, different proposed corrections, different judge verdicts. That
is fatal for a "before/after bucket numbers in the commit body"
workflow: the buckets weren't measuring my changes, they were
measuring run-to-run noise plus my changes, and at n=11 the noise
floor was loud.

**Golden was measuring me, not the agent.** `eval/golden.py` was
hand-authored: I wrote down the statuses I expected and counted
matches. With no nutritionist in the loop, the golden file encoded my
assumptions about the items, not ground truth. When the agent
disagreed, the right epistemic response was *I'm not sure who's
right* — but the metric reported it as a regression. That's
fake-rigor.

**`metrics.json` was a single number off a noisy single run.** One
verify pass, one judge pass, divide. Not an aggregate, not a
distribution, not a confidence interval. Useful as a smoke test;
misleading as a quality signal.

The fix had three pieces:

1. **Delete the fake-rigor.** `eval/golden.py`, `eval/metrics.py`,
   `outputs/metrics.json`, and their tests are gone. Better no number
   than a misleading one.
2. **Replace it with a stability matrix.** `uv run snaq-verify
   stability food_items.json` runs verify + judge K times and
   aggregates per-item agreement: modal status, status-agreement %,
   confidence mean/stdev, judge `grounded` agreement, Jaccard
   similarity of concern-kind sets. We can't say *correct*, but we
   can say *stable*, and stability is necessary for correctness.
3. **Sweep `reasoning_effort` as the sweep axis.** Once the matrix
   existed, the natural next question was *do we even need
   `medium`/`high`?* So `stability` runs each effort level K times
   and renders an "effort summary" table on top of the per-effort
   detail: status agreement, judge grounded rate, mean tool calls per
   run. Read it as: *what's the lowest effort whose grounded rate
   matches `high`?* That's the cheapest setting to ship, and it falls
   out of the same artefact that proves the agent is stable.

Two model decisions came out of the same redesign. The verifier is
`gpt-5-mini`, a reasoning model that *ignores* `temperature` and warns
if you set it — so effort is the only knob that meaningfully changes
its behaviour, and the sweep is the right way to characterise it. The
judge is `gpt-5-chat`, a non-reasoning model pinned at
`temperature=0`, so it stays a stable reference signal across the
verifier-side sweep. Different model families on each side keeps them
from sharing failure modes.

There is no response cache and no fixed seed. Both would mask the
drift the matrix is designed to surface.

The honest summary of the eval story is: M1–M5 made the verifier and
judge each *more inspectable*; the stability sweep is the first thing
in this repo that actually measures something the user cares about
(consistency, and whether higher effort buys it). Truth — the actual
nutrition values — still requires a nutritionist, and that's listed
in the README as future work, not pretended around.

## 12. Reading the matrix, then sharpening the prompt

The first stability sweep — four effort levels, three runs each, eleven
items — was meant to *characterise* the agent, but it ended up being
diagnostic. Status agreement scaled cleanly with effort (88% at
`minimal`, 100% at `high`). Grounded rate didn't: it sat at roughly
30% across all four levels and refused to climb. Confidence and tool
calls barely moved either.

That non-curve is the interesting signal. If higher reasoning effort
isn't buying grounding, then the residual judge concerns aren't *"the
agent didn't think long enough"* — they're *"the agent doesn't have
the right shape of tool or rule to do this correctly even with infinite
thinking."* Spending more on `gpt-5-mini` reasoning tokens would have
been buying a flatter line.

I went through the judge concerns by kind. Four patterns recurred:
`unit_mismatch` (USDA-vs-CIQUAL carbs/energy treated as a discrepancy
even when our own `compare_semantics_tool` had already explained why
they differ definitionally), `wrong_reference` (USDA's first hit being
"Egg, frozen, pasteurized" when the customer asked about a raw egg),
`rubric_violation` (verifier and judge each holding a slightly
different mental model of "when is confidence allowed to be < 1.0"),
and `variance_reasoning` (agent narrating a catalogue-matched item as
DISCREPANCY instead of using HIGH_VARIANCE). Three of the four are
fixable in the prompt — the rule existed but wasn't binding. The
fourth (`wrong_reference`) is a tool-shape problem: the agent has no
mechanical way to say *"that USDA hit is the wrong food, give me
another"*.

The temptation, of course, was to fix everything: add top-N USDA
candidates with a `match_quality` enum, make `calculate_discrepancy`
itself definitional-aware, introduce a typed
`propose_correction_from_reference_tool` so provenance becomes
structural. All three are real improvements. None of them are what the
brief asked for. I re-read `home_task.md` and stopped: it explicitly
warns against over-engineering and rewards focused, deliberate work,
and at this scope the *cheap* win was to tighten the instructions and
realign the judge against the same rubric.

So Phase 1 was four explicit rules in `INSTRUCTIONS`:

1. Mandate `compare_semantics_tool` *before* `calculate_discrepancy_tool`
   for cross-source comparisons — the definitional check has to happen
   first or it doesn't happen at all.
2. State, as a hard contract rather than a hint, that fields covered
   by a `compare_semantics` note do not count toward DISCREPANCY.
3. Make `HIGH_VARIANCE` *mandatory* (not suggestive) when the
   variance catalogue matches and every exceeding field is in
   `variable_fields`.
4. Cap confidence at 0.8 when two sources disagree on a
   non-definitional field beyond tolerance — closes the rubric
   ambiguity that was driving `rubric_violation` concerns on both
   sides.

Then I rewrote the judge's system prompt to grade against that exact
rubric, so the verifier and the judge stop talking past each other.

The piece I'm proudest of is the smallest one: an
`INSTRUCTIONS_VERSION = "v2"` constant in `agent.py`, surfaced in the
report header and matrix metadata. At two prompts a registry would be
absurd; a stamp is honest. Past about five prompts I'd reach for a
real prompt-management framework. That tradeoff is now explicit in the
README's future-work list rather than implicit in the code.

The numbers came out clean. At `medium` effort, three runs, v1 → v2
(numbers from [outputs/stability/matrix.md](outputs/stability/matrix.md)
for v2; v1 baseline from the prior matrix run that motivated this
phase): status agreement 94% → 100%, grounded rate **27% → 82%**,
concern-kind Jaccard 0.61 → 0.88, tool calls per run barely moved
(8.8 → 9.3), confidence essentially unchanged. `medium` now matches
what previously required `high`. The two items still ungrounded
across runs are exactly the failure modes that need Phase 2 (the
`wrong_reference` egg, and an avocado whose natural variance isn't
yet in the catalogue) — which is the right place for Phase 1 to leave
them.

The deliberate non-decisions here matter as much as the changes.
Phase 2 — top-N USDA candidates, definitional-aware
`calculate_discrepancy`, `propose_correction_from_reference_tool`,
expanded variance catalogue, optional third source — is documented in
the README as concrete future work, not done. A third data source
would have been satisfying and wrong: at n=11 with two sources
already disagreeing on definitional grounds, adding a tiebreaker
without first fixing how the agent *interprets* the existing two
would have just multiplied the noise.

The lesson, written down so I remember it: when a stability sweep
shows consistency scaling but grounding flat, the bottleneck isn't
reasoning capacity, it's *instruction precision and tool shape*. Read
the matrix before reaching for the model picker.

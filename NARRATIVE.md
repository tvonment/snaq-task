# Working narrative

SNAQ asks for the AI conversation log alongside the code. How
the project actually unfolded, where the AI helped, where it pulled
toward bad ideas, and where I overruled it.

This is a curated retrospective, not a transcript dump — the raw VS
Code Copilot Chat exports are in [transcripts/](transcripts/) for
anyone who wants the receipts. The point of this file is the
*editorial pass*: which decisions mattered, where the AI got it
wrong, and what I did about it.

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

The shape of the design that came out of that — five statuses, an
explicit confidence rubric, route-by-item-shape, LLM-decides /
code-computes — is documented properly in [README.md](README.md) and
[DESIGN.md](DESIGN.md). I'm not going to re-state it here.

---

## 2. Where the AI was wrong, and I had to catch it

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

---

## 3. Where the AI earned its keep

- **Scaffolding speed.** The entire `uv`/pyproject setup, models,
  ruff config, test layout, and fixtures came together quickly because
  the AI can type faster than I can.
- **Typed Pydantic boundaries.** Every tool has a Pydantic-modelled
  in/out with `Field(description=...)`. That's the difference between
  "tool calling works once" and "tool calling is reliable." The AI did
  this consistently without being told each time.
- **Plan-first for non-trivial changes.** The polish pass that
  silenced the `httpx` log firehose and hardened OFF against 429s
  came back as a *plan* before any code: per-host semaphore on the
  client (not the runner), Retry-After honoured on 429s, no `rich`
  dependency for progress, `-v`/`-vv` flagged as a follow-up question
  rather than silently included. That's the interaction mode I want
  from a coding agent, and it's what most of the polish work looked
  like.
- **Error handling discipline.** `return_exceptions=True` in the
  gather, per-item `ERROR` status instead of batch crashes, tenacity
  with jitter on 429/5xx. The AI proposed all of these; I just had to
  approve.

---

## 4. Critical self-review (a second pass against the brief)

Before calling it done I had Copilot re-audit the repo against the
brief from scratch — and pasted the same brief plus the repo into
Codex for a second outside opinion. Three findings shipped (USDA
sometimes returning kcal=0, the confidence rubric not punishing
incomplete matches, and a single authoritative source making 1.0
confidence nearly unreachable — fixed with a kJ/Atwater fallback, an
`assess_reference_completeness` cap, and a CIQUAL subset
respectively). The fixes themselves are documented in the README
decisions table.

The point of this section is not "look what I caught." It's that the
brief asks about *reasonableness of heuristics* and *the hard parts
of this task*, and you can't answer those honestly without a critical
second pass. The first pass got the architecture right; the second
pass found the bugs.

---

## 5. The honest correction: golden was theatre, ground truth was missing

After the self-review I built a small eval loop — a hand-authored
golden set, a single `metrics.json` number per run, a milestone log
of bucket counts before/after each change. It looked rigorous. It
wasn't.

Three things broke that frame and forced a redesign.

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

The honest summary of the eval story is: the verifier and judge each
became *more inspectable* through earlier iterations (structured
digit-free reasoning on the verifier; a typed 8-kind concern taxonomy
on the judge); the stability sweep is the first thing in this repo
that actually measures something the user cares about (consistency,
and whether higher effort buys it). Truth — the actual nutrition
values — still requires a nutritionist, and that's listed in the
README as future work, not pretended around.

---

## 6. Reading the matrix, then sharpening the prompt

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

# Working narrative

SNAQ asks for the AI conversation log alongside the code. How
the project actually unfolded, where the AI helped, where it pulled
toward bad ideas, and where I overruled it.

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

**USDA WAF.** Every USDA call from the dev-container returned HTTP 403,
including requests with `DEMO_KEY`. The AI initially tried to "fix" this
by blaming the API key. I ran a `curl` directly and confirmed the
response was a plain nginx 403 — not api.data.gov's JSON rate-limit
body — which meant the Codespaces egress IP range is on a denylist. No
code fix is possible; the workaround is to run locally. This is now
documented in the README.

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

## 6. What I would do differently if I ran it again

- Write the golden-set eval (`eval/golden.py` + `eval/judge.py`, the
  bonus from the brief) earlier, before the live-run debugging cycle.
  It would have caught the content-filter issue with a single reusable
  asset instead of a full 11-item run each time.
- Put the USDA egress check at the top of `runner.py` so it fails fast
  with a readable error instead of 11 parallel tracebacks.
- Add a `--dry-run` that exercises the agent against cached fixtures
  only, to demo the full pipeline without any live keys.

## 7. What SNAQ is actually reading here

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

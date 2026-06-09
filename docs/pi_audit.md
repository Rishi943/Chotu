# PALIV ⇄ pi Architecture Audit

Comparison of PALIV/Chotu's agent core against **pi** (`pi.dev`, earendil-works/badlogicgames),
a production TypeScript agent harness (~190k LOC; the direct analog of Claude Code).

**Scope (as agreed):** two subsystems only —
1. Agent **loop + context compaction + tool-result capping**
2. **LLM provider abstraction + prompt caching**

Horizon: tactical (mergeable on `loop-token-efficiency` now) **and** strategic (north-star).
Everything is filtered through one constraint: **Chotu is an always-on embodied pet with a
fixed persona**, not a terminating coding agent. Several pi patterns are deliberately *wrong*
for that and are called out in [§5 Deliberate divergences](#5-deliberate-divergences--do-not-adopt).

> Method: pi's two subsystems were read in full by two focused sub-agents; the two highest-stakes
> claims (Anthropic cache placement, truncation strategy) were re-verified by hand against source.
> Our side read directly: `core/brain.py`, `core/llm_client.py`, `core/loop_helpers.py`,
> `core/scratchpad.py`, `core/tools.py`.

---

## 0. What you already got right (don't touch these)

The audit is mostly affirming. Your `loop-token-efficiency` work converges on the same shape pi
arrived at, for the same reasons:

| Your design | Why it's correct | pi parallel |
|---|---|---|
| **Append-only window, rare batched trim** (`loop_helpers.py:91` `maybe_compact`) | Stable growing prefix → maximal KV-cache / prompt-cache reuse; the prefix only changes on the rare trim. | pi's session is append-only with a `firstKeptEntryId` pointer; it never rewrites history. |
| **Volatile tail placed LAST** — `[system │ memory │ STATE │ frames]` (`brain.py:92`) | Cache-busting content (the 3 changing frames, the regenerated STATE) sits *after* the stable prefix, so it never invalidates the cache. | pi places the moving cache breakpoint at the end of the *stable* region for exactly this reason. |
| **Mechanical scratchpad instead of an LLM summary** (`scratchpad.py`) | No extra LLM call, no blocking, deterministic. Right for a heartbeat loop. | pi *does* call the LLM to summarize — correct for it (see §5), wrong for you. |
| **One LLM call per heartbeat tick** (`brain.py:257` `run_iteration`) | Paced, predictable, non-twitchy. | pi runs an inner loop until no tool calls remain — correct for a task agent, wrong for a pet (see §5). |
| **Synchronous e-stop + motion_lock** (`tools.py:486`, `core/motion_lock.py`) | Hard obstacle safety in the dispatch path. pi has no embodied-safety needs, so nothing to learn here. | — |

The remaining findings are net-new value, tiered by ROI below.

---

## 1. Scorecard

| # | Finding | Tier | Effort | ROI | Path affected |
|---|---|---|---|---|---|
| T1.1 | **Move/extend the cache breakpoint to the end of the stable prefix**, not just the system message | **1 — do now** | ~20 LOC | **High** (cloud only) | DashScope / Claude |
| T2.1 | Port the cache strategy to the **Claude provider path** (currently zero caching) + parametrize hardcoded `max_tokens`/`thinking` | 2 | small | Med | Claude fallback |
| T2.2 | **Image-downgrade guard**: drop frames when the configured model isn't vision-capable | 2 | small | Med (correctness) | all |
| T2.3 | **Token-pressure compaction trigger** (measure memory tokens, not turn count) | 2 | medium | Med | all |
| T2.4 | Split **cache-read vs cache-write** in the token meter | 2 | small | Low | cloud |
| T3.1 | **Episodic / salient memory** that survives compaction (the persona-safe alternative to pi's summarizer) | **3 — strategic** | medium | **High** | all |
| T3.2 | **Real interrupt**: thread cancellation so the stop-word aborts in-flight work | **3 — strategic** | med–high | High (safety) | all |

No-ops / anti-patterns are in [§5](#5-deliberate-divergences--do-not-adopt).

---

## 2. Tier 1 — do now (tactical, fits this branch)

### T1.1 — Cache the whole stable prefix, not just the system message

**Today** (`llm_client.py:191` `_mark_system_cache`, gated by `_cache_system` at `:73`): on the
DashScope path you put one `cache_control: ephemeral` marker on the **system message only**. So the
cloud caches `[system]`; the entire `memory` window (8–30 turns of text) is re-tokenized and
re-billed every single turn.

**pi's pattern** (`anthropic.ts:1149-1171`, verified): a single ephemeral marker on the **last block
of the last user message**, plus markers on system and the last tool. The conversation marker
**moves forward each turn**, so the cached prefix grows with the conversation. The comment is
literally *"Add cache_control to the last user message to cache conversation history."*

**Why this maps perfectly onto your layout.** Your prefix is `[system │ memory]` and `memory` is
append-only between compactions (`brain.py:56`). So this turn's `[system │ memory]` is exactly last
turn's prefix **plus one appended turn** — a textbook moving-breakpoint cache. The volatile tail
(`STATE`, `frames`) already sits *after* it, so it doesn't interfere.

**Change:** mark `cache_control` on the **last `memory` message** (the boundary just before
`STATE`/frames), in addition to (or instead of) system. Caching up to end-of-memory subsumes
system, so even a single marker there is strictly better than today. Keep the system marker too if
DashScope honors ≥2 breakpoints — it survives across compactions, where the memory marker resets.

Sketch (extends the existing function; `brain.py` tags the boundary via the existing `_origin`
convention so `llm_client` stays declarative):

```python
# brain.py build_loop_messages: tag the last stable message
if memory:
    memory_msgs = strip_internal_fields(memory)
    memory_msgs[-1] = {**memory_msgs[-1], "_cache_boundary": True}   # consumed by llm_client

# llm_client: mark system AND the boundary message (DashScope path)
def _mark_cache_breakpoints(messages):
    # ... existing system marker ...
    for m in reversed(messages):
        if m.pop("_cache_boundary", False):
            _wrap_last_block_with_cache_control(m)   # same wrap as system
            break
```

**Scope honestly:**
- **Cloud (DashScope/Qwen):** this is the win. With `memory` at, say, 3–5k tokens, you stop
  re-billing it every turn — directly on-theme for `loop-token-efficiency` and your
  `chotu_token_economics` notes.
- **Local llama-server (default):** *no-op.* llama.cpp ignores `cache_control` and already reuses
  the KV cache for a stable prefix — which your layout already provides. So this costs nothing
  locally and helps only when you're on cloud (which you do use).
- **Risk:** low. Worst case DashScope ignores the extra marker (no regression).
- **⚠ Verification needs a cloud call** → per `CLAUDE.md` + `feedback_cloud_token_approval`, I will
  **not** run it without explicit approval. Open question O-1 (§6) is the exact thing to measure.

---

## 3. Tier 2 — near-term

### T2.1 — Claude path has *zero* caching + hardcoded limits
`_claude_complete` (`llm_client.py:250`) never sets `cache_control` anywhere, hardcodes
`max_tokens=4096` (`:275`), and ignores `thinking`. If Claude is ever the always-on brain, every
tick re-bills the full `PALIV.md + CHOTU_BASE.md` system prompt. Port the T1.1 breakpoint strategy
to this path (Anthropic natively supports up to 4 breakpoints — system + last message is the
minimum) and thread `max_tokens`/`thinking` through instead of hardcoding. Lower priority only
because Claude is fallback-only.

### T2.2 — Image-downgrade guard
You send 3 JPEG frames every turn (`loop_helpers.py:43` `render_frames`). `PALIV_BRAIN_MODEL` is
user-configurable; point it at a **non-vision** GGUF and you'll ship `image_url` blocks the model
can't parse → errors or silent garbage. pi guards this in its transform pre-pass
(`replaceImagesWithPlaceholder`): if `model.input` lacks `"image"`, images become a text
placeholder. Add a `supports_vision` flag per backend; when false, skip `render_frames` (or
substitute a one-line text note). Tiny, prevents a whole class of silent failure.

### T2.3 — Compact on token pressure, not turn count
`maybe_compact` triggers at `COMPACT_AT=30` assistant turns (`brain.py:38`). pi triggers on real
token pressure: `contextTokens > contextWindow − reserveTokens` (`compaction.ts:196`). Turn count
is a crude proxy — a `wait` turn and a turn carrying a fat tool result count the same.

**Nuance (don't naively use `prompt_tokens`):** your `response.usage.prompt_tokens` is dominated by
the **3 frames** in the volatile tail, which compaction does *not* trim. Triggering on total
`prompt_tokens` would fire compaction because of frames, not because `memory` is large. A correct
token trigger estimates **memory-only** tokens (a `chars/4` heuristic over `memory` is enough — no
tokenizer dependency). Then size for the real target: a 32k-context Qwen wants roughly
`reserve ≈ 4k`, `keep ≈ 6k`, not pi's 16k/20k (sized for 200k frontier models). Medium effort
because of the memory-only accounting; that's the only reason it's not Tier 1.

### T2.4 — Separate cache-read vs cache-write in the meter
pi's `Usage` carries `cacheRead` **and** `cacheWrite` (`types.ts:265`) because they price
differently. Your meter tracks one `cached` counter (`brain.py:67,287`). Your `eff_prompt` math is
*correct* for reads (verified — not a bug), but if DashScope charges a write premium on the turn a
new prefix block is first stored, that turn is slightly under-counted. Low stakes (dev meter only);
split the counter if you want the live cost number to be exact.

---

## 4. Tier 3 — strategic (north-star)

### T3.1 — Episodic memory that survives compaction  ★ highest strategic value
When `maybe_compact` trims 30→8 turns, **everything semantic is lost**: "the human said their name
is Rishi," "the kitchen is to my left," "I already greeted them." The `Scratchpad` only preserves
*mechanical* state (heading, recent motions, sensor health, last utterance) — not episodic facts.
pi solves this with an LLM summary (Goal/Progress/Decisions/Next-Steps/Critical-Context,
`compaction.ts generateSummary`) reinserted as a pinned message.

pi's *full* summarizer is wrong for a pet (§5), but the **gap is real**. Two persona-safe options:

- **(A, recommended) a `remember(fact)` tool.** Chotu *chooses* what's worth keeping; the fact is
  pinned outside the trimmed window. Zero extra LLM calls, free on local, and *more* characterful
  ("I'll remember that!"). Fits your "explore/investigate" direction in
  `project_next_explore_investigate`.
- **(B) summarize-on-trim with the local model.** Cheaper than it sounds (local = free) but it
  **blocks the tick** — must run between heartbeats, never inside `run_iteration`. Heavier; only if
  (A) proves too sparse.

### T3.2 — A real interrupt for the stop-word  ★ safety-relevant
Today the stop-word just `pending_input.push("[stop] freeze…")` (`brain.py:432`) — a *message the
model reads next turn*. It does **not** abort an in-flight LLM call or a motion already dispatched
to the Pi. (Obstacle e-stop *is* hard — `tools.py:486` — but a human "stop!" is soft.) For a
physical robot that's a real latency/safety gap.

pi threads one `AbortController.signal` through the LLM stream **and** every tool, so one
`abort()` cancels everything instantly. The persona *wants* this (a pet that freezes the instant
you say stop is more alive, not less). Staging:
- **Partial (med effort):** cancel the in-flight `chat_complete` (httpx/`asyncio` cancellation) and
  skip dispatching this turn's queued tools. Kills the "finishes its sentence first" lag.
- **Full (higher effort):** also halt an in-flight Pi motion — needs a bridge-side `/abort`.

---

## 5. Deliberate divergences — do NOT adopt

Cargo-culting these from a coding agent would *hurt* Chotu. Listed so the "no" is on record.

| pi pattern | Why it's wrong for Chotu |
|---|---|
| **Inner loop until no tool calls remain** (`agent-loop.ts`) | Chotu is paced; one call per tick is the whole point. An unbounded tool chain would be twitchy and burn tokens. Keep single-call ticks. |
| **Always-summarize on compaction** (LLM call) | A pet lives in the moment; forgetting old turns is *desirable*. Use the cheap `remember` tool (T3.1), not a mandatory summarizer. |
| **Spill-to-disk + 2000-line/50KB head/tail truncation** (`truncate.ts`) | Built for huge `bash`/`grep`/file output. Your tool envelopes are tiny JSON; `cap_result` at 1500 chars (`loop_helpers.py:102`) is plenty and basically never fires. **You are correctly simpler here — leave it.** (One free upgrade if a future tool ever returns a large opaque blob: *drop/replace* it rather than head-slicing — a truncated base64 is useless and still costs tokens.) |
| **Unified `Context`/`Message` discriminated-union model + full transform layer** (`types.ts`, `transform-messages.ts`) | Great at pi's 8 providers; overkill at your 2. Your provider branching in `llm_client` is contained and works. Cherry-pick only the *behaviors* in T2.2 (image guard) — not the architecture. |
| **Streaming everywhere** (`stream.ts`, `AssistantMessageEvent`) | No consumer: you can't act on half a tool call, and "speak is a tool" means no token-by-token TTS. The only payoff (first-token TTS latency) is a separate, larger redesign. Skip. |
| **Model registry / capability tables / `thinkingLevelMap`** (`models.ts`) | A dict literal for 2–3 models beats `models.generated.ts`. |
| **Session tree + branch-summarization** | You have no branching session UI. Irrelevant. |
| **Orphan-tool-call repair** (`transformMessages`) | pi needs it; you don't currently produce orphans — every `tool_call` already gets a result, including suppressed ones (`brain.py:330,336`). File as cheap defensive insurance, not a fix. |

---

## 6. Open questions (need answers before some changes)

- **O-1 — DashScope cache mechanics. ✅ RESOLVED 2026-06-09** (`scripts/test_cache.py --moving`
  against `qwen3.5-flash`). The moving end-of-memory breakpoint works: across 4 growing-prefix
  ticks `cached_tokens` *climbed* with the prefix (4103→4138→4173, 99% hit) instead of plateauing
  at the fixed system size — so memory is no longer re-billed each turn. DashScope **accepts
  `cache_control` on a tool-role block** (call 0 did not error; the §B tool-role fallback is not
  needed). The 2-marker design (system floor + moving boundary) ships as-is with no regression;
  a separate write *premium* can't be isolated from `cached_tokens` alone (that's T2.4's meter
  split, low stakes).
- **O-2 — Horizon for T3.1.** Is a `remember`-style episodic memory in-scope now, or deferred behind
  the explore/investigate sub-agent work in `project_next_explore_investigate`? (Respecting the
  `CLAUDE.md` "no persistence yet" rule — option A is in-process, not SQLite.)

---

## 7. Suggested sequencing

1. **T1.1** cache breakpoint (after O-1 is answered with a one-shot cloud probe).
2. **T2.2** image guard + **T2.1** Claude-path caching (small, independent, robustness).
3. **T2.3** token-pressure trigger (sizes the loop properly for 32k Qwen).
4. **T3.1** episodic memory via `remember` tool — the highest-value persona feature here.
5. **T3.2** real interrupt — schedule deliberately; it touches the Pi bridge.

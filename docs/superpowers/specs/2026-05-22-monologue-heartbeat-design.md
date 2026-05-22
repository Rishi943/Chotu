# Monologue-driven brain with heartbeat

**Date:** 2026-05-22
**Status:** Design — awaiting review
**Supersedes:** the IDLE/PLAY/LISTEN state machine in PALIV.md and the picker design (`2026-05-19-picker-design.md`).

## Motivation

The current design — a small-model picker that chooses between IDLE animations and PLAY habits — produces behaviour that is, structurally, a `while True: random_choice(tools)`. Each tick is amnesiac. There is no internal reason for any given action; the picker is a stochastic dispatcher. Picker observations confirm this: `handwork` was never picked in 25 tries, `explore` was over-picked from IDLE. The choices are arbitrary because the chooser has no inner state to reason from.

We want chotu to feel alive in the sense that **every action is traceable to a thought**. The thought lives in a running inner monologue that grows over the session. The session starts blank at boot — no persistent memory, no spatial map — and chotu builds up its understanding of the room from its own observations as it explores.

## Architecture

### One loop, monologue-driven

There is exactly one brain loop. No state machine, no mode switches, no picker. Each turn the LLM produces:

- **Content** = free-form inner monologue. This is the visible "why". It stays in the rolling context as the brain's working memory.
- **Tool calls** = the actions for the turn. Any tool from the registry, including `speak`, the existing motion/perception tools, and the new habit-tools (`investigate`, `sweep`).

The transcript itself is the memory. No SQLite, no separate store. The monologue refers to what the LLM saw earlier in the same transcript ("the blue thing in the corner — let me get closer") and that gives the next tool call its reason.

Thinking tokens are an independent toggle (Qwen3.5 `<think>` blocks). The monologue and the thinking tokens are different layers — thinking is private reasoning, monologue is the durable "why" that persists in context. Either, both, or neither can be enabled; the structure `monologue → (optional think) → tool calls` is invariant.

### Heartbeat

The brain is woken on a fixed cadence and by events. Modelled on OpenClaw's heartbeat: a scheduled agent turn that runs in the **same session and context**, injecting a synthetic user message and letting the LLM decide whether to act, speak, or no-op.

- **Cadence:** every 10 seconds.
- **Trigger payload:** synthetic user message `[heartbeat]`. Same context window; same monologue history.
- **Tool-chain guard:** if a tool chain is currently executing (any tool call dispatched and not yet returned its result back to the LLM), the heartbeat tick is **skipped, not queued**. The next opportunity is the next scheduled tick after the chain completes. This prevents heartbeat-fragmented habits.
- **Empty-turn drop:** when the LLM returns `{content: "", tool_calls: []}` in response to a `[heartbeat]`, the assistant message AND the triggering `[heartbeat]` user message are removed from the rolling context. The transcript stays meaningful — no `HEARTBEAT_OK` noise. This drop applies only to heartbeat-triggered empty turns; event-triggered or user-triggered empty turns are kept (events carry information worth retaining even if the LLM chooses not to react).

### Events

Three event sources can inject synthetic user messages into the brain's input queue. Events pre-empt the next scheduled heartbeat but still respect the tool-chain guard (with one exception below).

- `wake_word` — laptop wake-word detector fires. Injects `[event] wake_word: "<stt transcript>"`. STT happens before injection so the message carries the user's actual utterance.
- `battery_low` — Pi battery monitor crosses threshold (existing logic in brain.py). Injects `[event] battery_low: <percent>%`. **Bypasses the tool-chain guard** — battery is a hard interrupt.
- `stop_word` — laptop stop-word detector fires. Injects `[event] stop_word`. **Bypasses the tool-chain guard** — stop word is a hard interrupt.

The hard-interrupt bypass for `battery_low` and `stop_word` matches the current PALIV.md contract: these override active tool chains.

### Boot

On startup the brain injects exactly one synthetic user message before any heartbeat ticks fire:

```
[boot] You just woke up. You don't know where you are. The session starts here.
```

This primes the monologue — the first turn after boot is the LLM's first thought in this session.

### Rolling context window

When the message list exceeds ~12,000 tokens, the oldest non-system messages are dropped until under the threshold. System messages (`PALIV.md`, `CHOTU.md`, `HEARTBEAT.md`) are never dropped. Tool result/call pairs are dropped as a unit to keep the transcript coherent.

This is the simplest possible memory bounding. Better strategies (summarisation, salient-event retention) are explicit future work and out of scope here.

## Contracts

### Speech is now a tool

`speak(text)` is a registered tool. The TTS pipeline (`local_speak()` in `core/tools.py`) is unchanged; only the trigger moves from "parse content" to "dispatch tool call". `content` is reserved for the monologue.

Speech budget stays at **MAX 1 `speak` per turn**, enforced inline at dispatch time (second speak in the same turn is dropped with a warning log).

### Tool budgets

- 1 `speak` per turn
- 12 `set_legs` per turn
- 1 `wait` per turn
- No repeated identical tool calls back-to-back
- No `capture_vision` loops — one look, describe, stop

### Hard interrupts

- Battery ≤15% → inject `battery_low` event, bypass tool-chain guard
- Stop word → inject `stop_word` event, bypass tool-chain guard
- Pi offline 3 consecutive chunks → graceful stop (existing behaviour)

### Tool vocabulary v1

Existing (unchanged):
`move`, `pose`, `set_legs`, `do_trick`, `get_distance`, `get_battery`, `capture_vision`, `set_face`, `wait`, `get_perception`, `cast_spell`.

New:
- `speak(text)` — speak one line. Reuses `local_speak()`.
- `investigate()` — scripted: `get_distance` → if obstacle close, `pose("look up")` + `capture_vision`; else `move(forward, 2)` + `capture_vision`. Returns one consolidated result.
- `sweep()` — scripted: 4 quarter-turns with `capture_vision` at each. Returns 4 image results in one tool response.

`cast_spell` is now always available (previous "outside PLAY only" restriction dies with PLAY).

## Component changes

### Kill

- `core/picker.py` — delete entirely.
- `habits/` directory + `habits/README.md` — delete. The "PLAY-state habit prompt" model is gone.
- `core/habits.py` scripted habits (`_do_nothing`, `_yawn`, `_look_around`, `_pushup`, `_twist`, `_swimming`, `_handwork`) — delete. The file is repurposed (see Refactor).
- `core/brain.py` speech parsing — remove `_fire_speak_if_content`, `_pending_speaks` counter, the content-extraction path that fires TTS.
- `PALIV.md` PLAY/LISTEN state machine, "Speech is not a tool" rule, `goal_complete` tool, "cast_spell available outside PLAY" restriction.

### Refactor

- `core/habits.py` — gut existing bodies. File becomes home for `investigate()` and `sweep()` implementations, called via the new tool dispatchers in `core/tools.py`.
- `core/brain.py` main loop — add heartbeat ticker integration, event queue consumer, tool-chain-active guard, boot-message injection, empty-turn drop.
- `core/prompts.py` — load `HEARTBEAT.md` alongside `PALIV.md` + `CHOTU.md`. System prompt becomes `PALIV.md + "\n\n" + CHOTU.md + "\n\n" + HEARTBEAT.md`.
- `core/voice.py` — wake-word path injects `[event] wake_word: ...` into the event queue instead of feeding user input directly. STT logic unchanged.
- `PALIV.md` — rewrite (slice 7).

### Add

- `HEARTBEAT.md` at project root — checklist the LLM consults each tick (have you looked around yet? human been quiet? anything you saw earlier you wanted to revisit? battery?).
- `core/heartbeat.py` — scheduler, tool-chain-active guard, event queue, synthetic-message injector.
- `speak`, `investigate`, `sweep` tool schemas + dispatchers in `core/tools.py`.

### Keep unchanged

- `core/llm_client.py`, `core/pi_client.py`, `core/spells.py`, `core/gui_server.py`, `core/tools.py` (existing tools), Pi-side `pi_bridge/` entirely.

## Slice plan

Each slice ends in a runnable chotu with visible behaviour. Dry tests at each step (`scripts/test_dry.py` pattern); habit slices (3, 4) and the final integration also get on-Pi verification.

### Slice 0 — `speak` becomes a tool

Pure refactor; no behaviour change. Strip content-as-speech in `brain.py`, register `speak(text)` in `core/tools.py`, wire to existing `local_speak()`. The monologue contract starts working because there's nowhere else for speech to go. Update PALIV.md only enough to remove "Speech is not a tool".

**Test:** existing conversation flows; chotu speaks via tool call; no regressions.

### Slice 1 — Heartbeat scheduler scaffolding

Add `core/heartbeat.py` with a 10s tick loop that injects `[heartbeat]` synthetic user messages into `brain.py`'s input queue. Implement the tool-chain-active guard (skip ticks during dispatch). Allow empty assistant turns. Implement empty-turn drop (remove both the synthetic message and the empty assistant response from context). Stub `HEARTBEAT.md` with one line: "Evaluate if you want to do anything."

**Test:** launch brain, watch logs over 60s. Verify ticks fire at 10s intervals, LLM is mostly silent, occasional monologue. Verify no tick fires while a long tool is mid-dispatch.

### Slice 2 — Real `HEARTBEAT.md` + boot message

Write the actual checklist content. Add `[boot]` synthetic message injected exactly once on startup before any tick.

**Test:** cold boot in a real room. Chotu wakes, monologues about being new here, likely calls `capture_vision` or `sweep`, possibly speaks.

### Slice 3 — `investigate` habit-tool

First habit-as-tool. Schema in `core/tools.py`, body in `core/habits.py`. Single LLM tool call → multi-step Pi sequence (distance → conditional pose/move → capture). Returns consolidated result.

**Test:** dry test with faked Pi; on-Pi test triggered by "go check that out".

### Slice 4 — `sweep` habit-tool

Same pattern. 4 quarter-turns + `capture_vision` at each. Returns 4 image results. Verify it does not get fragmented by heartbeat (tool-chain guard from slice 1).

**Test:** dry test; on-Pi test with explicit ask and observed unprompted use.

### Slice 5 — Event-driven triggers

Wire `wake_word`, `battery_low`, `stop_word` event injectors. Hard-interrupt bypass for the latter two. Wake-word path moves from direct user-input feed to event queue.

**Test:** each event independently. Verify wake_word respects tool-chain guard; verify battery_low and stop_word do not.

### Slice 6 — Rolling context window

Trim at ~12k tokens by dropping oldest non-system messages (tool call/result pairs dropped as units). Verify session survives across a trim.

**Test:** long-running session; check trim fires and behaviour remains coherent.

### Slice 7 — Doc collapse

Rewrite `PALIV.md`: drop PLAY/LISTEN state machine, drop "Speech is not a tool", document heartbeat + monologue + speak-as-tool. Update `CLAUDE.md` to match. Bookkeeping only.

## Open items / deferred

- **Monologue structure** — starting free-form per session direction from `HEARTBEAT.md`. May tighten to a structured schema later if free-form drifts.
- **Heartbeat interval tuning** — 10s is a starting point. May go shorter (more alive) or longer (cheaper) after observation.
- **Persistent / cross-session memory** — explicitly out of scope. Every boot is a fresh session.
- **Spatial map / dead reckoning** — explicitly out of scope.
- **Summarisation in the rolling window** — out of scope; simple oldest-first drop only.
- **GUI monologue panel** — `gui_server.py` will likely want to surface the monologue stream eventually. Not in this spec.

## Non-goals

- Persistence across boots.
- Spatial reasoning beyond what fits in transcript text.
- A separate fast/slow loop. There is one loop.
- Drives / mood variables as first-class state. The monologue is the only state.

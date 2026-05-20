# Picker (v1) — design

**Status:** approved 2026-05-19
**Scope:** `core/picker.py` + `scripts/picker_dry.py`. No `brain.py` changes.
**Next spec:** wire picker into a unified IDLE loop in `brain.py`.

---

## Purpose

One LLM call that answers **"what should Chotu do next?"** Returns a tool call: `pick_habit(state, name)`.

The picker is a pure decision function. It does not execute habits, manage hard interrupts, handle wakeword, speak, or touch the Pi. `brain.py` consumes its output.

**Explicitly out of scope for v1:**
- Mood variables (deferred to v1.1 — see "Future work")
- PLAY proposal mechanic ("I'm bored, find me something red?")
- Targeted picks (e.g. `find_object(target="red bottle")`)
- LISTEN state (wakeword-driven, not picker-driven)

## When the picker fires

Defined here for context; enforced by `brain.py` in a later spec.

- **After every IDLE animation completes** — picker chooses the next animation or transitions to PLAY.
- **On PLAY skill exit** (success, give_up, or hard interrupt) — picker returns Chotu to IDLE or chooses a new PLAY skill.
- **Never mid-chunk during PLAY** — picker does not interrupt running skills.

Hard interrupts (battery ≤15%, stop word, Pi offline ≥3 chunks) bypass the picker entirely and force IDLE in `brain.py`.

## Habit catalogue (v1)

Defined as module-level constants in `core/picker.py`:

```python
IDLE_HABITS = ["do_nothing", "dangle_paws", "yawn", "look_around", "shake_paw"]
PLAY_HABITS = ["explore"]
```

- IDLE habits are tiny deterministic `set_legs`/`set_face` sequences executed inline by `brain.py` (no LLM call per execution). Defined in a future `core/idle_habits.py` — out of scope for this spec.
- PLAY habits are LLM-driven skills with their own `habits/<name>/HABIT.md` prompt files. Out of scope for this spec.

The picker only needs the **names** — it does not load or care about habit internals.

## Module interface

```python
# core/picker.py

from dataclasses import dataclass
from typing import Literal

IDLE_HABITS = ["do_nothing", "dangle_paws", "yawn", "look_around", "shake_paw"]
PLAY_HABITS = ["explore"]

State = Literal["idle", "play"]

@dataclass
class PickerInput:
    current_state: State
    recent_picks: list[str]   # last 5 picks, oldest first; may be shorter on cold start

@dataclass
class Pick:
    state: State
    name: str

async def pick_next(ctx: PickerInput, llm: LLMClient) -> Pick:
    """Single picker call. Returns a validated Pick. Never raises."""
```

`pick_next` is the only public function. All validation, fallback, and logging happen inside it. Callers receive a guaranteed-valid `Pick`.

## Input → prompt construction

### System prompt (inline in `picker.py`, ~100 tokens)

> You are Chotu's habit picker. Your only job is to choose what Chotu does next.
>
> You can stay in the current state or transition. Prefer variety over repetition — if the recent picks list shows the same habit twice in a row, pick something else. After many IDLE picks in a row, consider transitioning to PLAY.
>
> Available IDLE habits: do_nothing, dangle_paws, yawn, look_around, shake_paw.
> Available PLAY habits: explore.
>
> Call the `pick_habit` tool exactly once. Do not speak. Do not call any other tool.

The picker prompt is **not** `PALIV.md + CHOTU.md`. Those are large and unrelated to picking. Persona does not leak into the picker because the picker does not produce spoken output.

### User message

```
Current state: {ctx.current_state}.
Recent picks (oldest → newest): {", ".join(ctx.recent_picks) or "none yet"}.
```

That's it. No mood, no perception, no battery, no time-of-day in v1.

## Output: tool call

A single tool schema, defined inline in `picker.py` (not registered in the global brain tool map):

```python
PICK_HABIT_TOOL = {
    "type": "function",
    "function": {
        "name": "pick_habit",
        "description": "Choose Chotu's next state and habit.",
        "parameters": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["idle", "play"]},
                "name":  {"type": "string"},
            },
            "required": ["state", "name"],
        },
    },
}
```

Picker calls `llm.chat_complete` with:
- `tools=[PICK_HABIT_TOOL]`
- `tool_choice={"type": "function", "function": {"name": "pick_habit"}}` (force a single call)
- `extra_body={"chat_template_kwargs": {"enable_thinking": True}}` (thinking ON)

### Validation in `pick_next`

In order:
1. Response has no tool_calls → fallback.
2. First tool_call's `function.name` ≠ `"pick_habit"` → fallback.
3. `function.arguments` fails `json.loads` → fallback.
4. `state` not in `("idle", "play")` → fallback.
5. `name` not in (`IDLE_HABITS` if state=="idle" else `PLAY_HABITS`) → fallback.

**Fallback:** `Pick("idle", "do_nothing")` + `logger.warning("picker fallback: reason=…")`.

The picker is on the always-on path. It must never raise.

## LLM provider details

- **Provider:** same `LLMClient` instance used by `brain.py`. No new connections.
- **Model:** whatever `PALIV_BRAIN_MODEL` resolves to (default Qwen3.5-4B-Q4_K_M).
- **Thinking mode:** ON for picker (overrides the brain's default-off setting).
- **Token budget:** `max_tokens=1024`. The picker's *final* output is tiny, but thinking-mode Qwen3.5 generates 500–900 tokens of `<think>...</think>` before emitting the tool call; anything under ~1024 truncates the JSON arguments mid-string. Verified empirically (128 → 100% fallbacks, 512 → ~25%, 1024 → 0%).

If `llm.chat_complete` raises (llama-server down, timeout, etc.), `pick_next` catches it, logs, and returns the fallback. Brain loop continues.

## Dry-run harness — `scripts/picker_dry.py`

A standalone script for iterating on picker behaviour without the Pi.

### Modes

```bash
# Interactive: prompt for state + recent picks, print one pick
python -m scripts.picker_dry

# Simulate N picks, feed each pick back into recent_picks (FIFO, len ≤ 5),
# print pick-by-pick log and a final histogram
python -m scripts.picker_dry --simulate 50

# Seed initial history (comma-separated, oldest first)
python -m scripts.picker_dry --simulate 20 --seed-history "do_nothing,do_nothing,do_nothing"

# Start in PLAY state (otherwise IDLE)
python -m scripts.picker_dry --simulate 20 --start-state play
```

### Behaviour

- Uses the real `LLMClient` and the real local llama-server. No mocking.
- After `--simulate N` runs, prints a histogram of picks and counts state transitions (idle→play, play→idle).
- Exits non-zero if more than 50% of picks are the fallback (`{idle, do_nothing}` immediately following another `{idle, do_nothing}`) — a smoke signal that the picker isn't working.
- When picker emits `{play, explore}`, the harness logs the transition and continues with `current_state=play`. There is no real PLAY runner yet; this is a stub.

## File layout

```
core/picker.py             new — ~150 LOC
scripts/picker_dry.py      new — ~80 LOC
core/llm_client.py         touched only if it does not yet pass tool_choice through
```

## Risks & known gaps

1. **`tool_choice` enforcement.** llama-server + Qwen3.5 may not strictly honour forced tool_choice. Mitigation: parse the first `tool_call` from the response regardless; if no tool_call present, take the fallback.
2. **Anti-repeat prompt-only.** If the model ignores "prefer variety", the simulate histogram will show it. If it fails, the next iteration is to render `recent_picks` as a frequency summary ("you've done do_nothing 4 of the last 5 picks") instead of a raw list.
3. **Stuck-in-IDLE risk.** Without moods or thresholds, picker may rarely transition to PLAY. v1 accepts this — moods (v1.1) will add the explicit "boredom rising" pressure. The simulate harness exposes the issue.
4. **Cold start.** First call has empty `recent_picks`. Prompt + LLM should handle "none yet" gracefully — verified in dry-run.

## Future work (not this spec)

- **v1.1 mood model:** event-derived signals (recent_picks already exists; add `seconds_since_human_spoke`, `seconds_since_last_play`) rendered as prose in the user message.
- **PLAY proposal:** picker output extended to `{state: "propose", pick: <play_habit>, target: <str>}`.
- **Targeted picks:** `pick_habit(state, name, target?)` with an optional target string for skills like `find_object`.
- **Telemetry:** persist every pick to a JSONL log under `runtime/picks.log` for later analysis.

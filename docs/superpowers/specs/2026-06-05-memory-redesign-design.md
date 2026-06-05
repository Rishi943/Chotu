# Memory redesign — frame window + tool-result persistence

**Date:** 2026-06-05
**Status:** approved (design), ready for implementation plan

## Why

While estimating token cost for a 1-FPS vision loop, two things surfaced:

1. **A measurement script (`measure_fps_budget.py`) mismodeled the brain.** It
   assumed frames + tool results persist across turns. They do not — see below.
   The "49 frames in memory / ~12k reprocessed per turn" figures were artifacts
   of that bad model, not the real code. This spec works from the real behavior.

2. **The real behavior is the opposite extreme.** At the end of every turn
   (`core/brain.py:559-560`) only two things are saved to `memory`:
   ```python
   memory.append({"role": "user", "content": user_input, "_origin": kind})
   memory.append({"role": "assistant", "content": final_text or "", "_origin": kind})
   ```
   Tool calls, tool **results**, and captured **frames** are discarded at turn
   end. So across turns:
   - Frames persist for **zero** turns — no visual short-term memory.
   - The model remembers only **what it said**, never **what happened**
     (e.g. last turn's `get_distance` reading is gone).

We want Chotu to (a) keep a few recent frames as images so it has short-term
visual memory, and (b) remember tool outcomes — while keeping context small and
prompt-cache-friendly.

## Goals

- Persist the **full turn transcript** to memory (user, assistant+tool_calls,
  tool results, frames, assistant reply) so the model remembers what happened.
- Bound frames to the **last 4 images** (`PALIV_FRAME_WINDOW`, default 4); older
  frames are stripped to a tiny text stub. Their meaning survives as the
  assistant's description (the following message).
- Keep text memory bounded by the existing `trim_memory` token ceiling, which
  becomes meaningfully exercised now that tool pairs persist.
- Be prompt-cache-friendly (Variant A): continuous frame-stripping mutates only
  positions ~4 turns back, so the cache still covers the prefix + older text.

## Non-goals (deferred)

- **LLM-summarization compaction** of old text. YAGNI for v1 — text grows slowly
  (two short lines/turn) and `trim_memory` is a sufficient ceiling. Revisit only
  if text memory becomes the bottleneck.
- Tool-schema trimming (the 2,081-token prefix) and GUI work — separate later
  passes, per the agreed order: memory → tool schema → GUI.

## Current behavior (baseline, must not regress)

- `memory: list[dict]` is the rolling store. `build_messages` →
  `[system] + trim_memory(memory) + [current user]`.
- `trim_memory(items, max_tokens=PALIV_MEMORY_TOKENS=12000)` drops oldest
  messages until under budget, dropping assistant-tool_calls + matching
  `role=tool` results as **whole pairs** (logic already present, currently
  dormant because tool pairs are never persisted).
- `evict_old_heartbeats(messages)` keeps the last `HEARTBEAT_WINDOW=5`
  `[heartbeat]` user markers.
- Within a turn, `capture_vision` adds a deferred multimodal user message
  **after all tool results** (llama-server quirk) so the model reacts to the
  frame; it is discarded at turn end.
- Empty heartbeat turns (`iterations == 0`) are not saved.

## Design

### Component 1 — Full-turn persistence

`build_messages` returns `messages = [system] + memory + [current user]`, so the
current user message is the **last** element. Compute the turn start as
`prefix_len = len(messages) - 1` (robust to `trim_memory` having changed
`len(memory)` inside `build_messages`). At the end of `_process` (replacing the
two-line append):

```python
if kind == "heartbeat" and iterations == 0:
    return                      # unchanged: don't persist empty heartbeats
turn = messages[prefix_len:]    # user + assistant(+tool_calls) + tool results + frames + replies
memory.extend(turn)
enforce_frame_window(memory)    # Component 2
```

Notes:
- `messages` already contains exactly the API-shaped dicts (assistant messages
  via `format_assistant_message`, tool results via `format_tool_result`, deferred
  frame user-messages), so `memory.extend(turn)` keeps them API-valid as-is — no
  transformation needed.
- `_origin`: the current user message already carries `_origin=kind` (set in
  `build_messages`), which `evict_old_heartbeats` relies on. The loop-appended
  assistant/tool/frame messages have no `_origin`, which is correct — only user
  turns are tagged. `strip_internal_fields` already runs at send time
  (`brain.py:407`), so persisted `_origin` tags never reach the LLM.
- The deferred-frame ordering (frame after all tool results) is preserved by
  construction, so no `tool → user(image) → tool` sequence is introduced.

### Component 2 — `enforce_frame_window(memory, keep=None)`

New function in `core/brain.py` beside `trim_memory`.

```python
PALIV_FRAME_WINDOW = int(os.getenv("PALIV_FRAME_WINDOW", "4"))

def _is_frame_msg(m: dict) -> bool:
    """A persisted capture: user message whose content list holds an image_url."""
    c = m.get("content")
    return (
        m.get("role") == "user"
        and isinstance(c, list)
        and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c)
    )

def enforce_frame_window(memory: list[dict], keep: int = None) -> None:
    """Keep image bytes only for the newest `keep` frames; strip older frames
    to a text stub. Mutates in place. Idempotent. No-op when frames <= keep."""
    keep = PALIV_FRAME_WINDOW if keep is None else keep
    frame_idxs = [i for i, m in enumerate(memory) if _is_frame_msg(m)]
    for i in frame_idxs[:-keep] if keep > 0 else frame_idxs:
        memory[i] = {"role": "user",
                     "content": "[earlier camera frame — see description below]",
                     "_origin": "frame_stripped"}
```

- Runs once at end of `_process` so the invariant "≤ keep image-frames in
  memory" always holds between turns.
- Cache property: stripping only the (keep+1)-th newest frame each turn changes
  one position ~`keep` turns back; everything before it is byte-identical, so the
  prompt-cache prefix is preserved up to that point.

### Component 3 — Text ceiling (existing, lightly adjusted)

- Keep `trim_memory` as the hard token ceiling. It already drops tool pairs as
  units; verify it also never strands a `role=tool` whose matching assistant
  `tool_calls` message was dropped, now that such pairs actually exist.
- A stripped frame is a plain user message (not a tool result), so the trimmer
  treats it like any user turn — safe.
- `evict_old_heartbeats` is unchanged.

### Data flow per turn

```
build_messages: memory = trim_memory(memory)            (token ceiling)
                messages = [system] + memory + [user];  prefix_len = len(messages) - 1
heartbeat only: evict_old_heartbeats(messages)          (caps sent [heartbeat] markers at 5)
tool loop:      append assistant / tool-result / deferred-frame messages to `messages`
end of turn:    memory.extend(messages[prefix_len:])    (unless empty heartbeat)
                enforce_frame_window(memory)            (≤4 image-frame invariant)
```

`evict_old_heartbeats` operates on the per-turn `messages` list (the sent copy),
not on `memory`; persisted heartbeat markers in `memory` are bounded by
`trim_memory`'s token ceiling. Unchanged from today.

## Error handling / edge cases

- Turn with no `capture_vision`: `enforce_frame_window` is a no-op.
- `capture_vision` fails: no frame message appended; nothing to bound.
- Multiple captures in one turn: all persist within the turn; normalized to ≤4
  images at end of that same turn by `enforce_frame_window`.
- `keep=0` (env override): strip all frames — pure text memory, like today.
- `trim_memory` dropping a turn that contains a frame: fine; frame is a user
  message, dropped whole.

## Testing

New/updated tests in `tests/`:

1. `test_frame_window.py`
   - 6 frame messages → only newest 4 retain `image_url`; older are stubs.
   - Idempotent: second call changes nothing.
   - No-op when ≤4 frames.
   - `keep=0` strips all.
2. `test_memory_persistence.py` (or extend `test_memory_window.py`)
   - After a simulated `capture_vision` turn, memory contains the frame,
     the `tool` result(s), and the assistant description in order.
   - Empty heartbeat turn persists nothing.
3. Extend `trim_memory` tests
   - With persisted tool pairs + a frame, trimming drops whole pairs and never
     leaves an orphan `role=tool`.
4. Update `scripts/measure_fps_budget.py`
   - Model **real** persistence (full turns) + the 4-frame window, so a live
     re-measurement confirms steady-state context and per-turn fresh tokens.

## Verification

- `pytest` green.
- `python -m scripts.measure_fps_budget` (Pi on) shows: ≤4 frames in context,
  steady-state context and per-turn delta consistent with Variant A.
- Manual `python -m scripts.dry_run` / a short live run shows Chotu referencing
  something it saw a few turns earlier (visual short-term memory working).

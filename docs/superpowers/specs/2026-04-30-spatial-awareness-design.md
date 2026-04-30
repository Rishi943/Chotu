# Spatial Awareness Fix — Scan + Context

**Date:** 2026-04-30
**Status:** Approved
**Scope:** Goal-mode spatial reasoning fixes. Out of scope: pose tracker, GUI, thinking toggle (deferred to follow-up specs).

## Problem

Goal mode runs but Chotu is "lost in space":

1. **`scan_environment` doesn't actually scan 360°.** It does 7 turns × 1 step at ~30°/step = 210° of coverage. The remaining 150° (behind the robot) is never photographed.
2. **Compass labels are fictional.** Segments are tagged `N/NE/E/SE/S/SW/W/NW`, but the robot has no idea where true north is — the labels just describe whatever Chotu happens to face on each rotation. They imply absolute directions Chotu cannot resolve.
3. **Map goes stale silently.** Once Chotu turns, "north" no longer means whatever it meant at scan time. The injected `object_map` keeps presenting the old labels as if they were still valid.
4. **Context overflow.** `llama-server` runs at `-c 8192`. Goal runs blow past this once vision images, scan results, and tool history accumulate, killing the run with a context-exceeded error.

## Goal

Give the LLM enough spatial grounding to reason about scan results without hallucinating compass directions, and give it enough context window to actually finish a goal run. Stay simple — no coordinate tracking, no IMU, no SLAM.

## Non-Goals

- Dead-reckoning pose tracking (`(x, y, heading)`). The LLM does not need coordinates to reason about a 6-direction scan. Defer to a future spec if drift becomes a felt problem.
- Live GUI (camera stream + thought log). Separate spec.
- Thinking-mode toggle (`<think>` blocks). Separate spec.
- Calibrating the actual degrees-per-step. Theoretical 30° is good enough for this scope; if drift is severe in practice, recalibrate later.
- Visually confirming "back at start" after scan. The first turn after a scan invalidates the map anyway, so post-scan drift is not actionable.

## Design

### 1. `llama-server` context bump (8192 → 16384)

One-line change to the launch command in `CLAUDE.md`:

```
llama-server ... -c 16384 ...
```

VRAM impact on RTX 3060 6GB: model is ~2.5GB at Q4_K_M, +KV cache for 16K context ≈ 1.5GB, comfortable headroom. No code change.

### 2. `scan_environment` rewrite

**File:** `chotu/brain.py`, function `scan_environment_tool`.

**New constants:**

```python
SCAN_SEGMENTS = 6
TURN_STEPS_PER_SEGMENT = 2   # 6 × 2 × ~30° = ~360°
LABELS = ["front", "front-right", "back-right", "back", "back-left", "front-left"]
DEGREES = [0, 60, 120, 180, 240, 300]
```

**Algorithm:**

1. Photo at segment 0 (no turn yet — Chotu's current heading is "front")
2. For `i in 1..5`: `move("turn right", steps=2, speed=80)`, then photo, then `_describe_objects`
3. If a turn fails, break and store partial map
4. After segment 5, robot is back at ~start heading (6 × 2 × 30° = 360°). No undo turn.

**Map shape (injected into prompt):**

```json
{
  "front (+0°)": ["blue bottle", "wall"],
  "front-right (+60°)": ["fabric bag"],
  "back-right (+120°)": [],
  "back (+180°)": ["chair"],
  "back-left (+240°)": [],
  "front-left (+300°)": ["lamp"]
}
```

Keys combine the body-relative label and the absolute angle clockwise from scan-start heading. The LLM gets a human-readable name for speech/thinking and exact degrees for math, in one string.

**Internal storage** (`object_map` global) keeps the same flat dict shape. The legacy `_timestamp` field is replaced with `_scan_id` (monotonically incremented int) so future logic can detect "this map was from before the last turn" if needed; for now the map is simply cleared on turn.

**Return value (LLM-visible):**

```json
{
  "ok": true,
  "tool": "scan_environment",
  "result": {
    "map": {"front (+0°)": [...], ...},
    "summary": "Found: blue bottle (front), fabric bag (front-right), chair (back), lamp (front-left)"
  }
}
```

The `segments` parameter is removed from the tool schema. It implied flexibility that didn't exist (1–8 segments produced unaligned compass labels) and isn't needed now that 6 is fixed. The LLM calls `scan_environment()` with no args.

### 3. Map invalidation on turn

**Where:** in `_run_one` (or a small wrapper around the move dispatch) in `chotu/brain.py`.

**Rule:** when a `move` tool call dispatches with `direction in {"turn left", "turn right"}` *and* the call returns `ok=True`, clear the global `object_map` dict.

**Not invalidated by:**
- `move("forward", ...)` / `move("backward", ...)` — translation preserves angular bearings well enough for "the bottle is still in front-right" reasoning
- `pose(...)` — these don't rotate the body
- `set_legs(...)` — custom poses don't rotate (they're stationary leg gymnastics)
- Failed turns (estop blocked, Pi unreachable) — the body didn't actually rotate

**Implementation note:** the cleanest hook is in `_run_one` after `dispatch_tool` returns; check name + parsed args + result.ok. Keeps the rule co-located with the dispatch path.

### 4. System prompt update

**File:** `chotu/system_prompt.py`, section 6 (Object map) and section 5 (Sense tools — `scan_environment` description) and the "point at the red cup" example.

New section 6 text:

```
# 6. Object map

When scan results appear in your context, each entry is body-relative:
"front", "front-right", "back-right", "back", "back-left", "front-left".
The number in parentheses (e.g. +60°) is the angle clockwise from where
you were facing when the scan started.

Use the labels for speech and reasoning ("the bottle is front-right, I'll
turn that way"). Use the angles when you need to compute steps: 1 turn
step ≈ 30°, so a target at +60° is ~2 turn-right steps away.

The map clears the moment you turn. If you've turned since the last scan,
the map will not be in your context — re-scan before reasoning about
directions.
```

Update the "point at the red cup" example to use `front-right` instead of `north`.

Update the `scan_environment` line in section 5:

```
- `scan_environment()`: 360° sweep in 6 segments. Returns a body-relative
  map (front, front-right, back-right, back, back-left, front-left).
```

## Data Flow Walkthrough

1. Goal: "find the blue bottle".
2. LLM: `scan_environment()` → 6 photos, 60s elapsed, robot at start heading. Map populated.
3. State block injected next iteration includes `object_map` showing `"back-right (+120°)": ["blue bottle"]`.
4. LLM thinks: "bottle at back-right, turn right ~4 steps." Calls `move("turn right", 4)`.
5. Move dispatches successfully → `_run_one` clears `object_map`.
6. Next iteration: state block has no map. LLM proceeds from memory: "I just turned to face the bottle, take a photo to confirm." Calls `capture_vision()`.
7. Confirms, walks toward bottle, calls `goal_complete(success=True)`.

## Failure Modes

| Failure | Behavior |
|---|---|
| Turn fails mid-scan (estop, Pi unreachable) | Loop breaks at `if not turn.get("ok")`. Partial map stored with whatever segments completed. Summary reflects partial coverage. |
| Drift accumulates across scan | Tolerable. 6 segments at theoretical 30°/step; real drift probably 5–15° over 360°. Labels are approximate by design. |
| LLM acts on stale map after turn | Cannot — map dict is cleared, prompt injection skips empty maps (existing behavior at line 134, 243). |
| LLM walks forward and the map is "wrong" anyway | Acceptable for this scope. Forward motion preserves bearings well enough for short walks; if Chotu wants precision it re-scans. |
| Context still overflows at 16384 | Out of scope; bump again or compress more aggressively in a follow-up. |

## Test Plan (manual, on charged Pi)

After deploying:

1. **Sanity:** `python -m chotu.brain --goal "what's around you?"` → expect one `scan_environment` call, terminal shows 6 segments, summary speaks all 6 directions.
2. **Targeted retrieval:** Place a blue bottle ~120° clockwise from Chotu's start heading. `--goal "find blue bottle"`. Expect: scan tags it as `back-right`, LLM issues `move("turn right", ~4)`, then confirms with vision.
3. **Map invalidation:** Run goal that requires walking — verify the second iteration after a `turn` has no map injection. Check terminal log.
4. **Long run:** Goal with 5+ scans across the run. Confirm no context-exceeded error at 16384.
5. **Dry run:** `python -m scripts.dry_run "scan the room and tell me what's there"` — confirms behavior without a charged Pi (Pi calls faked, but scan logic exercises the same path; will need dry_run mock to handle the new label format).

## Files Touched

- `CLAUDE.md` — launch command (`-c 8192` → `-c 16384`)
- `chotu/brain.py` — `scan_environment_tool` rewrite, `_run_one` map-invalidation hook, constants block
- `chotu/system_prompt.py` — section 5 (tool list), section 6 (object map explainer), example
- `scripts/dry_run.py` — only if its mock for `scan_environment` references the old label set

## Rollback

Revert the three files. No persistent state, no schema migrations.

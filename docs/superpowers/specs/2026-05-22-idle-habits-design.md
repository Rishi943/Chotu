# IDLE Habits — Design Spec

**Date:** 2026-05-22
**Status:** Approved

## Problem

The picker (`core/picker.py`) returns a `Pick(state, name)` but there is nothing to execute it. Habit names like `yawn` and `pushup` exist only as strings — no implementation, no dispatch, no test harness.

## Goal

Define all scripted IDLE habits in one place on the laptop. The Pi bridge is unchanged. Each habit is an async Python function that orchestrates Pi client calls in sequence.

## Scope

- IDLE habits only. PLAY habits (e.g. `explore`) are LLM-driven and out of scope here.
- No changes to Pi bridge (`pi_bridge/server.py`).
- No wiring into `brain.py` yet — that is a separate phase.

---

## Architecture

### New file: `core/habits.py`

Single source of truth for scripted IDLE habit implementations.

```
core/habits.py
  _do_nothing(pi)     — asyncio.sleep(5), no Pi calls
  _yawn(pi)           — set_face → pose → sleep → pose → set_face
  _look_around(pi)    — pose left → sleep → pose right → sleep → pose stand
  _pushup(pi)         — do_trick("pushup")
  _twist(pi)          — do_trick("twist")
  _swimming(pi)       — do_trick("swimming")
  _handwork(pi)       — do_trick("handwork")

  IDLE_HABIT_MAP: dict[str, HabitFn]   # name → coroutine function
  run_habit(name, pi) -> None           # public entry point
```

**`run_habit` contract:**
- Looks up name in `IDLE_HABIT_MAP`
- Logs habit start and completion
- Catches and logs all exceptions — never raises, so the caller (brain) never crashes
- Unknown name → warning log, no-op

### Updated: `core/picker.py`

`IDLE_HABITS` list updated to exactly match `IDLE_HABIT_MAP` keys:

```python
IDLE_HABITS = ["do_nothing", "yawn", "look_around", "pushup", "twist", "swimming", "handwork"]
```

---

## Habit Definitions

| Habit | Type | Pi call sequence | Notes |
|---|---|---|---|
| `do_nothing` | local | — | `asyncio.sleep(5)` only |
| `yawn` | sequence | `set_face(sleeping)` → `pose(look up, speed=30)` → `sleep(1.2)` → `pose(stand, speed=30)` → `set_face(idle)` | slow, deliberate |
| `look_around` | sequence | `pose(look left, speed=40)` → `sleep(0.8)` → `pose(look right, speed=40)` → `sleep(0.8)` → `pose(stand, speed=40)` | returns to stand |
| `pushup` | trick | `do_trick(pushup, speed=60)` | Pi-side tight loop |
| `twist` | trick | `do_trick(twist, speed=60)` | Pi-side tight loop |
| `swimming` | trick | `do_trick(swimming, speed=60)` | Pi-side tight loop |
| `handwork` | trick | `do_trick(handwork, speed=60)` | Pi-side tight loop |

Speeds for sequences are kept low (30-40) to avoid brownout. Tricks are capped at 60 by the bridge (`MAX_MOTION_SPEED`).

---

## Testing Plan

### Phase 1 — Dry test (no Pi required)

A small test script (`scripts/test_habits_dry.py`) instantiates a mock `PiClient` that prints calls instead of hitting the Pi. Runs all 7 habits and asserts the expected call sequence for each. Pass = correct calls in correct order, no exceptions.

### Phase 2 — Live test (one habit at a time on bot)

A small live script (`scripts/test_habits_live.py`) takes a habit name as CLI arg and runs it against the real Pi. Each habit is tested and observed physically before moving to the next.

```bash
python scripts/test_habits_live.py yawn
python scripts/test_habits_live.py look_around
python scripts/test_habits_live.py pushup
# ... etc
```

---

## What This Does NOT Include

- Wiring `run_habit` into `brain.py` (next phase — requires picker→brain integration)
- PLAY habits (`explore` and future additions)
- Any changes to Pi bridge
- Habit scheduling, cooldowns, or history tracking

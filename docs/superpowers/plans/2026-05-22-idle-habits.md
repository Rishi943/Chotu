# Idle Habits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 7 scripted IDLE habits in `core/habits.py` with dry and live test harnesses.

**Architecture:** Each habit is an async Python function on the laptop that calls `PiClient` methods in sequence. `run_habit(name, pi)` is the single public entry point — it dispatches by name, logs, and never raises. Pi bridge is untouched.

**Tech Stack:** Python 3.12, asyncio, pytest, pytest-asyncio, httpx (via PiClient)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `core/habits.py` | **Create** | All 7 habit functions + `IDLE_HABIT_MAP` + `run_habit` |
| `core/picker.py` | **Modify** | Update `IDLE_HABITS` list to match habit map keys |
| `tests/test_picker.py` | **Modify** | Fix one test that references removed habit name `dangle_paws` |
| `tests/test_habits.py` | **Create** | Dry unit tests for all habits using mock PiClient |
| `scripts/test_habits_live.py` | **Create** | Live one-at-a-time test script against real Pi |

---

## Task 1: Update `picker.py` and fix stale test

**Files:**
- Modify: `core/picker.py:19`
- Modify: `tests/test_picker.py:23-24`

- [ ] **Step 1: Update `IDLE_HABITS` in `core/picker.py`**

Replace line 19:
```python
IDLE_HABITS: list[str] = ["do_nothing", "dangle_paws", "yawn", "look_around", "shake_paw"]
```
With:
```python
IDLE_HABITS: list[str] = ["do_nothing", "yawn", "look_around", "pushup", "twist", "swimming", "handwork"]
```

- [ ] **Step 2: Fix stale test in `tests/test_picker.py`**

Replace lines 22-24:
```python
def test_valid_idle_pick():
    r = _resp("pick_habit", json.dumps({"state": "idle", "name": "dangle_paws"}))
    assert _validate(r) == Pick("idle", "dangle_paws")
```
With:
```python
def test_valid_idle_pick():
    r = _resp("pick_habit", json.dumps({"state": "idle", "name": "yawn"}))
    assert _validate(r) == Pick("idle", "yawn")
```

- [ ] **Step 3: Run existing picker tests to confirm they still pass**

```bash
python -m pytest tests/test_picker.py -v
```
Expected: all 11 tests pass.

- [ ] **Step 4: Commit**

```bash
git add core/picker.py tests/test_picker.py
git commit -m "feat(picker): update IDLE_HABITS to final habit names"
```

---

## Task 2: Write failing tests for `core/habits.py`

**Files:**
- Create: `tests/test_habits.py`

- [ ] **Step 1: Create `tests/test_habits.py`**

```python
"""Dry unit tests for core.habits — no Pi required."""

import asyncio
from unittest.mock import patch

import pytest

from core.habits import IDLE_HABIT_MAP, run_habit


class _MockPi:
    """Records all Pi calls. Never raises."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def set_face(self, name: str) -> dict:
        self.calls.append(("set_face", name))
        return {"ok": True}

    async def pose(self, name: str, speed: int = 50) -> dict:
        self.calls.append(("pose", name, speed))
        return {"ok": True}

    async def do_trick(self, name: str, speed: int = 70) -> dict:
        self.calls.append(("do_trick", name, speed))
        return {"ok": True}


class _BrokenPi:
    """Every call raises — used to verify run_habit never propagates."""

    async def set_face(self, **kw):
        raise ConnectionError("Pi unreachable")

    async def pose(self, **kw):
        raise ConnectionError("Pi unreachable")

    async def do_trick(self, **kw):
        raise ConnectionError("Pi unreachable")


# ---------------------------------------------------------------------------
# Map completeness
# ---------------------------------------------------------------------------

def test_idle_habit_map_matches_picker():
    from core.picker import IDLE_HABITS
    assert set(IDLE_HABIT_MAP.keys()) == set(IDLE_HABITS)


# ---------------------------------------------------------------------------
# do_nothing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_do_nothing_makes_no_pi_calls():
    pi = _MockPi()
    with patch("asyncio.sleep"):
        await run_habit("do_nothing", pi)
    assert pi.calls == []


# ---------------------------------------------------------------------------
# yawn
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_yawn_call_sequence():
    pi = _MockPi()
    with patch("asyncio.sleep"):
        await run_habit("yawn", pi)
    assert pi.calls == [
        ("set_face", "sleeping"),
        ("pose", "look up", 30),
        ("pose", "stand", 30),
        ("set_face", "idle"),
    ]


# ---------------------------------------------------------------------------
# look_around
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_look_around_call_sequence():
    pi = _MockPi()
    with patch("asyncio.sleep"):
        await run_habit("look_around", pi)
    assert pi.calls == [
        ("pose", "look left", 40),
        ("pose", "look right", 40),
        ("pose", "stand", 40),
    ]


# ---------------------------------------------------------------------------
# tricks — one Pi call each
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pushup_calls_trick():
    pi = _MockPi()
    await run_habit("pushup", pi)
    assert pi.calls == [("do_trick", "pushup", 60)]


@pytest.mark.asyncio
async def test_twist_calls_trick():
    pi = _MockPi()
    await run_habit("twist", pi)
    assert pi.calls == [("do_trick", "twist", 60)]


@pytest.mark.asyncio
async def test_swimming_calls_trick():
    pi = _MockPi()
    await run_habit("swimming", pi)
    assert pi.calls == [("do_trick", "swimming", 60)]


@pytest.mark.asyncio
async def test_handwork_calls_trick():
    pi = _MockPi()
    await run_habit("handwork", pi)
    assert pi.calls == [("do_trick", "handwork", 60)]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_habit_is_noop():
    pi = _MockPi()
    await run_habit("moonwalk", pi)  # must not raise
    assert pi.calls == []


@pytest.mark.asyncio
async def test_pi_error_does_not_propagate_for_sequence_habit():
    # yawn calls set_face + pose — if Pi is broken, run_habit must still return cleanly
    await run_habit("yawn", _BrokenPi())  # must not raise


@pytest.mark.asyncio
async def test_pi_error_does_not_propagate_for_trick_habit():
    await run_habit("pushup", _BrokenPi())  # must not raise
```

- [ ] **Step 2: Run tests to confirm they all fail (module not found)**

```bash
python -m pytest tests/test_habits.py -v
```
Expected: `ModuleNotFoundError: No module named 'core.habits'` or `ImportError`.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_habits.py
git commit -m "test(habits): add dry unit tests (failing — habits not yet implemented)"
```

---

## Task 3: Implement `core/habits.py`

**Files:**
- Create: `core/habits.py`

- [ ] **Step 1: Create `core/habits.py`**

```python
"""Scripted IDLE habit implementations.

Each habit is an async coroutine: habit(pi: PiClient) -> None.
Brain calls run_habit(name, pi) to execute one.
IDLE_HABIT_MAP keys must stay in sync with picker.IDLE_HABITS.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

from core.pi_client import PiClient

logger = logging.getLogger(__name__)

HabitFn = Callable[[PiClient], Awaitable[None]]


# ---------------------------------------------------------------------------
# Habit implementations
# ---------------------------------------------------------------------------

async def _do_nothing(pi: PiClient) -> None:
    await asyncio.sleep(5)


async def _yawn(pi: PiClient) -> None:
    await pi.set_face("sleeping")
    await pi.pose(name="look up", speed=30)
    await asyncio.sleep(1.2)
    await pi.pose(name="stand", speed=30)
    await pi.set_face("idle")


async def _look_around(pi: PiClient) -> None:
    await pi.pose(name="look left", speed=40)
    await asyncio.sleep(0.8)
    await pi.pose(name="look right", speed=40)
    await asyncio.sleep(0.8)
    await pi.pose(name="stand", speed=40)


async def _pushup(pi: PiClient) -> None:
    await pi.do_trick(name="pushup", speed=60)


async def _twist(pi: PiClient) -> None:
    await pi.do_trick(name="twist", speed=60)


async def _swimming(pi: PiClient) -> None:
    await pi.do_trick(name="swimming", speed=60)


async def _handwork(pi: PiClient) -> None:
    await pi.do_trick(name="handwork", speed=60)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

IDLE_HABIT_MAP: dict[str, HabitFn] = {
    "do_nothing":  _do_nothing,
    "yawn":        _yawn,
    "look_around": _look_around,
    "pushup":      _pushup,
    "twist":       _twist,
    "swimming":    _swimming,
    "handwork":    _handwork,
}


async def run_habit(name: str, pi: PiClient) -> None:
    """Execute a named IDLE habit. Logs and swallows errors so brain loop never crashes."""
    fn = IDLE_HABIT_MAP.get(name)
    if fn is None:
        logger.warning("habits: unknown habit %r — skipping", name)
        return
    try:
        logger.info("habits: running %r", name)
        await fn(pi)
        logger.info("habits: %r complete", name)
    except Exception as e:
        logger.warning("habits: %r raised %s: %s", name, type(e).__name__, e)
```

- [ ] **Step 2: Run all habit tests**

```bash
python -m pytest tests/test_habits.py -v
```
Expected: all 11 tests pass.

- [ ] **Step 3: Run full test suite to check for regressions**

```bash
python -m pytest tests/ -v
```
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add core/habits.py
git commit -m "feat(habits): implement scripted IDLE habits"
```

---

## Task 4: Live test script

**Files:**
- Create: `scripts/test_habits_live.py`

- [ ] **Step 1: Create `scripts/test_habits_live.py`**

```python
#!/usr/bin/env python3
"""Live habit tester — runs one named habit against the real Pi.

Usage:
    python scripts/test_habits_live.py <habit_name>
    python scripts/test_habits_live.py --all     # runs all in sequence with pause between

Examples:
    python scripts/test_habits_live.py yawn
    python scripts/test_habits_live.py pushup
    python scripts/test_habits_live.py --all
"""

import asyncio
import logging
import sys

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from core.habits import IDLE_HABIT_MAP, run_habit
from core.pi_client import PiClient

import os
PI_URL = os.getenv("PI_URL", "http://chotu.local:7000")

HABIT_ORDER = ["do_nothing", "yawn", "look_around", "pushup", "twist", "swimming", "handwork"]


async def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <habit_name> | --all")
        print(f"Available: {', '.join(HABIT_ORDER)}")
        sys.exit(1)

    pi = PiClient(PI_URL)

    try:
        health = await pi.health()
        if not health.get("ok"):
            print(f"Pi bridge not reachable at {PI_URL}. Start it first.")
            sys.exit(1)
        print(f"Pi bridge OK at {PI_URL}")

        if sys.argv[1] == "--all":
            for name in HABIT_ORDER:
                print(f"\n--- Running: {name} ---")
                input("Press Enter to run, Ctrl+C to stop...")
                await run_habit(name, pi)
                print(f"Done: {name}")
        else:
            name = sys.argv[1]
            if name not in IDLE_HABIT_MAP:
                print(f"Unknown habit: {name!r}. Available: {', '.join(HABIT_ORDER)}")
                sys.exit(1)
            print(f"Running habit: {name}")
            await run_habit(name, pi)
            print("Done.")
    finally:
        await pi.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Verify the script is importable (no syntax errors)**

```bash
python -c "import scripts.test_habits_live" 2>&1 || python scripts/test_habits_live.py 2>&1 | head -5
```
Expected: prints usage line (exits 1 because no arg given) — no ImportError or SyntaxError.

- [ ] **Step 3: Commit**

```bash
git add scripts/test_habits_live.py
git commit -m "feat(habits): add live one-at-a-time test script"
```

---

## Live Testing Procedure (after Pi is connected)

Run each habit and observe physically. Check the table below:

```bash
# Start Pi bridge first (in separate terminal via SSH):
# ssh chotu@chotu.local 'sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py'

source .venv/bin/activate

python scripts/test_habits_live.py do_nothing    # should pause 5s silently
python scripts/test_habits_live.py yawn          # face→look up→stand→face
python scripts/test_habits_live.py look_around   # look left→look right→stand
python scripts/test_habits_live.py pushup        # front-legs push-up cycle
python scripts/test_habits_live.py twist         # 4-leg twist wave
python scripts/test_habits_live.py swimming      # slow front-extend sweep
python scripts/test_habits_live.py handwork      # sit→left paw→both paws→right paw→sit
```

| Habit | Pass condition |
|---|---|
| `do_nothing` | Robot holds still for ~5s, no movement |
| `yawn` | OLED goes sleepy, head tilts up, returns to stand, OLED back to idle |
| `look_around` | Body twists left, pauses, twists right, returns to stand |
| `pushup` | Front legs push up and down, rear legs brace |
| `twist` | All legs ripple in a wave pattern |
| `swimming` | Front legs extend and sweep slowly forward |
| `handwork` | Sits, raises left front paw, raises both, lowers, sits |

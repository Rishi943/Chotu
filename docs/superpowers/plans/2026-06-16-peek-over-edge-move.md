# `peek_over` Edge Move Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `peek_over` move — reach a chosen front leg out and freeze it mid-step, hold, lean back, end holding a look-up — exposed as a reel-persona-gated brain tool plus a manual `POST /peek_over` bridge endpoint.

**Architecture:** A hardware-free pure helper (`pi_bridge/peek_poses.py`) builds the leg coordinates; `pi_bridge/server.py` plays them through the existing motion lock and adds the endpoint; the brain side adds a `PiClient.peek_over` wrapper and a persona-gated tool schema + dispatch entry. The pose-builder and the gating logic are unit-tested on the laptop; the live motion is tuned on the Pi.

**Tech Stack:** Python 3.12/3.13, FastAPI (Pi bridge), picrawler 2.1.4, pytest 9.

---

## File Structure

- **Create `pi_bridge/peek_poses.py`** — pure, stdlib-only `peek_over_poses(lead, reach)` returning `[freeze_pose, lean_back_pose]`. Hardware-free so the laptop test suite can import it (the bridge flattens onto the Pi, so `server.py` imports it as a sibling).
- **Create `tests/test_peek_poses.py`** — unit tests for the pose builder (loads the file by path; no hardware).
- **Modify `pi_bridge/server.py`** — `from peek_poses import peek_over_poses`; add `PeekOverRequest`, `_peek_over_blocking`, and `POST /peek_over`.
- **Modify `pi_bridge/deploy.sh`** — also scp `peek_poses.py`.
- **Modify `core/pi_client.py`** — add `peek_over(...)`.
- **Modify `core/tools.py`** — `peek_over_enabled()`, `_PEEK_OVER_SCHEMA`, conditional append to `TOOL_SCHEMAS`, conditional dispatch entry.
- **Create `tests/test_peek_over_tool.py`** — gating + dispatch tests.
- **Modify `core/motion_lock.py`** — add `"peek_over"` to `MOTION_TOOLS`.
- **Modify `tests/test_loop_helpers.py` or new `tests/test_motion_tools.py`** — assert `peek_over` is a motion tool. (Use a new small test file to stay surgical.)
- **Modify `CHOTU_REEL.md`** — one conceit-preserving line.

Leg order in every pose is `[L1=front-right, L2=front-left, L3=rear-left, L4=rear-right]`. Constants from the gait: `X_DEFAULT=45, X_TURN=70, Y_DEFAULT*2=90, Z_DEFAULT=-50, Z_UP=-30`. See spec `docs/superpowers/specs/2026-06-16-peek-over-edge-move-design.md`.

---

## Task 1: Pure pose builder `peek_over_poses`

**Files:**
- Create: `pi_bridge/peek_poses.py`
- Test: `tests/test_peek_poses.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_peek_poses.py
"""Unit tests for the pure peek_over pose builder (no hardware)."""

import importlib.util
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "pi_bridge" / "peek_poses.py"
_spec = importlib.util.spec_from_file_location("peek_poses", _PATH)
peek_poses = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(peek_poses)
peek_over_poses = peek_poses.peek_over_poses


def test_left_shallow_freeze_is_mid_step_frame():
    freeze, lean = peek_over_poses("left", "shallow")
    # front-left (leg 2) lifted (Z_UP=-30) and reaching forward (x=X_TURN=70)
    assert freeze == [[45, 45, -50], [70, 0, -30], [45, 0, -50], [45, 45, -50]]


def test_left_deep_swings_forward():
    freeze, _ = peek_over_poses("left", "deep")
    assert freeze == [[45, 45, -50], [45, 90, -30], [45, 0, -50], [45, 45, -50]]


def test_right_is_parity_mirror_of_left():
    # parity-1 transform swaps legs 1<->2 and 3<->4: [s1, s0, s3, s2]
    left_freeze, _ = peek_over_poses("left", "shallow")
    right_freeze, _ = peek_over_poses("right", "shallow")
    assert right_freeze == [left_freeze[1], left_freeze[0], left_freeze[3], left_freeze[2]]
    assert right_freeze == [[70, 0, -30], [45, 45, -50], [45, 45, -50], [45, 0, -50]]


def test_lean_back_raises_front_feet():
    _, lean = peek_over_poses("left", "shallow")
    assert lean == [[45, 45, -30], [45, 0, -30], [45, 0, -50], [45, 45, -50]]


def test_invalid_lead_raises():
    with pytest.raises(ValueError):
        peek_over_poses("up", "shallow")


def test_invalid_reach_raises():
    with pytest.raises(ValueError):
        peek_over_poses("left", "huge")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_peek_poses.py -v`
Expected: FAIL — `FileNotFoundError` / module load error (`peek_poses.py` does not exist yet).

- [ ] **Step 3: Write minimal implementation**

```python
# pi_bridge/peek_poses.py
"""Pure pose builder for the `peek_over` move. Stdlib-only so it imports on the
laptop (tests) and on the Pi (server.py imports it as a flat sibling).

Leg order in every pose: [L1=front-right, L2=front-left, L3=rear-left, L4=rear-right].
Coordinates are derived from the picrawler forward gait so the frozen pose is an
exact mid-step frame. Constants: X_DEFAULT=45, X_TURN=70, Y_DEFAULT*2=90,
Z_DEFAULT=-50 (stand), Z_UP=-30 (foot lifted)."""

# Front-left (leg 2) leads — the parity-0 frame.
_FREEZE_LEFT = {
    "shallow": [[45, 45, -50], [70, 0, -30], [45, 0, -50], [45, 45, -50]],
    "deep":    [[45, 45, -50], [45, 90, -30], [45, 0, -50], [45, 45, -50]],
}

# Retract the reaching foot and raise both front feet to Z_UP so the nose dips
# and weight shifts rearward (no full gait step). Symmetric — same for both leads.
_LEAN_BACK = [[45, 45, -30], [45, 0, -30], [45, 0, -50], [45, 45, -50]]


def peek_over_poses(lead: str, reach: str) -> list:
    """Return [freeze_pose, lean_back_pose] for the given lead leg and reach depth.

    lead:  "left" (front-left/leg2 leads) | "right" (front-right/leg1 leads)
    reach: "shallow" (lifted + x reach) | "deep" (lifted + swung forward)
    """
    if lead not in ("left", "right"):
        raise ValueError(f"lead must be 'left' or 'right', got {lead!r}")
    if reach not in ("shallow", "deep"):
        raise ValueError(f"reach must be 'shallow' or 'deep', got {reach!r}")

    left = _FREEZE_LEFT[reach]
    if lead == "left":
        freeze = [list(c) for c in left]
    else:  # parity-1 transform: swap legs 1<->2 and 3<->4
        freeze = [list(left[1]), list(left[0]), list(left[3]), list(left[2])]
    return [freeze, [list(c) for c in _LEAN_BACK]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_peek_poses.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add pi_bridge/peek_poses.py tests/test_peek_poses.py
git commit -m "feat(peek_over): pure pose builder for the edge move"
```

---

## Task 2: Bridge endpoint `POST /peek_over`

**Files:**
- Modify: `pi_bridge/server.py`
- Modify: `pi_bridge/deploy.sh`

No laptop unit test (server.py imports hardware). Verified by `py_compile` here and on the Pi in Task 7.

- [ ] **Step 1: Add the import**

In `pi_bridge/server.py`, with the other local imports near the top (after `from picrawler import Picrawler`, line ~34), add:

```python
from peek_poses import peek_over_poses
```

- [ ] **Step 2: Add the request model**

Near the other `BaseModel` request classes (around `SetLegsRequest`, line ~204), add:

```python
class PeekOverRequest(BaseModel):
    lead: str               # "left" | "right" — which front leg freezes mid-air
    reach: str = "shallow"  # "shallow" | "deep"
    pause_s: float = 1.5    # hold time at the frozen frame
    speed: int = 60
```

- [ ] **Step 3: Add the blocking choreography + endpoint**

After the `/set_legs` endpoint (around line 319), add:

```python
def _peek_over_blocking(lead: str, reach: str, pause_s: float, speed: int) -> None:
    freeze, lean_back = peek_over_poses(lead, reach)   # validates lead/reach
    crawler.do_step("stand", 40)
    crawler.do_step(freeze, speed)        # reach + lift the chosen front leg
    time.sleep(pause_s)                   # hold the mid-step frame
    crawler.do_step(lean_back, speed)     # recoil: weight shifts back
    crawler.do_action("look up", 1, speed)  # end holding the look-up
    crawler.stand_position = 0            # reset gait parity for later move calls


@app.post("/peek_over")
async def peek_over(req: PeekOverRequest):
    start = time.time()
    speed = min(req.speed, MAX_MOTION_SPEED)
    logging.info(f"POST /peek_over  lead={req.lead} reach={req.reach} pause_s={req.pause_s} speed={speed}")
    try:
        async with _motion_section():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: _peek_over_blocking(req.lead, req.reach, req.pause_s, speed),
            )
        result = _envelope("peek_over", {
            "lead": req.lead, "reach": req.reach, "pause_s": req.pause_s,
        }, start)
        logging.info(f"  peek_over ok ({result['duration_ms']}ms)")
        return result
    except Exception as e:
        logging.error(f"  peek_over error: {e}")
        return _envelope("peek_over", {"lead": req.lead, "reach": req.reach}, start, str(e))
```

- [ ] **Step 4: Update deploy.sh to ship the new module**

In `pi_bridge/deploy.sh`, after the `scp pi_bridge/server.py ...` line, add:

```bash
scp pi_bridge/peek_poses.py ${PI_USER}@${PI_HOST}:${REMOTE_DIR}/peek_poses.py
```

- [ ] **Step 5: Syntax-check both files locally**

Run: `source .venv/bin/activate && python -m py_compile pi_bridge/peek_poses.py pi_bridge/server.py && echo "compile ok"`
Expected: prints `compile ok` (syntax valid; runtime imports verified on the Pi in Task 7).

- [ ] **Step 6: Commit**

```bash
git add pi_bridge/server.py pi_bridge/deploy.sh
git commit -m "feat(peek_over): bridge POST /peek_over choreography + deploy"
```

---

## Task 3: `PiClient.peek_over`

**Files:**
- Modify: `core/pi_client.py`

- [ ] **Step 1: Add the method**

In `core/pi_client.py`, after the `set_legs` method (line ~29), add:

```python
    async def peek_over(self, lead: str, reach: str = "shallow",
                        pause_s: float = 1.5, speed: int = 60) -> dict:
        return await self._post_slow("/peek_over", "peek_over", {
            "lead": lead, "reach": reach, "pause_s": pause_s, "speed": speed,
        })
```

- [ ] **Step 2: Verify it imports**

Run: `source .venv/bin/activate && python -c "from core.pi_client import PiClient; assert hasattr(PiClient, 'peek_over'); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add core/pi_client.py
git commit -m "feat(peek_over): PiClient.peek_over wrapper"
```

---

## Task 4: Persona-gated tool schema + dispatch

**Files:**
- Modify: `core/tools.py`
- Test: `tests/test_peek_over_tool.py`

`peek_over_enabled()` reads `PALIV_PERSONA` at call time so `build_dispatch` (a
function) is testable without reimport. `TOOL_SCHEMAS` is import-time, gated the
same way the launcher sets the flag before `core.tools` is imported.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_peek_over_tool.py
"""peek_over is a reel-persona-gated tool: present only when PALIV_PERSONA=reel."""

import asyncio
from unittest.mock import MagicMock

from core.tools import peek_over_enabled, _PEEK_OVER_SCHEMA, build_dispatch


def test_enabled_only_for_reel():
    assert peek_over_enabled({"PALIV_PERSONA": "reel"}) is True
    assert peek_over_enabled({"PALIV_PERSONA": "base"}) is False
    assert peek_over_enabled({}) is False


def test_schema_shape():
    fn = _PEEK_OVER_SCHEMA["function"]
    assert fn["name"] == "peek_over"
    assert "lead" in fn["parameters"]["properties"]
    assert fn["parameters"]["properties"]["lead"]["enum"] == ["left", "right"]
    assert fn["parameters"]["required"] == ["lead"]


def test_dispatch_includes_peek_over_when_reel(monkeypatch):
    monkeypatch.setenv("PALIV_PERSONA", "reel")
    d = build_dispatch(MagicMock(), asyncio.Event())
    assert "peek_over" in d


def test_dispatch_excludes_peek_over_by_default(monkeypatch):
    monkeypatch.delenv("PALIV_PERSONA", raising=False)
    d = build_dispatch(MagicMock(), asyncio.Event())
    assert "peek_over" not in d
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_peek_over_tool.py -v`
Expected: FAIL — `ImportError: cannot import name 'peek_over_enabled'`.

- [ ] **Step 3: Implement in `core/tools.py`**

After the `TOOL_SCHEMAS = [ ... ]` list literal closes (after line ~210), add:

```python
def peek_over_enabled(env=None) -> bool:
    """True when the reel persona is active (PALIV_PERSONA=reel)."""
    env = os.environ if env is None else env
    return env.get("PALIV_PERSONA") == "reel"


_PEEK_OVER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "peek_over",
        "description": (
            "Reach one front leg out and freeze it mid-step, hold, then lean back "
            "and look up. A held, deliberate motion — not a walk."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "lead": {
                    "type": "string",
                    "enum": ["left", "right"],
                    "description": "Which front leg reaches out and freezes.",
                },
                "reach": {
                    "type": "string",
                    "enum": ["shallow", "deep"],
                    "description": "How far the leg reaches. Default shallow.",
                },
                "pause_s": {
                    "type": "number",
                    "description": "Seconds to hold the frozen frame. Default 1.5.",
                },
            },
            "required": ["lead"],
        },
    },
}

if peek_over_enabled():
    TOOL_SCHEMAS.append(_PEEK_OVER_SCHEMA)
```

Then in `build_dispatch`, change the `return { ... }` to build, conditionally
extend, and return. Replace:

```python
    return {
        "move":           lambda **kw: _gated(motion_lock, "move", lambda **k: pi.move(**k))(**kw) if not estop.is_set() else _blocked_coro("move"),
        "pose":           lambda **kw: _gated(motion_lock, "pose", lambda **k: pi.pose(**k))(**kw),
        "get_distance":   lambda **kw: pi.get_distance(),
        "get_battery":    lambda **kw: pi.get_battery(),
        "set_face":       lambda **kw: pi.set_face(**kw),
        "wait":           lambda **kw: local_wait(**kw),
        "cast_spell":     lambda **kw: _do_cast_spell(pi, **kw),
        "speak":          lambda **kw: _do_speak(face_pi=pi, muted=mute, **kw),
    }
```

with:

```python
    dispatch = {
        "move":           lambda **kw: _gated(motion_lock, "move", lambda **k: pi.move(**k))(**kw) if not estop.is_set() else _blocked_coro("move"),
        "pose":           lambda **kw: _gated(motion_lock, "pose", lambda **k: pi.pose(**k))(**kw),
        "get_distance":   lambda **kw: pi.get_distance(),
        "get_battery":    lambda **kw: pi.get_battery(),
        "set_face":       lambda **kw: pi.set_face(**kw),
        "wait":           lambda **kw: local_wait(**kw),
        "cast_spell":     lambda **kw: _do_cast_spell(pi, **kw),
        "speak":          lambda **kw: _do_speak(face_pi=pi, muted=mute, **kw),
    }
    if peek_over_enabled():
        dispatch["peek_over"] = lambda **kw: _gated(motion_lock, "peek_over", lambda **k: pi.peek_over(**k))(**kw)
    return dispatch
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_peek_over_tool.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add core/tools.py tests/test_peek_over_tool.py
git commit -m "feat(peek_over): reel-persona-gated tool schema + dispatch"
```

---

## Task 5: Register `peek_over` with the motion lock

**Files:**
- Modify: `core/motion_lock.py:18`
- Test: `tests/test_motion_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_motion_tools.py
"""peek_over must serialize with other motion on the brain side."""

from core.motion_lock import MOTION_TOOLS


def test_peek_over_is_a_motion_tool():
    assert "peek_over" in MOTION_TOOLS


def test_existing_motion_tools_unchanged():
    assert {"move", "set_legs", "pose"} <= MOTION_TOOLS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_motion_tools.py -v`
Expected: FAIL — `test_peek_over_is_a_motion_tool` asserts False.

- [ ] **Step 3: Implement**

In `core/motion_lock.py:18`, change:

```python
MOTION_TOOLS = frozenset({"move", "set_legs", "pose", "do_trick"})
```

to:

```python
MOTION_TOOLS = frozenset({"move", "set_legs", "pose", "do_trick", "peek_over"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_motion_tools.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add core/motion_lock.py tests/test_motion_tools.py
git commit -m "feat(peek_over): register with motion lock"
```

---

## Task 6: Reel persona note (conceit-preserving)

**Files:**
- Modify: `CHOTU_REEL.md`

The reel conceit is that Chotu does not know what its tools do. Do **not** add a
"use peek_over at the edge" instruction. Add a neutral line so the move reads
in-world if the model discovers it; precise shoot timing uses the manual
`POST /peek_over` trigger.

- [ ] **Step 1: Add the line**

In `CHOTU_REEL.md`, in the section that lists the body/abilities (near line 14–16,
"You have legs… A list of tools with names you don't recognise yet."), append one
sentence to that paragraph:

```
One of those tools lets you reach a single leg out and hold it there, mid-motion — you don't know that yet.
```

- [ ] **Step 2: Verify the prompt still composes**

Run: `source .venv/bin/activate && PALIV_PERSONA=reel python -c "from core.prompts import load_system_prompt; assert 'reach a single leg' in load_system_prompt(); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add CHOTU_REEL.md
git commit -m "docs(reel): hint at the held-leg tool, conceit-preserving"
```

---

## Task 7: Deploy to the Pi + manual hardware tuning

**Files:** none (deploy + manual verification). Requires the bridge stopped, then restarted.

- [ ] **Step 1: Run the full laptop suite (no regressions)**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: PASS — previous total + the new `test_peek_poses.py` (6), `test_peek_over_tool.py` (4), `test_motion_tools.py` (2).

- [ ] **Step 2: Copy bridge files to the Pi**

Run: `scp pi_bridge/server.py pi_bridge/peek_poses.py chotu@chotu.local:~/chotu-bridge/`
Expected: both files copied.

- [ ] **Step 3: Verify the bridge imports cleanly on the Pi**

Run: `ssh chotu@chotu.local 'cd ~/chotu-bridge && python3 -c "import peek_poses; print(peek_poses.peek_over_poses(\"left\",\"shallow\")[0])"'`
Expected: prints `[[45, 45, -50], [70, 0, -30], [45, 0, -50], [45, 45, -50]]`.

- [ ] **Step 4: Restart the bridge (operator)**

Tell the user to restart the bridge so the new endpoint loads:
`sudo pkill -f server.py; sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py`
Wait for it to come up (stand + settle).

- [ ] **Step 5: Fire it on the table, BOTH leads, shallow first (off-edge)**

```bash
PI=http://192.168.0.190:7000
curl -s -X POST "$PI/peek_over" -H "Content-Type: application/json" -d '{"lead":"left","reach":"shallow","pause_s":1.5}'
curl -s -X POST "$PI/peek_over" -H "Content-Type: application/json" -d '{"lead":"right","reach":"shallow","pause_s":1.5}'
```
Expected: envelope `ok:true, tool:"peek_over"`; the named front leg lifts and
reaches forward, holds ~1.5s, the body leans back, then tilts up and holds.
Confirm the correct leg moves for each `lead`.

- [ ] **Step 6: Tune live, then try deep away from the edge**

Adjust `pause_s` to taste; if the lean-back or reach needs work, tune the
constants in `pi_bridge/peek_poses.py` (`_LEAN_BACK`, `_FREEZE_LEFT["deep"]`),
re-scp, restart, retry. Test `reach:"deep"` only away from the edge first (COM
shifts forward):
```bash
curl -s -X POST "$PI/peek_over" -H "Content-Type: application/json" -d '{"lead":"right","reach":"deep","pause_s":2.0}'
```
Expected: a bigger, more dramatic reach; no tip-over on a charged pack.

- [ ] **Step 7: Commit any tuning changes**

```bash
git add pi_bridge/peek_poses.py
git commit -m "tune(peek_over): table-tuned lean-back / reach values"
```

---

## Self-Review Notes

- **Spec coverage:** pose builder + exact coordinates + left/right mirror (Task 1);
  choreography stand→freeze→hold→lean-back→look-up→parity-reset and `/peek_over`
  manual endpoint (Task 2); `PiClient.peek_over` (Task 3); reel-gated schema +
  dispatch (Task 4); motion-lock registration (Task 5); reel persona note
  (Task 6); deploy + manual tuning incl. deep-off-edge safety (Task 7). Endpoint
  named `peek_over` consistently (spec's earlier `/edge_step` was renamed).
- **No placeholders:** every code step shows full code; commands have expected
  output; tuning step names exact constants to change.
- **Type consistency:** `peek_over_poses(lead, reach) -> [freeze, lean_back]`,
  `peek_over_enabled(env=None) -> bool`, `_PEEK_OVER_SCHEMA`, `PeekOverRequest`
  fields (`lead, reach, pause_s, speed`), `PiClient.peek_over(lead, reach, pause_s,
  speed)`, and the `peek_over` dispatch/MOTION_TOOLS name match across Tasks 1–7.
```

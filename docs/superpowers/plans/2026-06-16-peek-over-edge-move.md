# `peek_over` Edge Move Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `peek_over` move — reach a chosen front leg out and freeze it mid-step, hold, lean back, end holding a look-up — exposed as a reel-persona-gated brain tool plus a manual `POST /peek_over` bridge endpoint.

**Architecture:** All choreography lives inline in `pi_bridge/server.py` (like the existing trick/pose functions): a module-level `_peek_over_poses(lead, reach)` returns the coordinates, `_peek_over_blocking` plays them through the existing motion lock, and `POST /peek_over` is the manual endpoint. The brain side adds a `PiClient.peek_over` wrapper and a reel-persona-gated tool schema + dispatch entry. Brain-side gating is unit-tested on the laptop; the bridge motion is verified/tuned on the Pi (matching how the other moves are tested).

**Tech Stack:** Python 3.12/3.13, FastAPI (Pi bridge), picrawler 2.1.4, pytest 9.

---

## File Structure

- **Modify `pi_bridge/server.py`** — add `_peek_over_poses(lead, reach)`, `_peek_over_blocking(...)`, `PeekOverRequest`, and `POST /peek_over`. No new file; no `deploy.sh` change (server.py is already deployed/scp'd).
- **Modify `core/pi_client.py`** — add `peek_over(...)`.
- **Modify `core/tools.py`** — `peek_over_enabled()`, `_PEEK_OVER_SCHEMA`, conditional append to `TOOL_SCHEMAS`, conditional dispatch entry.
- **Create `tests/test_peek_over_tool.py`** — gating + dispatch tests (laptop-testable).
- **Modify `core/motion_lock.py`** — add `"peek_over"` to `MOTION_TOOLS`.
- **Create `tests/test_motion_tools.py`** — assert `peek_over` is a motion tool.
- **Modify `CHOTU_REEL.md`** — one conceit-preserving line.

Leg order in every pose is `[L1=front-right, L2=front-left, L3=rear-left, L4=rear-right]`. Constants from the picrawler forward gait: `X_DEFAULT=45, X_TURN=70, Y_DEFAULT*2=90, Z_DEFAULT=-50 (stand), Z_UP=-30 (foot lifted)`. See spec `docs/superpowers/specs/2026-06-16-peek-over-edge-move-design.md`. Coordinate correctness is verified on hardware in Task 6 (the table check confirms the right leg lifts for each `lead`).

---

## Task 1: Bridge `_peek_over_poses` + `_peek_over_blocking` + `POST /peek_over`

**Files:**
- Modify: `pi_bridge/server.py`

No laptop unit test (server.py imports hardware). Verified by `py_compile` here and on the Pi in Task 6.

- [ ] **Step 1: Add the request model**

In `pi_bridge/server.py`, near the other `BaseModel` request classes (around `SetLegsRequest`, line ~204), add:

```python
class PeekOverRequest(BaseModel):
    lead: str               # "left" | "right" — which front leg freezes mid-air
    reach: str = "shallow"  # "shallow" | "deep"
    pause_s: float = 1.5    # hold time at the frozen frame
    speed: int = 60
```

- [ ] **Step 2: Add the pose helper, blocking choreography, and endpoint**

After the `/set_legs` endpoint (around line 319), add:

```python
# Front-left (leg 2) leads — the picrawler forward-gait parity-0 mid-step frame.
_PEEK_FREEZE_LEFT = {
    "shallow": [[45, 45, -50], [70, 0, -30], [45, 0, -50], [45, 45, -50]],
    "deep":    [[45, 45, -50], [45, 90, -30], [45, 0, -50], [45, 45, -50]],
}
# Retract the reaching foot and raise both front feet to Z_UP so the nose dips and
# weight shifts rearward (no full gait step). Symmetric — same for both leads.
_PEEK_LEAN_BACK = [[45, 45, -30], [45, 0, -30], [45, 0, -50], [45, 45, -50]]


def _peek_over_poses(lead: str, reach: str):
    """Return (freeze_pose, lean_back_pose). lead: left|right, reach: shallow|deep."""
    if lead not in ("left", "right"):
        raise ValueError(f"lead must be 'left' or 'right', got {lead!r}")
    if reach not in ("shallow", "deep"):
        raise ValueError(f"reach must be 'shallow' or 'deep', got {reach!r}")
    left = _PEEK_FREEZE_LEFT[reach]
    if lead == "left":
        freeze = [list(c) for c in left]
    else:  # parity-1 transform: swap legs 1<->2 and 3<->4
        freeze = [list(left[1]), list(left[0]), list(left[3]), list(left[2])]
    return freeze, [list(c) for c in _PEEK_LEAN_BACK]


def _peek_over_blocking(lead: str, reach: str, pause_s: float, speed: int) -> None:
    freeze, lean_back = _peek_over_poses(lead, reach)   # validates lead/reach
    crawler.do_step("stand", 40)
    crawler.do_step(freeze, speed)          # reach + lift the chosen front leg
    time.sleep(pause_s)                     # hold the mid-step frame
    crawler.do_step(lean_back, speed)       # recoil: weight shifts back
    crawler.do_action("look up", 1, speed)  # end holding the look-up
    crawler.stand_position = 0              # reset gait parity for later move calls


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

- [ ] **Step 3: Syntax-check locally**

Run: `source .venv/bin/activate && python -m py_compile pi_bridge/server.py && echo "compile ok"`
Expected: prints `compile ok` (syntax valid; runtime verified on the Pi in Task 6).

- [ ] **Step 4: Commit**

```bash
git add pi_bridge/server.py
git commit -m "feat(peek_over): bridge POST /peek_over choreography"
```

---

## Task 2: `PiClient.peek_over`

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

## Task 3: Persona-gated tool schema + dispatch

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

## Task 4: Register `peek_over` with the motion lock

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

## Task 5: Reel persona note (conceit-preserving)

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

## Task 6: Deploy to the Pi + manual hardware tuning

**Files:** none (deploy + manual verification). Requires the bridge stopped, then restarted.

- [ ] **Step 1: Run the full laptop suite (no regressions)**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: PASS — previous total + the new `test_peek_over_tool.py` (4) and `test_motion_tools.py` (2).

- [ ] **Step 2: Copy server.py to the Pi**

Run: `scp pi_bridge/server.py chotu@chotu.local:~/chotu-bridge/server.py`
Expected: file copied.

- [ ] **Step 3: Verify the bridge compiles on the Pi**

Run: `ssh chotu@chotu.local 'python3 -m py_compile ~/chotu-bridge/server.py && echo "pi compile ok"'`
Expected: prints `pi compile ok`.

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
Confirm the correct leg moves for each `lead` (this is the coordinate/mirror check).

- [ ] **Step 6: Tune live, then try deep away from the edge**

Adjust `pause_s` to taste; if the lean-back or reach needs work, tune the
constants in `pi_bridge/server.py` (`_PEEK_LEAN_BACK`, `_PEEK_FREEZE_LEFT["deep"]`),
re-scp, restart, retry. Test `reach:"deep"` only away from the edge first (COM
shifts forward):
```bash
curl -s -X POST "$PI/peek_over" -H "Content-Type: application/json" -d '{"lead":"right","reach":"deep","pause_s":2.0}'
```
Expected: a bigger, more dramatic reach; no tip-over on a charged pack.

- [ ] **Step 7: Commit any tuning changes**

```bash
git add pi_bridge/server.py
git commit -m "tune(peek_over): table-tuned lean-back / reach values"
```

---

## Self-Review Notes

- **Spec coverage:** choreography stand→freeze→hold→lean-back→look-up→parity-reset,
  exact coordinates + left/right mirror, and `/peek_over` manual endpoint (Task 1);
  `PiClient.peek_over` (Task 2); reel-gated schema + dispatch (Task 3); motion-lock
  registration (Task 4); reel persona note (Task 5); deploy + manual tuning incl.
  deep-off-edge safety + the leg/mirror correctness check (Task 6). The spec's
  separate `peek_poses.py` module is intentionally dropped — choreography lives
  inline in `server.py` like the other moves; the mirror is verified on hardware.
- **No placeholders:** every code step shows full code; commands have expected
  output; tuning step names exact constants to change.
- **Type consistency:** `_peek_over_poses(lead, reach) -> (freeze, lean_back)`,
  `peek_over_enabled(env=None) -> bool`, `_PEEK_OVER_SCHEMA`, `PeekOverRequest`
  fields (`lead, reach, pause_s, speed`), `PiClient.peek_over(lead, reach, pause_s,
  speed)`, and the `peek_over` dispatch/MOTION_TOOLS name match across Tasks 1–6.
```

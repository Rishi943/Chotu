# Explore Subagent + Heartbeat Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate the explore workflow into a synchronous subagent with its own prompt and tool surface, add a 5-message heartbeat sliding window in the parent Chotu loop, and fix two scan bugs (per-args fail guard, per-heading `open_path`).

**Architecture:** Parent `core/brain.py` exposes `explore` as a plain tool that awaits `core/explore_agent.run_explore()`. The subagent runs its own message loop against llama-server with a stripped EXPLORE.md system prompt and only the explore-scope tools. State persists through a new `core/world.py` JSON-backed map module. Existing `core/scope.py` keeps the per-node state machine; the per-heading bug is fixed in place and the 360° step count becomes configurable.

**Tech Stack:** Python 3.12, async (asyncio), httpx, OpenAI-compatible llama-server, pytest.

**Spec reference:** `docs/superpowers/specs/2026-05-23-explore-subagent-design.md`

---

## File Structure

**New:**
- `core/world.py` — JSON-backed map, single shared graph (~120 lines)
- `core/explore_agent.py` — subagent run loop (~150 lines)
- `EXPLORE.md` — subagent system prompt (~150 lines)
- `tests/test_world.py`
- `tests/test_explore_agent.py`
- `tests/test_heartbeat_window.py`
- `tests/test_per_args_guard.py`
- `scripts/test_explore_dry.py` — end-to-end with faked Pi + LLM

**Modified:**
- `core/scope.py` — `current_node_open_path` (single) → `current_node_open_paths` (dict); hardcoded `% 12` → `TURNS_PER_REVOLUTION` constant from env
- `core/brain.py` — replace inline scope handling with subagent dispatch; per-args fail guard; heartbeat sliding window; `_origin` tagging on messages
- `core/heartbeat.py` — minor: tag the heartbeat wrap message with `_origin`
- `core/tools.py` — keep top-level `explore` tool schema, ensure no scoped tools leak into main registry
- `CHOTU.md` — new "Heartbeats" section with good/bad examples
- `CLAUDE.md` — drop HEARTBEAT.md reference; bump `-c 16384` → `-c 32768` in dev-setup line

**Deleted:**
- None — `HEARTBEAT.md` is already absent

---

## Task 1: TURNS_PER_REVOLUTION constant in scope.py

**Files:**
- Modify: `core/scope.py`
- Test: `tests/test_scope.py` (extend existing)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scope.py`:

```python
import os
from core.scope import ExploreState, bump_x, TURNS_PER_REVOLUTION


def test_turns_per_revolution_default_is_10():
    assert TURNS_PER_REVOLUTION == 10


def test_bump_x_wraps_at_turns_per_revolution():
    state = ExploreState()
    state.current_x = 9
    assert bump_x(state, +1) == 0  # wraps mod 10
    assert bump_x(state, -1) == 9


def test_turns_per_revolution_env_override(monkeypatch):
    # constant is module-level; re-import after monkeypatch
    monkeypatch.setenv("PALIV_EXPLORE_TURNS_PER_REVOLUTION", "12")
    import importlib, core.scope
    importlib.reload(core.scope)
    assert core.scope.TURNS_PER_REVOLUTION == 12
    # reset for other tests
    monkeypatch.delenv("PALIV_EXPLORE_TURNS_PER_REVOLUTION")
    importlib.reload(core.scope)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scope.py::test_turns_per_revolution_default_is_10 tests/test_scope.py::test_bump_x_wraps_at_turns_per_revolution -v`
Expected: FAIL — `TURNS_PER_REVOLUTION` not defined, `bump_x` still uses `% 12`.

- [ ] **Step 3: Add constant and replace all `% 12` in scope.py**

In `core/scope.py`, near the top after imports:

```python
import os

TURNS_PER_REVOLUTION = int(os.getenv("PALIV_EXPLORE_TURNS_PER_REVOLUTION", "10"))
```

Replace every `% 12` in the file with `% TURNS_PER_REVOLUTION`. Also replace any literal `12` used as the revolution count (e.g. in `bump_x`, `plan_return_steps`, `build_map`) with `TURNS_PER_REVOLUTION`. The `+ 6` "turn 180" math (in `return_to_origin` reorientation) becomes `+ TURNS_PER_REVOLUTION // 2`.

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_scope.py -v`
Expected: all scope tests pass (including pre-existing ones — the mod change must not break existing behavior).

- [ ] **Step 5: Commit**

```bash
git add core/scope.py tests/test_scope.py
git commit -m "fix(scope): configurable TURNS_PER_REVOLUTION (default 10)"
```

---

## Task 2: Per-heading open_path (fix the one-per-node bug)

**Files:**
- Modify: `core/scope.py`
- Test: `tests/test_scope.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scope.py`:

```python
def test_open_path_allowed_on_distinct_headings():
    state = ExploreState()
    err = record_photo_state(state, anchors=["a"], objects=[], description="d0",
                             open_path=True, forward_steps=3)
    assert err is None
    bump_x(state, +1)  # heading 1
    err = record_photo_state(state, anchors=["b"], objects=[], description="d1",
                             open_path=True, forward_steps=2)
    assert err is None, f"second heading should accept open_path: {err}"


def test_open_path_same_heading_rejected():
    state = ExploreState()
    record_photo_state(state, anchors=["a"], objects=[], description="d0",
                       open_path=True, forward_steps=3)
    err = record_photo_state(state, anchors=["a2"], objects=[], description="d0b",
                             open_path=True, forward_steps=4)
    assert err is not None and "heading" in err.lower()


def test_commit_node_advances_first_open_path():
    state = ExploreState()
    record_photo_state(state, anchors=["a"], objects=[], description="first",
                       open_path=True, forward_steps=3)
    bump_x(state, +1)
    record_photo_state(state, anchors=["b"], objects=[], description="second",
                       open_path=True, forward_steps=2)
    advanced, _ = commit_node_state(state)
    assert advanced is True
    # advanced along the FIRST open_path (heading 0, steps=3)
    assert state.path_stack[-1]["open_path_x"] == 0
    assert state.path_stack[-1]["forward_steps"] == 3
```

Also import `record_photo_state, commit_node_state` in the test file if not already.

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/test_scope.py::test_open_path_allowed_on_distinct_headings tests/test_scope.py::test_open_path_same_heading_rejected tests/test_scope.py::test_commit_node_advances_first_open_path -v`
Expected: FAIL — first test fails on second-heading rejection.

- [ ] **Step 3: Refactor scope state**

In `core/scope.py` `ExploreState`:

```python
# OLD:
# current_node_open_path: dict | None = None
# NEW:
current_node_open_paths: dict[int, dict] = field(default_factory=dict)
# keyed by heading (current_x at the time the photo was recorded);
# value: {"x": int, "forward_steps": int}
```

In `record_photo_state`, change the `open_path` branch:

```python
if open_path:
    if not isinstance(forward_steps, int) or forward_steps <= 0:
        return "open_path=True requires a positive integer forward_steps"
    if state.current_x in state.current_node_open_paths:
        return (
            f"open_path already set at heading x={state.current_x}; "
            f"one open_path per heading"
        )
```

And at the bottom of the function:

```python
if open_path:
    state.current_node_open_paths[state.current_x] = {
        "x": state.current_x,
        "forward_steps": forward_steps,
    }
```

In `commit_node_state`:

```python
# OLD: if state.current_node_open_path is None: ... advanced=False
# NEW:
if not state.current_node_open_paths:
    # no exits → terminal node
    state.nodes.append(_build_node_record(state))
    state.current_node_photos = []
    return False, {"advanced": False, "reason": "no_open_paths"}

# Pick the FIRST open_path declared (lowest insertion order in dict; Py3.7+ dicts preserve)
chosen = next(iter(state.current_node_open_paths.values()))
state.path_stack.append({
    "from_node": state.current_node_id,
    "open_path_x": chosen["x"],
    "forward_steps": chosen["forward_steps"],
})
# persist node record (with ALL open_paths as known exits) before advancing
state.nodes.append(_build_node_record(state))
state.current_node_id += 1
state.current_x = 0
state.current_node_photos = []
state.current_node_open_paths = {}
return True, {"advanced": True, "new_node_id": state.current_node_id,
              "followed_heading": chosen["x"]}
```

If `_build_node_record` doesn't exist, extract whatever the current `commit_node_state` already does to build the node dict, but make sure it includes all `current_node_open_paths` as a list under key `exits` (or whatever the existing field is called — match it).

Also update any reads of `state.current_node_open_path` elsewhere in `scope.py` (and in `core/explore_tools.py`) to use the new dict — most likely they appear in `return_to_origin` / `plan_return_steps` (which read from `path_stack`, not the live field, so should be unaffected).

- [ ] **Step 4: Run all scope tests, verify pass**

Run: `pytest tests/test_scope.py -v`
Expected: all pass, including the three new tests and any pre-existing tests that touched `current_node_open_path`.

If pre-existing tests break, they likely read `state.current_node_open_path` — update them to read from `state.current_node_open_paths` (it's a dict now).

- [ ] **Step 5: Commit**

```bash
git add core/scope.py tests/test_scope.py
git commit -m "fix(scope): allow open_path per heading, not per node"
```

---

## Task 3: Per-args fail guard in brain.py

**Files:**
- Modify: `core/brain.py` (around line 320–335 where `[guard]` lives)
- Test: `tests/test_per_args_guard.py` (new)

- [ ] **Step 1: Locate current guard**

Run: `grep -n "guard\|suppress\|failed_tools" core/brain.py | head -20`

Find the data structure that tracks failed tools for the current turn (likely a `set[str]` of tool names). The fix is to change the key to `(name, args_hash)`.

- [ ] **Step 2: Write the failing test**

Create `tests/test_per_args_guard.py`:

```python
"""Per-args fail guard: the same tool with different args may retry within a turn."""

import pytest
from core.brain import _guard_key, _should_suppress, _record_failure


def test_guard_key_differs_by_args():
    k1 = _guard_key("record_photo", {"anchors": ["a"], "open_path": True})
    k2 = _guard_key("record_photo", {"anchors": ["a"], "open_path": False})
    assert k1 != k2


def test_guard_key_stable_across_arg_order():
    k1 = _guard_key("move", {"direction": "turn left", "steps": 1})
    k2 = _guard_key("move", {"steps": 1, "direction": "turn left"})
    assert k1 == k2


def test_suppress_only_identical_call():
    state: set = set()
    _record_failure(state, "record_photo", {"open_path": True})
    assert _should_suppress(state, "record_photo", {"open_path": True}) is True
    assert _should_suppress(state, "record_photo", {"open_path": False}) is False
```

- [ ] **Step 3: Run, verify fail**

Run: `pytest tests/test_per_args_guard.py -v`
Expected: FAIL — `_guard_key`/`_should_suppress`/`_record_failure` not defined.

- [ ] **Step 4: Implement the helpers**

Add near the top of `core/brain.py` (above the existing guard logic):

```python
import hashlib
import json as _json

def _guard_key(name: str, args: dict) -> tuple[str, str]:
    """Hash key for per-args fail guard. Stable across arg order."""
    blob = _json.dumps(args or {}, sort_keys=True, default=str)
    h = hashlib.sha1(blob.encode()).hexdigest()[:16]
    return (name, h)


def _record_failure(failed_set: set, name: str, args: dict) -> None:
    failed_set.add(_guard_key(name, args))


def _should_suppress(failed_set: set, name: str, args: dict) -> bool:
    return _guard_key(name, args) in failed_set
```

Then rewire the existing guard. Find code that looks like `if name in failed_tools: continue` (with a `dbg("[guard] suppressed...")`) and change it to use `_should_suppress(failed_tools, name, args)`. Likewise change the place that adds to `failed_tools` to call `_record_failure(failed_tools, name, args)`.

The `failed_tools` variable may currently be typed as `set[str]`; change it to `set` (untyped) or `set[tuple[str, str]]`.

- [ ] **Step 5: Run, verify pass**

```bash
pytest tests/test_per_args_guard.py -v
pytest tests/ -v --ignore=tests/test_explore_integration.py
```

Expected: new tests pass, no regressions. (Skip the live-Pi integration test for unit pass.)

- [ ] **Step 6: Commit**

```bash
git add core/brain.py tests/test_per_args_guard.py
git commit -m "fix(brain): per-args fail guard so different args may retry"
```

---

## Task 4: Heartbeat sliding window

**Files:**
- Modify: `core/brain.py`, `core/heartbeat.py`
- Test: `tests/test_heartbeat_window.py` (new)

- [ ] **Step 1: Tag heartbeat messages with `_origin`**

In `core/heartbeat.py`, find `wrap_heartbeat` (imported from `core.brain` per current code). Confirm `wrap_heartbeat` returns the `{role: "user", content: "[heartbeat]"}` dict. In `core/brain.py`, modify `wrap_heartbeat`:

```python
def wrap_heartbeat() -> dict:
    return {"role": "user", "content": "[heartbeat]", "_origin": "heartbeat"}
```

User-input wrapping should set `_origin: "user"`; boot-message wrapping (if any) sets `_origin: "boot"`. Find those sites — likely `build_messages` and the live input loop — and add the tag.

- [ ] **Step 2: Write the sliding-window test**

Create `tests/test_heartbeat_window.py`:

```python
from core.brain import evict_old_heartbeats, HEARTBEAT_WINDOW


def _msg(role, content, origin):
    return {"role": role, "content": content, "_origin": origin}


def _hb_block(idx: int) -> list[dict]:
    """A heartbeat assistant turn: user-heartbeat trigger + assistant reply."""
    return [
        _msg("user", "[heartbeat]", "heartbeat"),
        _msg("assistant", f"thought-{idx}", "heartbeat"),
    ]


def test_keeps_user_turns_evicts_old_heartbeats():
    messages = [_msg("system", "...", "boot"), _msg("user", "hi", "user"), _msg("assistant", "yo", "user")]
    for i in range(HEARTBEAT_WINDOW + 3):
        messages.extend(_hb_block(i))

    evict_old_heartbeats(messages)

    # System + user pair preserved
    assert messages[0]["_origin"] == "boot"
    assert messages[1]["content"] == "hi"
    assert messages[2]["content"] == "yo"

    # Only HEARTBEAT_WINDOW heartbeat blocks remain
    remaining_hb_assistants = [m for m in messages
                               if m["_origin"] == "heartbeat" and m["role"] == "assistant"]
    assert len(remaining_hb_assistants) == HEARTBEAT_WINDOW
    # Oldest ones were evicted: thought-0 gone, thought-(N+2) present
    contents = [m["content"] for m in remaining_hb_assistants]
    assert "thought-0" not in contents
    assert f"thought-{HEARTBEAT_WINDOW + 2}" in contents


def test_no_eviction_when_under_window():
    messages = [_msg("system", "...", "boot")]
    for i in range(HEARTBEAT_WINDOW - 1):
        messages.extend(_hb_block(i))
    before = len(messages)
    evict_old_heartbeats(messages)
    assert len(messages) == before
```

- [ ] **Step 3: Run, verify fail**

Run: `pytest tests/test_heartbeat_window.py -v`
Expected: FAIL — `evict_old_heartbeats`, `HEARTBEAT_WINDOW` not defined.

- [ ] **Step 4: Implement the window**

Add to `core/brain.py`:

```python
HEARTBEAT_WINDOW = int(os.getenv("PALIV_HEARTBEAT_WINDOW", "5"))


def evict_old_heartbeats(messages: list[dict]) -> None:
    """Trim heartbeat blocks (user[heartbeat] + assistant + any tool calls/results
    that follow it before the next non-heartbeat) so that at most HEARTBEAT_WINDOW
    user[heartbeat] markers remain. Mutates in place."""
    # Find indices of heartbeat user-triggers
    hb_starts = [i for i, m in enumerate(messages)
                 if m.get("_origin") == "heartbeat" and m.get("role") == "user"]
    if len(hb_starts) <= HEARTBEAT_WINDOW:
        return

    # Block i runs from hb_starts[i] up to (but not including) hb_starts[i+1],
    # or to end of list for the last block.
    to_evict = hb_starts[: len(hb_starts) - HEARTBEAT_WINDOW]
    # Build block end indices
    boundaries = []
    for k, start in enumerate(to_evict):
        end = hb_starts[k + 1] if k + 1 < len(hb_starts) else len(messages)
        # but cap at the next heartbeat we are NOT evicting
        boundaries.append((start, end))

    # Delete from highest to lowest so indices stay valid
    for start, end in reversed(boundaries):
        del messages[start:end]


def strip_internal_fields(messages: list[dict]) -> list[dict]:
    """Return a copy with `_origin` (and any future internal fields) removed,
    safe to send to the LLM."""
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]
```

Wire `evict_old_heartbeats(messages)` to be called at the start of each heartbeat-triggered turn (find where `[heartbeat]` is dispatched to `chat_complete` — likely the live loop in `brain.py` after `input_queue.get()`).

Find every `llm_client.chat_complete(messages, ...)` call site and wrap as `llm_client.chat_complete(strip_internal_fields(messages), ...)`. The `_origin` field must never reach the LLM.

- [ ] **Step 5: Run all tests, verify pass**

```bash
pytest tests/test_heartbeat_window.py tests/test_memory_window.py tests/test_heartbeat.py -v
```

Expected: pass, no regression in existing heartbeat/memory tests.

- [ ] **Step 6: Commit**

```bash
git add core/brain.py core/heartbeat.py tests/test_heartbeat_window.py
git commit -m "feat(brain): heartbeat sliding window (last 5 ticks in context)"
```

---

## Task 5: CHOTU.md heartbeat section

**Files:**
- Modify: `CHOTU.md`
- Modify: `CLAUDE.md` (drop stale HEARTBEAT.md reference)

- [ ] **Step 1: Add Heartbeats section to CHOTU.md**

Append to `CHOTU.md` (or insert near the existing voice/personality section — match the file's existing flow):

```markdown
## Heartbeats

A `[heartbeat]` ticks every couple of seconds when nothing else is happening. You see the **last 5 heartbeats** in context. Use them to notice when you're stuck in your own head and break out.

**Rule:** if your last 3 heartbeats said roughly the same thing, on the next tick either take an action or output nothing at all. Empty content is fine.

### Good — notices the loop on the 4th tick
```
[heartbeat] *Still that white sheet. Same fold pattern.*
[heartbeat] *White sheet again. Nothing new.*
[heartbeat] *Same view. White sheet.*
[heartbeat] → calls move(direction="turn right", steps=1)
  *Three ticks of the same thing. Time to look elsewhere.*
```

### Bad — four identical observations, no action
```
[heartbeat] *Still white fabric. Still that pink wall.*
[heartbeat] *White fabric. Pink wall.*
[heartbeat] *Same white fabric.*
[heartbeat] *Still white fabric, still pink wall.*
```
This is a loop. After 3 similar ticks, **change something** — move, capture, or stay silent. Repeating the same observation is worse than saying nothing.
```

- [ ] **Step 2: Update CLAUDE.md**

In `CLAUDE.md`, find the "Authoritative docs" section. Delete the `HEARTBEAT.md` bullet (the file doesn't exist). Also update the system-prompt-at-runtime line:

```markdown
The system prompt at runtime is `PALIV.md + "\n\n" + CHOTU.md`, loaded by `core/prompts.py`.
```

Also in the "Dev setup" section, update the llama-server line:

```markdown
- Start llama-server: `llama-server -m <model.gguf> --mmproj <mmproj.gguf> --port 8080 -ngl 99 -c 32768 --parallel 1`
```

- [ ] **Step 3: Commit**

```bash
git add CHOTU.md CLAUDE.md
git commit -m "docs: heartbeat behavior in CHOTU.md; drop stale HEARTBEAT.md ref; bump -c to 32768"
```

---

## Task 6: World module (`core/world.py`)

**Files:**
- Create: `core/world.py`
- Create: `tests/test_world.py`
- Modify: `.gitignore` (add `data/`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_world.py`:

```python
import json
from pathlib import Path
import pytest

from core import world


@pytest.fixture(autouse=True)
def isolated_world(tmp_path, monkeypatch):
    """Each test gets its own data/world.json."""
    p = tmp_path / "world.json"
    monkeypatch.setattr(world, "WORLD_PATH", p)
    world._GRAPH = {"nodes": {}, "origin_node": None, "version": 1}
    yield p


def test_add_node_assigns_sequential_ids():
    a = world.add_node(0, 0, heading=0)
    b = world.add_node(1, 0, heading=0)
    assert a == "node-001"
    assert b == "node-002"


def test_first_node_is_origin():
    a = world.add_node(0, 0, heading=0)
    assert world.origin() == a


def test_add_photo_stored_under_node():
    nid = world.add_node(0, 0, heading=0)
    world.add_photo(nid, photo_idx=0, heading=0, description="d",
                    anchors_in_photo=["a"], objects_in_photo=[],
                    open_path=True, forward_steps=3, distance_cm=50)
    n = world.get_node(nid)
    assert len(n["photos"]) == 1
    assert n["photos"][0]["heading"] == 0
    assert n["photos"][0]["open_path"] is True


def test_add_photo_same_heading_replaces():
    nid = world.add_node(0, 0, heading=0)
    world.add_photo(nid, photo_idx=0, heading=0, description="first",
                    anchors_in_photo=[], objects_in_photo=[], open_path=False)
    world.add_photo(nid, photo_idx=0, heading=0, description="second",
                    anchors_in_photo=[], objects_in_photo=[], open_path=False)
    n = world.get_node(nid)
    assert len(n["photos"]) == 1
    assert n["photos"][0]["description"] == "second"


def test_add_exit_dedups():
    a = world.add_node(0, 0, heading=0)
    b = world.add_node(1, 0, heading=0)
    world.add_exit(a, heading=0, to_node=b, forward_steps=4)
    world.add_exit(a, heading=0, to_node=b, forward_steps=4)
    n = world.get_node(a)
    assert len(n["exits"]) == 1


def test_save_load_roundtrip(isolated_world):
    a = world.add_node(0, 0, heading=0)
    world.add_photo(a, photo_idx=0, heading=0, description="d",
                    anchors_in_photo=["x"], objects_in_photo=[], open_path=False)
    world.save()

    world._GRAPH = {"nodes": {}, "origin_node": None, "version": 1}
    world.load()
    n = world.get_node(a)
    assert n["photos"][0]["anchors_in_photo"] == ["x"]


def test_load_missing_file_starts_empty(isolated_world):
    assert not isolated_world.exists()
    world.load()
    assert world.list_nodes() == []


def test_load_corrupt_file_starts_empty(isolated_world):
    isolated_world.write_text("not json{")
    world.load()  # must not raise
    assert world.list_nodes() == []
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/test_world.py -v`
Expected: FAIL — `core.world` module missing.

- [ ] **Step 3: Implement `core/world.py`**

```python
"""Shared persistent world model — node graph of explored space.

Single writer (the explore subagent), readable by the parent Chotu loop.
JSON-backed at data/world.json; saved on every mutation.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
WORLD_PATH = REPO_ROOT / "data" / "world.json"

_GRAPH: dict = {"nodes": {}, "origin_node": None, "version": 1}


def load() -> None:
    """Load world from disk into _GRAPH. Tolerates missing/corrupt files."""
    global _GRAPH
    if not WORLD_PATH.exists():
        _GRAPH = {"nodes": {}, "origin_node": None, "version": 1}
        return
    try:
        _GRAPH = json.loads(WORLD_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("world.json corrupt or unreadable (%s); starting empty", e)
        _GRAPH = {"nodes": {}, "origin_node": None, "version": 1}


def save() -> None:
    """Persist _GRAPH to disk. Best-effort — logs on failure, doesn't raise."""
    try:
        WORLD_PATH.parent.mkdir(parents=True, exist_ok=True)
        WORLD_PATH.write_text(json.dumps(_GRAPH, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("world.save failed: %s", e)


def _next_node_id() -> str:
    n = len(_GRAPH["nodes"]) + 1
    return f"node-{n:03d}"


def add_node(x: int, y: int, heading: int) -> str:
    nid = _next_node_id()
    _GRAPH["nodes"][nid] = {
        "id": nid,
        "x": x, "y": y,
        "heading_at_scan_start": heading,
        "anchors": [],
        "objects": [],
        "photos": [],
        "exits": [],
        "created_at": int(time.time()),
    }
    if _GRAPH["origin_node"] is None:
        _GRAPH["origin_node"] = nid
    save()
    return nid


def add_photo(node_id: str, *, photo_idx: int, heading: int, description: str,
              anchors_in_photo: list[str], objects_in_photo: list[str],
              open_path: bool, forward_steps: int | None = None,
              distance_cm: int | None = None) -> None:
    node = _GRAPH["nodes"][node_id]
    # Same-heading replace
    node["photos"] = [p for p in node["photos"] if p["heading"] != heading]
    node["photos"].append({
        "photo_idx": photo_idx,
        "heading": heading,
        "description": description,
        "anchors_in_photo": anchors_in_photo,
        "objects_in_photo": objects_in_photo,
        "open_path": open_path,
        "forward_steps": forward_steps,
        "distance_cm": distance_cm,
    })
    # Roll up anchors/objects into node-level sets (preserve insertion order)
    for a in anchors_in_photo:
        if a not in node["anchors"]:
            node["anchors"].append(a)
    for o in objects_in_photo:
        if o not in node["objects"]:
            node["objects"].append(o)
    save()


def add_exit(from_node: str, *, heading: int, to_node: str, forward_steps: int) -> None:
    node = _GRAPH["nodes"][from_node]
    for ex in node["exits"]:
        if ex["heading"] == heading and ex["to_node"] == to_node:
            return  # dedup
    node["exits"].append({"heading": heading, "to_node": to_node, "forward_steps": forward_steps})
    save()


def get_node(node_id: str) -> dict:
    return _GRAPH["nodes"][node_id]


def list_nodes() -> list[dict]:
    return list(_GRAPH["nodes"].values())


def origin() -> str | None:
    return _GRAPH["origin_node"]
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_world.py -v`
Expected: all 8 tests pass.

- [ ] **Step 5: Gitignore data dir**

Append to `.gitignore`:

```
data/
```

- [ ] **Step 6: Commit**

```bash
git add core/world.py tests/test_world.py .gitignore
git commit -m "feat(world): shared persistent map module with JSON storage"
```

---

## Task 7: Wire world.py persistence into scope commits

**Files:**
- Modify: `core/explore_tools.py` (or wherever `commit_node_state` is called from)

- [ ] **Step 1: Find the commit call site**

Run: `grep -n "commit_node_state\|build_map" core/explore_tools.py core/scope.py core/habits.py`

- [ ] **Step 2: Write integration test**

Add to `tests/test_world.py`:

```python
def test_world_records_commit_from_scope(isolated_world):
    """When scope commits a node, world.py should reflect it."""
    from core.scope import ExploreState, record_photo_state, commit_node_state, bump_x
    from core import world as world_mod
    from core.explore_tools import persist_committed_node  # to be added

    state = ExploreState()
    record_photo_state(state, anchors=["wall"], objects=["chair"], description="d0",
                       open_path=True, forward_steps=3)
    bump_x(state, +1)
    record_photo_state(state, anchors=["lamp"], objects=[], description="d1",
                       open_path=False)
    advanced, info = commit_node_state(state)
    # New helper persists the just-committed node from state.nodes[-1] to world
    persist_committed_node(state.nodes[-1])

    nodes = world_mod.list_nodes()
    assert len(nodes) == 1
    n = nodes[0]
    assert "wall" in n["anchors"] and "lamp" in n["anchors"]
    assert len(n["photos"]) == 2
    # All declared open_paths should be exits (here only one was set)
    assert len(n["exits"]) >= 1
```

- [ ] **Step 3: Run, verify fail**

Run: `pytest tests/test_world.py::test_world_records_commit_from_scope -v`
Expected: FAIL — `persist_committed_node` doesn't exist.

- [ ] **Step 4: Implement persistence helper**

In `core/explore_tools.py`, add at module scope:

```python
from core import world


def persist_committed_node(node_record: dict) -> str:
    """Translate a scope.ExploreState node dict into world.py rows. Returns node_id."""
    nid = world.add_node(
        x=node_record.get("x", 0),
        y=node_record.get("y", 0),
        heading=node_record.get("heading_at_scan_start", 0),
    )
    for i, p in enumerate(node_record.get("photos", [])):
        world.add_photo(
            nid,
            photo_idx=i,
            heading=p.get("x", 0),  # scope uses "x" for heading slot
            description=p.get("description", ""),
            anchors_in_photo=p.get("anchors", []),
            objects_in_photo=p.get("objects", []),
            open_path=bool(p.get("open_path", False)),
            forward_steps=p.get("forward_steps"),
            distance_cm=p.get("distance_estimate_cm"),
        )
    # Exits: every photo with open_path becomes an exit. to_node unknown until next
    # commit advances; for now store as self-referential placeholder.
    for p in node_record.get("photos", []):
        if p.get("open_path"):
            world.add_exit(
                nid,
                heading=p.get("x", 0),
                to_node="",  # filled by subagent after next advance
                forward_steps=p.get("forward_steps", 0),
            )
    return nid
```

In `core/explore_tools.py`, find the existing `commit_node_and_advance` wrapper (the one called by the subagent). After it calls `commit_node_state(state)` and that returns `advanced=True`, also call `persist_committed_node(state.nodes[-1])` and capture the returned `nid`. Stash it somewhere the subagent can later use to patch the placeholder `to_node` on the previous node's exit.

For now (simplest), just store `nid` on the scope's `path_stack[-1]` as `to_node_id` so a later commit can backfill the previous node's matching exit:

```python
# pseudocode addition inside scoped_commit_node_and_advance:
advanced, info = commit_node_state(scope.state)
if advanced:
    nid = persist_committed_node(scope.state.nodes[-1])
    # Backfill previous node's exit with this new node_id
    if len(scope.state.path_stack) >= 2:
        prev_edge = scope.state.path_stack[-2]
        # Find the matching exit in world.py — by heading + forward_steps
        prev_node_id = prev_edge.get("world_node_id")
        if prev_node_id:
            from core import world
            prev_node = world.get_node(prev_node_id)
            for ex in prev_node["exits"]:
                if ex["heading"] == prev_edge["open_path_x"] and ex["to_node"] == "":
                    ex["to_node"] = nid
                    break
            world.save()
    # Stash the world id on the current path stack entry
    scope.state.path_stack[-1]["world_node_id"] = nid
```

Use your best judgement on where this snippet fits cleanly. The intent: every committed scope-node lives in world.py, and exits backfill as the graph extends.

- [ ] **Step 5: Run, verify pass**

```bash
pytest tests/test_world.py tests/test_scope.py tests/test_explore_tools.py -v
```

Expected: pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add core/explore_tools.py tests/test_world.py
git commit -m "feat(explore): persist committed nodes through world.py"
```

---

## Task 8: EXPLORE.md system prompt

**Files:**
- Create: `EXPLORE.md`

- [ ] **Step 1: Write the prompt file**

Create `EXPLORE.md` at repo root:

```markdown
# EXPLORE — mapping subroutine

You are a mapping subroutine. Your **only job** is to build a navigation graph of the space around you. Do **not** look for specific objects, do not chat, do not have a personality. Be terse and mechanical.

## Tools available

- `capture_vision()` — take a photo; you receive a description as a user message
- `record_photo(anchors, objects, description, open_path, forward_steps?, distance_cm?)` — log the current photo at your current heading
- `move(direction, steps)` — **restricted**: only `direction="turn left"` or `"turn right"`, only `steps=1`. Forward motion is forbidden here.
- `commit_node_and_advance()` — closes the current scan and walks forward through the first open path you marked. If no open path was marked, returns `advanced=false` and you should `conclude`.
- `return_to_origin()` — walks back to node 0 along the path you've recorded
- `conclude(notes)` — finish the explore. Subagent will auto-return to origin after.

## Scan protocol (per node)

At each new node, you take **10 photos** — one per 36° clockwise rotation — to cover a full 360°:

1. `capture_vision()` → look at the description.
2. `record_photo(anchors=[...], objects=[...], description="...", open_path=<bool>, forward_steps?, distance_cm?)`.
   - `anchors`: structural things (walls, doorways, furniture edges) — used to recognize this place later.
   - `objects`: items in the scene (cup, bottle, foot).
   - `open_path=true` if the floor is clear ahead and you could safely walk N steps forward; provide `forward_steps` (estimated steps before hitting something) and optionally `distance_cm`.
   - Multiple headings can have `open_path=true`. That's fine — each becomes a known exit.
3. `move(direction="turn right", steps=1)`.
4. Repeat for 10 photos total. After the 10th `turn right`, you are back to your starting heading.
5. `commit_node_and_advance()`.

## Movement protocol

- The only `move` calls allowed are single `turn left` / `turn right` steps.
- To move forward, call `commit_node_and_advance()` — it picks the first `open_path` from your scan and walks you to the next node.

## Termination

- After 3–5 nodes mapped, or when every direction from the current node is a dead end, call `conclude(notes="<short summary>")`.
- The subagent will automatically `return_to_origin()` after `conclude`.

## Failure handling

- If a tool returns an error envelope, **read the error and fix the arguments**. Do not repeat the same call with the same arguments — it will be suppressed.
- If `commit_node_and_advance()` returns `{advanced: false}`, this node has no exits — `conclude` now.
- If `move` fails twice in a row, give up and `conclude` — the bridge is likely down.

## Worked example

Starting fresh at node 0:

```
capture_vision()
  → "carpet, green wall, blue bottle on floor"
record_photo(anchors=["carpet","green wall"], objects=["blue bottle"],
             description="bottle on patterned carpet, green wall behind",
             open_path=true, forward_steps=4, distance_cm=80)
move(direction="turn right", steps=1)
capture_vision()
  → "wooden cabinet, more carpet"
record_photo(anchors=["wooden cabinet","carpet"], objects=[],
             description="cabinet flush against wall",
             open_path=false)
move(direction="turn right", steps=1)
... (8 more photo/turn cycles) ...
commit_node_and_advance()
  → {advanced: true, new_node_id: 1}
# now at node 1, scan again ...
```

After 3–5 nodes:

```
conclude(notes="Mapped 3 nodes; main exit south leads to corridor; east is cluttered")
```
```

- [ ] **Step 2: Commit**

```bash
git add EXPLORE.md
git commit -m "feat(explore): EXPLORE.md system prompt for subagent"
```

---

## Task 9: Subagent loop (`core/explore_agent.py`)

**Files:**
- Create: `core/explore_agent.py`
- Create: `tests/test_explore_agent.py`

- [ ] **Step 1: Write the failing test (fake LLM + Pi)**

Create `tests/test_explore_agent.py`:

```python
"""Subagent integration test with faked LLM responses and faked Pi."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core import explore_agent, world


@pytest.fixture
def fake_pi():
    pi = MagicMock()
    pi.move = AsyncMock(return_value={"ok": True, "tool": "move", "result": {}, "duration_ms": 10, "timestamp": 0, "error": None})
    pi.capture = AsyncMock(return_value={"ok": True, "tool": "capture", "result": {"image_b64": "AAAA"}, "duration_ms": 10, "timestamp": 0, "error": None})
    return pi


@pytest.fixture(autouse=True)
def isolated_world(tmp_path, monkeypatch):
    p = tmp_path / "world.json"
    monkeypatch.setattr(world, "WORLD_PATH", p)
    world._GRAPH = {"nodes": {}, "origin_node": None, "version": 1}


@pytest.fixture
def fake_llm(monkeypatch):
    """Scripted LLM that always calls `conclude` immediately."""
    class _FakeClient:
        async def chat_complete(self, messages, tools, thinking=False, **kwargs):
            return self._next_response()
        def format_assistant_message(self, response):
            return {"role": "assistant", "content": response.get("content"),
                    "tool_calls": response.get("tool_calls", [])}

        def __init__(self):
            self._calls = 0
        def _next_response(self):
            self._calls += 1
            if self._calls == 1:
                return {"content": "ok", "tool_calls": [
                    _tc("conclude", {"notes": "test stub"})
                ]}
            return {"content": "done", "tool_calls": []}

    def _tc(name, args):
        import json
        m = MagicMock()
        m.id = f"call-{name}"
        m.function = MagicMock()
        m.function.name = name
        m.function.arguments = json.dumps(args)
        return m

    fake = _FakeClient()
    monkeypatch.setattr(explore_agent, "llm_client", fake)
    return fake


@pytest.mark.asyncio
async def test_run_explore_concludes_cleanly(fake_pi, fake_llm):
    envelope = await explore_agent.run_explore(fake_pi, reason="test")
    assert envelope["status"] == "done"
    assert "nodes_added" in envelope
    assert "message" in envelope


@pytest.mark.asyncio
async def test_run_explore_respects_max_nodes(fake_pi, monkeypatch):
    """If subagent never concludes and keeps advancing, MAX_NODES caps it."""
    # Build a fake that calls commit_node_and_advance() repeatedly
    class _Spammer:
        def __init__(self): self._n = 0
        async def chat_complete(self, messages, tools, thinking=False, **kw):
            self._n += 1
            if self._n > 50:
                return {"content": "", "tool_calls": []}
            import json
            tc = MagicMock()
            tc.id = f"c-{self._n}"
            tc.function = MagicMock()
            tc.function.name = "commit_node_and_advance"
            tc.function.arguments = "{}"
            return {"content": None, "tool_calls": [tc]}
        def format_assistant_message(self, r):
            return {"role": "assistant", "content": r.get("content"),
                    "tool_calls": r.get("tool_calls", [])}
    monkeypatch.setattr(explore_agent, "llm_client", _Spammer())
    monkeypatch.setattr(explore_agent, "MAX_NODES", 2)
    envelope = await explore_agent.run_explore(fake_pi, reason="test")
    assert envelope["status"] in ("cap_nodes", "node_fuse", "error")
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/test_explore_agent.py -v`
Expected: FAIL — `core.explore_agent` missing.

- [ ] **Step 3: Implement `core/explore_agent.py`**

```python
"""Explore subagent: runs a synchronous, isolated mapping loop.

Has its own message list, its own (terse) system prompt (EXPLORE.md), and only
the explore-scope tools. Persists discovered nodes through core/world.py.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from core import llm_client
from core.pi_client import PiClient
from core.scope import Scope, ExploreState
from core.explore_tools import (
    SCOPE_TOOL_SCHEMAS,
    build_scope_dispatch,
)

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPLORE_PROMPT = (REPO_ROOT / "EXPLORE.md").read_text(encoding="utf-8")

MAX_NODES = int(os.getenv("PALIV_EXPLORE_MAX_NODES", "5"))
MAX_TURNS_PER_NODE = int(os.getenv("PALIV_EXPLORE_MAX_TURNS_PER_NODE", "30"))


def _strip_internal(messages: list[dict]) -> list[dict]:
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


def _evict_old_images(messages: list[dict]) -> None:
    """Replace JPEG bytes in old capture_vision tool results (older than the most
    recent record_photo) with a stub string. Mutates in place."""
    # Walk back from the end; once we hit a record_photo tool call, evict all
    # capture_vision results BEFORE it in the current scan.
    # Conservative: evict every capture_vision result older than the last 2.
    capture_indices = [i for i, m in enumerate(messages)
                       if m.get("role") == "tool" and "image_b64" in str(m.get("content", ""))]
    if len(capture_indices) <= 2:
        return
    for i in capture_indices[:-2]:
        msg = messages[i]
        try:
            content = json.loads(msg["content"])
            if isinstance(content, dict) and "result" in content:
                content["result"].pop("image_b64", None)
                content["result"]["image_evicted"] = True
                msg["content"] = json.dumps(content)
        except (json.JSONDecodeError, TypeError, KeyError):
            pass


async def run_explore(pi: PiClient, *, reason: str = "idle") -> dict:
    """Run the explore subagent to completion. Returns summary envelope."""
    scope = Scope(
        scope_id=f"explore-sub-{os.getpid()}",
        originating_tool_call_id="",
        originating_tool_name="explore",
        state=ExploreState(),
    )

    messages: list[dict] = [
        {"role": "system", "content": EXPLORE_PROMPT, "_origin": "boot"},
    ]

    dispatch = build_scope_dispatch(pi, scope)
    turns_in_current_node = 0
    last_committed_node_id = scope.state.current_node_id
    status = "error"
    concluded_notes = ""

    try:
        while True:
            response = await llm_client.chat_complete(
                _strip_internal(messages), SCOPE_TOOL_SCHEMAS, thinking=False,
            )
            assistant_msg = llm_client.format_assistant_message(response)
            messages.append({**assistant_msg, "_origin": "subagent"})

            tool_calls = getattr(response, "tool_calls", None) or response.get("tool_calls") or []
            if not tool_calls:
                # No tool call → subagent decided to stop talking; treat as conclude
                status = "done"
                concluded_notes = response.get("content") or ""
                break

            for tc in tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments or "{}")

                if name == "conclude":
                    concluded_notes = args.get("notes", "")
                    status = "done"
                    # Auto return to origin if not already there
                    if scope.state.current_node_id > 0:
                        await dispatch("return_to_origin", {})
                    break_outer = True
                    break

                result = await dispatch(name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                    "_origin": "subagent",
                })

                if name == "commit_node_and_advance" and result.get("ok") and result.get("result", {}).get("advanced"):
                    last_committed_node_id = scope.state.current_node_id
                    turns_in_current_node = 0
                    if last_committed_node_id >= MAX_NODES:
                        await dispatch("return_to_origin", {})
                        status = "cap_nodes"
                        break_outer = True
                        break
                else:
                    turns_in_current_node += 1

                if turns_in_current_node >= MAX_TURNS_PER_NODE:
                    # Per-node fuse: force a commit; if that fails too, abort
                    res = await dispatch("commit_node_and_advance", {})
                    if not res.get("ok"):
                        status = "node_fuse"
                        break_outer = True
                        break
                    turns_in_current_node = 0
            else:
                _evict_old_images(messages)
                continue
            break  # break_outer triggered
    except Exception as e:
        log.exception("explore subagent error")
        return {"status": "error", "nodes_added": [],
                "anchors_seen": [], "message": str(e)}

    nodes_added = [n.get("id", f"node-{i+1:03d}")
                   for i, n in enumerate(scope.state.nodes)]
    anchors = sorted({a for n in scope.state.nodes for a in n.get("anchors", [])})
    msg = concluded_notes or f"Mapped {len(nodes_added)} nodes; cap={status}"

    return {
        "status": status,
        "nodes_added": nodes_added,
        "anchors_seen": anchors,
        "message": msg,
    }
```

Note: the loop structure has a subtle break pattern (`break_outer` flag with `for/else`). Read it carefully — the `else` on the `for` only runs if the inner loop completed without `break`; the `continue` skips the final outer `break`.

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_explore_agent.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/explore_agent.py tests/test_explore_agent.py
git commit -m "feat(explore): subagent loop with own message list and EXPLORE.md prompt"
```

---

## Task 10: Rewire brain.py — `explore` becomes a plain tool dispatching the subagent

**Files:**
- Modify: `core/brain.py`
- Modify: `core/tools.py` (if explore schema lives there)

- [ ] **Step 1: Locate current `explore` handling**

Run: `grep -n "explore" core/brain.py | head -40`

You will find a block around line ~404 that special-cases `explore` (creates a Scope, calls `explore_entry`, sets `active_scope`). That block goes away.

- [ ] **Step 2: Write the integration test for the new shape**

Add to `tests/test_explore_integration.py` (or new file):

```python
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_explore_tool_dispatches_subagent():
    """The `explore` tool in main scope should await run_explore and return its envelope."""
    from core.brain import dispatch_explore_tool  # to be added
    fake_pi = AsyncMock()
    fake_envelope = {"status": "done", "nodes_added": ["node-001"],
                     "anchors_seen": ["wall"], "message": "test"}
    with patch("core.brain.explore_agent") as mod:
        mod.run_explore = AsyncMock(return_value=fake_envelope)
        result = await dispatch_explore_tool(fake_pi, {"reason": "idle"})
    assert result["ok"] is True
    assert result["tool"] == "explore"
    assert result["result"] == fake_envelope
```

- [ ] **Step 3: Run, verify fail**

Run: `pytest tests/test_explore_integration.py::test_explore_tool_dispatches_subagent -v`
Expected: FAIL.

- [ ] **Step 4: Implement `dispatch_explore_tool` and rewire**

In `core/brain.py`, add:

```python
import time
from core import explore_agent


async def dispatch_explore_tool(pi: PiClient, args: dict) -> dict:
    started = time.time()
    reason = args.get("reason", "idle")
    envelope = await explore_agent.run_explore(pi, reason=reason)
    return {
        "ok": envelope["status"] in ("done", "cap_nodes"),
        "tool": "explore",
        "result": envelope,
        "duration_ms": int((time.time() - started) * 1000),
        "timestamp": time.time(),
        "error": None if envelope["status"] in ("done", "cap_nodes") else envelope.get("message"),
    }
```

Then in the main brain tool dispatch table (search for where tools are mapped to their async handlers), add or replace the `explore` entry to call `dispatch_explore_tool(pi, args)`.

Remove the legacy block that did `active_scope = scope; messages.append(workflow_msg); ...`. Also remove the `if active_scope is not None: SCOPE_ITERATION_CAP` and the `_current_tool_schemas() / _current_dispatch()` plumbing that switched schemas based on `active_scope` — the parent no longer has an active scope; the subagent runs that loop internally.

Specifically:
- Delete `active_scope` global (or set to always `None` and add a deprecation comment if other code reads it).
- Delete `tag_message_index` calls in main loop.
- Delete the `scope_openers_this_turn` branch.
- Delete `explore_assistant_msg_index` / `explore_originating_tool_call_id` and the conclude-cleanup branch that referenced them.
- `_current_tool_schemas()` → return just `TOOL_SCHEMAS` (no scope switching).
- `_current_dispatch()` → return just the main dispatch table.

This is a substantial cleanup. After the cuts, the main loop should look much simpler: one tool schema list, one dispatch table, no scope state.

Keep the `explore` tool **schema** registered in `core/tools.py` / `TOOL_SCHEMAS`. The schema should be:

```python
{
    "type": "function",
    "function": {
        "name": "explore",
        "description": "Open a fresh mapping subagent that explores and builds the world map. Blocking; returns a summary when done.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why you're exploring (idle, user-asked, etc)"},
            },
        },
    },
}
```

- [ ] **Step 5: Run all tests, verify pass**

```bash
pytest tests/ -v --ignore=tests/test_explore_integration.py
pytest tests/test_explore_integration.py -v -k "not live"
```

Expected: pass. Some legacy tests in `test_explore_integration.py` may have asserted old in-process scope behavior — update them to assert the new subagent-dispatch shape, or remove them if they're testing deleted code.

- [ ] **Step 6: Commit**

```bash
git add core/brain.py core/tools.py tests/test_explore_integration.py
git commit -m "refactor(brain): explore becomes plain tool dispatching subagent"
```

---

## Task 11: Dry-run integration script

**Files:**
- Create: `scripts/test_explore_dry.py`

- [ ] **Step 1: Write the script**

```python
"""End-to-end dry run: explore subagent against a fake Pi, real LLM.

Usage: python -m scripts.test_explore_dry
Requires llama-server running on :8080 with a small model.
"""

import asyncio
import json
import logging
import sys
from unittest.mock import AsyncMock, MagicMock

from core import explore_agent, world


async def main() -> int:
    logging.basicConfig(level=logging.INFO)

    # Reset world to a temp file
    world.WORLD_PATH = world.WORLD_PATH.parent / "world.dryrun.json"
    if world.WORLD_PATH.exists():
        world.WORLD_PATH.unlink()
    world._GRAPH = {"nodes": {}, "origin_node": None, "version": 1}

    fake_pi = MagicMock()
    fake_pi.move = AsyncMock(return_value={"ok": True, "tool": "move", "result": {},
                                           "duration_ms": 5, "timestamp": 0, "error": None})
    # Canned image: 1x1 white JPEG b64
    fake_pi.capture = AsyncMock(return_value={
        "ok": True, "tool": "capture",
        "result": {"image_b64": "/9j/4AAQSkZJRgABAQAAAQABAAD//gA7Q1JFQVRPUjogZ2QtanBlZyB2MS4wIChVc2luZyBJSkcgSlBFRyB2NjIp"},
        "duration_ms": 5, "timestamp": 0, "error": None
    })

    envelope = await explore_agent.run_explore(fake_pi, reason="dryrun")
    print(json.dumps(envelope, indent=2))
    print("---")
    print(json.dumps(world.list_nodes(), indent=2))
    return 0 if envelope["status"] in ("done", "cap_nodes") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: Run it once (requires llama-server running)**

```bash
source .venv/bin/activate
python -m scripts.test_explore_dry
```

Expected output: envelope JSON with `status: "done"` (or `cap_nodes` if the model is chatty), and at least one node in `world.list_nodes()`.

If status is `"error"`, read the traceback — likely an LLM call shape mismatch worth debugging before considering the task done.

- [ ] **Step 3: Commit**

```bash
git add scripts/test_explore_dry.py
git commit -m "test(explore): dry-run integration script with faked Pi"
```

---

## Task 12: Manual smoke test on real Pi

**No code changes.** Validation only.

- [ ] **Step 1: Bring up the stack**

```bash
# Terminal 1: llama-server (verify -c 32768)
llama-server -m models/Qwen3.5-4B-Q4_K_M.gguf --mmproj models/<mmproj.gguf> \
  --port 8080 -ngl 99 -c 32768 --parallel 1

# Terminal 2: Pi bridge
ssh chotu@chotu.local 'sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py'

# Terminal 3: brain
source .venv/bin/activate && PALIV_DEBUG=1 PALIV_MUTE=1 python3 -m core.brain
```

- [ ] **Step 2: Trigger explore**

Type to Chotu:

```
explore the area
```

Expect: Chotu calls `explore()`, subagent takes over (visible in debug log as a separate run_explore block), maps 1–5 nodes, returns summary envelope. Parent Chotu speaks a short reply based on the envelope.

- [ ] **Step 3: Verify**

```bash
cat data/world.json | python -m json.tool | head -40
```

Expect: ≥1 node with photos, anchors, exits. `origin_node` set.

Verify the bot returned to its starting position (visual check).

- [ ] **Step 4: Idle-loop verification (no Ctrl+C needed)**

Leave brain.py running idle for 2 minutes. Watch the debug log for:
- Heartbeats firing periodically, NOT accumulating in `sending N messages` — N should plateau at ~5 heartbeats + system + last user pair.
- No 25× repeated identical thoughts. If the model still loops, the example-driven guidance in CHOTU.md may need a stronger one-liner.

- [ ] **Step 5: Commit any final docs**

If smoke test reveals doc gaps, fix them and commit.

```bash
git add CLAUDE.md CHOTU.md
git commit -m "docs: smoke-test learnings"
```

---

## Done criteria

- [ ] All unit tests pass: `pytest tests/ -v` (excluding live-Pi tests)
- [ ] Dry-run script returns `status: "done"` and writes valid `world.json`
- [ ] Manual Pi smoke: one explore → graph written, bot returned to origin, parent Chotu summarized
- [ ] Manual idle: 2 min idle, heartbeat context stays bounded, no Ctrl+C escape needed
- [ ] `git diff main..HEAD --stat` shows the expected file set; no surprise edits

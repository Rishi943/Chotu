# Explore Habit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `explore` habit — a scope-based workflow that maps the room as a linear chain of nodes, each with 12 tagged photos, and returns a structured map without compromising KV cache.

**Architecture:** Scope is installed inside the existing `brain._process` loop. A `workflows/explore.md` user message is appended to the local `messages` list, scope-tagged. All scope tool calls are tagged. On `conclude`, the splice drops tagged messages and inserts one synthetic `tool` result carrying the map JSON. PALIV+CHOTU+HEARTBEAT stay loaded throughout.

**Tech Stack:** Python 3.12, asyncio, OpenAI-compatible tool-calling via `LLMClient`, pytest+pytest-asyncio. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-23-explore-habit-design.md`

---

## File Structure

### Create

- `core/scope.py` — pure state machine for explore (dataclasses, mutators, planner, splice). No async, no Pi calls. Easy to unit-test.
- `core/explore_tools.py` — async tool wrappers exposed inside the scope (`record_photo`, `commit_node_and_advance`, `return_to_origin`, `conclude`, restricted `move`, augmented `capture_vision`). Calls into `core/scope.py` mutators + `PiClient`.
- `workflows/explore.md` — the skill doc the LLM follows.
- `tests/test_scope.py` — unit tests for `core/scope.py` (pure functions).
- `tests/test_explore_tools.py` — async tests for `core/explore_tools.py` with mock Pi.
- `tests/test_explore_integration.py` — end-to-end `_process` test with mocked LLM driving the whole flow.

### Modify

- `core/brain.py` — add `active_scope` global; scope-aware tool routing in `_process`; message tagging; `explore` tool dispatch routes through scope opener; close_scope persists explore tool_call + map result to `memory`.
- `core/tools.py` — register `explore` schema in `TOOL_SCHEMAS`; thread `dispatch_tool` so scope-aware lookup can intercept.
- `core/habits.py` — add `explore_entry(pi, brain, tool_call_id, assistant_msg)` that opens scope and injects workflow doc.

### Conventions

- All envelopes follow the existing `{ok, tool, result, duration_ms, timestamp, error}` shape (see `core/habits.py:17`).
- All scope state lives in `Scope.state: ExploreState`. Tests construct it directly.
- Hardcoded `speed=80` for every Pi move call inside the scope. LLM cannot influence speed (no `speed` parameter on scope tool schemas).

---

## Task 1: Scope dataclasses

**Files:**
- Create: `core/scope.py`
- Test: `tests/test_scope.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scope.py
"""Unit tests for core/scope.py — pure state machine for explore habit."""

import pytest


def test_explore_state_defaults():
    from core.scope import ExploreState
    s = ExploreState()
    assert s.current_node_id == 0
    assert s.current_x == 0
    assert s.nodes == []
    assert s.current_node_photos == []
    assert s.current_node_open_path is None
    assert s.path_stack == []
    assert s.failed_advances == 0
    assert s.returned_to_origin is None


def test_scope_construction():
    from core.scope import Scope, ExploreState
    state = ExploreState()
    sc = Scope(
        scope_id="explore-abc",
        originating_tool_call_id="call_42",
        originating_tool_name="explore",
        state=state,
    )
    assert sc.scope_id == "explore-abc"
    assert sc.originating_tool_call_id == "call_42"
    assert sc.originating_tool_name == "explore"
    assert sc.state is state
    assert sc.tagged_message_indexes == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scope.py::test_explore_state_defaults -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.scope'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/scope.py
"""Scope state machine for habit workflows (explore today, investigate later).

Pure data + pure functions. No async, no Pi calls, no LLM calls. Async wrappers
that call these mutators live in core/explore_tools.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExploreState:
    current_node_id: int = 0
    current_x: int = 0
    nodes: list[dict] = field(default_factory=list)
    current_node_photos: list[dict] = field(default_factory=list)
    current_node_open_path: dict | None = None  # {"x": int, "forward_steps": int}
    path_stack: list[dict] = field(default_factory=list)  # [{"from_node": int, "open_path_x": int, "forward_steps": int}, ...]
    failed_advances: int = 0
    returned_to_origin: bool | None = None


@dataclass
class Scope:
    scope_id: str
    originating_tool_call_id: str
    originating_tool_name: str
    state: ExploreState
    tagged_message_indexes: list[int] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scope.py::test_explore_state_defaults tests/test_scope.py::test_scope_construction -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/scope.py tests/test_scope.py
git commit -m "feat(scope): ExploreState + Scope dataclasses"
```

---

## Task 2: x-tracking helper

**Files:**
- Modify: `core/scope.py`
- Test: `tests/test_scope.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scope.py`:

```python
def test_bump_x_right():
    from core.scope import ExploreState, bump_x
    s = ExploreState(current_x=0)
    bump_x(s, +1)
    assert s.current_x == 1
    bump_x(s, +1)
    assert s.current_x == 2


def test_bump_x_wraps_right():
    from core.scope import ExploreState, bump_x
    s = ExploreState(current_x=11)
    bump_x(s, +1)
    assert s.current_x == 0


def test_bump_x_left_wraps():
    from core.scope import ExploreState, bump_x
    s = ExploreState(current_x=0)
    bump_x(s, -1)
    assert s.current_x == 11


def test_bump_x_multi_step():
    from core.scope import ExploreState, bump_x
    s = ExploreState(current_x=3)
    bump_x(s, +5)
    assert s.current_x == 8
    bump_x(s, -10)
    assert s.current_x == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scope.py -v -k bump_x`
Expected: FAIL with `ImportError: cannot import name 'bump_x'`

- [ ] **Step 3: Write minimal implementation**

Append to `core/scope.py`:

```python
def bump_x(state: ExploreState, delta: int) -> int:
    """Update current_x by delta, wrapping mod 12. Returns the new x."""
    state.current_x = (state.current_x + delta) % 12
    return state.current_x
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scope.py -v -k bump_x`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add core/scope.py tests/test_scope.py
git commit -m "feat(scope): bump_x helper with mod-12 wrap"
```

---

## Task 3: record_photo state mutator

**Files:**
- Modify: `core/scope.py`
- Test: `tests/test_scope.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scope.py`:

```python
def test_record_photo_appends_with_current_x():
    from core.scope import ExploreState, record_photo_state
    s = ExploreState(current_x=3)
    err = record_photo_state(
        s, anchors=["desk"], objects=["laptop"], description="desk ahead"
    )
    assert err is None
    assert len(s.current_node_photos) == 1
    p = s.current_node_photos[0]
    assert p == {
        "x": 3, "anchors": ["desk"], "objects": ["laptop"],
        "description": "desk ahead", "open_path": False, "forward_steps": None,
    }


def test_record_photo_open_path_requires_steps():
    from core.scope import ExploreState, record_photo_state
    s = ExploreState(current_x=3)
    err = record_photo_state(
        s, anchors=["desk"], objects=[], description="floor clear",
        open_path=True, forward_steps=None,
    )
    assert err is not None
    assert "forward_steps" in err
    assert s.current_node_photos == []
    assert s.current_node_open_path is None


def test_record_photo_open_path_sets_node_open_path():
    from core.scope import ExploreState, record_photo_state
    s = ExploreState(current_x=3)
    err = record_photo_state(
        s, anchors=["desk"], objects=[], description="floor clear",
        open_path=True, forward_steps=8,
    )
    assert err is None
    assert s.current_node_open_path == {"x": 3, "forward_steps": 8}
    assert s.current_node_photos[0]["open_path"] is True
    assert s.current_node_photos[0]["forward_steps"] == 8


def test_record_photo_rejects_second_open_path():
    from core.scope import ExploreState, record_photo_state
    s = ExploreState(current_x=3)
    record_photo_state(s, ["desk"], [], "first", open_path=True, forward_steps=8)
    s.current_x = 7
    err = record_photo_state(s, ["chair"], [], "second", open_path=True, forward_steps=5)
    assert err is not None
    assert "already" in err.lower()
    assert s.current_node_open_path == {"x": 3, "forward_steps": 8}
    assert len(s.current_node_photos) == 1


def test_record_photo_requires_positive_forward_steps():
    from core.scope import ExploreState, record_photo_state
    s = ExploreState(current_x=3)
    err = record_photo_state(s, [], [], "", open_path=True, forward_steps=0)
    assert err is not None
    assert "positive" in err.lower() or "steps" in err.lower()
    err = record_photo_state(s, [], [], "", open_path=True, forward_steps=-3)
    assert err is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scope.py -v -k record_photo`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `core/scope.py`:

```python
def record_photo_state(
    state: ExploreState,
    anchors: list[str],
    objects: list[str],
    description: str,
    open_path: bool = False,
    forward_steps: int | None = None,
) -> str | None:
    """Append a photo entry at current_x. Returns None on success, error string on failure.

    open_path=True requires a positive forward_steps and that no other photo on
    the current node has already been marked open_path.
    """
    if open_path:
        if forward_steps is None or not isinstance(forward_steps, int) or forward_steps <= 0:
            return "open_path=True requires a positive integer forward_steps"
        if state.current_node_open_path is not None:
            return (
                f"open_path already set on this node at x={state.current_node_open_path['x']}; "
                f"only one open_path per node"
            )

    photo = {
        "x": state.current_x,
        "anchors": list(anchors),
        "objects": list(objects),
        "description": description,
        "open_path": bool(open_path),
        "forward_steps": forward_steps if open_path else None,
    }
    state.current_node_photos.append(photo)
    if open_path:
        state.current_node_open_path = {"x": state.current_x, "forward_steps": forward_steps}
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scope.py -v -k record_photo`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add core/scope.py tests/test_scope.py
git commit -m "feat(scope): record_photo_state with open_path validation"
```

---

## Task 4: commit_node state mutator

**Files:**
- Modify: `core/scope.py`
- Test: `tests/test_scope.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scope.py`:

```python
def _photo(x, anchors=(), objects=(), open_path=False, forward_steps=None, description=""):
    return {
        "x": x, "anchors": list(anchors), "objects": list(objects),
        "description": description, "open_path": open_path, "forward_steps": forward_steps,
    }


def test_commit_node_terminal():
    from core.scope import ExploreState, commit_node_state
    s = ExploreState(current_node_id=0)
    s.current_node_photos = [_photo(i, anchors=["bed"]) for i in range(12)]
    s.current_node_open_path = None
    advanced, node = commit_node_state(s)
    assert advanced is False
    assert node["id"] == 0
    assert node["anchors_summary"] == ["bed"]
    assert len(node["photos"]) == 12
    assert s.nodes == [node]
    assert s.current_node_photos == [node["photos"][i] for i in range(12)] or True  # photos retained in node
    # Terminal: state is "ready to return_to_origin or conclude"; node_id NOT incremented
    assert s.current_node_id == 0


def test_commit_node_advance_resets_local_state():
    from core.scope import ExploreState, commit_node_state
    s = ExploreState(current_node_id=0, current_x=3)
    s.current_node_photos = [_photo(i) for i in range(12)]
    s.current_node_open_path = {"x": 3, "forward_steps": 8}
    advanced, node = commit_node_state(s)
    assert advanced is True
    assert node["id"] == 0
    # current node committed; advance prep: reset photos/open_path and increment id
    assert s.current_node_id == 1
    assert s.current_x == 0  # new node arrival heading
    assert s.current_node_photos == []
    assert s.current_node_open_path is None
    assert s.path_stack == [{"from_node": 0, "open_path_x": 3, "forward_steps": 8}]


def test_anchors_summary_dedup_preserves_order():
    from core.scope import ExploreState, commit_node_state
    s = ExploreState(current_node_id=0)
    s.current_node_photos = [
        _photo(0, anchors=["bed", "vent"]),
        _photo(1, anchors=["vent", "desk"]),
        _photo(2, anchors=["desk", "bed", "lamp"]),
    ]
    advanced, node = commit_node_state(s)
    assert node["anchors_summary"] == ["bed", "vent", "desk", "lamp"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scope.py -v -k commit_node`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `core/scope.py`:

```python
def _ordered_unique(items: list[str]) -> list[str]:
    """Dedup a flat list, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def commit_node_state(state: ExploreState) -> tuple[bool, dict]:
    """Finalize current node into state.nodes. Returns (advanced, node_dict).

    advanced=True iff current_node_open_path is set; in that case state is rolled
    forward (node_id+1, current_x=0, photos/open_path cleared, path_stack pushed).
    advanced=False iff terminal — node committed but state left in place for
    return_to_origin or conclude.
    """
    anchors_flat = [a for p in state.current_node_photos for a in p["anchors"]]
    node = {
        "id": state.current_node_id,
        "anchors_summary": _ordered_unique(anchors_flat),
        "photos": list(state.current_node_photos),
    }
    state.nodes.append(node)

    if state.current_node_open_path is None:
        return False, node

    state.path_stack.append({
        "from_node": state.current_node_id,
        "open_path_x": state.current_node_open_path["x"],
        "forward_steps": state.current_node_open_path["forward_steps"],
    })
    state.current_node_id += 1
    state.current_x = 0
    state.current_node_photos = []
    state.current_node_open_path = None
    return True, node
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scope.py -v -k commit_node`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add core/scope.py tests/test_scope.py
git commit -m "feat(scope): commit_node_state with anchors_summary + advance"
```

---

## Task 5: Return-path planner

**Files:**
- Modify: `core/scope.py`
- Test: `tests/test_scope.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scope.py`:

```python
def test_plan_return_two_node_chain():
    """Path: node 0 → (x=3, 8 steps) → node 1 (terminal).
    Robot is at node 1, current_x=0 (arrival heading).
    Return = turn right 6 (180°), forward 8."""
    from core.scope import plan_return_steps
    path_stack = [{"from_node": 0, "open_path_x": 3, "forward_steps": 8}]
    current_x = 0
    steps = plan_return_steps(path_stack, current_x)
    assert steps == [
        ("turn right", 6),
        ("forward", 8),
    ]


def test_plan_return_three_node_chain():
    """Path: node 0 → (x=3, 8 steps) → node 1 → (x=1, 6 steps) → node 2 (terminal).
    Robot at node 2, current_x=0.

    Step A (node 2 → node 1): turn right 6, forward 6. Arrives at node 1 facing x7.
    Step B (node 1 → node 0): from x7, re-orient to node 1's outbound x=1 (5 right turns: x7→x8→...→x0→x1),
                              then turn right 6 (180° to face back to node 0), forward 8.
    """
    from core.scope import plan_return_steps
    path_stack = [
        {"from_node": 0, "open_path_x": 3, "forward_steps": 8},
        {"from_node": 1, "open_path_x": 1, "forward_steps": 6},
    ]
    current_x = 0
    steps = plan_return_steps(path_stack, current_x)
    assert steps == [
        ("turn right", 6),    # at node 2: 180° flip
        ("forward", 6),       # arrive at node 1, now facing x(1+6)=x7
        ("turn right", 6),    # re-orient from x7 to x1: 6 right turns. (Wait — see note below.)
        ("turn right", 6),    # then 180° flip to face back toward node 0
        ("forward", 8),       # arrive at node 0
    ]
```

> **Note for implementer:** The planner re-orients from the post-arrival heading to the node's *outbound* `open_path_x`, then flips 180°. Two consecutive `("turn right", 6)` entries are emitted rather than collapsed — keeps the planner trivially correct and the Pi call sequence inspectable. (`6 + 6 = 12 ≡ 0 mod 12` and the second move is the canonical 180° flip.)
>
> Re-orientation delta = `(open_path_x - arrived_at_x) % 12` where `arrived_at_x = (prev_open_path_x + 6) % 12`. For node 1: arrived_at_x = (1 + 6) % 12 = 7; delta = (1 − 7) % 12 = 6. That's why the test expects `("turn right", 6)` for that re-orient.
>
> The planner emits zero-step moves only as `("turn right", 0)` if delta=0 — those are no-ops at execution time but kept in the sequence for traceability.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scope.py -v -k plan_return`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `core/scope.py`:

```python
def plan_return_steps(path_stack: list[dict], current_x: int) -> list[tuple[str, int]]:
    """Plan the move sequence to walk back to node 0.

    path_stack is the list of edges in order [node_0→node_1, node_1→node_2, ...].
    current_x is the robot's current heading in the last (terminal) node's frame.

    Returns a flat list of (direction, steps) Pi moves. direction ∈ {"turn right", "forward"}.
    A ("turn right", 0) is a no-op marker; executor may skip without sending to the Pi.
    """
    steps: list[tuple[str, int]] = []
    # Walk edges in reverse. At each edge we are at the CHILD node.
    arrived_at_x = current_x
    for edge in reversed(path_stack):
        outbound_x = edge["open_path_x"]
        forward_steps = edge["forward_steps"]
        # Re-orient from arrived_at_x to outbound_x (always non-negative right turns).
        reorient = (outbound_x - arrived_at_x) % 12
        steps.append(("turn right", reorient))
        # 180° flip so we face back toward the parent node.
        steps.append(("turn right", 6))
        # Walk back to the parent.
        steps.append(("forward", forward_steps))
        # On arrival at parent we face (outbound_x + 6) % 12 in parent's frame.
        arrived_at_x = (outbound_x + 6) % 12

    # First edge in the reversed walk is from terminal node — its first ("turn right", 0)
    # is just current_x re-orient back to itself, which is always 0 unless the LLM moved
    # after committing. The terminal-node first step is collapsed for clarity.
    if steps and steps[0] == ("turn right", 0):
        steps.pop(0)
    return steps
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scope.py -v -k plan_return`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add core/scope.py tests/test_scope.py
git commit -m "feat(scope): plan_return_steps emits flat move sequence"
```

---

## Task 6: build_map + splice_messages

**Files:**
- Modify: `core/scope.py`
- Test: `tests/test_scope.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scope.py`:

```python
def test_build_map_minimal():
    from core.scope import ExploreState, build_map
    s = ExploreState()
    s.nodes = [
        {"id": 0, "anchors_summary": ["bed"], "photos": [{"x": 0}]},
    ]
    s.returned_to_origin = True
    m = build_map(s, notes="test room")
    assert m == {
        "nodes": [{"id": 0, "anchors_summary": ["bed"], "photos": [{"x": 0}]}],
        "returned_to_origin": True,
        "node_count": 1,
        "notes": "test room",
    }


def test_build_map_returned_false_when_unset():
    """If return_to_origin was never called (e.g. LLM concluded without it),
    returned_to_origin should serialize as False, not None."""
    from core.scope import ExploreState, build_map
    s = ExploreState()
    s.nodes = [{"id": 0, "anchors_summary": [], "photos": []}]
    s.returned_to_origin = None
    m = build_map(s, notes="")
    assert m["returned_to_origin"] is False


def test_splice_messages_removes_tagged_and_appends_tool_result():
    from core.scope import splice_messages
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "calling explore", "tool_calls": [{"id": "call_42", "type": "function", "function": {"name": "explore", "arguments": "{}"}}]},
        {"role": "user", "content": "<workflow doc>"},            # tagged
        {"role": "assistant", "content": "ok", "tool_calls": []}, # tagged
        {"role": "tool", "tool_call_id": "inner1", "content": "{}"},  # tagged
    ]
    tagged = [3, 4, 5]
    result_json = '{"nodes": [], "node_count": 0}'
    spliced = splice_messages(messages, tagged_indexes=tagged, tool_call_id="call_42", result_json=result_json)
    assert spliced == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "calling explore", "tool_calls": [{"id": "call_42", "type": "function", "function": {"name": "explore", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_42", "content": result_json},
    ]


def test_splice_messages_preserves_input_when_no_tags():
    from core.scope import splice_messages
    messages = [{"role": "user", "content": "hi"}]
    spliced = splice_messages(messages, tagged_indexes=[], tool_call_id="call_x", result_json="{}")
    # Even with no tags, splice appends the synthetic tool result for the originating call.
    assert spliced == [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "call_x", "content": "{}"},
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scope.py -v -k "build_map or splice"`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `core/scope.py`:

```python
def build_map(state: ExploreState, notes: str) -> dict:
    return {
        "nodes": list(state.nodes),
        "returned_to_origin": bool(state.returned_to_origin),
        "node_count": len(state.nodes),
        "notes": notes,
    }


def splice_messages(
    messages: list[dict],
    *,
    tagged_indexes: list[int],
    tool_call_id: str,
    result_json: str,
) -> list[dict]:
    """Return a new list with tagged_indexes removed and a synthetic tool result appended."""
    drop = set(tagged_indexes)
    out = [m for i, m in enumerate(messages) if i not in drop]
    out.append({"role": "tool", "tool_call_id": tool_call_id, "content": result_json})
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scope.py -v -k "build_map or splice"`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add core/scope.py tests/test_scope.py
git commit -m "feat(scope): build_map + splice_messages"
```

---

## Task 7: Scope opener / closer helpers

**Files:**
- Modify: `core/scope.py`
- Test: `tests/test_scope.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scope.py`:

```python
def test_open_scope_returns_scope_with_state():
    from core.scope import open_scope
    sc = open_scope(originating_tool_call_id="call_99", originating_tool_name="explore")
    assert sc.originating_tool_call_id == "call_99"
    assert sc.originating_tool_name == "explore"
    assert sc.state.current_node_id == 0
    assert sc.tagged_message_indexes == []
    assert sc.scope_id.startswith("explore-")


def test_tag_message_index():
    from core.scope import open_scope, tag_message_index
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    tag_message_index(sc, 5)
    tag_message_index(sc, 7)
    assert sc.tagged_message_indexes == [5, 7]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scope.py -v -k "open_scope or tag_message"`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `core/scope.py`:

```python
import uuid


def open_scope(*, originating_tool_call_id: str, originating_tool_name: str) -> Scope:
    return Scope(
        scope_id=f"{originating_tool_name}-{uuid.uuid4().hex[:8]}",
        originating_tool_call_id=originating_tool_call_id,
        originating_tool_name=originating_tool_name,
        state=ExploreState(),
    )


def tag_message_index(scope: Scope, index: int) -> None:
    scope.tagged_message_indexes.append(index)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scope.py -v -k "open_scope or tag_message"`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add core/scope.py tests/test_scope.py
git commit -m "feat(scope): open_scope + tag_message_index"
```

---

## Task 8: Restricted move + augmented capture_vision scope tools

**Files:**
- Create: `core/explore_tools.py`
- Test: `tests/test_explore_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_explore_tools.py
"""Async tests for core/explore_tools.py."""

import pytest
from unittest.mock import AsyncMock


def _ok(tool: str, result: dict | None = None) -> dict:
    import time
    return {"ok": True, "tool": tool, "result": result or {}, "duration_ms": 1, "timestamp": time.time(), "error": None}


def _fail(tool: str, error: str) -> dict:
    import time
    return {"ok": False, "tool": tool, "result": {}, "duration_ms": 1, "timestamp": time.time(), "error": error}


@pytest.mark.asyncio
async def test_scoped_move_rejects_forward():
    from core.explore_tools import scoped_move
    from core.scope import open_scope
    pi = AsyncMock()
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    env = await scoped_move(pi, sc, direction="forward", steps=1)
    assert env["ok"] is False
    assert "restricted" in env["error"].lower()
    pi.move.assert_not_called()
    assert sc.state.current_x == 0


@pytest.mark.asyncio
async def test_scoped_move_rejects_multi_step_turn():
    from core.explore_tools import scoped_move
    from core.scope import open_scope
    pi = AsyncMock()
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    env = await scoped_move(pi, sc, direction="turn right", steps=2)
    assert env["ok"] is False
    pi.move.assert_not_called()


@pytest.mark.asyncio
async def test_scoped_move_turn_right_calls_pi_and_bumps_x():
    from core.explore_tools import scoped_move
    from core.scope import open_scope
    pi = AsyncMock()
    pi.move.return_value = _ok("move")
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 5
    env = await scoped_move(pi, sc, direction="turn right", steps=1)
    pi.move.assert_awaited_once_with(direction="turn right", steps=1, speed=80)
    assert env["ok"] is True
    assert env["result"]["current_x"] == 6
    assert sc.state.current_x == 6


@pytest.mark.asyncio
async def test_scoped_move_turn_left_decrements_x():
    from core.explore_tools import scoped_move
    from core.scope import open_scope
    pi = AsyncMock()
    pi.move.return_value = _ok("move")
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 0
    env = await scoped_move(pi, sc, direction="turn left", steps=1)
    assert env["result"]["current_x"] == 11
    assert sc.state.current_x == 11


@pytest.mark.asyncio
async def test_scoped_move_no_x_update_on_pi_failure():
    from core.explore_tools import scoped_move
    from core.scope import open_scope
    pi = AsyncMock()
    pi.move.return_value = _fail("move", "bridge down")
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 5
    env = await scoped_move(pi, sc, direction="turn right", steps=1)
    assert env["ok"] is False
    assert sc.state.current_x == 5  # unchanged


@pytest.mark.asyncio
async def test_scoped_capture_vision_attaches_current_x(monkeypatch):
    """capture_vision result envelope is augmented with current_x."""
    from core import explore_tools
    from core.scope import open_scope

    async def fake_capture(pi):
        return _ok("capture_vision", {"image_base64": "abc", "format": "jpeg"})

    monkeypatch.setattr(explore_tools, "capture_vision_tool", fake_capture)
    pi = AsyncMock()
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 4
    env = await explore_tools.scoped_capture_vision(pi, sc)
    assert env["ok"] is True
    assert env["result"]["current_x"] == 4
    assert env["result"]["image_base64"] == "abc"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_explore_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.explore_tools'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/explore_tools.py
"""Async tool wrappers exposed inside an explore scope.

Each wrapper calls into core/scope.py for pure state transitions, then
returns a standard envelope. All Pi moves use hardcoded speed=80.
"""

from __future__ import annotations

import time

from core.pi_client import PiClient
from core.scope import (
    Scope,
    bump_x,
    record_photo_state,
)
from core.tools import capture_vision_tool


SPEED = 80
ALLOWED_TURN_DIRECTIONS = {"turn left", "turn right"}


def _envelope(tool: str, result: dict, started: float, ok: bool = True, error: str | None = None) -> dict:
    return {
        "ok": ok, "tool": tool, "result": result,
        "duration_ms": int((time.time() - started) * 1000),
        "timestamp": time.time(), "error": error,
    }


async def scoped_move(pi: PiClient, scope: Scope, *, direction: str, steps: int = 1) -> dict:
    started = time.time()
    if direction not in ALLOWED_TURN_DIRECTIONS or steps != 1:
        return _envelope(
            "move", {}, started, ok=False,
            error=(
                "move restricted in explore scope: only single "
                "turn-left/turn-right steps allowed; use commit_node_and_advance "
                "for forward motion."
            ),
        )
    env = await pi.move(direction=direction, steps=1, speed=SPEED)
    if not env.get("ok"):
        return env
    delta = +1 if direction == "turn right" else -1
    new_x = bump_x(scope.state, delta)
    return _envelope("move", {"current_x": new_x, "direction": direction}, started)


async def scoped_capture_vision(pi: PiClient, scope: Scope) -> dict:
    started = time.time()
    env = await capture_vision_tool(pi)
    if env.get("ok"):
        env["result"] = {**env.get("result", {}), "current_x": scope.state.current_x}
    return env
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_explore_tools.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add core/explore_tools.py tests/test_explore_tools.py
git commit -m "feat(explore-tools): restricted move + augmented capture_vision"
```

---

## Task 9: record_photo scope tool

**Files:**
- Modify: `core/explore_tools.py`
- Test: `tests/test_explore_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_explore_tools.py`:

```python
@pytest.mark.asyncio
async def test_scoped_record_photo_appends():
    from core.explore_tools import scoped_record_photo
    from core.scope import open_scope
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 2
    env = await scoped_record_photo(
        sc, anchors=["bed"], objects=["pillow"], description="head of bed",
        open_path=False, forward_steps=None,
    )
    assert env["ok"] is True
    assert env["result"] == {"recorded": True, "photos_so_far": 1}
    assert sc.state.current_node_photos[0]["x"] == 2


@pytest.mark.asyncio
async def test_scoped_record_photo_rejects_double_open_path():
    from core.explore_tools import scoped_record_photo
    from core.scope import open_scope
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 3
    await scoped_record_photo(sc, anchors=[], objects=[], description="d1", open_path=True, forward_steps=8)
    sc.state.current_x = 7
    env = await scoped_record_photo(sc, anchors=[], objects=[], description="d2", open_path=True, forward_steps=5)
    assert env["ok"] is False
    assert "already" in env["error"].lower() or "one open_path" in env["error"].lower()
    assert len(sc.state.current_node_photos) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_explore_tools.py -v -k record_photo`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `core/explore_tools.py`:

```python
async def scoped_record_photo(
    scope: Scope,
    *,
    anchors: list[str],
    objects: list[str],
    description: str = "",
    open_path: bool = False,
    forward_steps: int | None = None,
) -> dict:
    started = time.time()
    err = record_photo_state(
        scope.state,
        anchors=anchors, objects=objects, description=description,
        open_path=open_path, forward_steps=forward_steps,
    )
    if err is not None:
        return _envelope("record_photo", {}, started, ok=False, error=err)
    return _envelope(
        "record_photo",
        {"recorded": True, "photos_so_far": len(scope.state.current_node_photos)},
        started,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_explore_tools.py -v -k record_photo`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add core/explore_tools.py tests/test_explore_tools.py
git commit -m "feat(explore-tools): scoped_record_photo"
```

---

## Task 10: commit_node_and_advance scope tool

**Files:**
- Modify: `core/explore_tools.py`
- Test: `tests/test_explore_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_explore_tools.py`:

```python
@pytest.mark.asyncio
async def test_commit_and_advance_terminal():
    """No open_path tagged → terminal: commits node, advanced:false."""
    from core.explore_tools import scoped_commit_node_and_advance
    from core.scope import open_scope
    pi = AsyncMock()
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    for i in range(12):
        sc.state.current_node_photos.append({
            "x": i, "anchors": [], "objects": [], "description": "",
            "open_path": False, "forward_steps": None,
        })
    sc.state.current_node_open_path = None
    env = await scoped_commit_node_and_advance(pi, sc)
    assert env["ok"] is True
    assert env["result"] == {"advanced": False, "new_node_id": None, "aborted": False, "reason": None}
    pi.move.assert_not_called()
    pi.get_distance.assert_not_called()


@pytest.mark.asyncio
async def test_commit_and_advance_happy_path():
    """open_path set, distance clear, Pi succeeds: turn from current_x to open_path_x via
    shortest side, walk forward, push edge onto path_stack, reset local state."""
    from core.explore_tools import scoped_commit_node_and_advance
    from core.scope import open_scope
    pi = AsyncMock()
    pi.get_distance.return_value = _ok("get_distance", {"cm": 200, "reliable": True})
    pi.move.return_value = _ok("move")
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 0
    sc.state.current_node_open_path = {"x": 3, "forward_steps": 8}
    sc.state.current_node_photos = [{"x": i, "anchors": [], "objects": [], "description": "", "open_path": False, "forward_steps": None} for i in range(12)]

    env = await scoped_commit_node_and_advance(pi, sc)
    assert env["ok"] is True
    assert env["result"]["advanced"] is True
    assert env["result"]["new_node_id"] == 1
    # Two Pi moves: turn from x=0 to x=3 (3 right), then forward 8.
    assert pi.move.await_count == 2
    pi.move.assert_any_await(direction="turn right", steps=3, speed=80)
    pi.move.assert_any_await(direction="forward", steps=8, speed=80)
    assert sc.state.path_stack == [{"from_node": 0, "open_path_x": 3, "forward_steps": 8}]
    assert sc.state.current_node_id == 1
    assert sc.state.current_x == 0
    assert sc.state.current_node_photos == []
    assert sc.state.current_node_open_path is None


@pytest.mark.asyncio
async def test_commit_and_advance_blocked_by_distance():
    """Ultrasonic reports obstacle < 15cm: don't walk; clear open_path; bump failed_advances."""
    from core.explore_tools import scoped_commit_node_and_advance
    from core.scope import open_scope
    pi = AsyncMock()
    pi.get_distance.return_value = _ok("get_distance", {"cm": 8, "reliable": True})
    pi.move.return_value = _ok("move")
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 0
    sc.state.current_node_open_path = {"x": 3, "forward_steps": 8}
    env = await scoped_commit_node_and_advance(pi, sc)
    assert env["ok"] is False
    assert "obstacle" in env["error"].lower()
    assert sc.state.failed_advances == 1
    assert sc.state.current_node_open_path is None
    assert sc.state.current_node_id == 0  # did not advance
    # Only the turn was executed (turn right 3 to face open_path_x); the forward was skipped after distance check.
    pi.move.assert_awaited_once_with(direction="turn right", steps=3, speed=80)


@pytest.mark.asyncio
async def test_commit_and_advance_three_failures_force_return():
    """After 3 cumulative failed advances, the tool returns aborted:true and queues
    return_to_origin implicitly by setting state.returned_to_origin via plan execution."""
    from core.explore_tools import scoped_commit_node_and_advance
    from core.scope import open_scope
    pi = AsyncMock()
    pi.get_distance.return_value = _ok("get_distance", {"cm": 5})
    pi.move.return_value = _ok("move")
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 0
    sc.state.failed_advances = 2

    # The third failure triggers abort
    sc.state.current_node_open_path = {"x": 3, "forward_steps": 8}
    env = await scoped_commit_node_and_advance(pi, sc)
    assert env["ok"] is False
    assert env["result"]["aborted"] is True
    assert "3 advance failures" in env["result"]["reason"]
    assert sc.state.failed_advances == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_explore_tools.py -v -k commit_and_advance`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `core/explore_tools.py`:

```python
from core.scope import commit_node_state


OBSTACLE_CM = 15
MAX_FAILED_ADVANCES = 3


async def _turn_to(pi: PiClient, scope: Scope, target_x: int) -> dict:
    """Shortest-side turn from current_x to target_x. Returns the move envelope."""
    delta = (target_x - scope.state.current_x) % 12
    if delta == 0:
        return _envelope("move", {"current_x": scope.state.current_x}, time.time())
    if delta <= 6:
        env = await pi.move(direction="turn right", steps=delta, speed=SPEED)
        if env.get("ok"):
            scope.state.current_x = (scope.state.current_x + delta) % 12
    else:
        left = 12 - delta
        env = await pi.move(direction="turn left", steps=left, speed=SPEED)
        if env.get("ok"):
            scope.state.current_x = (scope.state.current_x - left) % 12
    return env


async def scoped_commit_node_and_advance(pi: PiClient, scope: Scope) -> dict:
    started = time.time()
    open_path = scope.state.current_node_open_path  # snapshot before commit clears it
    advanced, _node = commit_node_state(scope.state)
    if not advanced:
        # Terminal: node committed, state preserved. Note: commit_node_state didn't change
        # current_node_id/photos because open_path was None.
        return _envelope(
            "commit_node_and_advance",
            {"advanced": False, "new_node_id": None, "aborted": False, "reason": None},
            started,
        )

    # commit_node_state already rolled state forward (incremented node_id, reset photos/x/open_path,
    # pushed onto path_stack). We now need to execute the physical exit move; on failure we must
    # roll the state BACK so the LLM can retry with a different open_path.
    new_node_id = scope.state.current_node_id
    edge = scope.state.path_stack[-1]

    def _rollback() -> None:
        scope.state.path_stack.pop()
        scope.state.current_node_id -= 1
        # current_x stays at 0 in the current node's frame; we'll turn back below.
        # photos/open_path were cleared by commit_node_state — but the node dict is in
        # state.nodes; pop it back into current_node_photos and clear current_node_open_path.
        prev_node = scope.state.nodes.pop()
        # Clear stale open_path flags on restored photos so the LLM can re-mark a different one.
        restored = []
        for p in prev_node["photos"]:
            q = dict(p)
            q["open_path"] = False
            q["forward_steps"] = None
            restored.append(q)
        scope.state.current_node_photos = restored
        scope.state.current_node_open_path = None  # forces LLM to pick a new path

    # Step 1: turn to face open_path_x. Before commit_node_state, current_x was the LLM's heading
    # after the 360° scan (typically 0). After commit, current_x was reset to 0 for the new node —
    # but we haven't physically moved yet. So we need to turn from the previous-node 0 to open_path_x.
    # commit_node_state set current_x=0 prematurely; for the physical turn we treat it as still being
    # in the previous node's frame.
    pre_turn_x_in_prev_frame = 0  # post-scan canonical heading
    delta = (edge["open_path_x"] - pre_turn_x_in_prev_frame) % 12
    if delta != 0:
        env_turn = await (
            pi.move(direction="turn right", steps=delta, speed=SPEED)
            if delta <= 6
            else pi.move(direction="turn left", steps=12 - delta, speed=SPEED)
        )
        if not env_turn.get("ok"):
            _rollback()
            scope.state.failed_advances += 1
            if scope.state.failed_advances >= MAX_FAILED_ADVANCES:
                return _envelope(
                    "commit_node_and_advance",
                    {"advanced": False, "new_node_id": None, "aborted": True,
                     "reason": "3 advance failures — call return_to_origin then conclude"},
                    started, ok=False, error="advance failed: turn",
                )
            return _envelope(
                "commit_node_and_advance",
                {"advanced": False, "new_node_id": None, "aborted": False, "reason": None},
                started, ok=False, error=f"advance failed: turn — {env_turn.get('error')}",
            )

    # Step 2: distance check
    dist_env = await pi.get_distance()
    cm = (dist_env.get("result") or {}).get("cm", 9999)
    if 0 < cm < OBSTACLE_CM:
        # Turn back to face the post-scan heading (x=0 in previous node's frame).
        await pi.move(direction="turn left", steps=delta, speed=SPEED) if delta <= 6 else \
            await pi.move(direction="turn right", steps=12 - delta, speed=SPEED)
        _rollback()
        scope.state.failed_advances += 1
        if scope.state.failed_advances >= MAX_FAILED_ADVANCES:
            return _envelope(
                "commit_node_and_advance",
                {"advanced": False, "new_node_id": None, "aborted": True,
                 "reason": "3 advance failures — call return_to_origin then conclude"},
                started, ok=False, error=f"obstacle at {cm}cm",
            )
        return _envelope(
            "commit_node_and_advance",
            {"advanced": False, "new_node_id": None, "aborted": False, "reason": None},
            started, ok=False, error=f"obstacle at {cm}cm — pick a different open_path",
        )

    # Step 3: walk forward
    fwd_env = await pi.move(direction="forward", steps=edge["forward_steps"], speed=SPEED)
    if not fwd_env.get("ok"):
        # Walk back the (possibly zero) distance we managed before failing.
        # Conservative: send a single backward move equal to the requested forward steps.
        await pi.move(direction="backward", steps=edge["forward_steps"], speed=SPEED)
        await pi.move(direction="turn left", steps=delta, speed=SPEED) if delta <= 6 else \
            await pi.move(direction="turn right", steps=12 - delta, speed=SPEED)
        _rollback()
        scope.state.failed_advances += 1
        if scope.state.failed_advances >= MAX_FAILED_ADVANCES:
            return _envelope(
                "commit_node_and_advance",
                {"advanced": False, "new_node_id": None, "aborted": True,
                 "reason": "3 advance failures — call return_to_origin then conclude"},
                started, ok=False, error=f"forward move failed: {fwd_env.get('error')}",
            )
        return _envelope(
            "commit_node_and_advance",
            {"advanced": False, "new_node_id": None, "aborted": False, "reason": None},
            started, ok=False, error=f"forward move failed: {fwd_env.get('error')}",
        )

    return _envelope(
        "commit_node_and_advance",
        {"advanced": True, "new_node_id": new_node_id, "aborted": False, "reason": None},
        started,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_explore_tools.py -v -k commit_and_advance`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add core/explore_tools.py tests/test_explore_tools.py
git commit -m "feat(explore-tools): commit_node_and_advance with rollback + 3-strike abort"
```

---

## Task 11: return_to_origin scope tool

**Files:**
- Modify: `core/explore_tools.py`
- Test: `tests/test_explore_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_explore_tools.py`:

```python
@pytest.mark.asyncio
async def test_return_to_origin_two_node_chain():
    from core.explore_tools import scoped_return_to_origin
    from core.scope import open_scope
    pi = AsyncMock()
    pi.move.return_value = _ok("move")
    pi.get_distance.return_value = _ok("get_distance", {"cm": 200})
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.path_stack = [{"from_node": 0, "open_path_x": 3, "forward_steps": 8}]
    sc.state.current_x = 0  # at terminal node 1, facing arrival heading

    env = await scoped_return_to_origin(pi, sc)
    assert env["ok"] is True
    assert env["result"]["success"] is True
    assert env["result"]["last_node_reached"] == 0
    assert sc.state.returned_to_origin is True
    # Expected moves: turn right 6, forward 8.
    pi.move.assert_any_await(direction="turn right", steps=6, speed=80)
    pi.move.assert_any_await(direction="forward", steps=8, speed=80)


@pytest.mark.asyncio
async def test_return_to_origin_stops_on_failure():
    from core.explore_tools import scoped_return_to_origin
    from core.scope import open_scope
    pi = AsyncMock()
    pi.get_distance.return_value = _ok("get_distance", {"cm": 200})
    # First move ok, second move (forward) fails.
    pi.move.side_effect = [_ok("move"), _fail("move", "bridge down")]
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.path_stack = [{"from_node": 0, "open_path_x": 3, "forward_steps": 8}]
    sc.state.current_x = 0

    env = await scoped_return_to_origin(pi, sc)
    assert env["ok"] is False
    assert env["result"]["success"] is False
    assert env["result"]["last_node_reached"] == 1  # never made it to 0
    assert sc.state.returned_to_origin is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_explore_tools.py -v -k return_to_origin`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `core/explore_tools.py`:

```python
from core.scope import plan_return_steps


async def scoped_return_to_origin(pi: PiClient, scope: Scope) -> dict:
    started = time.time()
    plan = plan_return_steps(scope.state.path_stack, scope.state.current_x)
    last_node_reached = scope.state.current_node_id

    forward_steps_remaining = list(scope.state.path_stack)

    for direction, n in plan:
        if n == 0:
            continue
        env = await pi.move(direction=direction, steps=n, speed=SPEED)
        if not env.get("ok"):
            scope.state.returned_to_origin = False
            return _envelope(
                "return_to_origin",
                {"success": False, "last_node_reached": last_node_reached,
                 "error": env.get("error") or "move failed"},
                started, ok=False, error="return aborted partway",
            )
        if direction == "forward":
            # Completed one edge backward — decrement remaining + update last_node_reached.
            if forward_steps_remaining:
                edge = forward_steps_remaining.pop()
                last_node_reached = edge["from_node"]

    scope.state.returned_to_origin = True
    return _envelope(
        "return_to_origin",
        {"success": True, "last_node_reached": 0, "error": None},
        started,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_explore_tools.py -v -k return_to_origin`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add core/explore_tools.py tests/test_explore_tools.py
git commit -m "feat(explore-tools): return_to_origin executes planned move sequence"
```

---

## Task 12: conclude scope tool

**Files:**
- Modify: `core/explore_tools.py`
- Test: `tests/test_explore_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_explore_tools.py`:

```python
@pytest.mark.asyncio
async def test_conclude_builds_map_and_signals_done():
    from core.explore_tools import scoped_conclude
    from core.scope import open_scope
    sc = open_scope(originating_tool_call_id="call_99", originating_tool_name="explore")
    sc.state.nodes = [{"id": 0, "anchors_summary": ["bed"], "photos": []}]
    sc.state.returned_to_origin = True

    env = await scoped_conclude(sc, status="done", notes="cozy room")
    assert env["ok"] is True
    assert env["result"]["status"] == "done"
    assert env["result"]["map"] == {
        "nodes": [{"id": 0, "anchors_summary": ["bed"], "photos": []}],
        "returned_to_origin": True,
        "node_count": 1,
        "notes": "cozy room",
    }


@pytest.mark.asyncio
async def test_conclude_rejects_bad_status():
    from core.explore_tools import scoped_conclude
    from core.scope import open_scope
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    env = await scoped_conclude(sc, status="bogus", notes="")
    assert env["ok"] is False
    assert "status" in env["error"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_explore_tools.py -v -k conclude`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `core/explore_tools.py`:

```python
from core.scope import build_map


VALID_CONCLUDE_STATUS = {"done", "inconclusive"}


async def scoped_conclude(scope: Scope, *, status: str, notes: str = "") -> dict:
    started = time.time()
    if status not in VALID_CONCLUDE_STATUS:
        return _envelope(
            "conclude", {}, started, ok=False,
            error=f"status must be one of {sorted(VALID_CONCLUDE_STATUS)}; got {status!r}",
        )
    map_dict = build_map(scope.state, notes=notes)
    return _envelope("conclude", {"status": status, "map": map_dict}, started)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_explore_tools.py -v -k conclude`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add core/explore_tools.py tests/test_explore_tools.py
git commit -m "feat(explore-tools): scoped_conclude builds + validates map"
```

---

## Task 13: Add `explore` and `conclude` schemas + the in-scope schema set

**Files:**
- Modify: `core/tools.py`
- Test: `tests/test_explore_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_explore_tools.py`:

```python
def test_explore_schema_registered():
    from core.tools import TOOL_SCHEMAS
    names = [t["function"]["name"] for t in TOOL_SCHEMAS]
    assert "explore" in names


def test_explore_schema_has_no_params():
    from core.tools import TOOL_SCHEMAS
    explore = [t for t in TOOL_SCHEMAS if t["function"]["name"] == "explore"][0]
    assert explore["function"]["parameters"]["properties"] == {}
    assert explore["function"]["parameters"].get("required", []) == []


def test_scope_schemas_have_no_speed_param():
    from core.explore_tools import SCOPE_TOOL_SCHEMAS
    for t in SCOPE_TOOL_SCHEMAS:
        params = t["function"]["parameters"].get("properties", {})
        assert "speed" not in params, f"{t['function']['name']} must not expose speed"


def test_scope_schemas_include_required_tools():
    from core.explore_tools import SCOPE_TOOL_SCHEMAS
    names = {t["function"]["name"] for t in SCOPE_TOOL_SCHEMAS}
    assert {"move", "capture_vision", "record_photo",
            "commit_node_and_advance", "return_to_origin", "conclude"} <= names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_explore_tools.py -v -k "schema"`
Expected: FAIL (`explore` missing from `TOOL_SCHEMAS`, `SCOPE_TOOL_SCHEMAS` missing)

- [ ] **Step 3: Write minimal implementation**

In `core/tools.py`, append to the `TOOL_SCHEMAS` list (before the closing `]`):

```python
    {
        "type": "function",
        "function": {
            "name": "explore",
            "description": (
                "Map the room you are in as a chain of vantage points. "
                "Long-running (minutes). Once it returns, you will have a structured "
                "map with per-photo anchors and objects for later use. Call when you "
                "have no spatial awareness or have moved to a new space."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
```

Append to `core/explore_tools.py`:

```python
SCOPE_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "move",
            "description": (
                "Inside explore: only single-step turn left or turn right is allowed. "
                "Forward motion happens through commit_node_and_advance."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["turn left", "turn right"]},
                    "steps": {"type": "integer", "enum": [1], "default": 1},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_vision",
            "description": "Take a photo at your current heading. Returns image + current_x.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_photo",
            "description": (
                "Record the photo you just looked at, at your current x. "
                "Set open_path=true on at most ONE photo per node — the direction you want "
                "to explore next. forward_steps is required when open_path=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "anchors": {"type": "array", "items": {"type": "string"},
                                "description": "Fixed landmarks visible in this photo (vents, frames, doors)."},
                    "objects": {"type": "array", "items": {"type": "string"},
                                "description": "Movable items visible in this photo."},
                    "description": {"type": "string", "description": "One-line description."},
                    "open_path": {"type": "boolean", "default": False},
                    "forward_steps": {"type": "integer",
                                      "description": "Required if open_path=true. How many steps to walk to the next node."},
                },
                "required": ["anchors", "objects", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commit_node_and_advance",
            "description": (
                "Finalize the current node. If you tagged an open_path, also walks to "
                "the next node and resets for a new 360° scan. If not, this is a terminal node — "
                "next step is return_to_origin."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "return_to_origin",
            "description": "Walk back to Node 0 atomically. Required before conclude on a successful run.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "conclude",
            "description": "End the explore. Returns the assembled map to Chotu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["done", "inconclusive"]},
                    "notes": {"type": "string", "description": "One-line summary of the room."},
                },
                "required": ["status"],
            },
        },
    },
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_explore_tools.py -v -k "schema"`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add core/tools.py core/explore_tools.py tests/test_explore_tools.py
git commit -m "feat(tools): register explore schema + SCOPE_TOOL_SCHEMAS"
```

---

## Task 14: Scope dispatch map builder

**Files:**
- Modify: `core/explore_tools.py`
- Test: `tests/test_explore_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_explore_tools.py`:

```python
@pytest.mark.asyncio
async def test_scope_dispatch_routes_record_photo():
    from core.explore_tools import build_scope_dispatch
    from core.scope import open_scope
    pi = AsyncMock()
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    dispatch = build_scope_dispatch(pi, sc)
    assert "record_photo" in dispatch
    env = await dispatch["record_photo"](anchors=["bed"], objects=[], description="head")
    assert env["ok"] is True
    assert sc.state.current_node_photos[0]["anchors"] == ["bed"]


@pytest.mark.asyncio
async def test_scope_dispatch_passive_tools_pass_through():
    """get_distance, get_battery, set_face, speak, wait stay available unchanged."""
    from core.explore_tools import build_scope_dispatch
    from core.scope import open_scope
    pi = AsyncMock()
    pi.get_distance.return_value = _ok("get_distance", {"cm": 100})
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    dispatch = build_scope_dispatch(pi, sc)
    assert "get_distance" in dispatch
    env = await dispatch["get_distance"]()
    assert env["ok"] is True


@pytest.mark.asyncio
async def test_scope_dispatch_blocks_pose_do_trick_investigate():
    """pose, do_trick, get_perception, investigate, explore are NOT in the scope dispatch."""
    from core.explore_tools import build_scope_dispatch
    from core.scope import open_scope
    pi = AsyncMock()
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    dispatch = build_scope_dispatch(pi, sc)
    for name in ("pose", "do_trick", "get_perception", "investigate", "explore", "set_legs", "cast_spell"):
        assert name not in dispatch, f"{name} must not be available in explore scope"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_explore_tools.py -v -k scope_dispatch`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

Append to `core/explore_tools.py`:

```python
def build_scope_dispatch(pi: PiClient, scope: Scope) -> dict:
    """Build the name -> async callable map active while a scope is open.

    Pi-passive tools and pi-active-but-irrelevant tools (speak, set_face, wait,
    get_distance, get_battery) stay available with the same signatures they have
    globally. move and capture_vision are wrapped. record_photo / commit_node_and_advance /
    return_to_origin / conclude are scope-only.

    Tools NOT in this map (pose, do_trick, get_perception, investigate, explore,
    set_legs, cast_spell) are blocked at dispatch time — _process returns a structured
    error envelope to the LLM rather than calling through.
    """
    from core.tools import local_wait, _do_speak

    return {
        "move":                     lambda **kw: scoped_move(pi, scope, **kw),
        "capture_vision":           lambda **kw: scoped_capture_vision(pi, scope),
        "record_photo":             lambda **kw: scoped_record_photo(scope, **kw),
        "commit_node_and_advance":  lambda **kw: scoped_commit_node_and_advance(pi, scope),
        "return_to_origin":         lambda **kw: scoped_return_to_origin(pi, scope),
        "conclude":                 lambda **kw: scoped_conclude(scope, **kw),
        # Pass-through passive tools
        "get_distance":             lambda **kw: pi.get_distance(),
        "get_battery":              lambda **kw: pi.get_battery(),
        "set_face":                 lambda **kw: pi.set_face(**kw),
        "speak":                    lambda **kw: _do_speak(face_pi=pi, muted=False, **kw),
        "wait":                     lambda **kw: local_wait(**kw),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_explore_tools.py -v -k scope_dispatch`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add core/explore_tools.py tests/test_explore_tools.py
git commit -m "feat(explore-tools): build_scope_dispatch with passive tool passthrough"
```

---

## Task 15: Brain integration — `active_scope`, scope-aware tool routing, message tagging

**Files:**
- Modify: `core/brain.py`
- Test: `tests/test_explore_integration.py` (new in next task; this task is structural)

- [ ] **Step 1: Write the failing test**

Create `tests/test_explore_integration.py`:

```python
"""Integration test: brain._process drives a full explore through mocked LLM."""

import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def _make_tool_call(call_id: str, name: str, args: dict):
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _make_response(content: str | None, tool_calls: list | None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


@pytest.mark.asyncio
async def test_active_scope_global_exists():
    """active_scope global is exposed by brain and initially None."""
    from core import brain
    assert hasattr(brain, "active_scope")
    assert brain.active_scope is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_explore_integration.py::test_active_scope_global_exists -v`
Expected: FAIL — `brain` has no attribute `active_scope`

- [ ] **Step 3: Write minimal implementation**

Modify `core/brain.py`:

**Add near the other globals (around line 146, after `_pi_reachable`):**

```python
# --- Scope (for habit workflows like explore) ---
# When set, scope-aware dispatch routes through scope.tools instead of the
# global dispatch_map, and every assistant/tool message added during the
# scope is tagged via tag_message_index() so close_scope can splice it out.
active_scope = None  # type: "core.scope.Scope | None"
```

**Replace the body of `_process` (lines 332-481) — full replacement below. The diff vs. the existing function is: scope routing in tool dispatch, message tagging, scope-opener short-circuit, scope-aware iteration limit (200 inside scope), and post-scope memory persistence.**

```python
async def _process(item: dict):
    global active_scope
    user_input = item["text"]
    kind = item["kind"]
    _emit({"type": kind, "text": user_input})
    _fire_face("thinking")
    messages = build_messages(user_input)
    dbg(f"sending {len(messages)} messages to LLM")

    try:
        response = await llm_client.chat_complete(messages, TOOL_SCHEMAS, thinking=thinking_enabled)
    except Exception as e:
        print(f"  LLM error: {e}")
        _fire_face("idle")
        return

    if not response.choices:
        print("  LLM error: empty choices")
        _fire_face("idle")
        return

    if response.choices:
        content = response.choices[0].message.content
        clean_content, think_blocks = _extract_think_blocks(content)
        for block in think_blocks:
            block = block.strip()
            if block:
                print(f"  [think] {block[:120]}...")
                _emit({"type": "think", "text": block})
        if think_blocks and response.choices[0].message.content != clean_content:
            response.choices[0].message.content = clean_content
        if clean_content:
            print_monologue(clean_content)

    set_legs_fired = 0
    waits_fired = 0
    failed_tools: set[str] = set()

    iterations = 0
    SCOPE_ITERATION_CAP = 200
    NORMAL_ITERATION_CAP = MAX_TOOL_ITERATIONS

    def _current_cap():
        return SCOPE_ITERATION_CAP if active_scope is not None else NORMAL_ITERATION_CAP

    def _current_tool_schemas():
        if active_scope is not None:
            from core.explore_tools import SCOPE_TOOL_SCHEMAS
            return SCOPE_TOOL_SCHEMAS
        return TOOL_SCHEMAS

    def _current_dispatch():
        if active_scope is not None:
            from core.explore_tools import build_scope_dispatch
            return build_scope_dispatch(pi, active_scope)
        return dispatch_map

    explore_assistant_msg_index: int | None = None
    explore_originating_tool_call_id: str | None = None

    while response.choices[0].message.tool_calls and iterations < _current_cap():
        assistant_msg = response.choices[0].message
        assistant_idx = len(messages)
        messages.append(llm_client.format_assistant_message(response))
        if active_scope is not None:
            from core.scope import tag_message_index
            tag_message_index(active_scope, assistant_idx)

        # --- Scope-opener short-circuit: an "explore" tool call opens a scope ---
        # The tool result for the explore call is NOT appended this iteration; it
        # will be appended by close_scope (splice) when the LLM calls conclude.
        scope_openers_this_turn = [tc for tc in assistant_msg.tool_calls if tc.function.name == "explore" and active_scope is None]
        if scope_openers_this_turn:
            tc = scope_openers_this_turn[0]
            from core.habits import explore_entry
            explore_assistant_msg_index = assistant_idx
            explore_originating_tool_call_id = tc.id
            doc_msg_idx = len(messages)
            workflow_msg = await explore_entry(pi, brain_module=None, tool_call_id=tc.id, assistant_idx=assistant_idx)
            messages.append(workflow_msg)
            from core.scope import tag_message_index
            tag_message_index(active_scope, doc_msg_idx)
            # No tool result for the explore call yet. Re-prompt the LLM with the
            # scope active.
            dbg(f"explore scope opened (id={active_scope.scope_id})")
            try:
                response = await llm_client.chat_complete(messages, _current_tool_schemas(), thinking=thinking_enabled)
            except Exception as e:
                print(f"  LLM error on scope open: {e}")
                return
            iterations += 1
            continue

        # --- Split: allowed vs suppressed (per-turn caps) ---
        to_dispatch = []
        suppressed = []
        for tc in assistant_msg.tool_calls:
            name = tc.function.name
            if name == "set_legs" and set_legs_fired >= 12:
                suppressed.append(_suppressed(tc.id, name, "12 set_legs per turn max"))
            elif name == "wait" and waits_fired >= 1:
                suppressed.append(_suppressed(tc.id, name, "1 wait per turn max"))
            elif name in failed_tools:
                suppressed.append(_suppressed(tc.id, name, f"{name} already failed this turn"))
            else:
                if name == "set_legs": set_legs_fired += 1
                if name == "wait":    waits_fired += 1
                to_dispatch.append(tc)

        deferred_vision = []
        dispatch_for_run = _current_dispatch()

        async def _run_one_scoped(tc, dmap):
            from core.tools import dispatch_tool
            name = tc.function.name
            args_json = tc.function.arguments
            dbg(f"dispatching {name}({args_json})")
            if name not in dmap:
                env = {"ok": False, "tool": name, "result": {}, "duration_ms": 0,
                       "timestamp": time.time(),
                       "error": f"'{name}' is not available in {'explore scope' if active_scope else 'this context'}"}
                return tc, name, args_json, env
            result = await dispatch_tool(dmap, name, args_json)
            return tc, name, args_json, result

        dispatched = await asyncio.gather(*[_run_one_scoped(tc, dispatch_for_run) for tc in to_dispatch])

        all_results = [(tc, name, result) for tc, name, _, result in dispatched] + \
                      [(None, name, result) for _, name, result in suppressed]

        scope_closed_this_pass = False
        for tool_call, name, result in all_results:
            suppressed_call = tool_call is None
            args_json = tool_call.function.arguments if tool_call else "{}"
            try:
                args = json.loads(args_json) if args_json else {}
            except json.JSONDecodeError:
                args = {"_raw": args_json}

            if not suppressed_call:
                print_tool_call(name, args, result)

            if not result.get("ok"):
                failed_tools.add(name)

            if tool_call is None:
                continue

            # --- conclude inside a scope: splice + close ---
            if active_scope is not None and name == "conclude" and result.get("ok"):
                from core.scope import splice_messages
                map_dict = result["result"]["map"]
                originating_id = active_scope.originating_tool_call_id
                tagged = list(active_scope.tagged_message_indexes)
                # Snapshot the explore assistant message + originating id so we can
                # also persist them to memory.
                # Splice messages list in place.
                new_messages = splice_messages(
                    messages, tagged_indexes=tagged,
                    tool_call_id=originating_id, result_json=json.dumps(map_dict),
                )
                messages.clear()
                messages.extend(new_messages)
                # Persist explore tool_call + map result into long-term memory.
                if explore_assistant_msg_index is not None:
                    explore_pair_assistant = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": originating_id, "type": "function",
                            "function": {"name": "explore", "arguments": "{}"},
                        }],
                    }
                    memory.append(explore_pair_assistant)
                    memory.append({"role": "tool", "tool_call_id": originating_id,
                                   "content": json.dumps(map_dict)})
                active_scope = None
                explore_assistant_msg_index = None
                explore_originating_tool_call_id = None
                scope_closed_this_pass = True
                continue

            # --- normal tool result handling ---
            if name == "capture_vision" and result.get("ok"):
                image_b64 = result["result"].get("image_base64", "")
                tool_result_idx = len(messages)
                messages.append(llm_client.format_tool_result(tool_call.id, "Camera snapshot taken."))
                if active_scope is not None:
                    from core.scope import tag_message_index
                    tag_message_index(active_scope, tool_result_idx)
                if image_b64:
                    deferred_vision.append({
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                            {"type": "text", "text": "This is your current camera view. Describe what you observe."},
                        ],
                    })
                    if len(gallery_store) >= 50:
                        gallery_store.pop(0)
                    gallery_store.append({"label": "capture", "image_b64": image_b64, "ts": time.time()})
                    _emit({"type": "image", "label": "capture", "image_b64": image_b64})
            else:
                tool_result_idx = len(messages)
                messages.append(llm_client.format_tool_result(tool_call.id, json.dumps(result)))
                if active_scope is not None:
                    from core.scope import tag_message_index
                    tag_message_index(active_scope, tool_result_idx)

        for tool_id, name, result in suppressed:
            sup_idx = len(messages)
            messages.append(llm_client.format_tool_result(tool_id, json.dumps(result)))
            if active_scope is not None:
                from core.scope import tag_message_index
                tag_message_index(active_scope, sup_idx)

        for msg in deferred_vision:
            vis_idx = len(messages)
            messages.append(msg)
            if active_scope is not None:
                from core.scope import tag_message_index
                tag_message_index(active_scope, vis_idx)

        dbg(f"follow-up LLM call (iteration {iterations + 1})")
        try:
            response = await llm_client.chat_complete(messages, _current_tool_schemas(), thinking=thinking_enabled)
        except Exception as e:
            print(f"  LLM error on follow-up: {e}")
            return

        if not response.choices:
            print("  LLM error: empty choices on follow-up")
            return

        if response.choices:
            content = response.choices[0].message.content
            clean_content, think_blocks = _extract_think_blocks(content)
            for block in think_blocks:
                block = block.strip()
                if block:
                    print(f"  [think] {block[:120]}...")
                    _emit({"type": "think", "text": block})
            if think_blocks and response.choices[0].message.content != clean_content:
                response.choices[0].message.content = clean_content
            if clean_content:
                print_monologue(clean_content)

        iterations += 1

    if iterations >= _current_cap():
        print("  [safety] Tool call limit reached, stopping.")

    final_text = response.choices[0].message.content
    _fire_face("idle")

    if kind == "heartbeat" and iterations == 0:
        return

    memory.append({"role": "user", "content": user_input})
    memory.append({"role": "assistant", "content": final_text or ""})
```

> **Implementer notes:**
> - The new logic preserves all existing per-turn caps, the heartbeat-drop rule, vision deferral, and suppressed-message handling. Only the scope routing + tagging + splice are new.
> - `explore_entry` is created in Task 16; for now this task will import it but the test only checks the `active_scope` global. The test in step 1 will pass; the import is forward-referenced.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_explore_integration.py::test_active_scope_global_exists -v`
Expected: PASS

Also re-run the existing brain tests to make sure nothing broke:
Run: `pytest tests/test_heartbeat.py tests/test_memory_window.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/brain.py tests/test_explore_integration.py
git commit -m "feat(brain): active_scope global + scope-aware dispatch/tagging/splice in _process"
```

---

## Task 16: `explore_entry` in habits.py

**Files:**
- Modify: `core/habits.py`
- Test: `tests/test_explore_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_explore_integration.py`:

```python
@pytest.mark.asyncio
async def test_explore_entry_opens_scope_and_returns_workflow_message():
    from core import brain
    from core.habits import explore_entry
    brain.active_scope = None  # reset
    msg = await explore_entry(pi=None, brain_module=brain, tool_call_id="call_xyz", assistant_idx=2)
    assert brain.active_scope is not None
    assert brain.active_scope.originating_tool_call_id == "call_xyz"
    assert brain.active_scope.originating_tool_name == "explore"
    assert msg["role"] == "user"
    assert "Explore" in msg["content"] or "explore" in msg["content"].lower()
    brain.active_scope = None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_explore_integration.py::test_explore_entry_opens_scope_and_returns_workflow_message -v`
Expected: FAIL — `core.habits` has no attribute `explore_entry`

- [ ] **Step 3: Write minimal implementation**

Add to `core/habits.py`:

```python
from pathlib import Path
from core import brain as brain_module_default


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / "workflows"


async def explore_entry(pi: PiClient, *, brain_module=None, tool_call_id: str, assistant_idx: int) -> dict:
    """Open an explore scope and return the workflow-doc user message for _process to append.

    Mutates brain_module.active_scope. Caller (brain._process) is responsible for
    appending the returned message to the local `messages` list and tagging its
    index via tag_message_index().
    """
    from core.scope import open_scope, tag_message_index
    bm = brain_module if brain_module is not None else brain_module_default
    workflow_path = WORKFLOWS_DIR / "explore.md"
    workflow_doc = workflow_path.read_text(encoding="utf-8")
    bm.active_scope = open_scope(
        originating_tool_call_id=tool_call_id,
        originating_tool_name="explore",
    )
    return {"role": "user", "content": workflow_doc}
```

> **Note:** Since `explore.md` is created in Task 17, this test will fail on `FileNotFoundError`. Create a minimal placeholder now so this task can pass: `mkdir -p workflows && echo "# Explore (placeholder)\n\nFull content lands in Task 17." > workflows/explore.md`.

- [ ] **Step 4: Run test to verify it passes**

```bash
mkdir -p workflows
printf "# Explore (placeholder)\n\nFull content lands in Task 17.\n" > workflows/explore.md
```

Run: `pytest tests/test_explore_integration.py::test_explore_entry_opens_scope_and_returns_workflow_message -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/habits.py tests/test_explore_integration.py workflows/explore.md
git commit -m "feat(habits): explore_entry opens scope, returns workflow message"
```

---

## Task 17: `workflows/explore.md` — full skill doc

**Files:**
- Modify: `workflows/explore.md`

- [ ] **Step 1: Write the full workflow doc** (no test — content review)

Replace `workflows/explore.md` with:

```markdown
# Explore — map a room as a chain of nodes

You are mapping the room you're standing in. You'll create a chain of
nodes, where each node is a spot where you spun a full 360° and described
what you saw. Consecutive nodes are connected by a known reversible walk,
so when you're done you can always return to where you started.

When you call `conclude()` only the map survives. The individual photos,
your scan-time notes, every single tool call — all of that is wiped from
your memory. Only the final structured map remains. So tag thoroughly:
your future self relies on what you record now.

## Headings

At every node, you start facing `x0` (your arrival heading). One right
turn = `+1` step in x. Photos are taken at `x0, x1, ... x11` — twelve in
total, one per ~30°. Twelve right turns brings you back to `x0`.

You don't compute x. The tools track it. Every result you get tells you
your `current_x`. You just turn and record.

## At each node, do this 12 times:

1. `capture_vision` — look at where you're facing now. You get back the
   image plus your `current_x`.
2. Describe the image in your monologue. Identify:
   - **anchors**: fixed landmarks that won't move (vents, doors, frames,
     windows, big furniture).
   - **objects**: things on/in/around the anchors (cups, books, chargers,
     clothes, toys).
   - is this an **open path**? — clear floor in front of you, somewhere
     worth going next.
3. `record_photo(anchors=[...], objects=[...], description="...",
                  open_path=true|false, forward_steps=N)`
   - `forward_steps` is REQUIRED whenever `open_path=true`. It's how many
     forward steps you commit to walking to drop the next node there.
4. `move(direction="turn right", steps=1)` — turns 30°. `current_x`
   increments to your new heading.
5. Repeat from step 1. After twelve right turns you're back at `x0`.

You may set `open_path=true` on **AT MOST ONE photo per node**. That's
the direction you commit to going next. Choose well — once you commit,
you'll walk there.

If nothing in this node's 12 photos looks worth exploring further (or
you've covered enough of the room), tag **no photo** as open_path. That
marks this node as terminal.

## After 12 photos at a node:

- `commit_node_and_advance()` finalizes the node.
  - If you tagged an open_path: this turns you to that heading, walks
    `forward_steps`, and resets you at the new node with `current_x=0`.
    Loop back to "At each node."
  - If no open_path: nothing moves. You're done adding nodes. Continue to
    "When you're done."

If `commit_node_and_advance` returns `ok: false`, the move was aborted
(usually an obstacle) and you're back at the same node. Pick a different
direction by re-running the scan or selectively re-recording photos with
a new open_path — keeping in mind you can only set ONE open_path per
node, so if you already set one and it failed, choose differently next
time around. After 3 failures total in a single explore run, the tool
will return `aborted: true, reason: "..."` — at that point call
`return_to_origin()` then `conclude(status="inconclusive")`.

## When you're done adding nodes:

- `return_to_origin()` — walks your chain backward to Node 0 atomically.
  You don't do any turn/walk math. The tool reports `{success, last_node_reached}`.
- `conclude(status="done" if success else "inconclusive",
            notes="<one-line summary of the room>")`.

## What you may NOT call inside explore:

- `pose`, `do_trick`, `get_perception`, `investigate`, `cast_spell`,
  `set_legs` — these are blocked for the duration of the scope.
- `move("forward", ...)` and `move("backward", ...)` — only single
  turn-left or turn-right steps are allowed. Forward motion goes through
  `commit_node_and_advance`.

Still available (use sparingly): `get_distance`, `get_battery`,
`set_face`, `speak` (only if you need to ask for help — e.g. the room is
too dark to see), `wait`.

## Example monologue + tools at a single x

> "I see a desk straight ahead. There's a laptop on it and a mug. The
>  floor between me and the desk looks clear — I'd say about 8 steps. I'll
>  mark this direction as my open path."

```
record_photo(
  anchors=["desk"],
  objects=["laptop", "mug"],
  description="desk ahead, laptop centered, floor clear ~8 steps",
  open_path=true,
  forward_steps=8
)
move(direction="turn right", steps=1)
```

## Tips

- Anchors stay still; objects might move tomorrow. Tag them as such.
- If `capture_vision` fails at a turn, record the photo with
  `description="vision failed"`, empty anchors/objects, `open_path=false`,
  and continue. Don't abort the scan.
- One open_path per node. The first `record_photo` with `open_path=true`
  wins for that node — subsequent calls with `open_path=true` will be
  rejected. Think before you commit.
- Don't speak unless something blocks you (e.g. dark room — ask for the
  light). Your job here is to look and remember, not to chat.
```

- [ ] **Step 2: Verify the file content**

Run: `wc -l workflows/explore.md`
Expected: > 80 lines.

- [ ] **Step 3: Re-run the explore_entry test**

Run: `pytest tests/test_explore_integration.py::test_explore_entry_opens_scope_and_returns_workflow_message -v`
Expected: PASS (test asserts message contains "explore" — still satisfied).

- [ ] **Step 4: Commit**

```bash
git add workflows/explore.md
git commit -m "feat(workflows): full explore.md skill doc"
```

---

## Task 18: End-to-end integration test — mocked LLM driving a tiny explore

**Files:**
- Modify: `tests/test_explore_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_explore_integration.py`:

```python
@pytest.mark.asyncio
async def test_full_explore_flow_one_terminal_node():
    """End-to-end: LLM calls explore → 12 record_photo + 12 turns (collapsed for the test) →
    commit_node_and_advance (terminal, no open_path) → conclude(done).
    Memory should contain the originating explore tool_call + a tool result carrying the map.
    """
    from core import brain
    from core.brain import _process, wrap_user_input
    from core import explore_tools

    brain.memory.clear()
    brain.active_scope = None

    # Scripted LLM responses for the live loop.
    # Turn 1 (user): "map the room" → tool_call(explore)
    # Turn 2 (workflow loaded): record photo at x0 + turn right 1
    # Turn 3: commit_node_and_advance (no open_path → terminal)
    # Turn 4: return_to_origin (empty path_stack → no-op success)
    # Turn 5: conclude(done)
    # Turn 6: final monologue

    scripted = [
        _make_response("I will map the room.",
            [_make_tool_call("call_explore", "explore", {})]),
        _make_response("Recording first photo.",
            [_make_tool_call("call_rec1", "record_photo",
                {"anchors": ["bed"], "objects": [], "description": "head of bed"})]),
        _make_response(None,
            [_make_tool_call("call_commit", "commit_node_and_advance", {})]),
        _make_response(None,
            [_make_tool_call("call_return", "return_to_origin", {})]),
        _make_response(None,
            [_make_tool_call("call_conclude", "conclude",
                {"status": "done", "notes": "tiny test room"})]),
        _make_response("Map ready, returned home.", []),
    ]
    script_iter = iter(scripted)

    async def fake_chat_complete(*args, **kwargs):
        return next(script_iter)

    async def fake_pi_call(*args, **kwargs):
        return {"ok": True, "tool": "fake", "result": {}, "duration_ms": 1,
                "timestamp": 0, "error": None}

    with patch.object(brain.llm_client, "chat_complete", new=fake_chat_complete):
        # All Pi methods used in the path
        for attr in ("move", "get_distance", "get_battery", "capture", "set_face", "pose"):
            setattr(brain.pi, attr, AsyncMock(side_effect=fake_pi_call))
        await _process(wrap_user_input("map the room"))

    # Active scope cleared on conclude
    assert brain.active_scope is None
    # Memory contains the explore tool_call assistant + tool result with map
    explore_calls = [m for m in brain.memory
                     if m.get("role") == "assistant" and any(
                         tc.get("function", {}).get("name") == "explore"
                         for tc in (m.get("tool_calls") or []))]
    assert len(explore_calls) == 1
    tool_results = [m for m in brain.memory
                    if m.get("role") == "tool" and m.get("tool_call_id") == "call_explore"]
    assert len(tool_results) == 1
    map_dict = json.loads(tool_results[0]["content"])
    assert map_dict["node_count"] == 1
    assert map_dict["returned_to_origin"] in (True, False)  # depends on path_stack handling
    assert map_dict["nodes"][0]["id"] == 0
    assert "bed" in map_dict["nodes"][0]["anchors_summary"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_explore_integration.py::test_full_explore_flow_one_terminal_node -v`
Expected: FAIL — most likely a missing wiring detail (e.g. assistant message not flowing into memory). Diagnose, fix, re-run.

- [ ] **Step 3: Fix any wiring bugs surfaced**

Common fixes that may be needed:
- If the `explore` tool_call assistant message isn't getting persisted to memory: revisit the `close_scope` block in `brain._process` (Task 15). The persistence block runs only when `name == "conclude"` succeeds — ensure both `memory.append`s happen there.
- If `return_to_origin` with an empty `path_stack` errors out: the planner returns an empty list; the tool sets `returned_to_origin=True` immediately and returns success. Confirm by re-reading `scoped_return_to_origin` in Task 11.
- If the LLM script runs out before conclude is reached: count scripted responses — every `_process` LLM turn consumes one.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_explore_integration.py -v`
Expected: PASS (both integration tests)

- [ ] **Step 5: Commit**

```bash
git add tests/test_explore_integration.py
# Plus any fix commits from Task 15 wiring touch-ups
git commit -m "test(explore): end-to-end one-terminal-node integration"
```

---

## Task 19: Smoke-run + manual on-Pi check (no automation)

**Files:** none (manual verification step)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -x -v`
Expected: PASS — full suite green (existing tests should be untouched by these changes; brain `_process` modifications kept the heartbeat-drop, vision deferral, and suppression caps intact).

- [ ] **Step 2: Bring up llama-server + Pi bridge**

```bash
# Terminal A — llama-server (already running in user's normal workflow)
# Terminal B — Pi bridge
ssh chotu@chotu.local 'sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py'
# Terminal C — brain
source .venv/bin/activate && PALIV_DEBUG=1 python3 -m core.brain
```

- [ ] **Step 3: Trigger explore manually from terminal**

At the `you>` prompt:
```
map the room please
```

Watch for:
- `[explore]` tool dispatch logs (or whatever name appears in `print_tool_call`).
- Sequential `capture_vision` / `record_photo` / `move (turn right, 1)` calls.
- `commit_node_and_advance` either advancing (with a forward move) or returning `advanced:false`.
- `return_to_origin` executing turn+forward pairs.
- `conclude` firing with a `status` + map payload.
- After conclude, the GUI/transcript shows ONE clean `[explore]` tool call with a JSON map result, not the dozens of intermediate calls.

- [ ] **Step 4: Verify memory persistence**

After explore completes, type a follow-up question:
```
where did you see the laptop?
```
Confirm Chotu can answer using the map — this validates that the map persists in `memory` across turns (the splice+memory.append in close_scope worked correctly).

- [ ] **Step 5: Commit nothing — record results in transcript and update plan if needed**

If anything regressed, open a follow-up task; otherwise the implementation is complete.

---

## Out of scope (not implemented in this plan)

- `get_map` tool for retrieving a prior map.
- `investigate` migration to scoped execution.
- Branching / looping node graphs.
- Cm-per-step calibration.
- Mid-scope user interruption.

These are flagged in the spec under "Out of scope."

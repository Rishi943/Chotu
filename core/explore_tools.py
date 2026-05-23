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
    commit_node_state,
    record_photo_state,
    plan_return_steps,
    build_map,
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


OBSTACLE_CM = 15
MAX_FAILED_ADVANCES = 3


async def scoped_commit_node_and_advance(pi: PiClient, scope: Scope) -> dict:
    started = time.time()
    # Snapshot the open_path and current heading before commit mutates state.
    open_path = scope.state.current_node_open_path
    pre_commit_node_id = scope.state.current_node_id
    pre_commit_photos = list(scope.state.current_node_photos)
    pre_commit_x = scope.state.current_x

    advanced, _node = commit_node_state(scope.state)

    if not advanced:
        return _envelope(
            "commit_node_and_advance",
            {"advanced": False, "new_node_id": None, "aborted": False, "reason": None},
            started,
        )

    # After commit: path_stack has the new edge, current_node_id incremented,
    # current_x reset to 0, current_node_photos/open_path cleared.
    edge = scope.state.path_stack[-1]

    def _rollback() -> None:
        scope.state.path_stack.pop()
        scope.state.current_node_id = pre_commit_node_id
        scope.state.current_x = pre_commit_x
        scope.state.nodes.pop()
        restored = []
        for p in pre_commit_photos:
            q = dict(p)
            q["open_path"] = False
            q["forward_steps"] = None
            restored.append(q)
        scope.state.current_node_photos = restored
        scope.state.current_node_open_path = None

    # Turn from current heading (pre-commit x=0 after commit reset) to open_path_x.
    # post-commit current_x is 0; open_path_x is relative to pre-commit frame.
    delta = edge["open_path_x"] % 12
    if delta != 0:
        if delta <= 6:
            env_turn = await pi.move(direction="turn right", steps=delta, speed=SPEED)
        else:
            env_turn = await pi.move(direction="turn left", steps=12 - delta, speed=SPEED)
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

    dist_env = await pi.get_distance()
    cm = (dist_env.get("result") or {}).get("cm", 9999)
    if 0 < cm < OBSTACLE_CM:
        # Don't turn back — just rollback state; robot stays facing open_path_x direction.
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

    fwd_env = await pi.move(direction="forward", steps=edge["forward_steps"], speed=SPEED)
    if not fwd_env.get("ok"):
        await pi.move(direction="backward", steps=edge["forward_steps"], speed=SPEED)
        if delta <= 6:
            await pi.move(direction="turn left", steps=delta, speed=SPEED)
        else:
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

    new_node_id = scope.state.current_node_id
    return _envelope(
        "commit_node_and_advance",
        {"advanced": True, "new_node_id": new_node_id, "aborted": False, "reason": None},
        started,
    )


async def scoped_return_to_origin(pi: PiClient, scope: Scope) -> dict:
    started = time.time()
    plan = plan_return_steps(scope.state.path_stack, scope.state.current_x)
    last_node_reached = len(scope.state.path_stack)  # we're at node N if path_stack has N edges

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
            if forward_steps_remaining:
                edge = forward_steps_remaining.pop()
                last_node_reached = edge["from_node"]

    scope.state.returned_to_origin = True
    return _envelope(
        "return_to_origin",
        {"success": True, "last_node_reached": 0, "error": None},
        started,
    )


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


def build_scope_dispatch(pi: PiClient, scope: Scope) -> dict:
    """Build the name -> async callable map active while a scope is open."""
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

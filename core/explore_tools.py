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

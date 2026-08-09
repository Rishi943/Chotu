"""Five tools, one envelope shape, no exceptions escaping."""

from __future__ import annotations

import json
import time
from typing import Callable

from core.prompts import DOCS_DIR
from core.tool_schemas import ACT_NAMES, SENSE_KINDS

# rough servo time so the motion lock can report something while it waits
_ETA_MS = {"move": 800, "act": 4000}

# Names that mean "walk or turn" even when they arrive as an `act`. The bridge
# only knows the four on the right (`left`/`right` alone silently did nothing on
# the bridge, which is why they are spelled out).
_MOVE_ALIASES = {
    "turn right": "turn right", "turn left": "turn left",
    "right": "turn right", "left": "turn left",
    "turn": "turn right",
    "forward": "forward", "walk": "forward", "walk forward": "forward",
    "backward": "backward", "back": "backward", "walk backward": "backward",
}


def _env(tool: str, result=None, error: str | None = None, ms: int = 0) -> dict:
    return {
        "ok": error is None,
        "tool": tool,
        "result": result,
        "duration_ms": ms,
        "timestamp": time.time(),
        "error": error,
    }


def build_dispatch(pi, motion_runner, speaker, estop=None) -> dict[str, Callable]:
    def _motion_stopped(tool: str) -> bool:
        return estop is not None and estop.is_set()

    async def _move(direction: str = "", steps: int = 1, speed: int = 70):
        if _motion_stopped("move"):
            return _env("move", error="motion is stopped (estop active)")
        eta = max(1500, int(steps) * _ETA_MS["move"])
        return motion_runner.start(
            "move", {"direction": direction, "steps": steps}, eta,
            lambda: pi.move(direction, steps=steps, speed=speed),
        )

    async def _act(name: str = "", reps: int = 1, **ignored):
        # Turning is a MOVE, not an act -- but "turn right" reads like the name
        # of an action, and on 2026-08-09 the model called `act{"turn right"}`
        # three times running and was told "no such action: turn right" while
        # standing next to a `move` tool that turns. It is the same intent; route
        # it rather than refusing it. Stray arguments (`steps` on an act) are
        # ignored for the same reason: a near-miss should still do the thing.
        direction = _MOVE_ALIASES.get(str(name).strip().lower())
        if direction:
            return await _move(direction=direction,
                               steps=int(ignored.get("steps", 1) or 1))

        route = ACT_NAMES.get(name)
        if route is None:
            return _env("act", error=f"no such action: {name}")
        if _motion_stopped("act"):
            return _env("act", error="motion is stopped (estop active)")
        endpoint, bridge_name = route
        if endpoint == "pose":
            if reps > 1:
                return _env(
                    "act",
                    error=(
                        f"{name} is a pose and cannot repeat "
                        f"(requested {reps} reps)"
                    ),
                )
            call = lambda: pi.pose(bridge_name)
        else:
            call = lambda: pi.do_trick(bridge_name, reps=reps)
        eta = _ETA_MS["act"] * max(1, int(reps))
        return motion_runner.start(
            "act", {"name": name, "reps": reps}, eta, call,
        )

    async def _sense(what: str = ""):
        if what not in SENSE_KINDS:
            return _env("sense", error=f"cannot sense {what!r}; "
                                       f"choose one of {', '.join(SENSE_KINDS)}")
        if what == "battery":
            env = await pi.get_battery()
        elif what == "distance":
            env = await pi.get_distance()
        else:
            env = await pi.capture()
        return _env("sense", result=env.get("result"),
                    error=env.get("error"), ms=env.get("duration_ms", 0))

    async def _say(text: str = ""):
        if not text.strip():
            return _env("say", error="nothing to say")
        return await speaker.speak(text)

    async def _read(path: str = ""):
        target = (DOCS_DIR / path).resolve()
        try:
            target.relative_to(DOCS_DIR.resolve())
        except ValueError:
            return _env("read", error="you can only read files under docs/")
        if not target.is_file():
            return _env("read", error=f"no such file: docs/{path}")
        text = target.read_text(encoding="utf8", errors="replace")
        if len(text) > 8000:
            text = text[:8000] + "\n\n[...truncated]"
        return _env("read", result={"path": f"docs/{path}", "text": text})

    return {"move": _move, "act": _act, "sense": _sense,
            "say": _say, "read": _read}


async def dispatch_tool(dispatch: dict, name: str, arguments_json: str) -> dict:
    fn = dispatch.get(name)
    if fn is None:
        return _env(name, error=f"no such tool: {name}")
    try:
        kwargs = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as e:
        return _env(name, error=f"arguments were not valid JSON: {e}")
    if not isinstance(kwargs, dict):
        return _env(name, error="arguments must be a JSON object")
    started = time.time()
    try:
        env = await fn(**kwargs)
    except TypeError as e:
        return _env(name, error=f"wrong arguments: {e}")
    except Exception as e:
        return _env(name, error=str(e))
    if env.get("duration_ms", 0) == 0:
        env["duration_ms"] = int((time.time() - started) * 1000)
    return env

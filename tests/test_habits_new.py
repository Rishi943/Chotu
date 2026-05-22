"""Dry unit tests for new habit-tools (investigate, sweep)."""

import asyncio
import pytest


class _MockPi:
    def __init__(self, distance_cm: float = 80.0):
        self.calls: list[tuple] = []
        self.distance_cm = distance_cm

    async def get_distance(self):
        self.calls.append(("get_distance",))
        return {"ok": True, "tool": "get_distance", "result": {"cm": self.distance_cm, "reliable": True},
                "duration_ms": 1, "timestamp": 0, "error": None}

    async def pose(self, name: str, speed: int = 50):
        self.calls.append(("pose", name, speed))
        return {"ok": True, "tool": "pose", "result": {"pose": name},
                "duration_ms": 1, "timestamp": 0, "error": None}

    async def move(self, direction: str, steps: int = 1, speed: int = 70):
        self.calls.append(("move", direction, steps, speed))
        return {"ok": True, "tool": "move", "result": {"direction": direction, "steps_completed": steps},
                "duration_ms": 1, "timestamp": 0, "error": None}


@pytest.mark.asyncio
async def test_investigate_close_obstacle_looks_up():
    from core.habits import investigate
    pi = _MockPi(distance_cm=12.0)  # below 15cm

    # Patch capture_vision to a stub
    from core import habits
    async def _fake_capture(_pi):
        return {"ok": True, "tool": "capture_vision", "result": {"image_base64": "FAKE"},
                "duration_ms": 1, "timestamp": 0, "error": None}
    orig = habits._capture
    habits._capture = _fake_capture
    try:
        env = await investigate(pi)
    finally:
        habits._capture = orig

    names = [c[0] for c in pi.calls]
    assert "get_distance" in names
    assert ("pose", "look up", 50) in pi.calls or any(c[0] == "pose" and c[1] == "look up" for c in pi.calls)
    assert env["ok"] is True
    assert env["tool"] == "investigate"


@pytest.mark.asyncio
async def test_investigate_clear_path_moves_forward():
    from core.habits import investigate
    pi = _MockPi(distance_cm=80.0)

    from core import habits
    async def _fake_capture(_pi):
        return {"ok": True, "tool": "capture_vision", "result": {"image_base64": "FAKE"},
                "duration_ms": 1, "timestamp": 0, "error": None}
    orig = habits._capture
    habits._capture = _fake_capture
    try:
        env = await investigate(pi)
    finally:
        habits._capture = orig

    assert any(c[0] == "move" and c[1] == "forward" for c in pi.calls)
    assert env["ok"] is True

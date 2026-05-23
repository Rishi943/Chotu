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

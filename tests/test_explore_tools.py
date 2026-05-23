# tests/test_explore_tools.py
"""Async tests for core/explore_tools.py."""

import pytest
from unittest.mock import AsyncMock
from core import world


@pytest.fixture()
def isolated_world(tmp_path, monkeypatch):
    """Give each test its own world.json and reset in-memory graph."""
    p = tmp_path / "world.json"
    monkeypatch.setattr(world, "WORLD_PATH", p)
    world._GRAPH = {"nodes": {}, "origin_node": None, "version": 1}
    yield p


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
    from core.scope import open_scope, TURNS_PER_REVOLUTION
    pi = AsyncMock()
    pi.move.return_value = _ok("move")
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 0
    env = await scoped_move(pi, sc, direction="turn left", steps=1)
    # Wraps to TURNS_PER_REVOLUTION - 1 (e.g. 9 when TPR=10)
    expected = TURNS_PER_REVOLUTION - 1
    assert env["result"]["current_x"] == expected
    assert sc.state.current_x == expected


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
async def test_scoped_record_photo_rejects_double_open_path_same_heading():
    """Two open_path photos at the SAME heading are rejected; different headings are allowed."""
    from core.explore_tools import scoped_record_photo
    from core.scope import open_scope
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 3
    env1 = await scoped_record_photo(sc, anchors=[], objects=[], description="d1", open_path=True, forward_steps=8)
    assert env1["ok"] is True
    # Same heading again — rejected
    env2 = await scoped_record_photo(sc, anchors=[], objects=[], description="d2", open_path=True, forward_steps=5)
    assert env2["ok"] is False
    assert "already" in env2["error"].lower() or "one open_path" in env2["error"].lower()
    assert len(sc.state.current_node_photos) == 1


@pytest.mark.asyncio
async def test_commit_and_advance_terminal():
    """No open_path tagged → terminal: commits node, advanced:false."""
    from core.explore_tools import scoped_commit_node_and_advance
    from core.scope import open_scope, TURNS_PER_REVOLUTION
    pi = AsyncMock()
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    for i in range(TURNS_PER_REVOLUTION):
        sc.state.current_node_photos.append({
            "x": i, "anchors": [], "objects": [], "description": "",
            "open_path": False, "forward_steps": None,
        })
    # current_node_open_paths is empty (no open path) — terminal node
    env = await scoped_commit_node_and_advance(pi, sc)
    assert env["ok"] is True
    assert env["result"] == {"advanced": False, "new_node_id": None, "aborted": False, "reason": None}
    pi.move.assert_not_called()
    pi.get_distance.assert_not_called()


@pytest.mark.asyncio
async def test_commit_and_advance_happy_path(isolated_world):
    """open_path set, distance clear, Pi succeeds: turn from current_x to open_path_x via
    shortest side, walk forward, push edge onto path_stack, reset local state."""
    from core.explore_tools import scoped_commit_node_and_advance
    from core.scope import open_scope, record_photo_state, TURNS_PER_REVOLUTION
    pi = AsyncMock()
    pi.get_distance.return_value = _ok("get_distance", {"cm": 200, "reliable": True})
    pi.move.return_value = _ok("move")
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 0
    # Use record_photo_state to set up open_path at heading 3
    record_photo_state(sc.state, anchors=[], objects=[], description="d0",
                       open_path=True, forward_steps=8)
    sc.state.current_node_photos = [
        {"x": i, "anchors": [], "objects": [], "description": "", "open_path": i == 0, "forward_steps": 8 if i == 0 else None}
        for i in range(TURNS_PER_REVOLUTION)
    ]
    sc.state.current_node_open_paths = {0: {"x": 0, "forward_steps": 8}}

    env = await scoped_commit_node_and_advance(pi, sc)
    assert env["ok"] is True
    assert env["result"]["advanced"] is True
    assert env["result"]["new_node_id"] == 1
    # open_path_x is 0, so delta=0 → no turn, just forward 8
    assert pi.move.await_count == 1
    pi.move.assert_any_await(direction="forward", steps=8, speed=80)
    assert sc.state.current_node_id == 1
    assert sc.state.current_x == 0
    assert sc.state.current_node_photos == []
    assert sc.state.current_node_open_paths == {}


@pytest.mark.asyncio
async def test_commit_and_advance_blocked_by_distance(isolated_world):
    """Ultrasonic reports obstacle < 15cm: don't walk; clear open_path; bump failed_advances."""
    from core.explore_tools import scoped_commit_node_and_advance
    from core.scope import open_scope, record_photo_state
    pi = AsyncMock()
    pi.get_distance.return_value = _ok("get_distance", {"cm": 8, "reliable": True})
    pi.move.return_value = _ok("move")
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 3
    record_photo_state(sc.state, anchors=[], objects=[], description="d0",
                       open_path=True, forward_steps=8)
    sc.state.current_x = 0  # reset x after recording

    # Manually set open_paths to heading 3
    sc.state.current_node_open_paths = {3: {"x": 3, "forward_steps": 8}}
    sc.state.current_node_photos = [{"x": 3, "anchors": [], "objects": [], "description": "d0",
                                      "open_path": True, "forward_steps": 8, "distance_estimate_cm": None}]

    env = await scoped_commit_node_and_advance(pi, sc)
    assert env["ok"] is False
    assert "obstacle" in env["error"].lower()
    assert sc.state.failed_advances == 1
    assert sc.state.current_node_open_paths == {}  # cleared by rollback
    assert sc.state.current_node_id == 0  # did not advance
    # Only the turn was executed (turn right 3 to face open_path_x); the forward was skipped after distance check.
    pi.move.assert_awaited_once_with(direction="turn right", steps=3, speed=80)


@pytest.mark.asyncio
async def test_commit_and_advance_three_failures_force_return(isolated_world):
    """After 3 cumulative failed advances, the tool returns aborted:true."""
    from core.explore_tools import scoped_commit_node_and_advance
    from core.scope import open_scope
    pi = AsyncMock()
    pi.get_distance.return_value = _ok("get_distance", {"cm": 5})
    pi.move.return_value = _ok("move")
    sc = open_scope(originating_tool_call_id="x", originating_tool_name="explore")
    sc.state.current_x = 0
    sc.state.failed_advances = 2

    # The third failure triggers abort
    sc.state.current_node_open_paths = {3: {"x": 3, "forward_steps": 8}}
    sc.state.current_node_photos = [{"x": 3, "anchors": [], "objects": [], "description": "d0",
                                      "open_path": True, "forward_steps": 8, "distance_estimate_cm": None}]
    env = await scoped_commit_node_and_advance(pi, sc)
    assert env["ok"] is False
    assert env["result"]["aborted"] is True
    assert "3 advance failures" in env["result"]["reason"]
    assert sc.state.failed_advances == 3


@pytest.mark.asyncio
async def test_return_to_origin_two_node_chain():
    from core.explore_tools import scoped_return_to_origin
    from core.scope import open_scope, TURNS_PER_REVOLUTION
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
    # Expected moves: turn right TURNS_PER_REVOLUTION//2 (to face back), forward 8.
    half = TURNS_PER_REVOLUTION // 2
    pi.move.assert_any_await(direction="turn right", steps=half, speed=80)
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

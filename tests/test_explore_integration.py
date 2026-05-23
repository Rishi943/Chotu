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


@pytest.mark.asyncio
async def test_full_explore_flow_one_terminal_node():
    """End-to-end: LLM calls explore → record_photo → commit_node_and_advance (terminal)
    → return_to_origin → conclude(done).
    Memory should contain the originating explore tool_call + a tool result carrying the map.
    """
    from core import brain
    from core.brain import _process, wrap_user_input
    from core import explore_tools

    brain.memory.clear()
    brain.active_scope = None

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
        for attr in ("move", "get_distance", "get_battery", "capture", "set_face", "pose"):
            setattr(brain.pi, attr, AsyncMock(side_effect=fake_pi_call))
        await _process(wrap_user_input("map the room"))

    assert brain.active_scope is None
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
    assert map_dict["returned_to_origin"] in (True, False)
    assert map_dict["nodes"][0]["id"] == 0
    assert "bed" in map_dict["nodes"][0]["anchors_summary"]

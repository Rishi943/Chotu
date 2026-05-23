"""Unit tests for core/scope.py — pure state machine for explore habit."""

import pytest
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


def test_bump_x_right():
    from core.scope import ExploreState, bump_x
    s = ExploreState(current_x=0)
    bump_x(s, +1)
    assert s.current_x == 1
    bump_x(s, +1)
    assert s.current_x == 2


def test_bump_x_wraps_right():
    from core.scope import ExploreState, bump_x
    s = ExploreState(current_x=9)
    bump_x(s, +1)
    assert s.current_x == 0


def test_bump_x_left_wraps():
    from core.scope import ExploreState, bump_x
    s = ExploreState(current_x=0)
    bump_x(s, -1)
    assert s.current_x == 9


def test_bump_x_multi_step():
    from core.scope import ExploreState, bump_x
    s = ExploreState(current_x=3)
    bump_x(s, +5)
    assert s.current_x == 8
    bump_x(s, -9)
    assert s.current_x == 9


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
        "distance_estimate_cm": None,
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
    assert s.current_node_id == 0


def test_commit_node_advance_resets_local_state():
    from core.scope import ExploreState, commit_node_state
    s = ExploreState(current_node_id=0, current_x=3)
    s.current_node_photos = [_photo(i) for i in range(12)]
    s.current_node_open_path = {"x": 3, "forward_steps": 8}
    advanced, node = commit_node_state(s)
    assert advanced is True
    assert node["id"] == 0
    assert s.current_node_id == 1
    assert s.current_x == 0
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


def test_plan_return_two_node_chain():
    """Path: node 0 → (x=3, 8 steps) → node 1 (terminal).
    Robot is at node 1, current_x=0 (arrival heading).
    Return = turn right 5 (180° with mod 10), forward 8."""
    from core.scope import plan_return_steps
    path_stack = [{"from_node": 0, "open_path_x": 3, "forward_steps": 8}]
    current_x = 0
    steps = plan_return_steps(path_stack, current_x)
    assert steps == [
        ("turn right", 5),
        ("forward", 8),
    ]


def test_plan_return_three_node_chain():
    from core.scope import plan_return_steps
    path_stack = [
        {"from_node": 0, "open_path_x": 3, "forward_steps": 8},
        {"from_node": 1, "open_path_x": 1, "forward_steps": 6},
    ]
    current_x = 0
    steps = plan_return_steps(path_stack, current_x)
    assert steps == [
        ("turn right", 5),
        ("forward", 6),
        ("turn right", 5),
        ("turn right", 5),
        ("forward", 8),
    ]


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
    assert spliced == [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "call_x", "content": "{}"},
    ]


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

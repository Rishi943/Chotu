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

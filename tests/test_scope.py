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

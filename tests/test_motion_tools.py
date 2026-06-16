"""peek_over must serialize with other motion on the brain side."""

from core.motion_lock import MOTION_TOOLS


def test_peek_over_is_a_motion_tool():
    assert "peek_over" in MOTION_TOOLS


def test_existing_motion_tools_unchanged():
    assert {"move", "set_legs", "pose"} <= MOTION_TOOLS

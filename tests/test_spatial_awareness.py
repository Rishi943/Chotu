"""Unit tests for spatial-awareness helpers in brain.py."""


def test_scan_labels_and_degrees_align():
    from chotu.brain import SCAN_LABELS, SCAN_DEGREES, SCAN_SEGMENTS
    assert len(SCAN_LABELS) == SCAN_SEGMENTS == 6
    assert len(SCAN_DEGREES) == 6
    assert SCAN_DEGREES == [0, 60, 120, 180, 240, 300]
    assert SCAN_LABELS == [
        "front", "front-right", "back-right",
        "back", "back-left", "front-left",
    ]


def test_build_map_key_combines_label_and_degree():
    from chotu.brain import _build_map_key
    assert _build_map_key("front", 0) == "front (+0°)"
    assert _build_map_key("front-right", 60) == "front-right (+60°)"
    assert _build_map_key("back", 180) == "back (+180°)"


def test_should_invalidate_map_after_turn_true_for_turn_right():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": True}
    assert _should_invalidate_map_after_turn("move", {"direction": "turn right"}, result) is True


def test_should_invalidate_map_after_turn_true_for_turn_left():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": True}
    assert _should_invalidate_map_after_turn("move", {"direction": "turn left"}, result) is True


def test_should_invalidate_map_after_turn_false_for_forward():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": True}
    assert _should_invalidate_map_after_turn("move", {"direction": "forward"}, result) is False


def test_should_invalidate_map_after_turn_false_for_backward():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": True}
    assert _should_invalidate_map_after_turn("move", {"direction": "backward"}, result) is False


def test_should_invalidate_map_after_turn_false_when_call_failed():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": False, "error": "estop blocked"}
    assert _should_invalidate_map_after_turn("move", {"direction": "turn right"}, result) is False


def test_should_invalidate_map_after_turn_false_for_pose_or_set_legs():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": True}
    assert _should_invalidate_map_after_turn("pose", {"name": "look left"}, result) is False
    assert _should_invalidate_map_after_turn("set_legs", {"legs": [[60, 0, -30]] * 4}, result) is False


def test_should_invalidate_map_after_turn_false_for_other_tools():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": True}
    assert _should_invalidate_map_after_turn("speak", {"text": "hi"}, result) is False
    assert _should_invalidate_map_after_turn("capture_vision", {}, result) is False

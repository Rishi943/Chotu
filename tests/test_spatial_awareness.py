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


def test_run_one_clears_object_map_after_turn(monkeypatch):
    """A successful turn dispatched through _run_one must clear object_map."""
    import asyncio
    from chotu import brain

    # Seed the map with sentinel data
    brain.object_map.clear()
    brain.object_map.update({"front (+0°)": ["bottle"], "_timestamp": 0.0})

    # Stub dispatch_tool so we don't need the Pi
    async def fake_dispatch(_map, _name, _args_json):
        return {"ok": True, "tool": "move", "result": {}, "duration_ms": 10,
                "timestamp": 0, "error": None}

    monkeypatch.setattr(brain, "dispatch_tool", fake_dispatch)

    class FakeFn:
        def __init__(self, name, args):
            self.name = name
            self.arguments = args
    class FakeTc:
        def __init__(self, name, args):
            self.function = FakeFn(name, args)

    tc = FakeTc("move", '{"direction": "turn right", "steps": 2}')
    asyncio.run(brain._run_one(tc))

    assert brain.object_map == {}, f"map should be cleared, got {brain.object_map}"


def test_run_one_preserves_object_map_after_forward(monkeypatch):
    import asyncio
    from chotu import brain

    brain.object_map.clear()
    brain.object_map.update({"front (+0°)": ["bottle"], "_timestamp": 0.0})

    async def fake_dispatch(_map, _name, _args_json):
        return {"ok": True, "tool": "move", "result": {}, "duration_ms": 10,
                "timestamp": 0, "error": None}

    monkeypatch.setattr(brain, "dispatch_tool", fake_dispatch)

    class FakeFn:
        def __init__(self, name, args):
            self.name = name
            self.arguments = args
    class FakeTc:
        def __init__(self, name, args):
            self.function = FakeFn(name, args)

    tc = FakeTc("move", '{"direction": "forward", "steps": 1}')
    asyncio.run(brain._run_one(tc))

    assert "front (+0°)" in brain.object_map, "forward must not clear map"


def test_system_prompt_describes_body_relative_labels():
    from chotu.system_prompt import build_system_prompt
    p = build_system_prompt("auto")
    assert "front-right" in p
    assert "back-left" in p
    assert "+60°" in p or "+0°" in p
    assert "map clears the moment you turn" in p


def test_system_prompt_no_longer_uses_compass_labels_in_example():
    from chotu.system_prompt import build_system_prompt
    p = build_system_prompt("reactive")
    assert "Red cup north" not in p

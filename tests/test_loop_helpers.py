from core.loop_helpers import describe_motion, motion_from_calls


def test_describe_move_forward_plural():
    assert describe_motion("move", {"direction": "forward", "steps": 2}) == "walked forward 2 steps"


def test_describe_move_forward_singular():
    assert describe_motion("move", {"direction": "forward", "steps": 1}) == "walked forward 1 step"


def test_describe_turn_degrees():
    assert describe_motion("move", {"direction": "turn right", "steps": 2}) == "turned right ~60°"


def test_describe_pose():
    assert describe_motion("pose", {"name": "wave"}) == "posed: wave"


def test_describe_non_motion_is_no_movement():
    assert describe_motion("speak", {"text": "hi"}) == "no movement"


def test_motion_from_calls_picks_first_motion():
    calls = [("speak", {"text": "hi"}), ("move", {"direction": "forward", "steps": 3})]
    assert motion_from_calls(calls) == "walked forward 3 steps"


def test_motion_from_calls_none():
    assert motion_from_calls([("speak", {"text": "hi"})]) == "no movement"

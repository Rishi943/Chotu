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


from core.loop_helpers import push_frame, render_frames


def test_push_frame_caps_at_three():
    stack = []
    for i in range(5):
        push_frame(stack, f"img{i}", "walked forward 1 step")
    assert len(stack) == 3
    assert [f["image_b64"] for f in stack] == ["img2", "img3", "img4"]


def test_push_frame_sets_motion_on_previous():
    stack = []
    push_frame(stack, "img0", "ignored-first")   # first has no predecessor
    push_frame(stack, "img1", "turned right ~30°")
    # img0 is now the predecessor of img1, so it carries the transition motion
    assert stack[0]["motion"] == "turned right ~30°"
    assert stack[1]["motion"] == ""               # newest = NOW, no motion yet


def test_render_frames_labels():
    stack = [
        {"image_b64": "a", "motion": "turned right ~30°"},
        {"image_b64": "b", "motion": "walked forward 2 steps"},
        {"image_b64": "c", "motion": ""},
    ]
    msgs = render_frames(stack)
    labels = [m["content"][1]["text"] for m in msgs]
    assert labels[0] == "[frame -2 | 2 calls ago, then: turned right ~30°]"
    assert labels[1] == "[frame -1 | 1 call ago, then: walked forward 2 steps]"
    assert labels[2] == "[frame 0 | NOW — current view]"
    assert msgs[2]["content"][0]["image_url"]["url"] == "data:image/jpeg;base64,c"


def test_render_frames_empty():
    assert render_frames([]) == []

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


from core.loop_helpers import trim_loop_window, strip_old_monologue


def _iter(i):
    return [
        {"role": "assistant", "content": f"think{i}", "tool_calls": [{"id": f"c{i}"}]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": "{}"},
    ]


def test_trim_keeps_last_n_iterations():
    mem = []
    for i in range(5):
        mem.extend(_iter(i))
    trim_loop_window(mem, n=2)
    assistants = [m for m in mem if m["role"] == "assistant"]
    assert [m["content"] for m in assistants] == ["think3", "think4"]
    # tool result for the oldest kept assistant is still present
    assert any(m.get("tool_call_id") == "c3" for m in mem)


def test_trim_noop_under_budget():
    mem = _iter(0) + _iter(1)
    before = [dict(m) for m in mem]
    trim_loop_window(mem, n=5)
    assert mem == before


def test_strip_blanks_old_monologue_keeps_tool_calls():
    mem = []
    for i in range(4):
        mem.extend(_iter(i))
    strip_old_monologue(mem, keep_last=2)
    assistants = [m for m in mem if m["role"] == "assistant"]
    assert [a["content"] for a in assistants] == ["", "", "think2", "think3"]
    # tool_calls survive the strip
    assert all("tool_calls" in a for a in assistants)


from core.loop_helpers import pace_remainder, split_tool_calls
from core.llm_client import ToolCall, ToolFunction


def _tc(i, name):
    return ToolCall(id=f"t{i}", function=ToolFunction(name=name, arguments="{}"))


def test_pace_remainder_sleeps_when_fast():
    assert pace_remainder(1.0, 2.0) == 1.0


def test_pace_remainder_zero_when_slow():
    assert pace_remainder(3.0, 2.0) == 0.0


def test_split_suppresses_second_motion_and_speak():
    calls = [_tc(0, "move"), _tc(1, "speak"), _tc(2, "pose"), _tc(3, "speak"), _tc(4, "set_face")]
    keep, suppressed = split_tool_calls(calls)
    assert [c.function.name for c in keep] == ["move", "speak", "set_face"]
    assert [c.function.name for c in suppressed] == ["pose", "speak"]


def test_split_allows_non_motion_non_speak_through():
    calls = [_tc(0, "get_distance"), _tc(1, "get_battery")]
    keep, suppressed = split_tool_calls(calls)
    assert len(keep) == 2 and suppressed == []


import asyncio
import pytest
from core.loop_helpers import PendingInput, paced_sleep


def test_pending_drain_joins_and_clears():
    p = PendingInput()
    p.push("hello")
    p.push("  ")        # whitespace ignored
    p.push("there")
    assert p.drain() == "hello\nthere"
    assert p.drain() is None    # empty after drain


@pytest.mark.asyncio
async def test_paced_sleep_returns_early_on_input():
    p = PendingInput()

    async def feed():
        await asyncio.sleep(0.02)
        p.push("hi")

    asyncio.create_task(feed())
    start = asyncio.get_event_loop().time()
    await paced_sleep(1.0, p)
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed < 0.5, f"sleep should have been cut short, took {elapsed}s"


@pytest.mark.asyncio
async def test_paced_sleep_zero_returns_immediately():
    p = PendingInput()
    await paced_sleep(0.0, p)   # must not hang

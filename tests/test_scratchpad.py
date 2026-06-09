from core.scratchpad import Scratchpad


def _dist(reliable):
    return {"ok": True, "tool": "get_distance",
            "result": {"cm": (50.0 if reliable else -1.0), "reliable": reliable}}


def test_recent_actions_newest_first_and_capped():
    sp = Scratchpad()
    sp.update([("move", {"direction": "forward", "steps": 1}, {})])
    sp.update([("move", {"direction": "turn left", "steps": 1}, {})])
    sp.update([("move", {"direction": "forward", "steps": 2}, {})])
    sp.update([("move", {"direction": "backward", "steps": 1}, {})])
    assert list(sp.recent) == [
        "walked backward 1 step",
        "walked forward 2 steps",
        "turned left ~30°",
    ]  # maxlen 3, newest first


def test_heading_accumulates_from_turns():
    sp = Scratchpad()
    sp.update([("move", {"direction": "turn left", "steps": 1}, {})])   # -30
    sp.update([("move", {"direction": "turn right", "steps": 2}, {})])  # +60
    assert sp.heading == 30


def test_distance_marked_dead_after_three_unreliable():
    sp = Scratchpad()
    for _ in range(2):
        sp.update([("get_distance", {}, _dist(False))])
    assert sp.distance_alive is True       # not yet
    sp.update([("get_distance", {}, _dist(False))])
    assert sp.distance_alive is False      # 3rd strike


def test_reliable_reading_revives_sensor():
    sp = Scratchpad()
    for _ in range(3):
        sp.update([("get_distance", {}, _dist(False))])
    assert sp.distance_alive is False
    sp.update([("get_distance", {}, _dist(True))])
    assert sp.distance_alive is True
    assert sp.distance_dead_streak == 0


def test_last_said_captured_from_speak():
    sp = Scratchpad()
    sp.update([("speak", {"text": "Found a way through."}, {})])
    assert sp.last_said == "Found a way through."


def test_render_none_when_empty():
    assert Scratchpad().render() is None


def test_render_contains_actions_sensor_and_speech():
    sp = Scratchpad()
    sp.update([("move", {"direction": "forward", "steps": 1}, {})])
    for _ in range(3):
        sp.update([("get_distance", {}, _dist(False))])
    sp.update([("speak", {"text": "Moving on."}, {})])
    msg = sp.render()
    assert msg["role"] == "user"
    assert msg["_origin"] == "state"
    body = msg["content"]
    assert body.startswith("[STATE]")
    assert "walked forward 1 step" in body
    assert "DEAD" in body
    assert '"Moving on."' in body

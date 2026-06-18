from pi_bridge.sequence import play_frames


class FakeCrawler:
    def __init__(self):
        self.calls = []          # list of (legs, speed)

    def do_step(self, legs, speed):
        self.calls.append((legs, speed))


def _frames():
    return [
        {"legs": [[45, 45, -50]] * 4, "speed": 60, "hold_s": 0},
        {"legs": [[45, 0, -50]] * 4, "speed": 200, "hold_s": 0.3},  # over-cap on purpose
    ]


def test_plays_each_frame_in_order_then_stands():
    c = FakeCrawler()
    slept = []
    play_frames(c, _frames(), cap=90, sleep=slept.append)
    # one do_step per frame + a final stand
    assert len(c.calls) == 3
    assert c.calls[0] == ([[45, 45, -50]] * 4, 60)
    assert c.calls[1][1] == 90                       # 200 capped to 90
    assert c.calls[2] == ("stand", 40)               # ends standing
    assert slept == [0.3]                            # only the non-zero hold


def test_speed_override_applies_to_all_frames_capped():
    c = FakeCrawler()
    play_frames(c, _frames(), cap=90, speed_override=75, sleep=lambda s: None)
    assert c.calls[0][1] == 75 and c.calls[1][1] == 75

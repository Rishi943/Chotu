"""Pure on-Pi frame-sequence player. No hardware imports so it is unit-testable.

`server.py` and add-chotu-tool's generated `_play_{tool}` both call play_frames, so the
studio preview and the scaffolded tool run identical playback. Each frame is
{legs: 4x[x,y,z], speed, hold_s}. Runs back-to-back in one loop (the caller already holds
the motion lock) — no network gap between frames, ending standing.
"""
import time


def play_frames(crawler, frames, cap=90, speed_override=None, sleep=time.sleep):
    for f in frames:
        spd = min(speed_override or f.get("speed", 60), cap)
        crawler.do_step(f["legs"], spd)
        if f.get("hold_s"):
            sleep(f["hold_s"])
    crawler.do_step("stand", 40)

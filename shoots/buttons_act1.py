"""Reel "What Are These Buttons?" — Act 1: "I can move?"

One warm process, no inter-beat CLI latency (see SKILL.md "Bulk beats").
Retake = edit the lines below, rerun. Run from repo root with the reel env:

  PALIV_TRACE_DIR=out/sessions/<sid>_fable PYTHONPATH=<repo> \
  PALIV_SPEAK_OUTPUT=pi LOCALIS_PIPER_MODEL=core/voices/en_US-libritts_r-medium.onnx \
  PALIV_PIPER_ARGS="--length-scale 1.0 --sentence-silence 0.6 --volume 1.0 -s 668" \
  <PY> shoots/buttons_act1.py    # (piper Scripts dir must be on PATH)

Beats (SHOOT_BRIEF Act 1):
  1. idle face, hold still 1.5s (alone in frame)
  2. wake + first-voice line (startled by its own voice)
  3. surprised face, button line
  4. pushup on a thread (speed 100) + mid-rep line at ~0.5s
  5. 1.5s beat, judging face, closer
"""
import json
import sys
import threading
import time

from scripts.robot import chotu_tool


def call(*args):
    sys.argv = ["chotu_tool", *args]
    chotu_tool.main()


# Continuous pushups: the exact up/down leg frames from _trick_pushup, sent as
# ONE play_sequence so there is NO stand between reps (do_trick re-stands every
# call). Ends with a single stand. speed capped at play_sequence's 90.
_PUSH_DOWN = [[80, 0, -30],  [80, 0, -30],  [0, 120, -60], [0, 120, -60]]
_PUSH_UP   = [[80, 0, -100], [80, 0, -100], [0, 120, -60], [0, 120, -60]]


def continuous_pushups(reps=3, speed=80, down_hold=0.15, up_hold=0.80):
    # up_hold is the ~300ms pause at the top BETWEEN reps; down_hold is the
    # brief dwell at the bottom. No stand until the very end.
    frames = []
    for _ in range(reps):
        frames.append({"legs": _PUSH_DOWN, "hold_s": down_hold, "speed": speed})
        frames.append({"legs": _PUSH_UP,   "hold_s": up_hold,   "speed": speed})
    call("play_sequence", json.dumps({"frames": frames}))
    call("pose", '{"name":"stand"}')


call("marker", '{"text":"ACT 1 BEGIN"}')

# Beat 1 — alone in frame, idle, hold.
call("set_face", '{"name":"idle"}')
time.sleep(1.5)

# Beat 2 — boot up, then startled by hearing its own voice.
call("think", '{"text":"First breath. The premise: I am hearing my own voice for the first time. Let the surprise be real."}')
call("speak", '{"text":"Oh. Oh, I am on. Hi... wait. Is that me? Am I making that sound?"}')
time.sleep(1.0)

# Beat 3 — surprised face, buttons.
call("set_face", '{"name":"playful"}')
call("think", '{"text":"There is a whole control surface in here. Reach for one and see."}')
call("speak", '{"text":"There is a whole menu of buttons in here. Let me try this one."}')
time.sleep(0.6)

# Beats 4-5 — continuous 5-rep pushup, mid-rep line over the reps, and the
# closer PRE-FIRED during the pushups so its audio onset lands on the stand-up
# out of the 5th rep (not lagging ~4s behind on piper's generation delay).
# piper spends ~GEN_LEAD synthesizing before any audio plays, so every speak is
# launched that many seconds before we want the sound.
call("think", '{"text":"Pressed the button. Body started doing pushups on its own. Both lines have to land ON the motion, so pre-synthesize each one and time its launch to piper generation delay."}')
GEN_LEAD = 2.5      # piper synthesis time before audio starts
PUSH_DUR = 7.8      # measured wall time of the 5-rep block at speed 80

# mid-rep line: head start, then pushups so audio starts with the first rep
st = threading.Thread(target=call, args=("speak", '{"text":"Whoa. What the hell. Nope. Nope. Okay, we are exercising now."}'))
st.start()
time.sleep(GEN_LEAD)

pt = threading.Thread(target=continuous_pushups, kwargs={"reps": 5, "speed": 80, "up_hold": 0.80})
pt.start()

# closer: fire it GEN_LEAD before the block ends so audio lands on the stand-up
time.sleep(PUSH_DUR - GEN_LEAD)
call("set_face", '{"name":"idle"}')
ct = threading.Thread(target=call, args=("speak", '{"text":"Great. One day old and I already have a gym membership."}'))
ct.start()

pt.join()
ct.join()
st.join()

call("marker", '{"text":"ACT 1 END"}')

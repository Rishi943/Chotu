"""Generate Chotu's built-in motions as frames-JSON into assets/Animations/builtin/.

One-time, reproducible, NO hardware imports. Gait/pose step lists are ported verbatim
from the picrawler MoveList (read off the Pi); trick keyframes are sampled from the
procedural routines in pi_bridge/server.py. Each step (4x[x,y,z]) becomes one frame.
Run: python -m scripts.gen_builtin_animations
"""
import json
import math
import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "assets" / "Animations" / "builtin"

# --- picrawler MoveList constants (verbatim) ---
XD, XT, XS = 45, 70, 0          # X_DEFAULT, X_TURN, X_START
YD, YT, YW, YS = 45, 130, 120, 0  # Y_DEFAULT, Y_TURN, Y_WAVE, Y_START
ZD, ZU, ZW, ZT, ZP = -50, -30, 60, -40, -76  # Z_DEFAULT, Z_UP, Z_WAVE, Z_TURN, Z_PUSH
SIDE = 77
zc = ZD  # z_current while standing

# turn geometry (verbatim from MoveList)
TEMP_A = math.sqrt((2 * XD + SIDE) ** 2 + YD ** 2)
TEMP_B = 2 * (YS + YD) + SIDE
TEMP_C = math.sqrt((2 * XD + SIDE) ** 2 + (2 * YS + YD + SIDE) ** 2)
TEMP_ALPHA = math.acos((TEMP_A ** 2 + TEMP_B ** 2 - TEMP_C ** 2) / 2 / TEMP_A / TEMP_B)
TX1 = (TEMP_A - SIDE) / 2
TY1 = YS + YD / 2
TX0 = TX1 - TEMP_B * math.cos(TEMP_ALPHA)
TY0 = TEMP_B * math.sin(TEMP_ALPHA) - TY1 - SIDE


def turn_angle_coord(angle):  # verbatim from MoveList.turn_angle_coord
    a = math.atan(YD / (XD + SIDE / 2)); angle1 = a / math.pi * 180
    r1 = math.sqrt(YD ** 2 + (XD + SIDE / 2) ** 2)
    x1 = r1 * math.cos((angle1 - angle) * math.pi / 180) - SIDE / 2
    y1 = r1 * math.sin((angle1 - angle) * math.pi / 180)
    x2 = (XD + SIDE / 2) * math.cos(angle * math.pi / 180) - SIDE / 2
    y2 = (XD + SIDE / 2) * math.sin(angle * math.pi / 180)
    b = math.atan((XD + SIDE / 2) / (YD + SIDE)); angle2 = b / math.pi * 180
    r2 = math.sqrt((XD + SIDE / 2) ** 2 + (YD + SIDE) ** 2)
    x3 = r2 * math.sin((angle2 - angle) * math.pi / 180) - SIDE / 2
    y3 = r2 * math.cos((angle2 - angle) * math.pi / 180) - SIDE
    x3 += 10
    return [x1, y1, x2, y2, x3, y3]


def rnd(step):  # round a 4x[x,y,z] step to ints
    return [[round(v) for v in leg] for leg in step]


# --- discrete gait/pose step lists (stand_position==0 branch) ---
MOVES = {}

MOVES["forward"] = (60, [
    [[XD, YD, zc], [XT, YS, ZU], [XD, YS, zc], [XD, YD, zc]],
    [[XD, YD, zc], [XD, YD * 2, ZU], [XD, YS, zc], [XD, YD, zc]],
    [[XD, YD, zc], [XD, YD * 2, zc], [XD, YS, zc], [XD, YD, zc]],
    [[XD, YS, zc], [XD, YD, zc], [XD, YD, zc], [XD, YD * 2, zc]],
    [[XD, YS, zc], [XD, YD, zc], [XD, YD, zc], [XD, YD * 2, ZU]],
    [[XD, YS, zc], [XD, YD, zc], [XD, YD, zc], [XT, YS, ZU]],
    [[XD, YS, zc], [XD, YD, zc], [XD, YD, zc], [XD, YS, zc]],
])
MOVES["backward"] = (60, [
    [[XD, YD, zc], [XD, YS, zc], [XT, YS, ZU], [XD, YD, zc]],
    [[XD, YD, zc], [XD, YS, zc], [XD, YD * 2, ZU], [XD, YD, zc]],
    [[XD, YD, zc], [XD, YS, zc], [XD, YD * 2, zc], [XD, YD, zc]],
    [[XD, YD * 2, zc], [XD, YD, zc], [XD, YD, zc], [XD, YS, zc]],
    [[XD, YD * 2, ZU], [XD, YD, zc], [XD, YD, zc], [XD, YS, zc]],
    [[XT, YS, ZU], [XD, YD, zc], [XD, YD, zc], [XD, YS, zc]],
    [[XD, YS, zc], [XD, YD, zc], [XD, YD, zc], [XD, YS, zc]],
])
MOVES["turn_left"] = (60, [
    [[XD, YD, zc], [XD, YS, zc], [XT, YS, ZU], [XD, YD, zc]],
    [[TX1, TY1, zc], [TX1, TY1, zc], [TX0, TY0, ZU], [TX0, TY0, zc]],
    [[TX1, TY1, zc], [TX1, TY1, zc], [TX0, TY0, zc], [TX0, TY0, zc]],
    [[TX1, TY1, zc], [TX1, TY1, zc], [TX0, TY0, zc], [TX0, TY0, ZU]],
    [[XD, YS, zc], [XD, YD, zc], [XD, YD, zc], [XT, YS, ZU]],
    [[XD, YS, zc], [XD, YD, zc], [XD, YD, zc], [XD, YS, zc]],
])
MOVES["turn_right"] = (60, [
    [[XD, YD, zc], [XT, YS, ZU], [XD, YS, zc], [XD, YD, zc]],
    [[TX0, TY0, zc], [TX0, TY0, ZU], [TX1, TY1, zc], [TX1, TX1, zc]],
    [[TX0, TY0, zc], [TX0, TY0, zc], [TX1, TY1, zc], [TX1, TX1, zc]],
    [[TX0, TY0, ZU], [TX0, TY0, zc], [TX1, TY1, zc], [TX1, TX1, zc]],
    [[XT, YS, ZU], [XD, YD, zc], [XD, YD, zc], [XD, YS, zc]],
    [[XD, YS, zc], [XD, YD, zc], [XD, YD, zc], [XD, YS, zc]],
])
MOVES["wave"] = (50, [
    [[XD, YD, zc], [XT, YS, ZU], [XD, YS, zc], [XD, YD, zc]],
    [[XD, YD, zc], [XS, YW, ZW], [XD, YS, zc], [XD, YD, zc]],
    [[XD, YD, zc], [XS, YW, ZU], [XD, YS, zc], [XD, YD, zc]],
    [[XD, YD, zc], [XS, YW, ZW], [XD, YS, zc], [XD, YD, zc]],
    [[XD, YD, zc], [XS, YW, ZU], [XD, YS, zc], [XD, YD, zc]],
    [[XD, YD, zc], [XT, YS, ZU], [XD, YS, zc], [XD, YD, zc]],
    [[XD, YD, zc], [XD, YS, zc], [XD, YS, zc], [XD, YD, zc]],
])
MOVES["sit"] = (50, [
    [[XD, YD, ZU], [XT, YS, ZU], [XT, YS, ZU], [XD, YD, ZU]],
])
MOVES["stand"] = (40, [
    [[XD, YD, round(ZD * m)], [XD, YS, round(ZD * m)], [XD, YS, round(ZD * m)], [XD, YD, round(ZD * m)]]
    for m in (0.35, 0.55, 0.75, 0.9, 1.0)
])
MOVES["look_up"] = (50, [
    [[XD, YD, ZD], [XD, YS, ZD], [XT, YS, ZU], [XD, YD, ZU]],
])
MOVES["look_down"] = (50, [
    [[XD, YD, ZU], [XT, YS, ZU], [XD, YS, zc], [XD, YD, zc]],
])


def _look(turn_first):
    li = turn_angle_coord(30)
    t1 = [li[0], li[1], zc]; t2 = [li[2], li[3], zc]; t3 = [li[4], li[5], zc]
    a = [[XD, YD, zc], [XD, YS, zc], [XT, YS, ZU], [XD, YD, zc]]
    b = [t1, t2, [XT, YS, ZU], t3] if turn_first else [t3, [XT, YS, ZU], t2, t1]
    return [a, b]


MOVES["look_left"] = (50, _look(True))
MOVES["look_right"] = (50, _look(False))
MOVES["push_up"] = (60, [
    [[XD, YD, ZU], [XT, YS, ZU], [XT, YS, ZU], [XD, YD, ZU]],          # sit
    [[XT, YS, ZT], [XT, YS, ZT], [XS, YT, ZT], [XS, YT, ZT]],
    [[XT, YS, ZP], [XT, YS, ZP], [XS, YT, ZT], [XS, YT, ZT]],
    [[XT, YS, ZT], [XT, YS, ZT], [XS, YT, ZT], [XS, YT, ZT]],
    [[XT, YS, ZP], [XT, YS, ZP], [XS, YT, ZT], [XS, YT, ZT]],
    [[XT, YS, ZT], [XT, YS, ZT], [XS, YT, ZT], [XS, YT, ZT]],
    [[XD, YD, zc], [XT, YS, zc], [XT, YS, zc], [XD, YD, zc]],          # back toward stand
])

# --- trick keyframes (sampled from pi_bridge/server.py procedural routines) ---
STAND = [[45, 45, -50], [45, 0, -50], [45, 0, -50], [45, 45, -50]]


def _twist_frames():
    frames = [STAND]
    for i in range(4):
        rise = [50, 50, -80 + 55 * 0.5]; drop = [50, 50, -80 - 55]
        s = [None] * 4
        s[i] = rise; s[(i + 2) % 4] = drop; s[(i + 1) % 4] = rise; s[(i - 1) % 4] = drop
        frames.append(s)
    frames.append(STAND)
    return frames


MOVES["twist"] = (100, _twist_frames())


def _swim_frames():
    out = [[[60, 0, -30]] * 4,
           [[80, 20, -20], [80, 20, -20], [40, 60, -50], [40, 60, -50]]]
    for phase in (0.5, 1.0):
        f = [80 + 20 * phase, 20 + 20 * phase, -20 + 10 * phase]
        r = [40 - 20 * phase, 60 + 40 * phase, -50 + 20 * phase]
        out.append([f, f, r, r])
    out.append(STAND)
    return out


MOVES["swimming"] = (100, _swim_frames())


def _handwork_frames():
    base = [[XD, YD, ZU], [XT, YS, ZU], [XT, YS, ZU], [XD, YD, ZU]]  # sit

    def mix(step, leg, coord):
        s = [list(l) for l in step]; s[leg] = list(coord); return s

    left = mix(base, 0, [0, 50, 80]); two = mix(left, 1, [0, 50, 80]); right = mix(base, 1, [0, 50, 80])
    return [base, left, two, right, base, STAND]


MOVES["handwork"] = (100, _handwork_frames())

DESCRIPTIONS = {
    "forward": "Walk forward one gait cycle.", "backward": "Walk backward one gait cycle.",
    "turn_left": "Turn left in place.", "turn_right": "Turn right in place.",
    "wave": "Wave the front-left leg.", "sit": "Sit down.", "stand": "Rise to a stand.",
    "look_up": "Tilt to look up.", "look_down": "Tilt to look down.",
    "look_left": "Turn head to the left.", "look_right": "Turn head to the right.",
    "push_up": "Do push-ups.", "twist": "Twist the body side to side.",
    "swimming": "Swimming-style leg motion.", "handwork": "Raise the front legs in turn.",
}


def build(name, default_speed, steps):
    return {
        "tool": name, "description": DESCRIPTIONS.get(name, ""),
        "persona_gated": False, "default_speed": default_speed,
        "frames": [{"legs": rnd(s), "speed": default_speed, "hold_s": 0} for s in steps],
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (spd, steps) in MOVES.items():
        d = build(name, spd, steps)
        (OUT / f"{name}.json").write_text(json.dumps(d, indent=2))
        print(f"wrote {name}.json ({len(d['frames'])} frames)")


if __name__ == "__main__":
    main()

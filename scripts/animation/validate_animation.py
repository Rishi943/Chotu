"""Validate (and optionally install) a Chotu frames-JSON animation. Laptop-only, no hardware.
Reachability gate uses scripts.animation.kinematics_ref. Run: python -m scripts.animation.validate_animation <f.json> [--install]"""
import argparse, json, math, pathlib, re, sys
from collections import namedtuple
from scripts.animation.kinematics_ref import A, B, C, is_reachable, coord2polar

LEG_NAMES = ["FR", "FL", "RL", "RR"]
STAND = [[45, 45, -50], [45, 0, -50], [45, 0, -50], [45, 45, -50]]
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
_ANIM_DIR = pathlib.Path(__file__).resolve().parents[2] / "assets" / "Animations"
Issue = namedtuple("Issue", "severity msg")
def E(m): return Issue("ERROR", m)
def W(m): return Issue("WARN", m)


def _why(leg):
    x, y, z = leg; L = math.sqrt(x * x + y * y + z * z)
    if L < C: return "too close (L<C)"
    if L > A + B + C: return "too far (L>A+B+C)"
    w = math.sqrt(x * x + y * y); u = math.sqrt((w - C) ** 2 + z * z)
    if u < 30 or u > 91.58: return f"u={u:.0f} out of [30,91.58]"
    a, b, g = coord2polar(leg); return f"angles a={a:.0f} b={b:.0f} g={g:.0f}"


def _is4(legs): return isinstance(legs, list) and len(legs) == 4


def validate(anim):
    out = []
    if not _SNAKE.match(anim.get("tool") or ""): out.append(E("tool must be snake_case"))
    frames = anim.get("frames")
    if not frames: out.append(E("frames must have >=1 entry")); return out
    for fi, f in enumerate(frames):
        legs = f.get("legs")
        if not _is4(legs): out.append(E(f"frame[{fi}].legs must be 4 legs")); continue
        for li, leg in enumerate(legs):
            if not (isinstance(leg, list) and len(leg) == 3 and all(isinstance(v, int) for v in leg)):
                out.append(E(f"frame[{fi}].leg[{LEG_NAMES[li]}] must be 3 ints")); continue
            if not is_reachable(leg):
                out.append(E(f"frame[{fi}].leg[{LEG_NAMES[li]}]={leg} unreachable ({_why(leg)})"))
        sp = f.get("speed", 60)
        if not (isinstance(sp, (int, float)) and 0 <= sp <= 90): out.append(E(f"frame[{fi}].speed out of 0-90"))
        h = f.get("hold_s", 0)
        if not (isinstance(h, (int, float)) and h >= 0): out.append(E(f"frame[{fi}].hold_s must be >=0"))
    if frames[-1].get("legs") != STAND: out.append(W("last frame != STAND (end-on-stand invariant)"))
    if frames[0].get("legs") != STAND: out.append(W("first frame != STAND"))
    for fi in range(1, len(frames)):
        a, b = frames[fi - 1].get("legs"), frames[fi].get("legs")
        if _is4(a) and _is4(b):
            for li in range(4):
                try: d = max(abs(a[li][k] - b[li][k]) for k in range(3))
                except Exception: continue
                if d > 60: out.append(W(f"frame[{fi}].leg[{LEG_NAMES[li]}] jumps {d}mm (>60, may look abrupt)"))
    return out


def install(anim, anim_dir=_ANIM_DIR):
    errs = [i for i in validate(anim) if i.severity == "ERROR"]
    if errs: raise ValueError("; ".join(i.msg for i in errs))
    anim_dir = pathlib.Path(anim_dir); anim_dir.mkdir(parents=True, exist_ok=True)
    dest = (anim_dir / f"{anim['tool']}.json").resolve()
    if dest.parent != anim_dir.resolve(): raise ValueError("invalid path")
    dest.write_text(json.dumps(anim, indent=2)); return dest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("file"); ap.add_argument("--install", action="store_true")
    ns = ap.parse_args(argv)
    anim = json.loads(pathlib.Path(ns.file).read_text())
    issues = validate(anim)
    for i in issues: print(f"{i.severity}: {i.msg}")
    errs = [i for i in issues if i.severity == "ERROR"]
    if errs: print(f"{len(errs)} error(s) — not installing."); return 1
    if ns.install: print(f"installed: {install(anim)}")
    else: print("OK (no errors)")
    return 0


if __name__ == "__main__": sys.exit(main())

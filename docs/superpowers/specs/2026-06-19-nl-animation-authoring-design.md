# Claude-Authored Chotu Animations — Design

**Date:** 2026-06-19
**Scope:** laptop-only, no hardware. Tools so Claude can author + visually iterate + validate frames
JSON for the animation studio. NL→model in-studio bar is DEFERRED (will reuse `validate()`).

## Workflow
Claude authors JSON → `render_animation` (see it) → fix → `validate_animation --install` → studio
library refresh shows it. All offline.

## Frame schema (existing, authoritative)
`{tool: snake_case, description, persona_gated?, default_speed, frames:[{legs:4×[x,y,z] ints, speed:0-90, hold_s≥0}]}`
Leg order `[FR,FL,RL,RR]`. Coords/IK + reachability from `scripts/kinematics_ref.py`. `STAND =
[[45,45,-50],[45,0,-50],[45,0,-50],[45,45,-50]]`.

## Component 1 — `scripts/validate_animation.py` (+ tests)
Pure `validate(anim:dict) -> list[Issue]` + CLI.
- ERROR (blocks install): schema (above); reachability — every leg of every frame passes
  `is_reachable`; report names `frame[i].leg[NAME]` + which bound it busts.
- WARN (allowed, surfaced): last frame ≠ STAND (tool end-on-stand invariant); first ≠ STAND; a
  single step moving any foot > ~60mm (smoothness hint).
- CLI: `python -m scripts.validate_animation <file.json>` prints report. `--install` → on zero
  ERRORs write `assets/Animations/<tool>.json` (reuse snake_case + path-safety like
  `animation_studio.py` POST /animations); refuse on any ERROR.

## Component 2 — `scripts/render_animation.py`
Frames JSON → contact-sheet PNG. Uses `kinematics_ref` joints (hip/knee/foot per leg) + matplotlib.
- Per frame: top view (body x/y) + side view (height z). Frames laid in a grid; frame index +
  hold_s labelled. Out: `<file>.preview.png` (written to disk, not inlined).
- `--stability`: overlay support polygon (convex hull of feet at z≈min, the planted set) + body-center
  CoM dot; red flag when CoM projection exits the hull. Geometry only, no physics engine.
- CLI: `python -m scripts.render_animation <file.json> [--stability] [--out PATH]`.
- matplotlib added to `.venv` (dev dep).

## Component 3 — `.claude/skills/chotu-animate/SKILL.md`
Reusable skill encoding: coord frame + leg order + sign conventions; reachable safe-box of x/y/z;
STAND + meaning of raising z / shifting x,y; lift→shift→plant gait pattern + support-polygon
intuition (keep ≥3 feet planted); studio tweens keyframes (eased lerp) so author KEYFRAMES not ticks;
worked refs to builtins (`forward.json` = canonical gait); end-on-stand invariant; and the WORKFLOW
(author → render [--stability] → inspect PNG → fix → validate --install). Covers gaits / gestures /
idle loops.

## Testing (offline)
`tests/test_validate_animation.py`: a builtin passes clean; a hand-broken out-of-envelope foot →
exact reachability ERROR; non-stand-ending anim → WARN; schema violations caught. `render_animation`
smoke test: a builtin produces a non-empty PNG without error.

## Deferred
In-studio NL input bar + model call (local/cloud, approval-gated) — later phase, reuses `validate()`.
Full physics/dynamics sim — out of scope (Chotu is quasi-static; static support-polygon suffices).

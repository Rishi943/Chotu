---
name: chotu-animate
description: Author a Chotu (PiCrawler quadruped) animation — a gait, gesture, or idle motion — as frames JSON for the animation studio. Use when the user asks to "make/design a chotu animation/gait/gesture/idle motion", "create a crab walk / wave / stretch", or hands a motion idea to turn into frames. Produces a validated, reachable JSON installed to assets/Animations and viewable in the studio. Laptop-only, no hardware.
---

# Authoring Chotu animations

You write the frames JSON yourself, iterate visually, validate, and install. No model API call.

## Frame schema
```
{tool: snake_case, description, persona_gated?: bool, default_speed: int, frames: [
  {legs: [[x,y,z],[x,y,z],[x,y,z],[x,y,z]], speed: 0-90, hold_s: >=0}
]}
```
Leg order is **[FR, FL, RL, RR]** (front-right, front-left, rear-left, rear-right). Coords are
**leg-local mm ints**. `STAND = [[45,45,-50],[45,0,-50],[45,0,-50],[45,45,-50]]`.

## Coordinate intuition (local frame, per leg)
- **z** = height of the foot. More negative = body higher / leg more extended down. **Raising z toward
  0 lifts the foot off the ground.** Stand z = -50; a lifted foot ≈ -30.
- **x** = reach away from the hip (forward/out). Stand x = 45; stepping forward raises x (e.g. 70).
- **y** = lateral splay. Right legs (FR,RR) stand at y=45, left (FL,RL) at y=0 — the stand is NOT
  left/right symmetric in local coords (mount convention). Don't assume mirrored legs share coords.
- Safe starting band (always re-validate): x∈[40,75], y∈[0,60], z∈[-76,-20]. Reachability is enforced
  by `kinematics_ref.is_reachable` (bounds: L∈[33,159], u∈[30,91.58], α∈[-10,90], β∈[-90,90], γ∈[-60,60]).

## Motion patterns
- **The studio tweens between keyframes** (eased lerp on preview/play). Author KEYFRAMES, not every
  tick — a handful of poses per cycle is enough; the studio fills the in-betweens.
- **Gaits (crab/forward/turn):** repeat a **lift → shift → plant** cycle per leg. Move one leg at a
  time; keep **≥3 feet planted** (z≈stand) so the support polygon holds and the body won't tip. E.g.
  sideways crab step: lift FR (z→-30), shift it laterally (change x), plant (z→-50), then the
  diagonal leg, etc. `assets/Animations/builtin/forward.json` is the canonical worked gait — read it.
- **Axis mapping (verified):** local **x = world-lateral** (sideways), **y = world fore-aft**,
  **z = height**. So a sideways crab modulates **x** (right legs FR/RR step world-right by *decreasing*
  x; left legs FL/RL by *increasing* x). Forward/back gaits modulate y. Probe with
  `scripts.render_animation.joints(i,legs)` if unsure.
- **Static-stability findings (learned the hard way — don't repeat):**
  - The default `STAND` is fore-aft asymmetric (right legs y=45), so lifting the world-left feet
    (robot's **FR/RR**) from stand already tips — `support_ok` False. Stand only safely single-lifts
    FL/RL.
  - A **perfectly symmetric stance has ZERO static margin**: lifting any one leg puts the CoM exactly
    on the support-triangle diagonal (tipping line). Symmetry alone does NOT make a stable gait.
  - **Statically-stable crawl ⇒ lean every step:** before lifting leg i, shift ALL four feet a few mm
    (a world lean) to move the CoM into the triangle of the other three, then lift/step/plant/un-lean.
    Brute-force the lean against `support_ok` as the oracle (see how `crab_right_static` was built).
  - **Dynamic trot** (move diagonal pairs together, big fast steps) is faster but only 2 feet are
    down — it CANNOT pass the static overlay (swing frames flag "tip!"); that's expected, confirm on
    hardware. `crab_right_trot` is the worked example.
  - The previewer keeps the body fixed at origin (no body translation) — true sideways travel only
    shows on the real robot. CoM check is therefore static/pessimistic for dynamic gaits.
- **Gestures (wave/nod/stretch):** mostly one limb moving from stand and back; the rest hold stand.
  Few keyframes, larger per-step deltas are fine.
- **Idle/ambient (sway/breathe/look):** small periodic z/x offsets on all legs; subtle, loopable.
- **End-on-stand invariant:** the LAST frame should be `STAND` (so it composes as a brain tool and the
  on-Pi player adds no settle artifact). The validator WARNs if not.

## Workflow (each iteration)
1. Write the JSON to a temp path (e.g. `/tmp/<tool>.json`).
2. **Render and look:** `python -m scripts.render_animation /tmp/<tool>.json --stability` → Read the
   `.preview.png`. Check: do the intended legs lift/shift/plant? Does the CoM dot stay inside the
   support polygon (green, not red "tip!")? Does the cycle read as the requested motion?
3. Fix the JSON and re-render until it reads right.
4. **Validate + install:** `python -m scripts.validate_animation /tmp/<tool>.json --install`. ERRORs
   (unreachable/schema) block install — fix them. WARNs are advisory.
5. Tell the user to refresh the studio library (`python -m scripts.animation_studio`, :8899) to view.
   Hardware test is deferred until they have the robot.

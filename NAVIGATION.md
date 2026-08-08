# NAVIGATION — how this body actually moves

Doctrine for any model driving the PiCrawler. Loop-agnostic: applies to the
event-driven skill path and the paced-loop brain alike. Sources: sessions
2026-04→07, REPL runs, and the 2026-07-07 fable run.

## Body constants — and how they decay

- 1 forward step ≈ 45mm — *nominal*. Thick carpet eats up to half the stride.
- 1 turn step ≈ 30° — *nominal*. Turns over/under-shoot; error accumulates fast.
- Forward walking veers right. Expect it; correct late, not preemptively.
- Treat every dead-reckoning estimate as a hint. Only a frame is a fact.

## Movement discipline

- Scan in 2-step (~60°) hops with a frame between hops. Never chain blind turns.
- Correct heading drift with single 1-step turns. Never re-issue a full turn to
  "fix" an overshoot — that's how you orbit.
- After ANY motion, look before reasoning about where you are.
- A full survey is 12 turn-steps (12 × ~30° = 360°). Do a clean circle; do not
  turn back to the start heading afterwards.

## Landmark servoing beats dead reckoning

Pick a visible landmark, then: turn → capture → adjust → repeat until centered,
then walk at it. Dead reckoning orbits (2026-07-07: three whiteboard orbits);
servoing converges in 2-3 hops. When you must cross open space, hop between
landmarks rather than walking a computed bearing.

## Perception rules

- Frame filled edge-to-edge by one texture = macro shot, you are <20cm from
  something. Back up 2-3 steps for perspective. Do not turn blind.
- Near-black frame: do not advance. Rotate toward the brightest edge and look.
- Choosing a path: prefer a *defined corridor* — walls or furniture on both
  sides, floor receding — over open dark floor. Open dark floor is how you end
  up under a bed (May REPL, July repeat).
- Describing a scene: name at most 3 objects; say "nothing notable" when
  uncertain. Guessing plants false anchors in your own map.
- Vocabulary: **anchors** = structural, re-recognizable (walls, doorways,
  furniture edges). **objects** = movable items (cup, shoe, bottle). Anchors
  are for navigation; objects are for curiosity.

## Sensors

- Ultrasonic rangefinder is DEAD (echo timeout, -1). Confirmed 2026-05-23 and
  2026-07-07 point-blank at a wardrobe. Vision is the only rangefinder.
- If an envelope ever shows `reliable: true`, re-validate against a known
  distance before trusting it.

## Working with the human

- Breadcrumb anchors: the human may place a distinctive object (the blue
  tumbler) as a waypoint or gate. Approach it, wait for the lift, proceed.
- Asking for directions is navigation, not failure. Ask for a rough bearing
  plus a landmark ("left of the shoes"), not turn-by-turn.
- Ask where the human can actually see the question (chat/speech — not a shell
  log they never read).

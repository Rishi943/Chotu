# Studio CAD QoL Features — Design

**Date:** 2026-06-18
**Scope:** `scripts/studio.html` only (the Chotu Animation Studio single-page UI). No `core/` or
`pi_bridge/` changes. `scripts/animation_studio.py` unchanged.

## Goal

Add CAD-style quality-of-life aids to the pose/animation editor so building poses is faster and
more precise. Four features, organized around one unifying concept (a shared snapping system) plus
standalone aids.

## Out of scope (deferred to their own specs)

- **Body-rise / support-polygon IK** — pushing one foot down should raise the body (other feet hold
  the floor), but the grid currently snaps to the lowest foot and drags down. Correct fix needs
  ground-contact IK. Explicitly deferred.
- **Axis-lock for free-drag** — redundant with the existing single-axis TransformControls gizmo. Cut.

## Features

### 1. Shared-step snapping system

A single **step value** (numeric input, default `5` mm; `Shift` held = ×4 coarse step) drives both
grid-snap and keyboard nudge. A master **Snap** toggle (on by default) enables snapping during
drag/nudge/numeric edits. Three snap targets, each independently toggleable:

**a) Grid snap.** Round the edited leg's local `[x,y,z]` to the nearest step increment. Applied on
free-drag, gizmo-drag, keyboard nudge, and numeric input commit.

**b) Symmetry snap (the "CAD symmetry").** When the edited foot's *world* position is within a
snap tolerance of its partner leg's mirror, snap it exactly onto that mirror. Pairings:
- **Left↔Right** (reflect world foot across the fore-aft centerline, negate world X): FR↔FL, RR↔RL.
- **Front↔Back** (reflect across the lateral centerline, negate world Z): FR↔RR, FL↔RL.

A symmetry-mode selector picks which axis is active (L/R, F/B, or off). **Critically, symmetry is
computed in world space, not by copying local coords** — the `STAND` pose proves left/right partners
use *different* local values (right legs `y=45`, left `y=0`) due to the yaw mount convention.

Exact solve (derived from `render3D`'s `sgn` mount transform; the forward map is
`worldFoot = base + dir·footR` with `dir ∝ (sgn[0]·x, sgn[1]·y)`, height `= footZ`, and the inverse
of `legPlane` gives `w = footR`, `z_local = footZ`):

```
Fp   = forwardFoot(partner)                         // partner world foot
F    = reflect(Fp)                                  // negate X (L/R) or Z (F/B) about body origin
x_E  = sgn_E[0] * (F.x - base_E.x)                  // edited leg's new local coords
y_E  = sgn_E[1] * (F.z - base_E.z)
z_E  = F.y
```

`base_E` = `CORNERS3D[E].p`, `sgn_E` = `CORNERS3D[E].sgn`. This is exact for both axes. A one-time
hardware sanity-check (send a symmetry-snapped stance to the Pi, confirm legs are visually mirrored)
is the only verification needed; the math is closed-form.

While dragging near a symmetry match, render a faint **ghost of the partner foot + a snap line** so
the user sees the magnet engage.

**c) Ground snap.** Snap the edited foot's local `z` so its *world height* lands on the current
ground plane (the existing `minFootY` grid level). Sets a foot down without moving the body — does
NOT touch the deferred body-rise problem. Surfaces as a "drop to ground" snap target during drag and
as a button on the leg editor.

Reachability check (`isReachable`) still runs after any snap; a snapped-but-clamping pose shows the
existing red ⚠ warning rather than being silently rejected.

### 2. Keyboard nudge

With a leg selected, arrow keys move the foot by the shared step in the current view's plane;
`PageUp`/`PageDown` (or a modifier) move along the third axis. `Shift` = coarse step. Nudges respect
grid snap (land on increments) and push to the undo stack. Disabled while an input field is focused.

### 3. Undo/redo (pose edits only)

A bounded history stack (e.g. 50 entries) of leg-coordinate states for the **current selected
frame**. `Ctrl+Z` / `Ctrl+Shift+Z` (and `Ctrl+Y`). Each committed pose mutation (drag end, nudge,
numeric commit, snap, ground-drop, symmetry-snap, reset-to-stand) pushes one entry. **Timeline
structure ops (add/dup/delete/reorder frame) are NOT undoable** — out of scope, confirmed. Switching
frames clears/segments the stack so undo never crosses frame boundaries.

### 4. Onion-skinning

Ghost the previous and/or next timeline frame behind the current pose in the 3D viewport (and
optionally the 2D thumb), as semi-transparent leg geometry. A toggle (off by default) with a
prev/next/both selector. Reuses the existing `legGroups` rendering with a translucent material and
the neighbor frames' coords. Pure visual aid; no effect on export or playback.

## UI placement

All controls live in the existing left/leg-editor and viewport-overlay panels — no new layout
regions, preserving the no-page-scroll constraint:
- **Snap controls** (step input, master toggle, target toggles, symmetry-axis selector): a compact
  row under the leg editor.
- **Ground-drop button**: in the leg editor next to the per-leg coords.
- **Onion toggle**: in the viewport overlay near the existing gizmo/view chips.
- **Undo/redo**: keyboard-first; optional small ↶/↷ chips in the toolbar.

## Architecture notes

- `studio.html` is an ES module — every new `onclick`/`oninput` handler must be exposed on `window`
  (existing gotcha).
- Keep `legPlane`/`coord2polar`/`isReachable` untouched; the inverse-IK for symmetry snap is a new
  small helper (`worldFootToLocal`) that does not duplicate them — it inverts the documented
  `render3D` forward map.
- New state on the single `state` object: `{ snap:{on,step,grid,symAxis,ground}, onion:{mode},
  history:{stack,index} }`. No persistence (no SQLite per project rules).
- Snapping/symmetry are pure functions of `state.legs[i]` + `CORNERS3D` → unit-testable in isolation
  if a JS test harness is wanted; at minimum, add a Python mirror of the symmetry solve to
  `tests/` alongside `test_kinematics_ref.py` to lock the math.

## Testing / success criteria

1. Grid snap: dragging/nudging a foot lands coords on step multiples.
2. Symmetry snap (L/R and F/B): snapping FL onto FR's mirror, sent to the Pi, produces a visually
   mirrored stance (hardware check). Unit test: `worldFootToLocal(reflect(forwardFoot(partner)))`
   round-trips to the expected mirror for a sample pose.
3. Ground snap: foot world-height lands on `minFootY`; body does not move.
4. Keyboard nudge: arrow keys move the selected foot by the step; blocked when typing in a field.
5. Undo/redo: pose mutations reverse/replay; frame add/delete are unaffected; undo never crosses
   frame boundaries.
6. Onion-skin: prev/next ghosts appear when toggled, vanish when off, do not affect export JSON.

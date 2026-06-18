# Animation Studio — redesign integration

**Date:** 2026-06-18
**Status:** Design — awaiting review

## Goal

Reskin the working Chotu Animation Studio (`scripts/studio.html`) to match the
new visual design (`assets/Chotu Animation Studio redesign/`) **without losing any
working functionality**, and add two CAD-style viewport capabilities the user asked
for. The whole UI must fit one 1440p / 16:9 screen at 100% browser zoom — no page
scroll.

## What stays as-is (the engine)

`scripts/studio.html` remains the single shipped file, served by
`scripts/animation_studio.py` (the FastAPI launcher + Pi proxy). We keep its
`<script type="module">` logic essentially intact:

- Real PiCrawler kinematics (`coord2polar` / `isReachable` / `legPlane`,
  mirror of `scripts/kinematics_ref.py`).
- The existing **three.js claw-skeleton model** — geometry unchanged (it matches the
  real PiCrawler best). Only colors and servo-block sizing change (see Restyle).
- Real Pi proxy calls (`/set_legs`, `/health`), export + validation, and the
  `add-chotu-tool` frames-JSON contract (`{tool, description, persona_gated,
  default_speed, frames:[{legs, speed, hold_s}]}`).
- Frames/timeline model and playback (`playPreview`, `playOnRobot`).

## What is reference only (NOT shipped)

`Studio Editor.dc.html` + `support.js` are a **visual mockup in Claude's DC/React
preview runtime**. They are not servable from our Python and their internals are
discarded:

- Their "3D" is flat SVG faux-3D with 4 fixed angles — cannot truly orbit/pan.
- Their IK is a screen-space approximation, not the real `coord2polar`.
- Pi connection, export, tool-name/description, and the persona toggle are dead
  (hard-coded / no handlers).

We take from them only the **visual language**: layout, palette, typography, spacing.

## Layout (adopted from the redesign)

Single `100vh` flex column, `overflow:hidden`, **no `zoom`**:

```
┌──────────────────────── top toolbar (~54px, fixed) ───────────────────────┐
│ logo · filename·dirty │ Pi pill │ Reset to stand │ Send to robot          │
├──────────┬───────────────────────────────────────────┬────────────────────┤
│ LEFT RAIL│            CENTER (flex:1)                 │   RIGHT RAIL       │
│ (330px)  │   three.js viewport — fills remaining      │   (316px)          │
│          │   space (real orbit/pan/zoom, foot-drag)   │  top-down foot map │
│ Leg      │   view-preset buttons + Gizmo toggle       │  side linkage      │
│ Editor   │   (bottom-center overlay)                  │  export panel      │
│ ×4 cards │                                            │                    │
│          │                                            │                    │
│ Servo    │                                            │                    │
│ Speed    │                                            │                    │
│ (under   │                                            │                    │
│  cards)  │                                            │                    │
├──────────┴───────────────────────────────────────────┴────────────────────┤
│                    TIMELINE (~150px, fixed)                                │
│  frame thumbnails · add · Play preview · Play on robot                     │
└────────────────────────────────────────────────────────────────────────────┘
```

### Fit-to-viewport (hard requirement)

The redesign currently requires scrolling to see the timeline (and servo speed) at
both 100% and 150% browser zoom — root cause: `zoom:1.25` plus a fixed-size
viewport. Fixes:

- Root container `height:100vh; overflow:hidden`; remove `zoom`.
- Toolbar and timeline are fixed-height flex rows; the center column is `flex:1` and
  fills the remaining height.
- The three.js renderer must size to its container, not a hard-coded `360×300`.
  Add a `ResizeObserver` on the viewport host that updates
  `renderer.setSize(...)` and `camera.aspect` + `camera.updateProjectionMatrix()`.
- **Servo-speed meter sits directly under the leg cards** — remove the flex spacer
  that pushed it to the bottom of the left rail.
- Right rail fits within `100vh`; only as a last-resort fallback does the rail get an
  internal `overflow-y:auto`, never the page.

## Viewport interaction (the two new capabilities)

### CAD navigation
- `OrbitControls`: enable **pan** (right-drag / two-finger) in addition to the
  existing orbit + zoom; verify full orbit so every angle is inspectable.
- The 4 view buttons (PERSP / TOP / SIDE / FRONT) become **camera-preset snaps**
  that move the real camera to a fixed pose + target.

### Free foot-drag (gizmo OFF)
- With the gizmo toggled OFF, dragging any foot moves the IK end-effector on a
  **camera-facing plane** (a plane through the current foot, normal = camera view
  direction). Raycast the pointer onto that plane → new world target → convert
  world→leg-local (reuse the existing `gizmoTarget`/`CORNERS3D` math) → set
  `state.legs[i]` → `renderAll()`. The leg tracks like a linkage; unreachable poses
  clamp + turn red exactly as today.
- With the gizmo ON, keep the precise single-axis `TransformControls` (current
  behavior).
- A "⊹ Gizmo" toggle in the bottom-center overlay switches modes; OrbitControls is
  disabled only while a drag (gizmo or free) is in progress.

## Model restyle (geometry unchanged)

- Recolor to the redesign palette: blue accent `#37b6ff`, active-leg highlight,
  steel-grey bones, dark board, red for unreachable. Background/grid to match the
  redesign's `#0e1014` / radial viewport.
- Resize the servo blocks so they read closer to the real servos (current
  `BoxGeometry(13,12,22)` markers look oversized vs. the reference photo).

## Wiring the redesign's dead controls

- **Reset to stand** → existing `resetToStand()`, bound to the new button.
- **Persona toggle** → existing `persona_gated` export field, restyled as the
  pill toggle; feeds the exported frames JSON unchanged.
- **Tool name / description / default-speed** → restored as real `<input>`s
  (mockup had static text). Live `renderExportStatus()` validation as today.
- **Pi pill** → real `/health` poll (shows connected/unreachable, gates
  `.needs-pi` buttons). **Send / Play-on-robot** → real `/set_legs`.
- **Export / Copy / Load** → existing `exportJSON` / `copyJSON` / `loadJSON`.
- **IK/FK toggle** (present in the mockup): out of scope — the working studio is
  IK-only and the `add-chotu-tool` contract is IK [x,y,z]. Either omit the FK toggle
  or leave it visually present but disabled. Default: omit to avoid implying an
  unsupported mode.

## Testing / success criteria

Manual verification (this is a single browser tool, no automated suite):

1. At 100% and 150% browser zoom on 1440p/16:9, the toolbar, both rails, the full
   3D viewport, **and** the timeline are all visible with **no page scroll**; servo
   speed sits under the leg cards.
2. Orbit, zoom, and pan all work in the viewport; the 4 view buttons snap the
   camera.
3. With gizmo OFF, dragging a foot moves it freely (camera-facing plane) and the leg
   follows; unreachable poses clamp red. With gizmo ON, single-axis drag still works.
4. Reset to stand restores the stand pose. Persona toggle flips `persona_gated` in
   exported JSON. Tool name/description/default-speed are editable and validate.
5. Send to robot / Play on robot hit the Pi (when connected); Export/Copy/Load
   round-trip a frames JSON unchanged from today's behavior.
6. Resizing the browser window keeps the 3D canvas correctly sized (ResizeObserver).

## Out of scope

- FK editing mode (IK-only contract).
- Changing the `add-chotu-tool` JSON schema or `animation_studio.py` proxy.
- Replacing the three.js model geometry.

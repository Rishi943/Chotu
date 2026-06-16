# Chotu Animation Studio — Design

**Date:** 2026-06-16
**Status:** Approved (pending spec review)

## Goal

A single-page browser tool to design PiCrawler poses and multi-frame animations by
editing each leg's `[x, y, z]`, previewing them faithfully (2D top-down + side, plus a
live 3D model), verifying them on the real robot via the Pi bridge, and exporting the
**frames JSON** that the `add-chotu-tool` skill ingests.

It replaces hand-writing frame coordinates: you sculpt a pose, see it rendered from the
real kinematics, send it to the hardware to confirm, snapshot it into a timeline, and
export a contract-shaped JSON.

## Non-goals (YAGNI)

- WASD / real-time teleop driving (explicitly cut).
- Continuous live-robot manipulation (drag a slider → robot follows in real time). Cut for
  hardware reasons — see "Why not live-robot manipulation" below.
- Persistence / database / auth / multi-user.
- Click-drag-the-foot IK manipulation in 3D — deferred to a documented fast-follow (Tier 2),
  not in v1.

## Architecture

Two new files under `scripts/`, purely additive. No changes to `core/` or the Pi bridge.

```
scripts/animation_studio.py   — launcher + proxy (FastAPI + uvicorn, port 8899)
  ├ GET  /            → serves studio.html
  ├ POST /set_legs    → forwards to Pi  {legs, speed}   (send current pose)
  ├ POST /pose        → forwards to Pi  {name, speed}   (stand/sit reset)
  └ GET  /health      → forwards to Pi                  (connection indicator)
scripts/studio.html           — the entire UI (self-contained single file, like
                                 scripts/chotu_faces.html); three.js via CDN <script>.
```

- **Why a proxy:** the browser cannot POST cross-origin to the Pi bridge (no CORS headers),
  and we will not add CORS to the sudo Pi bridge for a dev tool. The proxy is the thin
  same-origin shim the browser talks to.
- The proxy reads `PI_HOST` from `.env` (same `chotu.local:7000` the brain uses) and does
  **not** import `core.brain` — it is independent of the LLM runtime.
- Run: `python -m scripts.animation_studio`, then open `http://localhost:8899`.

### Why not live-robot manipulation

The Pi bridge enforces `MOTION_COOLDOWN_S = 0.6` between every motion call plus a serializing
motion lock, specifically to prevent servo-current brownouts on the 2S LiPo rail. Streaming
high-frequency `/set_legs` from a slider drag would queue behind the cooldown (seconds of lag,
jerking through stale targets) and risk brownout. Making it smooth would require removing that
safety. So the robot is a **request-driven confirm step** (Send / Play buttons), never a
live-drag target.

## Kinematics (ported from `picrawler` 2.1.4, read off the Pi)

The studio renders previews and validates reachability using the *real* library math, ported
to JS. Constants (mm):

- `A = 48` (upper leg), `B = 78` (lower leg), `C = 33` (hip horizontal offset)
- `LENGTH_SIDE = 77` (body side)

`[x, y, z]` is the foot position in each leg's local frame: `x`/`y` horizontal, `z` vertical
(negative = planted below body, positive = lifted). Stand ≈ `[60, 0, -30]`. Leg order is
`[FR, FL, RL, RR]` (front-right, front-left, rear-left, rear-right).

**`coord2polar(x,y,z)`** (port verbatim): computes `L = √(x²+y²+z²)`, clamps `L∈[C, A+B+C] =
[33, 159]`, `w = √(x²+y²)`, `v = w-C`, `u = √(z²+v²)` clamped to `[30, 91.58]`, then knee
angle `β`, lift `α`, and yaw `γ = atan2(y,x)`. Joint-angle limits: `α∈[-90,90]`,
`β∈[-10,90]`, `γ∈[-60,60]`.

These give us, with no guessing:
1. **Top-down (x,y):** plot each foot at its body corner — true stance/reach/asymmetry.
2. **Side linkage:** draw hip → `C` → `A` → knee → `B` → foot from the computed angles.
3. **3D:** position the four articulated legs in a three.js scene from the same angles.
4. **Reachability check:** run the real clamp logic; if a coordinate would be clamped
   (`L` out of range, or a joint angle hits a limit), flag it.

## UI (single screen, Layout A)

```
┌ title ───────────────────────── ● Pi connected · speed [====○==] 60 ┐
├ LEG EDITOR ──────┬ PREVIEW ──────────────────────┬ EXPORT → JSON ───┤
│ FR x/y/z  ● ok   │  [top-down]  [side]           │ tool (snake✓)    │
│ FL x/y/z  ● edit │  [   live 3D model (orbit)  ] │ description      │
│ RL x/y/z  ● ok   │                               │ ☐ persona_gated  │
│ RR x/y/z  ● clamp│                               │ default_speed    │
│ [Send][Reset]    │                               │ [Export][Copy]   │
│                  │                               │ [Load JSON]      │
├ TIMELINE ────────────────────────────────────────────────────────── ┤
│ [F1]→[F2◀]→[F3]  +Add ⧉Dup 🗑Del ◀▶reorder   ▶▶Play robot ▷Play prev │
└──────────────────────────────────────────────────────────────────────┘
```

### Leg editor (left)
- Four legs (FR, FL, RL, RR); each axis is a slider **and** a typed number box.
- Generous slider ranges (the reachability check is the real guardrail, not the range):
  x `0–100`, y `0–130`, z `-100–60`.
- Per-leg **reachability dot**: green = reachable; red = "will clamp", showing the value the
  hardware would snap to. The leg being edited is highlighted.
- Global speed slider `0–90` (the bridge caps `/set_legs` at `MAX_MOTION_SPEED = 90`).
- **Send to robot** → POST `/set_legs` with current pose + speed.
- **Reset to stand** → set all legs to `[60, 0, -30]`.

### Preview (center)
- **2D top-down** and **2D side** canvases, recomputed on every edit (instant, offline).
- **Live 3D model (Tier 1):** three.js scene of the body + 4 articulated legs, updating from
  the numbers/sliders; orbit camera. No drag-to-pose in v1.
- The leg being edited and any out-of-range leg are visually distinguished in all views.

### Export panel (right)
- `tool` (live snake_case validation), `description`, `persona_gated` checkbox,
  `default_speed`.
- **Export JSON** (download), **Copy to clipboard**, **Load JSON** (re-open an exported file
  to keep editing).
- Live validity status: frame count + valid/invalid.

### Timeline (bottom)
- Ordered keyframes; each shows its `speed` and `hold_s`.
- **Add** captures the current 4-leg pose + current speed as a new frame (default `hold_s=0`).
- Per-frame edit of `speed` and `hold_s`; **Duplicate**, **Delete**, **reorder**, and
  click-to-load-frame-back-into-editor.
- **Play on robot** → step the sequence: for each frame POST `/set_legs {legs, speed}` then
  wait `hold_s`. Mirrors exactly the `_play_{tool}` body that `add-chotu-tool` generates, so
  what you design is what the tool will run.
- **Play preview** → same stepping, animating the diagrams/3D only (no hardware).

### Connection
- A pill polls `/health`. If the Pi is unreachable, editing / preview / export all keep
  working; only the robot buttons (Send, Play on robot, Reset) are disabled.

## Export = the contract

Emits exactly what `add-chotu-tool` ingests:

```json
{
  "tool": "peek_left",
  "description": "Lean and peek to the left.",
  "persona_gated": false,
  "default_speed": 60,
  "frames": [
    {"legs": [[x,y,z],[x,y,z],[x,y,z],[x,y,z]], "speed": 60, "hold_s": 0.0}
  ]
}
```

Validated before export is enabled: ≥1 frame; every frame exactly 4 legs × `[x,y,z]`;
`tool` is snake_case. (Tool-name collision against existing tools is checked later by
`add-chotu-tool`, not here.)

## Testing

- **Proxy (`tests/test_animation_studio.py`, off-Pi):** `/set_legs` and `/pose` forward the
  right body to the Pi; `/health` reflects reachability; graceful degradation (502 / error
  envelope) when the Pi is unreachable. Pi mocked via httpx mock / monkeypatch.
- **Kinematics port:** the JS `coord2polar` is ported alongside a Python mirror
  (`scripts/_kinematics_reference.py` or inline in the test) carrying the same constants and
  formulas. The test asserts the mirror matches hard-coded reference outputs for stand
  `[60,0,-30]`, wave `[0,120,60]`, and an out-of-range point (e.g. `[100,130,-60]` → clamped),
  documenting the exact numbers the JS must reproduce. (Pure Python; no node dependency.)
- **HTML/JS UI:** not unit-tested (consistent with `chotu_faces.html`); validated by use.
- **Ground truth:** coordinate/visual correctness is confirmed on the real robot. The
  diagrams and 3D are faithful guides, not a substitute for the hardware check.

## Deferred (Tier 2, fast-follow)

Click-drag the foot/linkage in the 3D view with IK (drag foot target → run `coord2polar` →
leg follows). Deferred because 3D mouse interaction has depth ambiguity and needs a drag-plane
or axis-gizmo solution — the riskiest, most time-consuming UX piece, separable from v1.

# `peek_over` — dramatic "almost steps off the edge" move

**Date:** 2026-06-16
**Status:** Approved design, pending implementation plan

## Problem

The reel (`SHOOT_BRIEF.md`, Act 3 "The Edge") needs a dramatic beat: Chotu starts
to step toward the table edge, **freezes with the stepping front leg lifted
mid-air** (not committing), then pulls back and looks up — right as Rishi says
"Chotu—". The existing `move` tool can't do this: a `forward` step plays 7 gait
sub-poses atomically and always commits the step; there's no way to stop mid-swing.

Goal: a new motion, `peek_over`, that reproduces a real forward step but holds at
the mid-step frame, pauses, leans back, and ends holding a look-up. Exposed as a
persona-gated brain tool (reel only) and manually triggerable for shoot timing.

## Gait findings (why the coordinates below are what they are)

From `picrawler` 2.1.4 (`/usr/local/lib/python3.13/dist-packages/...picrawler.py`):

- A `forward` step = 7 sub-poses. **Sub-pose 0** lifts the leading **front** leg
  (`Z_UP = -30`) and reaches it forward (`x = X_TURN = 70`) — the mid-step frame
  we freeze on. Sub-pose 1 swings it forward (`y = Y_DEFAULT*2 = 90`, still
  lifted). Sub-pose 2 plants it (commits).
- `do_action` toggles `crawler.stand_position` 0↔1 **every** step, and the
  `normal_action(0)` decorator swaps legs `1↔2` / `3↔4` when parity is 1. Net:
  - parity 0 → **front-left (leg 2)** leads
  - parity 1 → **front-right (leg 1)** leads
  - consecutive forwards alternate the leading front leg.

Gait constants: `X_DEFAULT=45, X_TURN=70, Y_DEFAULT=45, Y_START=0,
Z_DEFAULT=-50, Z_UP=-30`. Leg order in every pose is
`[L1=front-right, L2=front-left, L3=rear-left, L4=rear-right]`.

Because we want a **deterministic** leg (not whatever parity happens to be next),
`peek_over` does **not** call the gait generator. It plays explicit coordinates
derived from the gait, so the frozen pose is identical to a true mid-step but the
leg is chosen by us.

## Choreography

Given `lead` (`left`|`right`), `reach` (`shallow`|`deep`), `pause_s`, `speed`:

1. **Ensure standing** — `crawler.do_step("stand", 40)`.
2. **Freeze (reach + lift)** — `crawler.do_step(<freeze pose>, speed)`. The foot
   hangs lifted and forward. This is the held mid-step frame.
3. **Hold** — `time.sleep(pause_s)` (default 1.5s). Servos stay put.
4. **Lean back** — `crawler.do_step(<lean-back pose>, speed)`: retract the
   reaching foot and shift weight rearward (no full gait step). Starting candidate
   below; tuned live on the table.
5. **Look up (held)** — `crawler.do_action("look up", 1, speed)`. `look_up` is a
   single held pose (rear feet to `Z_UP`, body tilts up); it does not return to
   neutral, so Chotu **ends holding the look-up** — the dramatic button.
6. **Reset parity** — set `crawler.stand_position = 0` so later normal `move`
   calls start from a known gait phase (we bypassed the gait, so its parity state
   is otherwise stale).

### Exact poses

Stand: `[[45,45,-50],[45,0,-50],[45,0,-50],[45,45,-50]]`

**Freeze, `lead="left"`** (leg 2 leads, parity-0 frame):
- `shallow`: `[[45,45,-50],[70,0,-30],[45,0,-50],[45,45,-50]]`
- `deep`:    `[[45,45,-50],[45,90,-30],[45,0,-50],[45,45,-50]]`

**Freeze, `lead="right"`** (leg 1 leads) — the parity-1 transform `[s1,s0,s3,s2]`
applied to the left poses:
- `shallow`: `[[70,0,-30],[45,45,-50],[45,45,-50],[45,0,-50]]`
- `deep`:    `[[45,90,-30],[45,45,-50],[45,45,-50],[45,0,-50]]`

**Lean-back (starting candidate, tunable):** raise the two front feet to `Z_UP`
so the nose dips and weight shifts back:
`[[45,45,-30],[45,0,-30],[45,0,-50],[45,45,-50]]`
The `y=90` "deep" reach shifts the COM forward, so test it away from the edge
first; `shallow` keeps the COM over the planted tripod and is the safe default.

## Components

### 1. Pi bridge — `pi_bridge/server.py`
- A module-level pure helper `peek_over_poses(lead: str, reach: str) -> list`
  returning `[freeze_pose, lean_back_pose]` from the constants above (unit-testable,
  no hardware).
- A `peek_over(lead, reach, pause_s, speed)` function running the 6-step sequence
  inside `async with _motion_section():` on the motion executor (`run_in_executor`),
  exactly like the `pose` endpoint. `speed` clamped to `MAX_MOTION_SPEED`.
- A **`POST /peek_over`** endpoint with a `PeekOverRequest`
  (`lead: str`, `reach: str = "shallow"`, `pause_s: float = 1.5`, `speed: int = 60`).
  Returns the standard envelope `{"ok", "tool": "peek_over", ...}`. This endpoint
  is the **manual trigger** (curl on cue during the shoot).

### 2. Brain tool — persona-gated
- `core/pi_client.py`: `async def peek_over(self, lead, reach="shallow",
  pause_s=1.5, speed=60)` → `_post_slow("/peek_over", "peek_over", {...})`.
- `core/tools.py`: a `peek_over` tool schema (params: `lead` enum `left|right`
  required; `reach` enum `shallow|deep` default `shallow`; `pause_s` number
  default 1.5) and a dispatch entry. Both are added **only when
  `os.getenv("PALIV_PERSONA") == "reel"`** — the same flag the launcher TUI sets.
  In base persona the tool is absent from `TOOL_SCHEMAS`/dispatch, so everyday
  Chotu cannot call it.
- `core/motion_lock.py`: add `"peek_over"` to `MOTION_TOOLS` so it serializes with
  other motion on the brain side.
- `CHOTU_REEL.md`: one line telling the reel persona it has `peek_over(lead=…)`
  for the edge moment, and that it ends holding a look-up.

### 3. Manual trigger
Covered by `POST /peek_over` — fired by curl exactly like the `move` calls in this
session. No extra brain plumbing.

## Data flow

```
(reel run) LLM emits tool_call peek_over{lead:"right"}
   → brain dispatch → pi.peek_over() → POST /peek_over
(shoot)   curl POST /peek_over {lead, reach, pause_s}
   → bridge peek_over(): stand → freeze → hold → lean back → look up → reset parity
   → envelope back
```

## Error handling
- `peek_over()` wrapped like `pose`: exceptions return an error envelope (no crash).
- `_motion_section()` serializes against `move`/`pose`/`set_legs` and enforces
  `MOTION_COOLDOWN_S`.
- Brownout still shows as the sub-100 ms stale-ok signature; the multi-`do_step`
  sequence normally takes a few seconds.
- Invalid `lead`/`reach` → `ValueError` → error envelope.

## Testing
- **Unit (no hardware):** `peek_over_poses("left", …)` vs `("right", …)` — assert
  right is the exact `[s1,s0,s3,s2]` mirror of left, and values match the gait
  constants (front leg at `Z_UP`, reaching `x=70` shallow / `y=90` deep).
- **Brain-side:** with `PALIV_PERSONA=reel`, `peek_over` is in the tool schemas and
  dispatch; with persona unset it is absent. `"peek_over" in MOTION_TOOLS`.
- **Manual on table:** curl `/peek_over` with `lead=left` and `lead=right`; confirm
  the correct front leg lifts and reaches; tune `pause_s`, `reach`, and the
  lean-back pose live. Run `deep` away from the edge first (COM forward).

## YAGNI / out of scope
- No autonomous edge-detection or safety stop — the operator places Chotu and fires
  the move; the shoot is supervised.
- No new gait baked into the picrawler library; `peek_over` is bridge-side
  choreography only.
- `reach`/`pause_s`/lean-back exposed as params/constants for live tuning, not a
  config system.
- Not added to base persona — reel only.
```

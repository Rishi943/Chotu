# Fluid sequence player + timeline polish

**Date:** 2026-06-18
**Status:** Design — awaiting review

## Goal

Make studio-designed animations play on the robot as fluidly as the native `move`/`pose`
motions, by running a whole frame sequence in **one on-Pi loop** instead of streaming
`/set_legs` frame-by-frame from the laptop. Share that player between the studio preview
and `add-chotu-tool`'s generated tools so they can't drift. Plus three studio touches:
timeline drag-to-reorder, no vertical scrollbar, and a ground grid that snaps to the
planted feet.

## Why frame-streaming is choppy (context)

Today `studio.html` `playOnRobot()` POSTs `/set_legs` once **per frame** and awaits each.
Every frame is a separate HTTP round-trip, and each `do_step` ramps the servos to the
target and *settles to a full stop* before the next request arrives — so the motion is
move-stop-gap-move-stop. Native `move`/`pose` (and the generated `_play_{tool}`) avoid
this by running every step **back-to-back inside one on-Pi call** (no network gap between
steps). This spec brings that same single-call playback to arbitrary frame sequences.

**Fluidity level chosen:** parity with the generated tool — on-Pi back-to-back, each pose
ramping via its own `speed`. No keyframe interpolation (deferred; not needed to match
`move`/`pose`, which are themselves back-to-back). This guarantees studio preview == the
real scaffolded tool.

## Component 1 — Bridge: shared `_play_frames` + `/play_sequence`

`pi_bridge/server.py`.

The playback body, extracted so the endpoint and generated tools call one function:

```python
def _play_frames(frames, cap=MAX_MOTION_SPEED, speed_override=None):
    """Run a studio frame sequence in one on-Pi loop. Each frame: {legs, speed, hold_s}.
    Ends standing — the rest of the stack assumes start/end = stand."""
    for f in frames:
        spd = min(speed_override or f.get("speed", 60), cap)
        crawler.do_step(f["legs"], spd)
        if f.get("hold_s"):
            time.sleep(f["hold_s"])
    crawler.do_step("stand", 40)
```

Request model + endpoint, guarded by the existing motion lock:

```python
class PlaySequenceRequest(BaseModel):
    frames: list
    speed: int | None = None   # optional override applied to every frame

@app.post("/play_sequence")
async def play_sequence(req: PlaySequenceRequest):
    start = time.time()
    # validate: non-empty, each frame 4 x [x,y,z]
    if not req.frames or any(
        not isinstance(f.get("legs"), list) or len(f["legs"]) != 4
        or any(not isinstance(leg, list) or len(leg) != 3 for leg in f["legs"])
        for f in req.frames
    ):
        return _envelope("play_sequence", {"frames": len(req.frames or [])}, start,
                         "each frame needs 4 legs of [x,y,z]")
    try:
        async with _motion_section():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: _play_frames(req.frames, MAX_MOTION_SPEED, req.speed))
        return _envelope("play_sequence",
                         {"frames": len(req.frames), "halted_early": False}, start)
    except Exception as e:
        return _envelope("play_sequence",
                         {"frames": len(req.frames), "halted_early": True}, start, str(e))
```

`_motion_section()`, `_envelope()`, `MAX_MOTION_SPEED`, the `crawler` instance, `time`,
and `asyncio` already exist. `_play_frames` is defined near the other motion helpers
(before `_TRICKS`).

## Component 2 — `add-chotu-tool` shares the player

`~/.claude/skills/add-chotu-tool/SKILL.md`. The generated `_play_{tool}` becomes a thin
wrapper so it runs byte-identical playback to `/play_sequence`:

```python
# Studio-designed animation. Each frame: (legs 4x[x,y,z], speed, hold_s).
_{TOOL}_FRAMES = {frames}   # the Python literal of the "frames" list, as dicts

def _play_{tool}(speed: int | None) -> None:
    _play_frames(_{TOOL}_FRAMES, MAX_MOTION_SPEED, speed)
```

Skill edits:
- Replace the inline `for f in ...: crawler.do_step(...)` template with the wrapper above.
- Add a note: `_play_frames` is the shared bridge helper (added by the play-sequence
  work); it already ends on stand, so the template no longer appends its own
  `crawler.do_step("stand", 40)`.
- Keep everything else (the FastAPI `@app.post("/{tool}")` handler, PiClient method,
  tool schema/dispatch, motion lock, ETA, tests) unchanged — the frames contract
  `{tool, description, persona_gated, default_speed, frames:[{legs,speed,hold_s}]}` is
  untouched, so studio exports still feed the skill as-is.

## Component 3 — Studio wiring

`scripts/animation_studio.py` — proxy the new endpoint (longer timeout; sequences with
holds can run 10–30 s):

```python
@app.post("/play_sequence")
async def play_sequence(req: Request):
    body = await req.json()
    return await _forward("POST", "/play_sequence",
                          {"frames": body["frames"], "speed": body.get("speed")},
                          timeout=120.0)
```

`_forward` gains an optional `timeout` param (defaulting to the client's current value) so
this one call can wait longer than the 30 s default.

`scripts/studio.html` — `playOnRobot()` becomes a single call:

```js
async function playOnRobot(){
  try{
    const r=await fetch("/play_sequence",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({frames: state.frames})});
    const j=await r.json();
    if(!j.ok) alert("Pi error: "+(j.error||JSON.stringify(j)));
  }catch(e){ alert("Play failed: "+e); }
}
```

The in-browser **Play preview** (the eased lerp in 3D) is unchanged — it stays the
"idealized" smooth preview; the robot now plays as fluidly as the real tool.

## Component 4 — Timeline UX

`scripts/studio.html`, `renderTimeline` + frame cards.

**Drag-to-reorder:** each frame card gets `draggable="true"` and `dragstart`/`dragover`/
`drop` handlers. `dragstart` records the source index; `drop` on a target card splices
`state.frames` (move source → target position), keeps the moved frame selected
(`state.selectedFrame` follows it), and re-renders. A thin insertion indicator on
`dragover` is optional polish, not required.

**No vertical scrollbar:** the frame strip currently overflows the 150 px band. Fix by
sizing cards to fit: smaller thumbnail (height ~44 px) and tighter padding so a card's
content (label row + thumb + spd/hold row) fits within the band, and set the strip to
`overflow-y:hidden; overflow-x:auto` (horizontal scroll remains for many frames). Net:
the whole app fits one 1440p/16:9 screen with no vertical scroll anywhere.

## Component 5 — Ground grid snaps to planted feet

`scripts/studio.html`, `render3D`. The `GridHelper` currently sits at fixed `y = -95`.
Instead, after computing the four feet each frame, set the grid's `position.y` to the
**minimum foot y** across the legs. The lowest foot/feet then rest exactly on the grid
(on the ground); any higher foot floats above it, making airborne legs obvious. Recomputes
on every edit and during preview/playback. (Track the foot world `y` already computed in
the render loop; take the min and assign it to `grid.position.y`.)

## Error handling

- `/play_sequence` validation failure or Pi exception → error envelope (studio alerts; no
  crash), same pattern as `/set_legs`/`/move`.
- Studio `/play_sequence` proxy unreachable → alert; studio keeps working.
- Drag-drop with a single frame is a no-op; dropping a frame on itself is a no-op.

## Testing / success criteria

Bridge (pytest, no hardware — `_play_frames` is import-safe if `crawler`/`time.sleep`
are monkeypatched):
1. `_play_frames` calls `do_step` once per frame in order, applies the speed cap and the
   optional override, sleeps for non-zero `hold_s`, and ends with a `do_step("stand", 40)`.
2. `/play_sequence` rejects an empty list and a frame without 4×`[x,y,z]` (error envelope).

Manual (real Pi + studio):
3. Designing peek_over (or any custom JSON) and hitting **Play on robot** plays in one
   continuous motion — visibly smoother than the old per-frame streaming, matching the
   feel of `move`/`pose`.
4. A tool scaffolded from the same JSON via `add-chotu-tool` plays identically to the
   studio preview (same `_play_frames` path).
5. Timeline: drag a frame to a new position — order changes, the moved frame stays
   selected, thumbnails update. No vertical scrollbar on the timeline at any frame count;
   the app fits one screen.
6. The ground grid sits under the planted feet; lifting a leg (e.g. peek_over F3) shows
   that foot clearly above the grid.

## Out of scope

- Keyframe interpolation / blending between poses (deferred; parity playback chosen).
- Halting a sequence mid-play from the studio (the existing e-stop path still applies).
- Changing the frames-JSON contract or the rest of the `add-chotu-tool` scaffold.
- Per-frame speed editing UI changes beyond what already exists.

# Fluid Sequence Player + Timeline Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Play studio frame sequences on the robot in one fluid on-Pi loop (not frame-by-frame over HTTP), share that player with `add-chotu-tool`'s generated tools, and polish the timeline (drag-reorder, no vertical scroll) and the ground grid.

**Architecture:** Extract the playback loop into a pure, hardware-free `pi_bridge/sequence.py` (`play_frames(crawler, frames, cap, speed_override, sleep)`) so it is unit-testable on the laptop; `server.py` and generated `_play_{tool}` both call it. Add a `/play_sequence` bridge endpoint + studio proxy + single-call `playOnRobot`. Then three `studio.html` touches.

**Tech Stack:** Python 3.12, FastAPI, pytest (laptop) for the pure player; vanilla HTML/JS + three.js 0.160 for the studio.

## Global Constraints

- `pi_bridge/server.py` imports hardware libs (`cv2`, `robot_hat`, `picrawler`, `vilib`) and runs `crawler = Picrawler()` at import — it **cannot be imported on the laptop**. Bridge endpoint behaviour is verified **on the Pi**; only the pure `pi_bridge/sequence.py` is unit-tested on the laptop.
- The bridge runs on the Pi as a **script** (`python ~/chotu-bridge/server.py`), so server.py imports siblings bare (e.g. `from sequence import play_frames`), matching its existing `from chotu...`/`from picrawler...` style. New bridge files must be added to `pi_bridge/deploy.sh`.
- Speed cap for sequences = `MAX_MOTION_SPEED` (90), the same cap `/set_legs` uses — what you design is what plays.
- Frames contract is unchanged: `{tool, description, persona_gated, default_speed, frames:[{legs, speed, hold_s}]}`; `legs` = 4×`[x,y,z]`, leg order `[FR, FL, RL, RR]`.
- Every sequence ends standing (`crawler.do_step("stand", 40)`) — the rest of the stack assumes start/end = stand.
- Studio JS edits must pass `node --check` on the extracted module (brace/paren counting is insufficient — a broken string escape already shipped once).

---

## Task 1: Pure sequence player (`pi_bridge/sequence.py`)

The hardware-free playback loop, unit-tested on the laptop.

**Files:**
- Create: `pi_bridge/sequence.py`
- Create: `pi_bridge/__init__.py` (empty — lets pytest import `pi_bridge.sequence`; NOT shipped to the Pi)
- Test: `pi_bridge/test_sequence.py`

**Interfaces:**
- Produces: `play_frames(crawler, frames, cap=90, speed_override=None, sleep=time.sleep) -> None`. Calls `crawler.do_step(legs, spd)` once per frame in order with `spd = min(speed_override or frame.get("speed", 60), cap)`, calls `sleep(hold_s)` when `hold_s` is truthy, and finishes with `crawler.do_step("stand", 40)`.

- [ ] **Step 1: Write the failing test**

Create `pi_bridge/test_sequence.py`:

```python
from pi_bridge.sequence import play_frames


class FakeCrawler:
    def __init__(self):
        self.calls = []          # list of (legs, speed)

    def do_step(self, legs, speed):
        self.calls.append((legs, speed))


def _frames():
    return [
        {"legs": [[45, 45, -50]] * 4, "speed": 60, "hold_s": 0},
        {"legs": [[45, 0, -50]] * 4, "speed": 200, "hold_s": 0.3},  # over-cap on purpose
    ]


def test_plays_each_frame_in_order_then_stands():
    c = FakeCrawler()
    slept = []
    play_frames(c, _frames(), cap=90, sleep=slept.append)
    # one do_step per frame + a final stand
    assert len(c.calls) == 3
    assert c.calls[0] == ([[45, 45, -50]] * 4, 60)
    assert c.calls[1][1] == 90                       # 200 capped to 90
    assert c.calls[2] == ("stand", 40)               # ends standing
    assert slept == [0.3]                            # only the non-zero hold


def test_speed_override_applies_to_all_frames_capped():
    c = FakeCrawler()
    play_frames(c, _frames(), cap=90, speed_override=75, sleep=lambda s: None)
    assert c.calls[0][1] == 75 and c.calls[1][1] == 75
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/rishi/Rishi/AI/Paliv && python -m pytest pi_bridge/test_sequence.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'pi_bridge.sequence'`).

- [ ] **Step 3: Implement the pure module**

Create `pi_bridge/__init__.py` (empty file).

Create `pi_bridge/sequence.py`:

```python
"""Pure on-Pi frame-sequence player. No hardware imports so it is unit-testable.

`server.py` and add-chotu-tool's generated `_play_{tool}` both call play_frames, so the
studio preview and the scaffolded tool run identical playback. Each frame is
{legs: 4x[x,y,z], speed, hold_s}. Runs back-to-back in one loop (the caller already holds
the motion lock) — no network gap between frames, ending standing.
"""
import time


def play_frames(crawler, frames, cap=90, speed_override=None, sleep=time.sleep):
    for f in frames:
        spd = min(speed_override or f.get("speed", 60), cap)
        crawler.do_step(f["legs"], spd)
        if f.get("hold_s"):
            sleep(f["hold_s"])
    crawler.do_step("stand", 40)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd /home/rishi/Rishi/AI/Paliv && python -m pytest pi_bridge/test_sequence.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add pi_bridge/sequence.py pi_bridge/__init__.py pi_bridge/test_sequence.py
git commit -m "feat(bridge): pure on-Pi frame-sequence player (play_frames)"
```

---

## Task 2: Bridge endpoint + wiring + deploy

Wire `play_frames` into `server.py` and expose `/play_sequence`. Endpoint behaviour is verified on the Pi (server.py can't import on the laptop).

**Files:**
- Modify: `pi_bridge/server.py`
- Modify: `pi_bridge/deploy.sh`

**Interfaces:**
- Consumes: `play_frames` (Task 1), existing `crawler`, `_motion_section()`, `_envelope()`, `MAX_MOTION_SPEED`, `asyncio`, `time`.
- Produces: `_play_frames(frames, cap=MAX_MOTION_SPEED, speed_override=None)` (server-local wrapper used by generated tools) and `POST /play_sequence`.

- [ ] **Step 1: Import the player and add the server wrapper**

Near the top of `pi_bridge/server.py`, with the other sibling imports (e.g. after the `from picrawler import Picrawler` line), add:

```python
from sequence import play_frames
```

After `crawler = Picrawler()` (line 47), add the wrapper generated tools will call:

```python
def _play_frames(frames, cap=MAX_MOTION_SPEED, speed_override=None):
    """Server-side shortcut binding play_frames to the live crawler."""
    play_frames(crawler, frames, cap, speed_override)
```

(`MAX_MOTION_SPEED` is defined later at module load before any request runs; if Python
flags the forward reference at def-time it will not — the name is only resolved when
`_play_frames` is *called*, which is always post-startup.)

- [ ] **Step 2: Add the request model**

With the other `BaseModel` request classes (near `SetLegsRequest`), add:

```python
class PlaySequenceRequest(BaseModel):
    frames: list
    speed: int | None = None   # optional override applied to every frame
```

- [ ] **Step 3: Add the endpoint**

After the `/set_legs` endpoint, add:

```python
@app.post("/play_sequence")
async def play_sequence(req: PlaySequenceRequest):
    start = time.time()
    bad = (not req.frames) or any(
        not isinstance(f.get("legs"), list) or len(f["legs"]) != 4
        or any(not isinstance(leg, list) or len(leg) != 3 for leg in f["legs"])
        for f in req.frames
    )
    if bad:
        return _envelope("play_sequence", {"frames": len(req.frames or [])}, start,
                         "each frame needs 4 legs of [x,y,z]")
    logging.info(f"POST /play_sequence  frames={len(req.frames)} speed={req.speed}")
    try:
        async with _motion_section():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: _play_frames(req.frames, MAX_MOTION_SPEED, req.speed))
        return _envelope("play_sequence",
                         {"frames": len(req.frames), "halted_early": False}, start)
    except Exception as e:
        logging.error(f"  play_sequence error: {e}")
        return _envelope("play_sequence",
                         {"frames": len(req.frames), "halted_early": True}, start, str(e))
```

- [ ] **Step 4: Ship `sequence.py` in deploy.sh**

In `pi_bridge/deploy.sh`, after the `scp pi_bridge/server.py ...` line, add:

```bash
echo "==> Copying sequence.py to Pi..."
scp pi_bridge/sequence.py ${PI_USER}@${PI_HOST}:${REMOTE_DIR}/sequence.py
```

- [ ] **Step 5: Verify on the Pi**

Deploy and restart the bridge, then play a sequence through the proxy:

```bash
bash pi_bridge/deploy.sh
# restart bridge: ssh chotu@chotu.local 'sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py'  (in its own shell)
# then, with the studio proxy up (python -m scripts.animation_studio):
curl -s -X POST localhost:8899/play_sequence -H 'Content-Type: application/json' \
  -d "$(python3 -c 'import json;print(json.dumps({"frames":json.load(open("assets/Animations/peek_over.json"))["frames"]}))')"
```
Expected: `{"ok": true, ... "result": {"frames": 6, "halted_early": false}}` and the robot plays peek_over as **one continuous motion** (no per-frame stalls). Also confirm an empty `{"frames":[]}` returns `ok:false` with the validation error.

- [ ] **Step 6: Commit**

```bash
git add pi_bridge/server.py pi_bridge/deploy.sh
git commit -m "feat(bridge): /play_sequence endpoint over shared play_frames"
```

---

## Task 3: add-chotu-tool shares the player

Point the skill's generated tool at `_play_frames` so preview == saved tool == skill output.

**Files:**
- Modify: `~/.claude/skills/add-chotu-tool/SKILL.md`

- [ ] **Step 1: Replace the `_play_{tool}` template**

In `SKILL.md`, find the bridge-function template (the block defining `_{TOOL}_FRAMES` and `def _play_{tool}`). Replace the body with the shared-player wrapper:

````markdown
```python
# Studio-designed animation. Each frame: (legs 4x[x,y,z], speed, hold_s).
_{TOOL}_FRAMES = {frames}   # the Python literal of the "frames" list, as dicts


def _play_{tool}(speed: int | None) -> None:
    # Shared on-Pi player (see pi_bridge/sequence.py) — identical playback to the
    # studio's /play_sequence preview, so what you previewed is what runs.
    _play_frames(_{TOOL}_FRAMES, MAX_MOTION_SPEED, speed)
```
````

- [ ] **Step 2: Update the surrounding notes**

In the same section, replace any instruction that says the body loops `crawler.do_step`
over frames / appends its own `crawler.do_step("stand", 40)` with:

> `_play_{tool}` delegates to `_play_frames` (the shared player from `pi_bridge/sequence.py`,
> imported at the top of `server.py`). `_play_frames` already ends on stand, so the template
> must NOT append its own stand. Speed is capped at `MAX_MOTION_SPEED` inside the player.

Leave the rest of the scaffold (the `@app.post("/{tool}")` handler, PiClient method, tool
schema/dispatch, motion lock, ETA formula, brain tests) unchanged.

- [ ] **Step 3: Verify the doc edit**

Run: `grep -n "_play_frames\|do_step(\"stand\", 40)" ~/.claude/skills/add-chotu-tool/SKILL.md`
Expected: the `_play_{tool}` template calls `_play_frames`; no leftover per-frame
`do_step` loop or appended stand inside that template.

- [ ] **Step 4: Commit**

The skill lives outside the repo (`~/.claude/skills/…`), so there is nothing to commit in
this repo for this task. Note completion and move on. (If the skills dir is itself a git
repo, commit there: `git -C ~/.claude/skills commit -am "add-chotu-tool: share play_frames"`.)

---

## Task 4: Studio proxy for `/play_sequence`

**Files:**
- Modify: `scripts/animation_studio.py`

**Interfaces:**
- Produces: `POST /play_sequence` on the studio app, forwarding to the Pi with a long timeout.
- Modifies: `_forward(method, path, json=None, timeout=None)`.

- [ ] **Step 1: Add an optional timeout to `_forward`**

Replace the `_forward` definition:

```python
async def _forward(method: str, path: str, json: dict | None = None, timeout: float | None = None):
    try:
        r = await _client.request(method, f"{PI_HOST}{path}", json=json, timeout=timeout)
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"pi_unreachable: {e}"}, status_code=502
        )
```

(httpx accepts `timeout=None` meaning "use the client default", so existing callers are
unaffected.)

- [ ] **Step 2: Add the proxy route**

After the existing `/set_legs` route, add:

```python
@app.post("/play_sequence")
async def play_sequence(req: Request):
    body = await req.json()
    return await _forward(
        "POST", "/play_sequence",
        {"frames": body["frames"], "speed": body.get("speed")},
        timeout=120.0,   # sequences with holds can run far past the 30s default
    )
```

- [ ] **Step 3: Verify it forwards (no Pi needed for the unreachable path)**

Run: `cd /home/rishi/Rishi/AI/Paliv && PI_HOST=http://127.0.0.1:1 .venv/bin/python -c "
from starlette.testclient import TestClient
from scripts.animation_studio import app
r=TestClient(app).post('/play_sequence', json={'frames':[{'legs':[[45,45,-50]]*4,'speed':60,'hold_s':0}]})
print(r.status_code, r.json()['ok'])"`
Expected: `502 False` (route exists, forwards, Pi unreachable → clean 502 — proves wiring without hardware).

- [ ] **Step 4: Commit**

```bash
git add scripts/animation_studio.py
git commit -m "feat(studio): proxy /play_sequence to the Pi (long timeout)"
```

---

## Task 5: Studio "Play on robot" → single call

**Files:**
- Modify: `scripts/studio.html`

- [ ] **Step 1: Replace `playOnRobot`**

Replace the existing `playOnRobot` function:

```js
async function playOnRobot() {  // one on-Pi sequence — matches the scaffolded tool
  try {
    const r = await fetch("/play_sequence", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({frames: state.frames})});
    const j = await r.json();
    if (!j.ok) alert("Pi error: " + (j.error || JSON.stringify(j)));
  } catch (e) { alert("Play failed: " + e); }
}
```

- [ ] **Step 2: Syntax-check the module**

Run:
```bash
cd /home/rishi/Rishi/AI/Paliv && python3 -c "
import re;html=open('scripts/studio.html').read()
src=re.sub(r'^import .*$','',re.search(r'<script type=\"module\">(.*?)</script>',html,re.S).group(1),flags=re.M)
open('/tmp/m.mjs','w').write('const THREE={};const OrbitControls=class{};const TransformControls=class{};\n'+src)" && node --check /tmp/m.mjs && echo OK
```
Expected: `OK`.

- [ ] **Step 3: Verify in browser (with Pi up)**

`python -m scripts.animation_studio`, open the studio, load `peek_over` (or any animation),
click **▶▶ Play on robot**. Expected: the robot plays the whole sequence in one fluid motion
(noticeably smoother than before; matches the feel of `move`/`pose`). Errors surface as an alert.

- [ ] **Step 4: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): Play on robot uses single /play_sequence call"
```

---

## Task 6: Timeline drag-to-reorder + no vertical scroll

**Files:**
- Modify: `scripts/studio.html`

**Interfaces:**
- Produces: `reorderFrame(from, to)` (module fn, exposed on `window`); drag handlers on cards.

- [ ] **Step 1: Add the reorder function and expose it**

Near the other frame functions (e.g. after `dupFrame`), add:

```js
function reorderFrame(from, to){
  if(from===to || from<0 || to<0 || from>=state.frames.length || to>=state.frames.length) return;
  const moved=state.frames.splice(from,1)[0];
  state.frames.splice(to,0,moved);
  state.selectedFrame=to;            // keep the moved frame selected
  selectFrame(to);
}
window.reorderFrame = reorderFrame;
```

- [ ] **Step 2: Make cards draggable + shorter, strip scroll**

In `renderTimeline`, change the strip styling line to hide vertical overflow:

```js
  el.style.display="flex"; el.style.alignItems="stretch"; el.style.gap="9px";
  el.style.overflowY="hidden"; el.style.overflowX="auto";
```

Replace the card opening `<div>` (the `onclick="loadFrame(${i})"` element) with a draggable
variant carrying drag handlers, and shrink the thumbnail so the card fits the 150px band:

```js
    return `<div draggable="true"
      ondragstart="event.dataTransfer.setData('text/plain',${i})"
      ondragover="event.preventDefault()"
      ondrop="event.preventDefault();reorderFrame(+event.dataTransfer.getData('text/plain'),${i})"
      onclick="loadFrame(${i})" style="${card}">
      <div style="display:flex;align-items:center;gap:6px">
        <b style="font:700 12px 'JetBrains Mono';color:${sel?'#eaf4fc':'#cdd6e0'}">F${i+1}</b>
        <span style="flex:1"></span>
        <span style="font-size:9px;color:#37b6ff;font-family:'JetBrains Mono'">${sel?'● SEL':''}</span>
        <span onclick="event.stopPropagation();dupFrame(${i})" title="duplicate" style="cursor:pointer;color:#7d8694;font-size:12px">⧉</span>
        <span onclick="event.stopPropagation();delFrame(${i})" title="delete" style="cursor:pointer;width:16px;height:16px;border-radius:3px;background:#2a1a1a;border:1px solid #5a2b2b;color:#f2664f;font-size:12px;line-height:14px;text-align:center">×</span>
      </div>
      <canvas id="thumb${i}" width="120" height="42" style="width:120px;height:42px;border-radius:4px;background:${sel?'#08111a':'#0a0d12'}"></canvas>
      <div style="display:flex;align-items:center;gap:5px;font:500 10px 'JetBrains Mono';color:${sel?'#8fb9d8':'#7d8694'}">
        spd<input type="number" value="${f.speed}" onclick="event.stopPropagation()" oninput="setFrameSpeed(${i},this.value)" style="width:36px;padding:1px 4px">
        hold<input type="number" step="0.1" value="${f.hold_s}" onclick="event.stopPropagation()" oninput="setHold(${i},this.value)" style="width:36px;padding:1px 4px">
      </div>
    </div>`;
```

Also reduce the card padding from `padding:7px 9px` to `padding:6px 8px` in **both** the
selected and unselected `card` style strings, so the content clears the band.

- [ ] **Step 3: Syntax-check the module**

Run the same `node --check` command from Task 5 Step 2. Expected: `OK`.

- [ ] **Step 4: Verify in browser**

Reload the studio. Drag a frame card onto another position → order changes, the dragged
frame stays selected, thumbnails redraw. With 6+ frames there is **no vertical scrollbar** on
the timeline (horizontal scroll appears only when cards overflow width); the whole app fits
one 1440p/16:9 screen at 125%.

- [ ] **Step 5: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): timeline drag-to-reorder + fit band (no vertical scroll)"
```

---

## Task 7: Ground grid snaps to the lowest foot

**Files:**
- Modify: `scripts/studio.html`

- [ ] **Step 1: Make the grid a module-scope variable**

In the module-scope `let` declaration line (`let scene, camera, renderer, controls, legGroups, gizmoTarget, tcontrols;`), add `grid`:

```js
let scene, camera, renderer, controls, legGroups, gizmoTarget, tcontrols, grid;
```

In `init3D`, change the grid creation from `const grid = ...` to assign the module var:

```js
  grid = new THREE.GridHelper(400,16,0x2a3744,0x202833); grid.position.y=-95; scene.add(grid);
```

- [ ] **Step 2: Snap the grid to the lowest foot each frame**

In `render3D`, the per-leg loop computes `foot` (`const ... foot=P(pl.footR,pl.footZ);`).
Track the minimum foot `y` and, after the `state.legs.forEach(...)` loop, move the grid there.
Add a `let minFootY = Infinity;` just before the loop, inside the loop add
`minFootY = Math.min(minFootY, foot.y);`, and after the loop add:

```js
  if(grid && isFinite(minFootY)) grid.position.y = minFootY;
```

- [ ] **Step 3: Syntax-check the module**

Run the same `node --check` command from Task 5 Step 2. Expected: `OK`.

- [ ] **Step 4: Verify in browser**

Reload. At stand, the planted feet rest on the grid plane. Select FL and raise it (e.g. load
peek_over and step to its raised frame): the lifted foot is clearly **above** the grid while
the others stay on it, so airborne vs planted legs are obvious. The grid follows as you edit/play.

- [ ] **Step 5: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): ground grid snaps to the lowest foot"
```

---

## Task 8: Verification pass

**Files:**
- Modify: `scripts/studio.html` or `pi_bridge/*` only if a regression surfaces.

- [ ] **Step 1: Run the automated tests**

Run: `cd /home/rishi/Rishi/AI/Paliv && python -m pytest pi_bridge/test_sequence.py scripts/test_gen_builtin.py scripts/test_animation_endpoints.py -q`
Expected: all PASS.

- [ ] **Step 2: Walk the manual checklist (spec §Testing)**

With the bridge deployed + restarted and `python -m scripts.animation_studio` running:
1. Play on robot on a custom JSON → one continuous, fluid motion (vs old per-frame stalls).
2. (If wiring a tool) a tool scaffolded via add-chotu-tool plays identically to the preview.
3. Timeline: drag-reorder works, moved frame stays selected; no vertical scrollbar at any
   frame count; app fits one screen.
4. Ground grid sits under planted feet; a lifted leg shows clearly above it.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "chore(studio): play-sequence + timeline polish verification pass"
```

---

## Self-review notes

- **Spec coverage:** shared `_play_frames`/`play_frames` (T1) + `/play_sequence` endpoint (T2); add-chotu-tool shares it (T3); studio proxy + long timeout (T4); single-call Play-on-robot (T5); timeline drag-reorder + no vertical scroll (T6); grid snaps to lowest foot (T7); verification (T8).
- **Testability correction vs spec:** the spec assumed `_play_frames` could be monkeypatch-tested in `server.py`; it can't (hardware imports + `Picrawler()` at import). The plan extracts the pure logic to `pi_bridge/sequence.py` (laptop-unit-tested) and verifies the endpoint on the Pi — same shared-player outcome, honest testing.
- **Names consistent across tasks:** `play_frames(crawler, frames, cap, speed_override, sleep)` (T1) ↔ `_play_frames(frames, cap, speed_override)` server wrapper (T2) ↔ `_play_frames(_{TOOL}_FRAMES, MAX_MOTION_SPEED, speed)` in the skill (T3). `reorderFrame(from,to)` (T6). `grid` module var (T7).
- **Deploy:** `sequence.py` added to `deploy.sh` (T2); `pi_bridge/__init__.py` is laptop-only (not shipped), so the Pi script-run import `from sequence import play_frames` is unaffected.
```

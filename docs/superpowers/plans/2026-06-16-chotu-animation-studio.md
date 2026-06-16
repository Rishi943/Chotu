# Chotu Animation Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A browser tool to design PiCrawler poses/animations by editing each leg's `[x,y,z]`, preview them faithfully (2D top-down + side + live 3D) using the real picrawler kinematics, verify on the robot via a local proxy to the Pi bridge, and export the frames JSON the `add-chotu-tool` skill ingests.

**Architecture:** Two new files under `scripts/`, purely additive (no `core/` or Pi-bridge changes). `scripts/animation_studio.py` is a tiny FastAPI/uvicorn launcher on port 8899 that serves the UI and proxies `/set_legs`, `/pose`, `/health` to the Pi (the browser can't reach the Pi cross-origin). `scripts/studio.html` is the entire self-contained UI (like `scripts/chotu_faces.html`), using three.js via CDN. `scripts/kinematics_ref.py` is the Python source-of-truth port of picrawler's IK, mirrored in JS inside the HTML and pinned by a unit test.

**Tech Stack:** Python 3.12, FastAPI + uvicorn, httpx, python-dotenv, pytest + `unittest.mock`, vanilla HTML/CSS/JS, three.js (CDN). Spec: `docs/superpowers/specs/2026-06-16-chotu-animation-studio-design.md`.

---

## File Structure

- `scripts/kinematics_ref.py` — **new.** Pure-Python port of picrawler `coord2polar` + `is_reachable`. Source of truth for the reachability math; the JS in `studio.html` mirrors it. Importable on the laptop (no hardware deps).
- `scripts/animation_studio.py` — **new.** Launcher + proxy. Serves `studio.html`; forwards `/set_legs`, `/pose`, `/health` to `PI_HOST`. Run: `python -m scripts.animation_studio`.
- `scripts/studio.html` — **new.** The whole UI: leg editor, 2D previews, 3D view, timeline, export. Self-contained single file. Verified in the browser (not unit-tested, consistent with `chotu_faces.html`).
- `tests/test_kinematics_ref.py` — **new.** Pins the kinematics outputs.
- `tests/test_animation_studio.py` — **new.** Proxy forwarding + graceful-degradation tests.

Reference reading before starting: the kinematics constants/limits live in memory `picrawler_kinematics.md`; `scripts/chotu_faces.html` is the precedent for a self-contained browser export tool; `core/gui_server.py` shows the FastAPI-serving-HTML + httpx-proxy pattern; `core/pi_client.py:28` shows the `set_legs` body shape.

---

## Task 1: Kinematics reference (Python source of truth)

**Files:**
- Create: `scripts/kinematics_ref.py`
- Test: `tests/test_kinematics_ref.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kinematics_ref.py
"""Pins the picrawler IK port. The JS in studio.html must reproduce these."""

from scripts.kinematics_ref import coord2polar, is_reachable, A, B, C


def test_constants_match_picrawler():
    assert (A, B, C) == (48, 78, 33)


def test_coord2polar_returns_three_angles():
    angles = coord2polar([60, 0, -30])  # stand
    assert len(angles) == 3


def test_stand_gamma_is_45():
    # y=0 -> foot points straight forward -> gamma = -(0 - 45) = 45
    _, _, gamma = coord2polar([60, 0, -30])
    assert abs(gamma - 45.0) < 0.01


def test_stand_is_reachable():
    assert is_reachable([60, 0, -30]) is True


def test_far_reach_clamps_so_not_reachable():
    # L = sqrt(100^2+130^2+60^2) ~= 174.6 > A+B+C (159) -> robot would clamp
    assert is_reachable([100, 130, -60]) is False


def test_wave_exceeds_u_limit():
    # [0,120,60] drives u beyond 91.58 -> clamped on hardware
    assert is_reachable([0, 120, 60]) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_kinematics_ref.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.kinematics_ref'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/kinematics_ref.py
"""Pure-Python port of picrawler 2.1.4 leg IK (read off the Pi).

Source of truth for the studio's reachability check; studio.html mirrors this
in JS. No hardware imports -- runs on the laptop. See memory picrawler_kinematics.md.
"""

import math

A = 48   # upper leg (mm)
B = 78   # lower leg (mm)
C = 33   # hip horizontal offset (mm)
LENGTH_SIDE = 77  # body side (mm)


def coord2polar(coord):
    """Foot [x,y,z] (leg-local frame) -> [alpha, beta, gamma] degrees.

    Verbatim port of Picrawler.coord2polar, including its internal L/u clamps.
    """
    x, y, z = coord
    L = math.sqrt(x ** 2 + y ** 2 + z ** 2)
    if L == 0:
        L = 0.1
    if L < C:
        t = C / L
        x, y, z = t * x, t * y, t * z
    elif L > (A + B + C):
        t = (A + B + C) / L
        x, y, z = t * x, t * y, t * z

    w = math.sqrt(x ** 2 + y ** 2)
    v = w - C
    u = math.sqrt(z ** 2 + v ** 2)
    u = max(30, min(91.58, u))

    beta = math.acos((B ** 2 + A ** 2 - u ** 2) / (2 * B * A))
    angle1 = math.atan2(z, v)
    angle2 = math.acos((A ** 2 + u ** 2 - B ** 2) / (2 * A * u))
    alpha = angle2 + angle1
    gamma = math.atan2(y, x)

    alpha = 90 - alpha / math.pi * 180
    beta = beta / math.pi * 180 - 90
    gamma = -(gamma / math.pi * 180 - 45)
    return [round(alpha, 4), round(beta, 4), round(gamma, 4)]


def is_reachable(coord):
    """True iff the coordinate commands the robot without any clamp/limit kicking in.

    Mirrors picrawler's positional clamps (L, u) and limit_angle bounds.
    """
    x, y, z = coord
    L = math.sqrt(x ** 2 + y ** 2 + z ** 2)
    if L == 0:
        return False
    if L < C or L > (A + B + C):
        return False
    w = math.sqrt(x ** 2 + y ** 2)
    v = w - C
    u = math.sqrt(z ** 2 + v ** 2)
    if u < 30 or u > 91.58:
        return False
    # picrawler sends servos as [beta, alpha, gamma] but limit_angle unpacks them as
    # (alpha, beta, gamma) -> the angle bounds are swapped vs coord2polar's names.
    alpha, beta, gamma = coord2polar(coord)
    return (-10 <= alpha <= 90) and (-90 <= beta <= 90) and (-60 <= gamma <= 60)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_kinematics_ref.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/kinematics_ref.py tests/test_kinematics_ref.py
git commit -m "feat(studio): picrawler IK reference port + reachability check"
```

---

## Task 2: Proxy — serve UI + forward to Pi

**Files:**
- Create: `scripts/animation_studio.py`
- Test: `tests/test_animation_studio.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_animation_studio.py
"""The studio proxy forwards the right body to the Pi and degrades gracefully."""

import httpx
import pytest
from fastapi.testclient import TestClient

from scripts import animation_studio as studio


class _FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data


@pytest.fixture
def client():
    return TestClient(studio.app)


def test_set_legs_forwards_legs_and_speed(monkeypatch, client):
    calls = {}

    async def fake_request(method, url, json=None):
        calls.update(method=method, url=url, json=json)
        return _FakeResp({"ok": True, "tool": "set_legs"})

    monkeypatch.setattr(studio._client, "request", fake_request)
    r = client.post("/set_legs", json={"legs": [[60, 0, -30]] * 4, "speed": 55})

    assert r.status_code == 200
    assert calls["method"] == "POST"
    assert calls["url"].endswith("/set_legs")
    assert calls["json"] == {"legs": [[60, 0, -30]] * 4, "speed": 55}


def test_pose_forwards_name_and_speed(monkeypatch, client):
    calls = {}

    async def fake_request(method, url, json=None):
        calls.update(method=method, url=url, json=json)
        return _FakeResp({"ok": True, "tool": "pose"})

    monkeypatch.setattr(studio._client, "request", fake_request)
    r = client.post("/pose", json={"name": "stand", "speed": 40})

    assert r.status_code == 200
    assert calls["url"].endswith("/pose")
    assert calls["json"] == {"name": "stand", "speed": 40}


def test_pi_unreachable_returns_502(monkeypatch, client):
    async def boom(method, url, json=None):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(studio._client, "request", boom)
    r = client.get("/health")

    assert r.status_code == 502
    assert r.json()["ok"] is False
    assert "pi_unreachable" in r.json()["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_animation_studio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.animation_studio'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/animation_studio.py
"""Chotu Animation Studio launcher + Pi proxy.

Serves studio.html and forwards the few motion endpoints the browser needs to
the Pi bridge (the browser can't POST cross-origin to the Pi). Independent of
core.brain. Run: python -m scripts.animation_studio  (then open :8899).
"""

import os
import pathlib

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

load_dotenv()
PI_HOST = os.getenv("PI_HOST", "http://chotu.local:7000")
_STUDIO_HTML = pathlib.Path(__file__).parent / "studio.html"

app = FastAPI()
_client = httpx.AsyncClient(timeout=30.0)  # set_legs/pose can take many seconds


async def _forward(method: str, path: str, json: dict | None = None):
    try:
        r = await _client.request(method, f"{PI_HOST}{path}", json=json)
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"pi_unreachable: {e}"}, status_code=502
        )


@app.get("/")
async def index():
    return FileResponse(_STUDIO_HTML)


@app.get("/health")
async def health():
    return await _forward("GET", "/health")


@app.post("/set_legs")
async def set_legs(req: Request):
    body = await req.json()
    return await _forward(
        "POST", "/set_legs", {"legs": body["legs"], "speed": body.get("speed", 60)}
    )


@app.post("/pose")
async def pose(req: Request):
    body = await req.json()
    return await _forward(
        "POST", "/pose", {"name": body["name"], "speed": body.get("speed", 40)}
    )


def main():
    print(f"Chotu Animation Studio: http://localhost:8899  (Pi: {PI_HOST})")
    uvicorn.run(app, host="0.0.0.0", port=8899, log_level="warning")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_animation_studio.py -v`
Expected: PASS (3 passed)

> Note: `index()` serves a file that doesn't exist yet — that route isn't covered by these tests, so they pass regardless. The HTML arrives in Task 3.

- [ ] **Step 5: Commit**

```bash
git add scripts/animation_studio.py tests/test_animation_studio.py
git commit -m "feat(studio): launcher + Pi proxy (set_legs/pose/health)"
```

---

## Task 3: UI scaffold — page shell, leg editor, state

**Files:**
- Create: `scripts/studio.html`

This task and all later HTML tasks are verified in the browser (no unit tests, matching `chotu_faces.html`). Keep one `state` object as the single source of truth.

- [ ] **Step 1: Create the page shell + state + leg editor**

Create `scripts/studio.html` as a full document (it talks to its own origin proxy, so it must be a real page). Use the dark monospace aesthetic of `scripts/chotu_faces.html`. Include this exact structure and logic; styling/layout is yours to match Layout A (editor left, preview center, export right, timeline bottom).

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Chotu Animation Studio</title>
<style>
  /* Dark theme, Share Tech Mono. 3-column grid + bottom timeline (Layout A). */
  body { background:#0a0a0a; color:#ddd; font-family:'Share Tech Mono',monospace; margin:0; padding:16px; }
  .col { border:1px solid #2a2a2a; border-radius:4px; padding:10px; }
  .leg.editing { border-left:2px solid #ffb13d; background:#161616; }
  .dot-ok { color:#3ddc6f; } .dot-clamp { color:#ff5b5b; }
  input[type=number] { width:64px; background:#111; color:#fff; border:1px solid #333; }
  button { background:#222; color:#ddd; border:1px solid #444; padding:6px 10px; cursor:pointer; }
  button.primary { background:#1c3a24; color:#3ddc6f; }
</style>
</head>
<body>
<h1 style="font-size:13px;letter-spacing:.2em;color:#888">▟ CHOTU ANIMATION STUDIO</h1>
<div id="conn" style="color:#888">● checking Pi…</div>

<div id="legEditor" class="col"></div>
<div style="margin:8px 0">speed <input id="speed" type="number" min="0" max="90" value="60"></div>
<button class="primary" onclick="sendToRobot()">▶ Send to robot</button>
<button onclick="resetToStand()">⟲ Reset to stand</button>

<!-- Preview / export / timeline mount points (filled in later tasks) -->
<div id="topdown" class="col"></div>
<div id="sideview" class="col"></div>
<div id="threed" class="col"></div>
<div id="exportPanel" class="col"></div>
<div id="timeline" class="col"></div>

<script>
const LEG_NAMES = ["FR · front-right", "FL · front-left", "RL · rear-left", "RR · rear-right"];
// Real picrawler stand (per-leg). The add-chotu-tool contract simplifies this to "[60,0,-30]
// each" but the actual stand frame is asymmetric per leg; the studio uses the real values.
const STAND = [[45,45,-50],[45,0,-50],[45,0,-50],[45,45,-50]];

// Single source of truth.
const state = {
  legs: STAND.map(l => l.slice()),  // current editable pose, 4x[x,y,z]
  speed: 60,
  editing: 1,                       // index of leg highlighted in the editor
  frames: [],                       // timeline: [{legs, speed, hold_s}]
  selectedFrame: null,
};

function setLeg(i, axis, val) {
  state.legs[i][axis] = Number(val);
  renderAll();
}

function resetToStand() {
  state.legs = STAND.map(l => l.slice());
  renderAll();
}

function renderLegEditor() {
  const el = document.getElementById("legEditor");
  el.innerHTML = state.legs.map((leg, i) => `
    <div class="leg ${i===state.editing?'editing':''}" onclick="state.editing=${i};renderAll()">
      <div>${LEG_NAMES[i]} <span id="dot${i}"></span></div>
      ${["x","y","z"].map((ax, a) =>
        `${ax} <input type="number" value="${leg[a]}" oninput="setLeg(${i},${a},this.value)">`
      ).join(" ")}
    </div>`).join("");
}

// renderAll() is extended by later tasks (previews, export, timeline).
function renderAll() {
  state.speed = Number(document.getElementById("speed").value);
  renderLegEditor();
}

renderAll();
</script>
</body>
</html>
```

- [ ] **Step 2: Verify in the browser**

Run: `source .venv/bin/activate && python -m scripts.animation_studio` then open `http://localhost:8899`.
Expected: four legs with x/y/z number inputs; clicking a leg highlights it (orange); editing a number and re-clicking persists; "Reset to stand" sets all to 60/0/-30. (Pi may be offline — editing still works.) Stop the server with Ctrl-C.

- [ ] **Step 3: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): UI scaffold + leg editor + state model"
```

---

## Task 4: 2D previews + reachability dots (JS IK port)

**Files:**
- Modify: `scripts/studio.html`

- [ ] **Step 1: Add the JS kinematics port (mirror of `scripts/kinematics_ref.py`)**

Inside the `<script>`, above `renderAll()`, add — these must reproduce `kinematics_ref.py` exactly (verified against `tests/test_kinematics_ref.py` values):

```javascript
const A=48, B=78, C=33, LENGTH_SIDE=77;

function coord2polar(x, y, z) {
  let L = Math.sqrt(x*x + y*y + z*z);
  if (L === 0) L = 0.1;
  if (L < C) { const t = C/L; x*=t; y*=t; z*=t; }
  else if (L > (A+B+C)) { const t = (A+B+C)/L; x*=t; y*=t; z*=t; }
  const w = Math.sqrt(x*x + y*y);
  const v = w - C;
  let u = Math.sqrt(z*z + v*v);
  u = Math.max(30, Math.min(91.58, u));
  const beta  = Math.acos((B*B + A*A - u*u) / (2*B*A));
  const angle1 = Math.atan2(z, v);
  const angle2 = Math.acos((A*A + u*u - B*B) / (2*A*u));
  const alpha = angle2 + angle1;
  const gamma = Math.atan2(y, x);
  return [90 - alpha*180/Math.PI, beta*180/Math.PI - 90, -(gamma*180/Math.PI - 45)];
}

function isReachable(x, y, z) {
  const L = Math.sqrt(x*x + y*y + z*z);
  if (L === 0) return false;
  if (L < C || L > (A+B+C)) return false;
  const w = Math.sqrt(x*x + y*y), v = w - C;
  const u = Math.sqrt(z*z + v*v);
  if (u < 30 || u > 91.58) return false;
  // picrawler sends servos as [beta,alpha,gamma] but limit_angle unpacks (alpha,beta,gamma),
  // so the bounds are swapped vs coord2polar names: alpha in [-10,90], beta in [-90,90].
  const [al, be, ga] = coord2polar(x, y, z);
  return al>=-10 && al<=90 && be>=-90 && be<=90 && ga>=-60 && ga<=60;
}

// Joint positions in the leg's local vertical plane, for drawing the linkage. Mirrors
// coord2polar's L/u clamps so an out-of-range pose renders where the robot ACTUALLY goes
// (rigid A,B), not a stretched leg. R = radial dist from hip, Z = height; hip=(0,0), base=(C,0).
function legPlane(x, y, z) {
  let L = Math.sqrt(x*x + y*y + z*z); if (L === 0) L = 0.1;
  if (L < C) { const t = C/L; x*=t; y*=t; z*=t; }
  else if (L > (A+B+C)) { const t = (A+B+C)/L; x*=t; y*=t; z*=t; }
  const w = Math.sqrt(x*x + y*y);
  const angle1 = Math.atan2(z, w - C);
  const u = Math.min(Math.max(Math.sqrt((w-C)**2 + z*z), 30), 91.58);
  const theta = angle1 + Math.acos((A*A + u*u - B*B)/(2*A*u));
  return {
    kneeR: C + A*Math.cos(theta), kneeZ: A*Math.sin(theta),
    footR: C + u*Math.cos(angle1), footZ: u*Math.sin(angle1), baseR: C, baseZ: 0,
  };
}
```

> These three helpers (`coord2polar`, `isReachable`, `legPlane`) are validated in `scripts/studio_3d_prototype.html` — port them verbatim. They are shared by the 2D previews (this task) and the 3D view (Task 5).

- [ ] **Step 2: Render the reachability dots + two canvases**

Replace the preview mount points with `<canvas>` elements (`#topCanvas`, `#sideCanvas`, e.g. 260×220) and add these render functions, then call them from `renderAll()`:

```javascript
function renderDots() {
  state.legs.forEach((leg, i) => {
    const ok = isReachable(leg[0], leg[1], leg[2]);
    const el = document.getElementById("dot"+i);
    el.className = ok ? "dot-ok" : "dot-clamp";
    el.textContent = ok ? "● reachable" : "● will clamp";
  });
}

// Top-down: plot each foot (x,y) at its body corner. Corner placement is a
// display convention (front = up); tune signs by eye against the real robot.
const CORNERS = [  // [cx, cy, sx, sy] body corner + axis sign for [FR,FL,RL,RR]
  [ 1,-1,  1,-1], [-1,-1, -1,-1], [-1, 1, -1, 1], [ 1, 1,  1, 1],
];
function renderTopDown() {
  const cv = document.getElementById("topCanvas"), g = cv.getContext("2d");
  g.clearRect(0,0,cv.width,cv.height);
  const cx = cv.width/2, cy = cv.height/2, s = 0.6;  // mm -> px
  const hs = LENGTH_SIDE/2 * s;
  g.strokeStyle = "#33373d"; g.strokeRect(cx-hs, cy-hs, hs*2, hs*2);
  state.legs.forEach((leg, i) => {
    const [bx, by, sgx, sgy] = CORNERS[i];
    const hx = cx + bx*hs, hy = cy + by*hs;
    const fx = hx + sgx*leg[0]*s, fy = hy - sgy*leg[1]*s;  // x reach, y outward
    const ok = isReachable(leg[0], leg[1], leg[2]);
    g.strokeStyle = ok ? "#3a6" : "#a33"; g.beginPath();
    g.moveTo(hx,hy); g.lineTo(fx,fy); g.stroke();
    g.fillStyle = i===state.editing ? "#ffb13d" : (ok ? "#3ddc6f" : "#ff5b5b");
    g.beginPath(); g.arc(fx, fy, i===state.editing?5:4, 0, 7); g.fill();
  });
}

function renderSide() {  // articulated linkage for the leg being edited
  const cv = document.getElementById("sideCanvas"), g = cv.getContext("2d");
  g.clearRect(0,0,cv.width,cv.height);
  const leg = state.legs[state.editing], p = legPlane(leg[0], leg[1], leg[2]);
  const ox = 40, oy = 50, s = 0.7;
  const P = (r,z) => [ox + r*s, oy - z*s];   // z up on screen
  const seg = (aR,aZ,bR,bZ,col) => { const [a0,a1]=P(aR,aZ),[b0,b1]=P(bR,bZ);
    g.strokeStyle=col; g.lineWidth=3; g.beginPath(); g.moveTo(a0,a1); g.lineTo(b0,b1); g.stroke(); };
  g.strokeStyle="#2a2a2a"; g.beginPath(); g.moveTo(0,oy); g.lineTo(cv.width,oy); g.stroke();
  seg(0,0, p.baseR,p.baseZ, "#556");   // C offset (hip at origin)
  seg(p.baseR,p.baseZ, p.kneeR,p.kneeZ, "#9ad");  // A
  seg(p.kneeR,p.kneeZ, p.footR,p.footZ, "#9ad");  // B
}
```

Add to `renderAll()`: `renderDots(); renderTopDown(); renderSide();`

- [ ] **Step 3: Verify in the browser**

Run the server, open `:8899`. Expected: editing x/y/z redraws the top-down feet and the side linkage live; the edited leg's foot is orange and the side view follows it; setting a leg to `[100,130,-60]` flips its dot to red "will clamp" and its top-down marker red. Stand `[60,0,-30]` shows all-green dots.

- [ ] **Step 4: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): faithful 2D top-down + side previews + reachability dots"
```

---

## Task 5: Live 3D view + CAD-style drag-to-pose (three.js)

This task is fully prototyped and validated in **`scripts/studio_3d_prototype.html`** (built and approved during brainstorming: symmetric stance, faithful IK, white-steel model, orbit camera, drag-to-pose gizmo, animation playback). **Port that 3D module into `studio.html` — it is the source of truth for everything below.** The notes here pin the decisions so they aren't lost in translation.

**Files:**
- Modify: `scripts/studio.html`
- Reference: `scripts/studio_3d_prototype.html` (port the `<script type="module">` 3D block from here)

- [ ] **Step 1: Load three.js via ES-module importmap (NOT the UMD build)**

three.js dropped the UMD `build/three.min.js` + `examples/js/` globals. Use the import map + addons (validated in the prototype). In `<head>`:

```html
<script type="importmap">
{ "imports": {
  "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
  "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"
}}
</script>
```

The studio's script that uses three.js must be `<script type="module">` and import:
```javascript
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
```
(Because module scripts can't read non-module globals, the shared IK helpers from Task 4 must be reachable from the module — keep them in the same module, or attach to `window`. The prototype keeps everything in one module; mirror that.)

- [ ] **Step 2: Build the scene + white-steel model (port from prototype)**

Port verbatim from `scripts/studio_3d_prototype.html`. The validated specifics:
- **Scene:** background `0x232730` (slate, not black); `AmbientLight` 0.7 + `DirectionalLight` 0.7; `GridHelper(400,16,0x4a4f57,0x363b42)` at `y=-95`; orbit camera at `(220,180,260)` targeting `(0,-20,0)`.
- **Body:** silver (`SILVER=0xe2e5ea`) chassis box + dark-green board on top + front nose cone and two ultrasonic "eye" cylinders (front = `+Z`).
- **Per leg:** flat metal-plate bones for the `C` hip-link and `A` thigh (`mkPlate`), a triangular **foot blade** for the `B` lower leg (`mkBlade`, base at knee → tip at foot, scaled per-frame), and three grey servo markers (`mkServo`) at the yaw/thigh/knee joints. Colors: edited leg `0xffb13d`, reachable `SILVER`, out-of-range `0xff5b5b`.
- **CALIBRATED per-leg orientation** (this is the key fix — makes the real per-leg stand render as a symmetric X-splay):
  ```javascript
  const hs = LENGTH_SIDE/2;
  const CORNERS = [ {p:[ hs,0, hs], yaw:Math.PI*0.5 },   // FR
                    {p:[-hs,0, hs], yaw:Math.PI*0.75},   // FL
                    {p:[-hs,0,-hs], yaw:Math.PI*1.25},   // RL
                    {p:[ hs,0,-hs], yaw:Math.PI*2.0 } ]; // RR
  // worldYaw = corner.yaw - atan2(y,x);  dir = (cos worldYaw, 0, sin worldYaw)
  // foot world point P(r,zz) = corner.p + dir*r + (0,zz,0); hip=P(0,0) base=P(C,0) knee=P(kneeR,kneeZ) foot=P(footR,footZ)
  ```
  Render from the numbers each frame; call it inside `renderAll()` and start a `requestAnimationFrame` loop with `controls.update()`.

- [ ] **Step 3: CAD-style drag-to-pose (TransformControls gizmo)**

Port verbatim from the prototype. A `TransformControls` translate-gizmo attaches to a `gizmoTarget` Object3D parked on the edited leg's foot. Dragging a handle converts the handle's world position back to that leg's local `[x,y,z]` (the exact inverse of the forward placement) and updates `state` + the inputs:
```javascript
// world -> leg-local (inverse of worldYaw = c.yaw - atan2(y,x))
const rel = gizmoTarget.position.clone().sub(new THREE.Vector3(...c.p));
const w = Math.hypot(rel.x, rel.z), la = c.yaw - Math.atan2(rel.z, rel.x);
state.legs[i] = [ Math.round(w*Math.cos(la)), Math.round(w*Math.sin(la)), Math.round(rel.y) ];
```
- Disable `OrbitControls` while a handle is dragged (`'dragging-changed'` → `controls.enabled = !e.value`).
- In `render`, only re-park `gizmoTarget` on the foot when **not** dragging (avoids fighting the user).
- Touching any slider also selects that leg (so the gizmo follows). Verified in the prototype.

- [ ] **Step 4: Verify in the browser**

Run the server, open `:8899`. Expected (matching the approved prototype):
- **Stand renders as a symmetric X-splay** (four legs evenly to the corners).
- Editing a leg (slider or gizmo) moves it live; the **gizmo arrows drag the foot** and the IK solves the leg; x/y/z update.
- Out-of-range pose keeps rigid `A`/`B` at the clamped position and flips the dot to red "will clamp".
- (Animation playback is exercised by the timeline in Task 7.)

- [ ] **Step 5: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): live 3D model + CAD-style drag-to-pose (three.js)"
```

> **Hardware-verify items (cannot be confirmed off-robot, noted in the spec):**
> (1) the leg **swing handedness** during motion is a calibration choice (one sign in `worldYaw`); (2) the per-leg orientation is solved against the real stand, but absolute mounting is confirmed on the table. Both are isolated to the `CORNERS` table / `worldYaw` sign — a one-line change if a leg reads mirrored on hardware.

---

## Task 6: Send to robot + connection pill

**Files:**
- Modify: `scripts/studio.html`

- [ ] **Step 1: Add the proxy calls + health poll**

```javascript
async function sendToRobot() {
  try {
    const r = await fetch("/set_legs", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({legs: state.legs, speed: state.speed})});
    const j = await r.json();
    if (!j.ok) alert("Pi error: " + (j.error || JSON.stringify(j)));
  } catch (e) { alert("Send failed: " + e); }
}

async function pollHealth() {
  const el = document.getElementById("conn");
  try {
    const r = await fetch("/health");
    const ok = r.ok && (await r.json()).ok;
    el.textContent = ok ? "● Pi connected" : "● Pi unreachable";
    el.style.color = ok ? "#3ddc6f" : "#ff5b5b";
    document.querySelectorAll(".needs-pi").forEach(b => b.disabled = !ok);
  } catch { el.textContent = "● Pi unreachable"; el.style.color = "#ff5b5b"; }
}
setInterval(pollHealth, 4000); pollHealth();
```

Add class `needs-pi` to the "Send to robot" and "Reset to stand" buttons so they disable when the Pi is down. (Reset uses `resetToStand()` locally; if you want it to also command the robot, wire it to POST `/pose {name:"stand"}` — keep it local-only for v1 per spec.)

- [ ] **Step 2: Verify**

With the Pi reachable (`ssh chotu@chotu.local` works), open `:8899`: pill shows green "Pi connected"; click "Send to robot" with a sane pose (e.g. stand) — the robot assumes it. Stop the Pi bridge and confirm the pill flips red and the button disables within ~4s. **Hardware caveat:** only send reachable poses; watch for brownout (an envelope returning suspiciously fast). Confirm coordinate correctness on the table — the diagrams are guides.

- [ ] **Step 3: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): send-to-robot + Pi connection indicator"
```

---

## Task 7: Timeline (keyframes + playback)

**Files:**
- Modify: `scripts/studio.html`

- [ ] **Step 1: Add timeline state ops + render + playback**

```javascript
function addFrame() {
  state.frames.push({legs: state.legs.map(l=>l.slice()), speed: state.speed, hold_s: 0});
  state.selectedFrame = state.frames.length-1; renderAll();
}
function dupFrame(i){ const f=state.frames[i];
  state.frames.splice(i+1,0,{legs:f.legs.map(l=>l.slice()),speed:f.speed,hold_s:f.hold_s}); renderAll(); }
function delFrame(i){ state.frames.splice(i,1); state.selectedFrame=null; renderAll(); }
function moveFrame(i,d){ const j=i+d; if(j<0||j>=state.frames.length) return;
  [state.frames[i],state.frames[j]]=[state.frames[j],state.frames[i]]; renderAll(); }
function loadFrame(i){ const f=state.frames[i];
  state.legs=f.legs.map(l=>l.slice()); state.speed=f.speed;
  document.getElementById("speed").value=f.speed; state.selectedFrame=i; renderAll(); }
function setHold(i,v){ state.frames[i].hold_s=Number(v); }
function setFrameSpeed(i,v){ state.frames[i].speed=Number(v); }

function renderTimeline() {
  const el = document.getElementById("timeline");
  el.innerHTML = state.frames.map((f,i)=>`
    <span class="${i===state.selectedFrame?'editing':''}" style="border:1px solid #333;padding:6px;margin:2px;display:inline-block">
      <b onclick="loadFrame(${i})" style="cursor:pointer">F${i+1}</b>
      spd <input type="number" value="${f.speed}" style="width:48px" oninput="setFrameSpeed(${i},this.value)">
      hold <input type="number" step="0.1" value="${f.hold_s}" style="width:48px" oninput="setHold(${i},this.value)">
      <button onclick="moveFrame(${i},-1)">◀</button><button onclick="moveFrame(${i},1)">▶</button>
      <button onclick="dupFrame(${i})">⧉</button><button onclick="delFrame(${i})">🗑</button>
    </span>`).join(" → ") +
    `<div style="margin-top:6px">
      <button onclick="addFrame()">＋ Add frame</button>
      <button onclick="playPreview()">▷ Play preview</button>
      <button class="needs-pi" onclick="playOnRobot()">▶▶ Play on robot</button></div>`;
}

const sleep = ms => new Promise(r=>setTimeout(r,ms));
async function playPreview() {
  for (const f of state.frames) {
    state.legs = f.legs.map(l=>l.slice()); renderAll();
    await sleep(700 + f.hold_s*1000);  // ~servo travel + hold, visual only
  }
}
async function playOnRobot() {  // mirrors the generated _play_{tool} body
  for (const f of state.frames) {
    await fetch("/set_legs", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({legs: f.legs, speed: f.speed})});
    if (f.hold_s) await sleep(f.hold_s*1000);
  }
}
```

Add `renderTimeline();` to `renderAll()`.

- [ ] **Step 2: Verify**

Open `:8899`: edit a pose, "Add frame" → a chip F1 appears with spd/hold inputs; add a few; reorder/dup/delete work; clicking F2's number loads it back into the editor (previews update); "Play preview" steps the diagrams through the frames; with the Pi up, "Play on robot" walks the robot through them and disables when the Pi is down.

- [ ] **Step 3: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): keyframe timeline + preview/robot playback"
```

---

## Task 8: Export panel (frames JSON contract)

**Files:**
- Modify: `scripts/studio.html`

- [ ] **Step 1: Add export metadata inputs + build/validate/download/load**

Add inputs to `#exportPanel`: `#toolName`, `#desc`, `#personaGated` (checkbox), `#defaultSpeed` (number, default 60), plus buttons wired to the functions below and a `#exportStatus` line.

```javascript
const SNAKE = /^[a-z][a-z0-9_]*$/;

function buildExport() {
  return {
    tool: document.getElementById("toolName").value.trim(),
    description: document.getElementById("desc").value.trim(),
    persona_gated: document.getElementById("personaGated").checked,
    default_speed: Number(document.getElementById("defaultSpeed").value),
    frames: state.frames.map(f => ({legs: f.legs, speed: f.speed, hold_s: f.hold_s})),
  };
}

function validateExport(d) {
  if (!SNAKE.test(d.tool)) return "tool must be snake_case";
  if (!d.frames.length) return "need at least 1 frame";
  for (const f of d.frames) {
    if (!Array.isArray(f.legs) || f.legs.length !== 4) return "each frame needs 4 legs";
    if (f.legs.some(l => !Array.isArray(l) || l.length !== 3)) return "each leg is [x,y,z]";
  }
  return null;
}

function renderExportStatus() {
  const err = validateExport(buildExport());
  const el = document.getElementById("exportStatus");
  el.textContent = err ? "✗ " + err : `✓ ${state.frames.length} frame(s) valid`;
  el.style.color = err ? "#ff5b5b" : "#3ddc6f";
}

function exportJSON() {
  const d = buildExport(); const err = validateExport(d);
  if (err) { alert("Cannot export: " + err); return; }
  const blob = new Blob([JSON.stringify(d, null, 2)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = d.tool + ".json"; a.click();
}

async function copyJSON() {
  const d = buildExport(); const err = validateExport(d);
  if (err) { alert("Cannot copy: " + err); return; }
  await navigator.clipboard.writeText(JSON.stringify(d, null, 2));
}

function loadJSON(file) {
  const reader = new FileReader();
  reader.onload = () => {
    const d = JSON.parse(reader.result);
    document.getElementById("toolName").value = d.tool || "";
    document.getElementById("desc").value = d.description || "";
    document.getElementById("personaGated").checked = !!d.persona_gated;
    document.getElementById("defaultSpeed").value = d.default_speed ?? 60;
    state.frames = (d.frames || []).map(f => ({legs: f.legs.map(l=>l.slice()), speed: f.speed, hold_s: f.hold_s}));
    state.selectedFrame = null; renderAll();
  };
  reader.readAsText(file);
}
```

Wire a hidden `<input type="file" accept=".json" onchange="loadJSON(this.files[0])">` behind a "Load JSON" button, and add `renderExportStatus();` to `renderAll()`.

- [ ] **Step 2: Verify export matches the contract**

Open `:8899`, build a 2-frame animation, set tool `shimmy`, export. Confirm the downloaded `shimmy.json` matches the `add-chotu-tool` contract exactly:

```bash
python -c "import json,sys; d=json.load(open('/home/rishi/Downloads/shimmy.json')); \
assert set(d)=={'tool','description','persona_gated','default_speed','frames'}; \
assert all(len(f['legs'])==4 and all(len(l)==3 for l in f['legs']) for f in d['frames']); \
print('contract OK', len(d['frames']), 'frames')"
```

Expected: `contract OK 2 frames`. Then click "Load JSON" on that file and confirm the timeline + metadata repopulate (round-trip). Invalid tool name (e.g. `Shimmy 1`) shows red status and blocks export.

- [ ] **Step 3: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): frames-JSON export, validation, copy, load round-trip"
```

---

## Task 9: Full-suite regression + docs note

**Files:**
- Modify: `CLAUDE.md` (one line under Dev setup)

- [ ] **Step 1: Run the full test suite**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: all pass (including the two new test files), no regressions.

- [ ] **Step 2: Add a dev-setup line for the studio**

In `CLAUDE.md`, under the "Dev setup" bullet list (near the other launch commands), add:

```markdown
- Animation studio: `python -m scripts.animation_studio` (pose/animation editor on :8899, proxies to the Pi; exports frames JSON for `add-chotu-tool`)
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(studio): note the animation studio launch command"
```

---

## Self-Review Notes

- **Spec coverage:** proxy/architecture (T2), kinematics + reachability (T1, mirrored T4/T5), leg editor (T3), 2D top-down+side (T4), 3D model **+ drag-to-pose** (T5 — promoted from Tier-2 after the prototype validated it), send-to-robot + connection pill (T6), timeline + dual playback (T7), frames-contract export + validation + Load JSON (T8), tests + degradation (T1/T2), full-suite + docs (T9).
- **Prototype is the source of truth for the 3D.** `scripts/studio_3d_prototype.html` was built and approved during brainstorming; T4/T5 port its validated helpers (`coord2polar`, `isReachable`, `legPlane`) and 3D module rather than re-deriving them. The prototype is a throwaway spike — it does not need to ship, but it must not be deleted until T5 is ported.
- **Hardware truths:** the kinematics math is exact (pinned in T1). The per-leg display orientation is calibrated so the *real* stand renders symmetric, but **swing handedness and absolute mounting are hardware-verify items** (isolated to the `CORNERS`/`worldYaw` sign — see T5 note). Real stand is `[[45,45,-50],[45,0,-50],[45,0,-50],[45,45,-50]]`, not the contract's `[60,0,-30]` simplification (T3).
- **Type consistency:** `state` shape, `coord2polar`/`isReachable`/`legPlane` signatures, frame `{legs,speed,hold_s}`, and the proxy `_forward(method,path,json)`/`_client.request` names are used consistently across tasks and match `kinematics_ref.py`. `legPlane` returns `{kneeR,kneeZ,footR,footZ,baseR,baseZ}` (hip is the origin); used by both T4 (side view) and T5 (3D).

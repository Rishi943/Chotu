# Animation Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a browsable animation library to the Chotu Animation Studio — load saved JSONs and all of Chotu's built-in motions from thumbnail cards, edit them, and save new custom JSON tools; remove the SIDE · LINKAGE panel.

**Architecture:** A pure-Python generator emits Chotu's built-in motions as frames-JSON into `assets/Animations/builtin/`. The FastAPI launcher (`scripts/animation_studio.py`) gains `GET/POST /animations` to list and save animations. `scripts/studio.html` gets a Library overlay (fetch + thumbnail grid + load) and a Save button, and loses the side-linkage panel.

**Tech Stack:** Python 3.12, FastAPI + Starlette `TestClient` for endpoint tests, vanilla HTML/JS (three.js studio), pytest.

**Verification:** Server endpoints get real pytest tests (FastAPI `TestClient`, no Pi needed). The generator is verified by running it and validating every output file against the studio's frames-JSON rules. The studio UI is verified manually in the browser (`python -m scripts.animation_studio`).

---

## Task 1: Built-in animation generator

Port Chotu's motions to frames-JSON. Pure Python, no hardware imports, reproducible.

**Files:**
- Create: `scripts/gen_builtin_animations.py`
- Create (output, committed): `assets/Animations/builtin/*.json`
- Test: `scripts/test_gen_builtin.py`

- [ ] **Step 1: Write the generator**

Create `scripts/gen_builtin_animations.py`:

```python
"""Generate Chotu's built-in motions as frames-JSON into assets/Animations/builtin/.

One-time, reproducible, NO hardware imports. Gait/pose step lists are ported verbatim
from the picrawler MoveList (read off the Pi); trick keyframes are sampled from the
procedural routines in pi_bridge/server.py. Each step (4x[x,y,z]) becomes one frame.
Run: python -m scripts.gen_builtin_animations
"""
import json
import math
import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "assets" / "Animations" / "builtin"

# --- picrawler MoveList constants (verbatim) ---
XD, XT, XS = 45, 70, 0          # X_DEFAULT, X_TURN, X_START
YD, YT, YW, YS = 45, 130, 120, 0  # Y_DEFAULT, Y_TURN, Y_WAVE, Y_START
ZD, ZU, ZW, ZT, ZP = -50, -30, 60, -40, -76  # Z_DEFAULT, Z_UP, Z_WAVE, Z_TURN, Z_PUSH
SIDE = 77
zc = ZD  # z_current while standing

# turn geometry (verbatim from MoveList)
TEMP_A = math.sqrt((2*XD + SIDE)**2 + YD**2)
TEMP_B = 2*(YS + YD) + SIDE
TEMP_C = math.sqrt((2*XD + SIDE)**2 + (2*YS + YD + SIDE)**2)
TEMP_ALPHA = math.acos((TEMP_A**2 + TEMP_B**2 - TEMP_C**2) / 2 / TEMP_A / TEMP_B)
TX1 = (TEMP_A - SIDE) / 2
TY1 = YS + YD/2
TX0 = TX1 - TEMP_B*math.cos(TEMP_ALPHA)
TY0 = TEMP_B*math.sin(TEMP_ALPHA) - TY1 - SIDE

def turn_angle_coord(angle):  # verbatim from MoveList.turn_angle_coord
    a = math.atan(YD/(XD+SIDE/2)); angle1 = a/math.pi*180
    r1 = math.sqrt(YD**2 + (XD+SIDE/2)**2)
    x1 = r1*math.cos((angle1-angle)*math.pi/180) - SIDE/2
    y1 = r1*math.sin((angle1-angle)*math.pi/180)
    x2 = (XD+SIDE/2)*math.cos(angle*math.pi/180) - SIDE/2
    y2 = (XD+SIDE/2)*math.sin(angle*math.pi/180)
    b = math.atan((XD+SIDE/2)/(YD+SIDE)); angle2 = b/math.pi*180
    r2 = math.sqrt((XD+SIDE/2)**2 + (YD+SIDE)**2)
    x3 = r2*math.sin((angle2-angle)*math.pi/180) - SIDE/2
    y3 = r2*math.cos((angle2-angle)*math.pi/180) - SIDE
    x3 += 10
    return [x1, y1, x2, y2, x3, y3]

def rnd(step):  # round a 4x[x,y,z] step to ints
    return [[round(v) for v in leg] for leg in step]

# --- discrete gait/pose step lists (stand_position==0 branch) ---
MOVES = {}

MOVES["forward"] = (60, [
    [[XD,YD,zc],[XT,YS,ZU],[XD,YS,zc],[XD,YD,zc]],
    [[XD,YD,zc],[XD,YD*2,ZU],[XD,YS,zc],[XD,YD,zc]],
    [[XD,YD,zc],[XD,YD*2,zc],[XD,YS,zc],[XD,YD,zc]],
    [[XD,YS,zc],[XD,YD,zc],[XD,YD,zc],[XD,YD*2,zc]],
    [[XD,YS,zc],[XD,YD,zc],[XD,YD,zc],[XD,YD*2,ZU]],
    [[XD,YS,zc],[XD,YD,zc],[XD,YD,zc],[XT,YS,ZU]],
    [[XD,YS,zc],[XD,YD,zc],[XD,YD,zc],[XD,YS,zc]],
])
MOVES["backward"] = (60, [
    [[XD,YD,zc],[XD,YS,zc],[XT,YS,ZU],[XD,YD,zc]],
    [[XD,YD,zc],[XD,YS,zc],[XD,YD*2,ZU],[XD,YD,zc]],
    [[XD,YD,zc],[XD,YS,zc],[XD,YD*2,zc],[XD,YD,zc]],
    [[XD,YD*2,zc],[XD,YD,zc],[XD,YD,zc],[XD,YS,zc]],
    [[XD,YD*2,ZU],[XD,YD,zc],[XD,YD,zc],[XD,YS,zc]],
    [[XT,YS,ZU],[XD,YD,zc],[XD,YD,zc],[XD,YS,zc]],
    [[XD,YS,zc],[XD,YD,zc],[XD,YD,zc],[XD,YS,zc]],
])
MOVES["turn_left"] = (60, [
    [[XD,YD,zc],[XD,YS,zc],[XT,YS,ZU],[XD,YD,zc]],
    [[TX1,TY1,zc],[TX1,TY1,zc],[TX0,TY0,ZU],[TX0,TY0,zc]],
    [[TX1,TY1,zc],[TX1,TY1,zc],[TX0,TY0,zc],[TX0,TY0,zc]],
    [[TX1,TY1,zc],[TX1,TY1,zc],[TX0,TY0,zc],[TX0,TY0,ZU]],
    [[XD,YS,zc],[XD,YD,zc],[XD,YD,zc],[XT,YS,ZU]],
    [[XD,YS,zc],[XD,YD,zc],[XD,YD,zc],[XD,YS,zc]],
])
MOVES["turn_right"] = (60, [
    [[XD,YD,zc],[XT,YS,ZU],[XD,YS,zc],[XD,YD,zc]],
    [[TX0,TY0,zc],[TX0,TY0,ZU],[TX1,TY1,zc],[TX1,TX1,zc]],
    [[TX0,TY0,zc],[TX0,TY0,zc],[TX1,TY1,zc],[TX1,TX1,zc]],
    [[TX0,TY0,ZU],[TX0,TY0,zc],[TX1,TY1,zc],[TX1,TX1,zc]],
    [[XT,YS,ZU],[XD,YD,zc],[XD,YD,zc],[XD,YS,zc]],
    [[XD,YS,zc],[XD,YD,zc],[XD,YD,zc],[XD,YS,zc]],
])
MOVES["wave"] = (50, [
    [[XD,YD,zc],[XT,YS,ZU],[XD,YS,zc],[XD,YD,zc]],
    [[XD,YD,zc],[XS,YW,ZW],[XD,YS,zc],[XD,YD,zc]],
    [[XD,YD,zc],[XS,YW,ZU],[XD,YS,zc],[XD,YD,zc]],
    [[XD,YD,zc],[XS,YW,ZW],[XD,YS,zc],[XD,YD,zc]],
    [[XD,YD,zc],[XS,YW,ZU],[XD,YS,zc],[XD,YD,zc]],
    [[XD,YD,zc],[XT,YS,ZU],[XD,YS,zc],[XD,YD,zc]],
    [[XD,YD,zc],[XD,YS,zc],[XD,YS,zc],[XD,YD,zc]],
])
MOVES["sit"] = (50, [
    [[XD,YD,ZU],[XT,YS,ZU],[XT,YS,ZU],[XD,YD,ZU]],
])
MOVES["stand"] = (40, [
    [[XD,YD,round(ZD*m)],[XD,YS,round(ZD*m)],[XD,YS,round(ZD*m)],[XD,YD,round(ZD*m)]]
    for m in (0.35, 0.55, 0.75, 0.9, 1.0)
])
MOVES["look_up"] = (50, [
    [[XD,YD,ZD],[XD,YS,ZD],[XT,YS,ZU],[XD,YD,ZU]],
])
MOVES["look_down"] = (50, [
    [[XD,YD,ZU],[XT,YS,ZU],[XD,YS,zc],[XD,YD,zc]],
])
def _look(turn_first):
    li = turn_angle_coord(30)
    t1 = [li[0], li[1], zc]; t2 = [li[2], li[3], zc]; t3 = [li[4], li[5], zc]
    a = [[XD,YD,zc],[XD,YS,zc],[XT,YS,ZU],[XD,YD,zc]]
    b = [t1, t2, [XT,YS,ZU], t3] if turn_first else [t3, [XT,YS,ZU], t2, t1]
    return [a, b]
MOVES["look_left"] = (50, _look(True))
MOVES["look_right"] = (50, _look(False))
MOVES["push_up"] = (60, [
    [[XD,YD,ZU],[XT,YS,ZU],[XT,YS,ZU],[XD,YD,ZU]],          # sit
    [[XT,YS,ZT],[XT,YS,ZT],[XS,YT,ZT],[XS,YT,ZT]],
    [[XT,YS,ZP],[XT,YS,ZP],[XS,YT,ZT],[XS,YT,ZT]],
    [[XT,YS,ZT],[XT,YS,ZT],[XS,YT,ZT],[XS,YT,ZT]],
    [[XT,YS,ZP],[XT,YS,ZP],[XS,YT,ZT],[XS,YT,ZT]],
    [[XT,YS,ZT],[XT,YS,ZT],[XS,YT,ZT],[XS,YT,ZT]],
    [[XD,YD,zc],[XT,YS,zc],[XT,YS,zc],[XD,YD,zc]],          # back toward stand
])

# --- trick keyframes (sampled from pi_bridge/server.py procedural routines) ---
STAND = [[45,45,-50],[45,0,-50],[45,0,-50],[45,45,-50]]

def _twist_frames():
    frames = [STAND]
    for i in range(4):
        rise = [50,50,-80 + 55*0.5]; drop = [50,50,-80 - 55]
        s = [None]*4
        s[i] = rise; s[(i+2)%4] = drop; s[(i+1)%4] = rise; s[(i-1)%4] = drop
        frames.append(s)
    frames.append(STAND)
    return frames
MOVES["twist"] = (100, _twist_frames())

def _swim_frames():
    out = [[[60,0,-30]]*4,
           [[80,20,-20],[80,20,-20],[40,60,-50],[40,60,-50]]]
    for phase in (0.5, 1.0):
        f = [80+20*phase, 20+20*phase, -20+10*phase]
        r = [40-20*phase, 60+40*phase, -50+20*phase]
        out.append([f, f, r, r])
    out.append(STAND)
    return out
MOVES["swimming"] = (100, _swim_frames())

def _handwork_frames():
    base = [[XD,YD,ZU],[XT,YS,ZU],[XT,YS,ZU],[XD,YD,ZU]]  # sit
    def mix(step, leg, coord):
        s = [list(l) for l in step]; s[leg] = list(coord); return s
    left = mix(base, 0, [0,50,80]); two = mix(left, 1, [0,50,80]); right = mix(base, 1, [0,50,80])
    return [base, left, two, right, base, STAND]
MOVES["handwork"] = (100, _handwork_frames())

DESCRIPTIONS = {
    "forward":"Walk forward one gait cycle.", "backward":"Walk backward one gait cycle.",
    "turn_left":"Turn left in place.", "turn_right":"Turn right in place.",
    "wave":"Wave the front-left leg.", "sit":"Sit down.", "stand":"Rise to a stand.",
    "look_up":"Tilt to look up.", "look_down":"Tilt to look down.",
    "look_left":"Turn head to the left.", "look_right":"Turn head to the right.",
    "push_up":"Do push-ups.", "twist":"Twist the body side to side.",
    "swimming":"Swimming-style leg motion.", "handwork":"Raise the front legs in turn.",
}

def build(name, default_speed, steps):
    return {
        "tool": name, "description": DESCRIPTIONS.get(name, ""),
        "persona_gated": False, "default_speed": default_speed,
        "frames": [{"legs": rnd(s), "speed": default_speed, "hold_s": 0} for s in steps],
    }

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (spd, steps) in MOVES.items():
        d = build(name, spd, steps)
        (OUT / f"{name}.json").write_text(json.dumps(d, indent=2))
        print(f"wrote {name}.json ({len(d['frames'])} frames)")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write a test that validates every generated file against the studio schema**

Create `scripts/test_gen_builtin.py`:

```python
import json
import re
import pathlib
import subprocess
import sys

SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILTIN = ROOT / "assets" / "Animations" / "builtin"

def test_generate_and_validate():
    subprocess.run([sys.executable, "-m", "scripts.gen_builtin_animations"],
                   cwd=ROOT, check=True)
    files = list(BUILTIN.glob("*.json"))
    expected = {"forward","backward","turn_left","turn_right","wave","sit","stand",
                "look_up","look_down","look_left","look_right","push_up",
                "twist","swimming","handwork"}
    assert {f.stem for f in files} == expected
    for f in files:
        d = json.loads(f.read_text())
        assert SNAKE.match(d["tool"]), f"{f.name} tool not snake_case"
        assert d["frames"], f"{f.name} has no frames"
        for fr in d["frames"]:
            assert len(fr["legs"]) == 4, f"{f.name} frame needs 4 legs"
            for leg in fr["legs"]:
                assert len(leg) == 3, f"{f.name} leg must be [x,y,z]"
                assert all(isinstance(v, int) for v in leg), f"{f.name} legs must be ints"
```

- [ ] **Step 3: Run the test**

Run: `cd /home/rishi/Rishi/AI/Paliv && python -m pytest scripts/test_gen_builtin.py -v`
Expected: PASS — 15 files generated and validated.

- [ ] **Step 4: Commit**

```bash
git add scripts/gen_builtin_animations.py scripts/test_gen_builtin.py assets/Animations/builtin
git commit -m "feat(studio): generate built-in Chotu motions as frames JSON"
```

---

## Task 2: Server `/animations` list + save endpoints

**Files:**
- Modify: `scripts/animation_studio.py`
- Test: `scripts/test_animation_endpoints.py`

- [ ] **Step 1: Write failing endpoint tests**

Create `scripts/test_animation_endpoints.py`:

```python
import json
from starlette.testclient import TestClient
from scripts.animation_studio import app, _ANIM_DIR

client = TestClient(app)

def test_list_includes_builtin_and_user():
    r = client.get("/animations")
    assert r.status_code == 200
    names = {a["tool"] for a in r.json()["animations"]}
    assert "forward" in names          # built-in
    builtins = [a for a in r.json()["animations"] if a["builtin"]]
    assert builtins and all("frames" in a for a in builtins)

def test_save_writes_user_file_then_lists():
    payload = {"tool":"unit_test_anim","description":"x","persona_gated":False,
               "default_speed":60,"frames":[{"legs":[[45,45,-50],[45,0,-50],[45,0,-50],[45,45,-50]],
               "speed":60,"hold_s":0}]}
    r = client.post("/animations", json=payload)
    assert r.status_code == 200 and r.json()["ok"]
    try:
        names = {a["tool"] for a in client.get("/animations").json()["animations"]}
        assert "unit_test_anim" in names
    finally:
        (_ANIM_DIR / "unit_test_anim.json").unlink(missing_ok=True)

def test_save_rejects_bad_tool_name():
    r = client.post("/animations", json={"tool":"Bad Name","frames":[{"legs":[]}]})
    assert r.status_code == 400

def test_save_rejects_path_escape():
    r = client.post("/animations", json={"tool":"../evil",
        "frames":[{"legs":[[1,1,1],[1,1,1],[1,1,1],[1,1,1]]}]})
    assert r.status_code in (400, 403)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/rishi/Rishi/AI/Paliv && python -m pytest scripts/test_animation_endpoints.py -v`
Expected: FAIL (404 — endpoints don't exist yet).

- [ ] **Step 3: Implement the endpoints**

In `scripts/animation_studio.py`, after the existing imports add `import json, re` (json/re if not present) and after `_STUDIO_HTML = ...` add:

```python
_REPO = pathlib.Path(__file__).resolve().parent.parent
_ANIM_DIR = _REPO / "assets" / "Animations"
_BUILTIN_DIR = _ANIM_DIR / "builtin"
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
_ANIM_DIR.mkdir(parents=True, exist_ok=True)
_BUILTIN_DIR.mkdir(parents=True, exist_ok=True)


def _read_anim(path: pathlib.Path, builtin: bool):
    try:
        d = json.loads(path.read_text())
    except Exception as e:
        print(f"skipping invalid animation {path.name}: {e}")
        return None
    return {
        "file": path.name, "name": d.get("tool") or path.stem, "builtin": builtin,
        "tool": d.get("tool") or path.stem, "description": d.get("description", ""),
        "persona_gated": bool(d.get("persona_gated", False)),
        "default_speed": d.get("default_speed", 60), "frames": d.get("frames", []),
    }
```

Then add the routes (anywhere among the other `@app` routes):

```python
@app.get("/animations")
async def list_animations():
    out = []
    for p in sorted(_ANIM_DIR.glob("*.json")):
        a = _read_anim(p, False)
        if a:
            out.append(a)
    for p in sorted(_BUILTIN_DIR.glob("*.json")):
        a = _read_anim(p, True)
        if a:
            out.append(a)
    return {"animations": out}


@app.post("/animations")
async def save_animation(req: Request):
    d = await req.json()
    tool = (d.get("tool") or "").strip()
    if not _SNAKE.match(tool) or not d.get("frames"):
        return JSONResponse({"ok": False, "error": "tool must be snake_case and have >=1 frame"},
                            status_code=400)
    dest = (_ANIM_DIR / f"{tool}.json").resolve()
    if dest.parent != _ANIM_DIR.resolve():
        return JSONResponse({"ok": False, "error": "invalid path"}, status_code=403)
    dest.write_text(json.dumps(d, indent=2))
    return {"ok": True, "file": dest.name}
```

(`Request`, `JSONResponse`, `pathlib`, `os` are already imported; add `import json` and `import re` to the top if missing.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/rishi/Rishi/AI/Paliv && python -m pytest scripts/test_animation_endpoints.py -v`
Expected: PASS (4 tests). The path-escape test passes because `../evil` fails the `_SNAKE` regex (400).

- [ ] **Step 5: Commit**

```bash
git add scripts/animation_studio.py scripts/test_animation_endpoints.py
git commit -m "feat(studio): /animations list + save endpoints"
```

---

## Task 3: Remove the SIDE · LINKAGE panel

**Files:**
- Modify: `scripts/studio.html`

- [ ] **Step 1: Remove the side-linkage markup**

Delete this block from the right-rail markup:

```html
      <div>
        <div style="font:600 11px 'JetBrains Mono';letter-spacing:.14em;color:#7d8694;margin-bottom:8px">SIDE · LINKAGE</div>
        <canvas id="sideCanvas" width="260" height="220" style="width:100%;height:auto"></canvas>
      </div>
```

- [ ] **Step 2: Remove the `renderSide()` call**

In `renderAll()`, delete the line:

```js
  renderSide();
```

- [ ] **Step 3: Remove the `renderSide` function**

Delete the entire `function renderSide() { ... }` definition (the articulated side-linkage canvas drawing). Leave `legPlane` intact — it is still used by `render3D` and `drawThumb`.

- [ ] **Step 4: Verify in browser**

Run: `python -m scripts.animation_studio`, open `http://localhost:8899`.
Expected: no SIDE · LINKAGE panel; 3D model, top view, timeline thumbnails, reachability all still work; no `renderSide`/`sideCanvas` errors in the console.

- [ ] **Step 5: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): remove side-linkage panel"
```

---

## Task 4: Library overlay — fetch, render cards, load

**Files:**
- Modify: `scripts/studio.html`

- [ ] **Step 1: Add the `⊞ Library` toolbar button**

In the toolbar, immediately before the Reset button, add:

```html
    <button onclick="openLibrary()">⊞ Library</button>
```

- [ ] **Step 2: Add the modal container markup**

Just inside `<div class="app">` (as the first child), add the hidden overlay:

```html
  <div id="libraryModal" style="display:none;position:fixed;inset:0;z-index:50;background:rgba(8,10,14,.78);backdrop-filter:blur(3px)">
    <div style="position:absolute;inset:5% 8%;background:#0e1116;border:1px solid #23272f;border-radius:10px;display:flex;flex-direction:column;overflow:hidden">
      <div style="flex:none;display:flex;align-items:center;padding:14px 18px;border-bottom:1px solid #20242d">
        <span style="font:700 12px 'JetBrains Mono';letter-spacing:.18em;color:#cdd6e0">ANIMATION LIBRARY</span>
        <span style="flex:1"></span>
        <span onclick="closeLibrary()" style="cursor:pointer;color:#7d8694;font-size:20px;line-height:1">×</span>
      </div>
      <div id="libraryBody" style="flex:1;overflow-y:auto;padding:16px 18px"></div>
    </div>
  </div>
```

- [ ] **Step 3: Add the library JS (fetch, render, load) before the `window.*` exports**

```js
let _animations = [];
async function openLibrary(){
  document.getElementById("libraryModal").style.display="block";
  const body=document.getElementById("libraryBody");
  body.innerHTML='<div style="color:#7d8694;font-family:\'JetBrains Mono\'">loading…</div>';
  try{
    _animations=(await (await fetch("/animations")).json()).animations||[];
  }catch(e){ body.innerHTML='<div style="color:#f2664f">library unavailable: '+e+'</div>'; return; }
  renderLibrary();
}
function closeLibrary(){ document.getElementById("libraryModal").style.display="none"; }

function renderLibrary(){
  const body=document.getElementById("libraryBody");
  const groups=[["MY ANIMATIONS",_animations.filter(a=>!a.builtin)],
                ["BUILT-IN",_animations.filter(a=>a.builtin)]];
  body.innerHTML=groups.map(([title,list],gi)=>`
    <div style="font:600 11px 'JetBrains Mono';letter-spacing:.16em;color:#7d8694;margin:${gi?18:0}px 0 10px">${title}</div>
    <div style="display:flex;flex-wrap:wrap;gap:12px">
      ${list.length?list.map((a,i)=>{
        const gid=gi+"_"+i;
        return `<div onclick="loadAnimation('${gi}',${i})" style="width:150px;border:1px solid #232934;border-radius:8px;background:#11151b;padding:9px;cursor:pointer">
          <canvas id="libthumb${gid}" width="132" height="60" style="width:132px;height:60px;border-radius:4px;background:#0a0d12"></canvas>
          <div style="display:flex;align-items:center;gap:6px;margin-top:7px">
            <span style="font:600 12px 'Space Grotesk';color:#dfe5ec">${a.tool}</span>
            <span style="flex:1"></span>
            <span style="font:500 10px 'JetBrains Mono';color:#7d8694">${a.frames.length}f</span>
          </div>
          ${a.builtin?'<div style="font:500 9px \\'JetBrains Mono\\';color:#5b6470;margin-top:2px">read-only</div>':''}
        </div>`;
      }).join(""):'<div style="color:#5b6470;font-size:12px">none yet</div>'}
    </div>`).join("");
  groups.forEach(([_,list],gi)=>list.forEach((a,i)=>{
    const cv=document.getElementById("libthumb"+gi+"_"+i);
    if(cv && a.frames[0]) drawThumb(cv, a.frames[0].legs, false);
  }));
}

function loadAnimation(gi,i){
  const list=Number(gi)===0?_animations.filter(a=>!a.builtin):_animations.filter(a=>a.builtin);
  const a=list[i]; if(!a) return;
  if(!confirm("Replace the current sequence with \""+a.tool+"\"?")) return;
  document.getElementById("toolName").value=a.builtin?"":a.tool;  // builtins load nameless → save as new
  document.getElementById("desc").value=a.description||"";
  document.getElementById("personaGated").checked=!!a.persona_gated;
  document.getElementById("defaultSpeed").value=a.default_speed??60;
  state.frames=a.frames.map(f=>({legs:f.legs.map(l=>l.slice()),speed:f.speed,hold_s:f.hold_s}));
  selectFrame(0);
  closeLibrary();
}
```

- [ ] **Step 4: Export the new globals**

Add to the `window.*` block:

```js
window.openLibrary = openLibrary; window.closeLibrary = closeLibrary;
window.loadAnimation = loadAnimation;
```

- [ ] **Step 5: Verify in browser**

Run the studio. Click **⊞ Library**: the overlay opens with BUILT-IN cards (forward, wave, twist, …) each showing a pose thumbnail + frame count + "read-only", and MY ANIMATIONS showing `crab_walk`/`jai_bhim`. Click a card → confirm → its frames load into the timeline (F1 selected), export fields fill, modal closes. Built-ins load with an empty tool name.

- [ ] **Step 6: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): animation library overlay (browse + load)"
```

---

## Task 5: Save current animation to the library

**Files:**
- Modify: `scripts/studio.html`

- [ ] **Step 1: Add the `💾 Save` button**

In the export-panel button row, immediately after the Export button, add:

```html
          <button onclick="saveAnimation()">💾 Save</button>
```

- [ ] **Step 2: Add the `saveAnimation` function (near `exportJSON`)**

```js
async function saveAnimation(){
  const d=buildExport(); const err=validateExport(d);
  if(err){ alert("Cannot save: "+err); return; }
  try{
    const existing=(await (await fetch("/animations")).json()).animations||[];
    if(existing.some(a=>!a.builtin && a.tool===d.tool)
       && !confirm("Overwrite "+d.tool+".json?")) return;
    const r=await fetch("/animations",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify(d)});
    const j=await r.json();
    if(!j.ok){ alert("Save failed: "+(j.error||JSON.stringify(j))); return; }
    const st=document.getElementById("exportStatus");
    st.textContent="✓ saved "+j.file; st.style.color="#4ec77f";
  }catch(e){ alert("Save failed: "+e); }
}
```

- [ ] **Step 3: Export the global**

Add to the `window.*` block:

```js
window.saveAnimation = saveAnimation;
```

- [ ] **Step 4: Verify in browser**

Run the studio. Set a snake_case tool name, edit a pose, click **💾 Save** → status shows "✓ saved <tool>.json". Re-open the Library → it appears under MY ANIMATIONS. Saving the same name again asks to overwrite. Load a built-in (e.g. `wave`), tweak it, give it a new name, Save → a new user JSON appears and `builtin/wave.json` is unchanged.

- [ ] **Step 5: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): save current animation to the library"
```

---

## Task 6: Final verification pass

**Files:**
- Modify: `scripts/studio.html` (only if a regression surfaces)

- [ ] **Step 1: Run the automated tests**

Run: `cd /home/rishi/Rishi/AI/Paliv && python -m pytest scripts/test_gen_builtin.py scripts/test_animation_endpoints.py -v`
Expected: all PASS.

- [ ] **Step 2: Walk the manual checklist (spec §Testing)**

With `python -m scripts.animation_studio` running, confirm each:
1. `builtin/` has 15 valid frames-JSON; each validates in the studio.
2. Library opens; MY ANIMATIONS = your JSONs, BUILT-IN = all motions, thumbnails + counts.
3. Selecting loads frames into the timeline (F1 selected) + fills export fields; 3D + thumbnails update.
4. Edit + Save writes `assets/Animations/<tool>.json`; reappears in MY ANIMATIONS; overwrite asks first.
5. Load a built-in + Save → new user JSON; `builtin/` file unchanged.
6. SIDE · LINKAGE gone; 3D model, timeline, top view, reachability all work (no console errors).
7. The library grid scrolls vertically as animations grow.

- [ ] **Step 3: Remove any code orphaned by these changes**

Confirm no dead references remain (e.g., `renderSide`, `sideCanvas`). Remove only what these changes orphaned.

- [ ] **Step 4: Commit**

```bash
git add -A scripts/studio.html
git commit -m "chore(studio): animation library verification pass"
```

---

## Self-review notes

- **Spec coverage:** built-in generation incl. all gaits/poses/tricks (T1); `GET`/`POST /animations` with snake_case + path guard + builtin read-only (T2); side-linkage removal keeping `legPlane` (T3); overlay grid with grouped scrollable cards + thumbnails + load-with-confirm (T4); Save with overwrite confirm + builtins-as-new (T5); manual + automated verification (T6).
- **Built-ins read-only:** the POST endpoint only ever writes to `_ANIM_DIR` (never `builtin/`), and loaded built-ins clear the tool name so a Save becomes a new user file — both mechanisms enforce it.
- **Interfaces reused:** `drawThumb(canvas, legs, sel)`, `buildExport()`, `validateExport(d)`, `selectFrame(i)`, `state.frames` shape `{legs, speed, hold_s}` — all already exist from the timeline work.
- **New globals:** `openLibrary`, `closeLibrary`, `loadAnimation`, `saveAnimation` (exposed on `window`).
- **Names consistent across tasks:** `_ANIM_DIR`, `_BUILTIN_DIR`, `_read_anim`, `_animations`, `renderLibrary`.
```

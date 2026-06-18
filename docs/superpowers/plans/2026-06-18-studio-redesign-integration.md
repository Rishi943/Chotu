# Animation Studio Redesign Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reskin `scripts/studio.html` to the new visual design and add CAD-style orbit/pan + free foot-drag, while preserving all existing functionality and fitting one 1440p/16:9 screen with no page scroll.

**Architecture:** `scripts/studio.html` stays the single shipped file (served by `scripts/animation_studio.py`). We keep its `<script type="module">` logic (real `coord2polar` kinematics, three.js claw model, Pi proxy, export contract) and rewrite the DOM/CSS to the redesign layout. The redesign (`assets/Chotu Animation Studio redesign/Studio Editor.dc.html` + `support.js`) is the visual reference only — not shipped. New viewport logic (ResizeObserver, pan, view presets, free foot-drag) is added to the module.

**Tech Stack:** Vanilla HTML/CSS, three.js 0.160 (ESM via importmap), OrbitControls + TransformControls, FastAPI launcher (unchanged).

**Verification:** No JS test harness exists for the studio. Each task is verified manually in the browser via `python -m scripts.animation_studio` (opens `http://localhost:8899`). Pi calls hit local hardware over LAN (free, no approval needed); when the Pi is offline the proxy returns an error envelope and `.needs-pi` buttons disable — that is expected and fine to test against.

**Reference colors (redesign palette):** bg `#0e1014`, panel `#101319`, border `#20242d`, accent blue `#37b6ff`, text `#e9edf2`, muted `#7d8694`, ok-green `#4ec77f`, clamp-red `#f2664f`. Fonts: `Space Grotesk` (UI), `JetBrains Mono` (labels/values).

---

## Task 1: Responsive three.js viewport (foundation, no visual change yet)

Make the renderer size to its container instead of the hard-coded `360×300`, so the later flex layout can give the viewport whatever space remains. Do this first, before the DOM rewrite, so it's an isolated, verifiable change.

**Files:**
- Modify: `scripts/studio.html` (the `init3D()` function and the `#threed` host)

- [ ] **Step 1: Replace the fixed-size renderer setup with container-sized + ResizeObserver**

In `init3D()`, replace the fixed `W`/`H` block:

```js
// OLD:
//   const W = 360, H = 300;
//   camera = new THREE.PerspectiveCamera(48, W/H, 1, 4000);
//   ...
//   renderer.setSize(W, H); host.appendChild(renderer.domElement);
```

with container-driven sizing:

```js
  const sizeOf = () => {
    const r = host.getBoundingClientRect();
    return { w: Math.max(1, Math.floor(r.width)), h: Math.max(1, Math.floor(r.height)) };
  };
  const { w, h } = sizeOf();
  camera = new THREE.PerspectiveCamera(48, w / h, 1, 4000);
  camera.position.set(220, 180, 260);
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(w, h);
  host.appendChild(renderer.domElement);

  const ro = new ResizeObserver(() => {
    const { w, h } = sizeOf();
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  });
  ro.observe(host);
```

Give the host a size so it isn't 0×0 before the rewrite — temporarily set inline style on `#threed` in the HTML body: `<div id="threed" class="col" style="width:360px;height:300px"></div>` (Task 2 removes this in favor of flex).

- [ ] **Step 2: Verify in browser**

Run: `python -m scripts.animation_studio` and open `http://localhost:8899`.
Expected: the 3D model still renders at ~360×300, orbit + zoom still work, no console errors. Resizing the `#threed` inline width/height via devtools resizes the canvas without distortion.

- [ ] **Step 3: Commit**

```bash
git add scripts/studio.html
git commit -m "refactor(studio): size three.js viewport to its container (ResizeObserver)"
```

---

## Task 2: DOM + flex layout to the redesign structure (fit-to-viewport)

Rewrite the page body to the redesign's toolbar / left-rail / center / right-rail / timeline structure as a `100vh` flex column with `overflow:hidden`. Keep every interactive element's existing `id` and `onclick`/handler name so the module logic keeps working. This task is about structure + fit; detailed styling is Task 3.

**Files:**
- Modify: `scripts/studio.html` (`<body>` markup and `<style>`)

- [ ] **Step 1: Replace the `<style>` block with layout primitives**

```html
<style>
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{background:#0e1014;color:#e9edf2;font-family:'Space Grotesk',sans-serif;overflow:hidden}
  .app{display:flex;flex-direction:column;height:100vh;overflow:hidden}
  .toolbar{flex:none;height:54px;display:flex;align-items:center;gap:14px;padding:0 16px;background:#14171d;border-bottom:1px solid #23272f}
  .mid{flex:1;display:flex;min-height:0}
  .rail-l{flex:none;width:330px;background:#101319;border-right:1px solid #20242d;display:flex;flex-direction:column;padding:12px 13px;gap:10px;overflow:hidden}
  .center{flex:1;position:relative;min-width:0;overflow:hidden;background:radial-gradient(120% 90% at 50% 30%,#262c38 0%,#181c25 55%,#10131a 100%)}
  .rail-r{flex:none;width:316px;background:#101319;border-left:1px solid #20242d;display:flex;flex-direction:column;padding:12px 13px;gap:12px;overflow-y:auto}
  .timeline{flex:none;height:150px;background:#0c0f14;border-top:1px solid #23272f;display:flex;flex-direction:column;padding:10px 16px;overflow:hidden}
  #threed{position:absolute;inset:0}
  #threed canvas{display:block}
  .ov{position:absolute;font-family:'JetBrains Mono',monospace}
  /* leg editor / inputs / buttons styled in Task 3 */
  .leg.editing{outline:1px solid #37b6ff}
  .dot-ok{color:#4ec77f} .dot-clamp{color:#f2664f}
  input[type=number],input[type=text]{background:#0b0e13;color:#e9edf2;border:1px solid #232934;border-radius:5px}
  button{background:#1a1e25;color:#c3cad4;border:1px solid #2c313b;border-radius:6px;padding:7px 12px;cursor:pointer;font-family:inherit}
  button:disabled{opacity:.4;cursor:not-allowed}
  button.primary{background:#37b6ff;color:#06121d;border-color:#37b6ff}
</style>
```

- [ ] **Step 2: Replace the `<body>` markup (keep all ids/handlers)**

Rewrite the body to this structure. The mount-point ids (`#legEditor`, `#topCanvas`, `#sideCanvas`, `#threed`, `#timeline`, `#exportPanel` children, `#conn`, `#speed`) are unchanged so the module's `render*` functions keep targeting them. The export inputs (`#toolName`, `#desc`, `#personaGated`, `#defaultSpeed`) and their handlers are preserved.

```html
<body>
<div class="app">
  <!-- TOP TOOLBAR -->
  <div class="toolbar">
    <div style="display:flex;align-items:center;gap:8px">
      <div style="width:22px;height:22px;border-radius:5px;background:linear-gradient(135deg,#37b6ff,#1d7fd0);display:flex;align-items:center;justify-content:center;color:#06121d;font-weight:700">◣</div>
      <span style="font-weight:700">CHOTU</span>
      <span style="font-size:11px;color:#6b7280;letter-spacing:.18em;font-family:'JetBrains Mono',monospace">ANIMATION&nbsp;STUDIO</span>
    </div>
    <div style="flex:1"></div>
    <div id="conn" style="color:#888;font-family:'JetBrains Mono',monospace;font-size:12px">● checking Pi…</div>
    <div style="flex:1"></div>
    <button onclick="resetToStand()">⟲ Reset to stand</button>
    <button class="primary needs-pi" onclick="sendToRobot()">▶ Send to robot</button>
  </div>

  <div class="mid">
    <!-- LEFT RAIL -->
    <div class="rail-l">
      <div style="font:600 11px 'JetBrains Mono';letter-spacing:.16em;color:#7d8694">LEG&nbsp;EDITOR</div>
      <div id="legEditor"></div>
      <div style="border-top:1px solid #20242d;padding-top:10px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <span style="font:600 11px 'JetBrains Mono';letter-spacing:.14em;color:#7d8694">SERVO&nbsp;SPEED</span>
          <span style="font-family:'JetBrains Mono',monospace;color:#5fc6ff" id="speedReadout">60<span style="color:#5b6470">/90</span></span>
        </div>
        speed <input id="speed" type="number" min="0" max="90" value="60" style="width:64px" oninput="onSpeedInput()">
      </div>
    </div>

    <!-- CENTER VIEWPORT -->
    <div class="center">
      <div id="threed"></div>
      <div class="ov" style="left:14px;top:12px">
        <div id="viewLabel" style="font-size:11px;color:#cdd6e0;letter-spacing:.12em">PERSPECTIVE</div>
        <div style="font-size:10px;color:#5b6470" id="viewSub">drag to pose</div>
      </div>
      <div class="ov" style="left:50%;bottom:14px;transform:translateX(-50%);display:flex;gap:4px;background:rgba(14,18,24,.82);border:1px solid #283041;border-radius:9px;padding:5px;font-size:11px">
        <span class="viewbtn" data-view="PERSP" onclick="setView('PERSP')">PERSP</span>
        <span class="viewbtn" data-view="TOP" onclick="setView('TOP')">TOP</span>
        <span class="viewbtn" data-view="SIDE" onclick="setView('SIDE')">SIDE</span>
        <span class="viewbtn" data-view="FRONT" onclick="setView('FRONT')">FRONT</span>
        <span style="width:1px;background:#283041;margin:2px 3px"></span>
        <span id="gizmoBtn" onclick="toggleGizmo()" style="padding:6px 10px;border-radius:6px;cursor:pointer;color:#37b6ff">⊹ Gizmo</span>
      </div>
    </div>

    <!-- RIGHT RAIL -->
    <div class="rail-r">
      <div>
        <div style="font:600 11px 'JetBrains Mono';letter-spacing:.14em;color:#7d8694;margin-bottom:8px">TOP-DOWN · FOOT MAP</div>
        <canvas id="topCanvas" width="260" height="220" style="width:100%;height:auto"></canvas>
      </div>
      <div>
        <div style="font:600 11px 'JetBrains Mono';letter-spacing:.14em;color:#7d8694;margin-bottom:8px">SIDE · LINKAGE</div>
        <canvas id="sideCanvas" width="260" height="220" style="width:100%;height:auto"></canvas>
      </div>
      <div id="exportPanel">
        <div style="font:600 11px 'JetBrains Mono';letter-spacing:.14em;color:#7d8694;margin-bottom:8px">EXPORT → TOOL JSON</div>
        tool <input id="toolName" type="text" placeholder="shimmy" style="width:120px" oninput="renderExportStatus()"><br>
        desc <input id="desc" type="text" placeholder="A quick description." style="width:200px;margin-top:6px"><br>
        <label style="display:inline-flex;align-items:center;gap:6px;margin-top:6px"><input id="personaGated" type="checkbox"> persona_gated (reel only)</label><br>
        default_speed <input id="defaultSpeed" type="number" value="60" style="width:60px;margin-top:6px"><br>
        <div style="margin-top:8px;display:flex;gap:6px">
          <button class="primary" onclick="exportJSON()">⬇ Export</button>
          <button onclick="copyJSON()">⧉</button>
          <button onclick="document.getElementById('loadFile').click()">⬆</button>
          <input id="loadFile" type="file" accept=".json" onchange="loadJSON(this.files[0])" style="display:none">
        </div>
        <div id="exportStatus" style="margin-top:6px;color:#777;font-family:'JetBrains Mono',monospace;font-size:11px"></div>
      </div>
    </div>
  </div>

  <!-- TIMELINE -->
  <div class="timeline">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
      <span style="font:600 11px 'JetBrains Mono';letter-spacing:.14em;color:#7d8694">TIMELINE</span>
      <span style="flex:1"></span>
      <button onclick="playPreview()">▷ Play preview</button>
      <button class="primary needs-pi" onclick="playOnRobot()">▶▶ Play on robot</button>
    </div>
    <div id="timeline" style="flex:1;overflow-x:auto;white-space:nowrap"></div>
  </div>
</div>
```

Note: the timeline's "Add frame / Play" buttons were previously rendered inside `renderTimeline()`. Keep `renderTimeline()` rendering the frame chips into `#timeline`; the Play buttons now live in the static timeline header above (remove the duplicate Play buttons from `renderTimeline()`'s template, but keep `＋ Add frame` there or move it — see Step 3).

- [ ] **Step 3: Adjust `renderTimeline()` to match the split (chips in `#timeline`, actions in header)**

Edit `renderTimeline()` so its trailing actions block only keeps `＋ Add frame` (the two Play buttons are now static in the header):

```js
  el.innerHTML = state.frames.map((f,i)=>`...`).join(" → ") +
    `<div style="margin-top:6px"><button onclick="addFrame()">＋ Add frame</button></div>`;
```

- [ ] **Step 4: Add `onSpeedInput()` and keep `#speed` as source of truth**

The old code read `#speed` inside `renderAll()`. Add a small handler that updates the readout and re-renders:

```js
function onSpeedInput(){
  document.getElementById("speedReadout").innerHTML =
    `${document.getElementById("speed").value}<span style="color:#5b6470">/90</span>`;
  renderAll();
}
window.onSpeedInput = onSpeedInput;
```

- [ ] **Step 5: Verify in browser**

Run: `python -m scripts.animation_studio`.
Expected on a 1440p/16:9 screen at 100% AND 150% browser zoom: toolbar, both rails, the full 3D viewport, and the timeline are all visible with NO page scroll. Servo speed sits directly under the leg cards. 3D model fills the center. Leg editing, top-down, side linkage, export, frames all still function (unstyled but working).

- [ ] **Step 6: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): redesign DOM + fit-to-viewport flex layout"
```

---

## Task 3: Visual styling — leg cards, servo meter, panels, toolbar polish

Bring the reskin up to the redesign's look: leg-editor cards, the equalizer servo-speed meter, Pi-pill styling, and panel typography. Pure CSS/markup in the render functions; no logic change.

**Files:**
- Modify: `scripts/studio.html` (`renderLegEditor`, `renderDots`, the servo-speed block, `pollHealth` pill, export panel)

- [ ] **Step 1: Restyle `renderLegEditor()` to card layout**

```js
function renderLegEditor() {
  const el = document.getElementById("legEditor");
  el.innerHTML = state.legs.map((leg, i) => {
    const editing = i===state.editing;
    const card = editing
      ? "border:1px solid #37b6ff;border-radius:7px;background:linear-gradient(180deg,rgba(55,182,255,.10),rgba(55,182,255,.03));padding:9px 11px;margin-bottom:8px"
      : "border:1px solid #20252e;border-radius:7px;background:#12161c;padding:9px 11px;margin-bottom:8px";
    return `<div class="leg" style="${card}" onclick="state.editing=${i};renderAll()">
      <div style="display:flex;align-items:center;gap:7px;margin-bottom:7px">
        <span id="dot${i}"></span>
        <span style="font-weight:600">${LEG_NAMES[i].split(' · ')[0]}</span>
        <span style="font-size:10.5px;color:#6b7280;font-family:'JetBrains Mono',monospace">${LEG_NAMES[i].split(' · ')[1]||''}</span>
        <span style="flex:1"></span>
        <span id="status${i}" style="font-size:10px;font-family:'JetBrains Mono',monospace"></span>
      </div>
      <div style="display:flex;gap:6px">
        ${["X","Y","Z"].map((ax, a) =>
          `<div style="flex:1;display:flex;align-items:center;background:#0b0e13;border:1px solid #232934;border-radius:5px;overflow:hidden">
             <span style="width:26px;text-align:center;font:600 11px 'JetBrains Mono';color:#6b7280;border-right:1px solid #232934;padding:6px 0">${ax}</span>
             <input type="number" value="${leg[a]}" oninput="setLeg(${i},${a},this.value)" style="flex:1;border:none;background:none;text-align:right;padding:6px 8px;width:auto">
           </div>`
        ).join("")}
      </div>
    </div>`;
  }).join("");
}
```

- [ ] **Step 2: Update `renderDots()` to also set the per-card status text**

```js
function renderDots() {
  state.legs.forEach((leg, i) => {
    const ok = isReachable(leg[0], leg[1], leg[2]);
    const editing = i===state.editing;
    const dot = document.getElementById("dot"+i);
    dot.textContent = "●";
    dot.style.color = editing ? "#37b6ff" : (ok ? "#4ec77f" : "#f2664f");
    const st = document.getElementById("status"+i);
    if (st) {
      st.textContent = editing ? "● EDITING" : (ok ? "REACHABLE" : "⚠ WILL CLAMP");
      st.style.color = editing ? "#37b6ff" : (ok ? "#4ec77f" : "#f2664f");
    }
  });
}
```

- [ ] **Step 3: Add the equalizer servo-speed bars (optional visual, keep the number input working)**

Replace the plain `speed <input>` line in the left rail with the equalizer + a hidden-but-present number input kept as source of truth. In the body markup (Task 2 left rail), the `#speed` input stays; add a bar strip above it that scrubs the value:

```html
<div id="speedBars" style="height:28px;display:flex;align-items:flex-end;gap:3px;margin-bottom:6px"></div>
```

And render it from `renderAll()` via a new `renderSpeedBars()`:

```js
function renderSpeedBars(){
  const host=document.getElementById("speedBars"); if(!host) return;
  const v=Number(document.getElementById("speed").value);
  const filled=Math.round(v/90*12);
  host.innerHTML="";
  for(let i=0;i<12;i++){
    const b=document.createElement("div");
    b.style.cssText=`flex:1;height:${28+i*6}%;border-radius:1px;cursor:pointer;background:${i<filled?'#37b6ff':(i<filled+3?'#22323f':'#1c2630')}`;
    b.onclick=()=>{document.getElementById("speed").value=Math.round((i+1)/12*90);onSpeedInput();};
    host.appendChild(b);
  }
}
```

Call `renderSpeedBars()` inside `renderAll()`.

- [ ] **Step 4: Style the Pi pill in `pollHealth()`**

```js
async function pollHealth() {
  const el = document.getElementById("conn");
  try {
    const r = await fetch("/health");
    const ok = r.ok && (await r.json()).ok;
    el.innerHTML = ok ? "● Pi connected" : "● Pi unreachable";
    el.style.color = ok ? "#4ec77f" : "#f2664f";
    document.querySelectorAll(".needs-pi").forEach(b => b.disabled = !ok);
  } catch { el.innerHTML = "● Pi unreachable"; el.style.color = "#f2664f";
    document.querySelectorAll(".needs-pi").forEach(b => b.disabled = true); }
}
```

- [ ] **Step 5: Verify in browser**

Run the studio. Expected: leg cards match the redesign (active = blue glow, reachable = green dot/REACHABLE, clamp = red/WILL CLAMP). Servo equalizer reflects speed and scrubs it. Pi pill is green/red. Everything still fits one screen, no scroll.

- [ ] **Step 6: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): redesign visual styling (leg cards, servo meter, pill)"
```

---

## Task 4: Recolor the 3D model + resize servo blocks

Geometry unchanged; only material colors and the servo-marker dimensions change to match the redesign palette and the real PiCrawler servo proportions.

**Files:**
- Modify: `scripts/studio.html` (`init3D` scene background/grid + materials, `mkServo`, `render3D` color logic)

- [ ] **Step 1: Update scene background, grid, and accent colors**

In `init3D()`:

```js
  scene.background = new THREE.Color(0x10131a);
  // grid:
  const grid = new THREE.GridHelper(400,16,0x2a3744,0x202833); grid.position.y=-95; scene.add(grid);
```

- [ ] **Step 2: Shrink the servo markers**

```js
  const mkServo = () => new THREE.Mesh(new THREE.BoxGeometry(9,9,15),
    new THREE.MeshStandardMaterial({color:0x8b9099, metalness:.35, roughness:.6}));
```

- [ ] **Step 3: Update the active/reachable/clamp accent in `render3D()`**

```js
    const col=(i===state.editing)?0x37b6ff:(ok?0xc3cbd6:0xf2664f);
    grp.upper.material.color.setHex(col); grp.blade.material.color.setHex(col);
    grp.hipBone.material.color.setHex(i===state.editing?0x37b6ff:0xc3cbd6);
```

Also change the `SILVER` constant usages if needed; keep `SILVER` for the body/board but the leg accent now uses the values above.

- [ ] **Step 4: Verify in browser**

Run the studio. Expected: the model reads like the reference photo — claw legs, blue active leg, smaller servo blocks, dark grid matching the viewport background.

- [ ] **Step 5: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): recolor 3D model + resize servo blocks to match design"
```

---

## Task 5: CAD navigation — enable pan + view-preset buttons

**Files:**
- Modify: `scripts/studio.html` (`init3D` OrbitControls config, new `setView()`, `.viewbtn` styling)

- [ ] **Step 1: Enable pan + zoom on OrbitControls**

In `init3D()` after `controls` is created:

```js
  controls.enablePan = true;
  controls.enableZoom = true;
  controls.screenSpacePanning = true;   // pan in screen plane (CAD-like)
  controls.target.set(0,-20,0);
```

- [ ] **Step 2: Add `setView()` camera presets + active-button styling**

```js
const VIEWS = {
  PERSP: { pos:[220,180,260], target:[0,-20,0], label:"PERSPECTIVE" },
  TOP:   { pos:[0,360,0.1],   target:[0,-20,0], label:"TOP VIEW" },
  SIDE:  { pos:[360,0,0],     target:[0,-20,0], label:"SIDE VIEW" },
  FRONT: { pos:[0,0,360],     target:[0,-20,0], label:"FRONT VIEW" },
};
let currentView = "PERSP";
function setView(v){
  const cfg = VIEWS[v]; if(!cfg) return;
  currentView = v;
  camera.position.set(...cfg.pos);
  controls.target.set(...cfg.target);
  controls.update();
  document.getElementById("viewLabel").textContent = cfg.label;
  document.querySelectorAll(".viewbtn").forEach(b=>{
    const on = b.dataset.view===v;
    b.style.cssText = on
      ? "padding:6px 11px;border-radius:6px;background:#37b6ff;color:#06121d;font-weight:700;cursor:pointer"
      : "padding:6px 11px;border-radius:6px;color:#aab2bd;cursor:pointer";
  });
}
window.setView = setView;
```

Call `setView("PERSP")` once at the end of init (after `init3D()`), to set the initial active-button state.

- [ ] **Step 3: Verify in browser**

Run the studio. Expected: right-drag (or two-finger) pans the model; scroll zooms; left-drag orbits a full 360°. The PERSP/TOP/SIDE/FRONT buttons snap the camera and the active button highlights; the view label updates.

- [ ] **Step 4: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): CAD orbit/pan + view-preset camera snaps"
```

---

## Task 6: Free foot-drag (gizmo OFF) on a camera-facing plane

Add a gizmo toggle. When the gizmo is ON, keep the existing `TransformControls` single-axis behavior. When OFF, hide the gizmo and let the user drag the edited leg's foot freely on a camera-facing plane; solve IK each frame so the leg tracks like a linkage.

**Files:**
- Modify: `scripts/studio.html` (`init3D` gizmo setup, new pointer handlers, `toggleGizmo()`, `render3D` gizmo parking guard)

- [ ] **Step 1: Add gizmo-mode state and toggle**

```js
let gizmoOn = true;   // ON = TransformControls single-axis; OFF = free foot-drag
function toggleGizmo(){
  gizmoOn = !gizmoOn;
  tcontrols.visible = gizmoOn;
  tcontrols.enabled = gizmoOn;
  document.getElementById("gizmoBtn").style.color = gizmoOn ? "#37b6ff" : "#aab2bd";
}
window.toggleGizmo = toggleGizmo;
```

- [ ] **Step 2: Add free-drag pointer handlers in `init3D()`**

After `tcontrols` setup, add raycaster-based free dragging on the renderer canvas:

```js
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const dragPlane = new THREE.Plane();
  let freeDragging = false;

  const toNDC = (e) => {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.x = ((e.clientX - r.left)/r.width)*2 - 1;
    ndc.y = -((e.clientY - r.top)/r.height)*2 + 1;
  };
  const footWorld = () => {
    // current edited foot world position == gizmoTarget (parked there each render)
    return gizmoTarget.position.clone();
  };

  renderer.domElement.addEventListener('pointerdown', (e)=>{
    if (gizmoOn) return;                       // gizmo handles its own drags
    toNDC(e);
    raycaster.setFromCamera(ndc, camera);
    const foot = footWorld();
    // only start a free-drag if the click lands near the edited foot (in screen space)
    const screen = foot.clone().project(camera);
    const dx = (screen.x - ndc.x), dy = (screen.y - ndc.y);
    if (Math.hypot(dx, dy) > 0.06) return;     // ~tolerance; otherwise let OrbitControls handle it
    // camera-facing plane through the foot
    dragPlane.setFromNormalAndCoplanarPoint(
      camera.getWorldDirection(new THREE.Vector3()).negate(), foot);
    freeDragging = true; controls.enabled = false;
    e.target.setPointerCapture?.(e.pointerId);
  });

  renderer.domElement.addEventListener('pointermove', (e)=>{
    if (!freeDragging) return;
    toNDC(e);
    raycaster.setFromCamera(ndc, camera);
    const hit = new THREE.Vector3();
    if (!raycaster.ray.intersectPlane(dragPlane, hit)) return;
    // world -> leg-local (mirror of the gizmo objectChange handler)
    const i = state.editing, c = CORNERS3D[i];
    const rel = hit.clone().sub(new THREE.Vector3(...c.p));
    const w = Math.hypot(rel.x, rel.z), la = c.yaw - Math.atan2(rel.z, rel.x);
    state.legs[i] = [ Math.round(w*Math.cos(la)), Math.round(w*Math.sin(la)), Math.round(rel.y) ];
    renderAll();
  });

  const endFree = ()=>{ if(freeDragging){ freeDragging=false; controls.enabled=true; } };
  renderer.domElement.addEventListener('pointerup', endFree);
  renderer.domElement.addEventListener('pointercancel', endFree);
```

- [ ] **Step 3: Guard the gizmo-parking line in `render3D()`**

The existing line parks the gizmo on the edited foot. Extend its guard so it also parks while free-dragging is NOT active (it already checks `!dragging`; add `!freeDragging`). Since `freeDragging` is scoped inside `init3D`, lift it to module scope: declare `let freeDragging = false;` alongside the other `let scene, camera, ...` declarations and remove the inner re-declaration. Then:

```js
    if(i===state.editing && !dragging && !freeDragging) gizmoTarget.position.copy(foot);
```

- [ ] **Step 4: Verify in browser**

Run the studio. Expected:
- Gizmo ON (default): the orange axis gizmo shows on the edited foot; single-axis drags work as before.
- Click "⊹ Gizmo" → gizmo hides. Click-dragging the edited foot moves it freely; the leg bends to follow (linkage). Orbit to a different angle and the drag plane follows the camera so you can reach other axes. Dragging into unreachable space clamps the leg red. Clicking empty space still orbits.

- [ ] **Step 5: Commit**

```bash
git add scripts/studio.html
git commit -m "feat(studio): free foot-drag on camera-facing plane (gizmo toggle)"
```

---

## Task 7: Final pass — verify all preserved controls + cleanup

**Files:**
- Modify: `scripts/studio.html` (only if verification surfaces a regression)

- [ ] **Step 1: Walk the full verification checklist (spec §Testing)**

Run `python -m scripts.animation_studio` and confirm, ticking each:
1. 100% and 150% zoom on 1440p/16:9: everything visible, no page scroll; servo speed under leg cards.
2. Orbit + zoom + pan work; 4 view buttons snap the camera.
3. Gizmo OFF free-drag follows + clamps; gizmo ON single-axis works.
4. Reset to stand restores stand pose. Persona checkbox flips `persona_gated` in exported JSON (export and inspect). Tool name / desc / default-speed editable + validation updates.
5. Send to robot / Play on robot reach the Pi when connected (or disable when offline).
6. Resizing the window keeps the canvas correctly sized.
7. Export / Copy / Load round-trip a frames JSON unchanged.

- [ ] **Step 2: Remove any orphaned code from the rewrite**

Confirm no dead references remain (e.g., the old fixed `W/H`, duplicate Play buttons, the temporary `#threed` inline size from Task 1). Remove only code your changes orphaned.

- [ ] **Step 3: Commit**

```bash
git add scripts/studio.html
git commit -m "chore(studio): final verification pass + cleanup"
```

---

## Self-review notes

- **Spec coverage:** fit-to-viewport (T2), servo-under-cards (T2/T3), keep three.js model + recolor/servo-resize (T4), orbit/pan + view presets (T5), free foot-drag camera-facing plane + gizmo toggle (T6), reset/persona/inputs/pi-pill wiring (T2/T3/T7), export contract unchanged (preserved ids, T2/T7). FK toggle omitted per spec.
- **Preserved interfaces:** all mount-point ids (`#legEditor`, `#topCanvas`, `#sideCanvas`, `#threed`, `#timeline`, `#speed`, `#toolName`, `#desc`, `#personaGated`, `#defaultSpeed`, `#conn`) and handler names (`setLeg`, `resetToStand`, `sendToRobot`, `addFrame`, `playPreview`, `playOnRobot`, `exportJSON`, `copyJSON`, `loadJSON`, `renderExportStatus`) are unchanged — module logic keeps working.
- **New globals exposed on `window`:** `onSpeedInput`, `setView`, `toggleGizmo` (added in their tasks).
- **`freeDragging` is module-scoped** (T6 step 3) so `render3D` can read it.
```

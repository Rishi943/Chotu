# Animation Studio — animation library

**Date:** 2026-06-18
**Status:** Design — awaiting review

## Goal

Add a browsable **animation library** to the Chotu Animation Studio: load any saved
animation (the user's JSONs plus all of Chotu's built-in motions) by selecting a
thumbnail card, edit it in the timeline, and save the result as a new custom JSON
tool. Remove the now-unneeded SIDE · LINKAGE panel.

## Sources of animations

1. **User animations** — `assets/Animations/*.json`, the frames-JSON files the user
   creates/exports (e.g. `crab_walk.json`, `jai_bhim.json`). Editable, overwritable.
2. **Built-in motions** — `assets/Animations/builtin/*.json`, generated once from
   Chotu's real motion code: the picrawler `MoveList` gaits/poses and the bridge
   tricks. Read-only templates (loading one and saving creates a new *user* JSON).

Built-ins to generate (all of Chotu's physical motions):
- Gaits: `forward`, `backward`, `turn_left`, `turn_right`
- Poses/expressive: `wave`, `sit`, `stand`, `look_up`, `look_down`, `look_left`,
  `look_right`, `push_up`
- Tricks: `twist`, `swimming`, `handwork`

## Built-in generation script

`scripts/gen_builtin_animations.py` — one-time, committed, **not** run at studio
runtime (picrawler is Pi-only; we port the literal coordinate sequences from source).

- Gait/pose sequences come from `picrawler` `MoveList` (source read off the Pi at
  `~/picrawler/picrawler/picrawler.py`). Each step (a 4×`[x,y,z]`) becomes one frame.
  Constants: `X_DEFAULT=45, X_TURN=70, X_START=0, Y_DEFAULT=45, Y_TURN=130, Y_WAVE=120,
  Y_START=0, Z_DEFAULT=-50, Z_UP=-30, Z_WAVE=60, Z_TURN=-40, Z_PUSH=-76`.
  For gaits we take the `stand_position==0` branch (no parity swap).
- Tricks (`twist`, `swimming`, `handwork`) come from `pi_bridge/server.py` `_TRICKS`.
- Each output file is a valid frames JSON: `{tool, description, persona_gated:false,
  default_speed, frames:[{legs, speed, hold_s}]}`. `default_speed`/`speed` per motion
  (gaits ~60, tricks at their choreographed speed, poses ~50). `hold_s` 0 except a
  small hold on terminal stand frames.
- Output: `assets/Animations/builtin/<tool>.json`. The script is idempotent
  (regenerates the folder). Committed output so the studio works without re-running.

Note: gaits are cyclic and only "walk" when looped on the robot; as library entries
they are editable starting points / references, which is acceptable and intended.

## Server endpoints (`scripts/animation_studio.py`)

Add to the existing FastAPI app (which already serves `studio.html` and proxies the
Pi). New constant: `_ANIM_DIR = repo/assets/Animations`, `_BUILTIN_DIR = _ANIM_DIR/builtin`.

- `GET /animations` → `{"animations": [ {file, name, builtin, tool, description,
  persona_gated, default_speed, frames}, ... ]}`.
  - Reads `*.json` from `_ANIM_DIR` (builtin:false) and `_BUILTIN_DIR` (builtin:true).
  - `name` = the `tool` field, falling back to the filename stem.
  - Malformed/invalid JSON files are skipped with `print(... warning ...)`; never raise.
  - Sorted: user animations first (alpha), then built-ins (alpha).
- `POST /animations` → body is a full frames-JSON (`buildExport()` shape). Writes to
  `_ANIM_DIR/<tool>.json` (pretty-printed, 2-space). Validation:
  - `tool` must match `^[a-z][a-z0-9_]*$` and have ≥1 frame → else 400.
  - The resolved path must stay inside `_ANIM_DIR` and **not** under `builtin/` →
    else 403 (`"built-ins are read-only"`). (Path-traversal guard via `os.path`.)
  - Returns `{ok:true, file:"<tool>.json"}`.
- The Pi proxy, `/set_legs`, `/pose`, `/health` endpoints are unchanged.

`_ANIM_DIR`/`_BUILTIN_DIR` are created with `mkdir(parents=True, exist_ok=True)` on
startup so a fresh checkout works.

## Studio UI (`scripts/studio.html`)

### Removed
- The SIDE · LINKAGE panel: its markup (`#sideCanvas` + the "SIDE · LINKAGE" block),
  the `renderSide()` function, and its call in `renderAll()`. (`legPlane` stays — it is
  still used by the 3D model and the timeline thumbnails.)

### Library overlay
- A **`⊞ Library`** button in the top toolbar (left of Reset).
- Opens a **full-window modal** (`position:fixed; inset:0;` dim backdrop, panel with the
  studio palette). Header "ANIMATION LIBRARY" + close `×`. Body **scrolls vertically**.
- Two labelled groups, **MY ANIMATIONS** then **BUILT-IN**, each a `flex-wrap` grid of
  cards. Card = a `<canvas>` thumbnail (`drawThumb` of `frames[0].legs`) + tool name +
  `Nf` frame-count + a small "read-only" tag on built-ins.
- Click a card → `loadAnimation(a)`:
  - If the current timeline differs from the last-loaded/seeded state, `confirm(
    "Replace the current sequence?")`; cancel aborts.
  - Sets `state.frames` from `a.frames` (deep copy), fills `#toolName`/`#desc`/
    `#personaGated`/`#defaultSpeed`, `selectFrame(0)`, closes the modal.
- The modal re-fetches `GET /animations` each time it opens (fresh list).

### Save
- A **`💾 Save`** button in the export panel (next to Export). On click:
  - Build + validate via existing `buildExport()`/`validateExport()`; invalid → alert.
  - `GET /animations` to check if a **user** file with that `tool` already exists →
    if so `confirm("Overwrite <tool>.json?")`.
  - `POST /animations` with the JSON. On `ok`, toast/status "saved"; on error, alert
    the server message. (Built-in name collisions can't happen — built-ins write-protected
    server-side.)
- Existing Export (download), Copy, and Load-file (`⬆`) buttons stay as-is.

### Plumbing
- New module functions exposed on `window`: `openLibrary`, `closeLibrary`,
  `loadAnimation`, `saveAnimation`.
- Reuses `drawThumb`, `buildExport`, `validateExport`. No new kinematics.

## Data flow

```
studio load ─┐
             ├─> (library button) GET /animations ─> render cards ─> click
             │                                                       └─> load frames
timeline edits ─> buildExport ─> (Save) POST /animations ─> re-list
```

## Error handling

- Server unreachable on list/save → `alert(...)`; studio keeps working, library empty.
- Built-in write attempt → server 403, surfaced via alert (shouldn't occur in normal UI).
- Malformed JSON in the folder → skipped server-side, logged; library still loads.
- `confirm()` guards both destructive actions (replace timeline, overwrite file).

## Testing / success criteria

Manual (browser + a running studio; `python -m scripts.animation_studio`):
1. `python -m scripts.gen_builtin_animations` creates `assets/Animations/builtin/`
   with one valid frames JSON per listed motion; each validates against the studio's
   `validateExport` rules (snake_case tool, ≥1 frame, 4 legs × `[x,y,z]`).
2. Library button opens the overlay; MY ANIMATIONS shows `crab_walk`/`jai_bhim`,
   BUILT-IN shows all generated motions, each with a pose thumbnail + frame count.
3. Selecting an animation loads its frames into the timeline (F1 selected) and fills
   the export fields; the 3D model and thumbnails reflect frame 1.
4. Editing then Save writes `assets/Animations/<tool>.json`; it reappears in MY
   ANIMATIONS on next open. Overwriting a user file asks first.
5. Loading a built-in then Save creates a new user JSON; the `builtin/` file is
   unchanged.
6. The SIDE · LINKAGE panel is gone; the 3D model, timeline thumbnails, top view,
   and reachability still work (no `renderSide` errors in console).
7. Library/grid scrolls vertically as the number of animations grows.

## Out of scope

- Editing/deleting built-ins (read-only by design).
- Renaming/deleting user files from the UI (delete via filesystem for now).
- Cloud/remote storage; everything is the local `assets/Animations/` folder.
- Changing the frames-JSON schema or the `add-chotu-tool` contract.
